# -*- coding: utf-8 -*-
"""静态检查 Node Skill 目录，不导入也不执行 JavaScript。

规则与 ``brain/skill_host/server.js`` 的实际扫描规则保持一致：支持单层
目录和同名双层目录。双层目录只有内层 ``index.js`` 会被当前 Host 自动下钻；
``index.mjs`` / ``index.cjs`` 需要放在 Skill 根目录并由 Manifest 的 ``entry``
明确指定。
"""
import json
import os
import posixpath
import re


ENTRY_NAMES = ("index.js", "index.mjs", "index.cjs")
_PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_IDENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *("COM%d" % i for i in range(1, 10)),
    *("LPT%d" % i for i in range(1, 10)),
}
_ALLOWED_EXT = {".js", ".mjs", ".cjs", ".json", ".md"}
_KNOWN_TEXT = {
    "id": 64,
    "name": 120,
    "version": 64,
    "description": 500,
    "entry": 160,
}


def _issue(code, message):
    return {"code": code, "message": message}


def _reserved_stem(name):
    stem = str(name or "").split(".", 1)[0].rstrip(" .").upper()
    return stem if stem in _WINDOWS_RESERVED else ""


def _name_issue(name, label="文件名"):
    name = str(name or "")
    if not name or name in (".", ".."):
        return _issue("invalid_name", "%s 不能为空或为 . / ..：%s" % (label, name))
    if any(ord(ch) < 32 for ch in name):
        return _issue("invalid_name", "%s 不能包含控制字符：%s" % (label, name[:80]))
    if any(ch in name for ch in ("/", "\\", ":", "<", ">", '"', "|", "?", "*")):
        return _issue("invalid_name", "%s 不能包含路径或非法字符：%s" % (label, name[:80]))
    if name.endswith((".", " ")):
        return _issue("invalid_name", "%s 不能以点或空格结尾：%s" % (label, name[:80]))
    reserved = _reserved_stem(name)
    if reserved:
        return _issue("windows_reserved_name",
                      "%s 使用 Windows 保留名称 %s：%s" % (label, reserved, name[:80]))
    return None


def _rel_issue(rel):
    """Validate a relative POSIX path before it reaches the filesystem."""
    raw = str(rel or "")
    if "\\" in raw:
        return _issue("backslash_path", "路径不能包含反斜杠：%s" % raw[:100])
    if raw.startswith("/") or raw.startswith("~") or ":" in raw:
        return _issue("absolute_path", "路径必须是 Skill 根目录内的相对路径：%s" % raw[:100])
    parts = raw.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return _issue("path_escape", "路径不能包含空段、. 或 ..：%s" % raw[:100])
    for part in parts:
        issue = _name_issue(part, "路径段")
        if issue:
            return issue
    return None


def _manifest(root, package, names, errors):
    candidates = [n for n in names if n.casefold() == "skill.json"]
    if not candidates:
        return {}
    if len(candidates) != 1 or candidates[0] != "skill.json":
        errors.append(_issue("manifest_name", "Manifest 必须命名为小写 skill.json，且只能有一个"))
        return {}
    path = os.path.join(root, "skill.json")
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, UnicodeError, ValueError) as exc:
        errors.append(_issue("manifest_json",
                             "skill.json 必须是有效 UTF-8 JSON：%s" % str(exc)[:140]))
        return {}
    if not isinstance(value, dict):
        errors.append(_issue("manifest_type", "skill.json 顶层必须是 JSON 对象"))
        return {}
    for key, limit in _KNOWN_TEXT.items():
        if key not in value:
            continue
        if not isinstance(value[key], str):
            errors.append(_issue("manifest_type", "skill.json 的 %s 必须是字符串" % key))
            continue
        if not value[key].strip() and key in ("id", "name", "version", "entry"):
            errors.append(_issue("manifest_value", "skill.json 的 %s 不能为空" % key))
        if len(value[key]) > limit:
            errors.append(_issue("manifest_length",
                                 "skill.json 的 %s 超过 %d 个字符" % (key, limit)))
    if "settings" in value and not isinstance(value["settings"], dict):
        errors.append(_issue("manifest_type", "skill.json 的 settings 必须是 JSON 对象"))
    ident = value.get("id")
    if isinstance(ident, str):
        if not _IDENT_RE.fullmatch(ident) or _reserved_stem(ident):
            errors.append(_issue("manifest_id", "skill.json 的 id 不是合法 Skill 名称"))
        elif ident != package:
            errors.append(_issue("manifest_id_mismatch",
                                 "skill.json 的 id 必须与 Skill 目录名一致：%s" % package))
    entry = value.get("entry", "index.js")
    if isinstance(entry, str):
        issue = _rel_issue(entry)
        if issue:
            errors.append(_issue("manifest_entry", issue["message"]))
        elif entry not in names:
            errors.append(_issue("manifest_entry_missing",
                                 "skill.json 的 entry 不存在于 Skill 根目录内：%s" % entry))
        elif os.path.isdir(os.path.join(root, *entry.split("/"))):
            errors.append(_issue("manifest_entry_type", "skill.json 的 entry 必须指向普通文件"))
    return value


def check_skill_directory(path, package_name=None):
    """Return a serializable static report for a Skill development directory.

    ``package_name`` is used by the ZIP installer while the content is in a
    temporary directory whose basename is ``stage`` rather than the final
    package name.
    """
    root = os.path.abspath(os.fspath(path))
    report = {"ok": False, "path": root, "root": root, "layout": "",
              "entry": "", "files": [], "manifest": {},
              "errors": [], "warnings": []}
    if not os.path.isdir(root) or os.path.islink(root):
        report["errors"].append(_issue("not_directory", "不是普通 Skill 目录：%s" % root))
        return report

    try:
        top = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError as exc:
        report["errors"].append(_issue("read_directory", "读不了 Skill 目录：%s" % exc))
        return report

    for entry in top:
        issue = _name_issue(entry.name)
        if issue:
            report["errors"].append(issue)
        if entry.is_symlink():
            report["errors"].append(_issue("symlink", "不允许符号链接：%s" % entry.name))

    package = str(package_name or os.path.basename(root))
    real = root
    direct_names = [e.name for e in top]
    direct_entries = [n for n in direct_names if n in ENTRY_NAMES]
    if direct_entries:
        report["layout"] = "single"
    else:
        kids = [e for e in top if e.is_dir() and not e.is_symlink()
                and not e.name.startswith((".", "_"))]
        same = [e for e in kids if e.name == package]
        inner = os.path.join(root, package)
        if len(kids) == 1 and len(same) == 1 and os.path.isfile(os.path.join(inner, "index.js")):
            real = inner
            report["layout"] = "double"
            direct_names = []
            for e in os.scandir(real):
                direct_names.append(e.name)
            outer_extra = [e.name for e in top if e.name != package]
            if outer_extra:
                report["errors"].append(_issue("outer_extra",
                                               "双层 Skill 外层不能有被 Host 忽略的文件：%s" %
                                               ", ".join(outer_extra[:5])))
        else:
            report["errors"].append(_issue("entry_missing",
                                           "Skill 根目录没有 Host 可识别的入口文件"))

    package = str(package_name or os.path.basename(real))
    report["root"] = real
    if not _PKG_RE.fullmatch(package) or _reserved_stem(package):
        report["errors"].append(_issue("invalid_package_name",
                                       "Skill 目录名不是合法名称：%s" % package))

    # Walk the real root without following links and retain relative paths for
    # duplicate case-fold checks and Manifest entry resolution.
    rel_names = []
    try:
        for base, dirs, files in os.walk(real, topdown=True, followlinks=False):
            dirs.sort(); files.sort()
            kept = []
            for name in dirs:
                p = os.path.join(base, name)
                rel = os.path.relpath(p, real).replace(os.sep, "/")
                issue = _rel_issue(rel)
                if issue: report["errors"].append(issue)
                if os.path.islink(p):
                    report["errors"].append(_issue("symlink", "不允许符号链接：%s" % rel))
                kept.append(name)
            dirs[:] = kept
            for name in files:
                p = os.path.join(base, name)
                rel = os.path.relpath(p, real).replace(os.sep, "/")
                issue = _rel_issue(rel)
                if issue: report["errors"].append(issue)
                if os.path.islink(p):
                    report["errors"].append(_issue("symlink", "不允许符号链接：%s" % rel))
                elif not os.path.isfile(p):
                    report["errors"].append(_issue("special_file", "必须是普通文件：%s" % rel))
                rel_names.append(rel)
    except OSError as exc:
        report["errors"].append(_issue("walk_failed", "读取 Skill 文件失败：%s" % exc))

    report["files"] = sorted(rel_names)
    folded = {}
    for rel in report["files"]:
        key = rel.casefold()
        if key in folded and folded[key] != rel:
            report["errors"].append(_issue("duplicate_path",
                                           "文件路径大小写冲突：%s / %s" % (folded[key], rel)))
        else:
            folded[key] = rel
        ext = os.path.splitext(rel)[1].lower()
        if ext not in _ALLOWED_EXT:
            report["errors"].append(_issue("unsupported_file", "不支持的文件类型：%s" % rel))

    entry = ""
    if report["layout"] in ("single", "double"):
        names = report["files"]
        if "skill.json" in names:
            report["manifest"] = _manifest(real, package, names, report["errors"])
        elif any(n.casefold() == "skill.json" for n in names):
            report["errors"].append(_issue("manifest_name", "Manifest 必须命名为小写 skill.json"))
        entry = report["manifest"].get("entry", "index.js") if isinstance(report["manifest"], dict) else "index.js"
        if entry == "index.js" and "index.js" not in names:
            report["errors"].append(_issue("entry_missing", "当前 Host 默认入口 index.js 不存在"))
        elif entry and entry in names and not entry.lower().endswith(ENTRY_NAMES):
            report["errors"].append(_issue("entry_type", "entry 必须是 index.js/index.mjs/index.cjs 或同类入口文件"))
        report["entry"] = entry
    report["ok"] = not report["errors"]
    return report


check_plugin_directory = check_skill_directory
