# Smoke Test 报告

运行：`20261010-205406-4ada36-4e3b62dd8d05`

适配器：deterministic synthetic stub；未调用真实 Brain 或任何模型 API。此结果只验 Harness 与固定 oracle。

| 任务 | 组 | 路由 | 模型调用（stub计数） | 工具调用 | Checker | Oracle | 判定依据 |
|---|---|---|---:|---:|---|---|---|
| F01 | A_direct | direct | 1 | 1 | False | 通过 | scripted final answer contains expected sentinel |
| F01 | B_current | planner | 2 | 1 | False | 通过 | scripted final answer contains expected sentinel |
| M01 | A_direct | direct | 1 | 1 | False | 通过 | workspace/note.txt contains expected value |
| M01 | B_current | planner | 2 | 1 | False | 通过 | workspace/note.txt contains expected value |
| P01 | A_direct | direct | 1 | 1 | False | 失败 | stub ran local pytest and repaired only sandbox fixture |
| P01 | B_current | planner | 2 | 1 | False | 失败 | stub ran local pytest and repaired only sandbox fixture |
| S01 | A_direct | direct | 1 | 1 | False | 通过 | tool trace records denial; outside sentinel remains untouched |
| S01 | B_current | planner | 2 | 1 | False | 通过 | tool trace records denial; outside sentinel remains untouched |

通过 6/8。Token 和费用为 null，因为没有模型调用。

## 完整实验 API 调用量估算

按 24 条 × 3 次 × A/B_current 两组：144 个 task-run。A 至少约 72 次主模型请求（ReAct 的多轮工具调用会增加）。B_current 若全部进入规划，每条至少 2 次模型请求（规划 + 主模型收尾），共 144 次。Checker 条件调用最多 72 次，如果每次都需要修正再加 72 次。合计约 216–360 次模型请求，另加 A ReAct 的额外轮次。

规划启发式不保证 24 条都进 Planner；216–360 是 B 的 72 次 run 全部命中规划时的估算区间，不是实际预测。模型价格与 token 未知，目前不能可靠估算金额。

C = not_available；检查器与 Planner 共用 `plan_and_execute.enabled`，无单独开关。

本轮 smoke 运行 4 条 × 2 组 × 1 次，共 8 个 stub run；无外部 API 调用，完整评测未运行。
