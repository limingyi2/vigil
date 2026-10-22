# -*- coding: utf-8 -*-
"""向量与块的落盘层。一个分区（人设 × 渠道）一个目录。

不引入新向量库，照 `memory_store_v2` 那套来：扁平矩阵 + json 清单。
理由和记忆库一样 —— 语料量级是"几千块"这个数量级，暴力余弦扫一遍是毫秒级，
多一个 Chroma/faiss 就多一份要同步、要迁移、要写进 requirements 的依赖。
真到了几十万块再换 ANN 也不迟，到时候只要换 `search()` 一个函数。

目录里四个文件，**行序严格对齐**（vectors.npy 第 i 行 ↔ chunks.json 第 i 项）：
    chunks.json  块正本：[{id,text,source,chunk,heading}]
    vectors.npy  (N, dim) float32
    ids.json     [id...]，用来快速校验对齐
    meta.json    {model, dim, count, updated_at, sources:{名字: {chunks,size,mtime,added_at}}}

meta 里的 `model`/`dim` 是**保险丝**：换了嵌入后端或模型之后维度会对不上，
这时整个索引作废并明确报"需要重建"，绝不拿两种模型的向量算相似度 ——
那不会报错，只会一直返回看起来像结果的东西。
"""
import hashlib
import io
import json
import os
import threading
import time

import numpy as np

_MISSING = object()


def _atomic_write_bytes(path, data):
    """临时文件 + os.replace。中途断电也不会留下半截文件。"""
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _atomic_write_json(path, obj):
    _atomic_write_bytes(path, json.dumps(obj, ensure_ascii=False,
                                         indent=2).encode("utf-8"))


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if v is not None else default
    except Exception:
        return default


def chunk_id(source, index):
    """稳定 id：同一个文件同一块号永远同一个 id，重跑不会重复入库。"""
    return hashlib.md5(("%s:%d" % (source, index)).encode("utf-8")).hexdigest()


class KBStore:
    """一个分区的知识库。线程安全：读写在锁内，向量化在锁外（见 engine）。"""

    def __init__(self, dirpath):
        self.dir = str(dirpath)
        self._lock = threading.RLock()
        self._chunks = None
        self._vectors = None
        self._meta = {}
        self._dirty = False

    # ---------- 落盘 ----------
    def _paths(self):
        return {
            "chunks": os.path.join(self.dir, "chunks.json"),
            "vectors": os.path.join(self.dir, "vectors.npy"),
            "ids": os.path.join(self.dir, "ids.json"),
            "meta": os.path.join(self.dir, "meta.json"),
        }

    def load(self):
        """懒加载 + 三重校验（行数对齐 / 维度 / 模型）。坏了就当空库。"""
        with self._lock:
            if self._chunks is not None:
                return self
            p = self._paths()
            chunks = _read_json(p["chunks"], [])
            meta = _read_json(p["meta"], {})
            vecs = None
            try:
                with open(p["vectors"], "rb") as f:
                    vecs = np.load(io.BytesIO(f.read()), allow_pickle=False)
            except Exception:
                vecs = None
            ok = (
                isinstance(chunks, list) and isinstance(vecs, np.ndarray)
                and vecs.ndim == 2 and len(chunks) == vecs.shape[0]
                and int(meta.get("dim") or 0) == (vecs.shape[1] if vecs.ndim == 2 else -1)
            )
            if not ok and chunks:
                print("[知识库] %s 的索引与块正本对不上，按空库处理（需要重建索引）"
                      % self.dir, flush=True)
                chunks = []
                vecs = None
                meta = {}
            self._chunks = list(chunks or [])
            self._vectors = (vecs.astype(np.float32) if isinstance(vecs, np.ndarray)
                             else np.zeros((0, 0), dtype=np.float32))
            self._meta = dict(meta or {})
            return self

    def save(self):
        with self._lock:
            self.load()
            os.makedirs(self.dir, exist_ok=True)
            p = self._paths()
            buf = io.BytesIO()
            np.save(buf, self._vectors, allow_pickle=False)
            _atomic_write_bytes(p["vectors"], buf.getvalue())
            _atomic_write_json(p["chunks"], self._chunks)
            _atomic_write_json(p["ids"], [c.get("id") for c in self._chunks])
            self._meta["count"] = len(self._chunks)
            self._meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _atomic_write_json(p["meta"], self._meta)
            self._dirty = False

    # ---------- 状态 ----------
    def stats(self):
        with self._lock:
            self.load()
            return {
                "dir": self.dir,
                "count": len(self._chunks),
                "docs": len(self._meta.get("sources") or {}),
                "dim": int(self._meta.get("dim") or 0),
                "model": self._meta.get("model") or "",
                "updated_at": self._meta.get("updated_at") or "",
                "sources": dict(self._meta.get("sources") or {}),
            }

    def compatible(self, model, dim):
        """当前索引能不能用来查（模型/维度一致）。空库视为兼容。"""
        with self._lock:
            self.load()
            if not self._chunks:
                return True
            return (self._meta.get("model") == model
                    and int(self._meta.get("dim") or 0) == int(dim or 0))

    # ---------- 写 ----------
    def upsert(self, source, chunks, vecs, model):
        """整份替换一个来源的所有块（先剔旧块，再插新块）。

        按来源整体替换而不是逐块 diff：重跑一次摄入就该得到一模一样的结果，
        diff 只会让"某次删了半段"这种状态悄悄留在库里。
        """
        with self._lock:
            self.load()
            keep_idx = [i for i, c in enumerate(self._chunks)
                        if c.get("source") != source]
            old = [self._chunks[i] for i in keep_idx]
            width = self._vectors.shape[1] if self._vectors.ndim == 2 else 0
            old_vecs = (self._vectors[keep_idx] if keep_idx and self._vectors.size
                        else np.zeros((0, width), dtype=np.float32))

            new = []
            for i, ch in enumerate(chunks):
                txt = ch.get("text") if isinstance(ch, dict) else str(ch)
                head = ch.get("heading", "") if isinstance(ch, dict) else ""
                new.append({"id": chunk_id(source, i), "text": txt, "source": source,
                            "chunk": i, "heading": head})
            if vecs is None or len(vecs) != len(new):
                raise ValueError("块数与向量数不一致：%d vs %s"
                                 % (len(new), "None" if vecs is None else len(vecs)))

            mat = np.asarray(vecs, dtype=np.float32)
            if self._vectors.ndim == 2 and self._vectors.shape[1] and mat.shape[1] != self._vectors.shape[1]:
                # 维度变了 = 换了嵌入模型，旧向量全部作废（留着算余弦只会得出垃圾）。
                self._chunks, old, old_vecs = [], [], np.zeros((0, mat.shape[1]), dtype=np.float32)

            self._chunks = old + new
            self._vectors = (np.vstack([old_vecs, mat]) if len(old_vecs) and len(mat)
                             else (old_vecs if len(old_vecs) else mat))
            self._meta["model"] = model
            self._meta["dim"] = int(mat.shape[1]) if len(mat) else int(self._meta.get("dim") or 0)
            src = dict(self._meta.get("sources") or {})
            cur = dict(src.get(source) or {})
            cur["chunks"] = len(new)
            cur["added_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            src[source] = cur
            self._meta["sources"] = src
            self.save()
            return len(new)

    def upsert_text(self, source, chunks):
        """Store searchable KB chunks without generating embeddings.

        Existing vectors are preserved; newly ingested chunks get zero vectors
        of the current width, which are ignored until an explicit reindex.
        """
        with self._lock:
            self.load()
            width = self._vectors.shape[1] if self._vectors.ndim == 2 else 0
            empty_vectors = np.zeros((len(chunks), width), dtype=np.float32)
            return self.upsert(source, chunks, empty_vectors, self._meta.get("model") or "")

    def set_source_meta(self, source, **kw):
        with self._lock:
            self.load()
            src = dict(self._meta.get("sources") or {})
            cur = dict(src.get(source) or {})
            cur.update(kw)
            src[source] = cur
            self._meta["sources"] = src
            self.save()

    def delete_source(self, source):
        with self._lock:
            self.load()
            keep = [i for i, c in enumerate(self._chunks) if c.get("source") != source]
            if len(keep) == len(self._chunks):
                return 0
            self._chunks = [self._chunks[i] for i in keep]
            self._vectors = (self._vectors[keep] if keep and self._vectors.size
                             else np.zeros((0, self._vectors.shape[1] if self._vectors.ndim == 2
                                            else 0), dtype=np.float32))
            src = dict(self._meta.get("sources") or {})
            src.pop(source, None)
            self._meta["sources"] = src
            self.save()
            return len(self._chunks)

    def replace_all(self, rows, vecs, model):
        """整库替换（重建索引用）。rows 是带 source 字段的块正本，来源信息保留。"""
        with self._lock:
            self.load()
            if len(rows) != len(vecs):
                raise ValueError("块数与向量数不一致：%d vs %d" % (len(rows), len(vecs)))
            self._chunks = [dict(r) for r in rows]
            self._vectors = np.asarray(vecs, dtype=np.float32)
            self._meta["model"] = model
            self._meta["dim"] = int(self._vectors.shape[1]) if self._vectors.size else 0
            rebuild = {}
            for c in self._chunks:
                s = c.get("source", "?")
                cur = rebuild.get(s) or {"chunks": 0,
                                         "added_at": time.strftime("%Y-%m-%d %H:%M:%S")}
                cur["chunks"] += 1
                rebuild[s] = cur
            self._meta["sources"] = rebuild
            self.save()
            return len(self._chunks)

    def clear(self):
        with self._lock:
            self._chunks = []
            self._vectors = np.zeros((0, 0), dtype=np.float32)
            self._meta = {}
            self.save()

    # ---------- 读 ----------
    def search(self, qvec, top_k=5, min_score=0.0):
        """余弦检索。向量已是单位长度时点积即余弦，这里统一再归一化一次兜底。"""
        with self._lock:
            self.load()
            if qvec is None or not len(self._chunks) or not self._vectors.size:
                return []
            q = np.asarray(qvec, dtype=np.float32)
            if q.ndim != 1 or q.shape[0] != self._vectors.shape[1]:
                return []
            qn = q / (np.linalg.norm(q) + 1e-9)
            m = self._vectors
            mn = m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
            sims = mn @ qn
            order = np.argsort(-sims)[:max(1, int(top_k or 5))]
            out = []
            for i in order:
                sc = float(sims[int(i)])
                if sc < float(min_score or 0.0):
                    continue
                c = self._chunks[int(i)]
                out.append({"score": round(sc, 3), "source": c.get("source", "?"),
                            "chunk": c.get("chunk", 0), "heading": c.get("heading", ""),
                            "text": c.get("text", "")})
            return out

    def chunks(self):
        with self._lock:
            self.load()
            return list(self._chunks)
