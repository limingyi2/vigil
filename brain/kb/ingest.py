# -*- coding: utf-8 -*-
"""摄入脚本：把 D:\\zhixia_kb\\docs\\ 下的文档切块、向量化、入库。

用法：
    python ingest.py              # 摄入 docs/ 下所有 .md/.txt/.pdf
    python ingest.py --clear      # 先清空向量库再摄入（文档大改时用）

重复运行会按 文件名:块号 作为 id 覆盖（upsert），不会重复入库。
删除某个文档后，旧块仍在库里，需要手动按 source 删（见 --remove）。
"""
import os, re, hashlib, argparse
from .kb_core import embed, get_collection, DOC_DIR, CHROMA_PATH

CHUNK_SIZE = 800      # bge-m3 支持 8192 token，块可以放大到 800 字
OVERLAP = 80           # 块间重叠，防止语义被切断


def split_text(text):
    """按字符切块 + 重叠。生产级可换 langchain RecursiveCharacterTextSplitter。"""
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        chunks.append(text[start:end])
        start += CHUNK_SIZE - OVERLAP
    return chunks


def read_file(path):
    """读 .md/.txt/.pdf。其他格式后续可扩展。"""
    if path.lower().endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clear", action="store_true", help="先清空再摄入")
    parser.add_argument("--remove", type=str, help="按文件名删除旧块（如 note.md）")
    args = parser.parse_args()

    col = get_collection()

    if args.remove:
        col.delete(where={"source": args.remove})
        print("[kb] 已删除来源为 %s 的所有块" % args.remove)
        return

    if args.clear:
        n = col.count()
        if n:
            col.delete(where={})  # 清空全部
            print("[kb] 已清空 %d 条旧记录" % n)

    if not os.path.isdir(DOC_DIR):
        print("[kb] 文档目录不存在：%s" % DOC_DIR)
        return

    ids, docs, metas = [], [], []
    files = [f for f in sorted(os.listdir(DOC_DIR))
             if f.lower().endswith((".md", ".txt", ".pdf"))]
    if not files:
        print("[kb] %s 里没有文档，丢几个 .md/.txt/.pdf 进去再跑" % DOC_DIR)
        return

    for fn in files:
        text = read_file(os.path.join(DOC_DIR, fn))
        for i, chunk in enumerate(split_text(text)):
            ids.append(hashlib.md5(("%s:%d" % (fn, i)).encode()).hexdigest())
            docs.append(chunk)
            metas.append({"source": fn, "chunk": i})

    print("[kb] 共 %d 个文件，%d 个块，开始向量化..." % (len(files), len(docs)))

    # 分批向量化 + 入库（每批 32 条，GPU 批量处理）
    batch = 32
    for i in range(0, len(docs), batch):
        vecs = embed(docs[i:i + batch])
        col.upsert(ids=ids[i:i + batch], documents=docs[i:i + batch],
                    embeddings=vecs, metadatas=metas[i:i + batch])
        print("  已入库 %d/%d" % (min(i + batch, len(docs)), len(docs)), flush=True)

    print("[kb] 完成：%d 个块，向量库在 %s" % (len(docs), CHROMA_PATH))


if __name__ == "__main__":
    main()
