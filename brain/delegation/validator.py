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

# 各 schema 的必填字段（紧凑证据版，P2）：
# - answer：给主模型/用户看的简短结论，长度由 MAX_* 硬夹；
# - evidence：程序核验过来源的证据条目，禁止大段原文转储。
REQUIRED_FIELDS = {
    "bug_analysis_v1": {
        "answer": str,         # 根因一句话（≤400 字）
        "evidence": list,      # ≥1 条核验证据 [{path,line?,excerpt,observation?}]
    },
    "file_list_v1": {
        "answer": str,         # 发现结论（≤400 字，含问到的值）
        "files": list,         # 发现的文件相对路径列表（机器校验用）
    },
    "extraction_v1": {
        "fields": dict,        # 抽取字段键值
    },
    "plan_draft_v1": {
        "steps": list,         # 步骤列表（每条 ≤200 字，≤8 条）
    },
}

# 紧凑度硬顶（超出记问题 → 触发一次修正重试；渲染层还会再裁一次）
# 900 字：实测日志根因的自然答案 600-800 字，600 会误杀正确结论；
# 云端回灌仍由 bridge 裁到 600 字，本地全量只用于落盘/直答。
MAX_ANSWER_CHARS = 900
MAX_EVIDENCE_ITEMS = 5
MAX_EXCERPT_CHARS = 160
MAX_PLAN_STEPS = 8
MAX_PLAN_STEP_CHARS = 200

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


def _norm(s):
    """空白归一化，方便做摘录子串匹配（模型复制原文时常改缩进/换行）。"""
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _resolve_in_workspace(rel, workspace):
    """相对路径 → 沙箱内真实文件；越界/不存在返回 None。"""
    rel = str(rel or "").replace("\\", "/").lstrip("./")
    if not rel:
        return None, rel
    cand = os.path.join(workspace, rel.replace("/", os.sep))
    try:
        real = os.path.realpath(cand)
        if os.path.commonpath([workspace, real]) == workspace and os.path.isfile(real):
            return real, rel
    except Exception:
        pass
    return None, rel


def _check_listed_files(files, workspace):
    ok, miss = [], []
    for f in (files or [])[:50]:
        if not isinstance(f, str):
            continue
        real, rel = _resolve_in_workspace(f, workspace)
        (ok if real else miss).append(rel)
    return sorted(set(ok)), sorted(set(miss))


def _verify_evidence_items(items, workspace, read_blobs):
    """逐条核验结构化证据。

    每条 {path, line?, excerpt, observation?}：
    - path 必须是工作区内真实文件；
    - excerpt 给了就必须出现在**子循环实际读到的内容**里（read_file 正文
      或 file_grep 命中行）；模型没读过的文件，嘴说不算；
    - line 给了时，摘录应落在该行（±2 行容差）；拿不到正文就只验文件存在。

    返回 (verified_items, bad_messages)。verified 条目已裁到安全长度，
    可直接用于回灌渲染。
    """
    verified, bad = [], []
    for it in (items or [])[:MAX_EVIDENCE_ITEMS]:
        if not isinstance(it, dict):
            continue
        real, rel = _resolve_in_workspace(it.get("path"), workspace)
        if not real:
            bad.append("证据文件不存在或越界：%s" % str(it.get("path"))[:60])
            continue
        excerpt = str(it.get("excerpt") or "").strip()
        blob = (read_blobs or {}).get(rel) or ""
        line_no = it.get("line")
        try:
            line_no = int(line_no) if line_no not in (None, "") else None
        except (TypeError, ValueError):
            line_no = None
        if excerpt:
            excerpt = excerpt[:MAX_EXCERPT_CHARS]
            if not blob:
                bad.append("证据未经过工具读取，无法核验摘录：%s" % rel)
                continue
            if _norm(excerpt) not in _norm(blob):
                bad.append("摘录在 %s 实际内容中找不到（疑似编造）" % rel)
                continue
            if line_no is not None:
                lines = blob.splitlines()
                lo, hi = max(0, line_no - 3), min(len(lines), line_no + 2)
                window = _norm("\n".join(lines[lo:hi]))
                if window and _norm(excerpt) not in window:
                    bad.append("行号 %s 与摘录对不上：%s" % (line_no, rel))
                    continue
        verified.append({
            "path": rel,
            "line": line_no,
            "excerpt": excerpt,
            "observation": str(it.get("observation") or "")[:120],
        })
    return verified, bad


def validate(kind, schema, text, workspace, *, tool_events=None,
             read_blobs=None):
    """判定一次子任务输出（紧凑证据版）。

    返回 dict：
      ok            是否通过
      structured    解析出的 JSON（已补 verified/generic 标记）
      verified      证据是否全部经过程序核验
      generic       plan_draft 无工作区时的通用草案标记
      evidence      {verified:[...], existing:[...], missing:[...]}
      problems      不通过原因（重试时原样喂回模型）
    """
    problems = []
    structured = None
    evidence = {"verified": [], "existing": [], "missing": []}
    tool_events = tool_events or []
    read_blobs = read_blobs or {}

    obj = extract_json_object(text) if schema in REQUIRED_FIELDS else None
    if schema in REQUIRED_FIELDS and obj is None:
        problems.append("最终回答必须是一个 JSON 对象（不要输出解释性文字）")
    elif obj is not None:
        structured = obj
        for field, typ in REQUIRED_FIELDS[schema].items():
            if field not in obj:
                problems.append("JSON 缺少必填字段：%s" % field)
            elif not isinstance(obj[field], typ):
                problems.append("字段 %s 类型应为 %s"
                                % (field, typ.__name__))
            elif typ in (str, list, dict) and len(obj[field]) == 0:
                problems.append("字段 %s 不能为空" % field)

        ans = str(obj.get("answer") or "")
        if "answer" in obj and len(ans) > MAX_ANSWER_CHARS:
            problems.append("answer 必须控制在 %d 字以内（只给结论，"
                            "不要转述日志/文件全文）" % MAX_ANSWER_CHARS)

        # bug_analysis_v1：证据条目逐条程序核验
        if schema == "bug_analysis_v1" and isinstance(obj.get("evidence"), list):
            if len(obj["evidence"]) > MAX_EVIDENCE_ITEMS:
                problems.append("evidence 最多 %d 条" % MAX_EVIDENCE_ITEMS)
            vitems, vbad = _verify_evidence_items(
                obj["evidence"], workspace, read_blobs)
            evidence["verified"] = vitems
            if not vitems:
                problems.append("没有任何经核验的证据条目（path 必须真实、"
                                "excerpt 必须来自工具实际读到的内容）")
            problems.extend(vbad)
            evidence["existing"] = [v["path"] for v in vitems]

        # file_list_v1：列出的文件必须真实存在
        if schema == "file_list_v1" and isinstance(obj.get("files"), list):
            ex, miss = _check_listed_files(obj["files"], workspace)
            evidence["existing"], evidence["missing"] = ex, miss
            if miss:
                problems.append("列出的文件在工作区里不存在：%s"
                                % "、".join(miss[:5]))
            if isinstance(obj.get("evidence"), list):
                vitems, vbad = _verify_evidence_items(
                    obj["evidence"], workspace, read_blobs)
                evidence["verified"] = vitems
                if vitems:
                    evidence["existing"] = sorted(
                        set(evidence["existing"])
                        | {v["path"] for v in vitems})
                problems.extend(vbad)

        # plan_draft_v1：步数/步长紧凑度
        if schema == "plan_draft_v1" and isinstance(obj.get("steps"), list):
            steps = obj["steps"]
            if len(steps) > MAX_PLAN_STEPS:
                problems.append("steps 最多 %d 步" % MAX_PLAN_STEPS)
            long_steps = [i + 1 for i, s in enumerate(steps)
                          if not isinstance(s, str)
                          or len(s) > MAX_PLAN_STEP_CHARS]
            if long_steps:
                problems.append("第 %s 步超过 %d 字，每步只写一个动作"
                                % ("、".join(map(str, long_steps[:5])),
                                   MAX_PLAN_STEP_CHARS))

    # 需要工作区/证据的 kind：必须实际调过只读工具（禁止凭空编）
    if kind in ("file_discovery", "log_root_cause",
                "read_only_bug_analysis") and workspace and not tool_events:
        problems.append("没有实际调用任何只读工具，不能凭空给结论")

    # plan_draft：带工作区 = 针对当前仓库的计划，必须先取证；
    # 没带工作区 = 允许通用草案（generic=True，主模型须标明未核实）。
    generic = False
    plan_evidence_ok = False
    if kind == "plan_draft":
        if workspace:
            touched = set()
            for ev in tool_events:
                for k in ("path", "file"):
                    v = ev.get(k)
                    if isinstance(v, str) and v:
                        _, rel = _resolve_in_workspace(v, workspace)
                        if rel:
                            touched.add(rel)
            ep = structured.get("evidence_paths", []) \
                if isinstance(structured, dict) else []
            ep_miss = []
            if isinstance(ep, list) and ep:
                ep_ex, ep_miss = _check_listed_files(ep, workspace)
                evidence["existing"] = sorted(set(ep_ex) | touched)
                if ep_miss:
                    problems.append("evidence_paths 里有不存在的文件：%s"
                                    % "、".join(ep_miss[:5]))
            else:
                evidence["existing"] = sorted(touched)
            if not tool_events or not touched:
                problems.append("针对具体仓库的计划必须先用只读工具取证"
                                "（读报错文件/相关源码），不能零工具凭空草拟")
            elif not ep_miss:
                # 实际读过文件且声明路径无造假 → 证据核验通过
                plan_evidence_ok = True
            if isinstance(structured, dict):
                structured["generic"] = False
        else:
            generic = True
            if isinstance(structured, dict):
                structured["generic"] = True

    verified = (bool(evidence["verified"])
                or (schema == "file_list_v1"
                    and bool(evidence["existing"]) and not evidence["missing"])
                or (kind == "plan_draft" and (generic or plan_evidence_ok)))
    needs_review = kind == "plan_draft"

    return {"ok": not problems,
            "structured": structured,
            "verified": verified,
            "generic": generic,
            "evidence": evidence,
            "problems": problems,
            "needs_main_agent_review": needs_review}
