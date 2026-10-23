# P19.1 路由收敛：现状基线与兼容契约

## 目标与范围

本阶段先冻结已有路由行为，再决定是否移动调用入口。**不新增路由框架**，
不让任何分类器成为权限来源，不改 P18 Workflow / Checkpoint / 执行重试语义。

## 已核对调用链（dev 基线与 P19.1 收敛）

| 入口 | 判定 | 后续 | 边界 |
| --- | --- | --- | --- |
| `server.py` PC 消息入口 | `tools.schema_needed(text)` 和请求身份、Agent 开关 | `_new_agent_run` / 普通聊天 | 是否创建 Run，不是任务计划 |
| `server.py::_new_agent_run` | 工作区已选择 + `coding_harness.looks_like_coding_request` | 设置 `kind=coding`，走 `agent_runs.create` | 只作 coding 提示；实际工作区仍经服务端校验 |
| `agent_runs.py::_run_coding` | coding kind | `CodingHarness.run` | 执行器而不是全局意图识别 |
| `coding_harness.py` | `should_plan` | PLAN / IMPLEMENT / VERIFY | coding 内部规划，不应取代普通聊天路由 |
| `brain.py::draft_native` | `delegation.bridge.run_local_direct` | 明确授权路径本地直答，否则继续现有 FC | 只在受控的混合委派模式生效 |
| `server.py` Plan-and-Execute | `run_hybrid_delegation_direct` + `planner.plan_decision` | 委派/Planner/FC | 存在历史旁路；整合时需独立兼容验收 |
| `task_workflows.py` | 不负责自动识别意图 | 持久化 Todo / Attempt / Checkpoint | 不自动调度、不给出执行授权 |

## 固定契约

- 混合委派分类继续返回 `route/reason/workspace/kind`，路径固定为 `cloud_direct`、`local_direct`、`hybrid_delegate`。
- 没有可信授权路径、不确定和异常的情况不得自动进入本地执行。
- `looks_like_coding_request` 只是类型提示；不能靠提示文本突破工作区、owner、persona 或工具权限。
- `schema_needed` 决定挂不挂工具表，不等同于完整用户意图识别。
- CodingHarness 的 `should_plan` 只决定 coding 内部规划，不参与全局执行授权。
- 不将已有 `Agent Run` 状态机和 `P18 Workflow` 合并成一套状态机；两者通过受控关联而非双写猜测同步。
- 不承诺零工具调用、零模型调用或自动恢复，除非现有路径已经保证。

## 本轮最小收敛

- `_chat_with_tools` 的规划前委派判定会把原路由结果（含服务端内部 workspace）
  传给 `Brain.chat` → `draft_native` → `run_local_direct`。因此，混合模式下
  `local_direct`、`cloud_direct` 或不匹配的路径不再对同一条用户输入重复调用
  `delegation.router.classify`。路由关闭、非混合模式及规划前入口异常仍按原有
  安全回退工作；Planner 生成的不同子任务文本仍由 `delegate_from_text` 独立判断。
- `/api/chat` trace 与现有 trace 行新增嵌套 `route_decision` 观测对象，记录
  `execution_route`、`decision_source`、`reason`。观测对象不复制 workspace；
  不改变既有 `route`、`kind`、
  `status` 字段含义，也不把 workspace 绝对路径写进聊天 trace。
- `cloud_direct/local_direct/hybrid_delegate` 仍只描述模型/执行位置的混合模式路由，
  不能视为用户意图四分类或操作授权。当前代码没有统一且可验证的分类规则，尤其
  普通聊天与信息检索、简单执行与复杂执行的边界分散在工具门禁、记忆检索、
  CodingHarness 提示和 Planner 复杂度启发式中；本轮保留这个差距，不新增关键词分类器。

## 本次交付

`tests/test_p19_routing_contract.py` 对现有三路分类、保守回退、Planner/CodingHarness
职责分离、聊天工具门禁及同一用户输入只分类一次建立离线契约测试。它不代表统一
四类任务调度已经完成，也不能代替服务器 SSE/客户端真实回归。

## 后续接口与差距

1. 本轮只统一已有混合路由的 trace 结构与分类调用；当前协议还没有可靠的
   `task_category`、`planner_required`、`selected_executor`、`fallback_reason` 统一值。
2. 不要把 `planner.plan_decision`、`coding_harness.looks_like_coding_request`、
   `tools.schema_needed` 或记忆是否检索提升成全局意图分类器；它们职责不同且不能
   单独区分四类任务。
3. P19.2 可从 `route_decision` 的嵌套对象和 `Brain.chat(..., route_decision=...)`
   透传参数继续接入 Todo/Agent Run，但必须另行定义执行器选择与回退字段的兼容协议。
4. P19.2 再考虑 Todo/Agent Run 生命周期衔接，尤其是未知副作用、人工核对、幂等和断点续跑。

## 不包含

不创建新 Intent Router/Task Scheduler，不修改 P18 或数据库迁移，不合并 PR，未经实测不声明全仓通过。
