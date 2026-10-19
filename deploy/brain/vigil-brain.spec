# -*- mode: python ; coding: utf-8 -*-
"""vigil-brain sidecar 打包 spec。

用法（仓库根）：
    venv\\Scripts\\python.exe -m PyInstaller --noconfirm deploy\\vigil-brain.spec

产物：dist\\vigil-brain\\vigil-brain.exe（onedir，启动快、便于排坑）
     exe 所在目录就是她的"家"：config/ data/ logs/ 都落这儿。

设计决定（spike 版）：
- brain/plugins/ 只包含随核心发布的内置插件源码；市场插件源放在仓库 market/，
  不进入 Brain 包。运行时下载的插件写入 data/installed_plugins，也不进入包。
- 内置插件按文件路径动态加载（spec_from_file_location），必须给真实 .py；
- kb 包只被插件动态 import，静态分析够不着 → 全部列进 hiddenimports；
- fastembed/onnxruntime 排除：懒加载失败会自动降级，记忆嵌入走云端 API。
"""
import os

BLOCK = os.path.normpath(os.path.join(SPECPATH, "..", "..", "brain"))

# 带插件源码及目录型插件的前端/附属资源（运行时文件不进包）。
plugin_data = []
plugins_src = os.path.join(BLOCK, "plugins")
for root, dirs, files in os.walk(plugins_src):
    dirs[:] = sorted(d for d in dirs if d != "__pycache__")
    rel_dir = os.path.relpath(root, plugins_src)
    for f in sorted(files):
        if f.endswith((".pyc", ".pyo", ".log")) or f == "_registry.json":
            continue
        # 顶层加载器只认独立插件 .py；下划线脚本是开发/测试辅助文件。
        if rel_dir == "." and (not f.endswith(".py") or f.startswith("_")):
            continue
        dest = "plugins" if rel_dir == "." else os.path.join("plugins", rel_dir)
        plugin_data.append((os.path.join(root, f), dest))

# 带 skill_host（Node.js 技能宿主）：server.js + start_host.bat + skills/ 下所有技能
skill_host_data = []
skill_host_src = os.path.join(BLOCK, "skill_host")
for root, dirs, files in os.walk(skill_host_src):
    for f in files:
        if f.endswith(('.pyc', '.pyo')) or f == '.gitkeep' or f.endswith('.log') or f == '.host_started':
            continue
        src = os.path.join(root, f)
        rel = os.path.relpath(src, skill_host_src)
        dst = os.path.join("skill_host", os.path.dirname(rel))
        skill_host_data.append((src, dst))

a = Analysis(
    [os.path.join(BLOCK, "server_launch.py")],
    pathex=[BLOCK],
    binaries=[],
    datas=plugin_data + skill_host_data + [(os.path.join(BLOCK, 'build-info.json'), '.')],
    hiddenimports=[
        "kb", "kb.config", "kb.chunker", "kb.embedder", "kb.engine",
        "kb.ingest", "kb.kb_core", "kb.reader", "kb.search", "kb.store",
        # 插件按文件路径动态加载（不在静态分析图里），它们 import 的
        # 顶层模块必须手动带进包 —— localtools 只被 remote 插件用
        "localtools", "devices", "agent_runs",
        # 运行模式切换门（Ollama 生命周期）：只在路由/作业里按需 import
        "run_modes",
    ],
    excludes=[
        "fastembed", "onnxruntime", "onnx", "torch", "tkinter",
        "matplotlib", "pytest", "IPython", "jupyter", "setuptools",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="vigil-brain",
    debug=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="vigil-brain",
)
