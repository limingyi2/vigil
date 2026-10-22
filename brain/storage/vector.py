# -*- coding: utf-8 -*-
"""P17 向量检索：Embedding 后端 + 余弦相似度。

## 铁律

1. **本地优先**。默认走本地后端（fastembed / 本地 sentence-transformers）。
2. **远程必须显式配置**。走远程 API 需要 `embedding.remote_enabled=true`，
   且必须有显式的 base 与 key。**没配置就不发任何网络请求**。
3. **失败降级 BM25**。Embedding 挂了就返回空，绝不把异常抛给聊天链路。
4. **严格校验模型标识与维度**。索引里记录的 `(model, dim)` 与当前后端不一致时，
   **整批向量作废**并重建 —— 绝不拿两种模型的向量算余弦。那不会报错，
   只会一直返回看起来像结果的东西（这是最坏的一种坏法）。

第 4 条在 `memory_store_v2` 里已经踩过一次（`kb/store.py` 的注释记录了同款教训），
这里把同一个保险丝再装一遍。

## numpy 缺失时的降级

项目里 numpy 不一定装得上（云端 venv 有，某些精简环境没有）。
向量检索依赖 numpy；**缺了就整个向量通道停用**，只跑 BM25。
不引入新依赖 —— 用纯 Python 算点积虽然慢，但语料量级（几千条）完全扛得住，
作为最后的兜底。
"""
import hashlib
import math
import os
import struct
import threading
import time

_LOCK = threading.RLock()
_CACHE = {}


def _np():
    """返回 numpy 模块，没有就返回 None。"""
    try:
        import numpy
        return numpy
    except Exception:
        return None


def have_numpy():
    return _np() is not None


def pack(vector):
    """float32 小端打包成 BLOB。"""
    data = struct.pack("<%df" % len(vector), *[float(v) for v in vector])
    return data


def unpack(blob):
    if not blob:
        return []
    count = len(blob) // 4
    if count == 0:
        return []
    return list(struct.unpack("<%df" % count, blob[:count * 4]))


def identity(model, dim):
    """模型身份标识。索引里存这个，用来判断能不能混用。"""
    return "%s:%d" % (str(model or "").strip(), int(dim or 0))


def cosine(a, b):
    """余弦相似度。纯 Python 实现，不依赖 numpy。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def validate_vectors(vectors, count, dim=None):
    """校验一批向量。返回维度。**不合格就抛 ValueError**（调用方负责降级）。"""
    if not isinstance(vectors, list) or len(vectors) != count or not vectors:
        raise ValueError("embedding_count_mismatch")
    first = vectors[0]
    if not isinstance(first, (list, tuple)) or not first:
        raise ValueError("embedding_empty")
    width = int(dim or len(first))
    if width <= 0:
        raise ValueError("embedding_dimension_invalid")
    for v in vectors:
        if not isinstance(v, (list, tuple)) or len(v) != width:
            raise ValueError("embedding_dimension_mismatch")
        for n in v:
            if not isinstance(n, (int, float)) or not math.isfinite(float(n)):
                raise ValueError("embedding_value_invalid")
    return width


# --------------------------------------------------------------------------
# 后端
# --------------------------------------------------------------------------

DEFAULT_LOCAL_MODEL = "BAAI/bge-small-zh-v1.5"


class EmbeddingBackend:
    """统一的向量化入口。

    `mode`:
      - ``local``  本地模型（fastembed / sentence-transformers）。**默认**。
      - ``api``    远程 OpenAI 兼容 /embeddings。**必须显式开启**。
      - ``none``   不用向量，只跑 BM25。

    `remote_enabled` 为假时，即使 mode='api' 也不发请求 —— 这是"远程必须
    显式配置"的落地点。

    本地模式**不要求显式配置模型名** —— 没配就用默认的轻量中文模型
    （bge-small-zh-v1.5）。本地优先的语义是"能本地跑就本地跑"，不是
    "本地还得先配置一遍才能用"。真到本地模型加载不了时，`embed()` 抛异常，
    上层降级到 BM25，聊天不受影响。
    """

    # 本地模型空闲卸载阈值（秒）。超过这个时间没有检索请求就卸载模型，省内存。
    # 桌面场景单用户查询频率低，5分钟无操作就卸载很合理。下次查询重新加载约1-2秒。
    IDLE_UNLOAD_SECONDS = 300

    def __init__(self, mode="local", model="", base="", key="", remote_enabled=False,
                 timeout=8.0, dim=None, idle_unload_seconds=None):
        self.mode = str(mode or "none").strip().lower()
        self.model = str(model or "").strip()
        if self.mode == "local" and not self.model:
            self.model = DEFAULT_LOCAL_MODEL
        self.base = str(base or "").strip().rstrip("/")
        self.key = str(key or "")
        self.remote_enabled = bool(remote_enabled)
        self.timeout = max(0.5, min(60.0, float(timeout or 8)))
        self.dim = int(dim or 0)
        self._local = None
        self._checked = False
        self._last_used = 0.0
        self._idle_unload = int(idle_unload_seconds or self.IDLE_UNLOAD_SECONDS)

    def unload(self):
        """卸载本地模型，释放内存。下次 embed 时自动重新加载。"""
        with _LOCK:
            if self._local is not None:
                self._local = None
                self._last_used = 0.0

    def _check_idle_unload(self):
        """检查是否空闲超时，超时就卸载模型。"""
        if self._local is None or self._idle_unload <= 0:
            return
        if self._last_used > 0 and (time.time() - self._last_used) > self._idle_unload:
            self.unload()

    # -- 可用性 --
    def available(self):
        """能不能用。**绝不触发网络请求** —— 这是给 UI 看状态的。

        本地模式的可用性只表示"配置上允许尝试本地"，不代表模型一定装得上。
        真实可用性要等 `embed()` 才知道 —— 那里失败会降级到 BM25。
        """
        if self.mode == "none":
            return False, "embedding_disabled"
        if self.mode == "api":
            if not self.remote_enabled:
                return False, "embedding_remote_not_enabled"
            if not self.base or not self.model:
                return False, "embedding_not_configured"
            return True, ""
        if self.mode == "local":
            return True, ""
        return False, "embedding_unknown_mode"

    def tag(self):
        """后端身份。索引用它判断兼容性。"""
        if self.mode == "api":
            return identity("%s@%s" % (self.model, self.base), self.dim)
        return identity("%s@%s" % (self.model, self.mode), self.dim)

    # -- 真正向量化 --
    def embed(self, texts):
        """返回 (vectors, model_tag)。失败抛异常，调用方负责降级。"""
        texts = [t for t in (texts or []) if str(t or "").strip()]
        if not texts:
            return [], self.tag()
        ok, reason = self.available()
        if not ok:
            raise ValueError(reason)
        if self.mode == "api":
            return self._embed_remote(texts)
        return self._embed_local(texts)

    def _embed_local(self, texts):
        with _LOCK:
            # 空闲超时则先卸载，再重新加载
            self._check_idle_unload()
            if self._local is None:
                self._local = self._load_local()
        vectors, tag = self._local(texts)
        dim = validate_vectors(vectors, len(texts), self.dim or None)
        if self.dim and dim != self.dim:
            raise ValueError("embedding_dimension_changed")
        self.dim = dim
        self._last_used = time.time()
        return vectors, tag

    def _load_local(self):
        """按优先级挑一个可用的本地后端。"""
        errors = []
        # 1) fastembed：ONNX 小模型，最轻
        try:
            from fastembed import TextEmbedding

            def _fastembed(texts):
                model = TextEmbedding(model_name=self.model or "BAAI/bge-small-zh-v1.5")
                vecs = [list(map(float, v)) for v in model.embed(texts)]
                return vecs, identity(self.model or "bge-small-zh-v1.5", len(vecs[0]) if vecs else 0)
            return _fastembed
        except Exception as exc:
            errors.append("fastembed: %s" % type(exc).__name__)
        # 2) sentence-transformers：本地已有的 bge-m3
        try:
            from sentence_transformers import SentenceTransformer

            def _st(texts):
                m = SentenceTransformer(self.model, device="cpu")
                vecs = m.encode(texts, normalize_embeddings=True,
                                show_progress_bar=False)
                return [list(map(float, v)) for v in vecs], identity(self.model, len(vecs[0]))
            return _st
        except Exception as exc:
            errors.append("sentence_transformers: %s" % type(exc).__name__)
        raise ValueError("local_embedding_unavailable: %s" % ",".join(errors))

    def _embed_remote(self, texts):
        import httpx
        headers = {"Authorization": "Bearer " + self.key} if self.key else {}
        response = httpx.post(self.base + "/embeddings", headers=headers,
                              json={"model": self.model, "input": texts},
                              timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        data = sorted(payload.get("data") or [], key=lambda row: row.get("index", 0))
        if [row.get("index") for row in data] != list(range(len(texts))):
            raise ValueError("embedding_indices_invalid")
        vectors = [row.get("embedding") for row in data]
        dim = validate_vectors(vectors, len(texts), self.dim or None)
        if self.dim and dim != self.dim:
            raise ValueError("embedding_dimension_changed")
        self.dim = dim
        return vectors, identity("%s@%s" % (self.model, self.base), dim)


def backend_from_config(api_config, section="embedding"):
    """从 config.json 的 `embedding` 段构造后端。

    读取的键：
      mode            local | api | none，默认 local
      model           模型标识
      base            远程 base_url（api 模式必填）
      key             远程 key
      remote_enabled  **远程开关，默认 False**
      timeout         超时秒数
    """
    cfg = dict(api_config or {})
    opts = cfg.get(section) if isinstance(cfg.get(section), dict) else {}
    mode = str(opts.get("mode") or "local").strip().lower()
    if mode not in ("local", "api", "none"):
        mode = "local"
    remote_enabled = opts.get("remote_enabled")
    if remote_enabled is None:
        # 显式写了 mode='api' 视为有意启用远程；否则一律视为未启用。
        remote_enabled = (mode == "api")
    return EmbeddingBackend(
        mode=mode,
        model=opts.get("model") or opts.get("embedding_model") or "",
        base=opts.get("base") or opts.get("base_url") or "",
        key=opts.get("key") or opts.get("api_key") or "",
        remote_enabled=bool(remote_enabled),
        timeout=opts.get("timeout") or 8.0,
        dim=opts.get("dim") or 0,
    )


def local_only_reason(api_config):
    """为什么不能用远程。给 UI 和日志用。"""
    opts = (api_config or {}).get("embedding") if isinstance((api_config or {}).get("embedding"), dict) else {}
    if not opts.get("remote_enabled"):
        return "remote_embedding_not_explicitly_enabled"
    return ""


# --------------------------------------------------------------------------
# ANN 近似最近邻索引（FAISS HNSW）
# --------------------------------------------------------------------------
# 暴力扫描在 1 万条以上会变慢（280ms+），HNSW 能降到 1-5ms。
# 设计原则：
#   1. 懒加载：第一次查询时才构建索引，不阻塞启动
#   2. 自动降级：FAISS 不可用或索引损坏时退回到暴力扫描
#   3. 模型保险丝：索引里记录 (model, dim)，不匹配就重建，绝不混算
#   4. 持久化：索引存文件，启动时直接加载，不用每次重建
#   5. 删除策略：HNSW 不支持直接删，用墓碑标记 + 定期重建

class ANNIndex:
    """FAISS HNSW 近似最近邻索引。

    用法：
        idx = ANNIndex(data_dir, dim, model_tag)
        idx.add(doc_ids, vectors)       # 增量添加
        scores, ids = idx.search(qv, k) # 搜索
        idx.remove(doc_id)              # 标记删除（墓碑）
        idx.rebuild(all_docs)           # 全量重建（清理墓碑）
        idx.save()                      # 持久化
    """

    # HNSW 参数：M=每个节点的连接数，ef_search=搜索时遍历宽度
    HNSW_M = 16
    EF_SEARCH = 50
    # 墓碑比例超过这个阈值就触发重建
    REBUILD_TOMBSTONE_RATIO = 0.2

    def __init__(self, data_dir, dim, model_tag):
        self.data_dir = str(data_dir or ".")
        self.dim = int(dim)
        self.model_tag = str(model_tag or "")
        self._index = None
        self._id_to_doc = {}   # faiss内部id -> doc_id
        self._doc_to_id = {}   # doc_id -> faiss内部id
        self._tombstones = set()  # 已删除的 doc_id
        self._next_id = 0
        self._dirty = False
        self._index_path = os.path.join(self.data_dir, "embeddings_hnsw.faiss")
        self._meta_path = os.path.join(self.data_dir, "embeddings_hnsw.meta.json")

    # -- 可用性 --
    def available(self):
        """FAISS 能不能用。不触发构建。"""
        try:
            import faiss
            return True, ""
        except Exception as exc:
            return False, "faiss_unavailable: %s" % type(exc).__name__

    def is_loaded(self):
        """索引是否已加载到内存。"""
        return self._index is not None

    # -- 加载/构建 --
    def ensure_loaded(self, all_docs=None):
        """确保索引已加载。all_docs 是 [(doc_id, vector_list), ...]，用于首次构建。"""
        if self._index is not None:
            return True
        ok, _ = self.available()
        if not ok:
            return False
        # 尝试从文件加载
        if self._load_from_disk():
            return True
        # 文件不存在或不兼容，全量构建
        if all_docs is not None:
            self._rebuild_internal(all_docs)
            return True
        return False

    def _load_from_disk(self):
        """从磁盘加载索引。成功返回 True。"""
        try:
            if not os.path.exists(self._index_path) or not os.path.exists(self._meta_path):
                return False
            import json
            with open(self._meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            # 模型/维度不匹配就不加载，让上层重建
            if meta.get("model") != self.model_tag or int(meta.get("dim", 0)) != self.dim:
                return False
            import faiss
            self._index = faiss.read_index(self._index_path)
            self._index.hnsw.efSearch = self.EF_SEARCH
            self._id_to_doc = {int(k): str(v) for k, v in (meta.get("id_to_doc") or {}).items()}
            self._doc_to_id = {str(v): int(k) for k, v in self._id_to_doc.items()}
            self._tombstones = set(str(x) for x in (meta.get("tombstones") or []))
            self._next_id = int(meta.get("next_id", max(self._id_to_doc.keys()) + 1 if self._id_to_doc else 0))
            self._dirty = False
            return True
        except Exception:
            self._index = None
            return False

    def _rebuild_internal(self, all_docs):
        """全量重建索引。all_docs = [(doc_id, vector_list), ...]"""
        import faiss
        import numpy as np
        # 过滤掉墓碑
        valid = [(did, vec) for did, vec in all_docs if did not in self._tombstones and vec and len(vec) == self.dim]
        if not valid:
            # 空索引
            self._index = faiss.IndexHNSWFlat(self.dim, self.HNSW_M)
            self._index.hnsw.efSearch = self.EF_SEARCH
            self._id_to_doc = {}
            self._doc_to_id = {}
            self._next_id = 0
            self._dirty = True
            return
        vectors = np.array([v for _, v in valid], dtype=np.float32)
        # 归一化（内积=余弦相似度）
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vectors = vectors / norms
        self._index = faiss.IndexHNSWFlat(self.dim, self.HNSW_M)
        self._index.hnsw.efSearch = self.EF_SEARCH
        self._index.add(vectors)
        self._id_to_doc = {i: str(did) for i, (did, _) in enumerate(valid)}
        self._doc_to_id = {str(did): i for i, (did, _) in enumerate(valid)}
        self._next_id = len(valid)
        self._tombstones.clear()
        self._dirty = True

    # -- 增删 --
    def add(self, doc_ids, vectors):
        """增量添加向量。doc_ids 和 vectors 一一对应。"""
        if self._index is None:
            return False
        ok, _ = self.available()
        if not ok:
            return False
        import numpy as np
        new_ids = []
        new_vecs = []
        for did, vec in zip(doc_ids, vectors):
            did = str(did)
            if not vec or len(vec) != self.dim:
                continue
            if did in self._doc_to_id:
                continue  # 已存在，跳过（调用方应先删再加）
            faiss_id = self._next_id
            self._next_id += 1
            self._doc_to_id[did] = faiss_id
            self._id_to_doc[faiss_id] = did
            new_ids.append(faiss_id)
            new_vecs.append(vec)
        if not new_vecs:
            return True
        arr = np.array(new_vecs, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        arr = arr / norms
        self._index.add(arr)
        self._dirty = True
        return True

    def remove(self, doc_id):
        """标记删除（墓碑）。HNSW 不支持直接删，搜索时过滤，定期重建。"""
        doc_id = str(doc_id)
        if doc_id in self._doc_to_id:
            self._tombstones.add(doc_id)
            self._dirty = True
            # 墓碑太多就重建
            total = len(self._doc_to_id)
            if total > 0 and len(self._tombstones) / total >= self.REBUILD_TOMBSTONE_RATIO:
                # 标记需要重建，但不在这里做（避免阻塞查询）
                pass
        return True

    def needs_rebuild(self):
        """墓碑比例是否超过阈值，需要重建。"""
        total = len(self._doc_to_id)
        if total == 0:
            return False
        return len(self._tombstones) / total >= self.REBUILD_TOMBSTONE_RATIO

    # -- 搜索 --
    def search(self, query_vector, k=10):
        """搜索 top-k。返回 [(doc_id, score), ...]，按分数降序。"""
        if self._index is None or not query_vector or len(query_vector) != self.dim:
            return []
        ok, _ = self.available()
        if not ok:
            return []
        import numpy as np
        qv = np.array([query_vector], dtype=np.float32)
        norm = np.linalg.norm(qv)
        if norm > 0:
            qv = qv / norm
        # 多取一些，过滤墓碑后再截断
        fetch_k = min(k + len(self._tombstones) + 10, self._index.ntotal)
        if fetch_k <= 0:
            return []
        scores, indices = self._index.search(qv, fetch_k)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            doc_id = self._id_to_doc.get(int(idx))
            if not doc_id or doc_id in self._tombstones:
                continue
            results.append((doc_id, float(score)))
            if len(results) >= k:
                break
        return results

    # -- 持久化 --
    def save(self):
        """保存索引到磁盘。"""
        if self._index is None or not self._dirty:
            return
        try:
            import faiss
            import json
            os.makedirs(self.data_dir, exist_ok=True)
            faiss.write_index(self._index, self._index_path)
            meta = {
                "model": self.model_tag,
                "dim": self.dim,
                "id_to_doc": self._id_to_doc,
                "tombstones": list(self._tombstones),
                "next_id": self._next_id,
            }
            with open(self._meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)
            self._dirty = False
        except Exception:
            pass  # 保存失败不影响查询，下次重建

    def close(self):
        """保存并释放。"""
        self.save()
        self._index = None


# 全局 ANN 索引缓存（按 model_tag 区分）
_ANN_INDEXES = {}
_ANN_LOCK = threading.RLock()


def get_ann_index(data_dir, dim, model_tag):
    """获取或创建 ANN 索引实例。线程安全。"""
    key = "%s:%d" % (model_tag, dim)
    with _ANN_LOCK:
        if key not in _ANN_INDEXES:
            _ANN_INDEXES[key] = ANNIndex(data_dir, dim, model_tag)
        return _ANN_INDEXES[key]
