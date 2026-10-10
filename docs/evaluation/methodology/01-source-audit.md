# 01 源码审计

审计对象：当前工作树 `<REPO>`，HEAD `4e3b62dd8d05e009b33e7bd3966f9a50d150e2eb`，Python `3.13.15`。未解压或以压缩包覆盖工作树；压缩包只含旧版任务书。工作树已有其他未提交改动，本次没有改动其内容。

## 真实调用链

1. `brain/server.py::_chat_with_tools`（约 398 行）负责每轮本地 Brain 请求。其 `_once` 在聊天锁内调用 `Brain.chat`，并从 Brain 对象复制 trace。
2. 规划判断在约 435–455 行：`planner.plan_decision(text)` 给出启发式决策和原因；`planner.enabled()` 检查配置；关闭时 trace 记录 `plan=off`。只有两者允许时才调用 `planner.plan(text)`。
3. `brain/planner.py::plan`（341 行）通过 `_ask_flash` 请求配置中的规划模型并解析最多 5 个步骤。模型/API/解析失败返回空值；server 将空计划记录为 `plan=empty` 并回退 ReAct。异常记录 `plan=error` 后同样回退。
4. 非空步骤调用 `planner.execute_plan`（server 459 行；实现 370 行），逐步调用 `tools.run_tool`。随后主模型以 `no_tools=True` 生成回答，防止同一工具步骤再次由原生 FC 执行。
5. server 469 行调用 `planner.verify_result`（实现 443 行）。无工具步骤，或工具步骤不超过 2 且工具种类不超过 1 时跳过；检查模型不可用时也跳过。若有反馈，主模型最多修正一次（server 472–477 行）。
6. 规划分支未进入、空计划或失败后，普通 ReAct 经 `_once(text)` 调用 `Brain.chat`，由 `brain/brain.py` 内部原生 function-calling 循环及 `tools.py::run_tool` 执行工具。

普通工具链的边界：`brain/tools.py::all_specs`（约 298 行）在工具表出口检查 `features.tools`，按渠道过滤内置/插件工具；普通原生 FC 在 `brain/brain.py::draft_native` 约 873 行请求该工具表，并在执行时传当前 `src` 到 `tools.run_tool`（约 434–441 行）。`tools.run_tool`（94 行）先执行内置工具，否则转 `plugin_host.run_tool`；插件执行会检查插件 feature。Planner 路径不同：`planner._available_tools_text`（184–205 行）用 `all_specs(src="app")` 构造提示，但 `planner.execute_plan`（370–390 行）直接 `tools.run_tool(tool,arg)`，没有传 `src`，也未在执行前核对 `all_specs`/任务级 `allowed_tools`。此外，工具总开关由 `all_specs` 的工具表控制，`tools.run_tool` 对内置工具本身没有同等的总开关检查。因此不能把模型提示里的工具清单当作授权层；本 Harness stub 按任务脚本执行并做工作区路径检查，不调用生产工具层。这是评测隔离的重要原因，第一阶段没有修改该业务行为。

真实外部入口是 `brain/server.py` 的 HTTP `POST /api/chat` handler（约 1514 行起）；它调用 `ask_with_retry`（约 1687 行），再到 `_chat_with_tools`（约 664 行）。`ask_with_retry` 接受 `src`/渠道和 `dry_run`，默认渠道会影响 persona、历史及记忆区；dry_run 的行为还需按 handler 请求字段确认。直接测生产服务还会经过会话历史、共享锁、渠道路由与 API 重试，因此本阶段不调用此接口。

## 可观测字段和开关

`planner.enabled()` 与 `planner.plan_decision()` 是独立可读状态，但关闭 `plan_and_execute.enabled` 会同时关闭规划与检查器。现有 trace 在成功路径包含 `plan_steps`, `revised`, `plan_final_no_tools`, `plan_ms`, `plan_calls`, `tool`；关闭/空/异常分别有 `plan=off|empty|error` 和原因/调用信息。原生 FC 工具名在 `_once` 结束时加入 trace。`planner.stats()` 另有 decided/would/ok/empty/error/revised、步数、耗时与额外调用总数。

状态区分：

- planner 决策命中：`plan_decision` 为 true。
- 规划请求成功解析出有效步骤：`plan()` 返回非空列表。
- 执行规划步骤：`execute_plan()` 对其中非空工具逐项 `run_tool`。
- Checker 被调用：任务进入成功计划路径且满足复杂度门槛，且实际请求 `_ask_flash`；生产 trace 未提供独立 `checker_invoked` 字段。
- 触发修正：`verify_result` 返回反馈，随后 `revised=true`。
- 回退 ReAct：未进入规划、空计划或异常后执行 `_once(text)`；trace 对后三者分别记录状态。

`plan_calls` 是路径内计划的额外模型调用计数，包含 planner、主模型收尾、checker、revision；它不能分离 provider/token/费用。`planner_model` 默认可为便宜的 flash 模型，主模型来自 Brain 配置；成本统计必须分别按模型/provider 采集。当前 smoke 不读 API key，也不连任何 provider。

## 组别可行性

- A / direct：可以通过 `plan_and_execute.enabled=false` 走 ReAct，但这属于配置开关，需隔离配置副本；工具权限仍由 `tools.all_specs` 和工具实现决定。
- B_current：开启时按启发式和配置进入当前 Planner + 条件 Checker；不是每个 enabled 请求都会进入 Planner。
- 纯 B（planner without verifier）：当前不可通过独立开关表达。
- C：`not_available`。Checker 检查同一个 enabled 开关；本阶段没有改生产代码来添加开关。

本目录 stub 的 A/B trace 是编排模拟值，未调用生产的 `planner`、Brain 或真实工具。它们不能作为 A/B 模型效果结果。
