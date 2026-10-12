"""Controlled-error transactions for Node Skill directories and channel config.

This is process-local coordination, not a crash-recovery journal or a cross-process
lock. Transaction backups are kept on the Skill filesystem until Host reload and
verification succeed. JavaScript side effects from setup/register cannot be undone.
"""
import json
import os
import shutil

import plugin_transaction as file_tx


def _inside(parent, child):
    rel = os.path.relpath(os.path.abspath(child), os.path.abspath(parent))
    return rel != os.pardir and not rel.startswith(os.pardir + os.sep) and not os.path.isabs(rel)


def _reject_tree_links(root):
    file_tx.reject_links(root)
    if not os.path.isdir(root):
        raise ValueError('Skill 根目录不是普通目录')
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            child = os.path.join(base, name)
            file_tx.reject_links(child)


def _read_config(path):
    raw = file_tx.snapshot(path)
    if raw is None:
        return None, {}
    value = json.loads(raw.decode('utf-8'))
    if not isinstance(value, dict):
        raise ValueError('配置文件顶层必须是对象')
    return raw, value


def channels_config_after_remove(raw, package):
    """Return new config bytes only when the package owns a channel entry."""
    if raw is None:
        return None
    value = json.loads(raw.decode('utf-8'))
    if not isinstance(value, dict):
        raise ValueError('配置文件顶层必须是对象')
    skill = value.get('skill')
    if not isinstance(skill, dict):
        return raw
    channels = skill.get('channels')
    if not isinstance(channels, dict) or package not in channels:
        return raw
    channels.pop(package, None)
    skill['channels'] = channels
    value['skill'] = skill
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def run(root, package, transaction_dir, new_dir, config_path, *, config_before,
        config_after=None, reload_host, verify_committed, verify_restored,
        action='install'):
    """Commit one directory install/update/uninstall or restore old state.

    `new_dir is None` means uninstall. `config_after is None` means this operation
    does not write config. Callbacks perform authenticated Host reload/list checks.
    On controlled failure, restore files and only restore config if its current bytes
    still equal the bytes written by this transaction.
    """
    root = os.path.abspath(root)
    transaction_dir = os.path.abspath(transaction_dir)
    final = os.path.join(root, package)
    backup = os.path.join(transaction_dir, 'old-skill')
    config_snapshot = os.path.join(transaction_dir, 'old-config.snapshot')
    had_final = os.path.lexists(final)
    config_written = False
    reload_attempted = False
    changed_dir = False
    phase = 'protect'
    errors = []
    try:
        os.makedirs(root, exist_ok=True)
        file_tx.reject_links(root)
        file_tx.reject_links(transaction_dir)
        if os.stat(root).st_dev != os.stat(transaction_dir).st_dev:
            raise ValueError('暂存区必须与 Skill 根目录位于同一文件系统')
        if not _inside(root, final):
            raise ValueError('事务目标路径越出 Skill 根目录')
        _reject_tree_links(root)
        if had_final and not os.path.isdir(final):
            raise ValueError('目标 Skill 不是普通目录')
        if new_dir is not None:
            file_tx.reject_links(new_dir)
            _reject_tree_links(new_dir)
            if not os.path.isdir(new_dir) or not _inside(transaction_dir, new_dir):
                raise ValueError('暂存 Skill 必须位于同一事务目录')

        if config_before is not None:
            with open(config_snapshot, 'wb') as stream:
                stream.write(config_before)
                stream.flush()
                os.fsync(stream.fileno())

        phase = 'files'
        if had_final:
            os.replace(final, backup)
            changed_dir = True
        if new_dir is not None:
            os.replace(new_dir, final)
            changed_dir = True

        if config_after is not None and config_after != config_before:
            phase = 'configuration_guard'
            current = file_tx.snapshot(config_path)
            if current != config_before:
                raise RuntimeError('渠道配置已被并发修改，保留最新配置并取消操作')
            phase = 'configuration_write'
            file_tx.atomic_bytes(config_path, config_after)
            config_written = True

        phase = 'host_reload'
        reload_attempted = True
        reload_host()
        phase = 'host_verify'
        verified = verify_committed()
        if not verified.get('ok'):
            raise RuntimeError(verified.get('err') or 'Host 未确认目标 Skill 状态')

        result = {
            'ok': True, 'phase': 'committed', 'rollback': 'not_needed',
            'static_validated': action != 'uninstall', 'files_committed': True,
            'config_committed': config_written or config_after == config_before,
            'host_reloaded': True, 'runtime_verified': True,
            'runtime': verified,
            'restart_needed': bool(had_final),
        }
        shutil.rmtree(transaction_dir, ignore_errors=True)
        return result
    except Exception as exc:
        phase_error = '%s: %s' % (phase, str(exc)[:180])
        rollback_dir_ok = True
        rollback_config_ok = True

        if changed_dir:
            try:
                if had_final and os.path.lexists(backup):
                    if os.path.lexists(final):
                        file_tx.remove(final)
                    os.replace(backup, final)
                elif not had_final and os.path.lexists(final):
                    file_tx.remove(final)
            except Exception as recovery:
                rollback_dir_ok = False
                errors.append('Skill 文件：%s' % str(recovery)[:180])

        if config_written:
            try:
                current = file_tx.snapshot(config_path)
                if current != config_after:
                    rollback_config_ok = False
                    errors.append('渠道配置在事务写入后又发生变化，未覆盖并发配置')
                elif config_before is None:
                    file_tx.remove(config_path)
                else:
                    file_tx.atomic_bytes(config_path, config_before)
            except Exception as recovery:
                rollback_config_ok = False
                errors.append('渠道配置：%s' % str(recovery)[:180])

        runtime_rollback_ok = True
        if reload_attempted and rollback_dir_ok and rollback_config_ok:
            try:
                reload_host()
                restored = verify_restored()
                if not restored.get('ok'):
                    raise RuntimeError(restored.get('err') or 'Host 未确认恢复后的 Skill 状态')
            except Exception as recovery:
                runtime_rollback_ok = False
                errors.append('Host 注册状态：%s' % str(recovery)[:180])
        elif reload_attempted:
            runtime_rollback_ok = False
            errors.append('文件或配置未恢复，未执行 Host reload')

        rollback_ok = rollback_dir_ok and rollback_config_ok and runtime_rollback_ok
        if rollback_ok:
            shutil.rmtree(transaction_dir, ignore_errors=True)
        result = {
            'ok': False, 'phase': phase, 'rollback': 'restored' if rollback_ok else 'failed',
            'static_validated': action != 'uninstall',
            'files_committed': False, 'config_committed': False,
            'host_reloaded': False, 'runtime_verified': False,
            'restart_needed': bool(had_final or reload_attempted),
            'err': 'Skill %s 事务失败（%s）' % (action, phase_error),
        }
        if errors:
            result['rollback_errors'] = errors
            result['recovery_path'] = transaction_dir
            result['err'] += '；回滚未完全成功，备份/证据保留于 %s' % transaction_dir
        return result

