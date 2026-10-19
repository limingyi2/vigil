# Agent 自动评测基础设施最终验收

日期：2026-10-20（Asia/Shanghai）

分支：`codex/vigil-agent-improvement-foundation`

PR：[limingyi2/vigil#10](https://github.com/limingyi2/vigil/pull/10)
范围：仅评测器、评测清单、基础设施测试、忽略规则和改进记录；未修改 Vigil 业务逻辑。

## 最终自动化结果

本地报告目录：`%LOCALAPPDATA%/Vigil/agent-eval/reports/`。完整路径由执行器写在下表报告文件中。报告未加入 Git。

| 命令 | 报告 | 案例结果 | 实际 pytest 结果 |
|---|---|---:|---:|
| `python scripts/agent_eval.py run --suite regression --label final-regression-final-run` | `run-20261020T030219-2ffdbd2c.json` | 12 PASS，0 FAIL，0 TIMEOUT，0 BLOCKED；2 NOT RUN（未选择的 security/full-local 案例） | 237 passed，3 skipped |
| `python scripts/agent_eval.py run --suite security --label final-security-final-run` | `run-20261020T030439-65f582e1.json` | 1 PASS，0 FAIL，0 TIMEOUT，0 BLOCKED；13 NOT RUN（该报告中的其他案例未单独运行） | 27 passed |
| `python scripts/agent_eval.py run --suite full-local --label all-local-pytest-acceptance` | `run-20261020T030257-00088aa0.json` | 1 聚合案例 PASS，0 FAIL/TIMEOUT/BLOCKED；清单中其余 13 案例未单独启动 | 1,381 passed，19 skipped，47 subtests passed，88.60 秒 |
| `python scripts/agent_eval.py run --set infrastructure --label runner-self-tests-final` | `run-20261020T030209-8c603637.json` | 1 PASS，0 FAIL/TIMEOUT/BLOCKED；13 NOT RUN（未选择） | 10 个评测器单元测试 passed |
| `python scripts/agent_eval.py run --case codex-reasoning-events --label reasoning-events-cwd-fix` | `run-20261020T025622-6f8500b7.json` | 1 PASS，0 FAIL/TIMEOUT/BLOCKED；12 NOT RUN（未选择） | 12 passed |

全仓 pytest 命令由清单声明为 `python -m pytest -rs`，依据根目录 `pytest.ini` 收集 `tests/` 和 `brain/tests/`。全仓运行实际执行了 1,400 项结果（1,381 passed、19 skipped、0 failed）；47 个 subtest 也通过。`full-local` 是聚合案例，报告中其他 13 个案例为“未单独执行该案例命令”，不表示这些测试目录没有被全仓 pytest 收集。

## 跳过、失败和环境未运行项

- **最终回归失败：** 0；**最终 TIMEOUT：** 0；**最终 BLOCKED：** 0。
- **pytest skipped：** 19，未计入 passed。原因包括 junction/symlink/mkfifo 在当前 Windows 权限或平台不可用；缺少本机 `app_conn.json`、`_chat.html` 和私有插件素材；FFmpeg 不可用。`-rs` 跳过原因保存在全仓报告的 stdout 中。
- **行为场景 NOT RUN：** 10。当前环境没有可供验收的真实模型凭据/受控模型账户、Electron 交互运行时及独立外部插件/服务夹具；相应场景保留在清单，不声称已通过。
- **曾发生并已修复的基础设施失败：** 初次完整 regression 报告 `run-20261020T025511-fc9a5c21.json` 中 `codex-reasoning-events` 因从仓库根目录执行而在收集阶段出现两个模块导入错误。确认其测试设计要求以 `brain/` 为工作目录后，执行器增加了受仓库路径限制的 `cwd` 支持，并将该字段纳入比较条件；定向重跑 12/12 通过，之后完整 regression 重跑为 12/12 PASS。原失败报告未删除。该修正只改变评测执行配置，没有修改业务模块或测试断言。

## 执行器与状态闭环验收

- `--help`、`list`、清单 `validate`、smoke `--dry-run` 已执行；清单最终为 14 个自动案例、4 个 suites、10 个 test sets 和 10 个需特殊环境的行为场景。
- 测试命令限定为清单 argv 中的 Python pytest；未声明案例/非 pytest 命令会拒绝。cwd 只允许仓库内路径，状态目录和报告拒绝写进仓库。
- 自动分类测试覆盖 PASS 与 skipped 计数分离、全跳过为 NOT RUN、TIMEOUT、启动 BLOCKED、pytest collection FAIL 诊断不重复计数、FIXED/REGRESSION、测试命令或工作目录变化导致 `same_conditions=false`。
- 同条件报告对比和任务续接已验证；本机 `IMP-0001` 经 `OPEN → BLOCKED → IN_PROGRESS → READY_FOR_VERIFY → COMPLETED`，blocked resume 没有运行命令。完成门禁要求关联的基线、后测和同条件对比，所有关联案例 PASS 且无回归。
- 报告/任务默认保存在用户状态目录；Git 状态仅包含明确的代码、清单、测试和文档文件，无报告、密钥、用户数据或运行产物。
- `python -m py_compile scripts/agent_eval.py`、`git diff --check` 通过。没有构建发布包、改动 master、合并 PR 或修复业务 Bug。

## 报告索引

全部原始报告与任务状态位于本机 `%LOCALAPPDATA%/Vigil/agent-eval/`，本地任务 `IMP-0001` 已完成评测器流程验证。核心报告：

- Regression：`run-20261020T030219-2ffdbd2c.json`
- Security：`run-20261020T030439-65f582e1.json`
- Full local pytest：`run-20261020T030257-00088aa0.json`
- Evaluator unit tests：`run-20261020T030209-8c603637.json`
- 初次 cwd 收集失败（保留）：`run-20261020T025511-fc9a5c21.json`
