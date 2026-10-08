# -*- coding: utf-8 -*-
"""本地知识库核心：bge-m3 本地向量化 + Chroma 向量库。

数据全部在 D 盘（不占 C 盘）：
- 文档目录：  D:\\zhixia_kb\\docs\\
- 向量库文件：D:\\zhixia_kb\\kb_data\\
- 模型缓存：  D:\\zhixia_kb\\models\\

模型用 sentence-transformers 本地跑（9070XT 走 CUDA），零 API 调用。
摄入和检索必须用同一个模型（这里固定 bge-m3）。
"""
import os

# ⚠️ ROCm 运行时必须在 Python 进程启动前就配好 PATH（torch_hip.dll 的依赖
#    在 ROCm bin 里）。Python 运行中改 os.environ 或 os.add_dll_directory 对
#    ctypes 的 DLL 搜索无效。已永久加入用户 PATH（安装时配置），新开终端直接
#    `python ingest.py` 即可；当前会话用同目录下的 kb.bat 启动（它会先设 PATH）。

# 模型缓存放 D 盘（不占 C 盘），国内镜像加速下载
os.environ.setdefault("HF_HOME", r"D:\zhixia_kb\models")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # 消掉 symlink 警告

# ⚠️ 必须先 import torch，再 import sentence_transformers/transformers！
#    transformers 在 import torch 之前会改变 DLL 搜索状态（可能调用了
#    os.add_dll_directory），导致后加载的 torch_hip.dll 找不到 ROCm 依赖（WinError 126）。
#    先 import torch 让 DLL 提前加载好，后续 transformers 就不会重新加载 torch。
import torch  # noqa: E402

from sentence_transformers import SentenceTransformer
import chromadb

# ==================== 配置 ====================
MODEL_NAME = "BAAI/bge-m3"          # 中文最强开源 embedding 之一，8192 token
CHROMA_PATH = r"D:\zhixia_kb\kb_data"
COLLECTION = "my_kb"
DOC_DIR = r"D:\zhixia_kb\docs"
# ============================================

_model = None


def get_model():
    """懒加载模型：第一次调用时下载（约2GB）并加载到显存，之后常驻。"""
    global _model
    if _model is None:
        print("[kb] 加载模型 %s（首次会自动下载到 D:\\zhixia_kb\\models）..."
              % MODEL_NAME, flush=True)
        _model = SentenceTransformer(MODEL_NAME, device="cuda")
        print("[kb] 模型已加载到 GPU", flush=True)
    return _model


def embed(texts):
    """批量向量化，返回 list[list[float]]。

    normalize_embeddings=True 后，余弦相似度 = 向量点积，Chroma 用 cosine 空间。
    batch_size=32 适合 9070XT，可根据显存调整到 64。
    """
    model = get_model()
    vecs = model.encode(texts, normalize_embeddings=True,
                         batch_size=32, show_progress_bar=False)
    return vecs.tolist()


def get_collection():
    """拿到（或创建）Chroma 向量集合，持久化到 D 盘。"""
    db = chromadb.PersistentClient(path=CHROMA_PATH)
    return db.get_or_create_collection(
        COLLECTION, metadata={"hnsw:space": "cosine"})


def stats():
    """查看当前向量库有多少条。"""
    col = get_collection()
    return {"count": col.count(), "collection": COLLECTION, "path": CHROMA_PATH}
