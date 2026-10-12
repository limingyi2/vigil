"""Process-local, controlled-error file transactions; no plugin execution.

Staging and backups live beside plugins. This is not a crash recovery journal
or a cross-process lock, and it cannot undo effects of already running plugins.
"""
import functools
import os
import shutil
import tempfile
import threading

LOCK = threading.RLock()


def serialized(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        with LOCK:
            return fn(*args, **kwargs)
    return wrapped


def reject_links(path):
    """Reject symlink/junction components, including an existing target."""
    path = os.path.abspath(path)
    current = path
    while True:
        if os.path.islink(current) or getattr(os.path, 'isjunction', lambda p: False)(current):
            raise ValueError('不允许符号链接或 junction：%s' % current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent


def snapshot(path):
    reject_links(path)
    if not os.path.exists(path):
        return None
    if not os.path.isfile(path):
        raise ValueError('不是普通文件：%s' % path)
    with open(path, 'rb') as fh:
        return fh.read()


def configuration_snapshot():
    import paths
    return snapshot(str(paths.CONFIG_PATH))


def atomic_bytes(path, content):
    """Replace a file on its own filesystem; never truncate the original."""
    reject_links(path)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='_atomic_', dir=parent)
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def remove(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.remove(path)


def commit(root, stage, replacements, old_names, registry, registry_content, write_registry):
    """Commit staged files/one directory and registry, or restore prior bytes.

    Configuration is read-only: a concurrent change is detected and never
    overwritten. Backups are retained if any recovery operation fails.
    Caller holds LOCK and validated the staging tree without importing code.
    """
    phase = 'protect'
    backup = os.path.join(stage, '_backup')
    os.mkdir(backup)
    changed = []
    reg_before = None
    reg_attempted = False
    try:
        reject_links(root)
        reg_before = snapshot(registry)
        config_before = configuration_snapshot()
        if reg_before is not None:
            with open(os.path.join(backup, '_registry.snapshot'), 'wb') as fh:
                fh.write(reg_before)
        if config_before is not None:
            with open(os.path.join(backup, '_config.snapshot'), 'wb') as fh:
                fh.write(config_before)
        owned = sorted(set(old_names) | set(replacements))
        for name in owned:
            dst = os.path.join(root, name)
            reject_links(dst)
            if os.path.lexists(dst):
                saved = os.path.join(backup, name)
                if os.path.isdir(dst):
                    # Reject links even in existing code before backing it up.
                    for base, dirs, files in os.walk(dst):
                        for child in dirs + files:
                            reject_links(os.path.join(base, child))
                    shutil.copytree(dst, saved)
                else:
                    shutil.copy2(dst, saved)
        phase = 'replace'
        for name in owned:
            dst = os.path.join(root, name)
            # Record before mutation: os.replace may fail midway through commit.
            changed.append(name)
            if name in replacements:
                if os.path.isdir(dst):
                    os.replace(dst, os.path.join(stage, '_displaced_' + name))
                os.replace(replacements[name], dst)
            else:
                remove(dst)
        phase = 'registry'
        reg_attempted = True
        if not write_registry(registry_content):
            raise OSError('注册表写入失败')
        phase = 'configuration_guard'
        if configuration_snapshot() != config_before:
            raise OSError('渠道配置并发变更，已取消安装；保留最新用户配置')
        return True, {'phase': 'committed', 'rollback': 'not_needed'}
    except Exception as exc:
        failures = []
        for name in reversed(changed):
            try:
                dst = os.path.join(root, name)
                saved = os.path.join(backup, name)
                if os.path.lexists(saved):
                    remove(dst)
                    os.replace(saved, dst)
                else:
                    remove(dst)
            except Exception as recovery:
                failures.append('%s: %s' % (name, recovery))
        if reg_attempted:
            try:
                if reg_before is None:
                    remove(registry)
                else:
                    atomic_bytes(registry, reg_before)
            except Exception as recovery:
                failures.append('registry: %s' % recovery)
        # Never restore config from a snapshot: install did not mutate it and
        # another authenticated request may have legitimately updated it.
        result = {'err': '安装事务失败（%s）：%s' % (phase, exc),
                  'phase': phase, 'rollback': 'failed' if failures else 'restored'}
        if failures:
            result.update(rollback_errors=failures, recovery_path=stage)
            result['err'] += '；回滚失败，备份保留于 %s' % stage
        return False, result
