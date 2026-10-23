# -*- coding: utf-8 -*-
"""P17 统一混合检索：FTS5/BM25 + 向量 + RRF 融合。

这是**唯一的检索入口**。记忆、知识库、聊天历史、工作区文件都从这里走，
上层（brain.py / kb 插件 / Agent 工具）不再各自搞一套。

## 融合方式：RRF（Reciprocal Rank Fusion）

RRF 只看**排名**，不看分数：

    score(d) = Σ 1 / (k + rank_i(d))

为什么用 RRF 而不是加权分数相加：

- BM25 的分数和余弦相似度**量纲完全不同**。BM25 是无界的（常见 0~30），
  余弦是 [-1,1]。直接加权要归一化，归一化又对参数极敏感。
- RRF 对量纲免疫，且**不需要调参**就能工作。k=60 是原论文的经验值，
  对绝大多数场景都够稳。

## 降级链

    BM25 + 向量 + RRF   ← 最优
      ↓ 向量失败 / 无 numpy / 未配置
    BM25 only           ← 保证聊天永远能检索

**向量通道的任何失败都不影响 BM25 通道**。这是硬要求：
检索挂了不能把聊天拖下水。

## 来源隔离

检索结果必须遵守会话、身份、人设、渠道、工作区授权边界。
实现在 `search()` 的 `scope` 参数里 —— **SQL 层面过滤**，不是在 Python 层
拿到结果后再筛。后者一旦有遗漏就是**越权泄漏**（把别的渠道的话召回来）。

## Token 预算

检索结果会整段注入模型上下文。所以：
- 按分数从高到低加，装进预算就停
- 单条结果有长度上限（避免一条巨长文档吃掉全部预算）
- 返回时带上 `truncated` 标记，调用方知道内容被裁过
"""
import json
import os
import time
import hashlib
import threading

from . import db
from . import tokenizer
from . import vector

# 检索结果单条最大字符数
MAX_SNIPPET = 1200
# RRF 常数
RRF_K = 60
# 默认返回条数
DEFAULT_TOP_K = 8
# 默认候选池大小
DEFAULT_CANDIDATES = 60

# 查询向量缓存：相同 query 不重复向量化，省 30ms/次
_QUERY_CACHE = {}
_QUERY_CACHE_LOCK = threading.Lock()
QUERY_CACHE_TTL = 3600  # 1小时
QUERY_CACHE_MAX = 1000  # 最多缓存1000条，防止内存膨胀


def _cached_query_embed(query, backend):
    """带缓存的查询向量化。命中缓存直接返回，不调模型。

    缓存键必须包含后端身份（model_tag = 模型+维度）：切换嵌入模型后，
    旧缓存的向量维度/语义空间不同，绝不能复用，否则检索结果无意义。
    """
    q = str(query or "").strip()
    if not q:
        return [], ""
    backend_tag = backend.tag() if hasattr(backend, "tag") else ""
    key = "%s:%s" % (backend_tag, hashlib.md5(q.encode("utf-8")).hexdigest())
    with _QUERY_CACHE_LOCK:
        cached = _QUERY_CACHE.get(key)
        if cached and time.time() - cached[1] < QUERY_CACHE_TTL:
            # 二次校验 tag 一致（防御性）
            if cached[2] == backend_tag:
                return cached[0], cached[2]  # (vectors, tag)
    # 缓存未命中，调模型
    vectors, tag = backend.embed([q])
    if vectors:
        with _QUERY_CACHE_LOCK:
            # 超过上限就清掉最旧的一半
            if len(_QUERY_CACHE) >= QUERY_CACHE_MAX:
                cutoff = time.time() - QUERY_CACHE_TTL / 2
                for k in list(_QUERY_CACHE.keys()):
                    if _QUERY_CACHE[k][1] < cutoff:
                        del _QUERY_CACHE[k]
            _QUERY_CACHE[key] = (vectors, time.time(), tag)
    return vectors, tag

# 检索范围种类
SCOPE_CHAT = "chat"
SCOPE_MEMORY = "memory"
SCOPE_KB = "kb"
SCOPE_WORKSPACE = "workspace"


class SearchResult(object):
    """一条检索结果。字段与前端/插件需要的对齐。"""

    __slots__ = ("doc_id", "kind", "title", "body", "source", "locator",
                 "score", "origin", "meta", "truncated")

    def __init__(self, doc_id, kind, title, body, source, locator, score=0.0,
                 origin="", meta=None, truncated=False):
        self.doc_id = doc_id
        self.kind = kind
        self.title = title
        self.body = body
        self.source = source
        self.locator = locator
        self.score = score
        self.origin = origin
        self.meta = meta or {}
        self.truncated = truncated

    def to_dict(self):
        return {
            "doc_id": self.doc_id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "source": self.source,
            "locator": self.locator,
            "score": round(float(self.score or 0.0), 6),
            "origin": self.origin,
            "meta": self.meta,
            "truncated": bool(self.truncated),
        }

    def __repr__(self):
        return "<SearchResult %s %s %.4f %s>" % (self.kind, self.doc_id[:8],
                                                self.score, self.title[:24])


# --------------------------------------------------------------------------
# 写入
# --------------------------------------------------------------------------

def upsert_doc(doc_id, kind, body, title="", source="", locator="",
               owner="", src="", persona="", chan="", workspace_id="",
               content_hash="", mtime=0.0, size=0, meta=None):
    """写入/更新一条可检索文档。

    `doc_id` 是稳定主键 —— 同一个内容重复写不会产生副本。
    """
    conn = db.connection("main")
    body = body or ""
    title = title or ""
    now = time.time()
    meta_blob = json.dumps(meta or {}, ensure_ascii=False)
    with db.write():
        row = conn.execute("SELECT rowid,content_hash,scope_kind,owner,src,persona,chan"
                           " FROM search_docs WHERE id=?", (doc_id,)).fetchone()
        if row and content_hash and row[1] == content_hash:
            # 内容与范围都没变 → 连 FTS 都不用碰。
            # 记忆/知识库每次同步都会整批重扫，不做这个短路就是每次同步把
            # 全库重新分词一遍（中文 bigram 展开后体积翻倍，很贵）。
            if (row[2], row[3], row[4], row[5], row[6]) == (kind, owner, src, persona, chan):
                return False
        if row is None:
            conn.execute(
                "INSERT INTO search_docs (id,scope_kind,owner,src,persona,chan,workspace_id,"
                "title,body,locator,source,content_hash,mtime,size,created_at,updated_at,meta)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, kind, owner, src, persona, chan, workspace_id, title, body,
                 locator, source, content_hash, float(mtime or 0), int(size or 0),
                 now, now, meta_blob))
            rid = conn.execute("SELECT rowid FROM search_docs WHERE id=?", (doc_id,)).fetchone()[0]
        else:
            conn.execute(
                "UPDATE search_docs SET scope_kind=?,owner=?,src=?,persona=?,chan=?,"
                "workspace_id=?,title=?,body=?,locator=?,source=?,content_hash=?,"
                "mtime=?,size=?,updated_at=?,meta=? WHERE id=?",
                (kind, owner, src, persona, chan, workspace_id, title, body, locator,
                 source, content_hash, float(mtime or 0), int(size or 0), now,
                 meta_blob, doc_id))
            rid = row[0]
        # FTS 影子表：内容变了要重分词，所以先删旧再插新
        conn.execute("DELETE FROM search_fts WHERE rowid=?", (rid,))
        conn.execute("DELETE FROM search_fts_trigram WHERE rowid=?", (rid,))
    _reindex_fts(doc_id, rid)
    return True


def _reindex_fts(doc_id, rid=None):
    """把一条文档写进 FTS 影子表。

    FTS 的 rowid 直接用 `search_docs` 的隐式 rowid —— 这样 JOIN 条件
    `search_fts.rowid = search_docs.rowid` 天然成立，不需要额外映射表。
    """
    conn = db.connection("main")
    if rid is None:
        row = conn.execute("SELECT rowid,title,body FROM search_docs WHERE id=?", (doc_id,)).fetchone()
        if row is None:
            return False
        rid, title, body = row[0], row[1] or "", row[2] or ""
    else:
        row = conn.execute("SELECT title,body FROM search_docs WHERE id=?", (doc_id,)).fetchone()
        if row is None:
            return False
        title, body = row[0] or "", row[1] or ""
    indexed = tokenizer.index_text(title + " " + body)
    with db.write():
        conn.execute("INSERT INTO search_fts(rowid, body, title) VALUES (?,?,?)",
                     (rid, indexed, tokenizer.index_text(title)))
        # trigram 表只索引 ASCII 片段：中文两字词 trigram 匹配不了
        # （要求 ≥3 字符），硬塞进去反而占空间。用它兜英文标识符/路径的部分匹配。
        ascii_only = " ".join(w for w in indexed.split(" ") if w.isascii())
        if ascii_only.strip():
            conn.execute("INSERT INTO search_fts_trigram(rowid, body, title) VALUES (?,?,?)",
                         (rid, ascii_only, tokenizer.index_text(title)))
    return True


def delete_doc(doc_id):
    """删除一条文档（含 FTS 与向量）。"""
    conn = db.connection("main")
    row = conn.execute("SELECT rowid FROM search_docs WHERE id=?", (doc_id,)).fetchone()
    rid = row[0] if row else None
    with db.write():
        conn.execute("DELETE FROM search_docs WHERE id=?", (doc_id,))
        if rid is not None:
            conn.execute("DELETE FROM search_fts WHERE rowid=?", (rid,))
            conn.execute("DELETE FROM search_fts_trigram WHERE rowid=?", (rid,))
        conn.execute("DELETE FROM embeddings WHERE doc_id=?", (doc_id,))
    return True


def rebuild_fts():
    """把 `search_docs` 的全部内容重新灌进 FTS 影子表。

    FTS 是**可重建的派生数据** —— 正文真身在 `search_docs`，重建不丢东西。
    什么时候需要重建：换了分词器、重建了 FTS 表、怀疑索引与正文不一致。
    """
    conn = db.connection("main")
    rows = conn.execute("SELECT id FROM search_docs").fetchall()
    count = 0
    for (doc_id,) in rows:
        if _reindex_fts(doc_id):
            count += 1
    return count


def _heal_fts_if_needed():
    """FTS 影子表空但正文非空 → 自愈重建一次。

    什么时候会发生：FTS 被重建（换分词器）、库被拷来拷去丢了影子表、
    迁移里没有顺手重建（迁移用临时连接，不能拿常驻连接）。
    FTS 是派生数据，重建只会补全，不会改正文。
    """
    conn = db.connection("main")
    try:
        docs = conn.execute("SELECT COUNT(*) FROM search_docs").fetchone()[0]
        if not docs:
            return 0
        indexed = conn.execute("SELECT COUNT(*) FROM search_fts").fetchone()[0]
        if indexed >= docs:
            return 0
    except Exception:
        return 0
    return rebuild_fts()


def delete_by_scope(kind, owner="", src="", persona="", workspace_id=None):
    """按范围批量删除。返回删除条数。"""
    conn = db.connection("main")
    sql = "SELECT id FROM search_docs WHERE scope_kind=? AND owner=? AND src=? AND persona=?"
    args = [kind, owner, src, persona]
    if workspace_id is not None:
        sql += " AND workspace_id=?"
        args.append(workspace_id)
    ids = [r[0] for r in conn.execute(sql, args).fetchall()]
    for doc_id in ids:
        delete_doc(doc_id)
    return len(ids)


# --------------------------------------------------------------------------
# 检索
# --------------------------------------------------------------------------

def _scope_where(scope, kinds):
    """构造 SQL 过滤条件。**隔离必须发生在 SQL 层**。

    `scope` 字典：
      owner / src / persona / chan / workspace_ids
    """
    clauses = []
    args = []
    if kinds:
        clauses.append("scope_kind IN (%s)" % ",".join("?" for _ in kinds))
        args.extend(kinds)
    for key in ("owner", "src", "persona", "chan"):
        value = (scope or {}).get(key)
        if value is not None and value != "":
            clauses.append("%s=?" % key)
            args.append(value)
    workspace_ids = (scope or {}).get("workspace_ids")
    if workspace_ids is not None:
        if not workspace_ids:
            # 空工作区授权只排除工作区文件，不影响同一身份可读的聊天、
            # 长期记忆和知识库文档。
            clauses.append("scope_kind!=?")
            args.append(SCOPE_WORKSPACE)
        else:
            placeholders = ",".join("?" for _ in workspace_ids)
            clauses.append("((scope_kind=? AND workspace_id IN (%s)) OR "
                           "(scope_kind!=? AND (workspace_id='' OR workspace_id IN (%s))))"
                           % (placeholders, placeholders))
            args.extend([SCOPE_WORKSPACE] + list(workspace_ids) +
                        [SCOPE_WORKSPACE] + list(workspace_ids))
    return (" AND " + " AND ".join(clauses)) if clauses else "", args


def _scope_match(row, scope, kinds):
    """Python 层单行范围校验。用于最终获取 SearchResult 时的防御性二次校验。

    row 是 search_docs 的一行元组：
      (id, scope_kind, owner, src, persona, chan, workspace_id, ...)
    """
    if not row:
        return False
    scope_kind = row[1]
    owner = row[2] or ""
    src = row[3] or ""
    persona = row[4] or ""
    chan = row[5] or ""
    workspace_id = row[6] or ""
    if kinds and scope_kind not in kinds:
        return False
    s = scope or {}
    for key, val in (("owner", owner), ("src", src), ("persona", persona), ("chan", chan)):
        expected = s.get(key)
        if expected is not None and expected != "" and val != expected:
            return False
    ws_ids = s.get("workspace_ids")
    if ws_ids is not None:
        if not ws_ids:
            return scope_kind != SCOPE_WORKSPACE
        if scope_kind == SCOPE_WORKSPACE:
            if workspace_id not in ws_ids:
                return False
        elif workspace_id and workspace_id not in ws_ids:
            return False
    return True


def bm25_search(query, scope=None, kinds=None, limit=DEFAULT_CANDIDATES):
    """BM25 关键词检索。返回 (doc_id, score) 列表，分数越大越相关。

    FTS 的 rowid 与 `search_docs` 的隐式 rowid 一致，所以隔离条件能直接
    写在 SQL 的 JOIN 上 —— **隔离发生在数据库层，不是在 Python 层筛**。
    """
    conn = db.connection("main")
    match = tokenizer.match_query(query)
    if not match:
        return []
    where, args = _scope_where(scope, kinds)
    sql = ("SELECT d.id, bm25(search_fts) AS rank FROM search_fts "
           "JOIN search_docs d ON d.rowid = search_fts.rowid "
           "WHERE search_fts MATCH ?" + where +
           " ORDER BY rank LIMIT ?")
    try:
        rows = conn.execute(sql, [match] + args + [int(limit)]).fetchall()
    except Exception:
        return []
    # bm25() 越小越相关，取负号当"越大越好"的分数
    return [(r[0], -float(r[1])) for r in rows if r[0]]


def vector_search(query, scope=None, kinds=None, limit=DEFAULT_CANDIDATES, backend=None):
    """向量语义检索。返回 (doc_id, score) 列表。

    **优先用 FAISS HNSW 近似最近邻索引**，1万条以上比暴力扫描快50-100倍。
    FAISS 不可用或索引未构建时自动降级到暴力扫描。
    **失败返回空列表**，绝不抛异常 —— 调用方据此降级到 BM25。
    """
    # Independent gate: disabled means zero embedding calls, including explicit backends.
    from . import unified
    if not unified.semantic_enabled():
        return []
    conn = db.connection("main")
    backend = backend or _default_backend()
    ok, reason = backend.available()
    if not ok:
        return []
    where, args = _scope_where(scope, kinds)
    try:
        vectors, tag = _cached_query_embed(query, backend)
    except Exception:
        return []
    if not vectors:
        return []
    qv = vectors[0]
    dim = len(qv)
    # 只取同模型同维度的向量 —— 混用会算出无意义的相似度
    rows = conn.execute(
        "SELECT e.doc_id, e.model, e.dim, e.vector FROM embeddings e "
        "JOIN search_docs d ON d.id = e.doc_id WHERE 1=1" + where,
        args).fetchall()
    if not rows:
        return []
    # 过滤出同模型同维度的向量
    valid_rows = []
    for row in rows:
        if row[1] != tag or int(row[2]) != dim:
            continue
        packed = vector.unpack(row[3])
        if len(packed) != dim:
            continue
        valid_rows.append((row[0], packed))
    if not valid_rows:
        return []

    # ---- 优先用 ANN 索引 ----
    try:
        ann = vector.get_ann_index(db.data_dir(), dim, tag)
        if ann.ensure_loaded(all_docs=valid_rows):
            results = ann.search(qv, k=limit)
            if results:
                # 【P0 范围隔离】ANN 索引是全局缓存的，可能包含其他 scope 的向量。
                # 必须用当前 scope 过滤后的 valid_rows 的 doc_id 集合再次校验，
                # 绝不能返回不在本次授权范围内的文档。
                allowed_ids = {did for did, _ in valid_rows}
                return [(did, s) for did, s in results
                        if did in allowed_ids and s > 0][:limit]
    except Exception:
        pass  # ANN 失败，降级到暴力扫描

    # ---- 降级：暴力扫描 ----
    scored = []
    np = vector.have_numpy()
    if np and len(valid_rows) > 50:
        import numpy as np
        mat = np.array([v for _, v in valid_rows], dtype=np.float32)
        q = np.asarray(qv, dtype=np.float32)
        qn = q / (np.linalg.norm(q) + 1e-9)
        mn = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        sims = mn @ qn
        for i, (doc_id, _) in enumerate(valid_rows):
            s = float(sims[i])
            if s > 0:
                scored.append((doc_id, s))
    else:
        for doc_id, vec in valid_rows:
            s = vector.cosine(qv, vec)
            if s > 0:
                scored.append((doc_id, s))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:limit]


def rrf(rankings, k=RRF_K):
    """Reciprocal Rank Fusion。输入多个排序列表，输出融合后的排序。

    每个排序列表是一个 doc_id 序列（已按相关性降序）。
    """
    scores = {}
    for ranking in rankings:
        seen = set()
        rank = 0
        for doc_id in ranking:
            if doc_id in seen:
                continue
            seen.add(doc_id)
            rank += 1
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))


def search(query, scope=None, kinds=None, top_k=DEFAULT_TOP_K,
           candidates=DEFAULT_CANDIDATES, backend=None, budget_chars=None):
    """统一检索入口。

    返回 (results, info)。`results` 是 SearchResult 列表，`info` 是诊断信息。

    `scope` 决定隔离边界：
      {"owner": "...", "src": "wechat", "persona": "default",
       "chan": "wechat", "workspace_ids": ["ws1", "ws2"]}

    `budget_chars`：结果总字符预算。超了就截断并标记。
    """
    info = {"query": (query or "")[:200], "method": "none", "hits": 0,
            "lexical": 0, "semantic": 0, "vector_available": False,
            "vector_reason": "", "candidates": 0, "truncated": False}
    text = (query or "").strip()
    if not text:
        return [], info

    try:
        _heal_fts_if_needed()
        lexical = bm25_search(text, scope=scope, kinds=kinds, limit=candidates)
    except Exception as exc:
        info["lexical_error"] = type(exc).__name__
        lexical = []

    semantic = []
    try:
        semantic = vector_search(text, scope=scope, kinds=kinds,
                                  limit=candidates, backend=backend)
        if semantic:
            info["vector_available"] = True
        else:
            backend_obj = backend or _default_backend()
            ok, reason = backend_obj.available()
            if not ok:
                info["vector_reason"] = reason
    except Exception as exc:
        info["vector_reason"] = type(exc).__name__

    if lexical and semantic:
        info["method"] = "bm25+vector+rrf"
    elif lexical:
        info["method"] = "bm25"
    elif semantic:
        info["method"] = "vector"
    else:
        info["method"] = "none"

    info["lexical"] = len(lexical)
    info["semantic"] = len(semantic)
    fused = rrf([[d for d, _ in lexical], [d for d, _ in semantic]])
    info["candidates"] = len(fused)
    if not fused:
        return [], info

    conn = db.connection("main")
    doc_ids = [d for d, _ in fused][:max(top_k * 3, top_k)]
    scores = dict(fused)
    origins = {}
    for d, _ in lexical[:top_k * 3]:
        origins.setdefault(d, set()).add("bm25")
    for d, _ in semantic[:top_k * 3]:
        origins.setdefault(d, set()).add("vector")

    placeholders = ",".join("?" for _ in doc_ids)
    rows = conn.execute(
        "SELECT id,scope_kind,owner,src,persona,chan,workspace_id,title,body,"
        "locator,source,meta FROM search_docs WHERE id IN (%s)" % placeholders,
        doc_ids).fetchall()

    results = []
    used = 0
    limit = int(budget_chars) if budget_chars else None
    for row in rows:
        if len(results) >= top_k:
            break
        # 【P0 范围隔离】最终防线：即使上游 BM25/向量/RRF 都已过滤，
        # 这里仍要二次校验，防止任何环节漏出越权数据。
        if not _scope_match(row, scope, kinds):
            continue
        body = row[8] or ""
        truncated = False
        if len(body) > MAX_SNIPPET:
            body = body[:MAX_SNIPPET]
            truncated = True
        if limit is not None and used + len(body) > limit:
            remaining = limit - used
            if remaining <= 120:
                info["truncated"] = True
                break
            body = body[:remaining]
            truncated = True
            info["truncated"] = True
        used += len(body)
        try:
            meta = json.loads(row[11] or "{}")
        except Exception:
            meta = {}
        results.append(SearchResult(
            doc_id=row[0], kind=row[1], title=row[7] or "", body=body,
            source=row[10] or "", locator=row[9] or "",
            score=scores.get(row[0], 0.0),
            # 列表而不是字符串：调用方要按"渠道"拼标签，字符串会被逐字拆开
            # （`"＋".join("bm25")` → `"2＋4＋b＋m"`，实测踩过）。
            origin=sorted(origins.get(row[0], ())),
            meta=meta, truncated=truncated))
    results.sort(key=lambda r: (-r.score, r.doc_id))
    info["hits"] = len(results)
    return results, info


def _default_backend():
    """默认向量后端。从 config.json 的 `embedding` 段读。

    🔴 **没有 config.json 时返回 none（只跑 BM25），不默认 local。**
    "本地优先"体现在 `backend_from_config` 的 mode 默认值上（有配置时就是
    local）；这里是另一回事：本地模式走 sentence-transformers 时，
    `SentenceTransformer(model)` 发现本地没缓存会**自己去 HuggingFace 下**
    （bge-small-zh 约 100MB）。没有任何配置就对每个用户触发一次静默下载，
    既占带宽也可能落在他不想放的盘上。所以"没配置"一律不碰向量 ——
    真要用本地向量，在 config.json 的 `embedding` 段里显式写 mode 和模型名。
    """
    try:
        import paths
        path = paths.CONFIG_PATH
        if not os.path.exists(path):
            return vector.EmbeddingBackend(mode="none")
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        return vector.backend_from_config(cfg)
    except Exception:
        return vector.EmbeddingBackend(mode="none")


# --------------------------------------------------------------------------
# 向量索引
# --------------------------------------------------------------------------

def index_embeddings(doc_ids, backend=None, include_workspace=False):
    """给文档批量补向量。

    **严格校验模型与维度**：索引里存了 `(model, dim)`，跟当前后端对不上就
    跳过（并把旧的作废），绝不混算。

    **默认跳过工作区文件**（scope_kind='workspace'）：代码文件以英文标识符
    为主，中文嵌入模型对代码语义理解有限，且代码检索靠 BM25 精确匹配
    （函数名/类名/注释）已经够用。需要给代码建向量时显式传 include_workspace=True。
    """
    from . import unified
    if not unified.semantic_enabled():
        return {"ok": False, "reason": "semantic_disabled", "indexed": 0}
    conn = db.connection("main")
    backend = backend or _default_backend()
    ok, reason = backend.available()
    if not ok:
        return {"ok": False, "reason": reason, "indexed": 0}
    doc_ids = list(doc_ids or [])
    if not doc_ids:
        return {"ok": True, "indexed": 0}
    placeholders = ",".join("?" for _ in doc_ids)
    # 默认排除 workspace 类型：代码文件不向量化，只走 BM25
    scope_filter = "" if include_workspace else " AND scope_kind != 'workspace'"
    rows = conn.execute(
        "SELECT id, title, body FROM search_docs WHERE id IN (%s)%s"
        % (placeholders, scope_filter),
        doc_ids).fetchall()
    if not rows:
        return {"ok": True, "indexed": 0}
    texts = ["%s\n%s" % (r[1] or "", r[2] or "") for r in rows]
    try:
        vectors, tag = backend.embed(texts)
    except Exception as exc:
        return {"ok": False, "reason": type(exc).__name__, "indexed": 0}
    try:
        dim = vector.validate_vectors(vectors, len(vectors))
    except ValueError as exc:
        return {"ok": False, "reason": str(exc), "indexed": 0}
    now = time.time()
    with db.write():
        for row, vec in zip(rows, vectors):
            conn.execute(
                "INSERT INTO embeddings (doc_id,model,dim,vector,created_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(doc_id) DO UPDATE SET model=excluded.model, dim=excluded.dim, "
                "vector=excluded.vector, created_at=excluded.created_at",
                (row[0], tag, dim, vector.pack(vec), now))
    # 增量更新 ANN 索引：如果索引已加载，直接 add 新向量，避免下次查询重建
    try:
        ann = vector.get_ann_index(db.data_dir(), dim, tag)
        if ann is not None and ann.is_loaded():
            ann.add([r[0] for r in rows], list(vectors))
            ann.save()
    except Exception:
        pass  # 索引更新失败不影响主流程，下次查询会重建
    return {"ok": True, "indexed": len(rows), "model": tag, "dim": dim}


def embedding_status():
    """向量索引概况。"""
    conn = db.connection("main")
    rows = conn.execute("SELECT model, dim, COUNT(*) FROM embeddings GROUP BY model, dim").fetchall()
    return {"models": [{"model": r[0], "dim": r[1], "count": r[2]} for r in rows],
            "total": sum(r[2] for r in rows)}


def drop_incompatible_embeddings(tag):
    """把与当前后端不兼容的向量删掉（维度变了/换模型了）。"""
    conn = db.connection("main")
    with db.write():
        cur = conn.execute("DELETE FROM embeddings WHERE NOT (model=? AND dim=?)", tag)
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
