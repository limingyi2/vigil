# -*- coding: utf-8 -*-
"""委派子任务专用的**只读沙箱工具**。

与客户端 localtool 的区别（刻意分开，别合并）：

- localtool：服务端排队 → Electron 客户端认领 → 在用户真实电脑上执行，
  带白名单/确认/审计，是面向"她直接操作他电脑"的全功能通道；
- sandbox：Dispatcher 进程内直接执行，只给委派给 Ollama 的有界子任务用，
  永远只读、永远限定在合同校验过的 workspace 根内，无确认弹窗（子任务本身
  已经是用户授权过的只读 kind）。

安全做法沿用 vigil-eval/adapters/ollama_worker.py 实战过的 safe_path
（resolve 后必须在根内，挡绝对路径 / .. / junction / symlink 外指），
但实现重写：不 monkeypatch、不暴露写/执行工具、输出有明确大小上限。
"""
import fnmatch
import json
import os

MAX_LIST_ENTRIES = 200        # list/find 最多返回条目
MAX_READ_BYTES = 200 * 1024  # 单文件最多读 200KB
MAX_GREP_HITS = 200          # grep 最多命中行
MAX_GREP_FILES = 50          # grep 最多扫的文件数
_TEXT_SUFFIX = (
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".ini",
    ".cfg", ".conf", ".log", ".csv", ".xml", ".html", ".css", ".sh",
    ".bat", ".ps1", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".sql",
    ".env", ".toml", ".vue", ".jsx", ".tsx",
)


class SandboxError(Exception):
    """沙箱工具错误（路径越界/参数缺失/读取失败）。category 给 Dispatcher 记账。"""

    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


class ReadOnlySandbox(object):
    """绑定一个 workspace 根的只读工具集。所有相对路径都在根内解析。"""

    def __init__(self, root):
        self.root = os.path.realpath(root)

    def safe_path(self, rel):
        p = str(rel or "").strip().strip('"')
        if not p:
            raise SandboxError("bad_args", "缺少路径")
        # 绝对路径（盘符/UNC）直接拒 —— 子任务只能用相对工作区的路径
        if os.path.isabs(p) or p.startswith("\\\\"):
            raise SandboxError("path_escape", "只允许工作区内的相对路径：" + p)
        target = os.path.realpath(os.path.join(self.root, p.replace("/", os.sep)))
        if target != self.root and not (
                os.path.commonpath([self.root, target]) == self.root):
            raise SandboxError("path_escape", "路径越出工作区：" + p)
        return target

    # --- 工具实现（名字即 FC 里暴露给子模型的工具名）---
    def file_list(self, path="."):
        target = self.safe_path(path or ".")
        if not os.path.isdir(target):
            raise SandboxError("not_found", "不是目录：" + path)
        entries = []
        for name in sorted(os.listdir(target))[:MAX_LIST_ENTRIES]:
            full = os.path.join(target, name)
            entries.append({
                "name": name,
                "type": "dir" if os.path.isdir(full) else "file",
                "size": os.path.getsize(full)
                if os.path.isfile(full) else None,
            })
        return json.dumps(entries, ensure_ascii=False)

    def file_find(self, pattern, path="."):
        pattern = str(pattern or "").strip()
        if not pattern:
            raise SandboxError("bad_args", "file_find 需要 pattern（通配符）")
        base = self.safe_path(path or ".")
        if not os.path.isdir(base):
            raise SandboxError("not_found", "不是目录：" + path)
        out = []
        for dirpath, dirnames, filenames in os.walk(base):
            # lstat 判断：不跟随 junction/symlink 目录（与 realpath 双保险）
            dirnames[:] = [d for d in dirnames
                           if not os.path.islink(os.path.join(dirpath, d))]
            for name in sorted(filenames):
                if fnmatch.fnmatch(name.lower(), pattern.lower()):
                    full = os.path.realpath(os.path.join(dirpath, name))
                    if os.path.commonpath([self.root, full]) != self.root:
                        continue
                    out.append(os.path.relpath(full, self.root)
                               .replace(os.sep, "/"))
                if len(out) >= MAX_LIST_ENTRIES:
                    return json.dumps(out, ensure_ascii=False)
        return json.dumps(out, ensure_ascii=False)

    def read_file(self, path):
        target = self.safe_path(path)
        if not os.path.isfile(target):
            raise SandboxError("not_found", "文件不存在：" + path)
        if os.path.islink(path) and not self._inside(os.path.realpath(path)):
            raise SandboxError("path_escape", "链接指向工作区外：" + path)
        size = os.path.getsize(target)
        with open(target, "rb") as f:
            raw = f.read(MAX_READ_BYTES + 1)
        truncated = len(raw) > MAX_READ_BYTES
        text = raw[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        if truncated:
            text += "\n[文件超过 %d 字节，已截断]" % MAX_READ_BYTES
        return text

    def file_grep(self, pattern, path="."):
        pattern = str(pattern or "").strip()
        if not pattern:
            raise SandboxError("bad_args", "file_grep 需要 pattern（正则）")
        import re
        try:
            rx = re.compile(pattern, re.I)
        except re.error as e:
            raise SandboxError("bad_args", "非法正则：%s" % e)
        base = self.safe_path(path or ".")
        # 受控兼容（P3 修复 H03 式重复错误）：path 给的是文件时直接在该文件内
        # 搜，仍然只读、仍在沙箱内；并提示单文件定位首选 read_file。
        # 旧版一律回 "不是目录"，模型会原样重试同一个错误调用，白烧预算。
        if os.path.isfile(base):
            rel = os.path.relpath(base, self.root).replace(os.sep, "/")
            hits = []
            try:
                with open(base, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f):
                        if i >= 500:
                            break
                        if rx.search(line):
                            hits.append("%s:%d:%s"
                                        % (rel, i + 1, line.strip()[:160]))
                            if len(hits) >= MAX_GREP_HITS:
                                break
            except Exception:
                pass
            if hits:
                return "（提示：path 是文件，已只搜该文件；要看上下文用 read_file）\n" \
                       + "\n".join(hits)
            return "没有匹配：%s（文件 %s；要看上下文用 read_file）" % (pattern, rel)
        if not os.path.isdir(base):
            raise SandboxError("not_found",
                               "不是目录也不是文件：" + path)
        hits = []
        scanned = 0
        truncated = False
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if not os.path.islink(os.path.join(dirpath, d))]
            for name in sorted(filenames):
                ext = os.path.splitext(name)[1].lower()
                if ext and ext not in _TEXT_SUFFIX:
                    continue
                full = os.path.realpath(os.path.join(dirpath, name))
                if os.path.commonpath([self.root, full]) != self.root:
                    continue
                scanned += 1
                if scanned > MAX_GREP_FILES:
                    truncated = True
                    break
                try:
                    with open(full, encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f):
                            if i >= 500:
                                break
                            if rx.search(line):
                                rel = os.path.relpath(full, self.root).replace(os.sep, "/")
                                hits.append("%s:%d:%s"
                                            % (rel, i + 1, line.strip()[:160]))
                                if len(hits) >= MAX_GREP_HITS:
                                    truncated = True
                                    break
                except Exception:
                    continue
                if truncated:
                    break
            if truncated:
                break
        if not hits:
            return "没有匹配：%s" % pattern
        head = "找到 %d 处%s：\n" % (
            len(hits), "（已达上限，截断）" if truncated else "")
        return head + "\n".join(hits)

    def _inside(self, target):
        return target == self.root or os.path.commonpath(
            [self.root, target]) == self.root

    def version_of(self, rel, recursive=False):
        """只读工具结果缓存的版本标识（dispatcher 用，不暴露给子模型）。

        文件：(mtime_ns, size)；目录：自身 mtime；递归目录：walk 内最大
        mtime_ns + 文件数 —— 文件增删改都会让版本变化，缓存立即失效。
        缓存只存在于单次委派内，无跨用户泄露面。
        """
        target = self.safe_path(rel)
        try:
            st = os.stat(target)
            if os.path.isfile(target):
                return ("f", st.st_mtime_ns, st.st_size)
            if not recursive:
                return ("d", st.st_mtime_ns)
            latest = st.st_mtime_ns
            n = 0
            for dirpath, dirnames, filenames in os.walk(target):
                dirnames[:] = [d for d in dirnames
                               if not os.path.islink(os.path.join(dirpath, d))]
                n += len(filenames)
                for name in filenames:
                    full = os.path.realpath(os.path.join(dirpath, name))
                    if not self._inside(full):
                        continue
                    try:
                        mt = os.stat(full).st_mtime_ns
                        if mt > latest:
                            latest = mt
                    except OSError:
                        continue
            return ("r", latest, n)
        except OSError:
            return None

    def execute(self, name, args):
        """Dispatcher 统一入口：args 是 dict；只执行白名单内、存在的方法。"""
        args = args if isinstance(args, dict) else {}
        fn = {
            "file_list": self.file_list,
            "file_find": self.file_find,
            "read_file": self.read_file,
            "file_grep": self.file_grep,
        }.get(name)
        if fn is None:
            raise SandboxError("tool_not_allowed",
                               "沙箱里没有这个工具：%s" % name)
        try:
            return fn(**args)
        except SandboxError:
            raise
        except TypeError as e:
            raise SandboxError("bad_args", "参数不对：%s" % str(e)[:100])
        except Exception as e:
            raise SandboxError("tool_error", "%s: %s"
                               % (type(e).__name__, str(e)[:120]))


# FC 工具 schema（子循环用，参数全部相对工作区）
TOOL_DESCRIPTIONS = {
    "file_list": ("列出工作区内某目录的文件/子目录（仅名字和类型）。"
                  "参数 path：相对路径，默认 '.'。先勘察目录结构用它。"),
    "file_find": ("按文件名通配符（如 *.py、test_*.log）在工作区内递归查找，"
                  "返回相对路径列表。参数 pattern（必填）、path（默认 '.'）。"),
    "read_file": ("读取工作区内某个文本文件的内容（UTF-8，大文件截断到 200KB）。"
                  "参数 path：相对路径，必填。"),
    "file_grep": ("在工作区内按正则（忽略大小写）搜文本文件内容，返回 文件:行号:内容。"
                  "参数 pattern（正则，必填）、path（默认 '.'）。"),
}


def tool_spec(name):
    """生成某个沙箱工具的 OpenAI function spec。"""
    p_path = {"type": "string",
              "description": "工作区内的相对路径，如 . 或 src/auth.py"}
    if name == "file_list":
        params = {"type": "object",
                  "properties": {"path": p_path}, "required": []}
    elif name == "file_find":
        params = {"type": "object",
                  "properties": {"pattern": {"type": "string",
                                             "description": "文件名通配符，如 *.py"},
                                 "path": p_path},
                  "required": ["pattern"]}
    elif name == "read_file":
        params = {"type": "object",
                  "properties": {"path": p_path}, "required": ["path"]}
    elif name == "file_grep":
        params = {"type": "object",
                  "properties": {"pattern": {"type": "string",
                                             "description": "正则表达式"},
                                 "path": p_path},
                  "required": ["pattern"]}
    else:
        raise SandboxError("tool_not_allowed", name)
    return {"type": "function",
            "function": {"name": name,
                         "description": TOOL_DESCRIPTIONS[name],
                         "parameters": params}}
