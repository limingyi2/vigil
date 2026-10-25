"""Run clean-install and uninstall-retention checks against an immutable Windows candidate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time


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

    output.parent.mkdir(parents=True, exist_ok=True)
    logs_dir = output.parent / (output.stem + '-logs')
    logs_dir.mkdir(exist_ok=True)
    log_files = {
        'install': root / 'installer-install.json',
        'nsis_install': nsis_install_log,
        'first_launch': root / 'runtime-first-launch.json',
        'restart': root / 'runtime-restart.json',
        'uninstall': root / 'installer-uninstall.json',
        'nsis_uninstall': nsis_uninstall_log,
    }
    available_logs = {}
    for name, source in log_files.items():
        if source.is_file():
            extension = '.log' if source.suffix == '.log' else '.json'
            shutil.copy2(source, logs_dir / f'{name}{extension}')
            available_logs[name] = f'{logs_dir.name}/{name}{extension}'
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
        failure = {'result': 'failed', 'source_commit_expected': args.expected_source_commit,
                   'error': f'{type(error).__name__}: {error}', 'logs': logs}
        output.write_text(json.dumps(failure, indent=2), encoding='utf-8')
        raise
