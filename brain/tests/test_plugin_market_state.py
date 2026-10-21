"""Plugin market setup/read states; all filesystem data is temporary and fake."""
import json
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch
from urllib.parse import urlparse

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
        publish([{
            "name": "fixture", "version": "1.0.0",
            "artifact": {"url": "https://raw.githubusercontent.com/example/repo/main/fixture.zip",
                         "sha256": "a" * 64},
            "protocol": {"name": "vigil-brain-plugin", "major": 1},
            "permissions": [],
        }])
        with patch.object(market, "_repo_path", return_value=str(clone)):
            result = market.api_refresh({}, {})
            assert result["ok"] is True
            listed = market.api_list({}, {})
        assert listed["state"] == "ready"
        assert [p["name"] for p in listed["plugins"]] == ["fixture"]


def test_raw_urls_map_to_live_mirror_first_with_raw_last():
    url = "https://raw.githubusercontent.com/limingyi2/vigil-plugins/main/market.json"
    urls = market._candidate_urls(url)
    assert urls[0] == "https://ghfast.top/" + url
    assert urls[1] == "https://cdn.jsdelivr.net/gh/limingyi2/vigil-plugins@main/market.json"
    assert urls[2] == "https://fastly.jsdelivr.net/gh/limingyi2/vigil-plugins@main/market.json"
    assert urls[3] == "https://testingcf.jsdelivr.net/gh/limingyi2/vigil-plugins@main/market.json"
    assert urls[-1] == url
    # 非 GitHub Raw 地址原样透传，不做镜像映射
    assert market._candidate_urls("https://cdn.jsdelivr.net/gh/a/b@main/c.json") == [
        "https://cdn.jsdelivr.net/gh/a/b@main/c.json"]


def test_preferred_host_jumps_the_queue_after_a_success():
    url = "https://raw.githubusercontent.com/example/repo/main/pkg/pkg.zip"
    market._preferred_host["host"] = "fastly.jsdelivr.net"
    try:
        assert market._candidate_urls(url)[0] == \
            "https://fastly.jsdelivr.net/gh/example/repo@main/pkg/pkg.zip"
    finally:
        market._preferred_host["host"] = ""


def test_fetch_falls_through_to_first_reachable_mirror():
    url = "https://raw.githubusercontent.com/example/repo/main/market.json"
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, n):
            return b'{"plugins": []}'

    def fake_urlopen(request, timeout):
        calls.append(urlparse(request.full_url).hostname)
        if calls[-1] in ("ghfast.top", "cdn.jsdelivr.net"):
            raise OSError("blocked")
        return FakeResponse()

    with patch.object(market, "urlopen", side_effect=fake_urlopen):
        data = market._fetch_bytes(url, 1024)
    assert data == b'{"plugins": []}'
    assert calls == ["ghfast.top", "cdn.jsdelivr.net", "fastly.jsdelivr.net"]
    assert market._preferred_host["host"] == "fastly.jsdelivr.net"
    # 下一次下载直接命中记忆的源
    with patch.object(market, "urlopen", side_effect=fake_urlopen) as again:
        market._fetch_bytes(url, 1024)
        assert urlparse(again.call_args[0][0].full_url).hostname == "fastly.jsdelivr.net"
    market._preferred_host["host"] = ""


def test_fetch_still_rejects_disallowed_download_hosts():
    import pytest
    with pytest.raises(ValueError):
        market._fetch_bytes("https://evil.example.com/market.json", 1024)


def test_offline_remote_market_uses_timestamped_validated_cache():
    with tempfile.TemporaryDirectory() as home:
        config_dir = Path(home) / "config"
        config_dir.mkdir()
        plugin = {
            "name": "wallpaper", "version": "3.0.1",
            "protocol": market.PLUGIN_PROTOCOL,
            "permissions": ["wallpaper.config.write"],
            "artifact": {
                "url": "https://raw.githubusercontent.com/example/repo/main/wallpaper.zip",
                "sha256": "a" * 64,
            },
        }
        with patch.object(paths, "CONFIG_DIR", str(config_dir)):
            market._write_market_cache({
                "version": "1.1", "updated": "2026-10-21", "plugins": [plugin],
                "state": "ready", "source_kind": "github",
            })
            with patch.object(market, "_fetch_bytes", side_effect=OSError("offline")):
                result = market._read_remote_market()
            assert result["state"] == "cached_stale"
            assert result["source_kind"] == "github-cache"
            assert result["fetched_at"]
            assert "在线读取失败" in result["err"]


def test_invalid_cached_market_is_not_displayed_as_verified():
    with tempfile.TemporaryDirectory() as home:
        config_dir = Path(home) / "config"
        config_dir.mkdir()
        with patch.object(paths, "CONFIG_DIR", str(config_dir)):
            (config_dir / "plugin_market_cache.json").write_text(
                json.dumps({"fetched_at": "2026-10-21T00:00:00+00:00",
                            "market": {"plugins": [{"name": "bad name"}]}}),
                encoding="utf-8")
            assert market._read_cached_market() is None
