# -*- coding: utf-8 -*-
"""Branch Atlas: read-only local Git branch and commit topology viewer."""
import os
import subprocess


NAME = "branch-atlas"
DESC = "只读查看本地 Git 仓库的分支、提交拓扑和工作区状态"

_MAX_PATH_CHARS = 4096
_MAX_BRANCHES = 100
_MAX_GRAPH_CHARS = 18000


def _run_git(root, args, timeout=8):
    """Run a fixed, read-only Git command without a shell or interactive prompt."""
    env = os.environ.copy()
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "GIT_OPTIONAL_LOCKS": "0",
    })
    command = [
        "git", "-c", "core.fsmonitor=false", "-c", "core.pager=cat",
        "-C", root,
    ] + list(args)
    try:
        result = subprocess.run(
            command, cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            env=env, shell=False, check=False,
        )
    except FileNotFoundError:
        raise ValueError("当前运行 Vigil Brain 的设备没有安装 Git，或 Git 未加入 PATH")
    except subprocess.TimeoutExpired:
        raise ValueError("Git 查询超时；仓库可能过大或磁盘响应缓慢")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ValueError("Git 查询失败：" + (detail[:180] or "未知错误"))
    return (result.stdout or "").strip()


def _inspect_repo(body, query=None):
    """Return bounded, read-only repository metadata for the selected root."""
    raw = str((body or {}).get("path") or "").strip()
    if not raw:
        return {"ok": False, "err": "请先选择一个本地 Git 仓库文件夹"}
    if len(raw) > _MAX_PATH_CHARS:
        return {"ok": False, "err": "文件夹路径过长"}
    if any(ord(char) < 32 for char in raw):
        return {"ok": False, "err": "文件夹路径不能包含控制字符"}
    if not os.path.isabs(raw):
        return {"ok": False, "err": "请提供绝对路径，或通过“选择仓库”按钮选择本地文件夹"}
    root = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(root):
        return {"ok": False, "err": "当前 Brain 无法访问这个文件夹；请在运行本地 Brain 的设备上选择本地仓库"}
    if os.path.islink(root) or getattr(os.path, "isjunction", lambda _p: False)(root):
        return {"ok": False, "err": "暂不接受符号链接或 junction 形式的仓库路径"}

    try:
        repo_root = _run_git(root, ["rev-parse", "--show-toplevel"])
        bare = _run_git(root, ["rev-parse", "--is-bare-repository"])
        if bare == "true":
            return {"ok": False, "err": "当前版本需要工作区仓库，不支持 bare repository"}
        if os.path.normcase(os.path.realpath(repo_root)) != os.path.normcase(os.path.realpath(root)):
            return {"ok": False, "err": "请选择 Git 仓库根目录，而不是仓库中的子文件夹"}
        current = _run_git(root, ["branch", "--show-current"]) or "(detached HEAD)"
        branches = _run_git(root, ["branch", "--all", "--no-color"]).splitlines()
        graph = _run_git(root, [
            "log", "--graph", "--oneline", "--decorate", "--all",
            "--date-order", "--no-color", "-n", "80",
        ])
        status = _run_git(root, ["status", "--short", "--branch"])
        branch_items = [line[:240] for line in branches[:_MAX_BRANCHES]]
        return {
            "ok": True,
            "repo": os.path.basename(root.rstrip("/\\ ")) or root,
            "path": root,
            "branch": current[:240],
            "branches": branch_items,
            "branch_count": len(branches),
            "branches_truncated": len(branches) > _MAX_BRANCHES,
            "graph": graph[:_MAX_GRAPH_CHARS],
            "graph_truncated": len(graph) > _MAX_GRAPH_CHARS,
            "status": status[:3000],
        }
    except ValueError as exc:
        return {"ok": False, "err": str(exc)[:240]}


def inspect_repo(body, query):
    """POST /api/plugin/branch-atlas/inspect."""
    return _inspect_repo(body, query)


def branch_atlas_inspect(path):
    """Chat tool: only inspect a path explicitly provided by the user."""
    result = _inspect_repo({"path": path})
    if not result.get("ok"):
        return "Branch Atlas 无法读取该仓库：" + str(result.get("err") or "未知错误")
    branches = "\n".join(result.get("branches") or []) or "（没有分支）"
    graph = result.get("graph") or "（仓库中没有可显示的提交）"
    status = result.get("status") or "（工作区状态为空）"
    return (
        "仓库：{repo}\n路径：{path}\n当前分支：{branch}\n"
        "分支数量：{branch_count}\n分支列表：\n{branches}\n"
        "工作区状态：\n{status}\n最近提交拓扑：\n{graph}"
    ).format(
        repo=result.get("repo", ""), path=result.get("path", ""),
        branch=result.get("branch", ""), branch_count=result.get("branch_count", 0),
        branches=branches[:5000], status=status[:1200], graph=graph[:5000],
    )


TOOLS = {"branch_atlas_inspect": branch_atlas_inspect}
TOOL_SPECS = {
    "branch_atlas_inspect": {
        "desc": "只读查看用户明确提供的本地 Git 仓库分支、最近提交拓扑和工作区状态；不会切换分支、提交、拉取或修改文件。",
        "params": [("path", "用户明确提供的本地 Git 仓库绝对路径", True)],
        "arg_param": "path",
    },
}
TOOL_HELP = (
    "Branch Atlas 用于查看本地 Git 仓库的分支与提交拓扑。"
    "只有用户明确要求检查仓库并提供了本地仓库绝对路径时才调用 branch_atlas_inspect。"
    "该工具只执行固定的只读 Git 查询，不执行 checkout、commit、pull 或文件写入。"
)
TOOL_HELP_NATIVE = TOOL_HELP

ROUTES = [
    ("POST", "/api/plugin/branch-atlas/inspect", inspect_repo),
]
