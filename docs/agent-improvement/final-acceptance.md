# Vigil 质量冲刺阶段验收

日期：2026-10-20（Asia/Shanghai）

分支：`codex/vigil-agent-improvement-foundation`

PR：[limingyi2/vigil#10](https://github.com/limingyi2/vigil/pull/10)
范围：本文件前半部分保留了先前“仅评测基础设施”的历史验收结果；本次追加的质量冲刺记录覆盖全仓自动化评测及三轮有针对性的 Agent 工具输出/本地文件读取改进。没有修复本轮其余待办中的业务问题。

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

---

## Vigil 全仓质量冲刺追加验收

本追加记录以 2026-10-20 当时 PR worktree 内容为准。当前本机报告和任务数据目录仍为 `%LOCALAPPDATA%/Vigil/agent-eval/`，文件未加入 Git。

### 三轮修改与前后测量

| 轮次 / 问题 | 修改前证据 | 修改后证据 | 差异 |
|---|---|---|---|
| R1 AG-001 大型工具结果 | `run-20261020T030935-d08e9dd6.json`：新增边界测试复现 FAIL，超限被 `resource_limit` 终止 | `run-20261020T075337-54ae25ea.json`：Agent lifecycle 28 PASS；全仓后续 1,384 passed | 有界预览保留 head/tail 和明确截断标记；`compare-17fe2d45f4.json` 报告 FAIL → PASS / FIXED |
| R2 AG-004 大文件分块 | 工具固定只读前 200KB，没有可调用的 offset/length | `run-20261020T075325-1231da3c.json` local tool 19 PASS；`run-20261020T075341-ac78a91f.json` plugin contract 38 passed / 3 skipped；临时大文件 Node test PASS | 新增类型化 offset/length schema、范围校验、多参数远程透传与下一段提示 |
| R3 UTF‑8 与短读 | 代码审查发现 byte range 会切开多字节字符；预分配 Buffer 未采用 `bytesRead` | `node --test client/tools/test-localtool-read.js` PASS，600KB 汉字文件逐块还原相等；Node 8 文件 56/56 PASS | 边界对齐、防不完整尾字符，并只返回实际读取字节 |

### 最终可运行回归

| 实际命令 | 结果 / 本机报告 |
|---|---|
| `python scripts/agent_eval.py validate` | 14 自动案例、4 suites、10 test sets、10 行为场景；PASS |
| `python scripts/agent_eval.py run --suite full-local --label sprint-round3-full-local` | `run-20261020T075418-809a0ebe.json`：1,384 passed、19 skipped、0 failed，105.469 秒；聚合案例 PASS |
| `python scripts/agent_eval.py run --suite regression --label sprint-round3-regression` | `run-20261020T075603-2252ce2d.json`：12 案例 PASS，240 passed、3 skipped，0 failed/timeout/blocked；2 个未选择案例 NOT RUN |
| `python scripts/agent_eval.py run --suite security --label sprint-round3-security` | `run-20261020T075641-c5d65998.json`：1 案例 PASS，29 passed，0 failed/timeout/blocked |
| `python scripts/agent_eval.py run --case agent-run-lifecycle --task IMP-0002 --label task-IMP-0002-verify` | `run-20261020T101141-34867c51.json`：1 案例 PASS；同条件 compare `compare-e64472c5a4.json` 判定 FAIL → PASS / FIXED；本地任务完成门禁通过 |
| `node --test client/tools/test-agent-center-notifications.js client/tools/test-agent-center-state.js client/tools/test-agent-timeline.js client/tools/test-p2-stream-lifecycle.js client/tools/test-plugin-media.js client/tools/test-public-updates.js client/tools/test-unified-runtime.js client/tools/test-localtool-read.js` | 56 passed、0 failed |
| `python scripts/agent_eval.py compare run-20261020T030219-2ffdbd2c.json run-20261020T075603-2252ce2d.json` | `compare-0566b4ee99.json`：12 个共同回归案例均 PASS → PASS，无回归；same test selection/environment=true |
| `node --test client/tools/test-*.js` | 69 tests：56 pass、13 fail-to-load；缺少 `electron`、`ws`、`ajv/dist/2020` 模块，退出码 1。此结果是 **BLOCKED**，不计为测试断言失败或通过 |
| `python scripts/build_release.py --verify` | **BLOCKED**：缺少生成的 `client/build-info.json`，尚未进行配对 Brain 构建；`client/node_modules` 不存在，当前环境没有 npm/Electron。未构建安装包 |

19 项 pytest skip 没有计入通过，skip 原因见全仓报告 stdout。清单里的 10 个真实模型、Electron 和外部 fixture 行为场景仍为 NOT RUN。特殊环境阻塞为：真实模型账户/凭据未提供、Electron/npm 客户端依赖不存在、生成的 Brain/客户端发行身份与产物不存在，以及私有插件/外部服务夹具未配置。

### 安全、状态与交付核验

- 使用 `scripts/agent_eval.py` 的 `--help`、`list`、`validate`、suite dry-run；测试命令只允许运行 manifest 中的 Python pytest argv，cwd 必须位于 checkout 内。Node 测试由人工明确调用，不通过执行器绕过 manifest。
- 任务状态续接与 block/resume、前后 compare 分类由此前基础设施测试及本机 `IMP-0001` 状态流转验证；被阻塞 resume 不运行测试。真实业务任务没有因为被跳过或失败而自动完成。
- 本轮还创建并完成可追踪任务 `IMP-0002`，从 oversized-tool-result 失败基线出发，关联 agent lifecycle 后测和同条件 compare；任务状态路径为 `IN_PROGRESS → READY_FOR_VERIFY → COMPLETED`。报告均在用户状态目录。
- `.gitignore` 对默认/显式本机报告和状态覆盖目录有效；`git check-ignore` 确认 `.agent-eval/`、`agent-eval-reports/`、`agent-eval-state/` 被忽略。实际报告均保存在用户目录，没有报告、密钥、用户聊天或本机数据进入候选提交。
- 清单执行器运行过程只生成报告，不修改业务文件，不执行任意 shell、不 commit、不 push。实际代码变更经过审阅，未修改会话、思考事件、权限、插件生命周期、Provider 或发行规则等剩余业务模块。
- Windows 候选安装包 **未构建**；不对 Windows 安装/升级/卸载或正式发布作通过声明。PR 继续保持未合并；本轮结束后等待后续单独验收，不启动下一轮业务 Bug 修复。

完整能力盘点见 `repository-audit.md`；逐轮根因和修改记录见 `improvement-log.md`。
