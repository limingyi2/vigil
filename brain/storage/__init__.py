# -*- coding: utf-8 -*-
"""P17 存储层：双 SQLite 数据库 + 统一混合检索。

- `storage.db`     —— 双库基础设施（主库 / 诊断库）、连接管理、迁移、备份
- `storage.diag`   —— 诊断写入：脱敏、大小控制、自动清理。**故障不阻塞业务**
- `storage.search` —— 统一混合检索：FTS5/BM25 + Embedding + RRF
- `storage.indexer`—— 增量索引：文件改了/删了要及时跟上
- `storage.legacy_import` —— 从现有 JSON/JSONL/NumPy/SQLite 迁移数据

主库 `vigil.db` 装业务数据；诊断库 `vigil_logs.db` 装可丢的诊断数据。
两者互不依赖：诊断库坏了不阻塞聊天，诊断库整个删掉业务照跑。
"""
from . import db  # noqa: F401

__all__ = ["db"]
