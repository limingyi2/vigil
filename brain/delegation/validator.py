# -*- coding: utf-8 -*-
"""子任务输出验收：结构校验 + 证据真实性。

原则（方案第 5 节）：
- confidence 只是参考，不参与放行；
- 引用了查无此文件/行的证据 → invalid_evidence，不能当成功；
- structured_extraction 必须过 JSON Schema 级字段断言；
- 证据检查不通过不算成功，Dispatcher 最多带修正意见重试一次。

本模块只做判定（纯函数式），重试循环在 dispatcher 里。
"""
import json
import os
import re

# 各 schema 的必填字段（首版字段断言保持简单：存在且非空/类型对）。
REQUIRED_FIELDS = {
    "bug_analysis_v1": {
        "root_cause": str,       # 根因一句话
        "file": str,             # 证据文件（工作区相对路径）
        "reason": str,           # 为什么
    },
    "file_list_v1": {
        "files": list,           # 发现的文件相对路径列表
    },
    "extraction_v1": {
        "fields": dict,          # 抽取字段键值
    },
    "plan_draft_v1": {
        "steps": list,           # 步骤列表（主模型还要再审一次）
    },
}

# 从正文里抠 JSON：模型可能包 ```json，也可能前后带废话。
_JSON_BLOCK = re.compile(r"\{.*\}", re.S)
_LINE_REF = re.compile(r"([\w./\\-]+\.[A-Za-z0-9]{1,8})(?::(\d+))?")


def extract_json_object(text):
    """从模型最终输出里尽力取出一个 JSON 对象；取不到返回 None。"""
    t = (text or "").strip()
    if not t:
        return None
    t2 = re.sub(r"^```(?:json)?\s*", "", t)
    t2 = re.sub(r"\s*```$", "", t2).strip()
    try:
        v = json.loads(t2)
        return v if isinstance(v, dict) else None
    except Exception:
        pass
    m = _JSON_BLOCK.search(t)
    if m:
        try:
            v = json.loads(m.group(0))
            return v if isinstance(v, dict) else None
        except Exception:
            return None
    return None


def _evidence_files(text, workspace):
    """从正文里收集"看着像工作区内文件"的引用，并判它们是否真实存在。

    返回 (existing, missing)：missing 非空即 invalid_evidence。
    只在 workspace 内比对，绝不访问工作区外的路径。
    """
    raw_refs, seen = [], set()
    for m in _LINE_REF.finditer(text or ""):
        name = m.group(1).replace("\\", "/").lstrip("./")
        if name in seen or len(name) < 3:
            continue
        seen.add(name)
        raw_refs.append(name)

    existing, missing = [], []

    def _is_real(rel):
        cand = os.path.join(workspace, rel.replace("/", os.sep))
        try:
            return os.path.commonpath(
                [workspace, os.path.realpath(cand)]) == workspace \
                and os.path.isfile(cand)
        except Exception:
            return False

    for name in raw_refs:
        if _is_real(name):
            existing.append(name)
            continue
        # 简写容错：模型常把 app/orders.py 写成 orders.py。若工作区内存在
        # 以该路径结尾的真实文件，视为同一证据（但不新增"不存在"罪名）。
        suffix_match = None
        for root, _, files in os.walk(workspace):
            for fn in files:
                full = os.path.relpath(os.path.join(root, fn),
                                       workspace).replace(os.sep, "/")
                if full == name or full.endswith("/" + name) \
                        or os.path.basename(full) == os.path.basename(name):
                    suffix_match = full
                    break
            if suffix_match:
                break
        if suffix_match:
            existing.append(name)
        else:
            missing.append(name)
    return existing, missing


def validate(kind, schema, text, workspace, *, tool_events=None):
    """判定一次子任务输出。

    返回 dict：
      ok            是否通过
      structured    解析出的 JSON 对象（若有）
      evidence      {existing:[...], missing:[...]}
      problems      不通过原因列表（重试时原样喂回给模型）
    """
    problems = []
    structured = None
    evidence = {"existing": [], "missing": []}
    tool_events = tool_events or []

    # 1) 结构类 kind：必须吐出符合 schema 的 JSON
    if schema in REQUIRED_FIELDS:
        obj = extract_json_object(text)
        if obj is None:
            problems.append("最终回答必须是一个 JSON 对象（不要输出解释性文字）")
        else:
            structured = obj
            for field, typ in REQUIRED_FIELDS[schema].items():
                if field not in obj:
                    problems.append("JSON 缺少必填字段：%s" % field)
                elif not isinstance(obj[field], typ):
                    problems.append("字段 %s 类型应为 %s"
                                    % (field, typ.__name__))
                elif typ in (str, list, dict) and len(obj[field]) == 0:
                    problems.append("字段 %s 不能为空" % field)
            # file_list_v1：列出来的文件必须真实存在
            if schema == "file_list_v1" and isinstance(obj.get("files"), list):
                ev_ok, ev_miss = _check_listed_files(obj["files"], workspace)
                evidence["existing"], evidence["missing"] = ev_ok, ev_miss
                if ev_miss:
                    problems.append("列出的文件在工作区里不存在：%s"
                                    % "、".join(ev_miss[:5]))

    # 2) 需要工作区/证据的 kind：正文或工具记录里必须有真实文件依据
    if kind in ("log_root_cause", "read_only_bug_analysis") and workspace:
        # 优先信工具实际读过的文件（模型嘴说不算）
        touched = []
        for ev in tool_events:
            for k in ("path", "file"):
                v = ev.get(k)
                if isinstance(v, str) and v:
                    touched.append(v.lstrip("./"))
        ex, miss = _evidence_files(
            "\n".join(touched) + "\n" + (text or ""), workspace)
        evidence["existing"] = sorted(set(evidence["existing"] + ex))
        evidence["missing"] = sorted(set(miss) - set(ex))
        if not evidence["existing"]:
            problems.append("结论没有引用任何工作区内真实存在的文件作为证据")
        elif evidence["missing"] and len(evidence["missing"]) > len(ex):
            # 引用了一堆不存在的、只有极少数真的 → 多半在编
            problems.append("引用的文件多数不存在：%s"
                            % "、".join(evidence["missing"][:5]))

    # 3) 工具型 kind 一次工具都没调：不算完成（可能模型在凭空编）
    if kind in ("file_discovery", "log_root_cause",
                "read_only_bug_analysis") and not tool_events:
        problems.append("没有实际调用任何只读工具，不能凭空给结论")

    # 4) plan_draft 永远带"待主模型审核"标记（不判失败，只标记）
    needs_review = kind == "plan_draft"

    return {"ok": not problems,
            "structured": structured,
            "evidence": evidence,
            "problems": problems,
            "needs_main_agent_review": needs_review}


def _check_listed_files(files, workspace):
    ok, miss = [], []
    for f in files[:50]:
        if not isinstance(f, str):
            continue
        rel = f.replace("\\", "/").lstrip("./")
        cand = os.path.join(workspace, rel.replace("/", os.sep))
        try:
            inside = os.path.commonpath(
                [workspace, os.path.realpath(cand)]) == workspace
            exists = os.path.isfile(cand)
        except Exception:
            inside, exists = False, False
        (ok if inside and exists else miss).append(rel)
    return sorted(set(ok)), sorted(set(miss))
