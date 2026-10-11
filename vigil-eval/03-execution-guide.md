# 第二阶段执行指南

## Deterministic 验证

```powershell
venv\Scripts\python.exe vigil-eval/runner.py --stub-all
venv\Scripts\python.exe -m pytest -p no:cacheprovider vigil-eval/tests brain/test_planner_execution_auth.py -q
```

`--stub-all` 运行 24 条任务 × A_direct/B_current，共 48 个 task-run，脚本化工具操作限定在每个独立 fixture 副本中。它不导入真实 Brain，不读取真实凭据，也不请求任何模型 API。

## 隔离的真实 Brain Adapter

`adapters/vigil_adapter.py` 为单个 task-run 新建 `workspace/` 和 `home/`：fixture 复制到 workspace；`VIGIL_HOME` 指向 home；其中只写调用方显式传入的最小配置；子进程只处理该任务。真实 Adapter 不读取 `brain/config/config.json`，不会启动服务、同步、插件或后台记忆写入，聊天关闭磁盘存档。Brain 的工具表及执行分发被替换为该 task 的 `allowed_tools` 和 workspace 沙箱。

调用接口要求显式 `confirmed=True`、`outputs_dir=vigil-eval/outputs` 和独立 `api_config`；未满足时 fail closed。Adapter 仍有网络能力，唯一预期的网络目的地是调用方提供的模型 API。适配器目前只实现每次单任务执行；外层实验编排器尚未接线，真实批量实验没有入口。

## Planner 执行授权

server 为当前请求渠道取得启用的工具 schema，并将 `src` 与 `allowed_tools` 传至 `planner.execute_plan`。执行器再次取得当前渠道可用 schema，并与 allow-list 求交集；无 allow-list、工具未启用、或工具不在任务 allow-list 中时均在 `run_tool` 前拒绝。Planner 的规划和 Checker 业务判断没有改动。

## 实验计划

方案和请求量估算见 `08-phase2-experiment-plan.md`。不发起任何真实 API 请求，直至用户确认该方案及其模型预算。
