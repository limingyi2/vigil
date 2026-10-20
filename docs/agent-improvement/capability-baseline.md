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

## 2026-10-20 — 第二阶段真实运行环境测量

本节以 Windows 隔离环境对当前 PR 分支进行的运行结果为准，补充并更新上面的早期“无 Electron 依赖”状态。测试没有读取真实用户配置或连接真实模型；所有聊天、插件、Brain 数据和令牌均为临时 Fake Provider fixture。

### 环境与真实运行结果

- **提交基线：** `aeacf166ea1478b830e6dfb9a0d9d7b50a6f21b9`；工作分支 `codex/vigil-agent-improvement-foundation`。
- **环境：** Windows 10 Pro x64，build 26300；Node 24.19.0 / npm 11.17.0（便携安装于用户本地状态目录）；CPython 3.12.14 隔离 venv；Electron 28.3.3 由 `npm ci` 安装；Brain 依赖来自仓库需求文件。
- `client/tools/test-electron-brain-e2e.js` 在真实 Electron renderer + 本地 Python Brain + loopback Fake Provider 中 **8 项 PASS**：插件页面加载、冷启动欢迎页、会话历史/隔离、工具成功及失败反馈、SSE 多事件单次终态、Creator 草稿与会话恢复、Brain 重启和进程断开后的 `interrupted` 展示及解锁。
- Node 原生组件回归：8 个明确包含 `node:test` 的文件 **56 passed / 0 failed / 0 skipped**，覆盖 Agent Center 游标缺口/重连快照、timeline 快照恢复、流生命周期、工具分块读取、更新事务与哈希。
- `agent_eval.py run --suite regression`：12 个声明案例 PASS，pytest 240 passed / 3 skipped；security 1 案例 PASS，29 passed；全仓 `python -m pytest -rs` 聚合案例 PASS，1,388 passed / 19 skipped / 0 failed / 47 subtests passed，114.01 秒。19 项 skipped 未计入 passed，原因和原始 pytest 摘要保留在本机报告。
- 任务工作流以独立用户状态目录中的合成任务验证 `OPEN → BLOCKED → IN_PROGRESS → READY_FOR_VERIFY → COMPLETED`；blocked resume 没有执行测试，后测和同条件比较关联成功。合成任务不是 Vigil 缺陷记录。

### P1 风险现状（本轮没有确认或修复业务 Bug）

| ID | 优先级 | 当前证据 | 状态 |
|---|---:|---|---|
| AG-002 | P1 | Electron/Brain/Fake Provider 300 delta 流；断开后从事件 cursor 重连，超过 256 事件窗口时取得一致终态 snapshot；Agent Center 的 Electron/preload fixture 25 项通过，覆盖断开/重连、generation 和 gap 重同步 | **部分验证**。Agent Center fixture 不是本地 Agent run/真实 Codex；本地客户端自动重连、长时持续压力和代理故障未验证 |
| AG-003 | P1 | Electron API bridge 取消/1 秒超时；Electron 退出期间 Brain 完成并恢复；Brain 重启后工具=`unknown`；renderer UI 点击取消/刷新恢复；慢 side-effecting plugin 已 in-flight 时取消，终态保持 cancelled、步骤结算为 returned 且没有模型 follow-up | **部分验证**。尚未覆盖 UI 点击取消与慢工具同场组合、窗口退出期间的全终态矩阵；已派发副作用可能继续运行，UI 会明确说明 |
| AG-005 | P1 | 同 request ID 不重复模型/工具调用；本地插件副作用未知后 Brain 重启为 interrupted/unknown 不重放；Fake Provider 已收请求后销毁响应 socket，run=`connection_lost`，相同 request_id 不重发 Provider/工具 | **部分验证**。仅本地 Fake Provider/插件；真实 Provider 接收后丢响应、第三方副作用和跨系统幂等仍未验证 |
| AG-008 | P1 | 旧候选 fresh NSIS 安装/首次聊天/重启/卸载保留 70 个 profile 文件 PASS；官方 1.0.2→旧候选升级迁移、Brain mismatch 阻断、精确 rollback 与失败 checkpoint 保护 PASS。当前 `2eb46d4` 候选 build/manifest/privacy audit 和 unpacked 首启/聊天/重启 PASS；当前 installer SHA 记录在本机 phase2 最终报告 | **部分验证**。当前 HEAD 的 NSIS 生命周期受宿主正在运行的 Vigil 阻塞；旧版本跨度、真实用户设备文件系统、安装器 verbose log 未验证 |

当前不允许提出“全部 P1 关闭”或完整产品交付结论。真实模型认证/限流/空响应的专用故障注入、Electron 窗口退出、长时事件队列压力和安装器升级回滚范围见 `phase2-real-runtime-acceptance.md`。

### P1 追加测量（2026-10-20）

本次测试扩展基于提交 `e2f39c160e9e74ff235ac297122783d17cdcc56d`，只增加隔离 E2E fixture 和报告。真实 Electron/Brain/Fake Provider 的完整 E2E **15 PASS**，包含 Electron 退出期间 Brain 完成 run 并在 relaunch 后恢复；最终日志 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-p1-agent-e2e-final2.log`。中间失败尝试和夹具调整记录在 `phase2-real-runtime-acceptance.md`，失败日志未覆盖。相关后端回归 `python -m pytest tests/test_p2_agent_runs.py tests/test_p2_agent_http.py -ra`：34 passed、8 subtests passed、0 skipped，5.12 秒；日志 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-agent-backend-regression-final.log`。评测执行器中的 `agent-run-lifecycle` 单案例也 PASS（3.328 秒），本机报告 `%LOCALAPPDATA%/Vigil/stage2-state/eval-reports/reports/run-20261020T114016-7c7b7153.json`。真实模型和外部 Provider 仍 BLOCKED/未运行，Fake Provider 结果不代表真实供应商验收。

额外实测 `client/tools/test-agent-center-electron.js`，通过 Electron renderer、正式 preload 和隔离 Codex App Server fixture：25/25 PASS，0 renderer/preload errors、0 模型请求。覆盖明确断开与重连、连接 generation 变化、cursor gap 后恢复、历史事件不触发通知、401 后恢复和任务状态；结果在 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-agent-center-electron/electron-results.json`。它不是本地 Agent run 的 SSE 客户端，也不代表真实 Codex 服务；AG-002 仍为部分验证。

## P1 运行时追加基线（2026-10-20，基于 `dbfb7f5aa2cbdb5d409fc83149f1053b58f5fae2`）

本节追加测量优先于文档中较早的“无 Electron 运行时”基线；历史测量仍保留。隔离环境为 Windows Electron 28.3.3 + 本地 Python Brain + loopback Fake Provider，临时配置、会话、插件和副作用目录均位于测试目录。当前分支工作区包含尚未提交的 AG-002 修改，因此下列结果不能归属于干净提交；后续提交/候选包还需重验。

| 模块 | 最新检查 | 当前能力证据 | 未验证/风险 |
|---|---|---|---|
| Electron/Brain 启动、IPC、连接与恢复 | 完整 E2E 16/16 PASS | 冷启动、Brain 重启、Brain 断开、会话切换、Agent chat SSE 单次断线重连；日志 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-ag002-chat-reconnect-full-e2e.log` | 真实 Provider/Codex；多次连续断线、主进程崩溃与高压队列组合 |
| Agent 状态、取消、超时、重复请求 | 完整 E2E PASS | 成功、失败、取消、超时、退出窗口、重启恢复；未知工具结果不重放副作用 | 外部系统副作用和真实 Provider 已接收但丢响应 |
| SSE/事件游标 | 真实 Electron 故障注入 PASS；后端 Agent HTTP/lifecycle 34 PASS + 8 subtests | Agent `seq` 投影，按 `after` 恢复；重放同一 run；超事件窗口时 snapshot；UI request_id 展示记录成功 | 长时压力测试、无限/多次断网和外部代理故障 |
| 工具和权限 | Agent 及全量测试结果见最新验收记录 | 本地插件副作用 fixture、权限和重复 request_id 自动化回归通过 | 真实模型工具选择；真实用户工作区组合 |
| 插件/Creator/会话 | Electron E2E 16/16，既有 Node 组件 56/56 | 插件成功/失败、会话隔离、Brain 重启后记录恢复 | 真实外部插件依赖和真实数据迁移组合 |
| Windows 候选运行时/安装升级 | 本轮提交的配对候选 `win-unpacked` 启动、bundled Brain health、Fake Provider 首次聊天及 Electron 重启历史恢复 PASS；静态清单 682 ASAR entries / 241 unpacked files，隐私扫描 0 命中 | build-info、release manifest、Brain manifest、关键文件哈希和候选 runtime 日志均在本机验收报告 | NSIS 安装、1.0.2→当前候选升级/回滚、卸载未运行：宿主上用户 Vigil 正在运行；此前 lifecycle 结果只对应旧源码 |
| 真实 Provider | 未连接 | 使用 Fake Provider 完成控制流与故障注入 | 真实认证、限流、空响应和服务端错误均 BLOCKED（无授权隔离凭据） |

当前 P1：AG-002/AG-003/AG-005 仍是**部分验证**；AG-008 当前 `2eb46d4` 源码候选已通过构建/身份/静态清单及 unpacked runtime 首启/聊天/重启检查，但 NSIS 安装生命周期受宿主正在运行的 Vigil 阻塞；此前指定 1.0.2 upgrade 结果只对其对应旧候选成立。更新的 renderer cancel、慢工具取消、Provider response-loss 证据与未覆盖矩阵见 `phase2-real-runtime-acceptance.md`。本文不宣称所有 P1 关闭。全仓 Python 结果和本轮候选详细 SHA 记在阶段验收报告；没运行或阻塞的场景仍分别标为 NOT RUN/BLOCKED。


## 2026-10-20 更新：AG-003 conversation-owner 取消实测

- 最新修复前基线：renderer 点击取消聊天创建的 Agent run 时，Brain 返回 404，run 保持 `running`；原始日志在 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-ag003-ui-slow-tool-cancel-diagnostic.log`。
- 修复后：活动 turn 与历史 turn cancel 请求带上 run 创建时相同的 `src`/`conversation_id`。真实 renderer + 本地 Brain 的慢工具取消场景将 run/UI 转为 cancelled；工具结果结算后没有模型续调或额外工具副作用。完整 E2E 18/18 PASS，backend owner/lifecycle 34 项与 8 subtests PASS，renderer fixture 7/7 PASS。详见 `phase2-real-runtime-acceptance.md` Round 11。
- 最新 `agent_eval regression`：12 PASS、0 FAIL、0 TIMEOUT、0 BLOCKED、2 NOT RUN；报告在用户本地 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-agent-eval-final/reports/run-20261020T124640-15c7dafd.json`。本报告是 dirty tree 上的验证，不代表已提交 HEAD。
- AG-003 仍为 P1/部分验证；完整 Electron/Brain 终态矩阵及真实 Provider 未覆盖。AG-002 持续压力、AG-005 外部副作用与真实 Provider、AG-008 当前源码 NSIS 安装器生命周期仍是待验证/阻塞项。禁止将这些子场景推断为 PASS。


## 2026-10-20 更新：SSE 重连压力测量（Round 13）

- 真实 Electron + 本地 Brain + Fake Provider 的 Agent chat 在同一 run 中经历 3 次代理断线，Electron 完成 3 次 cursor 恢复；7 个流式文本块完整保留，Provider 请求计数仍为 1，无插件副作用。PASS 日志：`%LOCALAPPDATA%/Vigil/stage2-state/phase2-ag002-reconnect-stress-round13-rerun.log`。
- 持久事件突发 1,200 个 text events，超过 Brain 256-event 窗口；旧 cursor 恢复返回一致终态和完整内容 snapshot。完整 Electron E2E 18/18 PASS：`%LOCALAPPDATA%/Vigil/stage2-state/phase2-ag002-1200-events-full-e2e-round13.log`。
- 首次压力场景执行因新夹具固定断言旧场景文本而 FAIL；源 run 已结束且数据完整。修改测试断言匹配压力场景，再次运行 PASS；两次证据均保留。没有确认新的产品缺陷。
- 仍未进行长时间持续队列积压/内存曲线测试及真实远端服务网络测试，不能从短时压力注入推断这些范围通过。


## 2026-10-20 更新：有界重连耗尽状态对照（Round 14）

- 隔离 Electron/Brain/Fake Provider 在一次 Agent chat 中被代理断开 4 次，Electron 使用上限 3 次恢复；相同 durable run 最终完成，renderer 与 Brain 均报告 succeeded。详见 Round 14 日志 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-ag003-reconnect-exhaustion-diagnostic-round14-rerun.log`。
- 标准单次 chat SSE 重连再次通过（`phase2-ag003-reconnect-normal-round14.log`）。测试夹具初次未对齐标记的失败日志保留，修正后重跑成功。
- 未发现有界恢复耗尽时的终态偏差；真实 Provider/代理与长时断线仍待测，AG-003 仍部分验证。


## 2026-10-20 更新：模型阶段 UI 取消（Round 15）

- 真实 renderer 对处于 generating 的 Agent run 点击取消；Brain 和 renderer 都记录 `cancelled`，composer busy 状态清除。随后放行 Fake Provider 返回 late tool call，工具未派发、未产生插件副作用、无模型 follow-up。PASS：`%LOCALAPPDATA%/Vigil/stage2-state/phase2-ag003-ui-model-cancel-round15-final.log`。
- 更改后的默认 Electron/Brain/Fake Provider E2E 18/18 PASS，含 1,200 event cursor-gap；日志 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-ag003-model-cancel-full-e2e-round15.log`。夹具错误尝试均在本机保留并于修正后重跑。
- AG-003 完整终态/长时故障矩阵仍不完整，真实 Provider 未验证；当前结论维持 P1/部分验证。


## 2026-10-20 更新：SSE SQLite 留存上限实测（Round 16）

- 真实 Electron/Brain/Fake Provider Agent run 输出 1,200 个持久 text events，event cursor 到 1204；断线后旧 cursor 收到正确 snapshot。只读检查 isolated `agent_runs.sqlite3` 发现该 run 仅保留 256 行，seq 连续为 949–1204，账本尾部包含终态 cursor。burst+恢复耗时 15.8 秒。
- Focused 与默认完整 E2E 均 PASS（完整套件 18/18）。完整套件第一次因已有 seed-history 第 8 项夹具超时，失败日志保留，随后未改断言重跑通过。详见阶段报告 Round 16。
- 长时间队列积压/内存曲线和真实代理仍未验证；不能从该短 burst 推断持续压力通过。


## 2026-10-20 更新：5,000-event SSE 压力扩展（Round 17）

- 在隔离 Electron + 本地 Brain + Fake Provider 下，将可配置 burst 上限设为 5,000，Focused 场景完成 5,000 个持久文本事件。Brain event cursor=5004；只读 SQLite 查询确认保留 256 条连续记录（seq 4749–5004）；旧 cursor 的权威 snapshot 内容完整。压力和恢复耗时 65.393 秒。报告 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-ag002-5000-events-round17.log`。
- 修改仅在 `client/tools/test-electron-brain-e2e.js`，增加有界测试参数并复用 ledger 检查；完整 E2E 随后重跑 18/18 PASS，退出码 0。报告 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-ag002-5000-burst-default-full-e2e-round17-rerun.log`。
- 证据加强短时事件窗口的完整性和有界留存；未测长时间高频积压、内存曲线或真实代理。AG-002 仍为 P1/部分验证。
