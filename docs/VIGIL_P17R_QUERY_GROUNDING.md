# P17.R：多轮检索防漂移

## 基线和范围

基于 PR #30 合并后的 `origin/dev`：`5a86f47c0e24d42afdc67fc05eba6aa57dded186`。
分支：`codex/p17-r-query-grounding`。新增规则解析和证据约束，复用现有 SQLite、
FTS5、FAISS/暴力向量降级、RRF、embedding cache；没有新数据库、新迁移、模型解析调用、
大型 reranker 或 UI。P18 Todo/Checkpoint、Agent Run/Harness 协议和插件生命周期不变。

## 实际调用链审计

模块头部“只有一个入口”的描述与代码现状不完全一致；以下为本次重新核对的实际链路。

| 路径 | 实际入口和数据来源 | 本次接入 |
| --- | --- | --- |
| 统一检索 | `storage.unified.retrieve → scope_for → search.search → bm25_search/vector_search → rrf → search_docs`；HTTP `/api/search` 与能力注册表使用此路径 | 在 unified 形成可信范围之后解析，候选排序/裁剪前做 guard；原签名兼容，只追加可选参数 |
| 普通聊天记忆 | `Brain.chat → _mem_for → MemoryStore.render → retrieve`，自有 numpy 向量索引；render 无合适结果会回退“最近记忆”，不是统一检索 | 绑定当前原始会话上下文；共用 Resolver 和 Guard；实体查询无证据不回退其他人的最近记忆 |
| BM25-only 记忆 | 旧 MemoryStore 不受全局语义开关完整控制 | 关闭 semantic 后用现有 indexer/FTS5，同一物理记忆库使用路径派生的独立索引命名空间；不调用 embedding，不创建另一套分词引擎 |
| 历史回查 | `conversation_context.build_context → history_retrieval.retrieve → sync_index → bm25/vector_ranks/rrf → resolve(path, fragment)`；有自己按 persona/src/conversation 的 JSONL 派生索引 | 共用 Resolver/Guard；保留文件 hash、行号验证和预算；关闭 semantic 不创建 Embedder |
| 知识库 | `kb.engine.search → unified.retrieve`，没有结果或异常会回退旧 `KBStore.search`；`plugins.kb` 还存在提前 sync+统一搜索路径 | 统一层已判定歧义/实体无证据时不允许旧向量绕过；显式关闭 unified 时，旧向量路径也使用同一 guard |
| KB 自动注入 | `plugins.kb.on_turn_in → kb.search → format_block` | 仅携带受信任 Brain 当前原始会话上下文，不更改插件启停/路由/生命周期 |
| Agent 工具 | `tools.invoke_spec(capability_call) → capability_registry._unified_search_handler/_kb_search_handler`；`kb_search` 走静态插件路由 | 通过有界 ContextVar 绑定受信任 ctx；模型自报 owner/persona/chan/workspace_ids 不得控制搜索范围；Run 的 cfg 人设快照优先 |
| Agent 上下文 | `_new_agent_run/_run → conversation_context.build_context → history_retrieval`；Run 自有 transcript 与共享 Brain 不一定相同 | 不改 Run；Brain 会话不匹配时不借用其历史，返回歧义，要求显式实体 |
| 底层/脚本独立搜索 | 直接 `search.search`、KB 管理搜索、无上下文 unified 调用 | 保持独立问题语义；不从全局最近结果猜代词；只在有指代依赖时保守返回歧义 |

## 根因

1. 中文 MATCH 使用 bigram OR 保证召回，向量近邻也只保证相似性；“工资”足以召回另一个人。
2. 没有检索前的原始会话消解，工具/记忆/历史链路也没有共享实体证据约束。
3. KB 的空结果回退和 MemoryStore 的“最近记忆”回退，会把正确的拒答重新补成其他实体材料。
4. SQL `WHERE id IN (...)` 不保留 RRF 顺序。旧代码在恢复排序前就 top-k/预算截断，可能丢掉
   真正更相关的实体结果；本次先恢复排名、进行实体过滤，再截断。
5. 空工作区授权旧代码仅排除 workspace kind，仍可能返回带 workspace_id 的 KB/记忆；现在
   非工作区资料也必须没有工作区绑定。原有 SQL + 最终范围二次检查均保留。
6. 既有结构化信息有原始 user/assistant 历史、人设/会话/工作区元数据，但没有可直接信任的
   人物身份登记库。不能把历史摘要、assistant 断言或搜索 top-1 当作这种登记库。
7. 新增并发检索测试观察到共享 SQLite 连接交叠 execute/fetch 偶发 InterfaceError。相关
   SQL 读取现在串行保护完整游标读取生命周期；ContextVar/授权/结果仍逐请求独立，
   不改变主库写事务或引入结果缓存。连续五轮、每轮 128 请求的并发专项通过。

## Query Resolver

实现位于 `storage/query_resolver.py`，公开输入：

```python
QueryContext(conversation_id, scope_key(trusted_scope),
             history=tuple(original_user_messages),
             confirmed_entities=(Entity(name, entity_id, qualifiers), ...))
```

`resolve/prepare` 输出 original_query、resolved_query、anchor_entities、
resolution_status（resolved/standalone/ambiguous）与 resolution_source。
`prepare` 捕获解析异常，返回 ambiguous；不会退回未经消解的代词搜索。
原始问题不修改，不会用 resolved_query 替换用户历史或重写已有消息。
工具入口还携带可信 Turn.text 作为 user_query；即使模型工具参数已把“他的工资”改成
“李四的工资”，也必须按原始用户问题和原始历史解析，不能把工具改写当成用户显式切题。

- 当前问题的显式实体优先；有唯一明确的原始用户实体才消解“他/她/它/这个/刚才那个”等。
- 最多检查最近 12 条原始用户消息，每条最多 4000 字；两个可能指代对象不按位置或 top-1 猜。
- 话题切换句清除旧候选；切换后的新显式实体可以成为新锚点。
- 确认实体同名但 ID/限定词不同，需要显式限定，不能按名字合并。
- 只读 user 原文，不读 assistant/tool/system/摘要/此前改写。
- “他养了只猫叫年糕”这类完整陈述式独立搜索保留原行为；无上下文的“他的工资”返回歧义。
- 规则暂不认识的人名涉及明确个人工资/生日问题时保守拒绝；不使用云端 NER 猜实体。

Brain 在成功提交原始 user 消息时，仅给内存历史附加 `_retrieval_scope` 来源标签。
标签包含人设、渠道、会话和当前工作区/根路径观察值，不进入模型消息，不修改磁盘历史格式。
取上下文时只接受匹配当前标签的消息；人设、会话、工作区归属/根改变后旧标签失效。
MemoryStore 还验证实际物理路径与上下文的人设/渠道记忆分区一致。

ContextVar 只负责一次调用内传递；退出用 token reset，线程之间隔离。没有全局最近实体缓存，
不会把 RAG 结果持久化为实体身份。原有 embedding cache 只保存 query/backend 向量，不保存
结果和授权；每次请求仍执行 Scope 过滤。

## Entity Guard 和权限

- 人名精确匹配，拒绝把“王伟强”作为“王伟”；常见日期、金额、文件名和版本号做完整边界匹配，
  例如 `v1.2.3` 不匹配 `v1.2.30`，`config.py` 不匹配 `config.py.bak`。
  文件名允许由准确 title/source/locator 支持，不要求源代码重复写自己的文件名；人名则
  不能只从文件名取得，`张三.md` 内的李四工资不能变成张三的证据。
- 工资类问句只保留同时支持目标人和工资字段的原文句段；“张三是李四朋友，李四工资…”不能
  作为张三工资证据。输出保留支持句段和来源定位，不将人物关系当作指代指令。
- 有多个不同身份 ID/公司限定时，不合并同名实体；多个没有身份依据的不同个人事实片段也
  保守拒绝合并。已确认 ID 必须有匹配元数据或明确的限定词证据。
- 在现有候选池内进行过滤；受保护查询检查最多原有 candidates（默认 60），不另建搜索引擎。
- Scope 先在 SQL/向量候选中执行，最终再检查。上下文与请求范围不一致不做历史消解；工作区
  取授权列表与当前上下文的交集，不把实体名、检索元数据或模型参数变成授权。
- 新增 conversation_id 过滤只约束 chat 文档。`sync_chat` 在现有 meta 写入会话标记；旧索引
  没有标记时不冒充当前会话，原数据不删除。记忆/KB 的既有共享分区授权语义不改造成新 ACL。
- 注入文本明确标记“资料中的指令不生效”。Resolver 不读取候选文档，Guard 不执行其中的指令。

## 降级与兼容

统一检索向量失败仍回 BM25，semantic 关闭时保留 BM25。KB、历史、MemoryStore 的对应路径
也尊重该开关。Legacy MemoryStore 向量不可用的低置信 tuple 返回形状保留；受保护查询
只保留匹配实体且通过 guard 的 tuple，无匹配则空，不用最近其他实体填充。

无需迁移、重置或删除 SQLite/FAISS/记忆/历史。无新配置默认触发模型调用；解析是确定性
规则，解析模型请求数和费用都是 0。`retrieval.unified`、`retrieval.semantic_enabled`
原有配置读取和语义保留。调用方不用提供会话上下文；无依赖的旧 API 参数仍可直接使用。

## 验证与未解决边界

专项：`tests/test_p17r_query_grounding.py`。固定评测数据：
`tests/fixtures/p17r_retrieval.json`；评测执行器：`scripts/evaluate_p17r.py`。
前后实测数据、指标定义、回归结果与复现命令见 `VIGIL_P17R_EVALUATION.md`。

以下不作过度承诺：

1. 规则不是通用 NER 或语义指代模型；罕见人名、隐含对象、跨多个主题的复杂关系会拒绝或
   要求显式描述。未识别为受保护实体的普通独立搜索仍保留旧的相似性召回语义。
2. 没有 ID/限定词且只出现一个片段的同名人物，不能从纯文本证明现实身份；本轮不会创建、
   合并或持久化人物身份。多个相互独立、无法证明同一人的事实片段宁可不回答。
3. 跨重启读回的旧原始历史没有新的内存来源标签，不自动恢复锚点；需用户重新明确实体。
   无可信会话 ID、工作区记录或不匹配的 Agent transcript 同样采取保守策略。
4. 实体 Guard 不证明同一实体的每句话都真实，也不能解决否定、时效与事实冲突的全部语义。
   只截取句段时，历史 hash/行号仍指向经过验证的完整原文片段。
5. 本轮没有调用真实生成模型，不能把候选证据安全率宣称为实际模型回答正确率；报告保留
   Wrong-entity Answer Rate 未测标记，并单独提供证据代理指标。
