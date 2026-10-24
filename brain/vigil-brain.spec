# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['server_launch.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('core_features/__init__.py', 'core_features'),
        ('core_features/plugin_market.py', 'core_features'),
        ('core_features/remote.py', 'core_features'),
        ('core_features/skill.py', 'core_features'),
        ('version.json', '.'),
    ],
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
