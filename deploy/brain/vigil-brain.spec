# -*- mode: python ; coding: utf-8 -*-
"""vigil-brain sidecar 打包 spec。

用法（仓库根）：
    venv\\Scripts\\python.exe -m PyInstaller --noconfirm deploy\\vigil-brain.spec

产物：dist\\vigil-brain\\vigil-brain.exe（onedir，启动快、便于排坑）
     exe 所在目录就是她的"家"：config/ data/ logs/ 都落这儿。

设计决定（spike 版）：
- brain/core_features/ 是随 Brain 发布的核心能力适配器；
  brain/plugins/ 是历史源码/升级迁移来源，绝不是打包输入。
- 外部插件只从 data/installed_plugins/ 加载；市场制品与客户端安装包独立分发。
- 核心能力按文件路径动态加载（spec_from_file_location），必须给真实 .py；
- kb 包只被核心能力适配器动态 import，静态分析够不着 → 全部列进 hiddenimports；
- fastembed/onnxruntime 排除：懒加载失败会自动降级，记忆嵌入走云端 API。
"""
import os
import subprocess
import sys

BLOCK = os.path.normpath(os.path.join(SPECPATH, "..", "..", "brain"))

# 只打包经过逐项批准的核心适配器。绝不扫描 brain/plugins 或 skill 包目录。
core_feature_data = []
CORE_FEATURE_ALLOWLIST = {
    "__init__.py", "plugin_market.py", "remote.py", "skill.py",
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

# Skill Host 基础设施是核心；技能包本身是独立安装内容，不能随宿主递归打包。
skill_host_data = []
skill_host_src = os.path.join(BLOCK, "skill_host")
SKILL_HOST_ALLOWLIST = {"server.js", "start_host.bat"}
tracked_skill_host = subprocess.check_output(
    ["git", "ls-files", "-z", "--", "brain/skill_host"], cwd=repo_root
).decode("utf-8").split(chr(0))
seen_skill_host = set()
for tracked in sorted(p for p in tracked_skill_host if p):
    if not tracked.startswith("brain/skill_host/"):
        raise RuntimeError("Unexpected Skill Host build path: " + tracked)
    rel_file = tracked[len("brain/skill_host/"):]
    # Repository may carry this launcher state marker for deploy/restart logic;
    # it is mutable runtime state, not Skill Host infrastructure for releases.
    if rel_file in {"skills/.gitkeep", ".host_started"}:
        continue
    if rel_file not in SKILL_HOST_ALLOWLIST:
        raise RuntimeError("Unapproved Skill Host build input: " + rel_file)
    source = os.path.join(repo_root, *tracked.split("/"))
    if not os.path.isfile(source) or os.path.islink(source):
        raise RuntimeError("Skill Host infrastructure source is missing or linked: " + tracked)
    seen_skill_host.add(rel_file)
    skill_host_data.append((source, "skill_host"))
if seen_skill_host != SKILL_HOST_ALLOWLIST:
    raise RuntimeError("Required Skill Host infrastructure is missing: " +
                       ", ".join(sorted(SKILL_HOST_ALLOWLIST - seen_skill_host)))

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

# Reject modules discovered from any file under brain/plugins, even when the
# resulting archive module name does not contain a `plugins/` path component.
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
from scripts.plugin_package_contract import assert_no_optional_plugin_modules
assert_no_optional_plugin_modules(a.pure, plugin_source_root=os.path.join(BLOCK, "plugins"))

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
