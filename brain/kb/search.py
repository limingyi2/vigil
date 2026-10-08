# -*- coding: utf-8 -*-
"""检索脚本：从本地知识库搜最相关的片段。

用法：
    python search.py "你的问题"          # 搜 top 5
    python search.py "你的问题" 3        # 搜 top 3
    python search.py --stats             # 看向量库有多少条
"""
import sys
from .kb_core import embed, get_collection, stats


def search(query, top_k=5):
    """语义检索，返回按相关度排序的结果列表。"""
    col = get_collection()
    qvec = embed([query])[0]
    res = col.query(query_embeddings=[qvec], n_results=top_k,
                    include=["documents", "metadatas", "distances"])
    out = []
    for doc, meta, dist in zip(res["documents"][0],
                                res["metadatas"][0],
                                res["distances"][0]):
        out.append({
            "score": round(1 - dist, 3),       # 余弦相似度，越接近1越相关
            "source": meta.get("source", "?"),
            "chunk": meta.get("chunk", 0),
            "text": doc,
        })
    return out


def format_context(results):
    """把检索结果拼成喂给 LLM 的上下文文本（接 Vigil 时用）。"""
    return "\n\n".join(
        "[来源：%s｜相关度%.2f]\n%s" % (r["source"], r["score"], r["text"])
        for r in results)


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--stats":
        print(stats())
        return
    if len(sys.argv) < 2:
        print('用法：python search.py "你的问题" [top_k]')
        return
    q = sys.argv[1]
    k = int(sys.argv[2]) if len(sys.argv) >= 3 else 5
    results = search(q, top_k=k)
    print("查询：%s\n" % q)
    for r in results:
        print("─" * 60)
        print("[相关度 %.3f] %s（块%d）" % (r["score"], r["source"], r["chunk"]))
        print(r["text"][:300] + ("..." if len(r["text"]) > 300 else ""))
    print("\n" + "=" * 60)
    print("注入 LLM 的上下文格式：")
    print(format_context(results)[:500])


if __name__ == "__main__":
    main()
