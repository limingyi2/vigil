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

---

## 五轮后续质量复核验收（2026-10-20）

本轮从 PR 分支提交 `5b3e86a151eae542a9dc901ee7cbd4bbfb289eb5` 开始，共完成五轮：Agent terminal transient 清理、更新目录重叠检查、插件用户数据清理失败提示、部分清理时提示准确性、旧式插件目录卸载错误处理。每个发现均先以隔离测试复现或反向故障注入，再修改并运行相关回归；详细证据见 `improvement-log.md` 五轮记录。

| 最终运行 | 结果 | 本机报告 |
|---|---|---|
| `python scripts/agent_eval.py validate` | PASS：14 自动案例、4 suites、10 test sets、10 行为场景 | stdout；清单文件 `benchmark-cases.json` |
| `python scripts/agent_eval.py run --suite regression --label five-round-regression` | 12 PASS，0 FAIL/TIMEOUT/BLOCKED，2 NOT RUN；244 passed、3 skipped | `run-20261020T102801-74c2e589.json` |
| `python scripts/agent_eval.py run --suite security --label five-round-security` | 1 PASS；29 passed，0 failed | `run-20261020T103048-56df5d22.json` |
| `python scripts/agent_eval.py run --suite full-local --label five-round-full-local-serial` | 1 PASS；1,388 passed、19 skipped，0 failed | `run-20261020T102908-1aea2f90.json` |
| `node --test client/tools/test-unified-runtime.js client/tools/test-agent-center-state.js client/tools/test-agent-timeline.js client/tools/test-p2-stream-lifecycle.js` | 49 passed、0 failed | Node stdout；进程退出码 0 |
| `python -m py_compile scripts/agent_eval.py brain/agent_runs.py brain/plugin_ops.py`; `git diff --check` | PASS | 命令退出码 0 |
| smoke/regression 基线与轮后同条件 compare | 12 个共同回归案例均 PASS → PASS，无新增回归；轮内复现 compare 对 AG-009/011/013 标为 FIXED | `compare-a514fff3ea.json` 及逐轮 compare 见改进日志 |

全仓 pytest 首次与 regression 并行运行时有 15 个启动错误，独立 Brain 测试进程未在 60 秒内就绪，报告 `run-20261020T102724-04ab9dd3.json` 保留为 FAIL。停止并行负载后，以相同全仓命令串行重跑通过；首次失败不删除，也不将其记为通过。全仓仍有 19 个 pytest skip，未计入 1,388 passed，原因包括 Windows symlink/junction/mkfifo 权限、缺少本机测试素材及 FFmpeg。

10 个需要真实模型、Electron 或外部插件/服务的行为场景仍是 NOT RUN；当前机器缺少 Electron/npm 依赖、真实模型测试账户和私有外部 fixture。没有构建发行包。Agent reasoning 缓存清理以对象/持久化断言验证，尚无真实模型长期内存曲线；升级目录分离验证为纯 Node 临时目录；插件锁定/部分删除通过临时故障注入，不代表每种 Windows 文件系统锁语义。评测报告及 compare JSON 均只在 `%LOCALAPPDATA%/Vigil/agent-eval/reports`，不入库。

轮后 regression 与轮前基线 compare：`compare-a514fff3ea.json`，同测试选择/环境为 true。R1/R3/R5 的复现到修复 compare：`compare-8991e541bc.json`、`compare-9fe0d1ca1e.json`、`compare-9d91a3082d.json`，均识别为 FIXED。PR #10 保持 open/unmerged；本轮无发布包，没有修改 master。

---

## 第二阶段真实 Windows / Electron / Brain 验收（2026-10-20）

本节 supersede 早期“无 Electron/npm”的环境阻塞描述。工作树使用 Node 24.19.0/npm 11.17.0 portable toolchain、隔离 CPython 3.12.14 venv、通过 `npm ci` 安装的 Electron 28.3.3；模型、Brain home、插件及 Electron user data 均为临时 fixture。没有读取真实 Provider 密钥或用户聊天。

### 实际命令与结果

| 命令 / 范围 | 结果 | 本机证据 |
|---|---|---|
| `python scripts/agent_eval.py --state-dir <本地隔离目录> validate` | PASS：14 自动案例、4 suites、10 sets、10 行为场景 | CLI 输出；状态目录在 `%LOCALAPPDATA%/Vigil/stage2-state` |
| `python scripts/agent_eval.py --state-dir <本地隔离目录> run --suite regression --label phase2-regression` | 12/12 案例 PASS；240 passed、3 skipped | `reports/run-20261020T104957-72a4dc41.json` |
| 同上 `run --suite security --label phase2-security` | 1/1 案例 PASS；29 passed | `reports/run-20261020T105048-084cb27f.json` |
| 同上 `run --suite full-local --label phase2-full-local` | 1 个聚合案例 PASS；1,388 passed、19 skipped、0 failed；47 subtests passed，114.01 秒 | `reports/run-20261020T105101-97df30a8.json` |
| `node --test` 8 个包含 `node:test` 的客户端组件文件 | 56 passed、0 failed、0 skipped | 控制台输出；组件清单为 Agent Center notifications/state、timeline、localtool read、stream lifecycle、plugin media、public updates、unified runtime |
| `node client/tools/test-electron-brain-e2e.js` | 最终两次独立执行均 exit 0、8/8 PASS | `%LOCALAPPDATA%/Vigil/stage2-state/phase2-electron-brain-e2e-final.log` 与 `phase2-electron-brain-e2e-stability.log` |
| Agent evaluator synthetic blocked/resume workflow | `OPEN → BLOCKED → IN_PROGRESS → READY_FOR_VERIFY → COMPLETED`；blocked resume 未运行测试；同条件 PASS → PASS / UNCHANGED | 任务与报告在 `%LOCALAPPDATA%/Vigil/stage2-state/task-flow-*` 的独立本地目录，不入库 |

全仓 Python 跳过 19 项未计入通过，`-rs` 原因保存在报告，包括缺少私有/本机素材、FFmpeg、Windows junction/symlink/mkfifo 权限限制。行为场景不因单个组件测试通过而升格为 PASS。

### 测试基础设施修复与失败证据

真实 Electron 首轮 E2E 暴露临时插件 `TOOL_SPECS.params` fixture 契约错误（`KeyError: slice(None, 3, None)`、Fake Provider 请求数为 0），已仅修复 fixture。随后稳定性复跑暴露会话列表异步加载 race（欢迎页已出现而列表请求未完成，断言 `0 !== 12`），已增加基于会话数量的显式 `waitFor`，未增加固定 sleep 或放宽断言。之后两次独立执行全部 8 项通过。以上均为测试基础设施问题，不是 Vigil 业务 Bug。

### P1 状态与候选发行包

| P1 | 当前状态 | 剩余范围 |
|---|---|---|
| AG-002 SSE 恢复/游标/队列压力 | 部分验证 | 真实 Electron 流、终态和 Brain 进程断开已验；Electron 网络断开后重连、Brain cursor gap 快照及长时间事件压力未完整验收 |
| AG-003 Electron/Brain 状态一致性 | 部分验证 | 成功、插件失败、会话与 Brain/Electron 重启、Brain 断开后 UI 解锁已验；取消/超时/退出窗口的状态矩阵待验证 |
| AG-005 不确定请求与幂等副作用 | 待验证 | 自动契约/去重套件通过；响应不确定情况下的真实非幂等外部操作未故障注入 |
| AG-008 包内容、用户数据、升级/回滚 | 部分验证 | 此环境能够尝试隔离 Windows candidate installer lifecycle；旧版本升级和安装器级失败回滚须结合候选包实测结果判断，不能以单元快照测试替代 |

Windows 候选包的版本、source commit、installer SHA-256、包内清单、实际安装/启动/重启/卸载及升级/回滚结果写入本阶段本机 `phase2-acceptance-final.json`，独立 lifecycle 结果见 `phase2-candidate-lifecycle-final.json` 与 `phase2-upgrade-rollback-final.json`；原 `phase2-acceptance.json` 是首次失败记录并保留不覆盖。候选包不发布，也不覆盖正式安装。未完成项按最终报告标为 BLOCKED/NOT RUN，不作通过推断。PR #10 保持 open/draft/unmerged；本阶段没有修复 Vigil 业务 Bug。
