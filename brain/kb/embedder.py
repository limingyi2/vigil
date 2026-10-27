# -*- coding: utf-8 -*-
"""嵌入：一个口子，三条后端。

选哪条由 `config["backend"]` 决定，**默认 api**：

- `api`       复用 `memory_store_v2.embed_texts` —— 云端 OpenAI 兼容 /embeddings
              （bge-m3，1024 维），失败自动降级本地 fastembed。零新依赖、无需 GPU、
              向量与记忆库同一套模型，检索口径一致。
- `local`     加载 D 盘那份 4.3G 的 `BAAI/bge-m3`（sentence-transformers + ROCm）。
              零 API 成本，但**要求进程启动前配好 ROCm 的 PATH**（见 kb_core.py 的说明），
              起不来时自动退回 api，不让整轮对话卡死。
- `fastembed` 直接用 fastembed 的 ONNX 小模型（bge-small-zh-v1.5，384 维）。
              最轻，但维度与记忆库不同，只能整库自洽。

注意： 一个库的向量必须来自**同一个后端**。`store.py` 的 meta 里记着 model 与 dim，
对不上就整个索引作废（宁可重建，也不要拿两种模型的向量算余弦 —— 那算出来的
相似度没有任何意义，而且不会报错，只会一直给烂结果）。
"""
import numpy as np

from . import config as _cfg

_model = None


def backend():
    return str(_cfg.get("backend") or "api").lower()


def _api_embed(texts):
    import memory_store_v2 as _m
    vecs = _m.embed_texts(texts)
    if not vecs:
        return []
    tag = getattr(_m, "_embed_model_used", "") or "api"
    return [np.asarray(v, dtype=np.float32) for v in vecs], tag


def _local_embed(texts):
    # kb_core 里那两行 import 顺序（先 torch 再 transformers）和 HF_HOME 设置是
    # 踩过坑的，别在这里重写一遍 —— 直接用它，失败就退回 api。
    from . import kb_core
    return [np.asarray(v, dtype=np.float32) for v in kb_core.embed(texts)], kb_core.MODEL_NAME


def _fastembed_embed(texts):
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        import memory_store_v2 as _m
        _model = TextEmbedding(model_name=_m.LOCAL_EMBED_MODEL)
    return [np.asarray(v, dtype=np.float32) for v in _model.embed(texts)], "fastembed"


_BACKENDS = {"api": _api_embed, "local": _local_embed, "fastembed": _fastembed_embed}


def embed(texts):
    """向量化。返回 (list[np.ndarray], 实际用的模型名)；失败返回 ([], "")。

    失败**不抛异常**：检索不到就当没命中，让对话继续 ——
    知识库挂掉不该把聊天一起拖下水。
    """
    texts = [t for t in (texts or []) if str(t or "").strip()]
    if not texts:
        return [], ""
    order = [backend()] + [b for b in ("api", "local", "fastembed") if b != backend()]
    last_err = ""
    for name in order:
        fn = _BACKENDS.get(name)
        if fn is None:
            continue
        try:
            vecs, tag = fn(texts)
            if vecs and len(vecs) == len(texts):
                return vecs, (tag or name)
        except Exception as e:
            last_err = "%s: %s %s" % (name, type(e).__name__, str(e)[:120])
            print("[知识库] 嵌入后端 %s 不可用，换下一个：%s" % (name, last_err), flush=True)
    if last_err:
        print("[知识库] 所有嵌入后端都失败，本次不做向量检索", flush=True)
    return [], ""


def embed_one(text):
    vecs, tag = embed([text])
    if not vecs:
        return None, ""
    return vecs[0], tag
