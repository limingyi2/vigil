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


CORE_FEATURE_NAMES = frozenset({
    "calc.py", "gate.py", "kb.py", "plugin_market.py",
    "react.py", "remote.py", "skill.py", "websearch.py",
})


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seed_core_features(bundle, home, legacy_roots=()):
    """Seed versioned core capability adapters; never seed optional plugins."""
    bundle, home = Path(bundle).resolve(), Path(home).resolve()
    marker = home / "data/run/bundled-core-features.json"
    previous = _read_json(marker)
    old_bundle_marker = _read_json(home / "data/run/bundled-plugins.json")
    current = {}
    for src in sorted(bundle.rglob("*")):
        if not src.is_file() or src.is_symlink() or src.suffix in (".pyc", ".pyo"):
            continue
        rel = src.relative_to(bundle)
        rel_name = rel.as_posix()
        dst = home / "core_features" / rel
        if not dst.resolve().is_relative_to(home):
            raise ValueError("Core feature path escapes the application home")
        digest = _sha256(src)
        old_hashes = {previous.get(rel_name), old_bundle_marker.get(rel_name)}
        for root in legacy_roots:
            old_root = Path(root)
            for folder in ("_internal/core_features", "_internal/plugins"):
                old = old_root / folder / rel
                if old.is_file() and not old.is_symlink():
                    old_hashes.add(_sha256(old))
        if not dst.exists() or (dst.is_file() and _sha256(dst) in old_hashes):
            dst.parent.mkdir(parents=True, exist_ok=True)
            temp = dst.with_name(dst.name + ".seed-tmp")
            shutil.copy2(src, temp)
            os.replace(temp, dst)
        elif not dst.is_file():
            raise ValueError("Core feature target is not a regular file: " + rel_name)
        current[rel_name] = digest

    removed_stale = 0
    # Remove only stale files that are still byte-for-byte equal to the previous
    # shipped core version. User-modified files and all plugin/user data survive.
    for rel_name, old_digest in previous.items():
        if rel_name in current:
            continue
        stale = home / "core_features" / rel_name
        if stale.is_file() and not stale.is_symlink() and _sha256(stale) == old_digest:
            stale.unlink()
            removed_stale += 1

    marker.parent.mkdir(parents=True, exist_ok=True)
    temp = marker.with_suffix(".tmp")
    temp.write_text(json.dumps(current, sort_keys=True), encoding="utf-8")
    os.replace(temp, marker)
    return {"seeded": len(current), "removed_stale": removed_stale}


def migrate_legacy_plugins(home):
    """Copy legacy user plugins into the user install directory without overwriting.

    New releases never ship a plugins/ tree. Existing homes may have one from older
    releases or user-created packages. Preserve optional packages as user installs;
    skip unchanged copies of the modules now shipped as core features. Source files
    are retained for recovery and no user data is deleted.
    """
    home = Path(home).resolve()
    legacy = home / "plugins"
    installed = home / "data/installed_plugins"
    if not legacy.is_dir() or legacy.is_symlink():
        return {"copied": 0, "skipped_core": 0, "skipped_links": 0}
    bundled = _read_json(home / "data/run/bundled-plugins.json")
    result = {"copied": 0, "skipped_core": 0, "skipped_links": 0}
    installed.mkdir(parents=True, exist_ok=True)

    for src in sorted(legacy.iterdir()):
        if src.is_symlink():
            result["skipped_links"] += 1
            continue
        if src.is_file():
            if src.name.startswith("_") or src.suffix.lower() not in (".py", ".json", ".md", ".txt"):
                continue
            core_copy = home / "core_features" / src.name
            same_as_current_core = (src.name in CORE_FEATURE_NAMES and core_copy.is_file()
                                    and _sha256(src) == _sha256(core_copy))
            same_as_old_bundle = bool(bundled.get(src.name) and _sha256(src) == bundled.get(src.name))
            if src.name in CORE_FEATURE_NAMES and (same_as_current_core or same_as_old_bundle):
                result["skipped_core"] += 1
                continue
            dst = installed / src.name
            if dst.exists():
                continue
            if not dst.resolve().is_relative_to(installed):
                raise ValueError("Legacy plugin target escapes install directory")
            shutil.copy2(src, dst)
            result["copied"] += 1
            continue

        if not src.is_dir():
            continue
        init_file = src / "__init__.py"
        if not init_file.is_file():
            continue
        # Reject a package containing symlinks rather than silently following them.
        if any(child.is_symlink() for child in src.rglob("*")):
            result["skipped_links"] += 1
            continue
        dst = installed / src.name
        if dst.exists():
            continue
        if not dst.resolve().is_relative_to(installed):
            raise ValueError("Legacy plugin package escapes install directory")
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.pyo", "*.log", ".host_started"))
        result["copied"] += 1
    return result


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
