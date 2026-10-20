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

### 质量冲刺后自动化测量（2026-10-20）

2026-10-20 在同一 Windows/CPython 环境继续完成本地自动化覆盖。此结果反映当前 PR worktree（报告标记工作区有改动），不是干净提交的 CI 结果。

| 命令 / 报告 | 评测案例结果 | pytest 结果 |
|---|---|---|
| `python scripts/agent_eval.py run --suite regression --label sprint-round3-regression` — `run-20261020T075603-2252ce2d.json` | 12 PASS，0 FAIL/TIMEOUT/BLOCKED；2 NOT RUN（security 与 full-local 未选择） | 240 passed，3 skipped |
| `python scripts/agent_eval.py run --suite security --label sprint-round3-security` — `run-20261020T075641-c5d65998.json` | 1 PASS，0 FAIL/TIMEOUT/BLOCKED；13 NOT RUN（未选择） | 29 passed |
| `python scripts/agent_eval.py run --suite full-local --label sprint-round3-full-local` — `run-20261020T075418-809a0ebe.json` | 1 PASS，0 FAIL/TIMEOUT/BLOCKED；13 NOT RUN（聚合全仓案例） | 1,384 passed，19 skipped；105.47 秒 |
| `node --test client/tools/test-agent-center-notifications.js ... test-localtool-read.js`（8 个显式文件） | Node 原生测试 PASS | 56 passed，0 failed |

全仓 pytest 的 19 项跳过保留了原因：缺少受支持的 junction/symlink/mkfifo 能力、缺少本机 `app_conn.json`/`_chat.html`/插件素材、FFmpeg 不可用，以及 Windows symlink 权限不足。清单里 10 个真实模型、Electron 或外部 fixture 行为场景仍为 NOT RUN；详见最终验收报告。全仓结果 `PASS` 只代表 1,384 项实际通过，不将 19 项跳过累计成通过。另尝试运行完整 `client/tools/test-*.js` 聚合：69 个 Node tests 中 56 通过，13 个文件在初始化阶段因本地没有 `electron`、`ws` 或 `ajv/dist/2020` 依赖而退出；不将这些加载失败算作通过。单独选择无 Electron 依赖的 8 个文件后 56/56 通过。

## 能力与风险矩阵

| 能力域 | 现有评测覆盖 | 实测状态 | 未验证项 / 风险 |
|---|---|---|---|
| Electron 启动、窗口、IPC | `client/tools/test-*-electron.js` 与 E2E | 纯 Node 组件中可测部分 PASS；Electron 运行时 BLOCKED | worktree 无 `client/node_modules/electron`；启动、窗口销毁及真实 IPC 待 Electron 环境验证 |
| Brain 启动、HTTP 路由与运行状态 | `run-mode-http`, `agent-run-lifecycle` | 6 与 28 个测试通过 | 隔离生产进程生命周期已覆盖；与 Electron 的真实联机行为待验证 |
| Provider 配置、流式响应和错误 | `provider-contract`, `codex-reasoning-events` | 37 与 12 个测试通过 | 真实供应商断流、认证错误、空响应和模型质量未验证 |
| Agent 规划、多步骤执行和完成判定 | `agent-run-lifecycle`, `plan-observability` | 28 与 11 个测试通过 | 真实模型多步骤完成质量待验证 |
| 工具调用、权限和大型输出 | `local-tool-boundaries`, `tool-call-permissions` | 19 与 29 个测试通过；大结果保留有界预览 | 真实模型处理截断提示及非幂等工具重试仍待验证 |
| 大文件/多文件检索 | Node `test-localtool-read.js`，Python schema/转发契约 | 真实临时大文件分块往返与 UTF‑8 边界 PASS；Python 19 项工具边界套件 PASS | 工作区内 Node fixture 验证，不代表 Electron UI 或模型实际选择后续 offset |
| 流式事件、断线重连和诊断 | `runtime-trace`, `codex-reasoning-events`, 前端 timeline 状态测试 | 9、12 项 Python；timeline 组件测试 PASS | 真正 Electron 断线恢复、真实事件队列压力待验证 |
| 任务状态、取消、重试 | `agent-run-lifecycle`, `cancel-contract`, Agent Center 状态测试 | 自动化 PASS；取消套件 11 项 | 真实模型请求中断与 UI 联动待 Electron 验证 |
| 上下文、会话历史及隔离 | `context-history`, `conversation-isolation` | 47 与 12 项通过 | 真实超长任务的 token 预算/压缩和多用户数据目录待测 |
| 会话恢复 | 基础历史/状态契约 | 部分自动化 PASS | Electron/Brain 重启后的端到端持久恢复待验证 |
| 插件、Creator、媒体 | `plugin-contract` 38 项及 Node plugin-media 用例 | 本地静态/事务和媒体组件通过 | Electron 插件面板与外部插件生命周期受依赖限制 |
| 工作区、文件访问和权限 | `local-tool-boundaries`, `tool-call-permissions` | PASS；范围与拒绝行为有自动化覆盖 | 真实客户端允许目录和用户特定权限组合待人工验证 |
| 模型设置、更新与安装升级 | 纯 Node public-updates/unified-runtime 测试 PASS | 协议配对、哈希、可信下载域及快照测试通过 | 完整 Electron 安装器/升级/卸载未执行；缺少 node_modules 和 Brain 构建产物 |
| 发行包内容与用户数据保护 | `build_release.py --help` PASS；`--verify` BLOCKED | 静态文件规则和更新快照逻辑测试通过 | 校验因缺少 `client/build-info.json` 终止；未构建 Windows 候选包 |

## 初始风险登记

以下是待验证问题，不是已确认的业务缺陷：

| ID | 风险 | 优先级 | 状态 |
|---|---|---:|---|
| AG-001 | 大型工具结果曾因 65,536 字符硬上限终止任务 | P1 | 已修复并有回归测试；模型输出本身超限仍按硬上限安全失败 |
| AG-002 | 流事件断线恢复、有限队列丢事件和快照完整性 | P1 | 待验证 |
| AG-003 | 前端、Brain、取消与重连后的任务状态一致性 | P1 | 部分自动化覆盖，待验证 |
| AG-004 | 大文件读取分块及明确继续 offset；工具返回有界 | P1 | 实现并通过隔离大文件/UTF‑8 回归；真实 UI/模型交互待验证 |
| AG-005 | 不确定请求状态下的副作用重试与幂等策略 | P1 | 待验证 |
| AG-006 | 长任务中历史截断、工具结果和上下文预算交互 | P1 | 待验证 |
| AG-007 | 未授权工具、越界路径、秘密泄露和插件隔离 | P1 | 自动化案例已列，首次基线未运行 |
| AG-008 | 测试和诊断数据不进入发行包，用户数据不进仓库 | P1 | 本阶段检查 Git 忽略策略；发行包行为待专项验证 |

## 测量规则

报告应保留提交、分支、工作区是否有改动、Python/平台、命令、suite/case、每项开始/结束时间、耗时、退出码和受限输出。只选择与问题有关的测试；未选择案例必须保留 `NOT RUN`。前后比较要求案例选择和执行环境匹配，修复完成前还需人工确认根因证据与验收条件。Fake Provider 只能说明控制流/契约；不能代替真实模型质量评估。

## 五轮改进后的测量（2026-10-20）

测量分支仍为 `codex/vigil-agent-improvement-foundation`。提交变更前在 Windows 11 / CPython 3.13（Codex workspace Python）下运行；最终全仓结果来自无并行测试竞争的重跑。

| 验证范围 | 实际结果 | 本机报告 |
|---|---|---|
| Regression suite | 12 案例 PASS，0 FAIL/TIMEOUT/BLOCKED，2 NOT RUN；pytest 244 passed、3 skipped | `run-20261020T102801-74c2e589.json` |
| Security suite | 1 案例 PASS，pytest 29 passed | `run-20261020T103048-56df5d22.json` |
| 全仓 Python pytest | 1 聚合案例 PASS；1,388 passed、19 skipped，0 failed | `run-20261020T102908-1aea2f90.json` |
| Node 核心组件回归 | 49 passed、0 failed（Agent Center、timeline、stream lifecycle、统一更新 runtime） | 控制台输出；`node --test` 未单独落评测 JSON |
| 清单 | 14 自动案例、4 suites、10 test sets、10 behavior scenarios 格式校验通过 | `agent_eval.py validate` stdout |
| 复现对比 | AG-009、AG-011、AG-013 均为同选择/环境 `FAIL → PASS (FIXED)`；最终 regression 与轮前 baseline 12 个共同案例均 UNCHANGED PASS | `compare-8991e541bc.json`、`compare-9fe0d1ca1e.json`、`compare-9d91a3082d.json`、`compare-a514fff3ea.json` |

第一次全仓执行与 regression 并行，出现隔离 Brain 60 秒启动失败，聚合报告 `run-20261020T102724-04ab9dd3.json` 为 FAIL（1,373 passed、19 skipped、15 errors）。停止并行后原样串行重跑通过；不把首次失败删除或覆盖。19 项 skip 仍是 skip，原因见 pytest 报告。真实模型、Electron 进程/窗口、外部插件和发行包端到端仍未验证：本环境没有 Electron runtime、`client/node_modules` 或 npm；这些不计为通过。

本轮改动只涉及终态 Agent reasoning transient 清理、更新快照目录隔离和插件卸载结果/清理错误处理，相关已知功能性风险 AG-002—AG-008 仍按前文状态保留，不能据自动测试推断为全部解决。
