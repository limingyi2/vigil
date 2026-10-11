# -*- coding: utf-8 -*-
"""P3 混合模式 A/B 对照评测（真实云端主模型 + 真实 Ollama）。

用法：
    python vigil-eval/run_hybrid_compare.py [--only H02] [--groups AB]

设计（vigil-hybrid-model-v1-plan.md 第 9 节 P3）：
- 6 个固定任务（tasks_hybrid.jsonl），其中 H04/H05 两个**不应该委派**，
  H06 委派可选；
- A 组 cloud：主模型工具表里没有 delegate_to_local（模拟云端直答）；
- B 组 hybrid：同一主模型 + delegate_to_local，沙箱根指向任务 fixture；
- 两组主模型是同一个云端模型（当前 config.json），差异**只有委派能力**，
  因此差异可归因于"有界本地委派"本身；
- 无 PC 客户端轮询（local_* 秒回不在线），所以本地文件任务只能靠委派完成；
- 采集：终局文本、Oracle、是否委派/委派是否命中、主模型与本地模型的
  请求数/token/耗时、工具调用、失败与回退。

输出：results/hybrid-compare-<时间戳>/
    raw_events.jsonl  每轮原始记录
    summary.json      结构化汇总
    summary.md        人读对照报告（区分语义正确与 Oracle 文字判定）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent            # vigil-eval
REPO = HERE.parent
BRAIN = REPO / "brain"
sys.path.insert(0, str(BRAIN))

import plugin_host                       # noqa: E402
import runtime                           # noqa: E402
import localtools                        # noqa: E402
from brain import Brain                  # noqa: E402
from delegation import bridge as dbridge  # noqa: E402
import model_hub                         # noqa: E402

TASKS_FILE = HERE / "dataset" / "tasks_hybrid.jsonl"
FIXTURES = HERE / "dataset" / "fixtures"
RESULTS = REPO / "results"

# 云端主模型配置（现 config.json），A/B 共用，保证差异只在委派能力
CLOUD_CFG = model_hub.apply_cfg(dict(runtime.api_config), {})[0]
# 本地 Ollama 配置（P1 已验证的模型）
OLLAMA_CFG = {"base": "http://127.0.0.1:11434",
              "model": "qwen27b-efficientthink:latest",
              "num_ctx": 16384, "num_predict": 2048, "think": True}


def load_tasks():
    out = []
    for line in TASKS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def prepare_workspace(task, run_dir):
    """把 fixture 复制成每个任务独立的沙箱，返回工作区路径（无 fixture 返回 ''）。"""
    fd = task.get("fixture_dir")
    if not fd:
        return ""
    src = FIXTURES / fd
    ws = run_dir / "ws" / task["id"]
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(src, ws)
    return str(ws)


def configure_group(group, allowed_root):
    """切 A/B 组：改内存配置 + 打桩委派开关。"""
    cfg = dict(CLOUD_CFG)
    if group == "A":
        cfg["model_mode"] = "cloud"
        cfg.pop("ollama", None)
        dbridge.load_delegation_config = lambda: {
            "enabled": False, "allowed_roots": []}
    else:
        cfg["model_mode"] = "hybrid"
        cfg["ollama"] = dict(OLLAMA_CFG)
        dbridge.load_delegation_config = lambda: {
            "enabled": True, "allowed_roots": [allowed_root]}
    runtime.API_CFG.clear()
    for k, v in cfg.items():
        runtime.API_CFG[k] = v
    localtools.reset()
    # 不刷 _LAST_POLL：local_* 一律"没有在线电脑"，模拟云端/隔离环境
    return cfg


def make_brain():
    return Brain(mem=None, api_config=runtime.API_CFG, mode="api")


def run_one(group, task, ws, capture):
    request = task["request"].replace("{ws}", ws or "（无）")
    cfg = configure_group(group, os.path.dirname(ws) if ws else "")
    plugin_host.load(force=True)
    br = make_brain()

    captured = {"calls": []}

    if group == "B":
        real_run = dbridge.run_delegation

        def spy(contract, **kw):
            res = real_run(contract, **kw)
            captured["calls"].append({
                "kind": contract["kind"], "goal": contract["goal"],
                "status": res["status"],
                "model_requests": res["model_requests"],
                "tool_calls": res["tool_calls"],
                "input_tokens": res["input_tokens"],
                "output_tokens": res["output_tokens"],
                "elapsed_seconds": res["elapsed_seconds"],
                "retry_used": res["retry_used"],
                "errors": res["errors"][:2],
                "structured": res["structured"],
            })
            return res
        dbridge.run_delegation = spy

    t0 = time.time()
    try:
        ans, mode = br.chat(request, log=False, src="pc")
    except Exception as e:
        ans, mode = "", "EXC: %s" % type(e).__name__
    wall = round(time.time() - t0, 1)

    if group == "B":
        dbridge.run_delegation = real_run  # noqa: F821（spy 闭包内定义）

    used = list(getattr(br, "last_tools_used", []) or [])
    usage = getattr(br, "last_usage", None) or {"in": 0, "out": 0}
    delegated = "delegate_to_local" in used or bool(captured["calls"])
    local_calls = [u for u in used if u.startswith("local_")]
    cloud_tool_calls = len(used) - (1 if "delegate_to_local" in used else 0)

    dl = captured["calls"][0] if captured["calls"] else None
    return {
        "group": group, "task_id": task["id"],
        "category": task["category"],
        "should_delegate": task["should_delegate"],
        "request": request, "answer": ans or "", "mode": mode,
        "wall_seconds": wall,
        "main_model": {"in_tokens": usage.get("in", 0),
                       "out_tokens": usage.get("out", 0),
                       "latency_ms": getattr(br, "last_ms", None),
                       "tools_used": used,
                       "tool_call_count": cloud_tool_calls},
        "delegated": delegated,
        "local_tool_attempts": local_calls,
        "delegation": dl,
    }


def judge(task, ev):
    """Oracle 判定。返回 (pass, notes)。notes 写明语义/格式分歧。"""
    ans = ev["answer"] or ""
    ora = task["oracle"]
    low = ans.lower()

    if ora.get("no_delegate"):
        if ev["delegated"]:
            return False, "误派：不该委派的任务调用了 delegate_to_local"
        missing = [k for k in ora.get("all_of", []) if k not in ans]
        if missing:
            return False, ("未误派但要点缺失：%s（语义需人工复核；H04 天气接口"
                          "失败不算决策错误）" % missing)
        return True, "未误派且要点齐全"

    for k in ora.get("all_of", []):
        if k not in ans:
            return False, "Oracle 缺关键词：%s（可能是表述差异，见 semantic_hint）" % k
    for k in ora.get("any_of", []):
        if k not in ans:
            return False, "Oracle 缺任一关键词：%s" % k
    for k in ora.get("none_of", []):
        if k in ans:
            return False, "出现干扰项：%s" % k
    return True, ora.get("note", "")


def delegation_decision(task, ev):
    """委派决策归类：hit(应派且派) / correct_skip(不应派且没派) / miss(应派没派)
    / wrong(不应派却派) / optional_*。"""
    should = task["should_delegate"]
    did = ev["delegation"] is not None
    if should == "optional":
        return "optional_delegated" if did else "optional_self"
    if should and did:
        return "hit"
    if should and not did:
        return "miss"
    if not should and not did:
        return "correct_skip"
    return "wrong_delegate"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--groups", default="AB")
    args = ap.parse_args()

    tasks = [t for t in load_tasks() if not args.only or t["id"] in args.only.split(",")]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = RESULTS / ("hybrid-compare-%s" % stamp)
    (run_dir / "ws").mkdir(parents=True, exist_ok=True)

    events = []
    for task in tasks:
        ws = prepare_workspace(task, run_dir)
        for g in args.groups:
            print("[%s/%s] %s %s …" % (task["id"], g, task["category"],
                                       "委派" if task["should_delegate"] is True
                                       else ("可选" if task["should_delegate"] == "optional"
                                             else "不委派")), flush=True)
            ev = run_one(g, task, ws, None)
            ok, notes = judge(task, ev)
            ev["oracle_pass"] = ok
            ev["oracle_notes"] = notes
            ev["decision"] = delegation_decision(task, ev)
            events.append(ev)
            print("   → %s 决策=%s 主模型tokens=%s/%s 墙钟=%.0fs %s"
                  % ("PASS" if ok else "FAIL", ev["decision"],
                     ev["main_model"]["in_tokens"],
                     ev["main_model"]["out_tokens"],
                     ev["wall_seconds"],
                     ("委派=%s" % ev["delegation"]["status"]) if ev["delegation"] else ""),
                  flush=True)

    (run_dir / "raw_events.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
        encoding="utf-8")
    write_summary(events, run_dir, tasks)
    print("\n报告：", run_dir)


def write_summary(events, run_dir, tasks):
    summary = aggregate(events, tasks)
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "summary.md").write_text(render_md(summary, events), encoding="utf-8")


def aggregate(events, tasks):
    groups = {}
    for g in ("A", "B"):
        es = [e for e in events if e["group"] == g]
        groups[g] = {
            "oracle_pass": sum(1 for e in es if e["oracle_pass"]),
            "total": len(es),
            "wall_seconds": round(sum(e["wall_seconds"] for e in es), 1),
            "main_in_tokens": sum(e["main_model"]["in_tokens"] for e in es),
            "main_out_tokens": sum(e["main_model"]["out_tokens"] for e in es),
            "main_tool_calls": sum(e["main_model"]["tool_call_count"] for e in es),
            "delegations": sum(1 for e in es if e["delegation"]),
            "delegation_ok": sum(1 for e in es
                                 if e["delegation"] and e["delegation"]["status"] == "succeeded"),
            "local_in_tokens": sum((e["delegation"] or {}).get("input_tokens", 0)
                                   for e in es),
            "local_out_tokens": sum((e["delegation"] or {}).get("output_tokens", 0)
                                    for e in es),
            "decisions": {},
        }
        for e in es:
            d = e["decision"]
            groups[g]["decisions"][d] = groups[g]["decisions"].get(d, 0) + 1
    return {"groups": groups,
            "note": "Oracle 为关键词级自动判定；语义正确性需结合 answer 与 "
                    "semantic_hint 人工复核（见下表备注列）。"}


def render_md(summary, events):
    L = ["# 混合模式 A/B 对照评测（P3）", "",
         "- A 组：cloud（无委派工具，云端主模型直答）",
         "- B 组：hybrid（同一云端主模型 + delegate_to_local 有界只读委派）",
         "- 主模型两组相同，差异仅在委派能力；无 PC 客户端轮询，本地文件任务只能靠委派",
         ""]
    ga, gb = summary["groups"]["A"], summary["groups"]["B"]
    L += ["## 总览", "",
          "| 指标 | A cloud | B hybrid |",
          "|---|---|---|",
          "| Oracle 通过 | %d/%d | %d/%d |" % (ga["oracle_pass"], ga["total"],
                                              gb["oracle_pass"], gb["total"]),
          "| 总墙钟(s) | %.0f | %.0f |" % (ga["wall_seconds"], gb["wall_seconds"]),
          "| 主模型输入 tokens | %d | %d |" % (ga["main_in_tokens"], gb["main_in_tokens"]),
          "| 主模型输出 tokens | %d | %d |" % (ga["main_out_tokens"], gb["main_out_tokens"]),
          "| 主模型工具调用次数 | %d | %d |" % (ga["main_tool_calls"], gb["main_tool_calls"]),
          "| 委派次数/成功 | 0/0 | %d/%d |" % (gb["delegations"], gb["delegation_ok"]),
          "| 本地模型 tokens(in/out) | 0/0 | %d/%d |" % (gb["local_in_tokens"],
                                                        gb["local_out_tokens"]),
          "| 委派决策分布 | %s | %s |" % (ga["decisions"], gb["decisions"]),
          "", "> Oracle 是关键词级自动判定；**模型语义正确但措辞不同可能判 FAIL**，"
          "逐条见下表备注与 raw_events.jsonl。", ""]

    L += ["## 逐任务", "",
          "| 任务 | 类别 | 应委派 | A 结果 | B 结果 | B 委派决策 | B 委派状态 | 备注 |",
          "|---|---|---|---|---|---|---|---|"]
    by = {}
    for e in events:
        by.setdefault(e["task_id"], {})[e["group"]] = e
    for tid in sorted(by):
        a = by[tid].get("A")
        b = by[tid].get("B")
        if not a or not b:
            continue
        dl = b["delegation"]
        should = {"True": "是", "False": "否", "optional": "可选"}[str(a["should_delegate"])]
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            tid, a["category"], should,
            "✅" if a["oracle_pass"] else "❌",
            "✅" if b["oracle_pass"] else "❌",
            b["decision"],
            dl["status"] if dl else "—",
            (b["oracle_notes"] or "").replace("|", "/")[:80]))
    L.append("")

    L += ["## 委派明细（B 组）", ""]
    for e in events:
        if e["group"] != "B" or not e["delegation"]:
            continue
        d = e["delegation"]
        L += ["### %s · %s" % (e["task_id"], d["kind"]),
              "- 状态：%s（重试=%s）｜本地模型请求 %d｜沙箱工具 %d｜"
              "tokens in/out %d/%d｜用时 %.0fs"
              % (d["status"], d["retry_used"], d["model_requests"],
                 d["tool_calls"], d["input_tokens"], d["output_tokens"],
                 d["elapsed_seconds"]),
              "- 结构化结果：`%s`"
              % json.dumps(d["structured"], ensure_ascii=False)[:300],
              "- 错误：%s" % (d["errors"] or "无"), ""]
    return "\n".join(L)


if __name__ == "__main__":
    main()
