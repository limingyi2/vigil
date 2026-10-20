# Vigil 第二阶段真实运行环境验收

日期：2026-10-20（Asia/Shanghai）
基线分支：`codex/vigil-agent-improvement-foundation`
基线提交：`aeacf166ea1478b830e6dfb9a0d9d7b50a6f21b9`
PR：[limingyi2/vigil#10](https://github.com/limingyi2/vigil/pull/10)

## 隔离环境

- Windows 10 Pro x64，build 26300。
- Node.js 24.19.0 与 npm 11.17.0 从官方 Windows x64 portable archive 安装到 `%LOCALAPPDATA%/Vigil/stage2-toolchain/`；归档 SHA-256 已校验为 `57f71ab3652e797d84acddc79c81cc9ff1c6ddb2a1974cdb83f00fee9bff4c73`。
- Electron 28.3.3 与 JS 依赖由 `npm ci --foreground-scripts --no-audit --no-fund` 安装在 worktree 忽略的 `client/node_modules/`；npm cache 与 Electron cache 在 `%LOCALAPPDATA%/Vigil/stage2-state/`。
- CPython 3.12.14、Brain requirements、开发 requirements 与 PyInstaller 6.22.3 装在 `%LOCALAPPDATA%/Vigil/stage2-state/python-venv/`。
- E2E 使用 loopback Fake Provider、随机临时 token、Brain home、Electron profile 和插件目录；未读取真实配置、真实密钥、真实聊天或用户工作区。

## 实际执行结果

| 验收项 | 结果 | 证据 |
|---|---|---|
| 清单、CLI / 评测器 | PASS | `agent_eval.py --help/list/validate/dry-run`；清单 14 自动案例、4 suites、10 test sets、10 behavior scenarios |
| Regression | PASS | `run-20261020T104957-72a4dc41.json`：12 案例 PASS，240 passed / 3 skipped |
| Security | PASS | `run-20261020T105048-084cb27f.json`：1 案例 PASS，29 passed |
| 全仓 Python | PASS（有 skipped） | `run-20261020T105101-97df30a8.json`：1,388 passed / 19 skipped / 0 failed；47 subtests passed，114.01 秒 |
| Node 原生测试 | PASS | 8 个 `node:test` 文件：56 passed / 0 failed / 0 skipped |
| Electron + Brain + Fake Provider | PASS | 两次独立执行各 8/8 PASS；`%LOCALAPPDATA%/Vigil/stage2-state/phase2-electron-brain-e2e-final.log` 与 `phase2-electron-brain-e2e-stability.log` |
| 任务阻塞、恢复与对比 | PASS（合成任务） | 独立 `task-flow-*` 状态目录：OPEN → BLOCKED → IN_PROGRESS → READY_FOR_VERIFY → COMPLETED；同条件 provider-contract PASS → PASS；blocked resume 未启动命令 |
| Windows 候选包 / NSIS 生命周期 | PASS（限定验收路径） | `%LOCALAPPDATA%/Vigil/stage2-state/phase2-candidate-lifecycle-0318835.json`；初始失败报告 `phase2-acceptance.json` 与后续夹具失败日志均保留；候选包不随 Git 提交或发布 |
| 官方 1.0.2 → 候选升级、数据迁移及故障回滚 | PASS（限定验收路径） | `%LOCALAPPDATA%/Vigil/stage2-state/phase2-upgrade-rollback-0318835-retry.json`；精确恢复旧安装文件树，profile 数据保留；首次基线安装崩溃记录在 `phase2-upgrade-rollback-0318835.stdout.log` |
| 候选包内容与身份核验 | PASS | `%LOCALAPPDATA%/Vigil/stage2-state/phase2-package-inventory-0318835-final.json`；241 unpacked files、804 ASAR entries，0 个禁止内容匹配；manifest source/size/SHA 与产物相符 |

全仓 pytest 的 19 个 skipped 保留在 `-rs` 输出，未计入通过：缺少私有/本机素材、FFmpeg，以及当前 Windows runner 不具备部分 junction/symlink/mkfifo 创建权限。早期 E2E 测试夹具失败和异步断言竞态均保留在 `improvement-log.md`；修正夹具契约和等待 session-list 后，最终连续两次通过。

## P1 风险状态

| ID | 优先级 | 证据状态 | 剩余验收 |
|---|---:|---|---|
| AG-002 SSE 重连、游标缺口、快照与队列压力 | P1 | 部分验证：真实 Electron/Brain/Fake Provider 运行 300 delta；在 SSE 首事件游标后断开并重连；超过 256 事件保留窗口时收到与终态一致的 authoritative snapshot；Agent Center Electron renderer/preload fixture 覆盖断开、显式重连、连接 generation 变更和 cursor gap 重同步（25/25） | Agent Center 使用结构化 Codex fixture，不代表本地 Agent run 或真实 Codex 服务；本地 Brain/Electron 自动恢复策略、持续高压、长时间积压和网络代理异常未完整验证 |
| AG-003 Electron/Brain 状态一致性 | P1 | 部分验证：通过真实 Electron API bridge 发起取消和 1 秒 Provider 请求超时；Agent 运行期间强制退出 Electron 后 Brain 继续完成一次，重新启动后恢复同一终态；Brain 重启后不确定工具步骤为 unknown；聊天断连后 UI 解锁 | UI 控件取消、长工具取消、窗口退出时 Brain 同步失败/取消/超时矩阵和真实 Provider 终态仍未完整验证 |
| AG-005 不确定请求及非幂等副作用 | P1 | 部分验证：真实 Electron/Brain + Fake Provider + 隔离插件副作用文件；重复 request ID 不重复 Provider/tool 执行；工具已记录副作用、结果未返回时强杀 Brain，重启后状态为 interrupted/unknown 且未重放 | 仅证明本地插件 fixture 与同一 request ID 的安全恢复策略；真实外部副作用、Provider 已接收但响应丢失及跨系统幂等仍待验证 |
| AG-008 安装包清单及用户数据保护 | P1 | **指定路径已验证通过**：fresh NSIS 安装/首次启动/Brain health/Fake Provider 聊天/Electron 重启/卸载保留 70 个 profile 文件；官方 1.0.2→候选迁移、Brain 不匹配阻断、精确回滚和升级 checkpoint 故障保护均 PASS；包清单扫描 0 禁止内容匹配 | 仅证明本次隔离环境、1.0.2 基线及候选版本；其他旧版升级跨度、真实用户机器/文件系统及安装器 verbose log 未验证 |

以上风险均没有因不完整覆盖而标为完成。本阶段没有修改 Vigil 业务逻辑；没有修改 `master`、合并 PR、发布正式版本或覆盖正式安装。

### P1 运行时补充验收（2026-10-20，测试改动基于 `498448f`）

- 扩展 `client/tools/test-electron-brain-e2e.js` 的隔离 E2E，新增 Agent run 的成功/失败、request ID 去重、取消、Provider 超时、Electron 退出期间任务继续、SSE cursor-gap snapshot 及 Brain 崩溃窗口场景。命令：`node client/tools/test-electron-brain-e2e.js`，解释器通过 `VIGIL_TEST_PYTHON` 指向隔离 Python venv；最终完整结果 **15 PASS**，日志 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-p1-agent-e2e-final2.log`。
- 窗口退出用例在 run=`running` 时结束隔离 Electron 进程，释放 Fake Provider gate，让 Brain 完成一次；重新启动 Electron 后读取同一个 `succeeded` run，并以原 request ID 重试确认 Provider 调用数不增加。它验证 Brain/Electron 进程生命周期，不代表 app 对窗口关闭的产品策略已经覆盖所有终态。
- 崩溃窗口用例让插件先将非幂等副作用写入隔离 trace，再阻塞返回；终止测试自有 Brain 并以相同状态目录重启。已观察到 run=`interrupted`、工具步骤=`unknown`；相同 request ID 重试返回原 run，Fake Provider 请求数和副作用计数均不增加。
- SSE 用例运行 300 个 delta（超过后端 256 项事件窗口），在首个持久游标后断开读取，再用原 cursor 重新连接；Brain 返回包含一致终态及完整 300 字符内容的 snapshot。它不验证 Electron renderer 自动重试，因为当前客户端对 chat SSE 断流呈现 interrupted。
- 相关后端测试：`python -m pytest tests/test_p2_agent_runs.py tests/test_p2_agent_http.py -ra`，**34 passed、8 subtests passed，0 skipped**，5.12 秒；控制台记录 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-agent-backend-regression-final.log`。
- `client/tools/test-agent-center-electron.js` 使用正式 Electron renderer、preload 和隔离 App Server 协议 fixture 验证断开/显式重连、cursor gap 与 connection generation 重置、401 恢复、任务/通知状态和页面布局；**25/25 PASS，errors=0，modelApiRequests=0**。结果 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-agent-center-electron/electron-results.json`，日志 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-agent-center-electron.log`。它没有连接真实 Codex 服务或 Provider。
- 完整 E2E 的失败尝试均保留：早期两次与扩展后的 seed-history 第 8 项超时 (`phase2-p1-agent-e2e-crash.log`, `...crash-rerun.log`, `...window-exit-rerun.log`, `...final.log`)；两次在窗口退出、恢复、cursor gap 与 Brain crash 用例通过后，因随后的旧 chat 断连步骤没有发出 Provider 请求而失败 (`...window-exit.log`, `...final-rerun.log`)。同环境基线脚本完成 8 项，夹具调整后最终脚本 15/15 PASS；各失败尝试仍是失败证据，当前 E2E 有重复性限制，不能计入 PASS。
- 本轮没有修改业务实现。AG-002/003/005 状态仍为部分验证；AG-008 维持本报告前述限定安装/升级路径结论。真实 Provider、Electron UI 的自动网络重连、窗口退出期间运行状态，以及其他历史版本跨度仍未验证。

### 第二阶段最终候选复验（Round 3）

首次 fresh-install 聊天验收失败已作为真实失败证据保留在 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-candidate-lifecycle-failure-initial.json` 及对应日志目录。故障定位在 lifecycle 测试夹具：模型配置字段应为 `chat`；随后又发现冷启动时 renderer 连接/发送按钮尚未就绪的竞态。修复测试夹具后，新的隔离 Electron profile 对已安装候选执行了 sentinel 聊天，sidecar Brain health 为 ready，Fake Provider 收到请求并在界面显示回复。详细通过/失败/阻塞结果、最终 installer 哈希、包清单和升级回滚结果以 `phase2-acceptance-final.json` 为准；如果该报告或对应独立 lifecycle 报告缺失，不得把该验收项推断为通过。

指定 Windows 环境下 1.0.2 到候选的安装器升级、数据迁移与故障注入回滚已实测通过，完整证据以 `phase2-upgrade-rollback-dfda34f.json` 为准。真实 Provider 认证、限流、空响应及服务端错误因没有授权的隔离凭据仍为 BLOCKED，Fake Provider 不代表真实服务验收。

## 报告和隐私

pytest 报告、任务状态、比较结果、E2E 原始日志和候选包均在 `%LOCALAPPDATA%/Vigil/stage2-state/` 及其子目录，且位于仓库之外。Git 提交不含本机报告、凭据、会话或构建产物。最终 Git SHA、远端核验和 PR 状态由 PR 页面及交付消息提供。

## AG-002 补充闭环与全量回归（2026-10-20）

本节源代码基于干净基线 HEAD `dbfb7f5aa2cbdb5d409fc83149f1053b58f5fae2`，执行时工作树包含 AG-002 代码和测试修改；报告已保存到用户本地状态目录，不提交 Git。Windows 11 build 26300、Node 24.19.0、Electron 28.3.3、隔离 CPython 3.12.14、临时 Brain/Electron profile、Fake Provider 和插件目录；没有加载真实配置或秘密凭据。

| 范围 | 结果 | 命令/证据 |
|---|---|---|
| Electron Agent chat 单次断线恢复 | PASS | `VIGIL_E2E_SCENARIO=agent-chat-reconnect node client/tools/test-electron-brain-e2e.js`；`cuts=1,resumed=1,lastCursor=11`，同一 Agent run 与 request_id 显示 succeeded，副作用无重复；日志 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-ag002-chat-reconnect-focused-final.log` |
| 完整 Electron/Brain E2E | PASS，16/16 | `node client/tools/test-electron-brain-e2e.js`；日志 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-ag002-chat-reconnect-full-e2e.log` |
| Agent HTTP/lifecycle | PASS，34 tests + 8 subtests | `python -m pytest tests/test_p2_agent_runs.py tests/test_p2_agent_http.py -ra`；日志 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-ag002-agent-backend-final.log` |
| 自动化 regression suite | PASS，12 个案例；0 FAIL/TIMEOUT/BLOCKED、2 NOT RUN | `python scripts/agent_eval.py --state-dir <本机状态目录> run --suite regression --label phase2-ag002-runtime-regression`；报告 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-agent-eval/reports/run-20261020T120733-ccfedf90.json` |
| 全仓 Python | 聚合案例 PASS；1,388 passed、19 skipped，0 failed | `python scripts/agent_eval.py --state-dir <本机状态目录> run --suite full-local --label phase2-ag002-full-local`；报告 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-agent-eval/reports/run-20261020T120827-24a9957d.json`。19 个 skip 原样保留，不计为 PASS |
| Node 原生组件 | PASS，56/56；0 failed/skipped | `node --test <client/tools 中含 node:test 的 8 个文件>`；日志 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-ag002-node-regression.log` |
| 清单/CLI/dry-run | PASS | `agent_eval.py --help`、`validate`、`run --case agent-run-lifecycle --dry-run`、`list`；manifest 14 自动案例、4 suites、10 test sets、10 behavior scenarios |

修复前，旧客户端在代理断开后没有带 durable cursor 恢复；同场景观察 `resumed=0`，Brain run 虽已成功但 renderer 不恢复终态。`brain/server.py` 现为 Agent chat 投影事件增加持久 seq 并接受经校验的 `after` cursor；`client/electron/main.js` 只在确认 run identity 后以原 request_id 最多重试三次。`IncomingMessage.aborted` 现在 settle stream Promise，避免中断后挂起。测试仅使用本地 Fake Provider。完整 E2E 的先行失败仍保留于本机日志：seed-history 第 8 项超时（`phase2-ag002-chat-reconnect-settle-fix.log`），以及两次测试误断言 run_id 字段（`phase2-ag002-chat-reconnect-focused-keep.log`、`phase2-ag002-chat-reconnect-focused-diagnostic.log`）；页面本地记录白名单不存 run_id，测试改用 request_id 关联。最终完整 16 项回归通过。

该结果扩展了 AG-002 的单次本地 Agent chat 断线恢复证据，但不证明高压/连续多次中断或真实远端 Agent 服务。AG-002、AG-003、AG-005 保持**部分验证**；真实 Provider 认证、限流、空响应、服务端错误仍为 **BLOCKED（无授权隔离凭据）**。AG-008 此前的 NSIS 生命周期报告对应旧构建身份；当前提交候选需要在干净隔离构建目录重建并复验清单。候选成功/失败、哈希与来源清单随后写入本机最终验收 JSON；PR #10 保持 open/draft/unmerged，未提出合并建议。
