# -*- coding: utf-8 -*-
"""本地知识库：她能翻你自己的文档回答问题。

接线方式（**不改 server.py、不改 brain.py**）：

- `TOOL_SPECS` + `TOOLS` → 她能**主动**调 `kb_search` 去查资料；
- `on_turn_in` → 每轮自动检索一次，命中的段落拼进 system（她不用先想到要查）；
- `ROUTES` → `/api/plugin/kb/*`，客户端的知识库页用这几个接口。

为什么不直接改 `brain.py:1086` 那个记忆注入点：那是核心链路，改一次就要跟着
记忆、摘要、人设一起回归测试。插件机制本来就是为这种"加一块能力"准备的，
放这里出问题也只影响这一个插件，`FEATURE` 一关就干净退出。

**代价说清楚**：`on_turn_in` 每轮多一次 embedding 调用（和记忆检索同量级）。
嫌贵就把 `inject` 关掉，只留工具 —— 她就只在需要时查。
"""
import os

import kb
from kb import config as _cfg

NAME = "kb"
DESC = "本地知识库：翻你自己的文档回答问题"
FEATURE = "kb"

# 当前 brain 引用（start 时拿到），用来问「现在是谁的人设、哪个渠道」。
_brain = None


def start(brain):
    global _brain
    _brain = brain


def _scope():
    """现在该用哪个分区。拿不到 brain 就退回默认人设 + app。"""
    try:
        src = getattr(_brain, "_cur_src", "app") or "app"
        key = _brain.persona_key_of(src) if hasattr(_brain, "persona_key_of") else ""
        return key, src
    except Exception:
        return "", "app"


# ---------------- 工具 ----------------

def _unified_rows(q, key, src):
    """走 P17 统一检索（FTS5 BM25 + 向量 + RRF）。拿不到就返回 None。

    为什么还要先 `sync_kb`：统一索引是**派生数据**，知识库这边刚导入的文档
    不会自动出现在里面。同步是增量的（内容没变的块直接跳过），
    所以这里花的是一次列表比对的代价，不是重新分词。
    """
    try:
        from storage import unified
        if not unified.unified_enabled():
            return None
        unified.sync_kb("", src, key, src)
        rows, _info = unified.retrieve(q, src=src, persona=key, chan=src,
                                       kinds=["kb"], top_k=5, budget_chars=2400)
    except Exception:
        return None
    return rows or None


def _t_kb_search(arg):
    """参数就是查询词。返回给她看的纯文本，不要 JSON。"""
    q = str(arg or "").strip()
    if not q:
        return "查不了：没给要查的内容"
    key, src = _scope()

    unified_rows = _unified_rows(q, key, src)
    if unified_rows:
        out = ["知识库里找到 %d 段相关内容：" % len(unified_rows)]
        for r in unified_rows:
            out.append("\n【%s｜%.2f】\n%s"
                       % (r.source or r.title or "?", r.score, (r.body or "")[:600]))
        return "\n".join(out)

    rows = kb.search(q, persona_key=key, src=src)
    if not rows:
        return ("知识库里没找到跟「%s」相关的内容。"
                "（要么确实没有，要么还没导入文档 —— 别自己编。）" % q[:40])
    out = ["知识库里找到 %d 段相关内容：" % len(rows)]
    for r in rows:
        out.append("\n【%s｜相关度 %.2f】\n%s"
                   % (r.get("source", "?"), r.get("score", 0), (r.get("text") or "")[:600]))
    return "\n".join(out)


TOOLS = {"kb_search": _t_kb_search}

TOOL_SPECS = {
    "kb_search": {
        "desc": "在他的本地知识库里做语义检索，返回最相关的文档片段。"
                "问题涉及他导入的资料、文档、笔记、手册时用它，不要凭印象回答。",
        "params": [("query", "要查的内容，用自然语言描述", True)],
        "arg_param": "query",
    },
}

TOOL_HELP = (
    "- 查资料：[tool:kb_search:要查的内容] 翻他的本地知识库。\n"
    "  问到文档、笔记、手册、资料里的内容时先查再答；查不到就直说查不到，"
    "不要拿常识补成像是文档里的内容。\n"
)

TOOL_HELP_NATIVE = (
    "- 查资料：翻他的本地知识库。问到文档、笔记、手册里的内容时先查再答；\n"
    "  查不到就直说查不到，不要拿常识补成像是文档里的内容。\n"
)


# ---------------- 每轮注入 ----------------

def on_turn_in(ctx):
    """返回要注入 system 的文本；空字符串 = 这轮不注入。"""
    cfg = _cfg.load()
    if not cfg["enabled"] or not cfg["inject"]:
        return ""
    text = (getattr(ctx, "text", "") or "").strip()
    if len(text) < 4:                    # "嗯""在吗"这种不值得检索
        return ""
    try:
        brain = getattr(ctx, "brain", None) or _brain
        src = getattr(ctx, "src", "app") or "app"
        key = brain.persona_key_of(src) if brain is not None and hasattr(brain, "persona_key_of") else ""
        rows = kb.search(text, persona_key=key, src=src, top_k=cfg["top_k"])
        block = kb.format_block(rows, cfg["max_chars"])
        if block:
            print("[知识库] 注入 %d 段（最高 %.2f）"
                  % (len(rows), rows[0]["score"]), flush=True)
        return block
    except Exception as e:
        # 知识库挂了不能影响聊天：记一行日志，这轮当没命中。
        print("[知识库] 注入失败：%s %s" % (type(e).__name__, str(e)[:120]), flush=True)
        return ""


# ---------------- HTTP ----------------

def _h_stats(body, query):
    key, src = _scope()
    return kb.stats(persona_key=(query.get("persona") or key), src=(query.get("src") or src))


def _h_docs(body, query):
    key, src = _scope()
    return {"ok": True, "items": kb.list_docs(persona_key=(query.get("persona") or key),
                                             src=(query.get("src") or src))}


def _h_search(body, query):
    q = query.get("q") or body.get("q") or ""
    key, src = _scope()
    k = int(query.get("k") or body.get("k") or 0) or None
    rows = kb.search(q, persona_key=key, src=src, top_k=k, min_score=0.0)
    return {"ok": True, "q": q, "items": rows}


def _h_ingest(body, query):
    """三种入参：text（直接给正文）、path（服务器上的文件/目录）、name（来源名）。"""
    key, src = _scope()
    text = body.get("text")
    path = body.get("path")
    name = body.get("name")
    if path:
        return kb.ingest_path(path, persona_key=key, src=src)
    if text:
        return kb.ingest_text(text, name or "手动添加", persona_key=key, src=src)
    return {"ok": False, "err": "要 text 或 path"}


def _h_delete(body, query):
    key, src = _scope()
    name = body.get("name") or query.get("name") or ""
    if not name:
        return {"ok": False, "err": "没给 name"}
    return kb.delete_doc(name, persona_key=key, src=src)


def _h_reindex(body, query):
    key, src = _scope()
    return kb.reindex(persona_key=key, src=src)


def _h_clear(body, query):
    key, src = _scope()
    return kb.clear(persona_key=key, src=src)


def _h_config(body, query):
    """改开关/阈值。只认白名单键，见 kb/config.py。"""
    if body:
        _cfg.set_config(body)
    key, src = _scope()
    return kb.stats(persona_key=key, src=src)


ROUTES = [
    ("GET", "/api/plugin/kb/stats", _h_stats),
    ("GET", "/api/plugin/kb/docs", _h_docs),
    ("GET", "/api/plugin/kb/search", _h_search),
    ("POST", "/api/plugin/kb/ingest", _h_ingest),
    ("POST", "/api/plugin/kb/delete", _h_delete),
    ("POST", "/api/plugin/kb/reindex", _h_reindex),
    ("POST", "/api/plugin/kb/clear", _h_clear),
    ("POST", "/api/plugin/kb/config", _h_config),
]


def _demo():
    """本地跑一遍看看通不通（不进对话链路）：
        cd brain && python plugins/kb.py 文档.md
    """
    import sys
    key, src = "", "app"
    if len(sys.argv) > 1:
        p = sys.argv[1]
        print(kb.ingest_path(p, persona_key=key, src=src))
    print(kb.stats(persona_key=key, src=src))
    print(kb.search(sys.argv[2] if len(sys.argv) > 2 else "这个项目是做什么的",
                    persona_key=key, src=src))


if __name__ == "__main__":
    _demo()
