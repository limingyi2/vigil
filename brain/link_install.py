# -*- coding: utf-8 -*-
"""把用户发来的一个链接解析成可安装的插件条目（P22 · 链接安装）。

装一条链接要回答两个问题，本模块只负责把这两件事查清楚：

1. **这是哪一份清单？** 只有带 ``market.json`` 的插件市场仓库能提供版本、
   协议、权限和制品 SHA-256；裸 zip 链接没有这些声明，装它等于让用户在
   不知道内容的情况下点确认。
2. **这份清单可信吗？** 可信来源（默认只有官方市场仓库，可在配置
   ``link_install.trusted_repos`` 里追加）直接装；其他仓库一律先出审批卡。

 这里**不**引入"任意网址都能装"的第二套信任模型。非可信来源同样要求
market.json + 制品 SHA-256 + Manifest 逐文件哈希，审批只改变"谁点头"，
不替代任何一项校验。没有清单的链接直接拒绝，并说明要发布什么才能装。
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

_REPO_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
_MARKET_LIMIT = 512 * 1024


class LinkError(ValueError):
    """链接无法解析成一个可核验的插件条目。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code or "link_error")


def _market_module():
    """市场管理器：核心包（随 Brain 发布），不在可选插件里。"""
    from core_features import plugin_market
    return plugin_market


def _config():
    try:
        from paths import CONFIG_DIR
        import os
        path = os.path.join(str(CONFIG_DIR), "config.json")
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def trusted_repos() -> set[str]:
    """可信来源清单（owner/repo，小写）。默认只有官方市场仓库。"""
    trusted = {str(_market_module().REMOTE_REPO).strip().lower()}
    extra = (_config().get("link_install") or {}).get("trusted_repos")
    if isinstance(extra, list):
        trusted |= {str(x).strip().lower() for x in extra
                    if isinstance(x, str) and x.strip()}
    return trusted


def parse(url: str) -> dict:
    """把一个 https 链接拆成市场来源（owner/repo/ref/插件名）。

    只认三种形状：GitHub 仓库首页、GitHub tree/ 目录、raw 上的 market.json。
    制品直链与裸 zip 一律拒绝 —— 它们没有所属清单，核对不了权限与哈希。
    """
    raw = str(url or "").strip()
    if not raw:
        raise LinkError("empty_link", "请给出一个插件链接")
    if len(raw) > 500:
        raise LinkError("link_too_long", "链接过长，请给出插件仓库或插件目录的地址")
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        raise LinkError("insecure_link", "只接受 https 链接")
    host = (parsed.hostname or "").lower()
    parts = [p for p in (parsed.path or "").split("/") if p]

    if host in ("github.com", "www.github.com"):
        if len(parts) < 2:
            raise LinkError("unsupported_link", "GitHub 链接至少要包含「用户/仓库」")
        owner, repo = parts[0], parts[1]
        ref, name = "main", ""
        tail = parts[2:]
        if tail:
            if tail[0] not in ("tree", "blob") or len(tail) < 2:
                raise LinkError(
                    "unsupported_link",
                    "只支持仓库首页或 tree/<分支>/<插件目录> 形式的链接")
            ref = tail[1]
            if tail[0] == "tree" and len(tail) >= 3:
                name = tail[2]
        _check_parts(owner, repo, ref)
        return {"kind": "market_source", "host": host, "owner": owner,
                "repo": repo, "ref": ref, "name": name, "url": raw}

    if host in _market_module().ALLOWED_HOSTS:
        if len(parts) >= 4 and parts[-1] == "market.json":
            owner, repo, ref = parts[0], parts[1], parts[2]
            _check_parts(owner, repo, ref)
            return {"kind": "market_source", "host": host, "owner": owner,
                    "repo": repo, "ref": ref, "name": "", "url": raw}
        raise LinkError(
            "artifact_link",
            "这是制品直链，缺少所属清单，无法核对版本、权限和哈希。"
            "请发插件市场仓库的地址（含 market.json），或其中某个插件的目录链接")

    raise LinkError(
        "unsupported_host",
        "只支持 GitHub 上的插件仓库链接；%s 不在允许的来源里" % (host or "该地址"))


def _check_parts(owner: str, repo: str, ref: str) -> None:
    if not _REPO_PART_RE.match(owner or "") or not _REPO_PART_RE.match(repo or ""):
        raise LinkError("invalid_repo", "仓库名不合法")
    if ref not in ("main", "master") and not _REF_RE.match(ref or ""):
        raise LinkError("invalid_ref", "分支名不合法")


def source_key(parsed: dict) -> str:
    return "%s/%s@%s" % (parsed["owner"], parsed["repo"], parsed["ref"])


def is_trusted(parsed: dict) -> bool:
    key = ("%s/%s" % (parsed["owner"], parsed["repo"])).lower()
    return key in trusted_repos()


def load_market(parsed: dict) -> dict:
    """读取并校验该仓库的 market.json（域名白名单 + 条目字段 + 制品哈希）。"""
    market = _market_module()
    url = "https://raw.githubusercontent.com/%s/%s/%s/market.json" % (
        parsed["owner"], parsed["repo"], parsed["ref"])
    data = market._fetch_bytes(url, _MARKET_LIMIT)
    try:
        payload = json.loads(data.decode("utf-8-sig"))
    except Exception as exc:
        raise LinkError("invalid_manifest", "market.json 不是合法 JSON：%s" % str(exc)[:120])
    payload = market._validate_market(payload, "link:%s" % source_key(parsed))
    if payload.get("state") == "read_failed":
        raise LinkError("invalid_manifest",
                        payload.get("err") or "market.json 校验未通过")
    return payload


def _summary(entry: dict) -> dict:
    artifact = entry.get("artifact") or {}
    return {
        "name": entry.get("name"),
        "version": entry.get("version"),
        "description": str(entry.get("description") or "")[:200],
        "permissions": list(entry.get("permissions") or []),
        "sha256": str(artifact.get("sha256") or "")[:16],
    }


def plan(url: str, name: str = "") -> dict:
    """解析链接 → 定位插件条目 → 给出"装不装、要不要人确认"的计划。

    返回 ``kind="plugin"`` 表示可以安装；``kind="choice"`` 表示该仓库有多个
    插件、需要先指明装哪个（不猜）。
    """
    parsed = parse(url)
    market = load_market(parsed)
    entries = [e for e in (market.get("plugins") or []) if isinstance(e, dict)]
    key = source_key(parsed)
    wanted = str(name or parsed.get("name") or "").strip()

    if wanted:
        entry = next((e for e in entries if e.get("name") == wanted), None)
        if entry is None:
            raise LinkError(
                "plugin_not_in_source",
                "该仓库清单里没有插件 %s；现有：%s" % (
                    wanted, "、".join(str(e.get("name")) for e in entries) or "无"))
    elif len(entries) == 1:
        entry = entries[0]
    elif entries:
        return {"ok": True, "kind": "choice", "source": key,
                "trusted": is_trusted(parsed),
                "candidates": [_summary(e) for e in entries],
                "detail": "这个仓库里有 %d 个插件，需要指明装哪一个" % len(entries)}
    else:
        raise LinkError("empty_source", "该仓库的 market.json 里没有可安装的插件")

    return {"ok": True, "kind": "plugin", "source": key,
            "trusted": is_trusted(parsed),
            "name": entry["name"], "entry": entry,
            "summary": _summary(entry)}


def execute(plan_data: dict, *, replace: bool = False) -> dict:
    """按计划安装。校验全部在 install_entry 里，这里只负责落地与回报。"""
    entry = plan_data.get("entry") or {}
    name = str(entry.get("name") or "")
    if not name:
        return {"ok": False, "error": "invalid_plan", "detail": "计划里没有插件名"}
    market = _market_module()
    result = market.install_entry(
        entry, name, replace=replace,
        note_source="link:%s" % plan_data.get("source", ""))
    result.setdefault("source", plan_data.get("source", ""))
    result.setdefault("version", entry.get("version", ""))
    return result
