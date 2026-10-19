"""Plugin market setup/read states; all filesystem data is temporary and fake."""
import json
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

import paths
from plugins import plugin_market as market


def test_market_repo_is_resolved_from_runtime_config_dir_not_developer_path():
    with tempfile.TemporaryDirectory() as home:
        config_dir = Path(home) / "config"
        config_dir.mkdir()
        (config_dir / "config.json").write_text(
            json.dumps({"market_repo": "plugin-market"}), encoding="utf-8")
        with patch.object(paths, "CONFIG_DIR", str(config_dir)):
            assert market._repo_path() == str(config_dir / "plugin-market")


def test_github_market_is_the_default_when_no_local_override_exists():
    with tempfile.TemporaryDirectory() as home:
        config_dir = Path(home) / "config"
        config_dir.mkdir()
        (config_dir / "config.json").write_text("{}", encoding="utf-8")
        fake = {"state": "ready", "version": "1", "source_kind": "github",
                "plugins": [{"name": "wallpaper", "version": "3.0.0"}]}
        with patch.object(paths, "CONFIG_DIR", str(config_dir)):
            with patch.object(market, "_read_remote_market", return_value=fake):
                assert market._repo_path() == ""
                result = market.api_list({}, {})
                assert result["state"] == "ready"
                assert result["source"] == "github"
                assert result["repo_configured"] is True
                assert [item["name"] for item in result["plugins"]] == ["wallpaper"]


def test_market_read_failure_and_valid_empty_list_are_distinct():
    with tempfile.TemporaryDirectory() as home:
        repo = Path(home)
        with patch.object(market, "_repo_path", return_value=str(repo)):
            assert market.api_list({}, {})["state"] == "read_failed"
            (repo / "market.json").write_text(
                json.dumps({"version": "1", "plugins": []}), encoding="utf-8")
            result = market.api_list({}, {})
            assert result["state"] == "empty"
            assert "没有已发布插件" in result["state_message"]


def test_malformed_manifest_is_read_failure_not_empty_market():
    with tempfile.TemporaryDirectory() as home:
        repo = Path(home)
        (repo / "market.json").write_text("{bad json", encoding="utf-8")
        with patch.object(market, "_repo_path", return_value=str(repo)):
            result = market.api_list({}, {})
            assert result["state"] == "read_failed"
            assert "无法读取" in result["state_message"]


def test_refresh_requires_configured_local_git_repo_and_uses_fast_forward_only():
    with patch.object(market, "_repo_path", return_value=""):
        with patch.object(market, "_read_remote_market", return_value={
                "state": "ready", "plugins": [], "source_kind": "github"}):
            result = market.api_refresh({}, {})
            assert result["ok"] is True
            assert result["state"] == "ready"
            assert result["source"] == "github"

    with tempfile.TemporaryDirectory() as home:
        with patch.object(market, "_repo_path", return_value=home), \
             patch.object(market.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "true\n"
            run.return_value.stderr = ""
            pull_result = type("Result", (), {
                "returncode": 0, "stdout": "Already up to date.\n", "stderr": ""})()
            run.side_effect = [run.return_value, pull_result]
            result = market.api_refresh({}, {})
            assert result["ok"] is True  # git pull succeeded; manifest is reported separately
            assert result["state"] == "read_failed"  # no market.json to read after pull
            assert run.call_count == 2
            assert "--ff-only" in run.call_args_list[1].args[0]


def test_refresh_pulls_updated_manifest_from_a_fake_local_git_remote():
    """Exercise the real git pull path with a temporary local bare remote."""
    with tempfile.TemporaryDirectory() as home:
        root = Path(home)
        remote, publisher, clone = root / "remote.git", root / "publisher", root / "clone"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True,
                       capture_output=True, text=True)
        subprocess.run(["git", "clone", str(remote), str(publisher)], check=True,
                       capture_output=True, text=True)

        def publish(entries):
            (publisher / "market.json").write_text(
                json.dumps({"version": "1", "plugins": entries}), encoding="utf-8")
            subprocess.run(["git", "-C", str(publisher), "add", "market.json"],
                           check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(publisher), "-c", "user.name=Fixture",
                            "-c", "user.email=fixture@example.invalid", "commit", "-m", "market"],
                           check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(publisher), "branch", "-M", "main"],
                           check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(publisher), "push", "-u", "origin", "main"],
                           check=True, capture_output=True, text=True)

        publish([])
        subprocess.run(["git", "clone", "--branch", "main", str(remote), str(clone)],
                       check=True, capture_output=True, text=True)
        publish([{"name": "fixture", "path": "plugins/fixture"}])
        with patch.object(market, "_repo_path", return_value=str(clone)):
            result = market.api_refresh({}, {})
            assert result["ok"] is True
            listed = market.api_list({}, {})
        assert listed["state"] == "ready"
        assert [p["name"] for p in listed["plugins"]] == ["fixture"]
