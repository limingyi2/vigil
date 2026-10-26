"""Plugin market setup/read states; all filesystem data is temporary and fake."""
import json
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch
from urllib.parse import urlparse

import paths
from core_features import plugin_market as market


def _builtin_patches(builtin_dir, coreplug_name="coreplug.py"):
    """让 plugin_host 把 ``builtin_dir`` 当成核心能力目录。

    🔴 **为什么不能只 patch ``_DIR``**：`plugin_manager._source()` 判定
    一个插件是 builtin 还是 user，看的是两个常量：

    ```python
    roots = (
        ("user",    getattr(plugin_host, "_INSTALLED_DIR", "")),
        ("builtin", getattr(plugin_host, "_CORE_PLUGIN_ROOT", "")),
    )
    ```

    ``_DIR`` 在 plugin_host.py:51 的注释里写明「仅供升级迁移兼容，
    **绝不作为运行时来源**」—— 早期测试 patch 的是它，所以 builtin 判定
    一直不成立（卸载用户版后恢复不到内置副本，state 报 'uninstalled'
    而不是 'builtin_restored'）。

    还要动 ``_CORE_ADAPTERS``：核心目录里只有白名单里的文件才会被当作
    插件加载（plugin_host.py:349 逐个遍历 ``sorted(_CORE_ADAPTERS)``），
    测试造的 ``overlay.py`` 默认不在名单里。
    """
    import plugin_host
    adapters = frozenset(set(plugin_host._CORE_ADAPTERS) | {coreplug_name})
    return (
        patch.object(plugin_host, '_CORE_PLUGIN_ROOT', str(builtin_dir)),
        patch.object(plugin_host, '_CORE_ADAPTERS', adapters),
    )


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
                # This contract covers the published market manifest. Installed
                # runtime plugins are covered by lifecycle tests and may be
                # present in a developer's test process.
                with patch("plugin_manager.list_plugins", return_value=[]):
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
            with patch("plugin_manager.list_plugins", return_value=[]):
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
        market._fetch_bytes("https://raw.githubusercontent.com/example/repo/main/package.zip", 1024)
        assert urlparse(again.call_args[0][0].full_url).hostname == "fastly.jsdelivr.net"
    market._preferred_host["host"] = ""


def test_mutable_index_keeps_fresh_source_priority_after_cdn_download():
    market._preferred_host['host'] = 'cdn.jsdelivr.net'
    try:
        assert market._candidate_urls(market.REMOTE_MARKET_URL)[0].startswith('https://ghfast.top/')
    finally:
        market._preferred_host['host'] = ''


def test_fetch_still_rejects_disallowed_download_hosts():
    import pytest
    with pytest.raises(ValueError):
        market._fetch_bytes("https://evil.example.com/market.json", 1024)


def _package(extra=False, bad_hash=False, duplicate=False, version='1.0.0', permissions=None):
    import hashlib
    import io
    import zipfile
    files = {'__init__.py': b'NAME = "fixture"\nDESC = "Fixture"\n',
             'frontend.js': b'window.fixture = true;', 'frontend.css': b'.fixture {}',
             'version.json': b'{"version":"1.0.0"}'}
    manifest = {'schema': 'vigil.brain-plugin/v1', 'name': 'fixture', 'version': version,
                'protocol': market.PLUGIN_PROTOCOL, 'permissions': permissions or [], 'entry': '__init__.py',
                'frontend': {'script': 'frontend.js', 'style': 'frontend.css'},
                'files': [{'path': n, 'sha256': hashlib.sha256(b).hexdigest()} for n, b in files.items()]}
    if extra:
        manifest['files'] = [x for x in manifest['files'] if x['path'] != 'version.json']
    if bad_hash:
        manifest['files'][0]['sha256'] = '0' * 64
    if duplicate:
        manifest['files'].append(manifest['files'][0])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for n, b in files.items():
            archive.writestr('fixture/' + n, b)
        archive.writestr('fixture/manifest.json', json.dumps(manifest))
    data = buffer.getvalue()
    meta = {k: manifest[k] for k in ('name', 'version', 'protocol', 'permissions')}
    meta['artifact'] = {'url': 'https://raw.githubusercontent.com/example/repo/commit/fixture.zip',
                        'sha256': hashlib.sha256(data).hexdigest()}
    return meta, data


def test_complete_package_install_refresh_uninstall_and_reinstall(tmp_path):
    import plugin_host
    import plugin_ops
    import restart_state
    meta, data = _package()
    target = tmp_path / 'installed'
    with patch.object(plugin_ops, '_PLUG_DIR', str(target)), \
         patch.object(plugin_ops, 'REGISTRY', str(target / '_registry.json')), \
         patch.object(plugin_host, '_INSTALLED_DIR', str(target)), \
         patch.object(market, '_plugins_dir', return_value=str(target)), \
         patch.object(market, '_read_market', return_value={'state': 'ready', 'plugins': [meta]}), \
         patch.object(market, '_fetch_bytes', return_value=data), \
         patch.object(restart_state, 'note_change'):
        for action in ('install', 'uninstall', 'install'):
            result = getattr(market, 'api_' + action)({'name': 'fixture'}, {})
            assert result['ok'], result
            installed = action == 'install'
            assert market.api_list({}, {})['plugins'][0]['installed'] == installed
            assert (target / 'fixture' / 'frontend.js').exists() == installed
            if action == 'uninstall':
                duplicate = market.api_uninstall({'name': 'fixture'}, {})
                assert duplicate['ok'] is False
                assert market.api_list({}, {})['plugins'][0]['installed'] is False
        assert not list(target.glob('_tmp*'))


def test_market_uninstall_reports_builtin_recovery_and_refreshes_status(tmp_path):
    import plugin_host
    import plugin_manager
    import plugin_ops
    user_plugins = tmp_path / 'installed'
    builtin_plugins = tmp_path / 'builtin'
    user_plugins.mkdir()
    builtin_plugins.mkdir()
    (user_plugins / 'overlay.py').write_text('NAME="overlay"\nVALUE="user"\n', encoding='utf-8')
    (builtin_plugins / 'overlay.py').write_text('NAME="overlay"\nVALUE="builtin"\n', encoding='utf-8')
    registry = user_plugins / '_registry.json'
    registry.write_text(json.dumps({'version': 1, 'plugins': {
        'overlay': {'version': '2.0.0', 'enabled': True}}}), encoding='utf-8')
    old_registry = plugin_host._registry
    old_started = plugin_host._started
    old_index = dict(plugin_host._TOOL_INDEX)
    plugin_host._registry = {}
    plugin_host._started = set()
    market_doc = {'state': 'ready', 'version': '1', 'plugins': [
        {'name': 'overlay', 'version': '2.0.0', 'desc': 'overlay fixture'}]}
    try:
        core_root, core_adapters = _builtin_patches(builtin_plugins, 'overlay.py')
        with patch.object(plugin_host, '_INSTALLED_DIR', str(user_plugins)), \
             patch.object(plugin_host, '_DIR', str(builtin_plugins)), \
             core_root, core_adapters, \
             patch.object(plugin_ops, '_PLUG_DIR', str(user_plugins)), \
             patch.object(plugin_ops, 'REGISTRY', str(registry)), \
             patch.object(market, '_plugins_dir', return_value=str(user_plugins)), \
             patch.object(market, '_read_market', return_value=market_doc):
            plugin_host.load(force=True, only='overlay')
            result = market.api_uninstall({'name': 'overlay'}, {})
            assert result['ok'] is True
            assert result['state'] == 'builtin_restored'
            assert result['source_before'] == 'user'
            assert result['source_kind'] == 'builtin'
            assert result['builtin_restored'] is True
            assert result['msg'] == '用户版本已卸载，已恢复内置版本；用户配置和素材保留'
            refreshed = market.api_list({}, {})
            plugin = refreshed['plugins'][0]
            assert plugin['installed'] is True
            assert plugin['source_kind'] == 'builtin'
            assert plugin['builtin'] is True
            assert plugin['removable'] is False
            assert not (user_plugins / 'overlay.py').exists()
            saved_registry = json.loads(registry.read_text(encoding='utf-8'))
            assert 'overlay' not in saved_registry['plugins']
    finally:
        plugin_host._registry = old_registry
        plugin_host._started = old_started
        plugin_host._TOOL_INDEX.clear()
        plugin_host._TOOL_INDEX.update(old_index)


def test_managed_market_update_persists_verified_manifest_and_clears_update_state(tmp_path):
    import plugin_host
    import plugin_manager
    import plugin_ops
    meta, data = _package(version='2.0.0', permissions=['fixture.read'])
    target = tmp_path / 'installed'
    old = target / 'fixture'
    old.mkdir(parents=True)
    (old / '__init__.py').write_text('NAME = "fixture"\nDESC = "old"\n', encoding='utf-8')
    registry = target / '_registry.json'
    registry.write_text(json.dumps({'version': 1, 'plugins': {'fixture': {
        'version': '1.0.0', 'enabled': True, 'source': 'market:fixture@1.0.0'}}}), encoding='utf-8')
    with patch.object(plugin_ops, '_PLUG_DIR', str(target)), \
         patch.object(plugin_ops, 'REGISTRY', str(registry)), \
         patch.object(plugin_host, '_INSTALLED_DIR', str(target)), \
         patch.object(market, '_fetch_bytes', return_value=data), \
         patch.object(plugin_manager, '_reload', return_value=True):
        ok, result = market._install_remote_directory(meta, 'fixture', replace=True, managed=True)
    assert ok, result
    saved = json.loads(registry.read_text(encoding='utf-8'))['plugins']['fixture']
    assert saved['version'] == '2.0.0'
    assert saved['permissions'] == ['fixture.read']
    assert saved['dependencies'] == {}
    assert saved['source'] == 'market:fixture@2.0.0'
    assert saved['manifest']['version'] == '2.0.0'
    assert saved['manifest']['permissions'] == ['fixture.read']
    with patch.object(plugin_ops, 'REGISTRY', str(registry)), \
         patch.object(plugin_host, '_INSTALLED_DIR', str(target)), \
         patch.object(plugin_host, '_registry', {k: v for k, v in plugin_host._registry.items()
                                                 if k != 'fixture'}):
        assert plugin_host._plugin_entry_path('fixture') == str(old / '__init__.py')
        assert plugin_manager.status('fixture', '2.0.0')['update_state'] == 'current'


def test_validation_and_network_failures_preserve_existing_install(tmp_path):
    import plugin_ops
    target = tmp_path / 'installed'
    target.mkdir()
    old = target / 'fixture'
    old.mkdir()
    (old / '__init__.py').write_bytes(b'old plugin')
    registry = target / '_registry.json'
    registry.write_bytes(b'{"version":1,"plugins":{"fixture":{"version":"old"}}}')
    original = registry.read_bytes()
    with patch.object(plugin_ops, '_PLUG_DIR', str(target)), \
         patch.object(plugin_ops, 'REGISTRY', str(registry)):
        for args, expected in [({'extra': True}, '未声明：version.json'),
                               ({'bad_hash': True}, '文件 SHA-256'),
                               ({'duplicate': True}, '重复路径')]:
            meta, data = _package(**args)
            with patch.object(market, '_fetch_bytes', return_value=data):
                ok, result = market._install_remote_directory(meta, 'fixture')
            assert not ok and expected in result['err']
            assert result['stage'].startswith('Manifest')
        with patch.object(market, '_fetch_bytes', side_effect=OSError('connection reset')):
            ok, result = market._install_remote_directory(meta, 'fixture')
        assert not ok and result['stage'] == '下载制品'
        with patch.object(market, '_fetch_bytes', return_value=b'tampered archive'):
            ok, result = market._install_remote_directory(meta, 'fixture')
        assert not ok and result['stage'] == '制品 SHA-256 校验'
        assert (old / '__init__.py').read_bytes() == b'old plugin'
        assert registry.read_bytes() == original
        assert sorted(p.name for p in target.iterdir()) == ['_registry.json', 'fixture']


def test_stale_mirror_hash_falls_through_to_verified_artifact():
    import hashlib
    class Response:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size): return self.data
    market._preferred_host['host'] = ''
    try:
        with patch.object(market, 'urlopen', side_effect=[Response(b'old cache'), Response(b'correct')]) as fetch:
            data = market._fetch_bytes('https://raw.githubusercontent.com/a/b/commit/p.zip', 1024,
                                      expected_sha=hashlib.sha256(b'correct').hexdigest())
        assert data == b'correct'
        assert fetch.call_count == 2
    finally:
        market._preferred_host['host'] = ''
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


def test_remote_connection_failure_diagnostics_separate_timeout_dns_tls_and_proxy():
    import socket
    import ssl
    from urllib.error import URLError

    assert market._remote_diagnostic(URLError(socket.timeout("timed out")))["kind"] == "timeout"
    assert market._remote_diagnostic(URLError(socket.gaierror("name resolution failed")))["kind"] == "dns"
    assert market._remote_diagnostic(URLError(ssl.SSLError("certificate verify failed")))["kind"] == "tls"
    assert market._remote_diagnostic(URLError("proxy tunnel failed"))["kind"] == "proxy"


def test_live_market_corruption_has_explicit_manifest_diagnostic():
    with tempfile.TemporaryDirectory() as home:
        with patch.object(paths, "CONFIG_DIR", home), \
             patch.object(market, "_fetch_bytes", return_value=b'{broken json'):
            result = market._read_remote_market()
    assert result["state"] == "read_failed"
    assert result["diagnostic"]["stage"] == "remote_market"
    assert result["diagnostic"]["kind"] == "invalid_manifest"
