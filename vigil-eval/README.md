# Vigil Agent Baseline 评测 Harness（第一阶段）

本目录是隔离的评测实现。deterministic stub 不导入 Brain 服务、不读取运行配置里的凭据、不请求模型 API。真实 Brain Adapter 已实现为单 task-run 子进程，但要求调用方显式传入隔离 API 配置并显式确认；stub 命令不会触发它。stub 只能验证任务脚本、沙箱边界、trace 和 oracle，不能代表模型效果。

## 环境与命令

Python 3.10+，只依赖标准库；Harness 自测使用 pytest。可使用项目已有虚拟环境：

```powershell
venv\Scripts\Activate.ps1
venv\Scripts\python.exe vigil-eval/runner.py --smoke
venv\Scripts\python.exe vigil-eval/runner.py --stub-all
venv\Scripts\python.exe -m pytest -p no:cacheprovider vigil-eval/tests brain/test_planner_execution_auth.py -q
python vigil-eval/report.py --latest
```

运行结果按时间及源码 commit 保存到 `outputs/`，该目录忽略在 Git 中。`run_meta.json` 记录 Python、commit、平台和无秘密的示例配置散列；不加载生产配置。每次 task/group 都从 `dataset/fixtures/` 复制独立工作区到 `outputs/.../workspaces/`。清理仅可使用 runner 的 `--clean-run RUN_ID`，且只删除评测 outputs 下该 run 的 workspace。

`--stub-all` 会运行全部 24 条任务 × A_direct/B_current，共 48 个独立 fixture 工作区，不发起模型调用。`A_direct` 是直接 Brain/ReAct 路由，`B_current` 保留当前 Planner 与条件 Checker 行为；没有 Checker 独立开关。真实 Adapter 每次调用新建 task workspace 和 `VIGIL_HOME`，单进程只处理一个任务；只接受调用方传入的 API 配置，不加载生产配置。历史只存在该进程内，记忆检索/写入、插件装载与同步都关闭；工具 schema 和工具执行都受本 task 的 `allowed_tools` 限制，文件系统工具仅在 task workspace 工作。调用要求 `confirmed=True`，当前阶段不启用 Runner 到该 Adapter 的路径。

## 任务与字段

`dataset/tasks.jsonl` 是 24 条固定任务，6 类各 4 条；每条含 `id, category, request, fixtures, allowed_tools, expected_artifacts, oracle, timeout_seconds, risk_level, holdout`。holdout 标记 5 条。哨兵凭据均为假值，工具仅在 run 专属临时副本中操作。

事件 JSONL 保存每个 run 的路由、规划/检查器状态、模型调用计数、工具调用、错误、验收、最终文本和沙箱位置。模型 token、费用与延迟在 stub 中记为 null，不会把缺失量写成 0。

## 真实 Brain 调用链摘要

`brain/server.py::_chat_with_tools` 调用 `planner.plan_decision` 与 `planner.enabled`；满足启发式且配置开启后调用 `planner.plan`，随后 `planner.execute_plan`、`Brain.chat(... no_tools=True)`、`planner.verify_result`，有反馈时最多再调用一次主模型修正。空计划或异常回退到普通 `Brain.chat` 原生 function calling。规划/检查器通过 `brain/planner.py::_ask_flash` 发独立云端 chat completion 请求。`verify_result` 对无工具、最多 2 个工具步骤且只有一种工具的情况跳过；失败也跳过。故生产现状只能作为 `B_current`，不能构成纯 Planner B 与 C 的消融。

生产入口 `brain/server.py` 的 `/api/chat` 会使用生产 Brain、历史与记忆，因此评测 Adapter 不调用它。Adapter 在隔离 worker 中直接构造 Brain，并通过内存 `session_src` 保持单个 task-run 会话；`log=False` 且隔离 Brain 不提供记忆存储。Planner 执行工具时传递渠道、task allow-list，并与该渠道当前启用的工具规格取交集；没有 allow-list 时拒绝执行。

小型真实实验的任务选择、组别、模型和调用预算见 [08-phase2-experiment-plan.md](08-phase2-experiment-plan.md)。它只是待确认方案，不代表已经请求 API。

## P13 before/after 指标

`p13_metrics.py` 汇总隔离模型运行的 JSONL 记录，按精确 `model_id` 与 `before`/`after` 分组计算完成率、缺陷准确率、修复验证成功率、工具异常、上下文超限、续跑成功率、输出 Token 与耗时。每行记录包含 `task_id, model_id, variant, success, confirmed_defects, false_positive_defects, repair_attempted, repair_verified, tool_errors, context_overflow, resume_expected, resume_succeeded, output_tokens, elapsed_seconds, provider_protocol`。缺失 Token/耗时保留为未知值。报告不会读取生产配置或自行请求模型；没有 P13 隔离模型记录时会写 `NOT RUN`。

至少两种 Provider 协议的本地适配契约通过后，只能说明协议映射受测；模型效果对比仍需固定任务集、同模型、隔离配置和独立数据目录后执行。
