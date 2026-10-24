# -*- mode: python ; coding: utf-8 -*-
"""vigil-brain sidecar 打包 spec。

用法（仓库根）：
    venv\\Scripts\\python.exe -m PyInstaller --noconfirm deploy\\vigil-brain.spec

产物：dist\\vigil-brain\\vigil-brain.exe（onedir，启动快、便于排坑）
     exe 所在目录就是她的"家"：config/ data/ logs/ 都落这儿。

设计决定（spike 版）：
- brain/core_features/ 仅包含随 Brain 发布的核心能力适配器；brain/plugins/ 是旧版/开发期扩展源，不进入制品。
- 可选扩展只从 data/installed_plugins/ 加载；市场制品与客户端安装包独立分发。
- 核心能力按文件路径动态加载（spec_from_file_location），必须给真实 .py；
- kb 包只被核心能力适配器动态 import，静态分析够不着 → 全部列进 hiddenimports；
- fastembed/onnxruntime 排除：懒加载失败会自动降级，记忆嵌入走云端 API。
"""
import os
import subprocess

BLOCK = os.path.normpath(os.path.join(SPECPATH, "..", "..", "brain"))

# 仅打包核心能力适配器。brain/plugins/ 是旧版/开发期扩展源，不进入发布制品。
core_feature_data = []
CORE_FEATURE_ALLOWLIST = {
    "__init__.py", "calc.py", "gate.py", "kb.py", "plugin_market.py",
    "react.py", "remote.py", "skill.py", "websearch.py",
}
seen_core_features = set()
features_src = os.path.join(BLOCK, "core_features")
repo_root = os.path.dirname(BLOCK)
tracked_features = subprocess.check_output(
    ["git", "ls-files", "-z", "--", "brain/core_features"], cwd=repo_root
).decode("utf-8").split(chr(0))
for tracked in sorted(p for p in tracked_features if p):
    if not tracked.startswith("brain/core_features/"):
        raise RuntimeError("Unexpected core feature build path: " + tracked)
    rel_file = tracked[len("brain/core_features/"):]
    if rel_file not in CORE_FEATURE_ALLOWLIST:
        raise RuntimeError("Unapproved core feature in release package: " + rel_file)
    seen_core_features.add(rel_file)
    source = os.path.join(repo_root, *tracked.split("/"))
    if not os.path.isfile(source) or os.path.islink(source):
        raise RuntimeError("Tracked core feature source is missing or linked: " + tracked)
    if rel_file.endswith((".pyc", ".pyo", ".log")) or os.path.basename(rel_file) == "_registry.json":
        continue
    rel_dir = os.path.dirname(rel_file) or "."
    # Top-level loader only recognizes standalone .py files and package directories.
    # Keep __init__.py so core_features is importable by capability_registry.
    if rel_dir == "." and (not rel_file.endswith(".py") or
                           (os.path.basename(rel_file).startswith("_") and rel_file != "__init__.py")):
        continue
    dest = "core_features" if rel_dir == "." else os.path.join("core_features", rel_dir)
    core_feature_data.append((source, dest))

if seen_core_features != CORE_FEATURE_ALLOWLIST:
    missing = sorted(CORE_FEATURE_ALLOWLIST - seen_core_features)
    raise RuntimeError("Required core feature source is missing: " + ", ".join(missing))

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
    datas=core_feature_data + skill_host_data + [(os.path.join(BLOCK, 'build-info.json'), '.')],
    hiddenimports=[
        "websocket",  # Optional Hub Bridge imports websocket-client lazily.
        "kb", "kb.config", "kb.chunker", "kb.embedder", "kb.engine",
        "kb.ingest", "kb.kb_core", "kb.reader", "kb.search", "kb.store",
        # 核心能力适配器按文件路径动态加载（不在静态分析图里），它们 import 的
        # 顶层模块必须手动带进包 —— localtools 只被 remote 能力用
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
