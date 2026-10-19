# Vigil Agent 改进日志

此文件只追加，不覆盖既有失败记录。用户本机的可续接任务 JSON 和每轮完整评测报告保存在状态目录；Git 中仅记录经脱敏的摘要。状态枚举：`OPEN`、`IN_PROGRESS`、`BLOCKED`、`READY_FOR_VERIFY`、`COMPLETED`、`NOT_RUN`。没有实际执行的测试一律记录为 `NOT RUN`。

## 2026-10-20 — IMP-INFRA-001 — 自动评测与任务续接基础设施

- **基线提交：** `a91b83fa6920c997a4a2e9c3a352615eb033fbc4`，分支 `codex/vigil-agent-improvement-foundation`
- **问题与影响：** 初版执行器能运行一组 pytest，但缺少任务状态续接和前后报告对比；状态枚举不能准确区分无法启动、未运行和测试跳过；manifest 缺少逐案例验收、环境和测试集。
- **复现步骤：** 初版 `scripts/agent_eval.py` 仅支持 `--list/--suite/--case`，并输出小写 `passed/failed/timeout/error`；没有 compare/task 子命令。审阅 `benchmark-cases.json` 可见行为场景没有统一环境标记和逐案例运行元数据。
- **根因证据：** PR 分支初始文件实现即为证据；首次基线后 `working_tree_dirty=true`，起始 HEAD SHA 仍为上列提交。
- **修改范围：** 仅 `scripts/agent_eval.py`、本目录 manifest/流程/能力文档及 `.gitignore`。没有修改 Vigil 业务源码。
- **架构/兼容性影响：** 保持 Python、pytest、JSON 和 Git 结构；命令改为清晰子命令 `list/validate/run/compare/task`。命令只从 manifest 读取 `{python} -m pytest` argv，禁止 shell 和命令行自由命令。报告及 task JSON 默认位于本机用户状态目录。
- **测试命令：**
  - `python scripts/agent_eval.py --help` — PASS
  - `python scripts/agent_eval.py validate` — PASS；12 自动案例、3 suites、8 test sets、10 行为场景，引用通过结构检查
  - `python scripts/agent_eval.py list` — PASS
  - `python scripts/agent_eval.py run --suite smoke --dry-run` — PASS；只打印清单命令
  - `python scripts/agent_eval.py run --suite smoke --label foundation-baseline` — 实测 PASS
  - `python scripts/agent_eval.py task create/update/resume/show` — 实测 PASS；`IMP-0001` 从 OPEN 更新为 BLOCKED，续接显示已保存证据并保持 BLOCKED，没有触发测试
- **结果：** 基础设施实现完成；任务闭环可创建、追加证据、阻塞、读取/续接、定向运行、比较及人工进入待验证。未启动任何 Vigil 业务 Bug 修复。
- **修改前结果：** 没有前版的 compare 或状态管理能力，因此没有伪造前后业务测量值。
- **修改后结果：** 报告 `run-20261020T024631-6d7caf09.json`：`agent-run-lifecycle` 27 passed；`plan-observability` 11 passed；`runtime-trace` 9 passed。案例计数 `PASS=3, FAIL=0, TIMEOUT=0, BLOCKED=0, NOT RUN=9`；10 个真实环境行为场景仍为 `NOT RUN`。执行总耗时 5.812 秒。报告路径 `%LOCALAPPDATA%/Vigil/agent-eval/reports/run-20261020T024631-6d7caf09.json`。
- **前后对比：** 尚未有业务修改前后报告；基础设施 compare 的分类验证记录在后续追加条目。真实业务修复前后对照待后续阶段。
- **尚未验证：** 真实模型行为、Electron 流断线/取消/恢复、真实插件与外部系统、Provider 故障注入；此外未运行自动 regression 全套、security、provider、context、plugins 和 cancellation 测试集。
- **下一步：** 基础设施可靠性复核完成后，由后续改进阶段从 AG-001/AG-002 等待验证风险中选一个问题，先关联本机基线报告，再创建定向任务；本阶段暂停于流程交付，不开始业务修复。

### 本轮基础设施验证追加（同日）

- **真实同条件评测对比：** `run-20261020T024915-5c5c4c79.json` 与 `run-20261020T024923-56a1a8da.json`；同一 smoke 案例、环境、argv 与超时，`same_conditions=true`，3 项 UNCHANGED PASS，其余 9 项 NOT RUN。
- **分类器验证：** 本机报告目录内两份标注为 synthetic 的 JSON fixture（不代表执行过测试）验证 `FAIL -> PASS = FIXED`、`PASS -> FAIL = REGRESSION`；真实烟测比较中未制造业务失败。
- **可续接任务闭环：** 本机任务 `IMP-0001` 实际经历 `OPEN -> BLOCKED -> IN_PROGRESS -> READY_FOR_VERIFY -> COMPLETED`。BLOCKED 状态执行 resume 时只恢复上下文且不运行命令；解除阻塞后 resume 只运行关联的 `agent-run-lifecycle`。前后单案例报告 `run-20261020T024943-d63c4d3c.json` / `run-20261020T024950-e3404975.json` 均 PASS；比较 `compare-91849b4ac3.json` 同条件且无回归。该本地任务只验证任务生命周期，不代表 Vigil 业务修复。
- **其余验证：** `python -m py_compile scripts/agent_eval.py` PASS；`git check-ignore` 确认常见本地状态/报告覆盖目录被忽略；smoke dry-run 只显示清单中的 Python pytest argv。

### 全量自动化验收追加（同日）

- **基准更新：** 清单现含 14 个自动案例、4 个 suites、10 个 test sets；增加 `full-local` 聚合套件与评测器本身的 10 项单元测试。
- **回归工作目录问题：** 第一次 regression 报告 `run-20261020T025511-fc9a5c21.json` 中推理事件案例收集失败；两个测试要求从 `brain/` 启动，错误为 `ModuleNotFoundError: codex_adapter` 和 `ImportError: tests.test_codex_adapter`。修复为 manifest 声明仓库内 `cwd=brain`，runner 校验目录不越出 checkout 并在报告中记录实际目录。定向报告 `run-20261020T025622-6f8500b7.json`：12 passed。初次失败报告保留。
- **最终 regression：** 实际命令 `python scripts/agent_eval.py run --suite regression --label final-regression-final-run`，报告 `run-20261020T030219-2ffdbd2c.json`：12 PASS，0 FAIL/TIMEOUT/BLOCKED，2 NOT RUN（security 和 full-local 案例未在这个运行中单独启动）；测试 237 passed、3 skipped。
- **最终 security：** `python scripts/agent_eval.py run --suite security --label final-security-final-run`，报告 `run-20261020T030439-65f582e1.json`：1 PASS，27 passed。
- **最终全仓自动化：** `python scripts/agent_eval.py run --suite full-local --label all-local-pytest-acceptance`，报告 `run-20261020T030257-00088aa0.json`：1 聚合案例 PASS，1,381 passed、19 skipped、47 subtests passed，88.60 秒，0 failed。使用 `-rs` 保存 19 项 skip reason：缺少 junction/app_conn.json/_chat.html/插件素材/FFmpeg，以及 Windows symlink/mkfifo 能力或权限。Skipped 单独计数，没有当作通过。
- **评测器测试：** `tests/test_agent_eval_runner.py` 10 passed，报告 `run-20261020T030209-8c603637.json`。覆盖 manifest/命令约束、cwd 越界拒绝、跳过/超时/启动阻塞、pytest collection error 计数、对比结果/条件和报告写入路径。
- **特殊环境：** 10 个真实模型、Electron 和外部系统行为场景均 NOT RUN；本轮没有环境阻塞的自动案例（BLOCKED=0）。精确 skip 与行为场景说明见 `final-acceptance.md`。
- **最终代码验证：** 在最后的路径隔离和失败证据追加改动后，重新运行 `run-20261020T025116-2d8247f1.json`：3 项 smoke PASS，共 47 个 pytest passed，0 failure/timeout/blocked，9 个未选自动案例 NOT RUN。`--help`、manifest validate、completed task resume 均正常；显式报告目录和状态目录写入仓库的请求被拒绝。`git diff --check` 通过。
- **终版复核：** 随后调整了“全跳过时返回非零”和任务 `NOT_RUN` 状态记录，执行 `python -m py_compile scripts/agent_eval.py`、manifest validate 和 smoke 再验，报告 `run-20261020T025153-72de9c4f.json` 仍为 PASS=3（47 passed）、FAIL/TIMEOUT/BLOCKED=0、NOT RUN=9。

---

## 后续追加格式

### YYYY-MM-DD — ISSUE-ID — 简短标题

- **基线提交：** `<sha>`
- **问题与影响：**
- **复现步骤：**
- **根因证据：**
- **修改范围：**
- **架构/兼容性影响：**
- **测试命令：**
- **结果：** PASS / FAIL / TIMEOUT / BLOCKED / NOT RUN
- **修改前结果：**
- **修改后结果：**
- **前后对比：** `<comparison report path and classification>`
- **尚未验证：**
- **下一步：**
