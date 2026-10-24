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
# calc 已在 P21.2 下线：旧安装里的 calc.py 不再往新 home 搬，否则它会以
# 用户插件的身份复活一个已经被删掉的功能。
LEGACY_OPTIONAL_FEATURE_NAMES = frozenset({"gate.py", "kb.py", "react.py", "websearch.py"})
# 外部插件由插件市场装到 data/installed_plugins，绝不从安装包种入。
# 打包 spec 是第一道防线；这里是纵深防御，旧包或误打包也带不进用户家目录。
EXTERNAL_PLUGIN_NAMES = frozenset({"wallpaper"})
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


def seed_plugins(bundle, home, legacy_roots=()):
    """Seed the plugins shipped inside the Brain package; never overwrite user edits.

    Bundled plugins land in home/plugins (the loader's builtin root). An extension
    that is not bundled — wallpaper today — lives only in data/installed_plugins.
    """
    bundle, home = Path(bundle).resolve(), Path(home).resolve()
    marker = home / "data/run/bundled-plugins.json"
    previous = _read_json(marker)
    current = {}
    seeded = 0
    for src in sorted(bundle.rglob("*")):
        if not src.is_file() or src.is_symlink():
            continue
        rel = src.relative_to(bundle)
        if "__pycache__" in rel.parts or src.suffix in (".pyc", ".pyo", ".log"):
            continue
        if rel.parts[0].lower().split(".")[0] in EXTERNAL_PLUGIN_NAMES:
            continue
        dst = home / "plugins" / rel
        if not dst.resolve().is_relative_to(home):
            raise ValueError("Bundled plugin path escapes the application home")
        digest = _sha256(src)
        old_hashes = {previous.get(rel.as_posix())}
        for root in legacy_roots:
            old = Path(root) / "_internal/plugins" / rel
            if old.is_file() and not old.is_symlink():
                old_hashes.add(_sha256(old))
        if not dst.exists() or (dst.is_file() and _sha256(dst) in old_hashes):
            dst.parent.mkdir(parents=True, exist_ok=True)
            temp = dst.with_name(dst.name + ".seed-tmp")
            shutil.copy2(src, temp)
            os.replace(temp, dst)
            seeded += 1
        elif not dst.is_file():
            raise ValueError("Bundled plugin target is not a regular file: " + rel.as_posix())
        current[rel.as_posix()] = digest
    marker.parent.mkdir(parents=True, exist_ok=True)
    temp = marker.with_suffix(".tmp")
    temp.write_text(json.dumps(current, sort_keys=True), encoding="utf-8")
    os.replace(temp, marker)
    return {"seeded": seeded, "tracked": len(current)}


def _matches_bundled_dir(src, bundled):
    """True when a legacy plugin directory is byte-identical to the shipped copy."""
    prefix = src.name + "/"
    entries = {k[len(prefix):]: v for k, v in bundled.items() if k.startswith(prefix)}
    if not entries:
        return False
    for rel, digest in entries.items():
        child = src / Path(rel)
        if not child.is_file() or child.is_symlink() or _sha256(child) != digest:
            return False
    return True


_IGNORED_INSTALL_SUFFIXES = (".pyc", ".pyo", ".log")


def _strict_dir_equals_bundled(src, bundled):
    """True only when the install directory is exactly the shipped file set.

    Unlike _matches_bundled_dir this also rejects extra files, so a user who
    dropped anything into a shipped package keeps it (and keeps overriding).
    """
    prefix = src.name + "/"
    expected = {k[len(prefix):]: v for k, v in bundled.items() if k.startswith(prefix)}
    if not expected:
        return False
    found = set()
    for path in src.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(src)
        if "__pycache__" in rel.parts or path.name.endswith(_IGNORED_INSTALL_SUFFIXES):
            continue
        rel_name = rel.as_posix()
        found.add(rel_name)
        if expected.get(rel_name) != _sha256(path):
            return False
    return found == set(expected)


def retire_superseded_installs(home):
    """Move unmodified copies of previously shipped plugins out of the install dir.

    P21.0 briefly copied unchanged bundled plugins into data/installed_plugins,
    where they shadow the newer bundled version after this release ships them
    again. Anything byte-identical to what that release shipped is moved to
    installed_plugins/_retired (kept, never deleted); a user-edited copy stays
    and keeps overriding the bundled plugin, which is the intended precedence.
    """
    home = Path(home).resolve()
    installed = home / "data" / "installed_plugins"
    bundled = _read_json(home / "data/run/bundled-plugins.json")
    result = {"retired": 0, "errors": []}
    if not bundled or not installed.is_dir():
        return result
    retired_root = installed / "_retired"
    for src in sorted(installed.iterdir()):
        if src.name == "_retired" or src.is_symlink():
            continue
        if src.is_file() and src.suffix == ".py":
            same_as_shipped = bool(bundled.get(src.name)) and _sha256(src) == bundled.get(src.name)
        elif src.is_dir() and (src / "__init__.py").is_file():
            same_as_shipped = _strict_dir_equals_bundled(src, bundled)
        else:
            continue
        if not same_as_shipped:
            continue
        try:
            retired_root.mkdir(parents=True, exist_ok=True)
            target = retired_root / src.name
            if target.exists():
                target = retired_root / (src.name + "." + uuid.uuid4().hex[:8])
            os.replace(src, target)
            result["retired"] += 1
        except Exception as exc:
            result["errors"].append("%s: %s" % (src.name, str(exc)[:160]))
    return result


def migrate_legacy_plugins(home, legacy_roots=()):
    """Copy legacy user plugins into the user install directory without overwriting.

    Bundled plugins ship in home/plugins, so an unchanged copy left by an older
    release must not also be imported as a user install (that would shadow the
    newer bundled version). Anything a user edited or added is copied instead.
    Source files are retained for recovery and no user data is deleted.
    """
    home = Path(home).resolve()
    legacy = home / "plugins"
    installed = home / "data/installed_plugins"
    old_core = home / "core_features"
    if legacy.is_symlink():
        return {"copied": 0, "skipped_core": 0, "skipped_links": 0, "errors": []}
    has_legacy = legacy.is_dir()
    legacy_roots = tuple(Path(root).resolve() for root in legacy_roots if root)
    if not has_legacy and not old_core.is_dir() and not legacy_roots:
        return {"copied": 0, "skipped_core": 0, "skipped_links": 0, "errors": []}
    bundled = _read_json(home / "data/run/bundled-plugins.json")
    migrated = _read_json(home / _MIGRATED_PLUGIN_MARKER)
    result = {"copied": 0, "skipped_core": 0, "skipped_links": 0, "errors": []}
    installed.mkdir(parents=True, exist_ok=True)

    for src in sorted(legacy.iterdir()) if has_legacy else ():
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
            if same_as_current_core or same_as_old_bundle:
                result["skipped_core"] += 1
                continue
            dst = installed / src.name
            digest = _sha256(src)
            # The legacy source is deliberately retained for recovery. Remember
            # which exact source revision was imported so uninstalling the user
            # copy cannot cause the next client upgrade/start to resurrect it.
            key = "plugins/" + src.name
            if migrated.get(key) == digest:
                continue
            if dst.exists():
                migrated[key] = digest
                continue
            if not dst.resolve().is_relative_to(installed):
                raise ValueError("Legacy plugin target escapes install directory")
            temp = dst.with_name(dst.name + ".migration-" + uuid.uuid4().hex)
            try:
                shutil.copy2(src, temp)
                os.replace(temp, dst)
                result["copied"] += 1
                migrated[key] = digest
            except Exception as exc:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
                result["errors"].append("%s: %s" % (src.name, str(exc)[:160]))
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
        key = "plugins/" + src.name
        digest = _tree_sha256(src)
        if migrated.get(key) == digest:
            continue
        if _matches_bundled_dir(src, bundled):
            result["skipped_core"] += 1
            continue
        if dst.exists():
            migrated[key] = digest
            continue
        if not dst.resolve().is_relative_to(installed):
            raise ValueError("Legacy plugin package escapes install directory")
        temp = installed / ("." + src.name + ".migration-" + uuid.uuid4().hex)
        try:
            shutil.copytree(src, temp, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.pyo", "*.log", ".host_started"))
            os.replace(temp, dst)
            result["copied"] += 1
            migrated[key] = digest
        except Exception as exc:
            shutil.rmtree(temp, ignore_errors=True)
            result["errors"].append("%s: %s" % (src.name, str(exc)[:160]))

    # Older clients stored extension code beside the packaged Brain instead of
    # under the persistent user home. Read those explicit previous-install roots
    # before the installer replaces them; copy only optional features from old
    # core_features, and preserve other non-core modules from old plugins trees.
    for root in legacy_roots:
        if root == home or not root.is_dir() or root.is_symlink():
            continue
        for folder in ("_internal/plugins", "plugins", "_internal/core_features", "core_features"):
            source_dir = root / folder
            if not source_dir.is_dir() or source_dir.is_symlink():
                continue
            is_core_folder = folder.endswith("core_features")
            for src in sorted(source_dir.iterdir()):
                if src.is_symlink():
                    result["skipped_links"] += 1
                    continue
                if is_core_folder:
                    if src.name not in LEGACY_OPTIONAL_FEATURE_NAMES or not src.is_file():
                        continue
                elif src.is_file():
                    if src.name.startswith("_") or src.suffix.lower() not in (".py", ".json", ".md", ".txt"):
                        continue
                    if src.name in CORE_FEATURE_NAMES:
                        # This is a previous application install root, so these
                        # names are shipped infrastructure adapters rather than
                        # user-installed extension entries. The old root remains
                        # untouched as recovery while the new Brain supplies them.
                        result["skipped_core"] += 1
                        continue
                elif src.is_dir():
                    if not (src / "__init__.py").is_file():
                        continue
                    if any(child.is_symlink() for child in src.rglob("*")):
                        result["skipped_links"] += 1
                        continue
                else:
                    continue

                digest = _sha256(src) if src.is_file() else _tree_sha256(src)
                key = folder + "/" + src.name
                if migrated.get(key) == digest:
                    continue
                dst = installed / src.name
                if dst.exists():
                    migrated[key] = digest
                    continue
                if not dst.resolve().is_relative_to(installed):
                    raise ValueError("Legacy plugin target escapes install directory")
                temp = installed / ("." + src.name + ".migration-" + uuid.uuid4().hex)
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

    # P21.0 briefly shipped optional feature modules under core_features/. Copy
    # them into the user install area before core seeding prunes unchanged files.
    if old_core.is_dir() and not old_core.is_symlink():
        for src in sorted(old_core.iterdir()):
            if (src.name not in LEGACY_OPTIONAL_FEATURE_NAMES or not src.is_file()
                    or src.is_symlink()):
                continue
            dst = installed / src.name
            key = "core_features/" + src.name
            digest = _sha256(src)
            if migrated.get(key) == digest:
                continue
            if dst.exists():
                migrated[key] = digest
                continue
            temp = dst.with_name(dst.name + ".migration-" + uuid.uuid4().hex)
            try:
                shutil.copy2(src, temp)
                os.replace(temp, dst)
                result["copied"] += 1
                migrated[key] = digest
            except Exception as exc:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
                result["errors"].append("%s: %s" % (src.name, str(exc)[:160]))
    # Persist migration receipts atomically; successful copies are idempotent
    # across restarts and do not turn an explicit later uninstall into reinstall.
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
