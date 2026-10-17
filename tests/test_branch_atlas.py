"""Tests for the read-only Branch Atlas Git inspection plugin."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))
PLUGIN_DIR = ROOT / "brain" / "plugins" / "branch-atlas"
SPEC = importlib.util.spec_from_file_location("branch_atlas_test", PLUGIN_DIR / "__init__.py")
branch_atlas = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(branch_atlas)


def git(root, *args):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True, capture_output=True, text=True, env=env,
    )


def make_repo(root):
    if not shutil.which("git"):
        pytest.skip("Git is not installed in this test environment")
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True, text=True)
    git(root, "checkout", "-b", "main")
    git(root, "config", "user.name", "Vigil Test")
    git(root, "config", "user.email", "vigil-test@example.invalid")
    (root / "README.md").write_text("initial\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "initial commit")
    git(root, "branch", "feature/demo")
    (root / "main.txt").write_text("main branch\n", encoding="utf-8")
    git(root, "add", "main.txt")
    git(root, "commit", "-m", "main branch commit")
    git(root, "checkout", "feature/demo")
    (root / "feature.txt").write_text("feature branch\n", encoding="utf-8")
    git(root, "add", "feature.txt")
    git(root, "commit", "-m", "feature branch commit")
    git(root, "checkout", "main")
    return root


def test_directory_plugin_has_valid_manifest_and_frontend_assets():
    from plugin_structure import check_plugin_directory

    report = check_plugin_directory(PLUGIN_DIR)
    assert report["ok"], report
    assert report["layout"] == "directory"
    assert report["manifest"]["name"] == "branch-atlas"
    assert "frontend.js" in report["files"]
    assert "frontend.css" in report["files"]


def test_inspect_repo_returns_branches_graph_and_status_without_mutating_files(tmp_path):
    repo = make_repo(tmp_path / "demo-repo")
    before = sorted(str(p.relative_to(repo)) for p in repo.rglob("*") if ".git" not in p.parts)
    result = branch_atlas.inspect_repo({"path": str(repo)}, {})

    assert result["ok"], result
    assert result["branch"] == "main"
    assert any(line.strip().endswith("main") for line in result["branches"])
    assert any("feature/demo" in line for line in result["branches"])
    assert "initial commit" in result["graph"]
    assert "main branch commit" in result["graph"]
    assert "feature branch commit" in result["graph"]
    assert "## main" in result["status"]
    after = sorted(str(p.relative_to(repo)) for p in repo.rglob("*") if ".git" not in p.parts)
    assert before == after
    assert not branch_atlas.inspect_repo({"path": str(repo / "README.md")}, {})["ok"]


def test_non_repository_and_unavailable_local_path_return_actionable_errors(tmp_path):
    not_repo = tmp_path / "not-repo"
    not_repo.mkdir()
    result = branch_atlas.inspect_repo({"path": str(not_repo)}, {})
    assert not result["ok"]
    assert "Git" in result["err"]

    missing = branch_atlas.inspect_repo({"path": str(tmp_path / "missing")}, {})
    assert not missing["ok"]
    assert "无法访问" in missing["err"]


def test_chat_tool_is_registered_and_returns_read_only_summary(tmp_path):
    repo = make_repo(tmp_path / "chat-repo")
    assert "branch_atlas_inspect" in branch_atlas.TOOLS
    assert "branch_atlas_inspect" in branch_atlas.TOOL_SPECS
    summary = branch_atlas.branch_atlas_inspect(str(repo))
    assert "当前分支：main" in summary
    assert "feature/demo" in summary
    assert "最近提交拓扑" in summary
    assert "initial commit" in summary
