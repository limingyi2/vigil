"""Upgrade from a hash-pinned published P21.2 installer in an isolated Windows profile."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


OLD_SETUP = 'Kestrel.Setup.1.1.9-p21.2.20261025.1.exe'
OLD_SIZE = 162533456
OLD_AUTHENTICODE_STATUS = 'NotSigned'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def run_logged(command, *, env, log_path, timeout=600):
    result = subprocess.run(command, env=env, text=True, encoding='utf-8', errors='replace',
                            capture_output=True, timeout=timeout)
    log_path.write_text(json.dumps({'command': [str(x) for x in command], 'exit_code': result.returncode,
                                    'stdout': result.stdout, 'stderr': result.stderr}, indent=2), encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'Command failed ({result.returncode}): {command[0]}\n'
                           + result.stderr[-3000:] + '\n' + result.stdout[-3000:])
    return result


def main(args):
    if os.name != 'nt':
        raise RuntimeError('Historical installer test must run on a Windows runner')
    old_installer = Path(args.old_installer).resolve()
    candidate = Path(args.candidate_installer).resolve()
    if old_installer.name != OLD_SETUP:
        raise RuntimeError('Unexpected historical installer filename')
    if old_installer.stat().st_size != OLD_SIZE or sha256(old_installer) != args.expected_old_sha256:
        raise RuntimeError('Historical installer size or SHA-256 mismatch')
    if args.expected_old_sha256 != '954ae2096bfc8a1327c35b50165404dc887b674a8ba32e45e079c33e598bcfd6':
        raise RuntimeError('Historical installer pin changed without review')
    if args.authenticode_status != OLD_AUTHENTICODE_STATUS:
        raise RuntimeError('Historical installer Authenticode status differs from recorded inspection')

    candidate_manifest_path = candidate.parent / 'release-manifest.json'
    candidate_contents_path = candidate.with_suffix('.contents.json')
    manifest = json.loads(candidate_manifest_path.read_text(encoding='utf-8'))
    contents = json.loads(candidate_contents_path.read_text(encoding='utf-8'))
    if manifest.get('candidate') is not True:
        raise RuntimeError('Current installer must be an unpublished candidate')
    if manifest.get('ver') != '1.1.9-p21.5.20261025.1':
        raise RuntimeError('Candidate is not labeled P21.5')
    if manifest.get('source_commit') != args.expected_new_source_commit:
        raise RuntimeError('Candidate source commit differs from the tested source')
    if manifest.get('sha256') != sha256(candidate) or manifest.get('size') != candidate.stat().st_size:
        raise RuntimeError('Candidate installer identity mismatch')

    root = Path(tempfile.mkdtemp(prefix='Vigil real historical P21 upgrade '))
    install = root / 'application'
    profile = root / 'profile'
    localappdata = root / 'localappdata'
    for folder in (profile, localappdata):
        folder.mkdir(parents=True, exist_ok=True)
    user_data = profile / 'vigil-client'
    env = dict(os.environ, VIGIL_USER_DATA=str(user_data), APPDATA=str(profile),
               LOCALAPPDATA=str(localappdata), VIGIL_SIDECAR_DISABLE='1')
    env.pop('ELECTRON_RUN_AS_NODE', None)
    runtime_helper = Path(__file__).resolve().parents[1] / 'client/tools/test-historical-upgrade-runtime.cjs'
    logs = root / 'logs'
    logs.mkdir()

    old_log = root / 'historical-install.log'
    run_logged([str(old_installer), '/S', '/LOG=' + str(old_log), '/D=' + str(install)], env=env,
               log_path=logs / 'historical-install.json')
    old_plugins = install / 'resources/vigil-brain/_internal/plugins'
    for required in ('websearch.py', 'kb.py'):
        if not (old_plugins / required).is_file():
            raise RuntimeError('Published P21.2 installation did not contain expected legacy plugin: ' + required)
    if not (install / 'Kestrel.exe').is_file():
        raise RuntimeError('Historical installer did not create the Kestrel application')
    if not (install / 'resources/vigil-brain/vigil-brain.exe').is_file():
        raise RuntimeError('Historical installer did not create the Brain sidecar')

    # The published P21.2 sidecar prints Chinese before it configures its
    # streams. GitHub's en-US runner uses cp1252, so use a UTF-8 process locale
    # only to initialize the real old installation. The upgraded P21.5 start
    # below receives the runner's unmodified environment and exercises the fix.
    old_runtime_env = dict(env, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    old_start = run_logged(['node', str(runtime_helper), str(install), 'old-install'], env=old_runtime_env,
                           log_path=logs / 'historical-first-start.json')
    old_record = json.loads(old_start.stdout.strip().splitlines()[-1])
    if old_record.get('result') != 'passed' or old_record.get('brain_health') != 'ready':
        raise RuntimeError('Published historical Electron/Brain failed startup')
    home = Path(old_record['home']).resolve()
    conversation_id = old_record['conversation_id']
    config = home / 'config/config.json'
    database = home / 'data/db/vigil.db'
    if not config.is_file() or not database.is_file():
        raise RuntimeError('Historical startup did not establish user configuration and conversation database')
    config_value = json.loads(config.read_text(encoding='utf-8'))
    config_value['p21_historical_upgrade_sentinel'] = 'keep-user-config'
    config.write_text(json.dumps(config_value, ensure_ascii=False, indent=2), encoding='utf-8')

    installed_plugins = home / 'data/installed_plugins'
    installed_plugins.mkdir(parents=True, exist_ok=True)
    # Model an already-installed market copy by keeping the actual plugin source
    # and adding a harmless user edit. The migration must never overwrite it.
    market_version = installed_plugins / 'websearch.py'
    market_version.write_bytes((old_plugins / 'websearch.py').read_bytes()
                                + b'\n# P21.5 historical upgrade user-version sentinel\n')
    market_hash = sha256(market_version)
    plugin_data = home / 'data/plugin_data/websearch/p21-historical-sentinel.json'
    plugin_data.parent.mkdir(parents=True, exist_ok=True)
    plugin_data.write_text('{"keep":"historical-plugin-data"}\n', encoding='utf-8')

    upgrade_log = root / 'candidate-upgrade.log'
    run_logged([str(candidate), '/S', '/LOG=' + str(upgrade_log), '/D=' + str(install)], env=env,
               log_path=logs / 'candidate-upgrade.json')
    for relative, digest in contents.items():
        installed_file = install / Path(relative)
        if not installed_file.is_file() or sha256(installed_file) != digest:
            raise RuntimeError('P21.5 upgrade did not install candidate payload: ' + relative)
    upgraded = run_logged(['node', str(runtime_helper), str(install), 'verify', conversation_id], env=env,
                          log_path=logs / 'candidate-first-start.json')
    if json.loads(upgraded.stdout.strip().splitlines()[-1]).get('result') != 'passed':
        raise RuntimeError('P21.5 upgraded Electron/Brain did not preserve history/plugins')
    if sha256(market_version) != market_hash:
        raise RuntimeError('Upgrade overwrote the previously installed market plugin version')
    if not (installed_plugins / 'kb.py').is_file():
        raise RuntimeError('P21.5 did not migrate the historical bundled kb plugin')
    if json.loads(config.read_text(encoding='utf-8')).get('p21_historical_upgrade_sentinel') != 'keep-user-config':
        raise RuntimeError('Upgrade lost the historical user configuration sentinel')
    if not plugin_data.is_file() or json.loads(plugin_data.read_text(encoding='utf-8')).get('keep') != 'historical-plugin-data':
        raise RuntimeError('Upgrade lost historical plugin user data')
    if not database.is_file() or database.stat().st_size == 0:
        raise RuntimeError('Upgrade lost the historical conversation database')

    run_logged(['node', str(runtime_helper), str(install), 'uninstall-websearch'], env=env,
               log_path=logs / 'plugin-manager-uninstall.json')
    if market_version.exists():
        raise RuntimeError('Plugin Manager did not remove the uninstalled market plugin')
    run_logged([str(candidate), '/S', '/LOG=' + str(root / 'candidate-post-uninstall-upgrade.log'),
                '/D=' + str(install)], env=env, log_path=logs / 'candidate-post-uninstall-upgrade.json')
    final = run_logged(['node', str(runtime_helper), str(install), 'verify-uninstalled', conversation_id], env=env,
                       log_path=logs / 'candidate-post-uninstall-start.json')
    if json.loads(final.stdout.strip().splitlines()[-1]).get('result') != 'passed':
        raise RuntimeError('Uninstalled websearch reappeared after P21.5 reinstall')
    if not (installed_plugins / 'kb.py').is_file():
        raise RuntimeError('Unrelated historical plugin did not survive subsequent upgrade')
    if not plugin_data.is_file() or not config.is_file() or not database.is_file():
        raise RuntimeError('Subsequent upgrade removed persistent user data')

    report = {
        'result': 'passed',
        'test_kind': 'actual_published_historical_installer_upgrade',
        'environment': {'os': 'Windows isolated GitHub runner', 'root': str(root)},
        'historical': {'release': 'v1.1.9-p21.2.20261025.1',
                       'source_commit': args.expected_old_source_commit,
                       'installer': old_installer.name, 'size': old_installer.stat().st_size,
                       'sha256': sha256(old_installer), 'authenticode_status': args.authenticode_status,
                       'old_bundle_plugins': ['websearch.py', 'kb.py'],
                       'installer_was_actually_run': True, 'old_electron_and_brain_started': True,
                       'old_startup_locale': 'UTF-8 override for the en-US runner cp1252 console'},
        'candidate': {'version': manifest['ver'], 'source_commit': manifest['source_commit'],
                      'installer': candidate.name, 'size': candidate.stat().st_size,
                      'sha256': sha256(candidate)},
        'upgrade': {'in_place': True, 'electron_and_brain_started': True,
                    'candidate_started_without_python_encoding_override': True,
                    'legacy_websearch_loaded': True, 'legacy_kb_migrated': True,
                    'existing_market_version_hash_preserved': True,
                    'user_configuration_preserved': True, 'conversation_history_preserved': True,
                    'conversation_database_preserved': True, 'plugin_user_data_preserved': True,
                    'plugin_manager_uninstall_passed': True,
                    'uninstall_survived_subsequent_candidate_upgrade': True,
                    'candidate_payload_manifest_verified': True},
        'logs_dir': str(logs),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    archived_logs = output.parent / (output.stem + '-logs')
    if archived_logs.exists():
        raise RuntimeError('Refusing to overwrite historical upgrade logs: ' + str(archived_logs))
    shutil.copytree(logs, archived_logs)
    report['logs_dir'] = str(archived_logs)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('old_installer')
    parser.add_argument('candidate_installer')
    parser.add_argument('--expected-old-source-commit', required=True)
    parser.add_argument('--expected-old-version', required=True)
    parser.add_argument('--expected-old-sha256', required=True)
    parser.add_argument('--expected-new-source-commit', required=True)
    parser.add_argument('--authenticode-status', required=True)
    parser.add_argument('--output', required=True)
    parsed = parser.parse_args()
    if parsed.expected_old_source_commit != 'd0a7e90409614961fda3a4a33523e42d4d4b92dc':
        parser.error('Unexpected historical release source commit')
    if parsed.expected_old_version != '1.1.9-p21.2.20261025.1':
        parser.error('Unexpected historical release version')
    try:
        main(parsed)
    except Exception as error:
        output = Path(parsed.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        archived_logs = output.parent / (output.stem + '-logs')
        candidates = sorted(Path(tempfile.gettempdir()).glob('Vigil real historical P21 upgrade */logs'),
                            key=lambda item: item.stat().st_mtime, reverse=True)
        if candidates and not archived_logs.exists():
            shutil.copytree(candidates[0], archived_logs)
        failure = {'result': 'failed', 'error': f'{type(error).__name__}: {error}',
                   'logs_dir': str(archived_logs) if archived_logs.exists() else None}
        output.write_text(json.dumps(failure, indent=2), encoding='utf-8')
        raise
