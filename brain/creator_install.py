# -*- coding: utf-8 -*-
"""Creator Mode MVP-2 approval snapshots and controlled installation.

This module deliberately has no model-facing entry point.  The HTTP handler
passes an already authenticated admin identity here, and the final install is
delegated to the existing Plugin/Skill transaction implementations.
"""
import datetime
import hashlib
import io
import json
import os
import secrets
import tempfile
import threading
import time
import zipfile

import creator
import paths


APPROVAL_TTL_SECONDS = 10 * 60
_LOCK = threading.RLock()


def _root():
    root = os.path.join(paths.DATA_DIR, "creator_approvals")
    os.makedirs(root, exist_ok=True)
    return root


def _safe_id(value):
    value = str(value or "")
    if not value or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in value):
        raise ValueError("审批编号不合法")
    return value


def _path(approval_id):
    aid = _safe_id(approval_id)
    return os.path.join(_root(), aid + ".json")


def _read(approval_id):
    try:
        with open(_path(approval_id), encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _write(record):
    os.makedirs(_root(), exist_ok=True)
    path = _path(record["approval_id"])
    tmp = path + ".tmp_%s" % secrets.token_hex(6)
    with open(tmp, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def _iso(ts=None):
    return datetime.datetime.fromtimestamp(ts or time.time()).isoformat(timespec="seconds")


def _audit(event, record, extra=None):
    """Append a redacted audit line; never write file contents or tokens."""
    try:
        path = os.path.join(_root(), "audit.jsonl")
        line = {
            "at": _iso(), "event": event,
            "approval_id": str(record.get("approval_id") or "")[:18],
            "draft_id": record.get("draft_id"),
            "version": record.get("version"),
            "kind": record.get("kind"),
            "digest": record.get("digest"),
        }
        if extra:
            line.update({str(k): v for k, v in extra.items() if k not in ("token", "content")})
        with open(path, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        # An audit failure must not turn a successful transaction into a false
        # failure, but the approval record itself remains durable.
        pass


def _draft_snapshot(draft_id):
    """Read and hash one immutable UTF-8 draft snapshot."""
    d = creator._draft_dir(draft_id)
    meta = creator._load_meta(d)
    if not meta:
        raise ValueError("草稿不存在：%s" % draft_id)
    kind = str(meta.get("kind") or "")
    if kind not in ("plugin", "skill"):
        raise ValueError("草稿类型不受支持")

    files = []
    for base, dirs, names in os.walk(d, followlinks=False):
        for dirname in list(dirs):
            child = os.path.join(base, dirname)
            if os.path.islink(child) or getattr(os.path, "isjunction", lambda p: False)(child):
                raise ValueError("草稿包含符号链接或 junction：%s" % dirname[:80])
        dirs[:] = sorted(dirs)
        for name in sorted(names):
            if name == "meta.json":
                continue
            full = os.path.join(base, name)
            if os.path.islink(full) or not os.path.isfile(full):
                raise ValueError("草稿包含不安全文件：%s" % name[:80])
            rel = os.path.relpath(full, d).replace(os.sep, "/")
            rel, err = creator._safe_relpath(rel, kind)
            if err:
                raise ValueError(err)
            with open(full, "rb") as stream:
                data = stream.read()
            files.append({
                "name": rel,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "data": data,
            })
    if not files:
        raise ValueError("草稿没有可安装文件")

    files.sort(key=lambda item: item["name"])
    digest_material = "\n".join("%s  %s" % (f["sha256"], f["name"]) for f in files).encode("utf-8")
    digest = hashlib.sha256(digest_material).hexdigest()
    manifest = {}
    if kind == "plugin":
        for f in files:
            if f["name"].lower() == "zxplugin.json" or f["name"].lower().endswith("/zxplugin.json"):
                try:
                    manifest = json.loads(f["data"].decode("utf-8"))
                except (ValueError, UnicodeError):
                    manifest = {}
                break
        entries = [f["name"] for f in files if f["name"].lower().endswith(".py")]
        directory_entry = next((name for name in entries
                                if name.lower().endswith("/__init__.py")), "")
        layout = "directory" if directory_entry else "single"
        target = (directory_entry.split("/", 1)[0] if directory_entry else
                  (os.path.splitext(entries[0])[0] if entries else ""))
        if isinstance(manifest, dict) and manifest.get("name"):
            target = str(manifest.get("name"))
        if layout == "directory" and any(
                not f["name"].startswith(target + "/") or
                "/" in f["name"].split("/", 1)[1]
                for f in files):
            raise ValueError("目录插件文件必须位于同一个目录且不得嵌套")
    else:
        for f in files:
            if f["name"] == "skill.json":
                try:
                    manifest = json.loads(f["data"].decode("utf-8"))
                except (ValueError, UnicodeError):
                    manifest = {}
                break
        target = str(manifest.get("id") or "") if isinstance(manifest, dict) else ""
        if not target:
            raise ValueError("skill.json 缺少 id")

    return {
        "draft_id": str(draft_id), "version": int(meta.get("version") or 0),
        "kind": kind, "target_name": target,
        "layout": layout if kind == "plugin" else "skill",
        "digest": digest,
        "files": [{k: f[k] for k in ("name", "sha256", "bytes")} for f in files],
        "_data": files,
        "validation": meta.get("validation") or {},
    }


def _public(record):
    out = dict(record)
    out.pop("approved_by", None)
    out.pop("_data", None)
    return out


def _valid_for_install(snap):
    # Re-run the existing static validator against the immutable bytes that
    # will be packaged, rather than validating a mutable draft path.
    try:
        with tempfile.TemporaryDirectory(prefix="creator_review_") as stage:
            for item in snap["_data"]:
                dst = os.path.join(stage, *item["name"].split("/"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as stream:
                    stream.write(item["data"])
            if snap["kind"] == "plugin":
                from plugin_structure import check_plugin_directory
                plugin_root = (os.path.join(stage, snap["target_name"])
                               if snap.get("layout") == "directory" else stage)
                report = check_plugin_directory(plugin_root, require_chat_tools=True)
            else:
                from skill_structure import check_skill_directory
                report = check_skill_directory(stage, package_name=snap["target_name"])
    except Exception as exc:
        return False, "静态检查器不可用：%s" % str(exc)[:120]
    if not report or not report.get("ok"):
        errors = (report or {}).get("errors") or []
        return False, "; ".join(str(e.get("message") or e) for e in errors[:3]) or "静态验证未通过"
    current = _draft_snapshot(snap["draft_id"])
    if current["digest"] != snap["digest"] or current["files"] != snap["files"]:
        return False, "草稿内容已改变，原审批已失效"
    return True, ""


def _target_exists(kind, target):
    if kind == "plugin":
        import plugin_ops
        return (os.path.lexists(os.path.join(plugin_ops._PLUG_DIR, target + ".py")) or
                os.path.lexists(os.path.join(plugin_ops._PLUG_DIR, target)))
    from core_features import skill as skill_ops
    return os.path.lexists(os.path.join(skill_ops._skill_dir(), target))


def approve(draft_id, identity, ttl=APPROVAL_TTL_SECONDS):
    """Create one pending approval after a fresh static validation."""
    identity = identity or {}
    if not identity.get("is_admin"):
        raise PermissionError("只有管理员可以审批 Creator 草稿")
    with _LOCK:
        snap = _draft_snapshot(draft_id)
        ok, err = _valid_for_install(snap)
        if not ok:
            raise ValueError(err)
        if _target_exists(snap["kind"], snap["target_name"]):
            raise ValueError("已存在同名扩展，默认拒绝覆盖：%s" % snap["target_name"])
        now = time.time()
        aid = "ca_" + secrets.token_urlsafe(24).replace("-", "_").replace(".", "_")
        record = {
            "approval_id": aid,
            "draft_id": snap["draft_id"], "version": snap["version"],
            "digest": snap["digest"], "kind": snap["kind"],
            "target_name": snap["target_name"], "files": snap["files"],
            "validation_ok": True,
            "approved_at": _iso(now), "expires_at": _iso(now + max(1, int(ttl))),
            "expires_at_epoch": now + max(1, int(ttl)),
            "approved_by": {"device_id": str(identity.get("device_id") or ""),
                            "token_kind": str(identity.get("token_kind") or "")},
            "state": "pending", "used_at": None, "install_result": None,
        }
        _write(record)
        _audit("approved", record)
        return _public(record)


def list_approvals(draft_id=""):
    out = []
    with _LOCK:
        try:
            names = sorted(n for n in os.listdir(_root()) if n.endswith(".json") and n != "audit.jsonl")
        except OSError:
            names = []
        for name in names:
            rec = _read(name[:-5])
            if not rec or (draft_id and rec.get("draft_id") != draft_id):
                continue
            out.append(_public(rec))
    return out


def _zip_bytes(snap):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        prefix = (snap["target_name"] + "/") if snap["kind"] == "skill" else ""
        for f in snap["_data"]:
            zf.writestr(prefix + f["name"], f["data"])
    return buf.getvalue()


def install(approval_id, identity):
    """Consume an approval exactly once and delegate to the P4 transaction."""
    identity = identity or {}
    if not identity.get("is_admin"):
        raise PermissionError("只有管理员可以安装 Creator 草稿")
    with _LOCK:
        rec = _read(approval_id)
        if not rec:
            raise ValueError("审批不存在")
        approved_by = rec.get("approved_by") or {}
        if (str(identity.get("token_kind") or "") != str(approved_by.get("token_kind") or "") or
                str(identity.get("device_id") or "") != str(approved_by.get("device_id") or "")):
            raise PermissionError("当前管理员身份与批准此草稿的身份不一致")
        if rec.get("state") != "pending":
            raise ValueError("审批已使用或已失效")
        if float(rec.get("expires_at_epoch") or 0) <= time.time():
            rec["state"] = "expired"
            _write(rec)
            _audit("expired", rec)
            raise ValueError("审批已过期")
        snap = _draft_snapshot(rec.get("draft_id"))
        if (snap["version"] != rec.get("version") or snap["kind"] != rec.get("kind") or
                snap["target_name"] != rec.get("target_name") or snap["digest"] != rec.get("digest") or
                snap["files"] != rec.get("files")):
            rec["state"] = "invalidated"
            _write(rec)
            _audit("invalidated", rec)
            raise ValueError("草稿内容或版本已改变，原审批已失效")
        ok, err = _valid_for_install(snap)
        if not ok:
            rec["state"] = "invalidated"
            _write(rec)
            _audit("invalidated", rec)
            raise ValueError(err)

        # Coordinate the same-name guard with existing extension transactions.
        # Their shared process lock is reentrant, so the delegated install can
        # safely acquire it again while preserving the no-overwrite default.
        import plugin_transaction
        plugin_transaction.LOCK.acquire()
        try:
            if _target_exists(snap["kind"], snap["target_name"]):
                rec["state"] = "invalidated"
                _write(rec)
                _audit("target_exists", rec)
                raise ValueError("已存在同名扩展，默认拒绝覆盖：%s" % snap["target_name"])
            # Mark consumed before entering the filesystem transaction.  A
            # crash or concurrent request cannot replay this authorization.
            rec["state"] = "installing"
            rec["used_at"] = _iso()
            _write(rec)
            blob = _zip_bytes(snap)
            if snap["kind"] == "plugin":
                import plugin_ops
                fd, path = tempfile.mkstemp(prefix="creator_", suffix=".zip")
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(blob)
                    ok, result = plugin_ops.install(path, note="Creator Mode MVP-2")
                finally:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            else:
                from core_features import skill as skill_ops
                result = skill_ops._install_zip(blob, note="Creator Mode MVP-2")
                ok = bool(result.get("ok"))
        except ValueError:
            raise
        except Exception as exc:
            ok, result = False, {"ok": False, "err": str(exc)[:180], "rollback": "unknown"}
        finally:
            plugin_transaction.LOCK.release()
        rec["state"] = "used" if ok else "failed"
        rec["install_result"] = result
        _write(rec)
        _audit("installed" if ok else "install_failed", rec,
               {"rollback": result.get("rollback"), "ok": bool(ok)})
        return {"ok": bool(ok), "approval": _public(rec), "install": result}
