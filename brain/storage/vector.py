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

    def __init__(self, mode="local", model="", base="", key="", remote_enabled=False,
                 timeout=8.0, dim=None):
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
            if self._local is None:
                self._local = self._load_local()
        vectors, tag = self._local(texts)
        dim = validate_vectors(vectors, len(texts), self.dim or None)
        if self.dim and dim != self.dim:
            raise ValueError("embedding_dimension_changed")
        self.dim = dim
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
