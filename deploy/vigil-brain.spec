# -*- mode: python ; coding: utf-8 -*-
"""vigil-brain sidecar 打包 spec。

用法（仓库根）：
    venv\\Scripts\\python.exe -m PyInstaller --noconfirm deploy\\vigil-brain.spec

产物：dist\\vigil-brain\\vigil-brain.exe（onedir，启动快、便于排坑）
     exe 所在目录就是她的"家"：config/ data/ logs/ 都落这儿。

设计决定（spike 版）：
- plugins/ 以**源码**随包分发，首启由 server_launch 种到 exe 旁 ——
  插件是按文件路径动态加载的（spec_from_file_location），必须给真实 .py；
- kb 包只被插件动态 import，静态分析够不着 → 全部列进 hiddenimports；
- fastembed/onnxruntime 排除：懒加载失败会自动降级，记忆嵌入走云端 API。
"""
import os

BLOCK = os.path.normpath(os.path.join(SPECPATH, "..", "brain"))

# 只带插件的 .py 源码（__pycache__、_registry.json 这类运行时垃圾不进包）
plugin_data = []
plugins_src = os.path.join(BLOCK, "plugins")
for f in sorted(os.listdir(plugins_src)):
    if f.endswith(".py"):
        plugin_data.append((os.path.join(plugins_src, f), "plugins"))

a = Analysis(
    [os.path.join(BLOCK, "server_launch.py")],
    pathex=[BLOCK],
    binaries=[],
    datas=plugin_data,
    hiddenimports=[
        "kb", "kb.config", "kb.chunker", "kb.embedder", "kb.engine",
        "kb.ingest", "kb.kb_core", "kb.reader", "kb.search", "kb.store",
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
    console=True,
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
