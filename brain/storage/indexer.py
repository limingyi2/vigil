# -*- coding: utf-8 -*-
"""P17 增量索引：文件改了、删了，索引要跟上。

## 核心机制

每条被索引的文件在 `search_docs` 里存了 `content_hash`（内容指纹）和
`mtime`。同步时对比：

- **mtime 变了** → 重新读文件、算 hash、重新索引
- **mtime 没变但 hash 变了** → 重新索引（有些工具会改内容但保留 mtime）
- **hash 没变** → 跳过（这就是"增量"：绝大多数文件没动时几乎零开销）

**删除**：靠 `index_state` 里记的"上次索引时有哪些文件"与现在对比。
文件消失了就把它从索引里删掉 —— 留着等于让检索召回了已经不存在的文档。

## 工作区授权边界

**只索引已授权的工作区**。`sync_workspace` 接收的必须是已经通过
`workspaces.list_for()` 过滤后的目录列表。函数本身会再校验一次路径
是否在授权集合内 —— 双保险。授权列表来自调用方，**没授权的一律不碰**。

## 不索引什么

- 二进制文件（按扩展名 + 内容嗅探）
- 超过 MAX_FILE_BYTES 的文件
- `.git`、`.venv`、`node_modules` 等目录
- 隐藏目录（以 `.` 开头）
"""
import hashlib
import json
import os
import time

from . import db
from . import search

MAX_FILE_BYTES = 2 * 1024 * 1024
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".idea",
             ".vscode", "dist", "build", ".pytest_cache", ".mypy_cache",
             ".workbuddy", ".codebuddy", ".gradle", "target"}
SKIP_EXT = {".pyc", ".pyo", ".pyd", ".exe", ".dll", ".so", ".dylib", ".zip",
            ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".png", ".jpg", ".jpeg",
            ".gif", ".bmp", ".webp", ".ico", ".svg", ".mp3", ".mp4", ".wav",
            ".avi", ".mov", ".pdf", ".ttf", ".otf", ".woff", ".woff2", ".db",
            ".sqlite", ".sqlite3", ".npy", ".npz", ".pkl", ".bin"}
TEXT_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".txt", ".json", ".yaml",
            ".yml", ".toml", ".ini", ".cfg", ".conf", ".sh", ".bat", ".ps1",
            ".c", ".h", ".cpp", ".hpp", ".java", ".go", ".rs", ".rb", ".php",
            ".html", ".htm", ".css", ".scss", ".less", ".xml", ".sql", ".log",
            ".csv", ".tsv", ".env", ".gitignore", ".dockerfile", ".makefile"}
# 无扩展名但可能有用的
TEXT_NAME = {"makefile", "dockerfile", "readme", "license", "changelog",
             "requirements", "pipfile", "cmakelists.txt"}


def _is_text_file(path, name):
    ext = os.path.splitext(name)[1].lower()
    if ext in SKIP_EXT:
        return False
    if ext in TEXT_EXT:
        return True
    if name.lower() in TEXT_NAME:
        return True
    if ext == "":
        return True
    return False


def content_hash(data):
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    return hashlib.sha256(data).hexdigest()


def read_text(path):
    """读文件为文本。失败返回 None。"""
    try:
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            return None
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None                      # 二进制
    for encoding in ("utf-8", "gbk", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def doc_id_for(path, workspace_id=""):
    """文件的稳定 id。同一路径 + 同一工作区 → 同一 id。"""
    digest = hashlib.sha256(
        ("%s|%s" % (os.path.abspath(path), workspace_id)).encode("utf-8")).hexdigest()
    return "file:%s" % digest[:32]


# --------------------------------------------------------------------------
# 单文件
# --------------------------------------------------------------------------

def index_file(path, workspace_id="", owner="", src="", persona="", chan="",
               title=None):
    """索引一个文件。返回 (是否更新, doc_id)。"""
    path = os.path.abspath(path)
    name = os.path.basename(path)
    if not _is_text_file(path, name):
        return False, None
    text = read_text(path)
    if text is None:
        return False, None
    digest = content_hash(text)
    doc_id = doc_id_for(path, workspace_id)
    try:
        stat = os.stat(path)
    except OSError:
        return False, None

    conn = db.connection("main")
    row = conn.execute("SELECT content_hash FROM search_docs WHERE id=?", (doc_id,)).fetchone()
    if row and row[0] == digest:
        return False, doc_id                   # 没变，跳过

    search.upsert_doc(
        doc_id, search.SCOPE_WORKSPACE, text,
        title=title or name, source=name, locator=path,
        owner=owner, src=src, persona=persona, chan=chan,
        workspace_id=workspace_id, content_hash=digest,
        mtime=stat.st_mtime, size=stat.st_size,
        meta={"path": path, "mtime": stat.st_mtime})
    return True, doc_id


def unindex_file(path, workspace_id=""):
    """把文件从索引里删掉（文件被删了或被移出授权范围）。"""
    doc_id = doc_id_for(path, workspace_id)
    conn = db.connection("main")
    row = conn.execute("SELECT id FROM search_docs WHERE id=?", (doc_id,)).fetchone()
    if row:
        search.delete_doc(doc_id)
        return True
    return False


# --------------------------------------------------------------------------
# 目录
# --------------------------------------------------------------------------

def walk_authorized(root, allowed_roots=None):
    """遍历目录，但**只走被授权的路径**。

    `allowed_roots` 是白名单（规范化后的绝对路径列表）。为空表示不限。
    符号链接不跟随（避免跳出授权范围）。

     四道检查，缺一不可：
      1. **root 本身**必须在白名单内 —— 否则整个目录都不该被碰
      2. **子目录**必须在白名单内
      3. **文件**的完整路径必须在白名单内
      4. **解析后的真实路径**也必须在白名单内

    只查子目录是不够的：调用方传了一个未授权的 root 时，
    root 下的文件照样会被索引 —— 那就等于授权边界形同虚设。

    第 4 道是后来补的，补之前这里有个真漏洞：`os.walk(followlinks=False)`
    **只挡目录递归，不挡文件符号链接** —— `/ws/evil.md → C:/secret.md`
    会原样出现在 `filenames` 里被 yield 出去。而 `_within` 用的是
    `abspath`，`abspath` 不解析链接，词汇路径 `/ws/evil.md` 当然"在
    /ws 内"，检查等于没做。Windows 的目录 junction 更隐蔽：
    `os.walk` 连递归都拦不住它。所以必须拿 `realpath` 再判一次。
    """
    root = os.path.abspath(root)
    allowed = None
    allowed_real = None
    if allowed_roots:
        allowed = {os.path.abspath(p) for p in allowed_roots}
        # 白名单自己也解析一份：授权根本身是符号链接/junction 是正常用法
        # （比如把工作区挂到别处），不能因此把整个工作区误杀成空。
        allowed_real = {os.path.realpath(p) for p in allowed_roots}
        # root 与白名单必须"相交"：要么 root 在某个白名单路径之下，
        # 要么某个白名单路径在 root 之下。两者都不成立 → 一个文件都不碰。
        # （只查子目录是不够的：root 未授权时其下的文件照样会被索引。）
        if not any(_within(root, a) or _within(a, root) for a in allowed):
            return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        if allowed is not None:
            dirnames[:] = [d for d in dirnames
                           if _authorized(os.path.join(dirpath, d), allowed, allowed_real)]
        for name in filenames:
            if name.startswith("."):
                continue
            path = os.path.join(dirpath, name)
            if allowed is not None and not _authorized(path, allowed, allowed_real):
                continue          # 文件路径本身越权（含链接逃逸）
            yield path


def _authorized(path, allowed, allowed_real):
    """路径必须同时过**词汇**与**解析后**两道关。

    词汇路径守的是"目录结构上在不在授权范围内"；
    解析后路径守的是"这个链接最终指向哪里"。两道都过才算数。
    """
    lex = os.path.abspath(path)
    if not any(_within(lex, a) for a in allowed):
        return False
    real = os.path.realpath(path)
    if real == lex:
        return True                      # 普通文件/目录，词汇路径即真实路径
    # 是符号链接/junction：解析后的落点也必须被授权。
    # realpath 对不存在的目标也返回一个路径，坏链接自然落不到白名单里。
    return any(_within(real, a) for a in (allowed | allowed_real))


def _within(path, root):
    try:
        path = os.path.normcase(os.path.abspath(path))
        root = os.path.normcase(os.path.abspath(root).rstrip("\\/"))
    except Exception:
        return False
    return path == root or path.startswith(root + os.sep)


def sync_workspace(workspace_id, root, allowed_roots=None, owner="", src="",
                    persona="", chan=""):
    """同步一个工作区的文件索引。

    会处理**删除**：先扫出当前存在的文件，再把索引里属于该工作区但
    已不存在的文件删掉。
    """
    root = os.path.abspath(root)
    seen = set()
    changed = 0
    scanned = 0
    for path in walk_authorized(root, allowed_roots):
        scanned += 1
        seen.add(doc_id_for(path, workspace_id))
        updated, _ = index_file(path, workspace_id=workspace_id, owner=owner,
                                src=src, persona=persona, chan=chan)
        if updated:
            changed += 1
    # 清理已删除的文件
    removed = 0
    conn = db.connection("main")
    rows = conn.execute("SELECT id FROM search_docs WHERE scope_kind=? AND workspace_id=?",
                        (search.SCOPE_WORKSPACE, workspace_id)).fetchall()
    for (doc_id,) in rows:
        if doc_id not in seen:
            search.delete_doc(doc_id)
            removed += 1
    return {"scanned": scanned, "changed": changed, "removed": removed,
            "workspace_id": workspace_id}


# --------------------------------------------------------------------------
# 会话 / 记忆 / 知识库的增量同步
# --------------------------------------------------------------------------

def sync_chat(chat_path, owner="", src="", persona="", chan="", conversation_id=""):
    """同步一份聊天存档（JSONL）到检索索引。

    聊天存档是**追加写**的，所以按行号做游标，只索引新增的行 ——
    这是聊天场景下最主要的增量路径。
    """
    if not chat_path or not os.path.exists(chat_path):
        return {"indexed": 0, "cursor": None}
    key = "chat:%s:%s" % (chat_path, persona or "")
    conn = db.connection("main")
    row = conn.execute("SELECT cursor FROM index_state WHERE key=?", (key,)).fetchone()
    offset = 0
    if row:
        try:
            offset = int(json.loads(row[0]).get("offset") or 0)
        except Exception:
            offset = 0
    stat = os.stat(chat_path)
    if stat.st_size < offset:
        offset = 0                          # 文件被截断/替换，从头再来
    indexed = 0
    try:
        with open(chat_path, "rb") as f:
            f.seek(offset)
            while True:
                line_no = f.tell()
                raw = f.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    break                     # 写到一半的行，下次再读
                offset = f.tell()
                record = _parse_line(raw)
                if record is None:
                    continue
                role, content, stamp = record
                if not content.strip():
                    continue
                doc_id = "chat:%s:%s:%d" % (persona or "-", src or "-", line_no)
                search.upsert_doc(
                    doc_id, search.SCOPE_CHAT, content,
                    title="%s %s" % (role, stamp), source=os.path.basename(chat_path),
                    locator="%s#L%d" % (chat_path, line_no),
                    owner=owner, src=src, persona=persona, chan=chan,
                    content_hash=content_hash(content),
                    mtime=stat.st_mtime, size=len(content),
                     meta={"role": role, "line": line_no, "time": stamp,
                           "conversation_id": conversation_id})
                indexed += 1
    except OSError:
        pass
    with db.write():
        conn.execute("INSERT INTO index_state (key,cursor,updated_at) VALUES (?,?,?) "
                     "ON CONFLICT(key) DO UPDATE SET cursor=excluded.cursor, updated_at=excluded.updated_at",
                     (key, json.dumps({"offset": offset}), time.time()))
    return {"indexed": indexed, "cursor": offset}


def _parse_line(raw):
    try:
        record = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return None
    if not isinstance(record, dict):
        return None
    role = record.get("role") or ""
    if role not in ("user", "assistant", "system"):
        return None
    content = record.get("text", record.get("content", ""))
    if not isinstance(content, str):
        return None
    stamp = str(record.get("t") or record.get("timestamp") or "")
    return role, content, stamp


def sync_memories(items, owner="", src="", persona="", chan=""):
    """同步长期记忆到检索索引。`items` 是记忆库导出的条目列表。"""
    indexed = 0
    for item in items or []:
        text = item.get("text") if isinstance(item, dict) else str(item)
        if not text or not str(text).strip():
            continue
        item_id = (item.get("id") if isinstance(item, dict) else None) or \
                  content_hash(str(text))[:32]
        doc_id = "mem:%s:%s:%s:%s" % (persona or "-", chan or "-", src or "-", item_id)
        # 记忆条目的类型字段在 memory_store_v2 里叫 `type`（`kind` 是旧写法），
        # 两个都认，别把类型丢掉 —— 标题会显示成清一色的"记忆"。
        kind = ""
        if isinstance(item, dict):
            kind = item.get("kind") or item.get("type") or ""
        search.upsert_doc(
            doc_id, search.SCOPE_MEMORY, str(text),
            title=(kind or "记忆"),
            source="memory", locator=doc_id,
            owner=owner, src=src, persona=persona, chan=chan,
            content_hash=content_hash(str(text)),
            meta={"kind": kind or "event"})
        indexed += 1
    return {"indexed": indexed}


def sync_kb_chunks(chunks, owner="", src="", persona="", chan="", doc_ref="", size=0):
    """同步知识库块到检索索引。"""
    indexed = 0
    for chunk in chunks or []:
        text = chunk.get("text") if isinstance(chunk, dict) else str(chunk)
        if not text or not str(text).strip():
            continue
        source = chunk.get("source", "") if isinstance(chunk, dict) else ""
        ordinal = chunk.get("chunk", 0) if isinstance(chunk, dict) else 0
        doc_id = "kb:%s:%s:%s:%s" % (persona or "-", src or "-", source, ordinal)
        search.upsert_doc(
            doc_id, search.SCOPE_KB, str(text),
            title=(chunk.get("heading") or source or "文档") if isinstance(chunk, dict) else "文档",
            source=source, locator="%s#%s" % (source, ordinal),
            owner=owner, src=src, persona=persona, chan=chan,
            content_hash=content_hash(str(text)),
            size=int(size or 0),
            meta={"chunk": ordinal, "source": source, "doc_ref": doc_ref})
        indexed += 1
    return {"indexed": indexed}
