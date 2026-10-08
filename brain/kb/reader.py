# -*- coding: utf-8 -*-
"""读文件成纯文本。只做这一件事。

支持的格式按"这个项目里真的会出现"来定，不追求全：
`.md` `.txt` `.py` `.json` `.csv` `.log` 直接读，`.pdf` 走 pypdf（装了才用），
`.html` 去标签。**不支持的格式明确报错**，不要静默读成乱码 ——
乱码进库比不入库更糟：检索命中一堆看不懂的字，看起来还像在工作。
"""
import os
import re

TEXT_EXT = (".md", ".markdown", ".txt", ".py", ".json", ".csv", ".log",
            ".yml", ".yaml", ".ini", ".cfg", ".conf", ".js", ".ts", ".html", ".htm")


def ext_of(path):
    return os.path.splitext(str(path or ""))[1].lower()


def supports(path):
    return ext_of(path) in TEXT_EXT or ext_of(path) == ".pdf"


def _strip_html(text):
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return re.sub(r"[ \t]{2,}", " ", text)


def read_file(path):
    """读一个文件，返回文本。读不了抛异常，由调用方决定怎么报。"""
    p = str(path or "")
    if not os.path.isfile(p):
        raise FileNotFoundError("文件不存在：%s" % p)
    ext = ext_of(p)
    if ext == ".pdf":
        from pypdf import PdfReader          # 没装就抛 ImportError，调用方去报清楚
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(p).pages)
    with open(p, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    if ext in (".html", ".htm"):
        text = _strip_html(text)
    return text


def list_files(root, recursive=True):
    """列出一个目录下所有能读的文件（按路径排序，结果稳定）。"""
    root = str(root or "")
    if os.path.isfile(root):
        return [root] if supports(root) else []
    out = []
    if not os.path.isdir(root):
        return out
    if recursive:
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")]
            for fn in sorted(files):
                if supports(fn):
                    out.append(os.path.join(base, fn))
    else:
        for fn in sorted(os.listdir(root)):
            fp = os.path.join(root, fn)
            if os.path.isfile(fp) and supports(fp):
                out.append(fp)
    return out
