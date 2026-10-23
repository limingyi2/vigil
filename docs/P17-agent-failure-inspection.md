# P17 任务失败自检与诊断一致性

## 基线与范围

基于 PR #24 的 `a762afc88ed0186c32a4edb0b8007cf7ae8cbfe4`，保留参数分片计数、finish_reason、输出上限、usage 与解析位置等内容无关诊断。
依赖 #24；不依赖 #25（recap 退役），也不引入其源码修改。未修改 Harness、Todo、Checkpoint、人设、检索和主库既有终态/事件语义。

## 查询边界

- `agent_run_inspect({run_id?})` 由 `tools.invoke_spec` 提供。省略编号读取本会话最近失败任务。
- owner 与 conversation_id 取自 Agent 执行器构造的 Turn；不是模型参数。P17 HTTP 入口原有管理员权限校验保留。没有该上下文的普通工具调用拒绝查询。
- 独立 SQLite URI `mode=ro` 连接加 `query_only`；不使用 `listing/snapshot/events`，因为它们可能触发启动修复。不调用存储初始化、不创建缺失主库。
- 查询同时校验 owner 和 conversation_id；不合法参数、无权限、未知编号、缺库均返回相同 `inaccessible`。
- 输出仅含编号、状态、阶段、时间、步骤类型/名称、失败分类、派发/回执状态和证据计数。没有任务原文、模型文本、参数、输出或异常详情。
- `calls` 记录是派发意图，不是外部副作用完成证明。是否执行依据既有 result_state/回执；结果不明返回 null 派发状态，不能据此自动重试。步骤最多返回尾部 120 项，截断或证据缺失显式标记 partial。
- failure_brief 优先展示失败末尾步骤；模型校验失败显示模型步骤，不能伪称工具执行失败。

## 诊断写入与分类

主库事务收集诊断元数据，commit 成功、连接关闭后才交给既有异步诊断队列；回滚不发诊断。诊断投影、入队或后台写入失败不反向修改主库，也不重派工具。

| 证据 | 分类 | 是否执行工具 |
| --- | --- | --- |
| 工具参数校验失败 | not_dispatched | false |
| 回执 not_dispatched | not_dispatched | false |
| 回执 denied | permission_denied | false |
| 回执 failed | execution_failed | true |
| unknown / 适配器异常 | result_unknown | unknown |
| 模型请求失败 | model_request_failed | 不构成工具派发证据 |

不完整 `local_file_read` 参数的三方对应：主库 model step 的 invalid_tool → `tool_validation_failed` 诊断的 not_dispatched → 自检工具的 not_dispatched。以 run_id、step_id 关联。不会生成 calls 行或虚假 tool_calls 诊断；重试次数保持既有值。真实工具回执继续使用原有单次诊断写入，不增加第二套回执。

模型请求记录包括成功/失败、耗时、可获得的 token 计数和 run/step 关联；Coding 请求只补计数/耗时，不改 Harness 协议。失败终态补 `run_failed`，详情使用同一安全投影。诊断为 best-effort，不是跨库事务性审计；崩溃、队列满或磁盘故障仍可造成诊断缺失，主库是任务事实来源。

## 诊断队列快照

`diagnostic_queue_status({})` 使用相同受信任 Agent 上下文门控。`diag.queue_snapshot()` 只读内存，返回 queue_size、busy（在途批次数）、dropped、write_failures、cleanup_failures、last_error_class。
读取不会启动写线程、flush、cleanup 或访问数据库。明确返回 current_process / resets_on_restart；不能用重启后的计数推断历史是否丢失。旧的 `stats()` 仍保留原有行为，新工具不调用它。

## 验收方法与限制

新增 `tests/test_agent_failure_inspection.py`，使用临时 VIGIL_HOME、临时双库和假模型，覆盖 owner/会话隔离、不可访问一致性、无迁移/修复、敏感内容排除、实际 Agent 自检、参数未派发一致性、四类回执、模型失败、诊断故障、事务回滚及无副作用计数。

已验证：
- 新增测试及 failure_brief 回归：26 项通过。
- Agent、HTTP、工具协议、Coding Harness、模型适配相关回归通过。
- `python -m compileall -q brain pet tests`、`git diff --check` 通过。

全仓 pytest 与 GitHub CI 的最终结果见 PR 验收记录。

仍待真实 Windows 验证：生产侧打包 Brain/客户端的鉴权上下文传递；真实 MiMo 失败时三方元数据关联；真实磁盘满/锁库/进程崩溃下队列计数和主库恢复。模拟测试不声称覆盖这些现场条件。本轮没有调用真实模型、读取或修改生产数据库。
