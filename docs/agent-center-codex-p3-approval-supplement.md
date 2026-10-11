# P3 真实审批闭环补充记录

日期：2026-10-12  
范围：仅验证真实 Codex App Server 是否能产生可安全答复的普通审批请求；未修改产品代码、审批策略、沙箱权限或连接配置。

## 测试环境

- 独立 `VIGIL_HOME` 和独立 Agent Center 工作区。
- Electron 28.3.3，生产 preload/main，真实 Brain HTTP handler。
- 真实 Codex App Server：Codex 0.162.0-alpha.17.2。
- 未使用自动批准，也未提交任何真实批准或拒绝响应。

## 结果

### 无害命令尝试

使用隔离任务请求执行无副作用的 `echo` 命令。Codex 直接生成了 `item/started` → `item/completed`，退出码为 0，没有产生 `item/commandExecution/requestApproval`。

因此该请求没有人工审批按钮，不能作为批准闭环测试。

### 第二次审批请求尝试

继续尝试触发隔离工作区内的普通文件写入请求。当前版本没有得到可安全答复的待审批请求；测试在等待审批状态时停止。没有降低 `readOnly` 沙箱、没有放宽 `approvalPolicy`，也没有向 App Server 伪造审批事件。

## 验收结论

| 项目 | 结果 |
| --- | --- |
| Electron 显示真实安全审批风险和按钮 | 未验证：未产生满足条件的真实审批请求 |
| 人工批准一次并恢复完成 | 未验证 |
| 人工拒绝并阻止命令执行 | 未验证 |
| 重复响应、过期响应、旧连接响应 | P3 隔离回归已验证；本次真实闭环未触发可答复请求 |
| 是否绕过策略或自动批准 | 否 |

当前 Codex 版本在本隔离配置下要么自动执行无害命令，要么在需要升级权限/策略时生成不可安全答复的请求。根据验收要求，本次不强行绕过，真实人工批准/拒绝闭环暂记为未验证。

脱敏摘要：[live-approval-loop-summary.json](agent-center-codex-p3-evidence/live-approval-loop-summary.json)

