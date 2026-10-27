# -*- coding: utf-8 -*-
"""P17 检索分词：让 FTS5 能搜中文、文件名、代码符号、路径、版本号和 SHA。

## 为什么需要自己分词

实测 SQLite 3.53 的 `unicode61` 分词器（`fts5vocab` 验证）：

| 输入 | unicode61 的结果 | 问题 |
|---|---|---|
| `src/file.py` | `src` `file` `py` | 路径被打散，"整个路径"搜不到 |
| `v1.2.3` | `v1` `2` `3` | 版本号被打散 |
| `foo_bar` | `foo` `bar` | 代码符号被打散 |
| `sha256:abcdef0123` | `sha256` `abcdef0123` | SHA 前缀与主体被切开 |
| `我想知道北京天气` | **整串一个 token** | 中文完全搜不了 |

trigram 分词器能解决路径/标识符，但**要求查询词至少 3 个字符** ——
两字中文（"北京"）直接无结果，而中文两字词是最常见的查询。

## 方案：写入前预分词 + tokenchars

两步配合，缺一不可：

1. **`tokenize="unicode61 tokenchars '_./:-'"`** —— 让 `.` `/` `-` `:` `_` 成为
   词元内部的字符，于是 `src/file.py`、`v1.2.3`、`foo_bar`、`sha256:abc123`
   各自是**一个完整词元**，可整体精确匹配。
2. **写入前自己切中文** —— 中文串在入库前展开成「单字 + 相邻双字」，
   用空格连接。这样 unicode61 按空格切分后，每个中文单字和双字都是独立词元，
   `北京` 能搜到 `我想知道北京天气`。

## 为什么要双字（bigram）

单字歧义太大（"的""了""我"到处都是）；整句当一个词元又搜不到。
双字（"北京""天气""知道"）是中文检索的经典做法：既比单字准，
又不需要外挂分词词典。

同时**保留原始 ASCII 词元**：`foo_bar` 既作为整体保留（精确匹配用），
也拆出 `foo` `bar`（部分匹配用）。路径同理：保留 `src/file.py` 整体，
也拆出 `src` `file` `py`。

## 查询侧的转义

FTS5 的 MATCH 语法里 `/` `.` `-` `:` 有特殊含义，直接拼进去会报
`syntax error`。所有查询词都用**双引号包裹成字符串字面量**，
双引号内的内容不做语法解析 —— 这是 FTS5 官方支持的转义方式。
"""
import re

# 中文（CJK 统一表意文字 + 扩展A + 兼容表意文字）
_CJK = r"㐀-䶿一-鿿豈-﫿"
_CJK_RUN = re.compile("[" + _CJK + "]+")
_CJK_SINGLE = re.compile("[" + _CJK + "]")

# ASCII 词元：字母数字开头，可含 . / - : _ 等内部字符。
# 刻意允许 SHA（纯 hex）整体成词。
_ASCII_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./:\\+-]*")
# 需要从整体里再拆出来的分隔符
_SPLIT = re.compile(r"[./:\\+\-]+")
# 单独的 SHA（7~64 位 hex），整体保留
_SHA = re.compile(r"\b[0-9a-fA-F]{7,64}\b")

# FTS5 查询语法里必须转义的字符
_FTS_SYNTAX = re.compile(r'[":^*(){}\[\]]')


def tokenize(text, expand_cjk=True):
    """把文本切成词元列表（写入索引与查询共用）。

    返回的词元**已转义**：每个词元都用双引号包裹，可以直接拼进
    FTS5 的 MATCH 表达式。带空格的词元不会出现（内部空白已清理）。
    """
    if not text:
        return []
    out = []
    seen = set()

    def _add(tok):
        if not tok:
            return
        key = tok.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(tok)

    for match in _ASCII_TOKEN.finditer(text):
        raw = match.group(0).rstrip("./\\:+-")
        if not raw:
            continue
        _add(raw)                                   # 整体：路径/版本号/符号/SHA
        parts = [p for p in _SPLIT.split(raw) if p]
        if len(parts) > 1:
            for p in parts:                          # 拆分：部分匹配
                _add(p)

    for match in _SHA.finditer(text):
        _add(match.group(0))                        # SHA 整体再保一次

    if expand_cjk:
        for run in _CJK_RUN.findall(text):
            for ch in run:                          # 单字
                _add(ch)
            for i in range(len(run) - 1):            # 相邻双字
                _add(run[i:i + 2])

    return out


def index_text(text):
    """生成写入 FTS5 的文本：词元用空格连接。

    unicode61 会按空白切分，所以这里用空格隔开就是"预分词"。
    写入侧用 `unicode61 tokenchars '_./:-'`，词元内部的 `.` `/` 等不会被打散。
    """
    return " ".join(tokenize(text))


def quote(term):
    """把词元转义成 FTS5 字符串字面量。"""
    cleaned = _FTS_SYNTAX.sub(" ", str(term or "")).strip()
    if not cleaned:
        return ""
    return '"%s"' % cleaned.replace('"', "")


def match_query(text, expand_cjk=True, operator=None):
    """构造 FTS5 MATCH 表达式。

     **运算符不能一律 AND**（这里踩过）：查询侧如果和写入侧一样展开出
    全部「单字 + 相邻双字」，四字查询「烟台天气」会产出 `烟台` `台天` `天气`
    三个双字加四个单字。`台天` 是**跨边界拼出来的假词**，任何文档里都不会
    出现 —— 用 AND 串起来就是永假，整条查询静默返回空。
    实测表现：两字查询能搜到，四字查询一定搜不到，而索引里明明有。

    规则：
    - 中文串**只取双字**（长度 ≥2 时）；单字串才退化成单字。
      写入侧存了单字 + 双字，所以双字查询照样能命中；反过来只取双字
      可以把歧义大的单字挡在查询之外，"香烟"不会被"烟"单独钓出来。
    - 含中文时用 **OR**：双字词天然有漏匹配（用户说的词和文档写的词
      只重合一半），AND 会把这类相关结果全毙掉。精度交给 BM25 ——
      命中词元越多、越稀有的排得越前。
    - 纯 ASCII/路径/版本号/SHA 用 **AND**：这类词元是完整的，
      `deploy.sh` `v1.2.3` 各是一个词元，AND 就是精确匹配，不会放松。
    """
    terms = _query_terms(text, expand_cjk=expand_cjk)
    terms = [t for t in (quote(x) for x in terms) if t]
    if not terms:
        return ""
    if operator is None:
        operator = "OR" if is_cjk_query(text) else "AND"
    return (" %s " % operator).join(terms)


def _query_terms(text, expand_cjk=True):
    """查询侧词元：ASCII 整体保留（含拆分），中文只取双字。"""
    if not text:
        return []
    out = []
    seen = set()

    def _add(tok):
        if not tok:
            return
        key = tok.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(tok)

    for match in _ASCII_TOKEN.finditer(text):
        raw = match.group(0).rstrip("./\\:+-")
        if not raw:
            continue
        _add(raw)
        for part in _SPLIT.split(raw):
            if part:
                _add(part)

    if expand_cjk:
        for run in _CJK_RUN.findall(text):
            if len(run) >= 2:
                for i in range(len(run) - 1):       # 只取双字，不取单字
                    _add(run[i:i + 2])
            else:
                _add(run)
    return out


def is_cjk_query(text):
    """查询是否以中文为主 —— 决定用 bigram 还是原样分词。"""
    if not text:
        return False
    cjk = len(_CJK_SINGLE.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return cjk > 0 and cjk * 2 >= latin
