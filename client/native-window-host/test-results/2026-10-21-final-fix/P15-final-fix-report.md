# P15 Final Fix — Windows 验收记录

状态：`P15_FINAL_FIX_COMPLETE`
功能状态：`FEATURE_FROZEN_PENDING_USER_ACCEPTANCE`
验收基线：`23e302571991ac9b52e387deb5fbbff22e2d74bb`
工作分支：`feat/p15-video-window`

## 修复内容

1. **重复 START 状态回退**：`ProcessCommand(START)` 先刷新实际进程族和主 HWND。相同配置仍已托管时发送 `start-reused` 并重发真实 `attached` 状态，不创建进程；窗口已分离时进入 `attaching` 并复用现有 PID/HWND；启动中继续保持 `starting`；目标进程退出后才清理旧会话。其他配置仍有活动窗口时返回 `ERROR_BUSY`。
2. **窗口恢复失败后提前清理**：恢复操作现在返回阶段、Win32 错误及窗口是否消失。只有样式、父/所有者关系、位置、placement 和可见状态验证成功，或 HWND 已不存在，才释放恢复上下文。有效 HWND 恢复失败时保留 HWND/PID/原始窗口状态，每秒安全重试；DETACH、会话清理和 QUIT 都等待恢复完成。恢复未完成时 Electron 阻止退出，原生容器不被销毁。
3. **Electron 状态同步**：主进程将辅助进程的真实 `state` 事件转发给页面；恢复失败或辅助进程异常丢失时不假报 `idle` 或退出成功。UI 增加对应状态文本。

## 修改文件

- `client/native-window-host/native_window_host.cpp`
- `client/electron/native-window-service.js`
- `client/electron/main.js`
- `client/src/js/pages.js`
- `client/native-window-host/poc-electron/main.js`
- `client/native-window-host/test-results/2026-10-21-final-fix/` 中的定向测试和本报告

未修改 `client/src/video-workbench.css`、preload、安全开关或 GPU 配置。视频工作台上下分区保留；隔离 Electron 实测布局为上方窗口区 319 px、下方预留区 295 px。

## Windows x64 测试

环境：Windows 11 专业版 x64，系统 build 26200，Per-Monitor DPI 144。原生模块使用 VS 2022 Build Tools x64 实际编译，命令：

```powershell
cd client
.\native-window-host\build.cmd
```

构建产物：`client/native-window-host/build/Release/native-window-host.exe`
SHA-256：`D6C89E7D26EE9D911A8718C381F15BEB96E8CB0F9D8BEF256D0AD117AAB634FD`

| 检查 | 结果 | 证据 |
|---|---|---|
| CharMap 连续/快速重复 START | PASS；每个会话仅一次 `started`，重复请求复用，托管后状态没有回退到 `starting` | `evidence/lifecycle-set-parent-fault.jsonl` |
| 启动中重复 START | PASS；PoC 在 `started` 后立即发出 3 个重复 START；进程数未增加，最终仅转到 `attached` | 同上 |
| A→B→A、退出后重启 | PASS；CharMap → System Information → CharMap；最后一次 A 使用新的 PID/HWND | `evidence/lifecycle-set-parent-fault.jsonl`、`evidence/electron-integration-summary.json` |
| 分离后重附着 | PASS；PID 和 HWND 保持不变，`detached → attaching → attached` | `evidence/electron-integration-events.jsonl` |
| SetParent 恢复故障注入 | PASS；记录 Win32 5、有效 HWND 和仍指向宿主的 parent；自动重试后恢复并完成解除托管 | `evidence/lifecycle-set-parent-fault.jsonl` |
| SetWindowPos 恢复故障注入 | PASS；记录 Win32 5，保留有效 HWND；自动重试后 `restore-complete` | `evidence/lifecycle-set-position-fault.jsonl` |
| 退出期间恢复故障注入 | PASS；`shutdown-blocked` 时 Electron 和容器仍保持；恢复成功后才出现 `shutdown-complete` 和 helper exit | `evidence/quit-restore-fault.jsonl` |
| 目标软件关闭、重新启动 | PASS；窗口销毁被报告为 `window-lost`，进程退出后清理会话；后续启动得到新 PID/HWND | 生命周期事件日志 |
| Task Manager 管理员级窗口 | 兼容性未通过：被委派到外部高权限进程后，样式/`SetWindowPos` 操作返回 Win32 5。有效 HWND 和恢复上下文持续保留，没有假报成功；用户正常关闭测试窗口后 helper 清理完成。此用例未计入兼容通过数 | `evidence/taskmgr-elevated-compatibility.jsonl` |
| Electron 主进程 + 页面桥接 | PASS；真实隔离 Vigil 完成 A→B→A、重复 START、UI 状态同步和 A 分离/重附着；退出码 0 | `evidence/electron-integration-summary.json`、`evidence/electron-integration-events.jsonl` |
| Electron service 桥接单测 | PASS；一个 helper、两个 START 命令，状态原样转发；`shutdown-blocked` 时 Promise 未完成，收到 `shutdown-complete` 才安全完成 | `evidence/electron-bridge-result.json` |
| 残留进程 | PASS；测试关闭后未发现本轮的 Electron、native-window-host、CharMap、System Information、Task Manager 或 OSK 进程 | `Get-CimInstance Win32_Process` 检查 |

定向回归运行命令：

```powershell
$env:P15_TEST_HELPER_PATH = (Resolve-Path '.\native-window-host\build\Release\native-window-host.exe').Path
$env:P15_TEST_SECOND_EXE = 'C:\Windows\System32\msinfo32.exe'
$env:P15_TEST_FAULT_STAGE = 'set-parent' # 再设为 set-position 运行第二次
node .\native-window-host\test-results\2026-10-21-final-fix\run-native-regression.cjs lifecycle-msinfo-set-parent
node .\native-window-host\test-results\2026-10-21-final-fix\run-native-regression.cjs lifecycle-msinfo
node .\native-window-host\test-results\2026-10-21-final-fix\run-native-regression.cjs quit-fault
node .\native-window-host\test-results\2026-10-21-final-fix\verify-electron-bridge.cjs
node .\native-window-host\test-results\2026-10-21-final-fix\verify-electron-integration.cjs
```

JS 静态语法检查和 `git diff --check` 均通过。Electron 主进程隔离测试使用独立 `VIGIL_USER_DATA`；没有启动随包 Brain。用户关闭 Task Manager 后重新确认测试进程无残留。

## 兼容性与待用户测试

- 剪映免安装版本轮不可用：剪映播放、时间轴、导入对话框、菜单/模态弹窗、工程保存与再次加载均为 `WAITING_USER_TEST`。CharMap/System Information 通过不能替代剪映验收。
- 管理员级 Task Manager 返回 Win32 5，窗口最终回到独立顶层但无法通过本进程验证原始位置。恢复上下文和宿主因此持续保留，安全性符合本轮恢复失败要求；该权限级别的软件当前不保证托管成功。
- 之前的 Microsoft Store Notepad 进程有窗口委派/重建行为，未作为本轮 A/B 通过对象。
- 不声明兼容所有 EXE。

## CI、提交与冻结

GitHub Actions `tests` 工作流将在推送后验证；远端状态与最终提交 SHA 会在完成摘要中记录。不会合并 `dev`/`master` 或发布正式版。
