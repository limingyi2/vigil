# -*- coding: utf-8 -*-
"""Creator Mode MVP-1：自然语言 → 创作草稿 + 静态验证。

## 这个模块是什么

把"用一句话描述想要的能力"变成一份**可审核的草稿**：真实调用当前配置的
模型提供者生成代码文件，落进独立草稿工作区，再用 P4 的静态检查器验证，
全程不安装、不装载、不执行生成的任何代码。

## 边界（每条都有测试盯着，别绕）

1. **草稿只写 ``<DATA_DIR>/creator_drafts/``**，绝不写进正式插件/技能目录
   （``paths.PLUGINS_DIR`` / 技能目录）。本模块没有任何 install/reload 入口。
2. **生成走真实模型链路**：``model_providers.get_provider`` → ``provider.chat``，
   和 Brain 聊天用的是同一条路。没有"关键词匹配假装生成"的旁路 —— 模型没按
   契约返回就是失败，不会拿模板糊弄。
3. **静态验证不执行代码**：复用 ``plugin_structure`` / ``skill_structure``，
   它们只读文件、``ast.parse`` / ``json.load``。验证通过 ≠ 沙箱安全，
   这一点在 API 响应和客户端界面上都明说。
4. **模型输出的文件名先过严格校验再落盘**：拒绝绝对路径、``..``、反斜杠、
   控制字符、Windows 保留名和类型外的扩展名 —— 防止生成内容借路径逃出
   草稿目录。
5. **不自动装第三方依赖**：提示词里就要求无第三方依赖；装不装由人在
   MVP-2 的审核安装流程里决定。
"""
import datetime
import json
import os
import re
import secrets

import paths


# ── 草稿工作区 ────────────────────────────────────────────────

def drafts_root():
    """草稿根目录：跟着 VIGIL_HOME 走（测试/多实例各一份），不存在就建。"""
    root = os.path.join(paths.DATA_DIR, "creator_drafts")
    try:
        os.makedirs(root, exist_ok=True)
    except OSError:
        pass
    return root


def _new_id():
    return "cr_%s_%s" % (datetime.datetime.now().strftime("%Y%m%d%H%M%S"),
                         secrets.token_hex(3))


def _draft_dir(draft_id):
    """把草稿 id 解析成目录；id 只允许安全字符，挡路径穿越。"""
    did = str(draft_id or "")
    if not re.fullmatch(r"cr_[0-9a-zA-Z_]+", did):
        raise ValueError("草稿编号不合法：%s" % did[:40])
    root = os.path.realpath(drafts_root())
    d = os.path.realpath(os.path.join(root, did))
    if d != root and not d.startswith(root + os.sep):
        raise ValueError("草稿目录逃出了工作区")
    return d


# ── 文件名安全（落盘前的硬闸） ────────────────────────────────

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}
_PLUGIN_EXTS = (".py", ".json", ".md", ".txt")
_SKILL_EXTS = (".js", ".mjs", ".cjs", ".json", ".md")


def _safe_relpath(name, kind):
    """校验模型给的文件名。返回 (relpath, err)；err 非空即拒绝。"""
    raw = str(name or "").strip()
    if not raw:
        return "", "文件名不能为空"
    if len(raw) > 120:
        return "", "文件名过长：%s" % raw[:60]
    if "\\" in raw:
        return "", "文件名不能包含反斜杠：%s" % raw[:60]
    if raw.startswith("/") or raw.startswith("~") or ":" in raw:
        return "", "文件名必须是工作区内的相对路径：%s" % raw[:60]
    if raw.startswith("."):
        return "", "文件名不能以点开头：%s" % raw[:60]
    parts = raw.split("/")
    if len(parts) > 3:
        return "", "文件路径层级太深（最多 3 层）：%s" % raw[:60]
    for p in parts:
        if p in ("", ".", ".."):
            return "", "文件名不能包含空段、. 或 ..：%s" % raw[:60]
        if any(ord(c) < 32 for c in p):
            return "", "文件名不能包含控制字符：%s" % raw[:60]
        if any(c in p for c in '/<>:"|?*'):
            return "", "文件名不能包含路径或非法字符：%s" % raw[:60]
        stem = p.split(".", 1)[0].rstrip(" .").upper()
        if stem in _WINDOWS_RESERVED:
            return "", "文件名使用 Windows 保留名称 %s：%s" % (stem, p[:60])
    ext = os.path.splitext(parts[-1])[1].lower()
    allowed = _PLUGIN_EXTS if kind == "plugin" else _SKILL_EXTS
    if ext not in allowed:
        return "", "文件类型不在%s契约中：%s" % ("插件" if kind == "plugin" else "技能", raw[:60])
    return raw, ""


def _extract_json(text):
    """从模型回复里抠出 JSON 对象。

    容差只给"格式包装"（代码围栏、前后废话、思考段），不给内容作弊：
    抠不出来就是失败，由调用方明说。
    """
    t = str(text or "").strip()
    if not t:
        return None
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t.strip())
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = t[start:i + 1]
                try:
                    return json.loads(blob)
                except ValueError:
                    return None
    return None


# ── 提示词 ───────────────────────────────────────────────────

_PLUGIN_CONTRACT = """Vigil 插件契约（当前 Creator 生成单文件 Python 插件）：
- 必须有模块级字符串 NAME（英文/数字/下划线/短横线，唯一）和 DESC（一句话描述）。
- 想让模型在聊天中真正调用插件，必须同时提供 TOOLS 与 TOOL_SPECS；只有 prompt(ctx) 只是补充提示，不会执行功能。
- 工具的标准形状如下，名称必须在 TOOLS 与 TOOL_SPECS 中完全一致：
  def lookup_text(arg):
      return "面向用户的真实结果文本"
  TOOLS = {"lookup_text": lookup_text}
  TOOL_SPECS = {
      "lookup_text": {
          "desc": "这个工具能做什么、适用于什么问题",
          "params": [("query", "要查询的内容", True)],
          "arg_param": "query"
      }
  }
- TOOL_SPECS 的 params 使用 (参数名, 参数说明, 是否必填) 元组列表；arg_param 指定传给工具函数的单个字符串参数。工具函数返回可直接交给模型阅读的字符串。
- 可选钩子：prompt(ctx) -> str（补充模型提示）；start(brain)（启动后台任务，只有确实需要时才写）。
- on_message(text, ctx) 不是当前插件宿主支持的钩子，禁止用它实现聊天功能。不要只写 TOOLS 而漏掉 TOOL_SPECS，否则模型看不到工具。
- 可选 zxplugin.json（{"name": 与 NAME 一致, "version": "x.y.z", "desc": "..."}）。
- 当前 Creator 这条路径不支持自定义前端页面；禁止把完整 HTML 塞进 Python 文档字符串或把 HTML 返回值伪装成可显示的插件界面。也不得用演示数据冒充真实本地/GitHub 仓库内容；需求依赖自定义 UI、真实 Git 读取或网络访问时，必须在 summary/risks 中明确说明能力边界。
- 禁止：import 第三方库、未经需求授权的联网、读写插件目录之外的文件、弹系统对话框、自启动安装。
- 目标是"最小可审查"：只做需求要求的那一件事，并确保聊天能力使用宿主实际支持的工具协议。"""

_SKILL_CONTRACT = """Vigil Skill 契约（Node 宿主，ES 模块）：
- index.js：export function setup(api)，在 setup 里 api.registerTool({id, name, description, parameters, async execute(ctx, args)})。
- skill.json：{"id": "与目录名一致的英文/数字/下划线/短横线", "name": "显示名", "version": "x.y.z", "description": "...", "entry": "index.js"}。
- 禁止：require/import 第三方包、联网、fs 写操作、child_process、长连接轮询。
- execute 返回 {content: "字符串"} 或 {ok: false, err: "..."}。
- 目标是"最小可审查"：一个工具、几十行、无副作用。"""

_OUTPUT_CONTRACT = """输出契约（硬性，违反即生成失败）：
只输出一个 JSON 对象本身 —— 不要 markdown 代码围栏、不要解释、不要思考段：
{"files": {"相对路径": "文件内容", ...}, "summary": "一句话说明这个草稿做了什么", "risks": "用户须知的风险或限制；没有就写\"无\""}
files 的键是文件名：插件用 "<name>.py"（name=NAME）+ 可选 "zxplugin.json"；
技能用 "index.js" + "skill.json"。文件内容必须是完整可用的源码字符串。"""

_TEMPLATE_DIR = {}


def _template_text(kind):
    if kind in _TEMPLATE_DIR:
        return _TEMPLATE_DIR[kind]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sub = "plugin-basic" if kind == "plugin" else "skill-basic"
    out = []
    base = os.path.join(root, "templates", sub)
    try:
        for name in sorted(os.listdir(base)):
            p = os.path.join(base, name)
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8") as fh:
                        out.append("── 模板 %s ──\n%s" % (name, fh.read()))
                except OSError:
                    pass
    except OSError:
        pass
    _TEMPLATE_DIR[kind] = "\n\n".join(out)
    return _TEMPLATE_DIR[kind]


def _build_messages(kind, requirement, prev=None, feedback=""):
    """组 (system, user)。prev+feedback 非空即"修改草稿"轮。"""
    contract = _PLUGIN_CONTRACT if kind == "plugin" else _SKILL_CONTRACT
    system = (
        "你是 Vigil 的创作助手，负责把用户的自然语言需求变成一份最小、可审查、"
        "无副作用的 Vigil %s草稿。只写代码，不执行任何操作。\n\n%s\n\n%s\n\n%s"
        % ("插件" if kind == "plugin" else "技能",
           contract, _OUTPUT_CONTRACT, _template_text(kind)))
    if prev:
        user = ("这是同一份草稿的当前文件：\n%s\n\n用户反馈：%s\n\n"
                "请按反馈修改，仍然只输出那个 JSON 对象。"
                % (json.dumps(prev, ensure_ascii=False, indent=1), feedback))
    else:
        user = "用户需求：%s" % requirement
    return system, user


# ── 模型调用（真实链路，无假装） ──────────────────────────────

def _call_model(api_cfg, system, user):
    """走 Brain 同款 provider 链路。返回 (text, err)。"""
    try:
        from model_providers import get_provider
        from model_providers.base import (
            ProviderError, ProviderUnavailable, ProviderHTTPError,
            ProviderResponseError,
        )
    except Exception as e:                       # 模块坏了也说实话
        return None, "模型提供者模块不可用：%s" % str(e)[:100]
    cfg = api_cfg or {}
    mp = get_provider(cfg)
    if not (cfg.get("api_base") and cfg.get("api_key")):
        return None, "模型未配置（缺 api_base 或 api_key），无法生成"
    model = str(cfg.get("model") or "")
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.2,
    }
    try:
        from model_providers.execution import prepare, normalize, ERROR_MESSAGES
        j = mp.chat(prepare(payload, cfg), timeout=180)
        result = normalize(j)
        if not result['complete']:
            return None, ERROR_MESSAGES.get(result['reason'], '模型响应不完整')
    except ProviderUnavailable as e:
        return None, "模型调用失败（连不上/超时）：%s" % str(e)[:140]
    except ProviderHTTPError as e:
        return None, "模型调用失败（服务返回 %s）：%s" % (
            getattr(e, "status", "?"), str(e)[:120])
    except ProviderResponseError as e:
        return None, "模型调用失败（响应无法解析）：%s" % str(e)[:120]
    except ProviderError as e:
        return None, "模型调用失败：%s" % str(e)[:140]
    except Exception as e:
        return None, "模型调用失败（%s）：%s" % (type(e).__name__, str(e)[:120])
    if not j:
        return None, "模型未就绪（提供者没配好）"
    try:
        return str(j["choices"][0]["message"]["content"] or ""), ""
    except (KeyError, IndexError, TypeError):
        return None, "模型响应里没有 choices（格式不对）"


# ── 草稿读写 ─────────────────────────────────────────────────

def _meta_path(d):
    return os.path.join(d, "meta.json")


def _load_meta(d):
    try:
        with open(_meta_path(d), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _save_meta(d, meta):
    with open(_meta_path(d), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _write_files(d, files, kind):
    """落盘模型给的文件。返回 (written, errs) —— errs 非空即整体拒绝。"""
    if not isinstance(files, dict) or not files:
        return [], ["模型没有给出任何文件"]
    written, errs = [], []
    for name, content in files.items():
        rel, err = _safe_relpath(name, kind)
        if err:
            errs.append(err)
            continue
        if not isinstance(content, str) or not content.strip():
            errs.append("文件 %s 的内容是空的" % rel)
            continue
        target = os.path.join(d, *rel.split("/"))
        try:
            os.makedirs(os.path.dirname(target) or d, exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            written.append(rel)
        except OSError as e:
            errs.append("写不了 %s：%s" % (rel, str(e)[:100]))
    return written, errs


def _history_dir(draft_id):
    """历史版本放草稿目录**外面**（`.history/<id>/`）。

    🔴 不能放 `<id>/history/`：插件静态检查器拒绝嵌套目录，历史目录会把
    一份本来合格的草稿判成 nested_directory（MVP-1 实测踩过）。
    """
    did = str(draft_id or "")
    if not re.fullmatch(r"cr_[0-9a-zA-Z_]+", did):
        raise ValueError("草稿编号不合法：%s" % did[:40])
    return os.path.join(drafts_root(), ".history", did)


def _read_files(d):
    out = []
    for base, _dirs, names in os.walk(d):
        for n in sorted(names):
            if n == "meta.json":
                continue
            p = os.path.join(base, n)
            rel = os.path.relpath(p, d).replace(os.sep, "/")
            try:
                with open(p, encoding="utf-8") as fh:
                    out.append({"name": rel, "content": fh.read()})
            except (OSError, UnicodeError):
                out.append({"name": rel, "content": "（读不出来：不是 UTF-8 文本？）"})
    return out


def _normalize_report(rep):
    return {
        "ok": bool(rep.get("ok")),
        "layout": rep.get("layout", ""),
        "entry": rep.get("entry", ""),
        "files": rep.get("files", []),
        "errors": [e if isinstance(e, dict) else {"code": "?", "message": str(e)}
                   for e in rep.get("errors", [])],
        "warnings": [e if isinstance(e, dict) else {"code": "?", "message": str(e)}
                     for e in rep.get("warnings", [])],
    }


def validate_draft(draft_id):
    """对草稿跑静态验证。返回 (report, err)。

    只读文件，不 import、不执行。skill 的 package_name 显式传 manifest 的 id
    （草稿目录名不是最终技能名，和 ZIP 安装器同款处理）。
    """
    try:
        d = _draft_dir(draft_id)
    except ValueError as e:
        return None, str(e)
    meta = _load_meta(d)
    if not meta:
        return None, "草稿不存在：%s" % draft_id
    kind = meta.get("kind")
    try:
        if kind == "plugin":
            import plugin_structure
            rep = plugin_structure.check_plugin_directory(d)
        else:
            import skill_structure
            pkg = ""
            try:
                with open(os.path.join(d, "skill.json"), encoding="utf-8") as fh:
                    pkg = str(json.load(fh).get("id") or "")
            except (OSError, ValueError, AttributeError):
                pass
            rep = skill_structure.check_skill_directory(d, package_name=pkg or None)
    except Exception as e:
        return None, "静态检查器不可用：%s" % str(e)[:120]
    rep = _normalize_report(rep)
    meta["validation"] = rep
    meta["updated_at"] = _now()
    _save_meta(d, meta)
    return rep, ""


class DraftFailure(str):
    """Keep the legacy (draft, error-string) API while carrying safe diagnostics."""
    def __new__(cls, message, code, stage='parse', **fields):
        value = super().__new__(cls, message)
        value.diagnostic = dict(message=message, code=code, stage=stage, **fields)
        return value


def _persist(kind, requirement, text, api_cfg, prev_meta=None, feedback=""):
    """公共落盘流程：解析 JSON → 写文件 → 验证 → 存 meta。返回 (meta, err)。"""
    obj = _extract_json(text)
    if not isinstance(obj, dict):
        probe = _json_probe(text)
        status = probe.get("status")
        if status == "empty":
            return None, DraftFailure("模型没按契约返回 JSON：本次没有返回任何内容（未落地文件）", 'json_empty')
        if status == "no_object":
            return None, DraftFailure("模型没按契约返回 JSON：输出里找不到 JSON 对象（未落地文件）", 'json_object_missing')
        if status == "unterminated":
            return None, DraftFailure("模型没按契约返回 JSON：对象未闭合，疑似在第 %s 个字符附近被截断（未落地文件）" % probe.get("position"), 'json_unterminated', position=probe.get('position'))
        if status == "parse_error":
            return None, DraftFailure("JSON 解析失败：%s（第 %s 行，第 %s 列，字符 %s；未落地文件）" % (probe['detail'], probe['line'], probe['column'], probe['position']), 'json_parse_error', **{k: probe[k] for k in ('position', 'line', 'column')})
        return None, DraftFailure("模型没按契约返回 JSON（未落地文件）", 'json_invalid')
    files = obj.get("files")
    if not isinstance(files, dict) or not files:
        return None, DraftFailure("模型没按契约返回 JSON：缺少 files 字段或 files 为空（未落地文件）", 'files_missing', missing=['files'])
    summary = str(obj.get("summary") or "").strip()[:300]
    risks = str(obj.get("risks") or "").strip()[:500] or "无"

    d = _draft_dir(prev_meta["id"]) if prev_meta else os.path.join(
        drafts_root(), _new_id())
    os.makedirs(d, exist_ok=True)
    # 修改轮：先把**当前**文件快照进 .history/<id>/v<N>/，再写新文件。
    # 顺序反了会把新内容当旧内容存（历史就失去意义）。
    if prev_meta:
        try:
            hist = os.path.join(_history_dir(os.path.basename(d)),
                                "v%d" % prev_meta.get("version", 1))
            os.makedirs(hist, exist_ok=True)
            for f in prev_meta.get("files", []):
                src = os.path.join(d, *str(f).split("/"))
                if os.path.isfile(src):
                    with open(src, encoding="utf-8") as fh:
                        blob = fh.read()
                    with open(os.path.join(hist, os.path.basename(f)), "w",
                              encoding="utf-8", newline="\n") as fh:
                        fh.write(blob)
        except OSError:
            pass
    written, errs = _write_files(d, files, kind)
    if not written:
        return None, DraftFailure("生成的文件全部被拒绝，请检查文件名和内容", 'files_rejected', 'codegen')

    meta = {
        "id": os.path.basename(d),
        "kind": kind,
        "requirement": str(requirement)[:2000],
        "summary": summary,
        "risks": risks,
        "model": str((api_cfg or {}).get("model") or ""),
        "version": int((prev_meta or {}).get("version") or 0) + 1,
        "status": "revised" if prev_meta else "generated",
        "feedback": str(feedback)[:1000] if feedback else "",
        "created_at": (prev_meta or {}).get("created_at") or _now(),
        "updated_at": _now(),
        "files": written,
        "rejected": errs,
    }
    _save_meta(d, meta)
    rep, verr = validate_draft(meta["id"])
    meta["validation"] = rep if rep else {"ok": False, "errors": [
        {"code": "validator_error", "message": verr}], "warnings": []}
    _save_meta(d, meta)
    if errs:
        meta["validation"]["warnings"].append(
            {"code": "files_rejected", "message": "；".join(errs[:5])})
        _save_meta(d, meta)
    # 落盘的 meta.json 里 files 只存名字（紧凑）；返回给调用方的带上内容，
    # 客户端/接口不用再读一次盘。
    out = dict(meta)
    out["files"] = _read_files(d)
    return out, ""


def generate_draft(kind, requirement, api_cfg):
    """生成新草稿。kind: plugin|skill。"""
    kind = str(kind or "")
    if kind not in ("plugin", "skill"):
        return None, "类型只能是 plugin 或 skill"
    req = str(requirement or "").strip()
    if not req:
        return None, "需求不能为空"
    if len(req) > 4000:
        return None, "需求太长（最多 4000 字）"
    system, user = _build_messages(kind, req)
    text, err = _call_model(api_cfg, system, user)
    if err:
        return None, err
    return _persist(kind, req, text, api_cfg)


def revise_draft(draft_id, feedback, api_cfg):
    """按反馈修改草稿（同目录升 version，旧版进 history/）。"""
    try:
        d = _draft_dir(draft_id)
    except ValueError as e:
        return None, str(e)
    meta = _load_meta(d)
    if not meta:
        return None, "草稿不存在：%s" % draft_id
    fb = str(feedback or "").strip()
    if not fb:
        return None, "修改意见不能为空"
    if len(fb) > 2000:
        return None, "修改意见太长（最多 2000 字）"
    prev = {f["name"]: f["content"] for f in _read_files(d)}
    system, user = _build_messages(meta.get("kind"), meta.get("requirement", ""),
                                   prev=prev, feedback=fb)
    text, err = _call_model(api_cfg, system, user)
    if err:
        return None, err
    return _persist(meta.get("kind"), meta.get("requirement", ""), text,
                    api_cfg, prev_meta=meta, feedback=fb)


def list_drafts():
    out = []
    root = drafts_root()
    try:
        names = sorted(os.listdir(root), reverse=True)
    except OSError:
        return out
    for n in names:
        if not n.startswith("cr_"):
            continue
        meta = _load_meta(os.path.join(root, n))
        if not meta:
            continue
        v = meta.get("validation") or {}
        out.append({
            "id": meta.get("id", n),
            "kind": meta.get("kind", ""),
            "requirement": (meta.get("requirement") or "")[:80],
            "summary": (meta.get("summary") or "")[:120],
            "version": meta.get("version", 1),
            "status": meta.get("status", ""),
            "created_at": meta.get("created_at", ""),
            "updated_at": meta.get("updated_at", ""),
            "valid": bool(v.get("ok")),
            "error_count": len(v.get("errors") or []),
        })
    return out


def get_draft(draft_id):
    try:
        d = _draft_dir(draft_id)
    except ValueError as e:
        return None, str(e)
    meta = _load_meta(d)
    if not meta:
        return None, "草稿不存在：%s" % draft_id
    meta = dict(meta)
    meta["files"] = _read_files(d)
    return meta, ""


def delete_draft(draft_id):
    try:
        d = _draft_dir(draft_id)
    except ValueError as e:
        return False, str(e)
    import shutil
    try:
        shutil.rmtree(d)
    except OSError as e:
        return False, "删不掉：%s" % str(e)[:100]
    return True, ""


def _json_probe(text):
    """只做诊断：判断 JSON 缺失/截断/解析失败的大致位置，绝不回显模型原文。"""
    t = str(text or "").strip()
    if not t:
        return {"status": "empty"}
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t.strip())
    start = t.find("{")
    if start < 0:
        return {"status": "no_object"}
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = t[start:i + 1]
                try:
                    json.loads(blob)
                    return {"status": "parsed"}
                except json.JSONDecodeError as e:
                    return {"status": "parse_error", "position": start + e.pos,
                            "line": t[:start + e.pos].count('\n') + 1,
                            "column": start + e.pos - t.rfind('\n', 0, start + e.pos),
                            "detail": e.msg}
    return {"status": "unterminated", "position": len(t)}
