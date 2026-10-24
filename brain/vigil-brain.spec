# -*- mode: python ; coding: utf-8 -*-

# 注意：正式发布入口是 deploy/brain/vigil-brain.spec（scripts/build_release.py 调用它）。
# 本文件只用于在 brain/ 目录下直接 pyinstaller 调试，别把它当发布清单。
import os

_datas = [
    ('core_features/__init__.py', 'core_features'),
    ('core_features/plugin_market.py', 'core_features'),
    ('core_features/remote.py', 'core_features'),
    ('core_features/skill.py', 'core_features'),
    ('version.json', '.'),
]

# 内置插件随包发布；wallpaper 是市场外部插件，不进制品。
_SHADOWED = {'plugin_market.py', 'remote.py', 'skill.py', 'calc.py',
             '_registry.json', '_pending_restart.json'}
_plugins_root = os.path.join(SPECPATH, 'plugins')
for _root, _dirs, _files in os.walk(_plugins_root):
    _dirs[:] = [d for d in _dirs if d != '__pycache__']
    for _name in _files:
        _rel = os.path.relpath(os.path.join(_root, _name), _plugins_root)
        _parts = _rel.split(os.sep)
        if _parts[0] == 'wallpaper' or '.bak' in _name:
            continue
        if _name.endswith(('.pyc', '.pyo', '.log')):
            continue
        if len(_parts) == 1 and (_name.startswith('_') or _name in _SHADOWED):
            continue
        _dest = 'plugins' if len(_parts) == 1 else os.path.join('plugins', os.path.dirname(_rel))
        _datas.append((os.path.join(_root, _name), _dest))

a = Analysis(
    ['server_launch.py'],
    pathex=[],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        'websocket',
        # The optional Knowledge Base extension is loaded from user data and
        # imports these package modules dynamically in frozen builds.
        'kb', 'kb.config', 'kb.chunker', 'kb.embedder', 'kb.engine',
        'kb.ingest', 'kb.kb_core', 'kb.reader', 'kb.search', 'kb.store',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='vigil-brain',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='vigil-brain',
)
