# -*- coding: utf-8 -*-
"""本地知识库（RAG）。

原来的 `kb_core.py` / `ingest.py` / `search.py` 是一份能跑但没接线、
且路径全写死在 D 盘的原型。这个包把它产品化：

| 问题 | 现在怎么解决 |
|---|---|
| 路径写死 `D:\\zhixia_kb` | 走 `paths.KB_DIR`（`brain/data/kb/`），模型缓存仍可留在 D 盘 |
| 全局一个 collection，换人设串味 | 按「人设 × 渠道」分区，与记忆库同一套 `paths.scope_name` 规则 |
| 只能 CLI 手动跑 | 插件 `brain/plugins/kb.py` 声明 ROUTES → `/api/plugin/kb/*`，不改 server.py |
| 检索结果没进对话 | 插件 `on_turn_in` 每轮注入，另给一个 `kb_search` 工具 |
| 依赖 Chroma/torch | 默认复用 `memory_store_v2.embed_texts`（云端 bge-m3，失败降级 fastembed），零新依赖；想零成本本地跑再切 `backend=local` |

对外只用下面这几个函数，内部模块别直接 import（除了测试）。
"""
from .engine import (clear, delete_doc, dir_of, format_block, ingest_path,
                     ingest_text, list_docs, reindex, reset_cache, root,
                     scope_of, search, stats, store_for)
from . import config

__all__ = [
    "search", "ingest_text", "ingest_path", "list_docs", "delete_doc",
    "reindex", "clear", "stats", "format_block", "scope_of", "dir_of",
    "store_for", "root", "reset_cache", "config",
]
