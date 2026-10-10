"""Recompute comparisons from archived data only; no model or task execution."""
from pathlib import Path
import csv
import json

ROOT = Path(__file__).resolve().parent


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def events(path):
    return [json.loads(line) for line in (ROOT / path).read_text(encoding="utf-8").splitlines() if line.strip()]


def aggregate(rows):
    return {
        "runs": len(rows),
        "oracle_passed": sum(bool(e["success"]) for e in rows),
        "planner_decisions": sum(bool(e["planner_decision"]) for e in rows),
        "effective_plans": sum(bool(e["plan_effective"]) for e in rows),
        "fallbacks": sum(bool(e["fallback"]) for e in rows),
        "checker_calls": sum(bool(e["checker_invoked"]) for e in rows),
        "revisions": sum(bool(e["revision_invoked"]) for e in rows),
        "model_requests": sum(e["model_calls"] for e in rows),
        "input_tokens_known": sum(e["tokens"]["input"] for e in rows),
        "output_tokens_known": sum(e["tokens"]["output"] for e in rows),
        "tool_calls": sum(e["tool_call_count"] for e in rows),
        "elapsed_seconds_total": round(sum(e["elapsed_seconds"] for e in rows), 2),
        "elapsed_seconds_mean": round(sum(e["elapsed_seconds"] for e in rows) / len(rows), 2),
    }


def write_csv(name, rows):
    with (ROOT / name).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    live = events("data/mimo-planner-first-batch/events.jsonl")
    recorded = load("data/mimo-planner-first-batch/summary.json")
    assert len(live) == 16
    assert len({e["workspace"] for e in live}) == 16
    assert len({e["session_id"] for e in live}) == 16
    computed = {}
    for key, selected in [("overall", live), ("planner_eligible_subset", [e for e in live if e["planner_eligible"]])]:
        computed[key] = {}
        for group in ["A_direct", "B_current"]:
            stats = aggregate([e for e in selected if e["group"] == group])
            assert all(recorded[key][group][k] == v for k, v in stats.items()), (key, group)
            computed[key][group] = stats
    paired = []
    table = ["| 任务 | Planner eligible | A/B Oracle | 请求 A/B | 输入 token A/B | 输出 token A/B | 工具 A/B | 秒 A/B |", "|---|---|---|---:|---:|---:|---:|---:|"]
    for task in dict.fromkeys(e["task_id"] for e in live):
        a, b = [next(e for e in live if e["task_id"] == task and e["group"] == g) for g in ["A_direct", "B_current"]]
        row = {"task_id": task, "planner_eligible": a["planner_eligible"], "A_oracle_pass": a["success"], "B_oracle_pass": b["success"]}
        fields = {"model_requests": "model_calls", "tool_calls": "tool_call_count", "seconds": "elapsed_seconds"}
        for label, field in fields.items():
            row.update({"A_" + label: a[field], "B_" + label: b[field], "delta_B_minus_A_" + label: round(b[field] - a[field], 3)})
        for label in ["input", "output"]:
            row.update({"A_tokens_" + label: a["tokens"][label], "B_tokens_" + label: b["tokens"][label], "delta_B_minus_A_tokens_" + label: b["tokens"][label] - a["tokens"][label]})
        row.update({"A_breaker": a["circuit_breaker"], "B_breaker": b["circuit_breaker"]})
        paired.append(row)
        table.append(f"| {task} | {'是' if a['planner_eligible'] else '否'} | {'通过' if a['success'] else '失败'} / {'通过' if b['success'] else '失败'} | {a['model_calls']}/{b['model_calls']} | {a['tokens']['input']}/{b['tokens']['input']} | {a['tokens']['output']}/{b['tokens']['output']} | {a['tool_call_count']}/{b['tool_call_count']} | {a['elapsed_seconds']:.2f}/{b['elapsed_seconds']:.2f} |")
    write_csv("paired_runs.csv", paired)
    qwen_rows = []
    for d in sorted((ROOT / "data/qwen-agent").iterdir()):
        for e in events(str(d.relative_to(ROOT) / "raw_events.jsonl")):
            t, o = e["trace"], e["oracle"]
            qwen_rows.append({"run_id": d.name, "task_id": e["task_id"], "oracle_pass": o["pass"], "model_requests": t["model_calls"], "tool_calls": t["tool_call_count"], "input_tokens": t["tokens"]["input"], "output_tokens": t["tokens"]["output"], "seconds": t["elapsed_seconds"], "breaker": t["circuit_breaker"], "provenance": "A03 rerun merged into this batch; not original first run" if e["task_id"] == "A03" and d.name.endswith("002805") else "separate A03 rerun; duplicate of merged row" if d.name.endswith("003059") else "available recorded event"})
    write_csv("qwen_runs.csv", qwen_rows)
    initial = load("data/qwen-agent/ollama-agent-smoke-20261011-002805/progress/A03/progress.json")
    rerun = events("data/qwen-agent/ollama-agent-smoke-20261011-003059/raw_events.jsonl")[0]
    merged = next(e for e in events("data/qwen-agent/ollama-agent-smoke-20261011-002805/raw_events.jsonl") if e["task_id"] == "A03")
    assert merged["trace"] == rerun["trace"]
    stub = []
    for d in sorted((ROOT / "data/stub").iterdir()):
        rows = events(str(d.relative_to(ROOT) / "events.jsonl"))
        stub.append({"run_id": d.name, "runs": len(rows), "passed": sum(bool(e["success"]) for e in rows), "actual_model_requests": 0})
    comparison = {"generated_from_existing_data_only": True, **computed, "paired_tasks": paired, "qwen_A03_initial_progress": initial, "qwen_A03_rerun": rerun["trace"], "qwen_merged_A03_equals_rerun": True, "qwen_original_failed_A03_trace_available": False, "stub_history": stub}
    (ROOT / "comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    overview = []
    for label in ["overall", "planner_eligible_subset"]:
        for group, s in computed[label].items():
            overview.append(f"| {label} | {group} | {s['oracle_passed']}/{s['runs']} | {s['planner_decisions']} | {s['effective_plans']} | {s['fallbacks']} | {s['checker_calls']} | {s['revisions']} | {s['model_requests']} | {s['input_tokens_known']}/{s['output_tokens_known']} | {s['tool_calls']} | {s['elapsed_seconds_total']}/{s['elapsed_seconds_mean']} |")
    after = rerun["trace"]
    report = """# Agent 测试对比（归档日期：2026-10-11）

本报告由已有 JSONL / JSON 复算，没有新运行模型。数据来源与证据限制见 [README](README.md)。

## 1. MiMo：同任务 A_direct / B_current 对照

模型均为 `mimo-v2.6-flash`，8 条任务，每组一次，共 16 个 task-run。单 run 上限为 8 次模型请求、8 次工具、180 秒。B_current 使用现有 Planner 和条件式 Checker。

| 统计范围 | 组别 | Oracle | Planner 决策 | 有效 plan | fallback | Checker | revision | 模型请求 | token 输入/输出 | 工具 | 总秒/均秒 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
""" + "\n".join(overview) + """

整体 B 比 A 少 2 次请求、少 7,604 输入 tokens、少 6 次工具调用，输出增加 284 tokens，总耗时增加 21.23 秒。Oracle 通过数没有提升。Planner eligible subset 的两组都通过 2/5；B 的 5 个计划均有效，没有 fallback，Checker 实际调用 1 次，revision 为 0。

""" + "\n".join(table) + """

P02/A_direct 触发工具调用数熔断。F02 的格式硬匹配、M01 修改目标不明确，以及 P02 / L04 / S02 文件发现问题影响了评分；详细解释见[原报告](data/mimo-planner-first-batch/summary.md)。保留原始判分，不做事后改分。只有一次配对，不能用于稳定性或显著性结论。

## 2. Qwen：环境修复与单题复测

模型为 `qwen27b-efficientthink:latest`，Ollama；配置记录为 `num_ctx=16384`、`num_predict=2048`、`think=true`，temperature 0.85。每 run 上限为 6 次模型请求、8 次工具、180 秒。原始环境文件没有记录源码 commit。

| 同题比较 | 前 | 后 | 证据范围 |
|---|---|---|---|
| A01 | 缺少 numpy，0 次模型请求，Oracle 失败 | 环境修复后单题试跑通过，5 请求 / 7 工具，18.562 秒 | 两次完整 event 可用；不是模型能力提升 |
""" + f"| A03 | 失败记录描述 pytest 未安装；进度记录 {initial['model_calls']} 请求 / {initial['tool_call_count']} 工具，{initial['elapsed_seconds']} 秒，`{initial['circuit_breaker']}` | 独立复测 Oracle 通过，{after['model_calls']} 请求 / {after['tool_call_count']} 工具，{after['elapsed_seconds']} 秒；pytest 工具返回 exit 0 | 原失败完整 event 缺失；前后不能做同等完整的轨迹审计 |\n" + """

A03 首轮遗留进度记录为 10,567 输入 / 590 输出 tokens；复测为 10,526 / 472。初次失败只保留了进度和异常说明，**不补造缺失的回答、工具参数或 Oracle 明细**。

六题目录 `002805` 的 summary 与 raw_events 都已经合入来自 `003059` 的 A03 复测；两者的 A03 trace 完全相同。因此现有汇总的 **5/6** 是“合入复测后的六题视图”，不是原始首轮成绩，两个 A03 条目也不能算作独立样本。该视图合计 25 次请求、27 次工具、35,243 输入 / 2,894 输出 tokens、约 105.66 秒。

A05 仍为 Oracle 失败：记录显示识别出 `ModuleNotFoundError: No module named 'crypto'`，但回答没有字面引用 `build.log`。这是可观察的判据不满足；历史报告对其能力原因的评价不替代原始判分。历史报告中的“28 次工具”和“约 70 秒”等说法与当前合并视图的 27 次 / 105.66 秒不一致，以逐次事件和本报告的统计为准。

这套 Qwen 题目与 MiMo 八题不同，不能把 3/8 → 5/6 写成同一实验的改进幅度。

## 3. Harness 的演进

Stub 仅检验执行语义、异常模拟、隔离与 Oracle。以下记录均没有真实模型调用，事件内的 model_calls 是模拟值。

| Stub run | task-run | Oracle 通过 | 真实模型请求 |
|---|---:|---:|---:|
""" + "\n".join(f"| {s['run_id']} | {s['runs']} | {s['passed']} | 0 |" for s in stub) + """

从 4 题 × 2 组的 smoke 扩展到 24 题 × 2 组，说明 Harness 的覆盖面扩展，不能解释为真实 Agent 成功率提高。历史 smoke 报告中的完整实验预算为当时方案，不代表后来执行了 144 个真实 run。

## 4. 用户提供记录中的其他前后信息

| 项目 | 原记录 | 后续记录 | 可比较性 |
|---|---|---|---|
| Qwen H2 | 3/25 评分，patch 不完整 | 扩大输出预算后测试 15/15 | 指标不同，不能计算评分提升比例 |
| Qwen H3 | 最终 JSON 输出不完整 | 三种复测配置均可生成合法 JSON | 可说明配置影响；仅有汇总材料 |
| 上下文诊断 | 曾误判 32K 设置只能读 16K | 独立诊断记录实际输入 27,847 tokens 并召回标记 | 后一次纠正前一次推断；不证明复杂长任务可靠性 |

这些项目前没有取得原始逐次文件，只能引用[用户提供的记录](vigil-evaluation-record-2026-10-10.md)，不能当作本次从 raw 数据复算的结论。原记录中的 Qwen 真实 Agent 六题“待执行”已经由本目录的后续数据更新。
"""
    (ROOT / "comparison.md").write_text(report, encoding="utf-8")
    print("Archived comparison validated: 16 MiMo runs, 8 pairs, 5 eligible tasks; no API calls.")


if __name__ == "__main__":
    main()
