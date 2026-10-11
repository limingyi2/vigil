# 第二阶段结论与建议

24 条任务现在都有确定的执行脚本、工具结果/异常事件和 Oracle；stub 运行不表示模型效果。全量 deterministic 结果只用于验证 harness 可复现和安全边界。

隔离 Brain Adapter 为每个 task-run 建立新进程、workspace、`VIGIL_HOME`、配置和会话。它只接收显式 API 配置，不导入生产配置；任务工具在隔离 worker 中被 allow-list 限制。该 Adapter 当前未被批量实验 Runner 调用，不能误触发真实模型评测。

Planner 执行授权已独立修复：请求渠道的当前工具规格 ∩ 本次 allow-list 决定执行权限，拒绝发生在 `tools.run_tool` 前。模型 prompt 中可见工具名单不具有授权效力。改动没有增加 Checker 开关，也没有改变 Planner/Checker 的业务判断。

第一轮真实模型只准备 A_direct 和 B_current，8 条 × 2 组 × 2 次。任务选择和调用预算见 `08-phase2-experiment-plan.md`。真实调用前需再次确认模型、任务清单和预算；本阶段不得执行真实评测。
