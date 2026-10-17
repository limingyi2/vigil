# -*- coding: utf-8 -*-
"""插件开发目录的静态结构检查。

这个模块只读开发目录，不 import 插件代码、不安装、不执行任何钩子。
规则复用 ``plugin_ops`` 的扩展名边界和 Manifest 校验，并对
``plugin_host`` 当前支持的单文件和 ``__init__.py`` 目录插件分别检查；Creator 插件还必须满足可调用聊天工具契约。
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
_SINGLE_EXTRAS = set(plugin_ops.ALLOWED_EXT) - {".js", ".css"}


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


def _module_assignment_value(tree, name):
    """Return the last module-level assignment value for *name*, if any."""
    found = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            found = node.value
    return found


def _literal_dict_keys(node):
    """Return unique literal string keys for a dict expression, otherwise None."""
    if not isinstance(node, ast.Dict) or not node.keys:
        return None
    keys = []
    for key in node.keys:
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return None
        keys.append(key.value)
    return keys if len(keys) == len(set(keys)) else None


def _literal_string_list(node):
    """Return unique non-empty string values for a literal list/tuple, otherwise None."""
    if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
        return None
    try:
        values = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    if not isinstance(values, (list, tuple)) or any(
        not isinstance(value, str) or not value.strip() for value in values
    ):
        return None
    return list(values) if len(values) == len(set(values)) else None


def _check_chat_tool_contract(tree, module_functions, errors, warnings, required=False):
    """Check both the current mapping contract and legacy Creator list dispatch."""
    issues = errors if required else warnings
    tools_node = _module_assignment_value(tree, "TOOLS")
    specs_node = _module_assignment_value(tree, "TOOL_SPECS")
    tool_names = _literal_dict_keys(tools_node)
    tools_list_contract = False

    if tools_node is None:
        issues.append(_issue(
            "missing_tool_implementation",
            "插件必须声明 TOOLS，并提供至少一个可由聊天调用的工具实现"
        ))
    elif tool_names is not None:
        for name, value in zip(tool_names, tools_node.values):
            if not isinstance(value, ast.Name) or value.id not in module_functions:
                issues.append(_issue(
                    "invalid_tool_implementation",
                    "工具 %s 必须指向同一文件中定义的模块级函数" % name
                ))
                break
    else:
        list_names = _literal_string_list(tools_node)
        if list_names is not None and "call_tool" in module_functions:
            tool_names = list_names
            tools_list_contract = True
        else:
            issues.append(_issue(
                "invalid_tool_implementation",
                "TOOLS 必须是工具名到模块级函数的字典，或配套 call_tool(name, args) 的工具名列表"
            ))

    if specs_node is None:
        issues.append(_issue(
            "missing_tool_specs",
            "插件必须声明 TOOL_SPECS；否则原生聊天模型看不到工具"
        ))
        specs = None
        spec_names = None
        list_schema_contract = False
    else:
        try:
            raw_specs = ast.literal_eval(specs_node)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            raw_specs = None

        if isinstance(raw_specs, dict) and _literal_dict_keys(specs_node) is not None:
            specs = raw_specs
            spec_names = list(raw_specs)
            list_schema_contract = False
        elif isinstance(raw_specs, (list, tuple)) and tools_list_contract:
            specs = {}
            spec_names = []
            list_schema_contract = True
            valid_list = bool(raw_specs)
            for item in raw_specs:
                if not isinstance(item, dict):
                    valid_list = False
                    break
                name = item.get("name")
                desc = item.get("description") or item.get("desc")
                schema = item.get("parameters")
                if (not isinstance(name, str) or not name.strip()
                        or not isinstance(desc, str) or not desc.strip()
                        or not isinstance(schema, dict) or schema.get("type") != "object"
                        or not isinstance(schema.get("properties", {}), dict)
                        or not isinstance(schema.get("required", []), list)
                        or any(not isinstance(key, str) for key in schema.get("required", []))):
                    valid_list = False
                    break
                properties = schema.get("properties", {})
                required_names = schema.get("required", [])
                if any(key not in properties for key in required_names):
                    valid_list = False
                    break
                params = []
                for pname, pdef in properties.items():
                    if (not isinstance(pname, str) or not pname.strip()
                            or not isinstance(pdef, dict)):
                        valid_list = False
                        break
                    pdesc = pdef.get("description") or pname
                    if not isinstance(pdesc, str) or not pdesc.strip():
                        valid_list = False
                        break
                    params.append((pname, pdesc, pname in required_names))
                if not valid_list or name in specs:
                    valid_list = False
                    break
                specs[name] = {
                    "desc": desc,
                    "params": params,
                    "arg_param": params[0][0] if len(params) == 1 else None,
                    "multi_args": True,
                    "parameters": schema,
                }
                spec_names.append(name)
            if not valid_list:
                issues.append(_issue(
                    "invalid_tool_specs",
                    "列表格式 TOOL_SPECS 必须是工具名唯一、含 description 和合法 object parameters 的规格列表"
                ))
                specs = None
                spec_names = None
        else:
            issues.append(_issue(
                "invalid_tool_specs",
                "TOOL_SPECS 必须是规格字典，或与 TOOLS 名称列表配套的规格列表"
            ))
            specs = None
            spec_names = None
            list_schema_contract = False

    if specs is not None:
        for name, spec in specs.items():
            if not isinstance(name, str) or not name.strip() or not isinstance(spec, dict):
                issues.append(_issue(
                    "invalid_tool_specs",
                    "每个 TOOL_SPECS 项都必须以非空工具名映射到规格字典"
                ))
                continue
            if not isinstance(spec.get("desc"), str) or not spec["desc"].strip():
                issues.append(_issue(
                    "invalid_tool_specs",
                    "工具 %s 缺少非空 desc" % name
                ))
            params = spec.get("params", [])
            if not isinstance(params, (list, tuple)):
                issues.append(_issue(
                    "invalid_tool_specs",
                    "工具 %s 的 params 必须是参数三元组列表" % name
                ))
                continue
            param_names = []
            params_valid = True
            for item in params:
                if (not isinstance(item, (list, tuple)) or len(item) != 3
                        or not isinstance(item[0], str) or not item[0].strip()
                        or not isinstance(item[1], str) or not item[1].strip()
                        or type(item[2]) is not bool):
                    params_valid = False
                    break
                param_names.append(item[0])
            if not params_valid or len(param_names) != len(set(param_names)):
                issues.append(_issue(
                    "invalid_tool_specs",
                    "工具 %s 的每个 params 项必须是 (名称, 说明, 必填布尔值)，且名称不能重复" % name
                ))
                continue
            arg_param = spec.get("arg_param")
            if len(param_names) > 1 and not list_schema_contract:
                issues.append(_issue(
                    "unsupported_tool_params",
                    "Creator 当前每个字典格式工具最多支持一个字符串参数；工具 %s 定义了多个参数" % name
                ))
            elif len(param_names) == 1 and not list_schema_contract and arg_param != param_names[0]:
                issues.append(_issue(
                    "invalid_tool_specs",
                    "工具 %s 的 arg_param 必须指向唯一参数 %s" % (name, param_names[0])
                ))
            elif not param_names and not list_schema_contract and arg_param is not None:
                issues.append(_issue(
                    "invalid_tool_specs",
                    "无参数工具 %s 的 arg_param 必须为 null" % name
                ))

    if tool_names is not None and spec_names is not None and set(tool_names) != set(spec_names):
        issues.append(_issue(
            "tool_name_mismatch",
            "TOOLS 与 TOOL_SPECS 的工具名称必须完全一致"
        ))


def _check_python(path, package_name, errors, warnings, layout="single", require_chat_tools=False):
    try:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=path)
    except (OSError, UnicodeError, SyntaxError) as exc:
        errors.append(_issue("python_syntax", "%s 不是有效 Python：%s" % (
            os.path.basename(path), str(exc)[:160])))
        return

    module_functions = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    module_assignments = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        module_assignments.update(
            target.id for target in targets if isinstance(target, ast.Name)
        )

    if "on_message" in module_functions:
        warnings.append(_issue(
            "unsupported_on_message_hook",
            "当前插件宿主不会调用 on_message；请使用 TOOLS 与 TOOL_SPECS 实现聊天功能"
        ))
    _check_chat_tool_contract(tree, module_functions, errors, warnings, required=require_chat_tools)
    if layout == "single" and any(
        isinstance(node, ast.Constant) and isinstance(node.value, str)
        and ("<!doctype html" in node.value.lower() or "<html" in node.value.lower())
        for node in ast.walk(tree)
    ):
        issue = _issue(
            "html_string_not_ui",
            "单文件插件中的 HTML 字符串不会自动变成插件页面；当前 Creator 不支持自定义前端 UI"
        )
        if require_chat_tools:
            errors.append(issue)
        else:
            warnings.append(issue)

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


def check_plugin_directory(path, require_chat_tools=False):
    """静态检查一个插件开发目录，返回可序列化的报告字典。

    ``ok`` 表示基础结构和 Manifest 通过；传入 ``require_chat_tools=True`` 时，聊天工具契约也必须通过。默认仅警告，以兼容既有的非聊天型插件。
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
        if package_name.startswith(("_", ".")):
            report["errors"].append(_issue("hidden_directory_name",
                                           "插件目录名不能以 _ 或 . 开头：%s" % package_name))
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
        if name.lower() in ("frontend.js", "frontend.css") and name not in ("frontend.js", "frontend.css"):
            report["errors"].append(_issue("frontend_filename_case",
                                           "前端资源必须准确命名为 frontend.js 或 frontend.css：%s" % name))
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
                      report["errors"], report["warnings"], report["layout"],
                      require_chat_tools=require_chat_tools)
    report["manifest"] = _read_manifest(root, package_name, names, report["errors"])
    report["ok"] = not report["errors"]
    return report
