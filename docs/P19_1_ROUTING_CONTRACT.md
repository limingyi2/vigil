# P19.1 路由收敛：现状基线与兼容契约

## 目标与范围

本阶段先冻结已有路由行为，再决定是否移动调用入口。**不新增路由框架**，
不让任何分类器成为权限来源，不改 P18 Workflow / Checkpoint / 执行重试语义。

## 已核对调用链（dev 基线）

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

## 本次交付

`tests/test_p19_routing_contract.py` 对三个现有入口的基础分类和返回结构建立离线契约测试。
这些测试是**收敛前的保护栏**，不是已完成统一调度，也不能代替服务器 SSE/客户端真实回归。

## 后续 P19.1 实施门槛

1. 测试通过后，在一个受信任服务端入口统一路由观测字段（来源、理由、选定执行器和回退原因），不改动选择规则。
2. 分别验证 PC 普通聊天、编码工作区、local_direct、hybrid_delegate、Planner 旁路的前后行为。
3. 任一功能发生路由变化立即回退并修复；不得为了结构简化扩大工具权限。
4. P19.2 再考虑 Todo/Agent Run 生命周期衔接，尤其是未知副作用、人工核对、幂等和断点续跑。

## 不包含

不创建新 Intent Router/Task Scheduler，不修改 P18 或数据库迁移，不合并 PR，未经实测不声明全仓通过。
