"""Bounded project coding harness used by P17 agent runs.

The normal chat executor is intentionally a model/tool conversation loop.  A
coding run needs a stronger contract: every file change is constrained to one
workspace, patches are applied atomically, verification uses real exit codes,
and a failed check can trigger a bounded diagnose/repair cycle.  This module is
kept independent from the provider so it is useful in tests, local automation,
and the provider backed adapter in :mod:`agent_runs`.
"""

from __future__ import annotations

import dataclasses
import copy
import difflib
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


PHASES = ("PLAN", "IMPLEMENT", "VERIFY", "DIAGNOSE", "REPAIR", "COMPLETE")
TERMINAL_PHASES = frozenset(("COMPLETE", "FAILED", "PAUSED"))
_IGNORED_DIRS = frozenset((".git", ".hg", ".svn", "node_modules", "__pycache__",
                           ".venv", "venv", "dist", "build", ".vigil"))
_TEXT_EXTENSIONS = frozenset((".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".json",
                              ".toml", ".yaml", ".yml", ".md", ".txt", ".css", ".html",
                              ".xml", ".ini", ".cfg", ".sh", ".bat", ".ps1"))
_COMMAND_DENY = (
    re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--)", re.I),
    re.compile(r"\b(?:rm|rmdir|del|remove-item)\b[^\n]*(?:-rf|-recurse|/s|/q)", re.I),
    re.compile(r"\b(?:format|diskpart|mkfs|shutdown|bcdedit)\b", re.I),
    re.compile(r"(?:^|[|;&])\s*(?:curl|wget)\b[^\n]*\|\s*(?:sh|bash|powershell)", re.I),
)
# TestRunner is an execution boundary, not a general purpose shell.  Keep the
# allow list explicit.  The previous expression had a catch-all alternative
# that made `powershell`, arbitrary .bat files and any executable name look like
# a valid verification command.
_COMMAND_ALLOW = frozenset((
    "python", "python.exe", "py", "py.exe", "pytest",
    "node", "node.exe", "npm", "npm.cmd", "npx", "npx.cmd",
    "pnpm", "pnpm.cmd", "yarn", "yarn.cmd", "ruff", "mypy", "pyright",
    "tsc", "tsc.cmd", "cargo", "go", "git",
))
# `python3`, `python3.12`, `py3` ... are the normal names on Linux and in
# virtual environments, so they have to be recognised as the same interpreter.
_PYTHON_EXECUTABLE = re.compile(r"^(?:python|py)\d*(?:\.\d+)?(?:\.exe)?$", re.I)
_SHELL_META = re.compile(r"[|;&<>]|[`$()]", re.I)


def _command_tokens(command: str) -> list[str]:
    """Parse the small cross-platform command grammar used by TestRunner."""
    try:
        # POSIX shells strip quotes before exec; Windows keeps them.  Parsing
        # with the native rules keeps the argv identical to what the user
        # would have typed in that shell.
        tokens = shlex.split(command, posix=(os.name != "nt"))
    except ValueError as exc:
        raise CodingHarnessError("验证命令引号不匹配") from exc
    return [str(token).strip().strip('"') for token in tokens if str(token).strip()]


def _allowed_test_command(command: str) -> bool:
    if not command or _SHELL_META.search(command):
        return False
    tokens = _command_tokens(command)
    if not tokens:
        return False
    executable = re.split(r"[\\/]", tokens[0])[-1].lower()
    if executable not in _COMMAND_ALLOW and not _PYTHON_EXECUTABLE.match(executable):
        return False
    args = [str(value).strip('"') for value in tokens[1:]]
    if _PYTHON_EXECUTABLE.match(executable):
        # Only test/type-check modules may be launched through Python.  This
        # blocks `python -c`, arbitrary scripts and shell escape helpers.
        return len(args) >= 2 and args[0] in {"-m", "-B", "-u"} and (
            (args[0] != "-m" and len(args) >= 3 and args[1] == "-m"
             and args[2].lower() in {"pytest", "unittest", "compileall"})
            or (args[0] == "-m" and args[1].lower() in {"pytest", "unittest", "compileall"})
        )
    if executable in {"pytest", "ruff", "mypy", "pyright", "tsc"}:
        return True
    if executable in {"node", "node.exe"}:
        return "--test" in args
    if executable in {"npm", "npm.cmd", "npx", "npx.cmd", "pnpm", "pnpm.cmd", "yarn", "yarn.cmd"}:
        if not args:
            return False
        if args[0].lower() == "test":
            return True
        return (args[0].lower() == "run" and len(args) > 1 and
                args[1].lower() in {"test", "lint", "typecheck", "check", "build"})
    if executable in {"cargo", "go"}:
        return bool(args) and args[0].lower() == "test"
    if executable == "git":
        return bool(args) and args[0].lower() in {"status", "diff", "log", "grep"}
    return False


class CodingHarnessError(RuntimeError):
    """A safe, user visible harness failure."""


def _purge_bytecode_cache(target: str) -> list[str]:
    """Drop the cached bytecode of a module we just rewrote.

    CPython validates a timestamp based ``.pyc`` with the pair
    ``(source mtime truncated to whole seconds, source size)``.  A repair that
    keeps the same byte size and lands in the same wall-clock second therefore
    leaves the *previous* bytecode valid, so the next test run keeps executing
    the pre-repair code.  The agent then sees its own fix as "still broken",
    burns another diagnose/repair round and finally reports failure even though
    the file on disk is correct.  Clearing the cache forces a recompile.

    This is not a test-only concern: any project whose fix keeps the file size
    (a one-character operator change, a renamed import, a tweaked constant)
    hits it on a fast machine.
    """
    removed: list[str] = []
    stem = Path(target).stem
    candidates: list[Path] = []
    cache_dir = Path(target).parent / "__pycache__"
    if cache_dir.is_dir():
        # Every interpreter tag (cpython-312, pypy, opt-1 ...) is removed.
        candidates.extend(sorted(cache_dir.glob("%s.*.pyc" % stem)))
    try:
        candidates.append(Path(importlib.util.cache_from_source(target)))
    except (NotImplementedError, ValueError):
        pass
    for path in candidates:
        try:
            if path.is_file():
                path.unlink()
                removed.append(path.name)
        except OSError:
            continue
    return removed


class WorkspaceError(CodingHarnessError):
    pass


class BudgetExceeded(CodingHarnessError):
    pass


@dataclasses.dataclass
class PlanStep:
    id: str
    title: str
    description: str = ""
    depends_on: list[str] = dataclasses.field(default_factory=list)
    acceptance: list[str] = dataclasses.field(default_factory=list)
    verification: list[str] = dataclasses.field(default_factory=list)
    status: str = "pending"
    attempts: int = 0
    evidence: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class Plan:
    version: int
    complexity: str
    rationale: str
    steps: list[PlanStep]

    def as_dict(self) -> dict[str, Any]:
        return {"version": self.version, "complexity": self.complexity,
                "rationale": self.rationale,
                "steps": [step.as_dict() for step in self.steps]}


@dataclasses.dataclass
class CommandResult:
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def as_dict(self) -> dict[str, Any]:
        return {"command": self.command, "exit_code": self.exit_code,
                "stdout": self.stdout, "stderr": self.stderr,
                "duration_seconds": self.duration_seconds,
                "timed_out": self.timed_out, "passed": self.passed}


@dataclasses.dataclass
class ProjectSnapshot:
    root: str
    files: list[str]
    manifests: list[str]
    symbols: dict[str, list[str]]
    dependencies: dict[str, Any]
    file_contents: dict[str, str] = dataclasses.field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _now() -> float:
    return time.time()


def _clip(value: Any, limit: int = 12000) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "\n…[已截断]"


def _json_object(value: Any) -> Any:
    """Decode JSON returned by a model without accepting prose as operations."""
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        match = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if match:
            try:
                return json.loads(match.group(1))
            except ValueError:
                pass
    raise CodingHarnessError("模型没有返回合法 JSON 操作")


class WorkspaceGuard:
    """Containment and concurrency guard for a selected workspace."""

    def __init__(self, root: str, run_id: str = "coding"):
        candidate = os.path.realpath(os.path.abspath(os.path.expanduser(str(root or ""))))
        if not candidate or not os.path.isdir(candidate):
            raise WorkspaceError("工作区不存在或不是目录")
        if os.path.dirname(candidate) == candidate:
            raise WorkspaceError("不能把文件系统根目录作为工作区")
        self.root = candidate
        self.run_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(run_id or "coding"))[:80]
        self._lock_path = os.path.join(self.root, ".vigil-coding.lock")
        self._lock_fd: int | None = None

    def resolve(self, path: str, *, allow_missing: bool = False) -> str:
        raw = str(path or "").strip()
        if not raw:
            raise WorkspaceError("路径不能为空")
        candidate = os.path.realpath(os.path.abspath(raw if os.path.isabs(raw)
                                                   else os.path.join(self.root, raw)))
        try:
            inside = os.path.commonpath((self.root, candidate)) == self.root
        except ValueError:
            inside = False
        if not inside or candidate == self._lock_path:
            raise WorkspaceError("路径越过工作区边界：%s" % raw)
        if not allow_missing and not os.path.exists(candidate):
            raise WorkspaceError("文件不存在：%s" % self.relative(candidate))
        return candidate

    def relative(self, path: str) -> str:
        return os.path.relpath(os.path.realpath(path), self.root).replace(os.sep, "/")

    def acquire(self) -> None:
        payload = json.dumps({"run_id": self.run_id, "pid": os.getpid(), "created_at": _now()},
                             ensure_ascii=False)
        try:
            self._lock_fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self._lock_fd, payload.encode("utf-8"))
        except FileExistsError:
            try:
                with open(self._lock_path, "r", encoding="utf-8") as stream:
                    current = json.load(stream)
            except Exception:
                current = {}
            age = _now() - float(current.get("created_at") or 0)
            if age > 24 * 3600:
                try:
                    os.unlink(self._lock_path)
                    return self.acquire()
                except OSError:
                    pass
            raise WorkspaceError("工作区正在被另一个 Coding Agent 使用")

    def release(self) -> None:
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None
            try:
                os.unlink(self._lock_path)
            except OSError:
                pass

    def __enter__(self) -> "WorkspaceGuard":
        self.acquire()
        return self

    def __exit__(self, *_: Any) -> None:
        self.release()


class ProjectIndexer:
    def __init__(self, guard: WorkspaceGuard, max_file_chars: int = 30000):
        self.guard = guard
        self.max_file_chars = max(1000, int(max_file_chars))

    def scan(self) -> ProjectSnapshot:
        files: list[str] = []
        contents: dict[str, str] = {}
        symbols: dict[str, list[str]] = {}
        manifests: list[str] = []
        dependencies: dict[str, Any] = {}
        for base, dirs, names in os.walk(self.guard.root):
            dirs[:] = sorted(d for d in dirs if d not in _IGNORED_DIRS and not d.startswith("."))
            for name in sorted(names):
                path = os.path.join(base, name)
                rel = self.guard.relative(path)
                if rel.startswith(".vigil-"):
                    continue
                files.append(rel)
                if name in ("package.json", "pyproject.toml", "requirements.txt", "requirements-dev.txt",
                            "Cargo.toml", "go.mod", "pom.xml"):
                    manifests.append(rel)
                    try:
                        if name == "package.json":
                            dependencies[rel] = json.loads(Path(path).read_text(encoding="utf-8"))
                        elif name.endswith(".txt"):
                            dependencies[rel] = [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines()
                                                 if line.strip() and not line.lstrip().startswith("#")]
                        else:
                            dependencies[rel] = _clip(Path(path).read_text(encoding="utf-8"), 12000)
                    except (OSError, UnicodeDecodeError, ValueError):
                        dependencies[rel] = "<unreadable>"
                if Path(path).suffix.lower() not in _TEXT_EXTENSIONS:
                    continue
                try:
                    text = Path(path).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                contents[rel] = _clip(text, self.max_file_chars)
                found: list[str] = []
                for line_no, line in enumerate(text.splitlines(), 1):
                    if re.search(r"\b(?:def|class|function|const|let|export\s+(?:class|function))\b", line):
                        found.append("%d:%s" % (line_no, line.strip()[:180]))
                if found:
                    symbols[rel] = found[:200]
        return ProjectSnapshot(self.guard.root, files, manifests, symbols, dependencies, contents)

    def grep(self, pattern: str, glob: str | None = None, max_results: int = 100) -> list[dict[str, Any]]:
        regex = re.compile(pattern, re.I)
        output: list[dict[str, Any]] = []
        for rel in self.scan().files:
            if glob and not Path(rel).match(glob):
                continue
            if Path(rel).suffix.lower() not in _TEXT_EXTENSIONS:
                continue
            try:
                lines = Path(self.guard.resolve(rel)).read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for line_no, line in enumerate(lines, 1):
                if regex.search(line):
                    output.append({"path": rel, "line": line_no, "text": line[:300]})
                    if len(output) >= max_results:
                        return output
        return output


class PatchEngine:
    """Atomic file operations and a small, strict unified-diff applier."""

    def __init__(self, guard: WorkspaceGuard):
        self.guard = guard
        self.changed: list[dict[str, Any]] = []
        # Keep recovery copies outside the project so a Coding Agent never
        # leaves .vigil backup artifacts in the user's Git diff.
        key = hashlib.sha256((self.guard.root + "\0" + self.guard.run_id).encode()).hexdigest()[:20]
        self.backup_root = os.path.join(tempfile.gettempdir(), "vigil-coding-backups", key,
                                        time.strftime("%Y%m%d-%H%M%S"))

    def _backup(self, rel: str, before: str) -> str | None:
        if not before or len(before.encode("utf-8")) > 50 * 1024 * 1024:
            return None
        target = os.path.join(self.backup_root, *rel.replace("/", os.sep).split(os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        Path(target).write_text(before, encoding="utf-8")
        return target

    def _record(self, rel: str, before: str, after: str, kind: str = "write",
                backup_path: str | None = None, existed: bool = True) -> dict[str, Any]:
        digest = hashlib.sha256(after.encode("utf-8")).hexdigest()
        # existed：回滚一个新建文件要把它删掉，不能留一个空文件在那儿。
        # 只看 before 是不是空串分不出"原本就是空文件"和"原本没有这个文件"。
        entry = {"path": rel, "kind": kind, "existed_before": bool(existed),
                 "before_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
                 "after_sha256": digest, "diff": "".join(difflib.unified_diff(
                     before.splitlines(True), after.splitlines(True), fromfile=rel, tofile=rel))}
        if backup_path:
            entry["backup_path"] = backup_path
        self.changed.append(entry)
        return entry

    def write(self, path: str, text: str, expected_sha256: str | None = None) -> dict[str, Any]:
        target = self.guard.resolve(path, allow_missing=True)
        before = ""
        existed = os.path.exists(target)
        if os.path.exists(target):
            if os.path.isdir(target):
                raise WorkspaceError("不能把文件写到目录：%s" % path)
            try:
                before = Path(target).read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise WorkspaceError("只支持 UTF-8 文本文件：%s" % path) from exc
        if expected_sha256 and hashlib.sha256(before.encode("utf-8")).hexdigest() != expected_sha256:
            raise WorkspaceError("文件已被其他修改者更新，拒绝覆盖：%s" % path)
        backup_path = self._backup(self.guard.relative(target), before)
        data = str(text)
        fd, tmp = tempfile.mkstemp(prefix=".vigil-patch-", dir=self.guard.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        entry = self._record(self.guard.relative(target), before, data,
                             backup_path=backup_path, existed=existed)
        stale = _purge_bytecode_cache(target)
        if stale:
            # Keep the evidence next to the diff: a reviewer has to be able to
            # tell that the verifier really recompiled the repaired module.
            entry["purged_bytecode"] = stale
        return entry

    def delete(self, path: str) -> dict[str, Any]:
        target = self.guard.resolve(path)
        if os.path.isdir(target):
            raise WorkspaceError("为防止误删，Coding Agent 不直接删除目录：%s" % path)
        before = Path(target).read_text(encoding="utf-8")
        backup_path = self._backup(self.guard.relative(target), before)
        os.unlink(target)
        entry = self._record(self.guard.relative(target), before, "", "delete",
                             backup_path, existed=True)
        stale = _purge_bytecode_cache(target)
        if stale:
            entry["purged_bytecode"] = stale
        return entry

    def restore(self, path: str) -> dict[str, Any]:
        rel = self.guard.relative(self.guard.resolve(path, allow_missing=True))
        entry = next((item for item in reversed(self.changed) if item.get("path") == rel
                      and item.get("backup_path")), None)
        if not entry:
            raise WorkspaceError("没有可恢复的修改记录：%s" % path)
        backup = entry["backup_path"] if os.path.isabs(str(entry["backup_path"])) else self.guard.resolve(entry["backup_path"])
        return self.write(rel, Path(backup).read_text(encoding="utf-8"))

    def apply_unified_diff(self, patch: str) -> list[dict[str, Any]]:
        text = str(patch or "")
        if not text.strip():
            raise CodingHarnessError("补丁为空")
        # A multi-file patch is one operation from the model's point of view.
        # Keep the pre-image for every target and restore all of it when any
        # hunk fails.  The old implementation applied earlier blocks before a
        # later mismatch was discovered, leaving a half-applied patch behind.
        changed_before = len(self.changed)
        originals: dict[str, tuple[bool, str]] = {}

        def remember(rel: str) -> None:
            if rel in originals:
                return
            target = self.guard.resolve(rel, allow_missing=True)
            if os.path.isdir(target):
                raise WorkspaceError("补丁目标不能是目录：%s" % rel)
            if os.path.exists(target):
                try:
                    originals[rel] = (True, Path(target).read_text(encoding="utf-8"))
                except UnicodeDecodeError as exc:
                    raise WorkspaceError("只支持 UTF-8 文本文件：%s" % rel) from exc
            else:
                originals[rel] = (False, "")

        def rollback() -> None:
            for rel, (exists, content) in originals.items():
                target = self.guard.resolve(rel, allow_missing=True)
                if exists:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    fd, tmp = tempfile.mkstemp(prefix=".vigil-rollback-", dir=self.guard.root)
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                            stream.write(content)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(tmp, target)
                    finally:
                        if os.path.exists(tmp):
                            os.unlink(tmp)
                elif os.path.exists(target):
                    os.unlink(target)
            del self.changed[changed_before:]

        lines = text.splitlines(True)
        blocks: list[list[str]] = []
        current: list[str] = []
        for line in lines:
            if line.startswith("--- ") and current:
                blocks.append(current)
                current = []
            current.append(line)
        if current:
            blocks.append(current)
        receipts: list[dict[str, Any]] = []
        try:
            for block in blocks:
                if not block or not block[0].startswith("--- ") or len(block) < 2 or not block[1].startswith("+++ "):
                    raise CodingHarnessError("补丁格式无效")
                old_name = block[0].split("\t", 1)[0][4:].strip()
                new_name = block[1].split("\t", 1)[0][4:].strip()
                old_name = old_name[2:] if old_name.startswith("a/") else old_name
                new_name = new_name[2:] if new_name.startswith("b/") else new_name
                if old_name != "/dev/null":
                    remember(old_name)
                if new_name != "/dev/null":
                    remember(new_name)
                if new_name == "/dev/null":
                    receipts.append(self.delete(old_name))
                    continue
                target_name = new_name if new_name != "/dev/null" else old_name
                old = ""
                target = self.guard.resolve(target_name, allow_missing=True)
                if os.path.exists(target):
                    old = Path(target).read_text(encoding="utf-8")
                source = old.splitlines(True)
                out: list[str] = []
                cursor = 0
                index = 2
                while index < len(block):
                    header = block[index]
                    if not header.startswith("@@"):
                        index += 1
                        continue
                    match = re.search(r"\-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?", header)
                    if not match:
                        raise CodingHarnessError("补丁 hunk 位置无效")
                    old_start = int(match.group(1))
                    # Unified diffs use line 0 for a /dev/null pre-image.
                    start = 0 if old_start == 0 else old_start - 1
                    if start < cursor or start > len(source):
                        raise CodingHarnessError("补丁 hunk 超出当前文件：%s" % target_name)
                    out.extend(source[cursor:start])
                    cursor = start
                    index += 1
                    while index < len(block) and not block[index].startswith("@@"):
                        line = block[index]
                        if line.startswith("\\ No newline"):
                            index += 1
                            continue
                        marker, payload = line[:1], line[1:]
                        if marker == " ":
                            if cursor >= len(source) or source[cursor] != payload:
                                raise CodingHarnessError("补丁上下文与当前文件不匹配：%s" % target_name)
                            out.append(source[cursor]); cursor += 1
                        elif marker == "-":
                            if cursor >= len(source) or source[cursor] != payload:
                                raise CodingHarnessError("补丁删除行与当前文件不匹配：%s" % target_name)
                            cursor += 1
                        elif marker == "+":
                            out.append(payload)
                        else:
                            raise CodingHarnessError("补丁行无效：%s" % line[:80])
                        index += 1
                out.extend(source[cursor:])
                receipts.append(self.write(target_name, "".join(out)))
            return receipts
        except Exception:
            rollback()
            raise


class TestRunner:
    """Run one bounded development command inside the workspace.

    The command is executed as an explicit argv with ``shell=False``.  The
    earlier implementation went through ``shell=True`` and relied on a regular
    expression to filter the shell string, which is not a security boundary:
    the filter sees a *string*, the shell sees a *program*.  Now the parsed
    argv is what actually runs, so metacharacters are never interpreted and
    the allow list is checked against the real executable.
    """

    # pytest must not try to collect this helper class as a test suite.
    __test__ = False

    def __init__(self, guard: WorkspaceGuard, timeout: int = 600):
        self.guard = guard
        self.timeout = max(1, int(timeout))

    def _resolve(self, tokens: list[str]) -> list[str]:
        """Turn the parsed command into a runnable argv."""
        raw = tokens[0]
        name = re.split(r"[\\/]", raw)[-1]
        resolved = shutil.which(raw) or shutil.which(name)
        if not resolved and _PYTHON_EXECUTABLE.match(name.lower()):
            # No interpreter on PATH: fall back to the one running the brain
            # so a workspace without a global python can still be verified.
            resolved = sys.executable
        if not resolved:
            raise CodingHarnessError("找不到验证命令的可执行文件：%s" % raw)
        return [resolved] + tokens[1:]

    def run(self, command: str, timeout: int | None = None) -> CommandResult:
        command = str(command or "").strip()
        if not command:
            raise CodingHarnessError("验证命令不能为空")
        if any(pattern.search(command) for pattern in _COMMAND_DENY):
            raise CodingHarnessError("命令包含被禁止的危险操作")
        if not _allowed_test_command(command):
            raise CodingHarnessError("命令不在 Coding Agent 开发命令范围内：%s" % command[:120])
        argv = self._resolve(_command_tokens(command))
        started = time.monotonic()
        try:
            completed = subprocess.run(argv, cwd=self.guard.root, shell=False, capture_output=True,
                                       text=True, encoding="utf-8", errors="replace",
                                       timeout=max(1, int(timeout or self.timeout)))
            return CommandResult(command, completed.returncode, _clip(completed.stdout),
                                 _clip(completed.stderr), round(time.monotonic() - started, 3))
        except subprocess.TimeoutExpired as exc:
            return CommandResult(command, None, _clip(exc.stdout), _clip(exc.stderr),
                                 round(time.monotonic() - started, 3), timed_out=True)


def _complexity(request: str) -> tuple[str, str]:
    text = str(request or "").strip()
    markers = ("并且", "同时", "多个", "重构", "测试", "修复", "实现", "修改", "文件",
               "bug", "bugfix", "refactor", "test", "build", "依赖", "模块")
    score = len(text.split()) + sum(2 for marker in markers if marker.lower() in text.lower())
    # Chinese requests often have no spaces, so token count alone misses a
    # project-wide task that names several distinct operations. Reuse this
    # structural signal in both the task category and Harness planner choice.
    project_wide = any(marker in text.lower() for marker in (
        "整个项目", "代码库", "仓库", "全仓", "多个模块", "多个子系统",
        "所有模块", "各个模块", "整个代码库"))
    operation_groups = (
        ("检查", "审查", "分析", "inspect", "review", "analyze", "analyse"),
        ("修复", "修好", "修缺陷", "fix", "repair"),
        ("修改", "编辑", "实现", "重构", "modify", "edit", "implement", "refactor"),
        ("运行", "执行", "测试", "验证", "构建", "编译", "run", "test", "verify", "build", "compile"),
    )
    distinct_operations = sum(any(marker in text.lower() for marker in group)
                              for group in operation_groups)
    if project_wide and distinct_operations >= 2:
        return "complex", "项目级范围包含多个不同工程操作"
    if score >= 14 or len(text) >= 160:
        return "complex", "任务包含多个工程动作或验收目标"
    if score >= 6:
        return "moderate", "任务需要代码修改并验证"
    return "simple", "任务范围较小，仍保留实现和验证证据"


def should_plan(request: str) -> bool:
    """Return whether a task warrants the model planning pass."""
    return _complexity(request)[0] != "simple"


_CODING_REQUEST_HINTS = (
    "代码", "源码", "实现", "修改", "编辑", "修复", "重构", "创建", "写入",
    "删除", "构建", "运行测试", "bug", "bugfix", "refactor", "implement",
    "modify", "edit", "fix", "build", "compile", "pytest", "unittest", "npm",
    "typescript", "javascript", "python", "rust", "golang",
)


def looks_like_coding_request(request: str) -> bool:
    """Detect a development request when a conversation has a workspace.

    This is deliberately a small routing hint, never an authorization check.
    The workspace record and the normal admin/agent gates remain authoritative.
    """
    text = str(request or "").strip().lower()
    return bool(text) and any(marker in text for marker in _CODING_REQUEST_HINTS)


class CodingHarness:
    """PLAN → IMPLEMENT → VERIFY → DIAGNOSE → REPAIR → VERIFY → COMPLETE."""

    def __init__(self, workspace_root: str, *, run_id: str = "coding", max_repairs: int = 2,
                 max_seconds: int = 1800, verify_commands: Sequence[str] | None = None,
                 planner: Callable[..., Any] | None = None,
                 implementer: Callable[..., Any] | None = None,
                 diagnostician: Callable[..., Any] | None = None,
                 repairer: Callable[..., Any] | None = None,
                 replanner: Callable[..., Any] | None = None,
                 stop_check: Callable[[], bool] | None = None,
                 pre_effect_check: Callable[[], None] | None = None,
                 event_sink: Callable[[str, Mapping[str, Any]], None] | None = None,
                 resume_state: Mapping[str, Any] | None = None):
        self.guard = WorkspaceGuard(workspace_root, run_id)
        self.indexer = ProjectIndexer(self.guard)
        self.patches = PatchEngine(self.guard)
        self.tests = TestRunner(self.guard, timeout=min(max_seconds, 900))
        self.max_repairs = max(0, int(max_repairs))
        self.max_seconds = max(1, int(max_seconds))
        self.verify_commands = [str(command) for command in (verify_commands or []) if str(command).strip()]
        self.planner = planner
        self.implementer = implementer
        self.diagnostician = diagnostician
        self.repairer = repairer
        self.replanner = replanner
        self.stop_check = stop_check
        self.pre_effect_check = pre_effect_check
        self.event_sink = event_sink
        self.resume_state = dict(resume_state or {})
        self.phase = "PLAN"
        self.events: list[dict[str, Any]] = []
        cursor = self.resume_state.get("harness_event_cursor") or {}
        self.event_seq = (int(cursor.get("seq") or 0)
                          if cursor.get("run_id") == self.guard.run_id else 0)
        self.plan: Plan | None = None
        self.snapshot: ProjectSnapshot | None = None
        self.verification: list[dict[str, Any]] = []
        self.diagnostics: list[dict[str, Any]] = []
        self.repair_count = 0
        self.started = 0.0

    def emit(self, kind: str, payload: Mapping[str, Any] | None = None) -> None:
        self.event_seq += 1
        event_payload = dict(payload or {})
        event_payload.setdefault("_harness_event_id", "%s:%d" % (self.guard.run_id, self.event_seq))
        event = {"type": kind, "phase": self.phase, "ts": _now(), "payload": event_payload}
        self.events.append(event)
        if self.event_sink:
            self.event_sink(kind, event["payload"])

    def transition(self, phase: str) -> None:
        if phase not in PHASES and phase not in TERMINAL_PHASES:
            raise CodingHarnessError("未知执行阶段：%s" % phase)
        self.phase = phase
        self.emit("coding_phase", {"phase": phase})

    def _budget(self) -> None:
        if self.stop_check and self.stop_check():
            raise CodingHarnessError("任务已被暂停或取消")
        if time.monotonic() - self.started > self.max_seconds:
            raise BudgetExceeded("Coding Agent 执行预算已用尽")

    def _pre_effect(self) -> None:
        """Recheck stop state and trusted workspace scope immediately before effects."""
        # Keep the existing low-level helper contract used by offline tests and
        # internal callers that exercise an operation batch before ``run()``.
        if self.started or self.stop_check:
            self._budget()
        if self.pre_effect_check:
            self.pre_effect_check()

    def _default_plan(self, request: str, snapshot: ProjectSnapshot, complexity: str, rationale: str) -> Plan:
        commands = list(self.verify_commands)
        if not commands:
            if any(name.endswith((".toml", ".txt")) and "pytest" in str(snapshot.dependencies.get(name, ""))
                   for name in snapshot.manifests):
                commands = ["python -m pytest -q"]
            elif any(Path(name).name.startswith("test_") or Path(name).name.endswith("_test.py")
                     for name in snapshot.files):
                commands = ["python -m pytest -q"]
            elif "package.json" in snapshot.manifests:
                package = snapshot.dependencies.get("package.json") or {}
                scripts = (package.get("scripts") or {}) if isinstance(package, dict) else {}
                commands = ["npm test"] if "test" in scripts else []
        steps = [PlanStep("analyze", "分析项目结构", "读取目录、依赖和相关符号", acceptance=["得到项目索引"]),
                 PlanStep("implement", "实现修改", request, ["analyze"], ["所有修改在工作区内"], []),
                 PlanStep("verify", "执行验证", "运行真实构建、类型检查或测试", ["implement"],
                          ["命令退出码为 0"], commands)]
        return Plan(1, complexity, rationale, steps)

    def _simple_plan(self, request: str, snapshot: ProjectSnapshot) -> Plan:
        commands = list(self.verify_commands)
        if not commands:
            commands = self._default_plan(request, snapshot, "simple", "").steps[-1].verification
        return Plan(1, "simple", "任务范围较小，跳过额外规划模型调用",
                    [PlanStep("implement", "实现修改", request, acceptance=["修改在工作区内"]),
                     PlanStep("verify", "执行验证", "运行真实验证命令", ["implement"],
                              ["命令退出码为 0"], commands)])

    def _normalize_plan(self, value: Any, request: str, complexity: str, rationale: str) -> Plan:
        if value is None:
            return self._default_plan(request, self.snapshot, complexity, rationale)  # type: ignore[arg-type]
        raw = _json_object(value)
        if isinstance(raw, dict):
            raw_steps = raw.get("steps") or raw.get("plan") or []
            complexity = str(raw.get("complexity") or complexity)
            rationale = str(raw.get("rationale") or rationale)
        else:
            raw_steps = raw
        if not isinstance(raw_steps, list) or not raw_steps:
            return self._default_plan(request, self.snapshot, complexity, rationale)  # type: ignore[arg-type]
        steps: list[PlanStep] = []
        for idx, item in enumerate(raw_steps, 1):
            if isinstance(item, str):
                item = {"title": item, "description": item}
            if not isinstance(item, dict):
                continue
            sid = str(item.get("id") or "step_%d" % idx)
            status = str(item.get("status") or "pending")
            if status not in {"pending", "running", "completed", "failed"}:
                status = "pending"
            try:
                attempts = max(0, int(item.get("attempts") or 0))
            except (TypeError, ValueError):
                attempts = 0
            evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
            steps.append(PlanStep(sid, str(item.get("title") or item.get("name") or sid),
                                  str(item.get("description") or ""),
                                  [str(v) for v in item.get("depends_on", item.get("dependencies", [])) or []],
                                  [str(v) for v in item.get("acceptance", item.get("acceptance_criteria", [])) or []],
                                  [str(v) for v in item.get("verification", item.get("verify", [])) or []],
                                  status=status if status != "running" else "pending",
                                  attempts=attempts, evidence=copy.deepcopy(evidence)))
        version = 1
        if isinstance(raw, dict):
            try:
                version = max(1, int(raw.get("version") or 1))
            except (TypeError, ValueError):
                version = 1
        return Plan(version, complexity, rationale, steps or self._default_plan(request, self.snapshot, complexity, rationale).steps)

    def _call(self, callback: Callable[..., Any] | None, *args: Any) -> Any:
        if callback is None:
            return None
        try:
            return callback(*args)
        except TypeError:
            # Keep callbacks ergonomic for tests and providers with a context-only signature.
            return callback(args[-1])

    def _apply_operations(self, operations: Any) -> list[dict[str, Any]]:
        raw = _json_object(operations) if isinstance(operations, str) else operations
        if isinstance(raw, dict):
            if "operations" in raw:
                raw = raw["operations"]
            elif "changes" in raw:
                raw = raw["changes"]
            else:
                raw = [raw]
        if not isinstance(raw, list):
            raise CodingHarnessError("实现器没有返回 operations 数组")
        receipts: list[dict[str, Any]] = []
        # 🔴 一批 operations 是一个整体：第 3 个操作因为哈希冲突被拒时，
        # 前两个已经落盘的改动必须退回原样。半批落盘等于给工作区留下
        # 没人知道来源的修改，台账和磁盘也不再对得上。
        changed_before = len(self.patches.changed)
        try:
            for op in raw:
                if not isinstance(op, dict):
                    raise CodingHarnessError("实现操作必须是对象")
                kind = str(op.get("op") or op.get("type") or "").lower()
                self._pre_effect()
                if kind in ("write", "create", "replace"):
                    receipts.append(self.patches.write(str(op.get("path") or ""), str(op.get("content", op.get("text", ""))),
                                                      op.get("expected_sha256")))
                elif kind in ("patch", "diff"):
                    receipts.extend(self.patches.apply_unified_diff(str(op.get("patch") or op.get("diff") or "")))
                elif kind == "delete":
                    receipts.append(self.patches.delete(str(op.get("path") or "")))
                elif kind in ("command", "run"):
                    result = self.tests.run(str(op.get("command") or op.get("cmd") or ""), op.get("timeout"))
                    receipts.append({"kind": "command", **result.as_dict()})
                elif kind in ("inspect", "read"):
                    path = self.guard.resolve(str(op.get("path") or ""))
                    receipts.append({"kind": "inspect", "path": self.guard.relative(path),
                                     "content": _clip(Path(path).read_text(encoding="utf-8"), 30000)})
                elif kind in ("noop", "note"):
                    receipts.append({"kind": "note", "text": _clip(op.get("text"), 1000)})
                else:
                    raise CodingHarnessError("不支持的实现操作：%s" % kind)
        except Exception:
            self._rollback_since(changed_before)
            raise
        for receipt in receipts:
            self.emit("coding_file" if receipt.get("path") else "coding_operation", receipt)
        return receipts

    @staticmethod
    def _operation_count(operations: Any) -> int:
        """Return only a bounded count for dispatch telemetry; never persist arguments."""
        raw = _json_object(operations) if isinstance(operations, str) else operations
        if isinstance(raw, dict):
            raw = raw.get("operations", raw.get("changes", [raw]))
        return min(len(raw), 200) if isinstance(raw, list) else 1

    def _rollback_since(self, index: int) -> list[str]:
        """把这一批里已经落盘的修改按逆序退回原样。

        index 是本批开始前 ``patches.changed`` 的长度；退到它就说明整批都撤回了。
        任何一步撤不回去都要留痕（``coding_rollback_failed``）—— 悄悄跳过会让
        工作区停在一个既不是原样、也没人知道的状态上。
        """
        restored: list[str] = []
        while len(self.patches.changed) > index:
            entry = self.patches.changed[-1]
            path = str(entry.get("path") or "")
            try:
                self._restore_entry(entry)
            except Exception as exc:
                self.emit("coding_rollback_failed",
                          {"path": path, "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
                break
            self.patches.changed.pop()
            restored.append(path)
        if restored:
            self.emit("coding_rollback", {"restored": list(reversed(restored))})
        return restored

    def _restore_entry(self, entry: Mapping[str, Any]) -> None:
        """按台账把单个条目恢复成写入前的样子。"""
        rel = str(entry.get("path") or "")
        target = self.guard.resolve(rel, allow_missing=True)
        if not entry.get("existed_before", True):
            if os.path.exists(target):
                os.unlink(target)
            _purge_bytecode_cache(target)
            return
        backup = str(entry.get("backup_path") or "")
        if not backup:
            raise WorkspaceError("没有可用于回滚的备份：%s" % rel)
        source = backup if os.path.isabs(backup) else self.guard.resolve(backup)
        content = Path(source).read_text(encoding="utf-8")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".vigil-rollback-", dir=self.guard.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        _purge_bytecode_cache(target)

    def _verify(self, commands: Sequence[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for command_index, command in enumerate(commands, 1):
            self._budget()
            self.emit("coding_test_started", {"command": command,
                                               "test_index": command_index})
            self._pre_effect()
            result = self.tests.run(command)
            data = result.as_dict()
            results.append(data)
            self.emit("coding_test", data)
            if not result.passed:
                break
        self.emit("coding_verification", {"count": len(results),
            "passed": bool(results) and all(item.get("passed") for item in results)})
        return results

    def _failure(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        failed = next((item for item in results if not item.get("passed")), results[-1] if results else {})
        return {"command": failed.get("command"), "exit_code": failed.get("exit_code"),
                "stderr": _clip(failed.get("stderr"), 8000), "stdout": _clip(failed.get("stdout"), 8000),
                "changed_files": [item.get("path") for item in self.patches.changed]}

    def run(self, request: str) -> dict[str, Any]:
        self.started = time.monotonic()
        request = str(request or "").strip()
        if not request:
            raise CodingHarnessError("Coding Agent 任务不能为空")
        try:
            with self.guard:
                self.snapshot = self.indexer.scan()
                self.emit("coding_analysis", {"root": self.guard.root, "files": self.snapshot.files,
                                               "manifests": self.snapshot.manifests,
                                               "symbols": self.snapshot.symbols})
                complexity, rationale = _complexity(request)
                self.transition("PLAN")
                saved_plan = self.resume_state.get("plan")
                if saved_plan:
                    # A resumed run reuses the durable plan and only reopens
                    # steps that were not recorded as completed.  This keeps a
                    # pause/restart from asking the model to repeat already
                    # applied multi-file edits.
                    self.plan = self._normalize_plan(saved_plan, request, complexity,
                                                     "从已保存的 Coding 检查点恢复")
                    self.emit("coding_resume", {"phase": self.resume_state.get("phase"),
                                                 "completed_steps": [s.id for s in self.plan.steps
                                                                      if s.status == "completed"]})
                else:
                    plan_value = (self._call(self.planner, request, self.snapshot.as_dict())
                                  if complexity != "simple" else None)
                    self.plan = (self._simple_plan(request, self.snapshot)
                                 if complexity == "simple" else
                                 self._normalize_plan(plan_value, request, complexity, rationale))
                self.emit("coding_plan", self.plan.as_dict())
                self.transition("IMPLEMENT")
                for step in self.plan.steps:
                    self._budget()
                    if step.status == "completed":
                        continue
                    if any(next((s.status for s in self.plan.steps if s.id == dep), "pending") != "completed"
                           for dep in step.depends_on):
                        raise CodingHarnessError("计划依赖尚未完成：%s" % step.id)
                    step.status = "running"
                    step.attempts += 1
                    self.emit("coding_step", step.as_dict())
                    if step.id == "analyze":
                        step.status = "completed"
                        step.evidence.append({"kind": "index", "files": len(self.snapshot.files)})
                        self.emit("coding_step", step.as_dict())
                        continue
                    if step.id == "verify":
                        # Verification is driven by the real command runner below;
                        # never let a model claim a test step completed.
                        step.status = "completed"
                        self.emit("coding_step", step.as_dict())
                        continue
                    operations = self._call(self.implementer, request, step.as_dict(), self.snapshot.as_dict())
                    if operations is None:
                        raise CodingHarnessError("没有配置实现器，无法修改项目文件")
                    # A pause/cancel may arrive while the model is generating.
                    # Re-check before applying its returned side effects.
                    self._budget()
                    self.emit("coding_operations_started", {
                        "step_id": step.id, "attempt": step.attempts,
                        "operation_count": self._operation_count(operations)})
                    receipts = self._apply_operations(operations)
                    self.emit("coding_operations_completed", {
                        "step_id": step.id, "attempt": step.attempts, "receipts": receipts})
                    step.evidence.extend(receipts)
                    step.status = "completed"
                    self.emit("coding_step", step.as_dict())
                self.transition("VERIFY")
                commands: list[str] = []
                for step in self.plan.steps:
                    commands.extend(step.verification)
                commands.extend(command for command in self.verify_commands if command not in commands)
                self.verification = self._verify(commands) if commands else []
                if not commands:
                    self.emit("coding_verification", {"count": 0, "passed": False})
                while self.verification and not all(item.get("passed") for item in self.verification):
                    self._budget()
                    failure = self._failure(self.verification)
                    self.transition("DIAGNOSE")
                    diagnosis = self._call(self.diagnostician, request, failure,
                                           self.snapshot.as_dict(), self.patches.changed) or failure
                    if isinstance(diagnosis, str):
                        diagnosis = {"summary": diagnosis}
                    diagnosis = dict(diagnosis)
                    self.diagnostics.append(diagnosis)
                    self.emit("coding_diagnosis", diagnosis)
                    if self.repair_count >= self.max_repairs or self.repairer is None:
                        self.phase = "FAILED"
                        break
                    self.transition("REPAIR")
                    repair = self._call(self.repairer, request, diagnosis,
                                        self.snapshot.as_dict(), self.patches.changed)
                    self._budget()
                    self.emit("coding_repair_started", {"repair_count": self.repair_count + 1,
                                                          "operation_count": self._operation_count(repair)})
                    receipts = self._apply_operations(repair)
                    self.repair_count += 1
                    self.emit("coding_repair", {"repair_count": self.repair_count, "receipts": receipts})
                    if self.replanner:
                        replanned = self._call(self.replanner, self.plan.as_dict(), diagnosis)
                        if replanned is not None:
                            completed = {step.id: step for step in self.plan.steps
                                         if step.status == "completed"}
                            revised = self._normalize_plan(replanned, request, self.plan.complexity,
                                                           "根据验证失败动态调整计划")
                            for step in revised.steps:
                                prior = completed.get(step.id)
                                if prior is not None:
                                    # A replan must not erase durable completion
                                    # evidence and cause a resumed Run to replay edits.
                                    step.status = "completed"
                                    step.attempts = max(step.attempts, prior.attempts)
                                    step.evidence = copy.deepcopy(prior.evidence)
                            self.plan = revised
                            self.plan.version += 1
                            self.emit("coding_plan_updated", self.plan.as_dict())
                    self.transition("VERIFY")
                    self.verification = self._verify(commands)
                if self.phase == "FAILED":
                    return self._result("failed", request)
                self.transition("COMPLETE")
                return self._result("succeeded", request)
        except (CodingHarnessError, OSError, ValueError) as exc:
            self.phase = "FAILED"
            self.emit("coding_failed", {"error": str(exc)[:500]})
            return self._result("failed", request, error=str(exc))

    def _result(self, status: str, request: str, error: str | None = None) -> dict[str, Any]:
        diff = "".join(entry.get("diff", "") for entry in self.patches.changed)
        return {"status": status, "phase": self.phase, "request": request,
                "workspace_root": self.guard.root, "plan": self.plan.as_dict() if self.plan else None,
                "verification": self.verification, "diagnostics": self.diagnostics,
                "repair_count": self.repair_count, "changed_files": [entry.get("path") for entry in self.patches.changed],
                "changes": self.patches.changed, "diff": diff[:120000], "events": self.events,
                "error": error}


__all__ = ["CodingHarness", "CodingHarnessError", "WorkspaceError", "BudgetExceeded", "WorkspaceGuard",
           "ProjectIndexer", "PatchEngine", "TestRunner", "Plan", "PlanStep", "ProjectSnapshot",
           "CommandResult", "PHASES", "should_plan", "looks_like_coding_request"]
