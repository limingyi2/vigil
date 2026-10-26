# -*- coding: utf-8 -*-
"""本地知识库（RAG）。

原来的 `kb_core.py` / `ingest.py` / `search.py` 是一份能跑但没接线、
且路径全写死在 D 盘的原型。知识库正文、全文索引与向量统一保存在 `vigil.db`；
`data/kb/settings.json` 仅保留知识库界面设置。

| 问题 | 现在怎么解决 |
|---|---|
| 检索数据落独立文件目录 | 正文、FTS 和向量统一保存在 `vigil.db`；仅知识库设置仍放在 `brain/data/kb/settings.json` |
| 人设间资料串用 | 当前 PC 客户端按人设分区，来源固定为 `pc` |
| 只能 CLI 手动跑 | 插件 `brain/plugins/kb.py` 声明 ROUTES → `/api/plugin/kb/*`，不改 server.py |
| 检索结果没进对话 | 插件 `on_turn_in` 每轮注入，另给一个 `kb_search` 工具 |
| 独立知识库向量后端 | 复用主库统一检索后端，不维护第二份知识库向量文件 |

对外只用下面这几个函数，内部模块别直接 import（除了测试）。
"""
from .engine import (clear, delete_doc, format_block, ingest_path,
                     ingest_text, list_docs, reindex, scope_of, search, stats)
from . import config

__all__ = [
    "search", "ingest_text", "ingest_path", "list_docs", "delete_doc",
    "reindex", "clear", "stats", "format_block", "scope_of", "config",
]
