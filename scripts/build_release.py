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
CAPS = ['creator_jobs', 'agent_runs', 'models_saved', 'chat_stream']


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf-8').strip()


def digest():
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).decode().split('\0')
    h = hashlib.sha256()
    for name in sorted(set(filter(None, names))):
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


def verify(candidate=False):
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


def build(candidate=False, brain_only=False, installer_test=False):
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
    if OUT.exists():
        assert OUT.resolve() == (ROOT / 'dist/brain-release/vigil-brain').resolve() and ROOT in OUT.resolve().parents
        shutil.rmtree(OUT)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(OUT.parent),
        '--workpath', str(ROOT / 'build/brain-release'), str(ROOT / 'deploy/brain/vigil-brain.spec')], cwd=ROOT, check=True)
    files = {p.relative_to(OUT).as_posix(): filehash(p) for p in sorted(OUT.rglob('*')) if p.is_file()}
    (OUT / 'brain-manifest.json').write_text(json.dumps(dict(info, files=files), indent=2), encoding='utf-8')
    verify(candidate)
    if not brain_only:
        env = dict(os.environ, VIGIL_BUILD_PYTHON=sys.executable, VIGIL_BUILD_CANDIDATE='1' if candidate else '0')
        command = ['npm', 'run', 'dist']
        if installer_test:
            if not candidate:
                raise RuntimeError('Isolated installer identity is for acceptance candidates only')
            command += ['--', '--config.appId=com.vigil.acceptance.unified', '--config.productName=Vigil Acceptance',
                '--config.win.executableName=Vigil', '--config.win.target=nsis',
                '--config.artifactName=Vigil-Acceptance-${version}.exe']
        subprocess.run(['cmd', '/c', *command] if os.name == 'nt' else command,
            cwd=ROOT / 'client', env=env, check=True)
        verify(candidate)
        installer = ROOT / 'client/dist' / (('Vigil-Acceptance-' if installer_test else 'Vigil Setup ') + info['version'] + '.exe')
        release = dict(code=info['code'], ver=info['version'], source_commit=info['commit'], candidate=candidate,
            sha256=filehash(installer), size=installer.stat().st_size,
            setup_file='Vigil.Setup.' + info['version'] + '.exe', notes='Unified Electron and Brain update')
        (ROOT / 'client/dist/release-manifest.json').write_text(json.dumps(release, indent=2), encoding='utf-8')
        contents = {rel: filehash(ROOT / 'client/dist/win-unpacked' / rel) for rel in
            ['resources/app.asar', 'resources/vigil-brain/vigil-brain.exe', 'resources/vigil-brain/brain-manifest.json']}
        installer.with_suffix('.contents.json').write_text(json.dumps(contents, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', action='store_true', help='Unpublished acceptance artifact, never a formal release')
    parser.add_argument('--brain-only', action='store_true')
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--installer-test', action='store_true', help='Separate NSIS identity; never touches production install registration')
    args = parser.parse_args()
    if args.verify:
        verify(args.candidate)
    else:
        build(args.candidate, args.brain_only, args.installer_test)
