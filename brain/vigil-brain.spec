# -*- mode: python ; coding: utf-8 -*-

# 注意：正式发布入口是 deploy/brain/vigil-brain.spec（scripts/build_release.py 调用它）。
# 本文件只用于在 brain/ 目录下直接 pyinstaller 调试，别把它当发布清单。
import os

_datas = [
    ('version.json', '.'),
    ('config/experts/code-expert', 'config/experts/code-expert'),
    ('config/experts/writing-expert', 'config/experts/writing-expert'),
]

# 旧调试配置也必须遵守零可选插件规则：不要遍历 SPECPATH/plugins。
# 只显式带入被批准的核心适配器和 Skill Host 基础设施。
_CORE_ALLOWLIST = {'__init__.py', 'plugin_market.py', 'remote.py', 'skill.py'}
_core_root = os.path.join(SPECPATH, 'core_features')
for _name in sorted(_CORE_ALLOWLIST):
    _source = os.path.join(_core_root, _name)
    if not os.path.isfile(_source) or os.path.islink(_source):
        raise RuntimeError('Required core feature is missing or linked: ' + _name)
    _datas.append((_source, 'core_features'))

_SKILL_HOST_ALLOWLIST = {'server.js', 'start_host.bat'}
_skill_host_root = os.path.join(SPECPATH, 'skill_host')
for _name in sorted(_SKILL_HOST_ALLOWLIST):
    _source = os.path.join(_skill_host_root, _name)
    if not os.path.isfile(_source) or os.path.islink(_source):
        raise RuntimeError('Required Skill Host infrastructure is missing or linked: ' + _name)
    _datas.append((_source, 'skill_host'))

a = Analysis(
    ['server_launch.py'],
    pathex=[],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        'websocket',
        # Core Knowledge Base engine uses the unified SQLite search index.
        'kb', 'kb.config', 'kb.chunker', 'kb.engine',
        'kb.ingest', 'kb.kb_core', 'kb.reader', 'kb.search',
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
