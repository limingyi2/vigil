# Vigil Agent 能力基线

## 范围与判读

基线覆盖 Agent 执行和状态、工具调用与大型工具输出、流式事件、取消、上下文预算、权限、会话恢复、插件和 Provider。自动化案例见 `benchmark-cases.json`；真实模型、Electron 和外部环境行为场景单列。自动化测试通过仅证明所覆盖的本地测试契约，不证明生产模型体验或未执行行为场景。

### 首次实测

- **基线提交：** `a91b83fa6920c997a4a2e9c3a352615eb033fbc4` (`codex/vigil-agent-improvement-foundation`，PR #10 分支)
- **评测时间：** 2026-10-20 02:46（Asia/Shanghai；报告时间为 2026-10-19 18:46 UTC）
- **环境：** Windows 11 `10.0.26300-SP0`，CPython 3.12.14 64-bit，pytest 本地运行
- **命令：** `python scripts/agent_eval.py run --suite smoke --label foundation-baseline`
- **报告：** `%LOCALAPPDATA%/Vigil/agent-eval/reports/run-20261020T024631-6d7caf09.json`
- **工作区：** 报告记录 `working_tree_dirty=true`；提交 SHA 是评测起始 HEAD，评测过程中工作区含本阶段的评测基础设施改动。该运行不是干净提交快照上的 CI 结果。
- **实际结果：** 选择 3 个自动案例，`PASS=3 / FAIL=0 / TIMEOUT=0 / BLOCKED=0`；12 个自动案例中另有 `NOT RUN=9`；10 个行为场景均 `NOT RUN`。

| 实测案例 | 结果 | 实际证据 |
|---|---|---|
| `agent-run-lifecycle` | PASS | `tests/test_p2_agent_runs.py`，27 项，退出码 0，4.125 秒 |
| `plan-observability` | PASS | `tests/test_plan_observability.py`，11 项，退出码 0，0.687 秒 |
| `runtime-trace` | PASS | `tests/test_runtime_trace.py`，9 项，退出码 0，1.000 秒 |

本轮总用时 5.812 秒。没有真实模型调用、token 统计或 Electron 行为测量；不可从这组 smoke 结果推算它们的成功率。

### 基础设施验收后的自动化测量

2026-10-20 在同一 Windows/CPython 环境继续完成本地自动化覆盖。此结果反映当前 PR worktree（报告标记工作区有改动），不是干净提交的 CI 结果。

| 命令 / 报告 | 评测案例结果 | pytest 结果 |
|---|---|---|
| `python scripts/agent_eval.py run --suite regression --label final-regression-final-run` — `run-20261020T030219-2ffdbd2c.json` | 12 PASS，0 FAIL/TIMEOUT/BLOCKED；2 NOT RUN（security 与 full-local 未选择） | 237 passed，3 skipped |
| `python scripts/agent_eval.py run --suite security --label final-security-final-run` — `run-20261020T030439-65f582e1.json` | 1 PASS，0 FAIL/TIMEOUT/BLOCKED；13 NOT RUN（未选择） | 27 passed |
| `python scripts/agent_eval.py run --suite full-local --label all-local-pytest-acceptance` — `run-20261020T030257-00088aa0.json` | 1 PASS，0 FAIL/TIMEOUT/BLOCKED；13 NOT RUN（逐案例清单中未逐项选择） | 1,381 passed，19 skipped，47 subtests passed；88.60 秒 |

全仓 pytest 的 19 项跳过保留了原因：缺少受支持的 junction/symlink/mkfifo 能力、缺少本机 `app_conn.json`/`_chat.html`/插件素材、FFmpeg 不可用，以及 Windows symlink 权限不足。清单里 10 个真实模型、Electron 或外部 fixture 行为场景仍为 NOT RUN；详见最终验收报告。全仓结果 `PASS` 只代表 1,381 项实际通过，不将 19 项跳过累计成通过。

## 能力与风险矩阵

| 能力域 | 现有评测覆盖 | 实测状态 | 未验证项 / 风险 |
|---|---|---|---|
| Agent 执行、运行生命周期 | `agent-run-lifecycle`, `run-mode-http` | 生命周期自动化 PASS；run-mode HTTP NOT RUN | 真实任务完成质量、长时运行和生产模型表现待验证 |
| 工具调用与权限 | `local-tool-boundaries`, `tool-call-permissions` | NOT RUN | 参数错误、未授权调用、幂等重试和副作用行为待验证 |
| 大型工具输出 | `large-tool-result` 行为场景 | NOT RUN | 输出上限来源、分块检索、静默截断及内存边界待验证 |
| 流式事件与诊断 | `runtime-trace`, `codex-reasoning-events` | runtime trace PASS；reasoning/timeline NOT RUN | 断线重连、事件排序、队列丢失与快照一致性待验证 |
| 任务状态与取消 | `agent-run-lifecycle`, `cancel-contract`、`task-state-and-cancel` | lifecycle PASS；取消案例/场景 NOT RUN | UI 与 Brain 状态一致性、取消传播、取消后不派发待验证 |
| 上下文和历史 | `context-history`, `conversation-isolation`、`context-budget` | NOT RUN | 长上下文、历史截断、预算和工具结果交互待验证 |
| Provider / 超时 / 错误 | `provider-contract`, `provider-timeout` | NOT RUN | 真实连接超时、流中断、畸形事件及重试策略待验证 |
| 会话恢复 | `session-recovery` | NOT RUN | Electron/Brain 重启后的持久状态与瞬态事件边界待验证 |
| 插件 | `plugin-contract`, `plugin-lifecycle` | NOT RUN | 安装、授权、调用、卸载及外部插件隔离待验证 |
| 安全边界 | `local-tool-boundaries`, `tool-call-permissions`, `permission-boundary` | NOT RUN | 越权、路径遍历、秘密泄露和插件隔离需更多验证 |

## 初始风险登记

以下是待验证问题，不是已确认的业务缺陷：

| ID | 风险 | 优先级 | 状态 |
|---|---|---:|---|
| AG-001 | 大型工具结果或累计输出可能受硬上限影响；需确认具体触发源和保留上下文的行为 | P0 | 待验证 |
| AG-002 | 流事件断线恢复、有限队列丢事件和快照完整性 | P1 | 待验证 |
| AG-003 | 前端、Brain、取消与重连后的任务状态一致性 | P1 | 部分自动化覆盖，待验证 |
| AG-004 | 大文件/多文件检索的分块、截断说明和内存上界 | P0 | 待验证 |
| AG-005 | 不确定请求状态下的副作用重试与幂等策略 | P1 | 待验证 |
| AG-006 | 长任务中历史截断、工具结果和上下文预算交互 | P1 | 待验证 |
| AG-007 | 未授权工具、越界路径、秘密泄露和插件隔离 | P1 | 自动化案例已列，首次基线未运行 |
| AG-008 | 测试和诊断数据不进入发行包，用户数据不进仓库 | P1 | 本阶段检查 Git 忽略策略；发行包行为待专项验证 |

## 测量规则

报告应保留提交、分支、工作区是否有改动、Python/平台、命令、suite/case、每项开始/结束时间、耗时、退出码和受限输出。只选择与问题有关的测试；未选择案例必须保留 `NOT RUN`。前后比较要求案例选择和执行环境匹配，修复完成前还需人工确认根因证据与验收条件。Fake Provider 只能说明控制流/契约；不能代替真实模型质量评估。
