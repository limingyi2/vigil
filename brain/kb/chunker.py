# -*- coding: utf-8 -*-
"""把一篇文档切成检索用的块。

比原型 `ingest.py` 那版（纯按字数硬切）多做三件事，都是为了检索质量：

1. **先按标题切"节"**。跨节的块里两件事各占一半，向量变成两件事的平均值，
   谁问都匹配不上 —— 这是"检索总是差一点"最常见的原因。
2. **节内按空行累加**，只有超长的段才按句末硬切。段落是作者自己划的语义单元，
   比任何按字数切都准。
3. **每块挂上它的标题路径**（如 `安装 > 依赖`）。块被拆出来之后语义会飘，
   挂上标题能把上下文救回来，检索时标题词也参与匹配。

纯函数，不碰文件、不碰网络 —— 好测，也不会在导入时炸。
"""
import re

# 中英文句末。冒号不算 —— "步骤如下："后面接的列表不该被切开。
_SENT_END = re.compile(r"(?<=[。！？!?；;])")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def _norm(text):
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _sections(text):
    """按 Markdown 标题切成 (标题路径, 正文) 序列。没有标题就整篇当一节。"""
    lines = text.split("\n")
    out, stack, buf, in_fence = [], [], [], False
    for ln in lines:
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(ln)
        if m:
            if buf and "".join(buf).strip():
                out.append((" > ".join(stack), "\n".join(buf).strip()))
            buf = []
            level = len(m.group(1))
            title = m.group(2).strip()
            del stack[level - 1:]
            while len(stack) < level - 1:
                stack.append("")
            stack.append(title)
            continue
        buf.append(ln)
    if buf and "".join(buf).strip():
        out.append((" > ".join([s for s in stack if s]), "\n".join(buf).strip()))
    return out


def _hard_split(para, size, overlap):
    """超长段落按句末切；一句都没有（比如整段代码）就按字数硬切。"""
    parts = [p for p in _SENT_END.split(para) if p]
    if len(parts) <= 1:
        out, start = [], 0
        while start < len(para):
            out.append(para[start:start + size])
            start += max(1, size - overlap)
        return out
    out, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) > size:
            out.append(cur)
            cur = cur[-overlap:] if overlap else ""
        cur += p
    if cur:
        out.append(cur)
    return out


def split_text(text, size=800, overlap=80):
    """切块，返回 [{"text": 带标题前缀的块, "heading": 标题路径}]。"""
    size = max(120, int(size or 800))
    overlap = max(0, min(int(overlap or 0), size // 2))
    text = _norm(text)
    if not text:
        return []

    chunks = []
    for heading, body in _sections(text):
        prefix = ("【%s】\n" % heading) if heading else ""
        paras = [p for p in body.split("\n\n") if p.strip()]
        cur = ""
        for para in paras:
            if len(para) > size:
                if cur:
                    chunks.append((prefix, cur))
                    cur = ""
                for piece in _hard_split(para, size, overlap):
                    chunks.append((prefix, piece))
                continue
            if cur and len(cur) + len(para) + 2 > size:
                chunks.append((prefix, cur))
                # 重叠：把上一块的尾巴接上，避免跨块的句子被切断
                cur = (cur[-overlap:] + "\n\n") if overlap else ""
            cur = (cur + "\n\n" + para) if cur else para
        if cur:
            chunks.append((prefix, cur))

    out = []
    for prefix, body in chunks:
        body = body.strip()
        if not body:
            continue
        out.append({"text": (prefix + body).strip(), "heading": prefix.strip("【】\n")})
    return out
