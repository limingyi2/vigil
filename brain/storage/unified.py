# -*- coding: utf-8 -*-
"""P17 统一检索入口：上层只调这里。

现有的记忆检索、知识库检索、历史检索、工作区检索各有各的实现和调用点。
这个模块把它们收敛到一个门面后面，让上层（brain.py / kb 插件 / Agent 工具）
只需要知道 `retrieve()` 一个函数。

## 收敛策略：不推翻，只接管

- **旧实现全部保留**。`memory_store_v2` / `kb` / `history_retrieval` 的代码
  一个字节都没改，仍然可用。
- 新增的统一检索**默认开启**，但任何一条链路失败都**自动退回旧实现**。
- 通过配置 `retrieval.unified` 可以整体关掉，退回纯旧行为。

这符合「不借机重构无关功能」的要求：改的是"入口"，不是"实现"。

## 隔离边界怎么算

检索范围 = **人设 × 渠道 × 身份(owner)**。

- `owner` 来自当前会话的用户标识
- `src` 是渠道（wechat / wechat_group / pc）
- `persona` 是当前人设 key
- `workspace_ids` 是**当前会话已授权的工作区**

**没授权的工作区文件一条都不返回**。这在 SQL 层保证，不是拿回来再筛。
"""
import os
import time

from . import db
from . import indexer
from . import search
from . import vector


def _cfg(api_config=None):
    """读检索配置。"""
    cfg = {}
    try:
        import paths
        path = paths.CONFIG_PATH
        if os.path.exists(path):
            import json
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
    except Exception:
        cfg = {}
    section = cfg.get("retrieval") if isinstance(cfg.get("retrieval"), dict) else {}
    return cfg, (section or {})


def unified_enabled(api_config=None):
    """统一检索是否开启。默认开。"""
    _, section = _cfg(api_config)
    return section.get("unified", True) is not False


def scope_for(owner="", src="", persona="", chan="", workspace_ids=None):
    """构造检索范围。**这是隔离边界的唯一出处。**"""
    scope = {}
    if owner:
        scope["owner"] = owner
    if src:
        scope["src"] = src
    if persona:
        scope["persona"] = persona
    if chan:
        scope["chan"] = chan
    if workspace_ids is not None:
        scope["workspace_ids"] = list(workspace_ids)
    return scope


def retrieve(query, owner="", src="", persona="", chan="", workspace_ids=None,
             kinds=None, top_k=6, budget_chars=2400, api_config=None,
             backend=None):
    """统一检索。返回 (results, info)。

    `results` 是 SearchResult 列表（可能为空）。
    `info` 含 `method` / `hits` / `vector_available` 等诊断字段。

    **任何内部异常都不抛** —— 检索失败返回空结果，聊天照常。
    """
    info = {"method": "none", "hits": 0, "unified": unified_enabled(api_config),
            "error": ""}
    try:
        if not unified_enabled(api_config):
            info["method"] = "legacy"
            return [], info
        scope = scope_for(owner, src, persona, chan, workspace_ids)
        backend = backend or _backend(api_config)
        results, detail = search.search(query, scope=scope, kinds=kinds,
                                        top_k=top_k, budget_chars=budget_chars,
                                        backend=backend)
        info.update(detail)
        return results, info
    except Exception as exc:
        info["error"] = type(exc).__name__
        return [], info


def _backend(api_config=None):
    """向量后端。配置读不到就返回 none（只跑 BM25）。"""
    try:
        if api_config:
            return vector.backend_from_config(api_config)
        return search._default_backend()
    except Exception:
        return vector.EmbeddingBackend(mode="none")


# --------------------------------------------------------------------------
# 结果格式化（给模型看的纯文本，不是 JSON）
# --------------------------------------------------------------------------

_SOURCE_LABEL = {
    search.SCOPE_CHAT: "聊天记录",
    search.SCOPE_MEMORY: "记忆",
    search.SCOPE_KB: "知识库",
    search.SCOPE_WORKSPACE: "工作区文件",
}


def format_results(results, max_chars=2000, per_item=500):
    """把检索结果拼成一段给模型读的文本。空结果返回 ""。"""
    if not results:
        return ""
    lines = []
    used = 0
    for item in results:
        label = _SOURCE_LABEL.get(item.kind, item.kind or "资料")
        # `origin` 正常是渠道名列表；万一是字符串也别逐字拆开
        origin = item.origin
        if isinstance(origin, str):
            origin = [origin] if origin else []
        origin_text = "＋".join(str(o) for o in origin) if origin else ""
        head = "【%s｜%s%s】" % (label, item.title or item.source or "片段",
                                "｜" + origin_text if origin_text else "")
        body = (item.body or "").strip()[:per_item]
        block = "%s\n%s" % (head, body)
        if used + len(block) > max_chars:
            break
        lines.append(block)
        used += len(block)
    return "\n\n".join(lines)


def scope_ids(owner="", src="", persona=""):
    """当前会话**已授权**的工作区 id 列表。"""
    try:
        import workspaces
        rows = workspaces.list_for(owner, src, persona)
    except Exception:
        return []
    return [r.get("id") for r in (rows or []) if r.get("id")]


# --------------------------------------------------------------------------
# 索引同步（增量）
# --------------------------------------------------------------------------

def sync_all(owner="", src="", persona="", chan="", include_workspace=True):
    """把所有来源同步进检索索引。返回各部分统计。

    这是**增量**的：聊天走行号游标，文件走 mtime+hash 比对。
    """
    out = {}
    try:
        import paths
        # 聊天
        if src:
            chat_path = paths.chat_log_for(src)
            out["chat"] = indexer.sync_chat(chat_path, owner=owner, src=src,
                                            persona=persona, chan=chan)
        # 记忆
        out["memory"] = sync_memory(owner, src, persona, chan)
        # 知识库
        out["kb"] = sync_kb(owner, src, persona, chan)
        # 工作区
        if include_workspace:
            out["workspace"] = sync_workspaces(owner, src, persona, chan)
    except Exception as exc:
        out["error"] = type(exc).__name__
    return out


def sync_memory(owner, src, persona, chan):
    try:
        import memory_store_v2
        store = memory_store_v2.get_default_store(persona or None, src or "app")
        items = store.list_items()
    except Exception:
        return {"indexed": 0}
    return indexer.sync_memories(items, owner=owner, src=src, persona=persona, chan=chan)


def sync_kb(owner, src, persona, chan):
    """知识库语料块 → 检索索引。

    块只能从 `kb.store_for(...).chunks()` 拿。`kb.list_docs()` 只给文档清单
    而且字段名是 `name` 不是 `source` —— 拿它去取块会一条都取不到
    （之前这里调的 `kb.load_chunks` 在仓库里根本不存在，异常被吞掉，
    结果是知识库永远同步不进统一索引，检索时知识库看起来"没有内容"）。
    """
    try:
        import kb
        chunks = kb.store_for(persona or "", src or "app").chunks()
    except Exception:
        return {"indexed": 0, "error": "kb_unavailable"}
    if not chunks:
        return {"indexed": 0}
    return indexer.sync_kb_chunks(chunks, owner=owner, src=src,
                                  persona=persona, chan=chan)


def sync_workspaces(owner, src, persona, chan):
    """只同步**已授权**的工作区。"""
    try:
        import workspaces
        rows = workspaces.list_for(owner, src, persona)
    except Exception:
        return {"indexed": 0}
    changed = 0
    scanned = 0
    for row in rows or []:
        path = row.get("path")
        wid = row.get("id")
        if not path or not wid:
            continue
        result = indexer.sync_workspace(wid, path, allowed_roots=[path],
                                        owner=owner, src=src, persona=persona, chan=chan)
        changed += result["changed"]
        scanned += result["scanned"]
    return {"indexed": changed, "scanned": scanned}


def reindex_embeddings(doc_ids=None, api_config=None, backend=None):
    """给索引里的文档补向量。"""
    conn = db.connection("main")
    if doc_ids is None:
        rows = conn.execute("SELECT id FROM search_docs").fetchall()
        doc_ids = [r[0] for r in rows]
    backend = backend or _backend(api_config)
    result = search.index_embeddings(doc_ids, backend=backend)
    if result.get("ok") and result.get("model"):
        search.drop_incompatible_embeddings((result["model"], int(result.get("dim") or 0)))
    return result


def status(owner="", src="", persona="", chan=""):
    """检索层概况。给面板/插件看。"""
    out = {"unified": unified_enabled(), "db": {}, "index": {}, "vector": {}}
    try:
        conn = db.connection("main")
        out["db"] = {
            "main": {"path": db.main_db_path(), "version": db.current_version("main"),
                     "healthy": db.healthy("main")},
            "logs": {"path": db.logs_db_path(), "version": db.current_version("logs"),
                     "healthy": db.healthy("logs")},
        }
        row = conn.execute("SELECT COUNT(*) FROM search_docs").fetchone()
        out["index"] = {"docs": int(row[0] or 0)}
        by_kind = conn.execute("SELECT scope_kind, COUNT(*) FROM search_docs GROUP BY scope_kind").fetchall()
        out["index"]["by_kind"] = {r[0]: int(r[1]) for r in by_kind}
    except Exception as exc:
        out["db"]["error"] = type(exc).__name__
    try:
        out["vector"] = search.embedding_status()
        backend = _backend()
        ok, reason = backend.available()
        out["vector"]["backend_ok"] = ok
        out["vector"]["backend_reason"] = reason
    except Exception:
        pass
    try:
        from . import diag
        out["diagnostics"] = diag.stats()
    except Exception:
        pass
    return out
