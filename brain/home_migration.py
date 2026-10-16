"""Copy-only legacy migration. SQLite backup includes committed WAL transactions."""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

DIRECTORIES = ('config', 'data', 'plugins', 'logs', 'archive', 'skill_host/skills', 'skill_host/data')


def seed_plugins(bundle, home, legacy_roots=()):
    """Update untouched shipped code; never overwrite custom plugin changes."""
    bundle, home = Path(bundle), Path(home)
    marker = home / 'data/run/bundled-plugins.json'
    previous = json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {}
    current = {}
    def sha(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()
    for src in bundle.rglob('*'):
        if not src.is_file() or src.suffix == '.pyc':
            continue
        rel = src.relative_to(bundle)
        dst = home / 'plugins' / rel
        if not dst.resolve().is_relative_to(home.resolve()):
            raise ValueError('Plugin directory contains an escaping link')
        digest = sha(src)
        old_hashes = {previous.get(rel.as_posix())}
        for root in legacy_roots:
            old = Path(root) / '_internal/plugins' / rel
            if old.is_file():
                old_hashes.add(sha(old))
        if not dst.exists() or sha(dst) in old_hashes:
            dst.parent.mkdir(parents=True, exist_ok=True)
            temp = dst.with_name(dst.name + '.seed-tmp')
            shutil.copy2(src, temp)
            os.replace(temp, dst)
        current[rel.as_posix()] = digest
    marker.parent.mkdir(parents=True, exist_ok=True)
    temp = marker.with_suffix('.tmp')
    temp.write_text(json.dumps(current), encoding='utf-8')
    os.replace(temp, marker)


def migrate(source, home):
    source, home = Path(source).resolve(), Path(home).resolve()
    if source == home:
        return {'copied': 0, 'conflicts': 0, 'skipped_links': 0}
    if source in home.parents or home in source.parents or not source.is_dir():
        raise ValueError('Migration source and destination must be separate directories')
    home.mkdir(parents=True, exist_ok=True)
    def contained(p):
        if not p.resolve().is_relative_to(home):
            raise ValueError('Migration target contains an escaping link')
    contained(home / 'migration')
    ident = hashlib.sha256(str(source).encode()).hexdigest()[:16]
    marker = home / 'migration' / (ident + '.json')
    if marker.exists():
        return json.loads(marker.read_text(encoding='utf-8'))
    stage = home / 'migration' / ('stage-' + uuid.uuid4().hex)
    stage.mkdir(parents=True)
    result = {'copied': 0, 'conflicts': 0, 'skipped_links': 0}
    try:
        for directory in DIRECTORIES:
            base = source / directory
            if base.is_symlink():
                result['skipped_links'] += 1
                continue
            if not base.is_dir():
                continue
            for root, dirs, files in os.walk(base, followlinks=False):
                clean = [d for d in dirs if not (Path(root) / d).is_symlink() and d != '__pycache__']
                result['skipped_links'] += len(dirs) - len(clean)
                dirs[:] = clean
                for name in files:
                    src = Path(root) / name
                    if src.is_symlink():
                        result['skipped_links'] += 1
                        continue
                    if name.endswith(('-wal', '-shm', '-journal', '.pyc', '.lock')):
                        continue
                    rel = src.relative_to(source)
                    dst = stage / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    with src.open('rb') as f:
                        sqlite = f.read(16) == b'SQLite format 3\x00'
                    if sqlite:
                        # Read-only SQLite online backup, never raw-copy an open DB.
                        with closing(sqlite3.connect(src.as_uri() + '?mode=ro', uri=True, timeout=5)) as inp, closing(sqlite3.connect(dst)) as out:
                            deadline = time.monotonic() + 30
                            def progress(*_):
                                if time.monotonic() > deadline:
                                    raise TimeoutError('Database snapshot timed out; original retained')
                            inp.backup(out, pages=256, progress=progress)
                    else:
                        shutil.copy2(src, dst)
        for src in stage.rglob('*'):
            if not src.is_file():
                continue
            rel = src.relative_to(stage)
            dst = home / rel
            if dst.exists():
                dst = home / 'migration' / 'conflicts' / ident / rel
                result['conflicts'] += 1
            else:
                result['copied'] += 1
            contained(dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                os.replace(src, dst)
        # Rewrite only path values contained in the selected legacy home.
        cfg = home / 'config/config.json'
        if cfg.exists() and not (home / 'migration/conflicts' / ident / 'config/config.json').exists():
            value = json.loads(cfg.read_text(encoding='utf-8-sig'))
            for key in ('persona_file', 'memory_dir'):
                old = value.get(key)
                if isinstance(old, str) and Path(old).is_absolute():
                    try:
                        new = home / Path(old).resolve().relative_to(source)
                        if new.exists():
                            value[key] = str(new)
                    except ValueError:
                        pass
            cfg.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        marker.parent.mkdir(exist_ok=True)
        temp = marker.with_suffix('.tmp')
        temp.write_text(json.dumps(result), encoding='utf-8')
        os.replace(temp, marker)
        return result
    finally:
        # stage was generated under this home; no user-supplied deletion path.
        assert stage.parent == home / 'migration' and stage.name.startswith('stage-')
        shutil.rmtree(stage, ignore_errors=True)
