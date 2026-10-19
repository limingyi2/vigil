"""One source snapshot -> Brain + Electron. Dirty candidate builds cannot be released."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'dist/brain-release/vigil-brain'
BUILD_WORK = ROOT / 'build/brain-release'
CLIENT_DIST = ROOT / 'client/dist-release'
CAPS = ['creator_jobs', 'agent_runs', 'models_saved', 'chat_stream']
NON_SOURCE_REPORTS = {
    'docs/P9.2B_ACCEPTANCE_REPORT.md',
    'docs/P9.2B_DISABLE_DIAGNOSTIC_FOLLOWUP.md',
    'docs/P9.2_PLUGIN_UI_INJECTION_INVENTORY.md',
    'docs/P9.2_WECHAT_BRIDGE_PLAN.md',
    'docs/P9_WALLPAPER_WECHAT_SPLIT_AUDIT.md',
    'docs/P9.2B_WALLPAPER_CLIENT_INTEGRATION_REPORT.md',
}


def configure_outputs(candidate_id=None):
    """Use immutable candidate-specific paths so prior reports and builds survive."""
    global OUT, BUILD_WORK, CLIENT_DIST
    if candidate_id is None:
        OUT = ROOT / 'dist/brain-release/vigil-brain'
        BUILD_WORK = ROOT / 'build/brain-release'
        CLIENT_DIST = ROOT / 'client/dist-release'
        return
    if not __import__('re').fullmatch(r'[a-z0-9][a-z0-9.-]{0,63}', candidate_id):
        raise ValueError('Invalid candidate id')
    OUT = ROOT / 'dist/brain-release-candidates' / candidate_id / 'vigil-brain'
    BUILD_WORK = ROOT / 'build/brain-release-candidates' / candidate_id
    CLIENT_DIST = ROOT / 'client/dist-release-candidates' / candidate_id


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf-8').strip()


def digest():
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).decode().split('\0')
    h = hashlib.sha256()
    for name in sorted(set(filter(None, names))):
        if name in ('brain/build-info.json', 'client/build-info.json') or name in NON_SOURCE_REPORTS:
            continue
        p = ROOT / name
        if p.is_file():
            h.update(name.encode() + b'\0' + p.read_bytes() + b'\0')
    return h.hexdigest()


def filehash(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_release_manifest(installer, manifest_path, build):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    expected = {
        'source_commit': build['commit'],
        'ver': build['version'],
        'code': build['code'],
        'candidate': build['candidate'],
        'setup_file': installer.name,
        'sha256': filehash(installer),
        'size': installer.stat().st_size,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise RuntimeError('Release manifest mismatch: ' + key)
    return manifest


def verify(candidate=False, candidate_id=None):
    configure_outputs(candidate_id)
    build = json.loads((ROOT / 'client/build-info.json').read_text(encoding='utf-8'))
    manifest = json.loads((OUT / 'brain-manifest.json').read_text(encoding='utf-8'))
    if not candidate and (build['candidate'] or git('status', '--porcelain')):
        raise RuntimeError('Official installer requires a clean committed source; candidates are not releasable')
    if build['commit'] != git('rev-parse', 'HEAD') or build['source_digest'] != digest():
        raise RuntimeError('Source changed after Brain build; rebuild the complete Vigil package')
    if any(build.get(k) != manifest.get(k) for k in build):
        raise RuntimeError('Electron and Brain build identities differ')
    if json.loads((ROOT / 'client/package.json').read_text(encoding='utf-8'))['version'] != build['version']:
        raise RuntimeError('Client version differs from Brain')
    if not manifest.get('files', {}).get('vigil-brain.exe'):
        raise RuntimeError('Missing Brain executable')
    for rel, sha in manifest['files'].items():
        p = (OUT / rel).resolve()
        if not p.is_relative_to(OUT.resolve()) or filehash(p) != sha:
            raise RuntimeError('Brain file missing/modified: ' + rel)
    if json.loads((OUT / '_internal/build-info.json').read_text(encoding='utf-8')) != build:
        raise RuntimeError('Embedded Brain identity differs')
    return build


def build(candidate=False, brain_only=False, candidate_id=None):
    configure_outputs(candidate_id)
    if not candidate and git('status', '--porcelain'):
        raise RuntimeError('Commit and test source before building an official installer')
    package = json.loads((ROOT / 'client/package.json').read_text(encoding='utf-8'))
    version = json.loads((ROOT / 'client/version.json').read_text(encoding='utf-8'))
    if version['ver'] != package['version']:
        raise RuntimeError('version.json and package.json differ')
    info = dict(schema=1, version=version['ver'], code=version['code'], commit=git('rev-parse', 'HEAD'),
                source_digest=digest(), protocol=1, capabilities=CAPS, candidate=candidate)
    for rel in ['brain/build-info.json', 'client/build-info.json']:
        (ROOT / rel).write_text(json.dumps(info, indent=2), encoding='utf-8')
    if candidate_id and any(p.exists() or p.is_symlink() for p in (OUT, BUILD_WORK, CLIENT_DIST)):
        raise RuntimeError('Candidate output already exists; choose a new candidate id')
    if OUT.exists():
        assert OUT.resolve() == (ROOT / 'dist/brain-release/vigil-brain').resolve() and ROOT in OUT.resolve().parents
        shutil.rmtree(OUT)
    if BUILD_WORK.is_symlink() or BUILD_WORK.exists():
        if BUILD_WORK.resolve() != (ROOT / 'build/brain-release').resolve() or not BUILD_WORK.is_dir():
            raise RuntimeError('Unsafe Brain build work directory')
        shutil.rmtree(BUILD_WORK)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(OUT.parent),
        '--workpath', str(BUILD_WORK), str(ROOT / 'deploy/brain/vigil-brain.spec')], cwd=ROOT, check=True)
    files = {p.relative_to(OUT).as_posix(): filehash(p) for p in sorted(OUT.rglob('*')) if p.is_file()}
    (OUT / 'brain-manifest.json').write_text(json.dumps(dict(info, files=files), indent=2), encoding='utf-8')
    verify(candidate)
    if not brain_only:
        env = dict(os.environ, VIGIL_BUILD_PYTHON=sys.executable,
                   VIGIL_BUILD_CANDIDATE='1' if candidate else '0',
                   VIGIL_CANDIDATE_ID=candidate_id or '')
        npm = shutil.which('npm')
        through_npm = bool(npm)
        if npm:
            command = [npm, 'run', 'dist']
        else:
            # Build from the lockfile-installed local toolchain when npm itself is
            # not on PATH (for example, managed Windows build environments).
            builder = ROOT / 'client/node_modules/.bin/electron-builder.cmd'
            if not builder.is_file():
                raise RuntimeError('npm is unavailable and the local electron-builder executable is missing')
            command = [str(builder), '--win']
        if CLIENT_DIST.is_symlink() or CLIENT_DIST.exists():
            resolved = CLIENT_DIST.resolve()
            if resolved != CLIENT_DIST or CLIENT_DIST.parent.resolve() != (ROOT / 'client').resolve() or not CLIENT_DIST.is_dir():
                raise RuntimeError('Unsafe existing release output directory')
            shutil.rmtree(CLIENT_DIST)
        output_rel = str(CLIENT_DIST.relative_to(ROOT / 'client')).replace('\\', '/')
        brain_rel = os.path.relpath(OUT, ROOT / 'client').replace('\\', '/')
        if candidate_id:
            build_config = dict(package.get('build') or {})
            build_config['directories'] = dict(build_config.get('directories') or {}, output=output_rel)
            resources = list(build_config.get('extraResources') or [])
            if not resources or not isinstance(resources[0], dict):
                raise RuntimeError('Candidate requires a configured Brain extraResource')
            resources[0] = dict(resources[0], **{'from': brain_rel})
            build_config['extraResources'] = resources
            candidate_config = BUILD_WORK / 'electron-builder-candidate.json'
            candidate_config.write_text(json.dumps(build_config, indent=2), encoding='utf-8')
            command += ['--config', str(candidate_config)]
        elif through_npm:
            command += ['--', '--config.directories.output=' + output_rel]
        else:
            command += ['--config.directories.output=' + output_rel]
        subprocess.run(['cmd', '/c', *command] if os.name == 'nt' else command,
            cwd=ROOT / 'client', env=env, check=True)
        verify(candidate)
        installer = CLIENT_DIST / ('Vigil.Setup.' + info['version'] + '.exe')
        release = dict(code=info['code'], ver=info['version'], source_commit=info['commit'], candidate=candidate,
            sha256=filehash(installer), size=installer.stat().st_size,
            setup_file=installer.name, notes='Unified Electron and Brain update')
        manifest_path = CLIENT_DIST / 'release-manifest.json'
        manifest_path.write_text(json.dumps(release, indent=2), encoding='utf-8')
        verify_release_manifest(installer, manifest_path, info)
        contents = {rel: filehash(CLIENT_DIST / 'win-unpacked' / rel) for rel in
            ['resources/app.asar', 'resources/vigil-brain/vigil-brain.exe', 'resources/vigil-brain/brain-manifest.json']}
        installer.with_suffix('.contents.json').write_text(json.dumps(contents, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', action='store_true', help='Unpublished acceptance artifact, never a formal release')
    parser.add_argument('--candidate-id', help='Unique, immutable output namespace for this candidate')
    parser.add_argument('--brain-only', action='store_true')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if args.candidate_id:
        configure_outputs(args.candidate_id)
    if args.verify:
        verify(args.candidate, args.candidate_id)
    else:
        build(args.candidate, args.brain_only, args.candidate_id)
