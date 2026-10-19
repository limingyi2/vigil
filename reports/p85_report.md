# Vigil P8.5 实现与验收

日期：2026-10-19。基线：最新 `origin/dev` = `7d961619608c54053e198ef283e00b0a93b56ca6`，P8.4 实现提交 `320d17c`。独立 worktree：`F:\vigil-p85`，分支 `codex/p8-5-context`。原工作区 `F:\zhixia` 的未提交文件、用户配置、凭据和真实聊天数据均未改动；没有修改 master、委派 dispatcher/router/contracts 或 recap 存档，也没有运行混合委派 30 题评测。

## 根因和改动文件

| 文件 | 改动及原因 |
|---|---|
| `brain/conversation_context.py` | 统一动态预算，按本地/云端选择近期历史；修复旧裁剪逻辑不能可靠保留最新消息的问题。摘要改为严格 JSON schema，校验必需字段、真实来源、明确决定原文与精确标识符。按输入上限每轮处理一批，超长消息保存片段进度；完整保留 pending，超过旧软上限不再丢弃。候选状态原子写入成功才提交，失败保留旧摘要、pending 和水位线。旧自然语言摘要保留在升级状态；旧结构化摘要按完整段落存档，不截断数字/SHA/路径。保留独立的用户原文证据账本，时间和来源可追踪，冲突版本都保留、较新明确决定优先显示。 |
| `brain/history_retrieval.py`（新增） | 仅从 `_log_for()` 的真实 JSONL 建立独立、隔离的增量索引，持久化字节游标、行号、角色、时间、片段 ID、内容和记录哈希。BM25 与向量余弦排名经 RRF 去重融合，注入前回读原文校验。Embedding 为独立单后端实现，不导入长期记忆/知识库的多后端回退链路。 |
| `brain/brain.py` | 本地 provider 构造失败禁止云端兜底；实际输出上限与预留保持一致；固定输入超容量时明确返回错误；清空历史同时清除内存历史、摘要和检索索引。继续复用 draft/stream 的现有 builder。 |
| `brain/server.py` | Agent run 的工具定义与已完成 run 历史在同一个 builder 中计预算，记录 `last_context_info`；创建 run 不发摘要请求。 |
| `brain/agent_runs.py` | 给 Agent 请求设置与上下文预留一致的输出上限，不改派发协议或工具流程。 |
| `brain/config/config.example.json` | Ollama 示例改为 12288；添加预算、分批和历史检索的可选配置。未修改实际 config.json。 |
| `brain/_test_p84_context.py` | 18 项原验收保持执行；FakeProvider 改用带真实合成来源的结构化协议。旧“超长摘要截断”验收升级为“拒绝超长输出并完整保留 pending”。 |
| `tests/test_p85_context_history.py`（新增） | 38 项专门验收；隔离 VIGIL_HOME/数据目录、FakeProvider、合成聊天、socket 网络拦截。包含 P8.4 的子进程验收入口，现有 CI `pytest tests` 会实际执行，无需改 workflow。 |
| `client/package.json`、`client/package-lock.json`、`client/version.json` | 桌面版本 1.1.5 / code 26；修正 lockfile 原先仍为 1.1.0 的版本元数据。 |
| `reports/p85_report.md`、`reports/p85-evidence/*` | 本报告、真实模型观察和机器可读测试结果；安装包交付信息追加在单独交付记录。 |

## 预算与行为

| 场景 | 实际验收 |
|---|---|
| 本地 12288、输出 2048、安全余量 256 | 输入工作上限 9000，积极压缩阈值 7500，目标 6000；默认近期 4 个来回，预算不足时减少。系统、工具、装饰后的当前用户输入、摘要、历史证据、近期历史共用预算。 |
| 云端容量 200000 | 工作上限 131072，阈值 98304，目标 65536；默认近期 8 个来回。 |
| 云端实际容量 16384 | 输入工作预算自动降为 14080。 |
| 云端容量完全未知 | 即使工作预算/default_capacity 配成 131072，仍按保守容量 8192，输入上限 5888。 |
| 本地摘要批次 | 系统提示、schema 与新增批次合计不超过配置上限 4096 Token 估算值，并另行保留输出与余量；每轮最多一批。 |
| 历史检索 | 历史意图或精确事实需求触发；BM25 Top 10、向量 Top 10 经 RRF；默认最多 4 段，证据预算本地 800、云端 2000，进一步受剩余总预算约束。 |
| 固定输入已经超过安全预算 | 不截断用户指令，不再靠历史挤占输出空间；入口返回明确的容量错误。 |

Token 预算是启发式估算，不冒充真实 tokenizer；真实 usage 单独统计。索引/摘要失败不会删除或改写原始聊天记录。检索和摘要块明确声明历史引文不构成当前指令。

## 真实测试结果

- P8.5：**38/38 通过**，包括 P8.4 的 **18/18** 子进程验收。额外定向执行 P8.5 + Agent run/HTTP：**71/71 通过**。
- 项目全量 `python -B -m pytest tests brain -q --tb=short -ra`：**1327 收集，1310 通过，17 跳过，0 失败，0 错误**。详见 `p85-evidence/full-regression.xml`。跳过项为已有的 Windows junction/symlink/mkfifo 权限、未随仓库分发的旧母本/手机资源、无本地 venv 等条件；未将它们计为通过。
- 前端 CI 对应 3 组 Node 回归：**24/24 通过**。
- Python 全仓编译与 `git diff --check`：通过（发布前再次检查版本文件）。
- 20/50/100/200 轮合成对话，在第 5/30/70 轮埋入数字、日期、版本、路径及 SHA，存在的 **9 个历史回查样本全部恢复原文**；单独 SHA/路径/端口/版本 BM25 命中通过。压缩前后原始日志字节及 SHA-256 相同。
- 语义改写命中、RRF 排序去重、向量模型/维度切换在 Fake Embedding 后端通过；本地适配器 HTTP seam 只允许 loopback，socket 全部阻断外部网络；缺模型、超时、无效维度均降级 BM25。
- JSON 无效、缺字段、伪造来源、非原文禁止事项、模型超时、候选状态写盘失败、实际 `os.replace` 失败均验证旧状态保留。
- 516 条 pending 不丢失，按 4096 上限逐批处理；单条超长消息通过片段水位恢复，重复原文映射不同来源行。重启恢复摘要、索引及未完成 pending。

全量回归保留了失败过程：第一次包含编译生成的插件 `__pycache__` 与旧版测试夹具问题；第二次因给整个旧测试集强制设置空 VIGIL_HOME/UTF-8 导致预置人设、插件和 Windows 子进程解码失败。缓存已无损移入 worktree 临时目录；最终恢复既有全量测试环境，用 `-B` 避免生成目录污染。新增测试仍各自强制隔离临时 VIGIL_HOME。最终全量是真实零失败结果，未修改旧测试断言来掩盖这些环境错误。

## 真实 Ollama 试跑（与 FakeProvider 分开）

本机 `ollama list` 仅列出 `qwen27b-efficientthink:latest`（13GB），没有专用 Embedding 模型。检查现有配置只读取 Embedding 是否配置的元信息，不输出 Key；现有云端 Embedding 有配置，但没有本地历史 Embedding 配置。默认 Hugging Face 缓存未发现 BGE/Embedding 缓存，旧知识库代码引用的 `D:\zhixia_kb\models` 不存在。未下载新模型，未改实际配置。

使用该真实本地模型、临时 VIGIL_HOME、4 条合成原始消息试跑：**20.28 秒**，`num_ctx=12288`，结构化摘要成功保存，pending 从 2 清为 0；真实 usage 输入 **389**、输出 **401** Token。包括摘要 schema 的批次估算 **951** Token；最终主上下文估算 **483**，输出预留 **2048**。BM25 命中 **2** 条，端口 **18787** 原文恢复，原始存档仍为 **4** 行。详见 `p85-evidence/ollama.json`。

第一次真实试跑模型返回空事实数组，被来源验证拒绝，旧状态保持；随后 schema 强制至少一条事实和来源，复验成功。没有为提高成功率接受不合格摘要或调用云端。

## 已知限制

1. **本机本地向量检索尚未启用**：明确为 `vector_available=false`、`vector_reason=embedding_not_configured`，使用 BM25。配置已安装的本机 Ollama Embedding 模型后才启用向量分支；本地 endpoint 限 loopback，并检查模型已安装，绝不自动下载或云端兜底。
2. 向量初次暖索引按每次触发默认 24 段推进，诊断记录覆盖数与 `vector_status=partial/ready`；未编码段仍有完整 BM25 检索。模型/端点或维度不兼容时更换/禁用向量分支，不混算。
3. 云端预算已通过 FakeProvider 验证；没有消耗真实云端 API 做性能或问答评测。真实本地验证是小规模 smoke，**不能据此声称通用历史问答准确率达到 95%**；合成精确事实回查的 9/9 成绩也不等同于真实生成式问答准确率。
4. 摘要按轮次同步执行，最多增加配置的超时等待。单个无分隔符且本身超过批次预算的超长标识符不会被截断，批次会明确失败并保留 pending，可继续用原文索引回查。
5. 待压缩消息及证据账本完整保存在本地；`pending_max_messages` 为兼容的软压力阈值，不能授权丢弃内容。摘要/索引均是派生数据，长期存储空间随历史增长。

安装包、构建源提交、大小、SHA-256、manifest 交叉验证及 dev 推送结果见交付记录 `reports/p85_delivery.md`。
