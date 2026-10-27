# 混合检索

Vigil 通过 `brain/storage/unified.py` 提供统一检索入口。聊天记录、长期记忆、知识库和授权工作区可以共用检索流程，但仍受各自的来源、人设、会话和工作区范围约束。

## 流程

1. 根据调用上下文构造检索范围并解析查询。无法确认的指代不会自行扩大搜索范围。
2. 使用 SQLite FTS5 执行关键词检索。
3. 语义检索启用且嵌入后端可用时，再取向量检索结果。
4. 使用 Reciprocal Rank Fusion（RRF）融合排名，按条数与字符预算返回结果。

```mermaid
flowchart LR
    Query[查询与调用范围] --> Scope[范围检查与查询解析]
    Scope --> FTS[SQLite FTS5]
    Scope --> Vector[可选向量检索]
    FTS --> RRF[RRF 排序]
    Vector --> RRF
    RRF --> Result[受预算约束的结果]
```

## 数据与索引

| 模块 | 内容 |
| --- | --- |
| `storage/db.py` | 数据库连接、表结构与迁移 |
| `storage/indexer.py` | 聊天、记忆、知识库和工作区的索引写入 |
| `storage/search.py` | 全文检索、查询嵌入缓存、向量查询和 RRF |
| `storage/vector.py` | 嵌入后端、向量记录与 FAISS ANN |
| `kb/engine.py` | 知识库导入、删除、重建和结果格式 |
| `retrieval_adapters.py` | 各调用入口与统一检索之间的适配 |

知识库正文、元数据、全文索引与向量记录通过主存储管理。FAISS 索引是可重建的检索索引，不应成为资料的唯一副本。旧索引的度量或元数据不兼容时，需要按实现规则重建。

## 分数

RRF 使用排名而不是原始向量相似度。当前 `RRF_K` 为 60，每个命中的排名列表贡献 `1 / (60 + rank)`。

知识库接口将两路融合分数归一化到 0–1 范围后应用 `min_score`，并保留原始 `rank_score`。单路第一名的归一化得分为 0.5；两路都排第一时为 1。这个分数表示排序信号，不是语义相似度或答案正确率。

调整门槛时，应同时检查纯关键词与混合检索结果，避免把仅有关键词命中的有效资料全部过滤。

## 开关与降级

`retrieval.semantic_enabled=false` 关闭语义检索，全文检索仍可工作。嵌入后端不可用时，接口按实际可用通道降级，不应报告已经完成向量化。

本地嵌入模型、远程嵌入服务和 FAISS 索引的资源消耗与资料规模有关。容量与延迟需要在目标机器和数据集上测量，不能从索引类型直接推断可支持的数据量。

## 正确性检查

删除和清空使用精确范围与来源匹配，不能将文件名中的 `%` 或 `_` 当成 SQL 通配符。向量查询还需要核对模型、维度与调用范围；ANN 结果在返回前仍受范围过滤。

相关回归包括 `tests/test_kb.py`、`tests/test_p17_dual_db_hybrid.py`、`tests/test_p17r_query_grounding.py` 和 `test_rag_acceptance.py`。FAISS 验收应明确安装并使用真实后端，不能用模拟向量结果替代 ANN 验收结论。

历史评测保留在 [evaluation/](evaluation/README.md)。比较数字时需要同时列出提交、模型、数据集、硬件和测量方法。
