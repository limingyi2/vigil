# Agent Workspace UI 交接

分支：`feature/agent-workspace-ui`

## 本轮完成

- Agent Center 首页改为 Agent 工作室总览：Codex 实时卡片、DeepSeek Harness 和 Work Buddy 待接入卡片、最近任务摘要、添加 Agent 入口。
- Codex 详情按需打开；完整任务、observed 历史、事件和原生审批只在详情视图加载。
- Codex 详情继续复用原有 Agent Center 状态机和审批身份字段：`connection`、`generation`、`thread`、`turn`、`request`、`item`，并保留 `response_enabled`、stale、owned/observed 限制。
- Brain 缺少 Agent Center 路由时显示“当前 Brain 版本不支持 Agent Center，请检查版本”；Token/权限、App Server 不可用和过期状态分别显示。
- 未接入 Agent 只显示“待接入”，没有在线、任务、审批或控制能力。

## 验证

- Agent Center state：12 项通过。
- 桌面通知：3 项通过。
- Windows Electron 隔离检查：通过，Electron 28.3.3。
- Electron 检查包含：3 张 Agent 卡片、1 个真实 Codex、2 个待接入卡片、详情打开、owned/observed、断连重连、审批安全条件、主题和壁纸兼容。
- 证据：`docs/agent-center-codex-p2-evidence/electron-results.json`、`workspace-overview-light.png` 及同目录截图。

## 尚未接入

仓库当前没有可信的 DeepSeek Harness 或 Work Buddy Agent Adapter，因此本轮不伪造其状态。后续接入应提供状态、任务、事件、身份和权限契约后，再替换待接入卡片。
