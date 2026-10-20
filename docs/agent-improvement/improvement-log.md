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

## 2026-10-20 — Vigil 全仓质量冲刺追加（Rounds 1–3）

本节追加至已有基础设施建设历史之后。三轮只改动 Agent 工具结果与本地文件读取路径及其测试；不表示全产品达到发布标准。完整范围审计见 `repository-audit.md`。

### Round 1 — AG-001：大型工具结果不再丢弃整次任务进度

- **基线提交：** `480b9d4a69e4134e6cabaa7d7c65aaec0af1f2da`，分支 `codex/vigil-agent-improvement-foundation`。
- **修改前表现：** Agent lifecycle 新增 oversized tool result 断言后，旧实现将超过 65,536 字符的工具结果以 `resource_limit` 结束运行。失败报告 `run-20261020T030935-d08e9dd6.json` 保留在本地报告目录。
- **根因证据：** `brain/agent_runs.py::_tool` 将超过 `MAX_TEXT` 的工具结果判为资源错误，导致运行终止；与模型流式输出限制不同，这一限制不必让已有执行进度全部失败。
- **修改范围：** `_bounded_tool_result()` 仅保留有界 head/tail 并插入明确省略提示；工具执行继续，缓存/ledger 中存储的结果也有界。新增 `test_large_tool_result_is_bounded_and_explicitly_truncated`。没有提高限制或移除资源保护。
- **修改后结果：** `agent-run-lifecycle` 28 项 PASS，报告 `run-20261020T075337-54ae25ea.json`；全仓 pytest 最终 1,384 passed、19 skipped、0 failed。
- **前后对比：** 原复现失败到修复后 PASS，compare `compare-17fe2d45f4.json` 将 `FAIL -> PASS` 归类为 `FIXED`，同测试选择/环境。
- **可续接任务记录：** 本机评测队列 `IMP-0002` 实际经历 `IN_PROGRESS → READY_FOR_VERIFY → COMPLETED`；后测 `run-20261020T101141-34867c51.json` 和 `compare-e64472c5a4.json` 已关联到该任务。任务 JSON 位于用户状态目录，不入库。
- **尚未验证：** 真实模型是否总能根据截断提示继续检索；模型生成本身超过上限仍安全失败。

### Round 2 — AG-004：远程本地工具支持有界分块读取

- **修改前表现：** `local_file_read` 对大文件只返回前 200KB，工具 schema 不允许指定后续位置；旧插件代理只透传一个位置参数。
- **根因证据：** `client/electron/localtool.js::tool_file_read` 固定从偏移 0 读取；`brain/localtools.py` 只声明 path；`brain/plugins/remote.py::_mk` 把单参数包装成 `{path: value}`。
- **修改范围：** 增加可选整数 offset/length（单次最大 204800 字节），严格验证范围，向 schema 传递整数类型和 `multi_args`，代理透传命名参数；超大文件输出范围、下一 offset 和 EOF 提示。新增隔离临时文件 Node 测试及 Python schema/透传契约。
- **首次测试结果：** 用例首次运行 FAIL（`run-20261020T075259-a3e455e4.json`），错误为测试夹具缺少 `brain/plugins` 导入路径 (`ModuleNotFoundError: remote`)，不是业务断言通过。修正夹具后 local-tool-boundaries 19 项 PASS (`run-20261020T075325-1231da3c.json`)，plugin-contract 38 项（3 skipped）PASS (`run-20261020T075341-ac78a91f.json`)，Node 临时大文件读取 PASS。
- **尚未验证：** Electron UI/真实模型是否选择后续 offset；实际用户工作区读文件行为需 GUI 环境回归。

### Round 3 — AG-004 子问题：多字节字符边界和短读

- **审查发现：** 按字节切片可能将 UTF‑8 字符分到两个 chunk，Node 的非严格 `Buffer.toString('utf8')` 会产生替换字符，导致拼接结果损坏；并发文件缩短时 `FileHandle.read` 可能短读，预分配 Buffer 尾部会变成 NUL。
- **根因证据：** 第二轮代码审阅发现 range 边界直接对 byte offset 截取；`read()` 返回的 `bytesRead` 原先未检查。针对旧解码方式的复现命令：`node -e "const b=Buffer.from('汉'.repeat(10)); console.log(JSON.stringify(b.subarray(0,2).toString('utf8')))` 会显示替换字符。
- **修改范围：** 记录实际 `bytesRead`，将 continuation-byte 起始位置前移，并在截断不完整末尾 code point 时缩短有效范围、让下次读取从有效边界继续；保持每次读取上限。
- **修改后结果：** 临时 600KB 汉字文件逐块读取并重建原文的 Node 测试通过（无替换字符，字节 offset 连续）；8 个无 Electron 依赖 Node 文件 56/56 PASS。全仓 Python 1,384 passed、19 skipped；regression 12 案例 PASS（240 passed、3 skipped）；security 1 案例 PASS（29 passed）。
- **回归对比：** `run-20261020T030219-2ffdbd2c.json` 与 `run-20261020T075603-2252ce2d.json` 比较为相同用例选择/环境；12 个回归案例均 PASS → PASS，无新增回归。报告 `compare-0566b4ee99.json`。
- **尚未验证：** 非 UTF‑8 任意二进制、文件读取期间大规模并发修改及模型最终答案仍需进一步验证；二进制检测继续有效。

### Round 3 反向界面检查追加 — 可选参数空值

- **发现：** Electron 本地工具表单遍历每个字段并将 `el.value` 放入参数对象；未填写的可选字段因此以 `""` 传入。第二轮实现把空 `length` 转成数字 0 并拒绝，导致面板上的默认读取失败。
- **修复：** `tool_file_read()` 将 `undefined`、`null` 和空白字符串视为未提供，并使用 offset 0 / length 204800 默认值；数值非法值仍拒绝。
- **测试：** Node 测试现在以 `{offset: '', length: ''}` 模拟真实表单，再覆盖大文件继续读取和 UTF‑8 全量拼接。最终 `node --test` 该测试 PASS；`run-20261020T101057-1dda636a.json` local-tool-boundaries 19 PASS；8 个纯 Node 文件再次 56/56 PASS。
- **根因与范围：** 根因由 `client/src/js/pages.js::renderTools` 参数采集代码和 Electron localtool 校验规则交叉审查确认。本轮只修复文件读取参数缺省处理，不改表单、权限或其它工具。

### 最终冲刺验收状态

- **当前全仓自动化：** `run-20261020T075418-809a0ebe.json`：1 个 full-local 聚合案例 PASS，pytest 1,384 passed、19 skipped、0 failed，105.469 秒。
- **当前目标回归：** `run-20261020T075603-2252ce2d.json`：12 PASS、0 FAIL/TIMEOUT/BLOCKED、2 NOT RUN；240 passed、3 skipped。
- **安全：** `run-20261020T075641-c5d65998.json`：1 PASS、29 passed、0 skipped。
- **最后受影响项重跑：** `run-20261020T101057-1dda636a.json`：local-tool-boundaries 1 案例 PASS、19 passed；8 个纯 Node 文件 56/56 PASS。
- **Node 组件：** `node --test` 对 8 个无 Electron 依赖文件，56 PASS。全量 `client/tools/test-*.js` 聚合 69 tests 中 56 pass、13 文件加载失败；缺少依赖 `electron`、`ws`、`ajv/dist/2020`，按环境阻塞，不计为 PASS。完整日志保存在 `%TEMP%/vigil-node-suite-20261020.log`。
- **发行包：** `scripts/build_release.py --help` PASS；`--verify` BLOCKED 于未生成的 `client/build-info.json`。`client/node_modules` 不存在且当前 shell 没有 npm；没有生成候选安装包。
- **未验证：** 10 个真实模型/Electron/外部服务行为场景仍 NOT RUN；19 个 pytest skip 及其原因见本机报告；高优先级事件恢复、真实 UI 状态一致性和安装包端到端待有完整运行时的 Windows 环境验证。

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

## 2026-10-20 — 五轮客户端/Brain/插件回归改进

本组工作从提交 `5b3e86a151eae542a9dc901ee7cbd4bbfb289eb5` 开始；所有复现和故障注入均使用临时测试目录/Fake Provider。没有修改 Electron + Python Brain 的总体架构，没有触碰 `D:\vigil` worktree 中的用户文件。

### Round 1 — AG-009：终态任务释放瞬态推理缓存

- **问题与影响：** Agent 结束后，最多 200 个 run 的全局 transient 映射仍保留最多 256 条 reasoning delta；这类内容不持久化，也不能用于终态恢复，却会持续驻留进程内存。
- **复现步骤：** `python scripts/agent_eval.py run --case agent-run-lifecycle --label round1-transient-cleanup-repro`；测试发现任务成功后 `run_id` 仍在 `TRANSIENT`，其中包含 `PRIVATE_REASONING`。FAIL 报告 `run-20261020T102251-cc29787e.json`。
- **修改范围：** `brain/agent_runs.py::_finish` 写入 terminal event 和状态后删除对应 transient reasoning；新增终态内存清理回归断言。不持久化推理文本。
- **修改后结果：** 同案例 PASS，报告 `run-20261020T102301-f77d4e85.json`；后续复验 `run-20261020T102513-287a33e6.json` 与 `run-20261020T102716-7e359d7a.json` 均 PASS。比较 `compare-8991e541bc.json` 在相同案例/环境下归类为 FIXED。
- **尚未验证：** 真实模型下长时间运行的全进程内存曲线；本轮证明对象清理语义，不是系统级内存基准。

### Round 2 — AG-010：拒绝完全重合的安装目录和用户数据目录

- **问题与影响：** 更新回滚快照仅拒绝两目录互相嵌套；当两者完全相等时未命中旧前缀检查，可能在安装树内创建 updates/rollback，再复制同一树。
- **复现步骤：** `node --test client/tools/test-unified-runtime.js` 的隔离临时目录测试在 `checkpoint(install, install, identity)` 处失败；旧逻辑继续进入 `fs.cpSync`，由测试注入的 sentinel 拦截并报错。
- **修改范围：** `client/electron/update-transaction.js` 用 `path.relative` 关系检查，并拒绝相等或任一方向包含；断言拒绝发生于复制前。仅使用临时 fixture。
- **修改后结果：** `node --test client/tools/test-unified-runtime.js` 5/5 PASS，五轮结束时相同文件复验仍 5/5 PASS。
- **尚未验证：** 完整安装器/升级流程因当前 worktree 缺少 Electron 构建依赖未能执行；纯 Node 事务逻辑已覆盖。

### Round 3 — AG-011：插件用户数据清理失败不再谎报完成

- **问题与影响：** 插件卸载的 `purge_data` 使用 `ignore_errors=True`，并在删除调用后无条件声称数据已清；权限拒绝时会留下数据却给出成功清理消息。
- **复现步骤：** `python scripts/agent_eval.py run --case plugin-contract --label round3-plugin-purge-repro`；临时数据目录注入 `PermissionError`，验证发现目录保留而响应没有失败提示。FAIL 报告 `run-20261020T102456-cc86e81a.json`。
- **修改范围：** `brain/plugin_ops.py::uninstall` 只在目录确实消失后追加“插件数据已清理”；失败则返回清理未完成提示和安全错误类型日志，不包含路径或敏感异常内容。新增保留数据的故障注入测试。
- **修改后结果：** `plugin-contract` PASS，报告 `run-20261020T102513-2d20d8fb.json`；比较 `compare-9fe0d1ca1e.json` 归类 FIXED。
- **尚未验证：** 不同 Windows 文件系统/杀毒软件对真实锁定目录的行为；通过可控 `PermissionError` 验证错误路径。

### Round 4 — AG-012：部分清理失败提示不再声称数据完整保留

- **问题与影响：** 反向审查发现 Round 3 的失败提示“数据仍保留”在删除部分文件后抛错时不成立，可能误导用户认为数据未受影响。
- **复现步骤：** 使用临时插件数据目录，让故障注入先删除 `user.txt` 再抛 `PermissionError`；验证目录残留但文件已消失。
- **修改范围：** 失败提示改为“插件数据清理未完成，请检查残留数据”；新增部分删除故障测试，要求明确不能宣称数据仍完整保留或已全部删除。
- **修改后结果：** `python scripts/agent_eval.py run --case plugin-contract --label round4-partial-purge-message-fix` PASS；报告 `run-20261020T102651-e8cebaf7.json`。
- **尚未验证：** UI 对该完整中文消息在所有渠道中的布局；Brain 返回文本契约通过。

### Round 5 — AG-013：插件目录包删除失败不再返回卸载成功

- **问题与影响：** 旧式目录包卸载同样使用 `shutil.rmtree(..., ignore_errors=True)`；目录被锁定时错误被吞掉，插件仍可留在磁盘，而 API 仍返回“已卸载”。
- **复现步骤：** `python scripts/agent_eval.py run --case plugin-contract --label round5-plugin-code-purge-repro`；临时目录模拟 `ignore_errors=True` 吞错，断言原实现仍返回 `ok=True`。FAIL 报告 `run-20261020T102701-80789fb3.json`。
- **修改范围：** 卸载在清理目录包后检查路径是否消失；失败时返回明确错误并停止后续元数据清理。新增旧式目录插件的锁定故障注入。
- **修改后结果：** `plugin-contract` PASS，报告 `run-20261020T102716-37253c5d.json`；比较 `compare-9d91a3082d.json` 归类 FIXED。Round 2 Node 回归 5/5；关联 Node Agent UI/Timeline/stream lifecycle 回归 49/49 PASS。
- **尚未验证：** 被真实系统进程占用时 Electron 插件面板的端到端提示；当前宿主无 Electron runtime。

本轮所有 pytest 报告与 compare JSON 存放在 `%LOCALAPPDATA%\Vigil\agent-eval\reports`，不入库。测试代码只对临时目录施加故障，不运行真实模型，不修改用户配置、聊天记录或插件数据。

## 2026-10-20 — 第二阶段真实 Electron/Brain 环境验收

**基线提交：** `aeacf166ea1478b830e6dfb9a0d9d7b50a6f21b9`，分支 `codex/vigil-agent-improvement-foundation`。Windows 工具链安装在用户本地状态目录的隔离 Node/Python 环境；Electron 28.3.3 和 Brain 均对临时 profile 启动。模型请求只打到 loopback Fake Provider。

### Round 1 — 修正 Electron/Brain E2E 插件夹具契约

- **修改前表现：** `node client/tools/test-electron-brain-e2e.js` 在第一次“保存会话”即失败；Brain 日志栈为 `tools.py::_parameters_schema` 对 `param[:3]` 执行时收到 dict，抛出 `KeyError: slice(None, 3, None)`；Fake Provider 请求数为 0。
- **根因证据：** `brain/tools.py::_parameters_schema` 与 Creator 插件契约要求 `TOOL_SPECS.params` 使用 `(name, description, required, type)` 元组列表；E2E 生成的临时 `smoke_ping` 插件错误地声明为 dict 列表。
- **修改范围：** 只修正 `client/tools/test-electron-brain-e2e.js` 临时插件 fixture 为正确 tuple 参数契约，并保留只输出请求序号/stream 标记的诊断；没有更改 Brain 或 Electron 业务代码。
- **前后对照：** 修复前 E2E FAIL（请求未发出）；修复后 E2E 8/8 PASS，报告日志在 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-electron-brain-e2e-final.log`。另一次仅为检查异步稳定性的初跑发现会话列表断言竞态，保留观察结果并进入 Round 2。

### Round 2 — 等待 Electron 会话列表恢复后再断言

- **修改前表现：** Brain/Electron 重启后欢迎页已可见，但 renderer 的异步会话列表请求尚未完成；测试立即断言 12 条记录时偶发得到 0。原始观察是 FAIL (`0 !== 12`)，其临时测试目录按 harness 默认清理，原始 stack 保存在本任务工具输出中。
- **根因证据：** E2E 只等待欢迎页，不等待 `[data-session]` 列表加载；欢迎页可先于 session API 响应渲染。不是聊天或持久化业务缺陷。
- **修改范围：** 在最终冷启动断言之前增加 `waitFor("document.querySelectorAll('[data-session]').length===12", ...)`，等待明确的 UI 后置条件。未延长固定 sleep，也未放宽数量断言。
- **修改后结果：** 修复后以默认安全清理运行两次，均退出码 0、8/8 PASS；日志分别为 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-electron-brain-e2e-final.log` 和 `phase2-electron-brain-e2e-stability.log`。两次均覆盖工具调用、流事件、会话隔离、Creator、Brain 重启和 Brain 进程断开恢复。

### 全仓关联回归与续接闭环

- `python scripts/agent_eval.py --state-dir <本机隔离状态> run --suite regression --label phase2-regression`：12/12 案例 PASS，pytest 240 passed / 3 skipped，报告 `run-20261020T104957-72a4dc41.json`。
- `... run --suite security --label phase2-security`：1/1 案例 PASS，pytest 29 passed，报告 `run-20261020T105048-084cb27f.json`。
- `... run --suite full-local --label phase2-full-local`：全仓 1,388 passed / 19 skipped / 0 failed、47 subtests passed、114.01 秒；1 个聚合案例 PASS，报告 `run-20261020T105101-97df30a8.json`。19 项 skip 未计入通过，原因保存在该报告 `stdout`。
- `node --test <8 个声明 node:test 的 client/tools 文件>`：56 passed / 0 failed / 0 skipped。
- 合成任务在独立状态目录中完成 `OPEN → BLOCKED → IN_PROGRESS → READY_FOR_VERIFY → COMPLETED`；blocked resume 没运行命令；同一个 `provider-contract` 案例的前后报告为 PASS → PASS、`same_conditions=true`、classification `UNCHANGED`。该任务只测任务状态机，并非产品缺陷或 fix。
- `npm ci`、Node/Python 工具链和 Fake Provider 均位于本机隔离目录；运行报告没有进入 Git。最终包和其 lifecycle 实际结果另见 `phase2-real-runtime-acceptance.md` 及本机验收 JSON。

### P1 状态

- **AG-002：部分验证，仍 P1。** 真实 Electron SSE 增量/终态、Brain 进程退出的 interrupted 恢复和组件级 cursor gap/snapshot 测试有证据；真正网络断线后的 UI 自动重连、Brain event cursor gap 与持续压力没有端到端证明。
- **AG-003：部分验证，仍 P1。** 成功/插件失败、会话/Brain/Electron 重启及 Brain 断开后 UI 解锁有 Fake Provider/Electron 结果；长任务取消、超时、窗口退出后的完整状态矩阵待验证。
- **AG-005：待验证，仍 P1。** 自动 Provider/Agent 契约通过；外部非幂等副作用在连接中断与响应不确定状态下的端到端不重放尚无证据。
- **AG-008：部分验证，仍 P1。** 本轮建立 Windows 隔离工具链并运行候选包 lifecycle（如安装/重启/卸载保留数据通过）；旧版升级及升级失败回滚仍需单独结论。没有声明 P1 全部关闭。
- 本阶段未修复 Vigil 业务 Bug，未接入真实 Provider 凭据，不触碰用户数据。Windows 候选包为非正式验收产物，SHA-256/清单仅记录在本机验收 JSON；不发布、不覆盖正式安装。

### Round 3 — Windows 候选安装验收夹具及启动竞态

- **修改前表现：** `scripts/build_release.py --candidate` 成功构建配对 Windows NSIS + portable 候选包，`build_release.py --verify --candidate` 通过，静默安装退出码 0，真实 sidecar Brain health 为 ready；首次聊天验收失败。
- **根因证据：** 首次安装 profile 脱敏配置检查确认 `/api/models/apply` 已保存 loopback base/key，但原验收夹具发送 `model` 而接口字段契约是 `chat`。修正字段后，单独复跑仍观察到一次没有创建 display turn 的首次启动竞态；Brain/Electron 启动期间 renderer 初始 ping 可能触发 chat 页重新渲染，测试此时提交消息，初始化会丢掉输入。安装后用例原本只等待欢迎页和 composer DOM，没有等 `API.state.conn.ok`。
- **修改范围：** `client/tools/test-fresh-installed-runtime.cjs` 使用 `/api/models/apply` 的 `chat` 字段，等待 renderer 与 bundled Brain 连接成功、填入内容后等待发送按钮 enabled，再发消息；加入脱敏诊断和 Fake Provider 必须收到 `P8_FRESH_CHAT_SENTINEL` 的断言。`scripts/test_windows_install.py` 使用 release-manifest 里的候选版本而不是硬编码 `1.1.0`，可把报告写到 checkout 外。`scripts/test_windows_candidate_lifecycle.py` 每份报告使用独立日志子目录并拒绝覆盖已存在报告。
- **修复后实测：** 对新的临时 profile 调用已安装候选 `Vigil.exe`，runtime harness exit 0；sidecar Brain ready，Fake Provider 收到 1 个带 sentinel 的真实聊天请求，renderer 显示回复并创建会话。结果日志：`%LOCALAPPDATA%/Vigil/stage2-state/phase2-first-boot-isolated-profile.log`。
- **失败证据保留：** 首次安装器 lifecycle 报告为 FAIL（安装成功，但 chat 首次启动 race），保存在 `%LOCALAPPDATA%/Vigil/stage2-state/phase2-candidate-lifecycle-failure-initial.json` 与 `lifecycle-failure-initial-logs/`；后续正式候选完整安装/升级结果写入独立 `phase2-acceptance-final.json`，不覆盖这份失败记录。
- **回归与范围：** 失败/根因/修复均为发行和集成测试夹具，没有改 Brain、Electron 业务实现；最终候选以新提交重建后再执行 fresh install、restart、uninstall retention 和旧版 upgrade/rollback。运行的版本、报告及完整范围以最终 JSON 为准。
