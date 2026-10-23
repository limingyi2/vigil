# P18.1：Todo + Checkpoint 持久化基础

## 基线与实际代码审查

开发分支：`codex/p18-1-todo-checkpoint`。基线：PR #29 合并后的
`origin/dev`，`4e5b18f40470dac42d9bb9e27d75c9b5d2112ecc`。

本文件是当前实现说明。`VIGIL_TASK_RECOVERY_STAGE1.md` 是 2026-10-15 的设计资料，
其中“本文档是唯一判据”“当前没有持久化任务实体”“工具结果只在调用栈”等描述
已经不能代表现在的代码；旧文件保留作历史，本实现不替换现有 Agent Run / Harness。

| 当前代码 | 重新核对的事实 | P18.1 处理 |
| --- | --- | --- |
| `agent_runs.create / resume / interject` | 已有持久化 Run；继续与插话派生新 run_id | workflow_id 独立稳定，显式关联一个或多个 Run，不调用这些执行接口 |
| `storage.db` v5/v6、`agent_runs._db_file` | P17 已将旧库收编；有效表为主库 `runs/calls/call_ids/events`，不是 v1 的 `agent_runs/agent_calls/agent_events` | v7 只增加 `task_*` 表，外键关联当前 `runs` |
| `agent_runs._db / _ensure_repaired` | 首次使用会将旧进程在途 Run 改为 interrupted、dispatched call 改 unknown | P18 查询直接读主库，不调用这些带修复的接口，不改变 Run 状态 |
| `agent_runs._tool` | 工具派发前提交 `calls` 意图；结果之后另事务保存 result_state、回执与结果 | 保留现有协议；P18 意图不能证明外部操作完成 |
| `agent_runs._event / _save / _flush_pending` | 事件序号、快照与流式批次已有持久化机制 | P18 检查点引用 Run 状态、事件游标与调用记录，不复制流式正文 |
| `agent_runs._run_coding`、`coding_harness` | Harness 已有 plan、phase、tests、files、repair、diff 和 resume_state | 新 Todo 版本不是 Harness plan；本轮不导入、不自动转译、不改变 Harness |
| `agent_runs._checkpoint` | 是既有上下文压缩/恢复摘要机制 | 新 Checkpoint Engine 是主库事务快照，独立命名于 task_workflows 模块 |
| `agent_inspection`、P17 诊断 | 检查工具以 owner/conversation 约束；诊断在主事务提交后 best effort 异步写 | 不改变自检工具、诊断分类或日志表；P18 不依赖诊断库 |
| `server._conversation_context / _display_owner` | 会话/工作区 owner 和 Run owner 使用不同的身份派生规则 | Scope 分别派生两种 owner，不能将会话 owner 直接拿来查询 Run |

没有新增 HTTP、模型工具、UI、自动调度、启动扫描或恢复动作；普通聊天 FC/ReAct、
Creator、插件系统和已有 Agent Run 源码均未修改。

## 数据结构与事务

```text
受信任 Scope（身份、人设、会话、工作区）
  └─ workflow_id ── plan_version ── Todo 步骤、依赖、完成条件、状态、证据
       ├─ run_id 关联 ── 现有 runs / calls / events（主库事实）
       ├─ attempt_id ── 工具、effect、dispatch_key、状态、结果引用、证据
       └─ revision ── 不可变 checkpoint + 幂等 request_id
```

主库新增七张表：

| 表 | 职责与约束 |
| --- | --- |
| `task_workflows` | 稳定身份、完整归属、创建时工作区规范化路径键、当前版本与 revision |
| `task_plans` | `(workflow_id, plan_version)` 唯一，冻结计划定义 |
| `task_steps` | 版本内 step_id 唯一；状态和完成证据；外键指向计划 |
| `task_run_links` | workflow 到既有 Run 的关联；一个 Run 只允许归属于一个 workflow |
| `task_attempts` | 唯一 attempt_id；跨计划版本唯一 `(workflow_id, dispatch_key)`；结果引用与证据 |
| `task_checkpoints` | `(workflow_id, revision)` 唯一；原因、时间和不可变 JSON 快照 |
| `task_requests` | 完整归属内 request_id 唯一；请求指纹与原始 checkpoint 响应 |

计划是最多 200 步的 DAG；拒绝重复步骤、缺失依赖和环。步骤包含稳定的 step_id、
title、kind（model/tool/finalize）、depends_on、completion_condition。进度按当前
版本的 completed / total 计算，失败、暂停、取消不算完成。

每个成功变更使用现有 `storage.db.write_once('main')`、主库写锁和
`BEGIN IMMEDIATE`：校验归属 → 查幂等请求 → 校验 revision → 状态变更 → 创建检查点
→ 写入请求响应引用 → 一次提交。任一步失败整笔回滚。数据库约束再次保护状态集合、
版本范围、唯一键和外键。读取使用短连接和显式读事务，保持多个 SELECT 的一致视图。

重复 request_id + 相同规范化请求体返回原始检查点，即使后续状态已推进；不同请求体
拒绝。所有变更需要 expected_revision，新 request_id 携带旧 revision 会冲突；
跨线程/进程写入由 SQLite 写事务串行化。幂等请求不会额外增加版本、步骤、attempt 或 checkpoint。

计划只能在 ready/paused/failed 且无在途或未知 attempt、无 running step 时调整。
新版本从 pending 开始；旧版本及证据仍能查询，旧检查点永远不改。
不自动将“相同标题”的旧完成状态迁移到新定义，避免完成条件变化后误计完成。
旧执行派发键在整个 workflow 中仍受唯一约束，换 plan_version/run_id 不会解锁旧键。

## 状态与证据

| 对象 | 状态与主要出口 |
| --- | --- |
| workflow | ready → active/paused/cancelled；active → paused/failed/cancelled/completed；paused/failed → ready/cancelled；needs_review → paused/cancelled；completed/cancelled 不再执行 |
| Todo step | pending → running/cancelled；running → completed/paused/failed/cancelled；paused/failed → pending/cancelled；needs_review → paused/cancelled |
| attempt | prepared → dispatched/not_dispatched/denied；dispatched → returned/failed/unknown |

`prepared` 表示已持久化意图但未登记派发；`dispatched` 表示可能已生效，绝不表示成功。
`returned` 必须有结果引用与核验证据；返回结果不会自动完成 Todo。
tool 步骤完成还要求最后一次 attempt 为 returned，且提供独立的完成条件核验证据。
模型与收尾步骤也必须有完成证据。workflow 完成要求当前版本所有步骤完成、无未决执行。

证据结构：`{ref, digest, verified_by}`，digest 为 SHA-256。服务校验结构并持久化；
**不读取产物、不替调用方验证文件内容或完成条件**。这些证据必须由后续受信任执行器
或人工核对流程生成，不能直接信任模型填写的“已完成”。引用必须指向持久的主库或
受控产物；不得使用诊断日志作为唯一结果证明。

未知 attempt 将步骤置为 needs_review，并将非终态 workflow 置为 needs_review；取消后
的未知结果仍保留在 attempt / requires_review，取消事实不会被改成成功。
未知状态阻止新尝试、计划调整和完成步骤。
只有显式 `reconcile=True` 且附带核对证据，才能将 unknown 核对为 returned、failed
或 not_dispatched；这只是记录人工结论，不执行、不重试、不自动恢复。
核对后仍需明确解除步骤和 workflow 的暂停/核对状态。

读取发现 P18 或既有 Run 的 dispatched/unknown call 时，返回
`requires_review=true`、`continuation=manual_review`；不推断旧执行者已退出，不改库，
不尝试重发。prepared 返回 pending_intent 预检阻断。暂停、失败、取消分别保留；
迟到结果可以更新 attempt，但不会自动把暂停/取消/失败 workflow 改成完成。

这不是外部副作用的 exactly-once 协议。dispatch_key 由后续受信任执行层按稳定操作
身份生成，不可通过更换随机键绕过幂等；本轮服务完全不派发工具。

## 身份、工作区与权限

`Scope(identity, persona, conversation_id, workspace_id='', src='pc')` 必须来自已认证
服务端上下文。identity 对应当前 `_who`，绝不能由模型/请求体自行声明。
兼容服务器主令牌的 `('brain', '')` 身份；拒绝匿名身份，以及会被现有 owner 派生规则
截断到 128 字符而丢失人设/会话信息的 Scope。

- 会话/工作区 owner：`display_turns.owner(identity, 'pc', 'conversations:' + persona)`。
- Run owner：`display_turns.owner(identity, 'pc', persona + ':' + conversation_id)`。
- 每次查询、变更、检查点历史读取和幂等重放都重新验证 conversations 的完整归属与
  workspace_id，工作区的完整归属与规范化 path/key，以及创建时冻结的 workspace_key。
- Run 关联还核对 Run owner、conversation_id、src、workspace_root；每次访问已关联
  workflow 也重新验证这些 Run。跨用户、人设、会话、工作区返回相同 inaccessible。
- 无工作区的普通会话可以持久化 Todo，但不获得文件访问权。会话被删除、移出工作区、
  工作区被删除/换根时旧任务拒绝访问；本轮没有重新归属/接管接口。
- 不自动接受旧的未绑定人设 Run owner、main/welcome、空人设或历史档案 owner fallback。
  旧数据保留且继续走 P17 原有接口；未来如需接入必须有明确迁移/归属证明。

`assess_continuation(scope, workflow_id, permission_check)` 只做只读预检：重新校验
身份与工作区、阻断在途/未知/停止状态、按已记录的 tool/effect 调用当前权限检查器，
异常或非 True 都拒绝；权限计算后再次检查归属和 revision，避免使用过期观察。
返回 eligible 是状态预检结果，始终 `dispatch_authorized=false`、
`automatically_resumed=false`。没有历史工具时不会猜测未来工具所需权限。
下一阶段实际派发每个新工具仍必须重新校验 schema、当前工具白名单、工作区真实路径、
文件/命令权限与审批，不能把 checkpoint 或预检结果当成授权令牌。

## 服务接口与下一阶段 Run 接入点

`brain/task_workflows.py` 提供：

- `create / get / listing / update_plan`：任务与计划历史。
- `transition / step_status`：显式状态机与完成证据。
- `link_run`：关联既有 Run；不创建、不恢复、不修改 Run。
- `prepare_attempt / attempt_status`：意图、结果、未知与人工核对。
- `checkpoint / checkpoints`：检查点创建、完整历史及按 revision 增量查询。
- `assess_continuation`：当前归属、状态与工具权限预检，无执行效果。

一般调用示例（仅持久化）：

```python
scope = Scope(authenticated_identity, persona, conversation_id, workspace_id)
cp = create(scope, request_id, goal, steps)
wid, revision = cp['workflow_id'], cp['revision']
cp = link_run(scope, wid, link_request_id, revision, existing_run_id)
state = get(scope, wid, plan_version=1)
history = checkpoints(scope, wid, after_revision=0)
```

后续接线位置已经核对，但本轮不调用：

1. `server._new_agent_run` 完成认证、会话与工作区解析之后构造 Scope，创建/关联 workflow。
   resume/interject 的新 Run 显式关联原 workflow，禁止从文本推断归属。
2. `_tool` 在现有派发意图事务处登记 attempt/检查点，再派发；结果事务处保存回执、引用、
   unknown 与检查点。必须复用既有主库连接，不能在 `_db()` 的写事务里另开一个写连接。
3. `checkpoint(..., connection=conn)` 已支持调用方的活动主库事务：验证主库路径、
   foreign_keys 与活动事务，用 SAVEPOINT 保证本次检查点失败不会留下半条状态。
   不提交/关闭调用方连接，恢复 row_factory；检查点只能在外层 commit 成功后对外发布。
   后续若把 Todo/attempt 变更也接入同一 Run 事务，应复用这里的事务边界实现，不能双写后
   宣称两笔提交原子。本轮仅提供检查点共事务入口，没有改现有 Run 的提交顺序。
4. `_run_coding.persist` 的 coding_plan/coding_step/coding_test/coding_file 是后续证据映射点；
   Harness 仍拥有其计划和执行循环。必须先定义语义映射，再接 Todo 完成条件验证。
5. 真正恢复执行前必须确认旧执行者已退出/被隔离，核对未知副作用，并重新检查权限和预算。
   当前没有 lease/fencing、自动恢复、自动尝试或完整调度器。

## 数据库迁移与回退

主库 schema_version 6 → 7。沿用现有 `_migrate` 的单步事务，新建七张表及一个归属索引；
不改、不重命名、不删除旧表，不重搬旧文件，不更新任何既有会话、消息、记忆、历史或 Run。
诊断库无新迁移，版本仍为 4；缺失、删除或不可用不影响 P18。

应用初始化主库时自动执行迁移，重复启动不会重复建表。升级前可沿用现有主库在线备份 API。
如需回退应用，旧代码会忽略新增表；保留 v7 表与版本记录，不做破坏性降级。
不要删除 task_* 表来“回退”，否则会丢失新任务事实；正式回滚应恢复升级前一致性备份，
或保留新增数据供再次升级读取。未提供清理/删除接口。

## 验证证据

测试文件：`tests/test_p18_workflows.py`；仅使用临时目录和假数据，不访问生产数据库，
不请求真实模型，不派发真实文件/删除/命令工具。

专项覆盖：

- 新进程打开同一 SQLite 文件，逐字段比对 Todo、两版计划、完成证据、在途 attempt
  和完整 checkpoint 历史；保留人工核对结果，不执行恢复动作。
- 重复创建/变更/attempt 请求、并发重复请求、不同 payload 冲突、revision 并发冲突。
- 用户、人设、会话、工作区隔离；同用户另一工作区/会话也无法读取；归属变更后拒绝旧访问。
- 依赖、计划环、完成条件、进度、未知结果、无核验证据不能完成。
- 写入 snapshot、checkpoint、request 失败时回滚；attempt 新建和返回结果失败时也整笔回滚。
- 调用方事务中的 Run 调用记录与 checkpoint 一起提交/回滚；SAVEPOINT 失败不提交外层；
  无事务或诊断库连接被拒绝。
- write/delete/command/other/read 的未知结果都需人工核对；暂停、失败、取消后迟到回执
  不改终态；派发键跨版本保留。
- 当前权限拒绝/检查器异常/权限计算期间 revision 改变时预检拒绝。
- v6 → v7 迁移逐行比对旧表，诊断 schema 不变；实际 P17 Agent Run 和诊断写入兼容；
  删除诊断库后任务及检查点仍可读。

运行记录及最终测试统计见同 PR 的 `VIGIL_P18_1_TEST_EVIDENCE.md`。
模拟故障与重开进程覆盖的是本轮持久化契约，不声称已验证断电、真实磁盘满或外部工具 exactly-once。
