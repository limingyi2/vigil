# Vigil P10 验收记录

基线：`origin/dev` / `ca793d5`，独立 worktree `codex/p10-harness-upgrade`。

## P10.1 发送、暂停与继续

- 证据：发送状态由 run 台账和 SSE 终态驱动；新增 `/api/agent/runs/active` 查询，run 保存 `conversation_id`。
- 变更：刷新/切换会话恢复 queued/running 为暂停；迟到事件按 run ID、请求 ID、序号过滤；断流查询实际任务状态，running 不释放输入框。
- 验收：`tests/test_p10_harness.py::test_active_run_is_session_scoped_and_survives_readback`、P2 Agent HTTP/run 全部通过。
- 状态：PASS（浏览器连续 5 轮需 Electron 依赖后再跑）。

## P10.2 跨模型适配

- Provider 能力报告包含上下文/输出预算、工具协议、usage、reasoning 策略和降级原因。
- `fast/balanced/deep` 由 adapter 映射；未知网关不发送未经确认的推理字段；MiMo 和 Ollama 分别使用确认的字段。
- 验收：Provider、Native FC、P2 Agent 测试通过。
- 状态：PASS（真实云端/真实 Ollama 未在本环境联网验证）。

## P10.3 执行过程展示

- 统一 `tool` 事件包含 started/returned/error、唯一 step/call ID 和有界脱敏的参数/输出/错误。
- UI 轻量行内显示执行中/完成/失败，详情使用原生折叠元素；重复事件更新同一行。
- 状态：静态脚本检查 PASS；真实 Electron 截图 NOT RUN：worktree 缺少 `client/node_modules/electron`。

## P10.4 项目级自主审查

- `project_review_start/step/report` 建立技术栈、模块、入口、文件覆盖计划，按批次保存进度和行级证据。
- 状态：PASS（隔离 fixture 已验证 TODO 发现、进度完成和 owner 隔离）。
- 真实 worktree 只读审查：识别 JavaScript/TypeScript + Python，17 个模块，601 个源码/配置文件，分批完成；发现 40 条低优先级 deferred-work 证据，未把它们冒充为确认 Bug。
- 与独立 Codex 审查的误报/漏报对照：NOT RUN，本次没有第二个独立审查执行结果可比对。

## P10.5 修复与验证闭环

- `project_repair_plan` 只生成差异方案；`apply` 要求明确授权、项目内路径和基线哈希；`verify` 只接受外部测试证据；重复/并发变化会阻止写入。
- 状态：PASS（隔离 fixture 已覆盖成功修复、拒绝授权、基线变化保护和验证记录）。

## 回归统计

- 新增/针对性：4 passed。
- 关键既有套件：P2 Agent、Provider、Native FC、tools、run-mode、frontend invariants 全部通过。
- 全仓：2 个既有 `brain/tests/test_plugin_market_state.py` 测试失败，均断言旧的 mirror 顺序（jsdelivr 首位），与本次改动无关。
- Windows 安装包与 SHA-256：NOT RUN（未执行发布构建，未修改 master/未推送）。
