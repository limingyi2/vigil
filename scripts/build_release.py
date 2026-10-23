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


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf-8').strip()


def digest():
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).decode().split('\0')
    h = hashlib.sha256()
    for name in sorted(set(filter(None, names))):
        # Acceptance reports record the resulting artifact SHA, so including them
        # would make every report update invalidate an otherwise identical build.
        normalized = name.replace('\\', '/')
        if normalized.startswith('reports/') or normalized == 'client/tools/e2e.js':
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


def build_native_window_host():
    if os.name != 'nt':
        raise RuntimeError('Windows release requires the native window host and a Visual Studio C++ toolchain')
    script = ROOT / 'client/native-window-host/build.cmd'
    helper = ROOT / 'client/native-window-host/build/Release/native-window-host.exe'
    subprocess.run(['cmd', '/c', str(script)], cwd=ROOT / 'client', check=True)
    if not helper.is_file():
        raise RuntimeError('Native window host build completed without producing native-window-host.exe')


def verify(candidate=False):
    build = json.loads((ROOT / 'client/build-info.json').read_text(encoding='utf-8'))
    manifest = json.loads((OUT / 'brain-manifest.json').read_text(encoding='utf-8'))
    if not candidate and (build['candidate'] or git('status', '--porcelain')):
        raise RuntimeError('Official installer requires a clean committed source; candidates are not releasable')
    head = git('rev-parse', 'HEAD')
    if build['commit'] != head:
        changed = git('diff', '--name-only', build['commit'], head).splitlines()
        if not changed or any(not p.replace('\\', '/').startswith('reports/') for p in changed):
            raise RuntimeError('Source changed after Brain build; rebuild the complete Vigil package')
    if build['source_digest'] != digest():
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


def build(candidate=False, brain_only=False):
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
    if not brain_only:
        build_native_window_host()
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
        env = dict(os.environ, VIGIL_BUILD_PYTHON=sys.executable, VIGIL_BUILD_CANDIDATE='1' if candidate else '0')
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
        if through_npm:
            command += ['--', '--config.directories.output=dist-release']
        else:
            command += ['--config.directories.output=dist-release']
        subprocess.run(['cmd', '/c', *command] if os.name == 'nt' else command,
            cwd=ROOT / 'client', env=env, check=True)
        verify(candidate)
        # 安装包名从 package.json 的 build.productName 读取，不硬编码。
        # electron-builder 会按 productName 生成 Setup EXE，这里必须与之一致。
        build_cfg = package.get('build') or {}
        product_name = build_cfg.get('productName') or package.get('productName') or package.get('name') or 'Vigil'
        installer = CLIENT_DIST / (product_name + '.Setup.' + info['version'] + '.exe')
        if not installer.is_file():
            # 兜底：在 dist 目录里找唯一的 *.Setup.*.exe，避免改名后找不到
            candidates = list(CLIENT_DIST.glob('*.Setup.' + info['version'] + '.exe'))
            if len(candidates) == 1:
                installer = candidates[0]
            elif len(candidates) > 1:
                raise RuntimeError('Ambiguous installer artifacts: ' + ', '.join(c.name for c in candidates))
            else:
                raise RuntimeError('Installer not found: ' + installer.name)
        release = dict(code=info['code'], ver=info['version'], source_commit=info['commit'], candidate=candidate,
            sha256=filehash(installer), size=installer.stat().st_size,
            setup_file=installer.name, notes='Unified Electron and Brain update')
        manifest_path = CLIENT_DIST / 'release-manifest.json'
        manifest_path.write_text(json.dumps(release, indent=2), encoding='utf-8')
        verify_release_manifest(installer, manifest_path, info)
        contents = {rel: filehash(CLIENT_DIST / 'win-unpacked' / rel) for rel in
            ['resources/app.asar', 'resources/vigil-brain/vigil-brain.exe', 'resources/vigil-brain/brain-manifest.json',
             'resources/native-window-host/native-window-host.exe']}
        installer.with_suffix('.contents.json').write_text(json.dumps(contents, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', action='store_true', help='Unpublished acceptance artifact, never a formal release')
    parser.add_argument('--brain-only', action='store_true')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if args.verify:
        verify(args.candidate)
    else:
        build(args.candidate, args.brain_only)
