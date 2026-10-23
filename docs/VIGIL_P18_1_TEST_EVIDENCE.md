# P18.1 测试证据

日期：2026-10-23（Asia/Shanghai）。环境：Windows、Python 3.12.14。
开发基线为 PR #29 合并提交 `4e5b18f40470dac42d9bb9e27d75c9b5d2112ecc`；
提交前再次 fetch 确认 `origin/dev` 未变化。

## 正式验证

| 检查 | 结果 |
| --- | --- |
| `python -m pytest tests/test_p18_workflows.py -o addopts='' -q` | **41 passed**，7.38 秒 |
| `python -m pytest -q`，第一轮全仓 | 退出码 0，包含 P18 初版及既有全部测试 |
| `python -m pytest -o addopts='' -q --tb=short --junitxml=.local-dev/p18-final-results.xml` | **1769 passed, 21 skipped, 49 subtests passed**，165.36 秒，退出码 0 |
| `python -m compileall -q brain pet tests` | 退出码 0 |
| `git diff --check` | 通过 |

最终全仓运行包含 40 项 P18 测试；运行期间仅增强了迁移测试的
旧数据样本并补充了第 41 项跨计划派发键测试，生产实现未再变动；这两项均包含在最终
41 passed 专项运行中。完整命令不依赖本机绝对 Python 路径，CI 可直接复现。

本机 `venv/Scripts/python.exe` 引用的历史运行时已失效，`python` 默认命令为 Windows
商店别名。正式验证使用 Codex 提供的 Python 3.12.14，并在测试进程 PATH 中把该 Python
目录放在首位，使 Coding Harness 内执行的 `python -m pytest` 使用相同解释器。
未修改仓库虚拟环境或项目依赖。

## 必需验收项对应

| 要求 | 直接测试证据 |
| --- | --- |
| 跨重启完整恢复 | `test_separate_process_reads_all_versions_attempts_and_checkpoints` 用独立子进程重开同一主库，比较两版计划、旧完成证据、当前在途 attempt、所有 checkpoint 的完整 JSON |
| 重复请求幂等 | `test_idempotence_restart_and_conflicting_requests`、`test_parallel_requests_serialize_and_replay`、`test_attempt_checkpoints_confirmed_result_and_duplicate_guard` |
| 用户/人设/会话/工作区隔离 | `test_scope_isolation_all_entry_points`、`test_owned_other_conversation_and_workspace_are_isolated`、实际 Brain 身份与 owner 截断边界测试 |
| 异常回滚无半完成检查点 | snapshot/request/checkpoint 故障注入、attempt 新建/结果回滚、共事务外层回滚与 SAVEPOINT 故障测试 |
| 计划更新保留历史证据 | `test_plan_history_preserves_evidence`、独立进程测试、`test_dispatch_key_and_execution_evidence_survive_plan_replacement` |
| 未知副作用需人工核对 | `test_unknown_effect_manual_review_and_reconciliation` 的 write/delete/command/other/read 五类参数；无结果或无证据不能完成 |
| 恢复前校验身份/工作区/权限 | 权限拒绝、权限异常、工作区改变、会话改归属、权限计算期间 revision 改变；所有测试均没有调度/恢复动作 |
| 兼容 P17 Run 与诊断 | 实际 `agent_runs.create(start=False)` 关联、既有诊断写入与诊断库删除测试、Run/calls 查询不触发 P17 修复 |
| 既有数据不受影响 | v6 → v7 对有数据的 conversations/workspaces/memories/messages/runs/events/calls/call_ids/jobs/turns 逐行比较；日志 schema 保持 v4 |

测试均使用临时数据库、假模型或假证据，不访问生产主库，不调用真实工具，不改用户现有
未跟踪文档。本轮没有真实断电/磁盘满/外部工具效果确认测试，不把模拟持久化验证宣称为
自动恢复或外部副作用 exactly-once 验收。

## 已核对的基线测试顺序问题

额外尝试按人工指定顺序先运行 `test_p17_dual_db_hybrid.py`，再运行
`test_agent_failure_inspection.py`，诊断模块的 9 项测试会失败。原因是前一文件的 fixture
会移除并重新导入 `storage.*`，而后一文件在收集时缓存了旧 `db/diag` 模块引用，随后
两个模块实例指向不同临时目录。

为确认来源，用 `git archive 4e5b18f...` 导出未改动基线到忽略目录，在相同 Python/PATH 下
运行以下命令，得到 **9 failed, 100 passed, 1 skipped**；失败项目与开发分支一致：

```powershell
python -m pytest tests/test_p17_dual_db_hybrid.py tests/test_agent_failure_inspection.py -o addopts='' -q --tb=short
```

该失败在没有任何 P18 文件的基线中也存在。没有为了改变既有测试顺序修改 P17 源码或测试。
P18 服务在操作时获取当前 storage 模块，P18 fixture 也重新绑定当前模块，避免引入相同
缓存问题。正式全仓按仓库默认收集顺序运行；额外顺序问题单独披露，不计作全仓通过。

## 下一阶段的验证边界

需要在实际 Agent Run 接线后验证：受信任 Scope 从 HTTP 到执行线程的传递、真实工具
schema/权限/审批复查、同一 Run 事务里的 attempt 与检查点提交、旧执行者 fencing、
真实客户端迟到回执、写文件/删除/命令的人工效果核对、预算重新授予。
本轮仅交付持久化接口与共事务检查点基础，不自动执行这些流程。
