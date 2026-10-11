# Vigil Agent Center P3 验收报告

日期：2026-10-12\r\n范围：受管 Codex 会话的人工审批与用户输入；不包含自动批准、桌面接管、多 Agent 扩展、混合模式或云端路由改动。

## 结论

P3 已完成本地实现与回归验证。审批只对 Vigil 创建并登记的 `owned` 会话开放；`observed` 会话始终只读。没有自动批准或自动拒绝路径，生产 Brain 服务已停止，未推送 GitHub。

## 实际改动

- `brain/codex_adapter.py`
  - 从真实 Codex App Server 请求中提取内存态风险信息：命令、工作目录、原因、执行环境、权限/网络/策略变更、文件差异预览、用户输入问题。
  - 审批记录显式绑定 `connection`、`connection_generation`、`thread_id`、`turn_id`、`request_id`、`item_id`。
  - 新增协议准确的命令/文件审批 `accept`、`decline` 响应，以及 `item/tool/requestUserInput` 的严格选项校验和答案响应。
  - 响应路径采用 `pending → responding → responded` 一次性状态；并发重复提交、连接代次变化、断线、轮次结束、过期和非 owned 归属都会拒绝。
  - 风险信息不完整、额外权限或策略变更未能安全确认时，记录仍可查看，但 `response_enabled=false`，前端不显示提交控件。
  - 风险详情和命令/文件预览不写入 `owned.json` 或事件日志；断线时清空内存态预览。
- `client/src/js/agent-center.js`、`client/src/agent-center.css`
  - Agent Center 展示完整归属字段、风险详情、文件差异、用户输入问题和过期/已处理状态。
  - 答复控件在每次增量刷新后重新绑定，避免初始渲染后按钮失效。
  - 仅在当前连接代次、状态和风险校验均通过时显示“仅本次批准/拒绝本次请求”或用户输入提交。
- `client/tools/test-agent-center-p3-live-electron.js`
  - 使用真实 Electron 28.3.3、生产 preload/main API 代理、隔离 Brain 和真实 Codex App Server 的联调脚本。
- 回归测试：补充审批协议、精确归属、一次性并发提交、过期、用户输入和风险不完整场景。

## 协议与安全边界

本地真实 Codex CLI 为 `0.162.0-alpha.17.2`。响应结构依据本机 `codex app-server generate-json-schema --experimental` 生成的 schema：

- 命令审批和文件修改审批：`{"decision":"accept"|"decline"}`。
- 用户输入：`{"answers":{"question_id":{"answers":["选项"]}}}`，问题 ID、选项和值长度均由服务端校验。
- 不接受 `acceptForSession`、持久策略变更或未出现在当前请求中的额外字段。
- 原生 Codex 沙箱、审批策略和权限执行仍由 App Server 保持；Vigil 只提交用户明确点击产生的一次响应。

## 真实 Electron + Brain + Codex App Server 联调

联调使用独立 `VIGIL_HOME`、独立端口和隔离任务工作区，未触碰生产历史或项目。实际观察到：

1. Electron 28.3.3 通过生产 preload/main HTTP 代理连接生产 Brain HTTP handler，Brain 再连接真实 Codex App Server；连接成功，模型列表 7 个。
2. 真实任务完成事件从 App Server 经 adapter、Brain、Electron 页面显示为 `completed`，来源标记为 `realtime`。
3. 真实命令审批事件显示为 `waiting_approval`，归属字段齐全，事件方法包含 `turn/started`、`turn/completed`、`item/commandExecution/requestApproval`。
4. 该真实请求同时包含执行策略变更信息，因此安全策略将其标记为 `response_enabled=false`、`command_risk_incomplete_or_extra_permissions`，页面显示风险详情且没有答复按钮；没有提交真实批准或拒绝。

脱敏证据：

- [真实 adapter 事件](agent-center-codex-p3-evidence/live-adapter-events.json)
- [真实 Electron 结果](agent-center-codex-p3-evidence/live-electron-results.json)

事件证据只保留方法、状态、来源、事件序号和错误分类，不含原始命令、提示词、输出、凭据或工作区内容。

## 测试结果

- Python Brain 全量：**40 项通过**，包含 P1、P2、P3 adapter、HTTP、native 和 management 回归。
- Node Agent Center 状态与通知：**14 项通过**。
- P2 Electron 生产页面回归：通过，覆盖历史回放、运行中工具、工具失败计数、等待审批通知去重、断线重连、observed 只读、完成/失败/用户输入、cursor gap 和页面重新打开。
- 真实 Electron 联调：通过，结果见上方脱敏 JSON。
- JS 语法检查：`agent-center.js`、真实 Electron 联调脚本均通过。

## 当前限制

- 本轮真实任务触发的是带执行策略变更的命令审批，因此真实环境没有提交批准；可提交的命令、文件和用户输入协议路径由隔离 adapter 回归测试覆盖。
- 真实 Codex App Server 没有被强行制造文件审批或用户输入请求；对应协议校验和响应在测试中使用真实 schema 形状验证。
- 审批风险详情仅保留在当前 adapter 进程内，重启后不会恢复待答复请求；这是为了避免恢复过期或归属不明的审批。
