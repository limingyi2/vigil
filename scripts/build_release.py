"""One source snapshot -> Brain + Electron. Dirty candidate builds cannot be released."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

try:
    from plugin_package_contract import (assert_no_bundled_plugin_tree,
                                         assert_no_optional_plugin_modules)
except ModuleNotFoundError:
    from scripts.plugin_package_contract import (assert_no_bundled_plugin_tree,
                                                 assert_no_optional_plugin_modules)

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


def assert_client_version_metadata(version, package, lock):
    """Keep source, npm package and npm lock identity on one candidate version."""
    expected = version.get('ver')
    code = version.get('code')
    if not isinstance(expected, str) or not expected or not isinstance(code, int):
        raise RuntimeError('client/version.json must define a version string and integer code')
    if package.get('version') != expected:
        raise RuntimeError('client/package.json version differs from client/version.json')
    if lock.get('version') != expected or (lock.get('packages') or {}).get('', {}).get('version') != expected:
        raise RuntimeError('client/package-lock.json version differs from client/version.json')
    return expected, code


def inspect_pyinstaller_archive(executable):
    """Inspect the actual EXE, including recursively embedded PYZ modules."""
    viewer = shutil.which('pyi-archive_viewer')
    if not viewer:
        raise RuntimeError('pyi-archive_viewer is required to inspect the Brain EXE archive')
    result = subprocess.run([viewer, '-b', '-r', str(executable)],
        cwd=ROOT, check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
    # The viewer emits readable sections followed by one archive member per line.
    # Keep the parser strict so changed or unrecognized output fails closed.
    sections = re.findall(
        r"(?m)^Contents of '[^']+' \([^)]+\):\s*([\s\S]*?)(?=^Contents of '|\Z)",
        result.stdout,
    )
    if not sections:
        raise RuntimeError('Could not parse recursive PyInstaller archive inventory')
    entries = [line.strip() for section in sections for line in section.splitlines()
               if line.strip()]
    if not entries:
        raise RuntimeError('PyInstaller archive viewer returned an empty inventory')
    entries = sorted(set(entries))
    assert_no_optional_plugin_modules(entries)
    return entries


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


def verify(candidate=False):
    assert_no_bundled_plugin_tree(OUT)
    build = json.loads((ROOT / 'client/build-info.json').read_text(encoding='utf-8'))
    client_version = json.loads((ROOT / 'client/version.json').read_text(encoding='utf-8'))
    client_package = json.loads((ROOT / 'client/package.json').read_text(encoding='utf-8'))
    client_lock = json.loads((ROOT / 'client/package-lock.json').read_text(encoding='utf-8'))
    ver, code = assert_client_version_metadata(client_version, client_package, client_lock)
    if ver != build['version'] or code != build['code']:
        raise RuntimeError('Client version metadata differs from the Brain build identity')
    manifest = json.loads((OUT / 'brain-manifest.json').read_text(encoding='utf-8'))
    executable = OUT / 'vigil-brain.exe'
    embedded_modules = inspect_pyinstaller_archive(executable)
    assert_no_bundled_plugin_tree(OUT, embedded_modules=embedded_modules)
    archive_manifest = manifest.get('module_archive') or {}
    if archive_manifest.get('executable_sha256') != filehash(executable):
        raise RuntimeError('Brain module archive report does not match the actual executable')
    if archive_manifest.get('entries') != embedded_modules:
        raise RuntimeError('Brain module archive report differs from the actual executable')
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
    lock = json.loads((ROOT / 'client/package-lock.json').read_text(encoding='utf-8'))
    assert_client_version_metadata(version, package, lock)
    info = dict(schema=1, version=version['ver'], code=version['code'], commit=git('rev-parse', 'HEAD'),
                source_digest=digest(), protocol=1, capabilities=CAPS, candidate=candidate)
    for rel in ['brain/build-info.json', 'client/build-info.json']:
        (ROOT / rel).write_text(json.dumps(info, indent=2), encoding='utf-8')
    if OUT.exists():
        assert OUT.resolve() == (ROOT / 'dist/brain-release/vigil-brain').resolve() and ROOT in OUT.resolve().parents
        shutil.rmtree(OUT)
    if BUILD_WORK.is_symlink() or BUILD_WORK.exists():
        if BUILD_WORK.resolve() != (ROOT / 'build/brain-release').resolve() or not BUILD_WORK.is_dir():
            raise RuntimeError('Unsafe Brain build work directory')
        shutil.rmtree(BUILD_WORK)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(OUT.parent),
        '--workpath', str(BUILD_WORK), str(ROOT / 'deploy/brain/vigil-brain.spec')], cwd=ROOT, check=True)
    assert_no_bundled_plugin_tree(OUT)
    embedded_modules = inspect_pyinstaller_archive(OUT / 'vigil-brain.exe')
    assert_no_bundled_plugin_tree(OUT, embedded_modules=embedded_modules)
    files = {p.relative_to(OUT).as_posix(): filehash(p) for p in sorted(OUT.rglob('*')) if p.is_file()}
    archive_manifest = {'executable_sha256': filehash(OUT / 'vigil-brain.exe'),
                        'viewer': 'pyi-archive_viewer -b -r',
                        'entry_count': len(embedded_modules), 'entries': embedded_modules}
    (OUT / 'brain-manifest.json').write_text(json.dumps(
        dict(info, files=files, module_archive=archive_manifest), indent=2), encoding='utf-8')
    print('[release] inspected %d embedded Brain archive entries; optional plugin modules: 0' %
          len(embedded_modules), flush=True)
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
            ['resources/app.asar', 'resources/vigil-brain/vigil-brain.exe', 'resources/vigil-brain/brain-manifest.json']}
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
