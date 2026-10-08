# -*- coding: utf-8 -*-
"""知识库门面：摄入、检索、统计。插件和 CLI 都只从这里调。

三个设计决定，都是为了和这个项目已有的东西保持一致：

1. **分区跟记忆库同一套规则** —— `paths.scope_name(persona_key, src)`。
   换了人设就是换了一个人，她的知识库不该带着上一位的资料上岗。
   默认人设 + app 落到 `default/`，老路径不存在迁移问题（知识库是新的）。
2. **单例 + 读写锁**，和 `memory_store_v2._cached_store` 一样：同一个目录只有一个
   store 对象，避免两个实例各写一半。**向量化放在锁外** —— 一次嵌入可能是几百毫秒的
   网络请求，锁在里面会让所有检索排队等它。
3. **失败一律不抛**：知识库是锦上添花，查不到就当没命中，绝不能把聊天拖死。
   但**要说清楚为什么**（stats 里带 reason，日志里打出来），不然就是静默不工作。
"""
import os
import threading
import time

from paths import KB_DIR
from . import config as _cfg
from . import embedder as _emb
from .chunker import split_text
from .reader import list_files, read_file
from .store import KBStore

# 测试用：把根目录指到临时目录，别碰真数据。
ROOT_OVERRIDE = None

_STORES = {}
_LOCK = threading.RLock()


def root():
    return ROOT_OVERRIDE or KB_DIR


def scope_of(persona_key="", src="app"):
    from paths import scope_name
    return scope_name(persona_key, src) or "default"


def dir_of(persona_key="", src="app"):
    return os.path.join(root(), scope_of(persona_key, src))


def store_for(persona_key="", src="app"):
    d = dir_of(persona_key, src)
    with _LOCK:
        st = _STORES.get(d)
        if st is None:
            st = KBStore(d)
            _STORES[d] = st
        return st


def reset_cache():
    """测试或换根目录后清掉单例。"""
    with _LOCK:
        _STORES.clear()
        _cfg.load(force=True)


# ---------------- 摄入 ----------------

def ingest_text(text, name, persona_key="", src="app"):
    """把一段文本切成块入库。name 是来源名（显示与删除都用它）。"""
    cfg = _cfg.load()
    name = str(name or "手动添加").strip() or "手动添加"
    chunks = split_text(text, cfg["chunk_size"], cfg["chunk_overlap"])
    if not chunks:
        return {"ok": False, "err": "没有可入库的内容", "chunks": 0}
    vecs, model = [], ""
    for i in range(0, len(chunks), cfg["batch"]):
        part = [c["text"] for c in chunks[i:i + cfg["batch"]]]
        v, m = _emb.embed(part)          # 锁外做网络/GPU 调用
        if not v:
            return {"ok": False, "err": "向量化失败（嵌入后端都不可用）", "chunks": 0}
        vecs.extend(v)
        model = m
    st = store_for(persona_key, src)
    n = st.upsert(name, chunks, vecs, model)
    st.set_source_meta(name, size=len(text or ""))
    return {"ok": True, "name": name, "chunks": n, "model": model, "scope": scope_of(persona_key, src)}


def ingest_path(path, persona_key="", src="app"):
    """摄入一个文件或整个目录。返回每个文件的处理结果。"""
    files = list_files(path)
    if not files:
        return {"ok": False, "err": "这里没有能读的文件（.md/.txt/.pdf/代码类）", "items": []}
    items, total = [], 0
    for fp in files:
        name = os.path.basename(fp)
        try:
            text = read_file(fp)
            r = ingest_text(text, name, persona_key, src)
            st = store_for(persona_key, src)
            try:
                st.set_source_meta(name, size=os.path.getsize(fp),
                                   mtime=int(os.path.getmtime(fp)),
                                   path=fp)
            except OSError:
                pass
            items.append({"name": name, "ok": bool(r.get("ok")),
                          "chunks": r.get("chunks", 0), "err": r.get("err", "")})
            if r.get("ok"):
                total += int(r.get("chunks") or 0)
        except Exception as e:
            items.append({"name": name, "ok": False, "chunks": 0,
                          "err": "%s: %s" % (type(e).__name__, str(e)[:120])})
    return {"ok": total > 0, "items": items, "chunks": total,
            "scope": scope_of(persona_key, src)}


def reindex(persona_key="", src="app"):
    """按现有块正本重算向量（换了嵌入模型之后必须跑一次）。"""
    st = store_for(persona_key, src)
    rows = st.chunks()
    if not rows:
        return {"ok": True, "chunks": 0, "note": "库是空的"}
    cfg = _cfg.load()
    vecs, model = [], ""
    for i in range(0, len(rows), cfg["batch"]):
        part = [r.get("text", "") for r in rows[i:i + cfg["batch"]]]
        v, m = _emb.embed(part)
        if not v:
            return {"ok": False, "err": "向量化失败"}
        vecs.extend(v)
        model = m
    st.clear()
    n = st.replace_all(rows, vecs, model)
    return {"ok": True, "chunks": n, "model": model}


def delete_doc(name, persona_key="", src="app"):
    st = store_for(persona_key, src)
    st.load()
    st.delete_source(str(name))
    left = [s for s in (st.stats().get("sources") or {}) if s != str(name)]
    return {"ok": True, "name": name, "left": len(left)}


def clear(persona_key="", src="app"):
    store_for(persona_key, src).clear()
    return {"ok": True}


# ---------------- 检索 ----------------

def search(query, persona_key="", src="app", top_k=None, min_score=None):
    cfg = _cfg.load()
    q = str(query or "").strip()
    if not q or not cfg["enabled"]:
        return []
    qv, _tag = _emb.embed_one(q)
    if qv is None:
        return []
    st = store_for(persona_key, src)
    k = int(top_k or cfg["search_k"])
    ms = cfg["min_score"] if min_score is None else min_score
    return st.search(qv, top_k=k, min_score=ms)


def list_docs(persona_key="", src="app"):
    st = store_for(persona_key, src)
    st.load()
    stt = st.stats()
    srcs = stt.get("sources") or {}
    return [{"name": n, "chunks": int((v or {}).get("chunks") or 0),
             "size": int((v or {}).get("size") or 0),
             "added_at": (v or {}).get("added_at", "")}
            for n, v in sorted(srcs.items())]


def stats(persona_key="", src="app"):
    cfg = _cfg.load()
    st = store_for(persona_key, src)
    stt = st.stats()
    return {
        "ok": True,
        "enabled": bool(cfg["enabled"]),
        "inject": bool(cfg["inject"]),
        "scope": scope_of(persona_key, src),
        "dir": stt["dir"],
        "count": stt["count"],
        "docs": stt["docs"],
        "dim": stt["dim"],
        "model": stt["model"],
        "backend": cfg["backend"],
        "top_k": cfg["top_k"],
        "min_score": cfg["min_score"],
        "updated_at": stt["updated_at"],
        "config": cfg,
    }


def format_block(results, max_chars=None):
    """拼成注入 system 的段落。空结果返回 ""（调用方据此跳过拼接）。"""
    if not results:
        return ""
    limit = int(max_chars or _cfg.get("max_chars") or 1200)
    parts, used = [], 0
    for r in results:
        seg = "【%s｜相关度 %.2f】\n%s" % (r.get("source", "?"), r.get("score", 0), r.get("text", ""))
        if parts and used + len(seg) > limit:
            break
        if len(seg) > limit:            # 单段就超预算：截断，别整块塞进提示词
            seg = seg[:limit]
        parts.append(seg)
        used += len(seg)
    head = "他的本地知识库里有这些（只在这些确实相关时引用，不要编造出处）：\n"
    return head + "\n\n".join(parts)


def search_for(persona_key="", src="app", query="", top_k=None):
    """给插件用的便捷入口：带耗时与异常兜底。"""
    t0 = time.time()
    try:
        rows = search(query, persona_key=persona_key, src=src, top_k=top_k)
    except Exception as e:
        print("[知识库] 检索失败：%s %s" % (type(e).__name__, str(e)[:120]), flush=True)
        return [], 0.0
    return rows, time.time() - t0
