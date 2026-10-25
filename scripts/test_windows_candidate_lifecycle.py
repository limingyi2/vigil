"""Run clean-install and uninstall-retention checks against an immutable Windows candidate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): sha256(p)
            for p in sorted(root.rglob('*')) if p.is_file()}


def run_logged(command, *, env, log_path, timeout):
    # 子进程（Node/Electron/大脑）输出 UTF-8；用 text=True 默认走 Windows 本地代码页
    # (cp1252) 会在遇到中文日志时抛 UnicodeDecodeError，把真实失败原因吞掉，故显式指定 UTF-8。
    result = subprocess.run(command, env=env, text=True, encoding='utf-8', errors='replace',
                            capture_output=True, timeout=timeout)
    log_path.write_text(json.dumps({'command': [str(x) for x in command], 'exit_code': result.returncode,
                                    'stdout': result.stdout, 'stderr': result.stderr}, indent=2), encoding='utf-8')
    return result


def failure_detail(result, tail=20):
    """把子进程的 returncode/stderr/stdout 尾巴一起带出来，避免只看到一句"失败了"。"""
    lines = [f'rc={result.returncode}']
    if result.stderr:
        lines.append('stderr: ' + result.stderr.strip()[-2000:])
    if result.stdout:
        lines.append('stdout tail: ' + '\n'.join(result.stdout.strip().splitlines()[-tail:]))
    return '\n'.join(lines)


def download_verified(url, path, expected_sha256, attempts=4):
    path = Path(path)
    temporary = path.with_name(path.name + '.download')
    last_error = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=90) as response, temporary.open('wb') as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            if sha256(temporary) != expected_sha256:
                raise RuntimeError('Downloaded release asset SHA-256 mismatch: ' + path.name)
            os.replace(temporary, path)
            return path
        except Exception as error:
            last_error = error
            temporary.unlink(missing_ok=True)
            if attempt + 1 < attempts:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f'Could not download verified release asset {url}: {last_error}')


def run_historical_upgrade(installer, expected_commit, root):
    """Use the published P21.2 setup executable, never a synthesized old layout."""
    old_version = '1.1.9-p21.2.20261025.1'
    old_source = 'd0a7e90409614961fda3a4a33523e42d4d4b92dc'
    old_sha = '954ae2096bfc8a1327c35b50165404dc887b674a8ba32e45e079c33e598bcfd6'
    manifest_sha = '0603ee73c8623c7bda5d9f49b3e4d5beb4c060f349068cb42584283c3d0819d0'
    release = f'https://github.com/limingyi2/vigil/releases/download/v{old_version}'
    old_dir = root / 'historical-release'
    old_dir.mkdir()
    historical_installer = download_verified(
        release + f'/Kestrel.Setup.{old_version}.exe',
        old_dir / f'Kestrel.Setup.{old_version}.exe', old_sha)
    historical_manifest_path = download_verified(
        release + '/release-manifest.json', old_dir / 'release-manifest.json', manifest_sha)
    historical_manifest = json.loads(historical_manifest_path.read_text(encoding='utf-8'))
    if (historical_manifest.get('source_commit') != old_source or
            historical_manifest.get('ver') != old_version or
            historical_manifest.get('candidate') is not False or
            historical_manifest.get('sha256') != old_sha or
            historical_manifest.get('size') != 162533456):
        raise RuntimeError('Published P21.2 release manifest identity mismatch')
    quoted_installer = "'" + str(historical_installer).replace("'", "''") + "'"
    signature = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
         f'(Get-AuthenticodeSignature -LiteralPath {quoted_installer}).Status.ToString()'],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    if signature.returncode:
        raise RuntimeError('Could not inspect historical installer Authenticode signature: ' + signature.stderr[-1000:])
    auth_status = signature.stdout.strip().splitlines()[-1]
    output = root / 'historical-upgrade.json'
    result = subprocess.run([
        sys.executable, str(Path(__file__).with_name('test_windows_historical_upgrade.py')),
        str(historical_installer), str(installer),
        '--expected-old-source-commit', old_source,
        '--expected-old-version', old_version,
        '--expected-old-sha256', old_sha,
        '--expected-new-source-commit', expected_commit,
        '--authenticode-status', auth_status,
        '--output', str(output),
    ], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=1800)
    if result.returncode:
        raise RuntimeError('Actual historical P21.2 upgrade failed: ' + failure_detail(result))
    report = json.loads(output.read_text(encoding='utf-8'))
    if report.get('result') != 'passed':
        raise RuntimeError('Historical upgrade report did not pass')
    return report


def main(installer, expected_commit, output, expected_version=None):
    assert os.name == 'nt', 'This acceptance check must run on an isolated Windows runner'
    installer = Path(installer).resolve()
    output = Path(output).resolve()
    manifest_path = installer.parent / 'release-manifest.json'
    contents_path = installer.with_suffix('.contents.json')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if expected_version is None:
        expected_version = manifest['ver']
    assert manifest['candidate'] is True and manifest['ver'] == expected_version
    assert manifest['source_commit'] == expected_commit, 'Candidate source commit mismatch'
    assert manifest['setup_file'] == installer.name
    installer_hash = sha256(installer)
    assert installer_hash == manifest['sha256'], 'Candidate installer SHA-256 mismatch'
    assert installer.stat().st_size == manifest['size'], 'Candidate installer size mismatch'

    root = Path(tempfile.mkdtemp(prefix='Vigil candidate lifecycle '))
    install = root / 'application'
    profile = root / 'profile'
    localappdata = root / 'localappdata'
    for directory in (profile, localappdata):
        directory.mkdir(parents=True, exist_ok=True)
    isolated_user_data = profile / 'vigil-client'
    env = dict(os.environ, VIGIL_USER_DATA=str(isolated_user_data), APPDATA=str(profile), LOCALAPPDATA=str(localappdata),
               VIGIL_SIDECAR_DISABLE='1')
    env.pop('ELECTRON_RUN_AS_NODE', None)
    nsis_install_log = root / 'nsis-install.log'
    install_result = run_logged([str(installer), '/S', '/LOG=' + str(nsis_install_log), '/D=' + str(install)], env=env,
                                log_path=root / 'installer-install.json', timeout=300)
    assert install_result.returncode == 0, f'Candidate installer exited {install_result.returncode}'
    package = json.loads((Path(__file__).resolve().parents[1] / 'client/package.json').read_text(encoding='utf-8'))
    product_name = (package.get('build') or {}).get('productName') or package.get('productName') or 'Vigil'
    assert (install / f'{product_name}.exe').is_file(), f'Fresh installation did not create {product_name}.exe'
    for relative, expected in json.loads(contents_path.read_text(encoding='utf-8')).items():
        file = install / Path(relative)
        assert file.is_file() and sha256(file) == expected, f'Installed core file missing or changed: {relative}'
    assert (install / 'resources/vigil-brain/_internal').is_dir(), 'Bundled Brain runtime is missing'
    packaged_paths = [p.relative_to(install).as_posix().lower() for p in install.rglob('*')]
    packaged_plugins = [p for p in packaged_paths
                        if 'installed_plugins' in p.split('/') or 'plugins' in p.split('/')
                        or p.endswith('/zxplugin.json')]
    assert not packaged_plugins, f'Client installation contains a bundled plugin path: {packaged_plugins[:10]}'

    runtime_script = Path(__file__).resolve().parents[1] / 'client/tools/test-fresh-installed-runtime.cjs'
    first = run_logged(['node', str(runtime_script), str(install), 'first'], env=env,
                       log_path=root / 'runtime-first-launch.json', timeout=300)
    assert first.returncode == 0, ('Fresh installed Electron/Brain/chat test failed: '
                                   + failure_detail(first))
    first_record = json.loads(next(line for line in reversed(first.stdout.splitlines()) if line.startswith('{')))
    assert first_record['result'] == 'passed' and first_record['fake_provider_chat'] == 'passed'
    conversation_id = first_record['conversation_id']

    restarted = run_logged(['node', str(runtime_script), str(install), 'restart'], env=env,
                           log_path=root / 'runtime-restart.json', timeout=300)
    assert restarted.returncode == 0, 'Chat restore after Electron restart failed: ' + failure_detail(restarted)
    restart_record = json.loads(next(line for line in reversed(restarted.stdout.splitlines()) if line.startswith('{')))
    assert restart_record['conversation_id'] == conversation_id
    assert restart_record['durable_chat_history'] is True
    brain_home = Path(first_record['brain_home'])
    user_data_root = brain_home.parent

    # Exercise an actual NSIS reinstall/upgrade and Brain startup against legacy
    # plugin locations. These fixture files use the old on-disk layout; the app
    # and sidecar are the real Windows candidate binaries under test.
    old_plugin_root = install / 'resources/vigil-brain/_internal/plugins'
    old_plugin_root.mkdir(parents=True, exist_ok=True)
    (old_plugin_root / 'p21_upgrade_fixture.py').write_text(
        "NAME='p21_upgrade_fixture'\nDESC='legacy package fixture'\nTOOLS={}\n", encoding='utf-8')
    old_plugin_root.mkdir(parents=True, exist_ok=True)
    (old_plugin_root / 'p21_preserved_fixture.py').write_text(
        "NAME='p21_preserved_fixture'\nDESC='old bundled shadow'\nTOOLS={}\n", encoding='utf-8')
    legacy_home_plugins = brain_home / 'plugins'
    legacy_home_plugins.mkdir(parents=True, exist_ok=True)
    (legacy_home_plugins / 'p21_home_fixture.py').write_text(
        "NAME='p21_home_fixture'\nDESC='legacy home fixture'\nTOOLS={}\n", encoding='utf-8')
    installed_plugins = brain_home / 'data/installed_plugins'
    installed_plugins.mkdir(parents=True, exist_ok=True)
    preserved = installed_plugins / 'p21_preserved_fixture.py'
    preserved.write_text(
        "NAME='p21_preserved_fixture'\nDESC='existing user version'\nTOOLS={}\n", encoding='utf-8')
    preserved_hash = sha256(preserved)

    upgrade_log = root / 'nsis-upgrade.log'
    upgrade = run_logged([str(installer), '/S', '/LOG=' + str(upgrade_log), '/D=' + str(install)], env=env,
                         log_path=root / 'installer-upgrade.json', timeout=300)
    assert upgrade.returncode == 0, 'Candidate in-place upgrade failed: ' + failure_detail(upgrade)
    migrated_runtime = run_logged(['node', str(runtime_script), str(install), 'upgrade'], env=env,
                                  log_path=root / 'runtime-upgrade.json', timeout=300)
    assert migrated_runtime.returncode == 0, 'Legacy plugin upgrade/runtime check failed: ' + failure_detail(migrated_runtime)
    for name in ('p21_upgrade_fixture.py', 'p21_home_fixture.py'):
        assert (installed_plugins / name).is_file(), f'Legacy plugin was not migrated: {name}'
    assert sha256(preserved) == preserved_hash, 'Upgrade overwrote an existing user-installed plugin'

    uninstall_plugins = run_logged(['node', str(runtime_script), str(install), 'uninstall-migrated'], env=env,
                                   log_path=root / 'runtime-plugin-uninstall.json', timeout=300)
    assert uninstall_plugins.returncode == 0, 'Plugin Manager uninstall failed: ' + failure_detail(uninstall_plugins)
    for name in ('p21_upgrade_fixture.py', 'p21_home_fixture.py'):
        assert not (installed_plugins / name).exists(), f'Plugin Manager did not uninstall {name}'
    assert (legacy_home_plugins / 'p21_home_fixture.py').is_file(), 'Uninstall removed legacy recovery source'

    post_uninstall_upgrade_log = root / 'nsis-post-uninstall-upgrade.log'
    post_uninstall_upgrade = run_logged(
        [str(installer), '/S', '/LOG=' + str(post_uninstall_upgrade_log), '/D=' + str(install)], env=env,
        log_path=root / 'installer-post-uninstall-upgrade.json', timeout=300)
    assert post_uninstall_upgrade.returncode == 0, 'Upgrade after plugin uninstall failed: ' + failure_detail(post_uninstall_upgrade)
    post_uninstall_runtime = run_logged(['node', str(runtime_script), str(install), 'post-uninstall-upgrade'], env=env,
                                        log_path=root / 'runtime-post-uninstall-upgrade.json', timeout=300)
    assert post_uninstall_runtime.returncode == 0, 'Uninstalled plugins reappeared after upgrade: ' + failure_detail(post_uninstall_runtime)
    assert sha256(preserved) == preserved_hash, 'Later upgrade overwrote an existing user-installed plugin'

    sentinels = {
        brain_home / 'plugins/p8-retention-plugin.py': 'NAME="p8_retention"\n',
        brain_home / 'skill_host/skills/p8-retention/skill.json': '{"id":"p8-retention"}\n',
        brain_home / 'data/creator_drafts/p8-retention/meta.json': '{"id":"p8-retention","status":"generated"}\n',
        brain_home / 'data/p8-retention-chat.jsonl': '{"role":"user","content":"retention sentinel"}\n',
    }
    for path, content in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    config_file = brain_home / 'config/config.json'
    conversations_db = brain_home / 'data/db/vigil.db'
    assert config_file.is_file(), 'Fresh chat did not persist the user configuration'
    assert conversations_db.is_file(), 'Fresh chat did not persist conversation metadata in SQLite'
    with sqlite3.connect(conversations_db.as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('SELECT COUNT(*) FROM conversations WHERE id=?', (conversation_id,)).fetchone()[0] == 1
    retained_files = [*sentinels, config_file, conversations_db]
    retained_before = {str(path): sha256(path) for path in retained_files}
    user_data_before = inventory(user_data_root)
    assert user_data_before, 'Isolated default Electron user-data directory is empty'
    outside = root / 'outside-data' / 'do-not-delete.txt'
    outside.parent.mkdir()
    outside.write_text('outside install and profile sentinel\n', encoding='utf-8')
    outside_hash = sha256(outside)

    uninstallers = list(install.glob('Uninstall*.exe'))
    assert uninstallers, 'NSIS uninstaller was not installed'
    nsis_uninstall_log = root / 'nsis-uninstall.log'
    uninstall_result = run_logged([str(uninstallers[0]), '/S', '/LOG=' + str(nsis_uninstall_log)], env=env,
                                  log_path=root / 'installer-uninstall.json', timeout=300)
    assert uninstall_result.returncode == 0, f'NSIS uninstaller exited {uninstall_result.returncode}'
    # NSIS exits before its deferred cleanup has always settled on Windows;
    # wait for removal rather than sampling one second after process exit.
    deadline = time.monotonic() + 30
    remaining_app_files = []
    while time.monotonic() < deadline:
        remaining_app_files = [str(path.relative_to(install)) for path in install.rglob('*') if path.is_file()] if install.exists() else []
        if not remaining_app_files:
            break
        time.sleep(0.25)
    assert not remaining_app_files, f'Program files remain after uninstall: {remaining_app_files[:10]}'
    retained_after = {name: sha256(Path(name)) for name in retained_before if Path(name).is_file()}
    assert retained_after == retained_before, 'Uninstall changed or removed user profile data'
    user_data_after = inventory(user_data_root)
    assert user_data_after == user_data_before, 'Uninstall changed or removed files in the default user-data directory'
    assert sha256(outside) == outside_hash, 'Uninstall crossed the application/profile boundary'

    historical_upgrade = run_historical_upgrade(installer, expected_commit, root)

    output.parent.mkdir(parents=True, exist_ok=True)
    logs_dir = output.parent / (output.stem + '-logs')
    logs_dir.mkdir(exist_ok=True)
    log_files = {
        'install': root / 'installer-install.json',
        'nsis_install': nsis_install_log,
        'first_launch': root / 'runtime-first-launch.json',
        'restart': root / 'runtime-restart.json',
        'upgrade': root / 'installer-upgrade.json',
        'upgrade_nsis': upgrade_log,
        'runtime_upgrade': root / 'runtime-upgrade.json',
        'plugin_uninstall': root / 'runtime-plugin-uninstall.json',
        'post_uninstall_upgrade': root / 'installer-post-uninstall-upgrade.json',
        'post_uninstall_upgrade_nsis': post_uninstall_upgrade_log,
        'post_uninstall_runtime': root / 'runtime-post-uninstall-upgrade.json',
        'historical_upgrade': root / 'historical-upgrade.json',
        'uninstall': root / 'installer-uninstall.json',
        'nsis_uninstall': nsis_uninstall_log,
    }
    available_logs = {}
    for name, source in log_files.items():
        if source.is_file():
            extension = '.log' if source.suffix == '.log' else '.json'
            shutil.copy2(source, logs_dir / f'{name}{extension}')
            available_logs[name] = f'{logs_dir.name}/{name}{extension}'
    historical_logs = root / 'historical-upgrade-logs'
    if historical_logs.is_dir():
        shutil.copytree(historical_logs, logs_dir / 'historical-upgrade')
    report = {
        'result': 'passed',
        'environment': {'os': os.environ.get('RUNNER_OS', 'Windows isolated runner'),
                        'runner_name': os.environ.get('RUNNER_NAME'), 'isolation_root': str(root)},
        'candidate': {'version': manifest['ver'], 'source_commit': manifest['source_commit'],
                      'installer': installer.name, 'size': installer.stat().st_size, 'sha256': installer_hash},
        'fresh_install': {'installer_exit_code': install_result.returncode, 'core_files_verified': True,
                          'electron_started': True, 'brain_health': 'ready', 'welcome_page': True,
                          'fake_provider_first_chat': True, 'conversation_id': conversation_id},
        'restart_recovery': {'electron_restarted': True, 'chat_history_restored': True,
                             'durable_brain_display_turn_restored_via_api': True},
        'legacy_plugin_upgrade': {'candidate_reinstalled_in_place': True,
                                  'legacy_bundle_source_migrated': True,
                                  'legacy_home_source_migrated': True,
                                  'existing_user_plugin_hash_preserved': True,
                                  'plugin_manager_uninstall_api_passed': True,
                                  'uninstall_survived_later_reinstall': True,
                                  'legacy_source_preserved': True,
                                  'old_signed_release_binary_used': False},
        'actual_historical_upgrade': historical_upgrade,
        'uninstall': {'uninstaller': uninstallers[0].name, 'exit_code': uninstall_result.returncode,
                      'program_files_removed': True, 'user_data_policy': 'preserved',
                      'retained_test_data': list(retained_before),
                      'actual_configuration_and_conversation_metadata_retained': True,
                      'all_user_data_files_unchanged': True,
                      'user_data_file_count': len(user_data_before),
                      'outside_sentinel_preserved': True},
        'nsis_verbose_logs': {'install_available': nsis_install_log.is_file(),
                              'uninstall_available': nsis_uninstall_log.is_file()},
        'logs': available_logs,
    }
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('installer')
    parser.add_argument('--expected-source-commit', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--expected-version')
    args = parser.parse_args()
    if Path(args.output).resolve().exists():
        parser.error('Refusing to overwrite an existing lifecycle report')
    try:
        main(args.installer, args.expected_source_commit, args.output, args.expected_version)
    except Exception as error:
        output = Path(args.output).resolve()
        if output.exists():
            raise
        output.parent.mkdir(parents=True, exist_ok=True)
        logs_dir = output.parent / (output.stem + '-logs')
        logs_dir.mkdir(exist_ok=True)
        root_candidates = sorted(Path(tempfile.gettempdir()).glob('Vigil candidate lifecycle *'),
                                 key=lambda item: item.stat().st_mtime, reverse=True)
        logs = {}
        if root_candidates:
            root = root_candidates[0]
            for name, filename in (('install', 'installer-install.json'),
                                   ('nsis_install', 'nsis-install.log'),
                                   ('first_launch', 'runtime-first-launch.json'),
                                   ('restart', 'runtime-restart.json'),
                                   ('historical_upgrade', 'historical-upgrade.json'),
                                   ('uninstall', 'installer-uninstall.json'),
                                   ('nsis_uninstall', 'nsis-uninstall.log')):
                source = root / filename
                if source.is_file():
                    extension = '.log' if source.suffix == '.log' else '.json'
                    destination = logs_dir / f'{name}{extension}'
                    if destination.exists():
                        continue
                    shutil.copy2(source, destination)
                    logs[name] = f'{logs_dir.name}/{name}{extension}'
            historical_logs = root / 'historical-upgrade-logs'
            if historical_logs.is_dir() and not (logs_dir / 'historical-upgrade').exists():
                shutil.copytree(historical_logs, logs_dir / 'historical-upgrade')
        failure = {'result': 'failed', 'source_commit_expected': args.expected_source_commit,
                   'error': f'{type(error).__name__}: {error}', 'logs': logs}
        output.write_text(json.dumps(failure, indent=2), encoding='utf-8')
        raise
