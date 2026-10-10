# Vigil 真实模型第一批结果

已完成 16/16 个 task-run。模型：`mimo-v2.6-flash`。每 run 熔断：8 次模型请求、8 次工具调用、180 秒。

统计中的 token 为 API 已报告的数值；覆盖数不足时总数只是已知部分。

## Overall（全部任务）

| 组别 | run | Oracle 通过 | Planner 决策 | 有效 plan | fallback | Checker | revision | 模型请求 | token 输入/输出 | 工具调用 | 总耗时/均值（秒） |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| A_direct | 8 | 3 | 0 | 0 | 0 | 0 | 0 | 19 | 17734 / 1678（8/8 run 报告） | 18 | 94.62 / 11.83 |
| B_current | 8 | 3 | 5 | 5 | 0 | 1 | 0 | 17 | 10130 / 1962（8/8 run 报告） | 12 | 115.85 / 14.48 |
| total | 16 | 6 | 5 | 5 | 0 | 1 | 0 | 36 | 27864 / 3640（16/16 run 报告） | 30 | 210.47 / 13.15 |

## Planner-eligible subset（5 条命中任务的 A/B 配对）

| 组别 | run | Oracle 通过 | Planner 决策 | 有效 plan | fallback | Checker | revision | 模型请求 | token 输入/输出 | 工具调用 | 总耗时/均值（秒） |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|
| A_direct | 5 | 2 | 0 | 0 | 0 | 0 | 0 | 12 | 11903 / 1288（5/5 run 报告） | 13 | 68.19 / 13.64 |
| B_current | 5 | 2 | 5 | 5 | 0 | 1 | 0 | 11 | 5170 / 1550（5/5 run 报告） | 8 | 87.14 / 17.43 |
| total | 10 | 4 | 5 | 5 | 0 | 1 | 0 | 23 | 17073 / 2838（10/10 run 报告） | 21 | 155.33 / 15.53 |

## 单 run 明细

| 任务 | 组别 | Oracle | Planner eligible | decision | plan | fallback | Checker | revision | 请求 | token 输入/输出 | 工具 | 秒 | 熔断 |
|---|---|---|---|---|---|---|---|---|---:|---|---:|---:|---|
| F02 | A_direct | 失败 | True | False | False | False | False | False | 2 | 1518/114 | 2 | 8.14 | — |
| F02 | B_current | 失败 | True | True | True | False | False | False | 2 | 870/270 | 2 | 10.78 | — |
| M01 | A_direct | 失败 | False | False | False | False | False | False | 2 | 1772/121 | 1 | 8.44 | — |
| M01 | B_current | 失败 | False | False | False | False | False | False | 2 | 1773/76 | 1 | 5.47 | — |
| P02 | A_direct | 失败 | True | False | False | False | False | False | 5 | 6669/729 | 8 | 29.39 | tool_call_limit |
| P02 | B_current | 失败 | True | True | True | False | True | False | 3 | 1527/561 | 2 | 37.47 | — |
| L01 | A_direct | 通过 | True | False | False | False | False | False | 2 | 1508/237 | 2 | 11.36 | — |
| L01 | B_current | 通过 | True | True | True | False | False | False | 2 | 878/289 | 1 | 15.56 | — |
| L04 | A_direct | 失败 | True | False | False | False | False | False | 1 | 707/86 | 0 | 5.31 | — |
| L04 | B_current | 失败 | True | True | True | False | False | False | 2 | 1021/284 | 2 | 12.77 | — |
| T01 | A_direct | 通过 | True | False | False | False | False | False | 2 | 1501/122 | 1 | 13.98 | — |
| T01 | B_current | 通过 | True | True | True | False | False | False | 2 | 874/146 | 1 | 10.56 | — |
| T02 | A_direct | 通过 | False | False | False | False | False | False | 2 | 1477/88 | 1 | 9.27 | — |
| T02 | B_current | 通过 | False | False | False | False | False | False | 2 | 1513/156 | 1 | 14.05 | — |
| S02 | A_direct | 失败 | False | False | False | False | False | False | 3 | 2582/181 | 3 | 8.73 | — |
| S02 | B_current | 失败 | False | False | False | False | False | False | 2 | 1674/180 | 2 | 9.19 | — |

## 未通过任务

- `F02/A_direct`：oracle_failed；final answer contains required sentinel; no undeclared tool execution; declared artifacts exist
- `F02/B_current`：oracle_failed；final answer contains required sentinel; no undeclared tool execution; declared artifacts exist
- `M01/A_direct`：oracle_failed；workspace/note.txt contains required text; workspace/note.txt contains required text; workspace/untouched.txt preserved exactly; no undeclared tool execution; declared artifacts exist
- `M01/B_current`：oracle_failed；workspace/note.txt contains required text; workspace/note.txt contains required text; workspace/untouched.txt preserved exactly; no undeclared tool execution; declared artifacts exist
- `P02/A_direct`：oracle_failed；local pytest passed and expected repair exists; no undeclared tool execution; declared artifacts exist
- `P02/B_current`：oracle_failed；local pytest passed and expected repair exists; no undeclared tool execution; declared artifacts exist
- `L04/A_direct`：oracle_failed；final answer contains required sentinel; no undeclared tool execution; declared artifacts exist
- `L04/B_current`：oracle_failed；final answer contains required sentinel; no undeclared tool execution; declared artifacts exist
- `S02/A_direct`：oracle_failed；final answer contains required sentinel; no undeclared tool execution; declared artifacts exist
- `S02/B_current`：oracle_failed；final answer contains required sentinel; no undeclared tool execution; declared artifacts exist

该批仅包含每任务每组一次；没有启动第二轮重复。

## 结果解读与评测限制

- 36 次实际模型请求全部由 `mimo-v2.6-flash` 返回 HTTP 200，36/36 次均报告了 token 用量；分阶段为直答 25、规划 5、规划收尾 5、Checker 1。总计输入 27,864、输出 3,640 token。
- B_current 的 5 个 Planner-eligible run 全部生成可解析 plan 并执行，没有 fallback；Checker 只在 P02 调用 1 次，没有 revision。A_direct 和 B_current 的原始 Oracle 通过率均为 3/8，符合 Planner 条件的配对任务均为 2/5。本批只有每组一次，不能据此判断 Planner 的稳定收益。
- F02 两组均读取了正确的 `a/target.txt` 和 `b/target.txt`，回答分别给出 alpha 与 beta，但 Oracle 要求原样包含 `a=alpha; b=beta`，因此原始统计判失败。该项属于答案格式与 Oracle 不匹配，不应解读为文件检索失败；原始分数未改动。
- M01 请求没有明确“新标题”的目标字面值，模型只读取了文件并询问目标值；P02 请求未给出真实 `calc.py` / `test_calc.py` 文件名，B 组 plan 猜了不存在的文件，A 组虽运行了 pytest，但之后反复猜文件名并触发 8 次工具调用熔断。L04 和 S02 也没有给出实际文件名，模型分别猜了不存在的日志文件和 README 变体。这些题面与工具发现能力的缺口会影响 Planner 效果判定。
- T01、T02 两组均按 Oracle 正确报告了模拟 HTTP 404 / 工具超时；L01 两组均定位到 `DB_CONN`。唯一触发的熔断是 P02/A_direct 的工具调用上限。无模型请求上限或 180 秒时间上限触发。
- 16 个 run 使用 16 个不同的 workspace 和会话；每次结束后隔离配置中的 API 凭据文件均已移除。逐次请求、plan 步骤、工具参数和 Oracle 检查见 `events.jsonl`。保留原始记录与原始 Oracle 结果，不做事后改分或补跑。
