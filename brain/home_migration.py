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
    "plugin_market.py", "remote.py", "skill.py",
})
# 这些可选功能已在 P21.2 下线：旧安装里的副本不再导入，避免以用户插件身份复活。
LEGACY_OPTIONAL_FEATURE_NAMES = frozenset({"kb.py", "react.py", "websearch.py"})
RETIRED_PLUGIN_NAMES = frozenset({"calc", "gate", "meme", "sticker_pack"})
# 外部插件由插件市场装到 data/installed_plugins，绝不从安装包种入。
# 打包 spec 是第一道防线；这里是纵深防御，旧包或误打包也带不进用户家目录。
_MIGRATED_PLUGIN_MARKER = "data/run/legacy-plugin-migration.json"


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


def _legacy_plugin_candidate(src, *, core_folder=False, plugin_name=None):
    """Return whether a legacy entry is an optional plugin candidate."""
    if src.is_symlink():
        return False
    plugin_name = plugin_name or (src.stem if src.is_file() else src.name)
    if plugin_name.lower() in RETIRED_PLUGIN_NAMES:
        return False
    if core_folder:
        return src.is_file() and src.name in LEGACY_OPTIONAL_FEATURE_NAMES
    if src.is_file():
        return (not src.name.startswith("_")
                and src.suffix.lower() in (".py", ".json", ".md", ".txt")
                and src.name not in CORE_FEATURE_NAMES)
    if src.is_dir() and (src / "__init__.py").is_file():
        return not any(child.is_symlink() for child in src.rglob("*"))
    return False


def _copy_legacy_plugin(src, installed, key, migrated, result, dest_name=None):
    """Copy one old plugin atomically, retaining source and sticky receipt."""
    if key in migrated:
        return
    dest_name = dest_name or src.name
    dst = installed / dest_name
    digest = _sha256(src) if src.is_file() else _tree_sha256(src)
    if dst.exists():
        # Existing market/user versions always win; receipt prevents later resurrection.
        migrated[key] = digest
        return
    if not dst.resolve().is_relative_to(installed):
        raise ValueError("Legacy plugin target escapes install directory")
    temp = installed / ("." + dest_name + ".migration-" + uuid.uuid4().hex)
    try:
        if src.is_dir():
            shutil.copytree(src, temp, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.pyo", "*.log", ".host_started"))
        else:
            shutil.copy2(src, temp)
        os.replace(temp, dst)
        result["copied"] += 1
        migrated[key] = digest
    except Exception as exc:
        if temp.is_dir():
            shutil.rmtree(temp, ignore_errors=True)
        else:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        result["errors"].append("%s: %s" % (src.name, str(exc)[:160]))


def migrate_legacy_plugins(home, legacy_roots=()):
    """Copy old optional plugins into installed_plugins without deleting sources.

    Legacy paths are migration-only. Every eligible optional plugin is imported,
    including copies identical to a version that an old release bundled. Receipts
    are sticky by source identity so an explicit uninstall cannot be undone at
    the next launch. Existing installed packages always take precedence.
    """
    home = Path(home).resolve()
    legacy = home / "plugins"
    installed = home / "data/installed_plugins"
    old_core = home / "core_features"
    result = {"copied": 0, "skipped_core": 0, "skipped_links": 0, "errors": []}
    legacy_roots = tuple(Path(root).resolve() for root in legacy_roots if root)
    if legacy.is_symlink():
        result["skipped_links"] += 1
        has_legacy = False
    else:
        has_legacy = legacy.is_dir()
    retired_root = installed / "_retired"
    if (not has_legacy and not old_core.is_dir() and not legacy_roots
            and not retired_root.is_dir()):
        return result
    installed.mkdir(parents=True, exist_ok=True)
    migrated = _read_json(home / _MIGRATED_PLUGIN_MARKER)

    sources = []
    if has_legacy:
        sources.extend((src, "plugins/" + src.name, False, "plugins/" + src.name)
                       for src in sorted(legacy.iterdir()))
    # Old application payloads are preserved in place. Only optional plugin
    # entries are copied; explicit core adapters remain owned by the application.
    for root in legacy_roots:
        if root == home or not root.is_dir() or root.is_symlink():
            continue
        for folder in ("_internal/plugins", "plugins", "_internal/core_features", "core_features"):
            source_dir = root / folder
            if not source_dir.is_dir() or source_dir.is_symlink():
                continue
            is_core_folder = folder.endswith("core_features")
            root_id = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()[:16]
            sources.extend((src, "legacy/" + root_id + "/" + folder + "/" + src.name,
                            is_core_folder, folder + "/" + src.name)
                           for src in sorted(source_dir.iterdir()))
    if old_core.is_dir() and not old_core.is_symlink():
        sources.extend((src, "core_features/" + src.name, True,
                        "core_features/" + src.name)
                       for src in sorted(old_core.iterdir()))

    for src, key, core_folder, legacy_key in sources:
        if src.is_symlink():
            result["skipped_links"] += 1
            continue
        if not _legacy_plugin_candidate(src, core_folder=core_folder):
            if src.name in CORE_FEATURE_NAMES:
                result["skipped_core"] += 1
            continue
        if src.is_dir() and any(child.is_symlink() for child in src.rglob("*")):
            result["skipped_links"] += 1
            continue
        # P21.0-P21.2 receipts used a root-relative key without the old
        # installation identity. Honor those receipts during the transition.
        if legacy_key in migrated:
            migrated[key] = migrated[legacy_key]
            continue
        _copy_legacy_plugin(src, installed, key, migrated, result)

    # P21.1 placed byte-identical old bundled plugin copies in _retired. Restore
    # them by copy, retaining that recovery data; a receipt prevents re-import
    # after a subsequent explicit uninstall.
    retired = retired_root
    if retired.is_dir() and not retired.is_symlink():
        for src in sorted(retired.iterdir()):
            if src.is_symlink():
                result["skipped_links"] += 1
                continue
            name = src.name
            if src.is_dir() and len(name) > 9 and name[-9] == "." and all(
                    c in "0123456789abcdef" for c in name[-8:].lower()):
                name = name[:-9]
            plugin_name = Path(name).stem if src.is_file() else name
            if not _legacy_plugin_candidate(src, plugin_name=plugin_name):
                continue
            if src.is_dir() and any(child.is_symlink() for child in src.rglob("*")):
                result["skipped_links"] += 1
                continue
            _copy_legacy_plugin(src, installed, "_retired/" + src.name,
                                migrated, result, dest_name=name)

    marker = home / _MIGRATED_PLUGIN_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    temp = marker.with_name(marker.name + ".tmp")
    temp.write_text(json.dumps(migrated, sort_keys=True), encoding="utf-8")
    os.replace(temp, marker)
    return result


def _tree_sha256(root):
    """Stable digest for a legacy directory package without following links."""
    digest = hashlib.sha256()
    root = Path(root)
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


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
