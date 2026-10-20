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
| Windows 候选包 / NSIS 生命周期 | 查看本阶段本机报告 | `%LOCALAPPDATA%/Vigil/stage2-state/phase2-acceptance.json`（构建来源、包清单、哈希和实际安装/启动/重启/卸载结果）；候选包不随 Git 提交或发布 |

全仓 pytest 的 19 个 skipped 保留在 `-rs` 输出，未计入通过：缺少私有/本机素材、FFmpeg，以及当前 Windows runner 不具备部分 junction/symlink/mkfifo 创建权限。早期 E2E 测试夹具失败和异步断言竞态均保留在 `improvement-log.md`；修正夹具契约和等待 session-list 后，最终连续两次通过。

## P1 风险状态

| ID | 优先级 | 证据状态 | 剩余验收 |
|---|---:|---|---|
| AG-002 SSE 重连、游标缺口、快照与队列压力 | P1 | 部分验证：Electron 流式 delta/terminal 与 Brain crash interruption 实测；Node cursor gap/snapshot 分支通过 | 网络断流后的 Electron 自动重连、Brain cursor gap 一致性、有限队列持续压力未完整验证 |
| AG-003 Electron/Brain 状态一致性 | P1 | 部分验证：成功工作流、工具失败显示、会话及 Brain/Electron 重启、进程断开后 UI 解锁 | 长任务取消/超时、窗口退出以及完成/失败/取消/重启的持久状态矩阵未完整验证 |
| AG-005 不确定请求及非幂等副作用 | P1 | 待验证：Provider/Agent contract 与工具去重自动化通过 | 仍需 Fake Provider 在请求发出后丢响应、模拟重复请求，并对非幂等副作用计数的端到端测试 |
| AG-008 安装包清单及用户数据保护 | P1 | 以本机 candidate JSON 报告中的 Windows lifecycle 结果为准 | 旧版本原位升级和故障注入回滚须独立确认；纯 Node rollback snapshot 测试不等价于 NSIS 升级/回滚 |

以上风险均没有因不完整覆盖而标为完成。本阶段没有修改 Vigil 业务逻辑；没有修改 `master`、合并 PR、发布正式版本或覆盖正式安装。

## 报告和隐私

pytest 报告、任务状态、比较结果、E2E 原始日志和候选包均在 `%LOCALAPPDATA%/Vigil/stage2-state/` 及其子目录，且位于仓库之外。Git 提交不含本机报告、凭据、会话或构建产物。最终 Git SHA、远端核验和 PR 状态由 PR 页面及交付消息提供。
