# -*- coding: utf-8 -*-
"""插件开发目录的静态结构检查。

这个模块只读开发目录，不 import 插件代码、不安装、不执行任何钩子。
规则复用 ``plugin_ops`` 的扩展名边界和 Manifest 校验，并对
``plugin_host`` 当前支持的单文件和 ``__init__.py`` 目录插件分别检查。
"""
import ast
import json
import os
import re

import plugin_ops


_PY_NAME_OK = re.compile(r"^[A-Za-z0-9_-]+\.py$")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}
_DIRECTORY_EXTRAS = {".json", ".md", ".txt", ".js", ".css"}
_SINGLE_EXTRAS = set(plugin_ops.ALLOWED_EXT)


def _issue(code, message):
    return {"code": code, "message": message}


def _reserved_stem(name):
    """Return the Windows device stem, if *name* is reserved."""
    stem = str(name or "").split(".", 1)[0].rstrip(" .").upper()
    return stem if stem in _WINDOWS_RESERVED else ""


def _filename_issue(name):
    """Check a filename without touching the filesystem."""
    if not name or name in (".", ".."):
        return _issue("invalid_filename", "文件名不能为空或为 . / ..：%s" % name)
    if any(ord(c) < 32 for c in name):
        return _issue("invalid_filename", "文件名不能包含控制字符：%s" % name[:60])
    if any(c in name for c in ("/", "\\", ":", "<", ">", "\"", "|", "?", "*")):
        return _issue("invalid_filename", "文件名不能包含路径或冒号：%s" % name[:60])
    if name.endswith((".", " ")):
        return _issue("invalid_filename", "文件名不能以点或空格结尾：%s" % name[:60])
    reserved = _reserved_stem(name)
    if reserved:
        return _issue("windows_reserved_name",
                      "文件名使用 Windows 保留名称 %s：%s" % (reserved, name[:60]))
    return None


def _literal_assignment(tree, key):
    """Return a string literal assigned to a module-level name, or None."""
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == key for t in targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        return None
    return None


def _check_python(path, package_name, errors, warnings):
    try:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=path)
    except (OSError, UnicodeError, SyntaxError) as exc:
        errors.append(_issue("python_syntax", "%s 不是有效 Python：%s" % (
            os.path.basename(path), str(exc)[:160])))
        return

    module_name = _literal_assignment(tree, "NAME")
    if module_name is None:
        warnings.append(_issue("dynamic_name", "%s 没有可静态读取的 NAME；"
                               "运行时仍需提供合法 NAME" % os.path.basename(path)))
    elif not plugin_ops.re_name_ok_safe(module_name):
        errors.append(_issue("invalid_plugin_name",
                             "NAME 只能使用英文/数字/下划线/短横线：%s" % module_name))
    elif package_name and module_name != package_name:
        warnings.append(_issue("module_name_mismatch",
                               "代码中的 NAME=%s 与文件名/目录名 %s 不同；"
                               "加载器以代码中的 NAME 为准" % (module_name, package_name)))

    if _literal_assignment(tree, "DESC") is None:
        warnings.append(_issue("missing_desc",
                               "%s 没有静态 DESC；加载器允许，但建议补充描述" %
                               os.path.basename(path)))


def _read_manifest(root, package_name, names, errors):
    manifests = [n for n in names if n.lower() == "zxplugin.json"]
    if not manifests:
        return {}
    if len(manifests) > 1:
        errors.append(_issue("duplicate_manifest", "只能有一个 zxplugin.json"))
        return {}
    path = os.path.join(root, manifests[0])
    try:
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, UnicodeError, ValueError) as exc:
        errors.append(_issue("manifest_json",
                             "zxplugin.json 必须是有效 UTF-8 JSON 对象：%s" % str(exc)[:120]))
        return {}
    err = plugin_ops.validate_manifest(manifest, package_name)
    if err:
        errors.append(_issue("manifest_invalid", err))
        return manifest if isinstance(manifest, dict) else {}
    return manifest


def check_plugin_directory(path):
    """静态检查一个插件开发目录，返回可序列化的报告字典。

    ``ok`` 只表示结构和 Manifest 通过；warnings 不会触碰旧插件的兼容性，
    但会把动态 NAME、缺少 DESC 等运行时才可确定的事项明确列出来。
    """
    root = os.path.abspath(os.fspath(path))
    report = {"ok": False, "path": root, "layout": "", "entry": "",
              "files": [], "manifest": {}, "errors": [], "warnings": []}
    if not os.path.isdir(root):
        report["errors"].append(_issue("not_directory", "不是插件目录：%s" % root))
        return report

    try:
        with os.scandir(root) as scanned:
            entries = sorted(scanned, key=lambda e: e.name)
    except OSError as exc:
        report["errors"].append(_issue("read_directory", "读不了插件目录：%s" % exc))
        return report

    names = []
    for entry in entries:
        names.append(entry.name)
        issue = _filename_issue(entry.name)
        if issue:
            report["errors"].append(issue)
        try:
            if entry.is_symlink():
                report["errors"].append(_issue("symlink", "不允许符号链接：%s" % entry.name))
            elif not entry.is_file(follow_symlinks=False) and not entry.is_dir():
                report["errors"].append(_issue("special_file", "必须是普通文件：%s" % entry.name))
            elif entry.is_dir():
                report["errors"].append(_issue("nested_directory",
                                               "插件目录不能再嵌套目录：%s" % entry.name))
        except OSError as exc:
            report["errors"].append(_issue("stat_failed", "读不了 %s：%s" %
                                           (entry.name, exc)))
    report["files"] = names

    py_files = [n for n in names if n.lower().endswith(".py")]
    init_present = "__init__.py" in py_files
    if init_present:
        report["layout"] = "directory"
        if py_files != ["__init__.py"]:
            report["errors"].append(_issue("multiple_python_files",
                                           "目录插件只能有一个 __init__.py"))
        entry_name = "__init__.py"
        package_name = os.path.basename(root)
        root_issue = _filename_issue(package_name)
        if root_issue:
            report["errors"].append(root_issue)
        if not plugin_ops.re_name_ok_safe(package_name):
            report["errors"].append(_issue("invalid_directory_name",
                                           "目录名只能使用英文/数字/下划线/短横线：%s" %
                                           package_name))
        allowed = _DIRECTORY_EXTRAS
    else:
        report["layout"] = "single"
        if len(py_files) != 1:
            report["errors"].append(_issue("python_entry_count",
                                           "单文件插件必须恰好有一个 .py"))
            entry_name = py_files[0] if py_files else ""
        else:
            entry_name = py_files[0]
        package_name = os.path.splitext(entry_name)[0] if entry_name else ""
        if entry_name and not _PY_NAME_OK.match(entry_name):
            report["errors"].append(_issue("invalid_python_filename",
                                           "插件 .py 文件名不合法：%s" % entry_name))
        allowed = _SINGLE_EXTRAS
    report["entry"] = entry_name

    for name in names:
        if name == entry_name:
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in allowed:
            report["errors"].append(_issue("unsupported_file",
                                           "文件类型不在当前插件契约中：%s" % name))
        if name.lower() == "zxplugin.json" and name != "zxplugin.json":
            report["warnings"].append(_issue("manifest_case",
                                              "建议将 Manifest 命名为小写 zxplugin.json"))

    # Never read rejected links/special files, even to parse static metadata.
    if report["errors"]:
        return report
    if entry_name:
        _check_python(os.path.join(root, entry_name), package_name,
                      report["errors"], report["warnings"])
    report["manifest"] = _read_manifest(root, package_name, names, report["errors"])
    report["ok"] = not report["errors"]
    return report
