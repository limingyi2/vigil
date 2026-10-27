"""测试数据目录隔离的共享实现。

被 `tests/conftest.py` 与 `brain/tests/conftest.py` 共同引用，
避免两份逻辑漂移。文件名以 `_` 开头 —— pytest 默认只收集
`test_*.py` 与 `*_test.py`，这个模块不会被当成测试收集。

用法见 `tests/conftest.py` 的模块注释。

## 设计要点（都是已知问题）

**1. 绝不删 `sys.modules["paths"]`。**
删掉再 import 会造出第二个模块实例：别处 `import paths` 拿到的还是老对象，
而 `storage.db` 拿到的是新对象 —— 两边 `DATA_DIR` 不是同一个变量，
patch 完一个、另一个还指着真实用户目录。表现就是「单跑通过、全量失败」。
（`tests/test_p17_dual_db_hybrid.py` 的注释里记了同一个坑。）

**2. 派生常量必须一起 patch。**
`paths.py` 里的 `MEMORY_DIR` / `KB_DIR` / `PLUGIN_INSTALL_DIR` 等是在
import 期由 `DATA_DIR` 算好的常量。只 patch `DATA_DIR` 的话，
插件目录仍指向生产库 —— 插件相关的测试会因此失败。
所以这里按 `paths.py` 的派生关系把整组常量重新指向 tmp_path。

**3. 数据库目录用 `storage.db.set_dir()`，不用 monkeypatch。**
`storage.db._dir()` 的优先级是 `_DB_DIR > paths.db_dir()`；只 patch
`paths.DATA_DIR` 时残留的 `_DB_DIR` 仍会生效。`set_dir()` 顺带
`close_all()`，把上一轮的长连接句柄放掉。

**4. 不预创建子目录。**
有些测试自己会 `tmp_path/'data'.mkdir()`（不带 exist_ok），
我们抢先建过就会撞 `FileExistsError`。目录让被测代码按需创建。
"""

import os
import sys

# brain/ 目录进 sys.path，让 `import paths` / `from storage import db`
# 这类绝对导入在本模块被导入时就能用上。
_BRAIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _BRAIN_DIR not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))


def isolate(monkeypatch, tmp_path):
    """把数据目录与数据库目录指到 tmp_path。

    返回一个清理函数，测试结束后必须调用（`set_dir(None)` 复位 +
    `close_all()` 放掉长连接句柄，否则 Windows 上 tmp_path 删不掉）。
    """
    import paths
    from storage import db as store

    home = str(tmp_path)
    data_dir = os.path.join(home, "data")

    # --- 1. 根与数据目录 ---
    monkeypatch.setattr(paths, "ROOT", home, raising=False)
    monkeypatch.setattr(paths, "DATA_DIR", data_dir, raising=False)

    # --- 2. DATA_DIR 派生出来的常量（paths.py 的定义顺序）---
    # 插件安装目录：plugin_host._INSTALLED_DIR 等按这个算
    monkeypatch.setattr(paths, "PLUGIN_INSTALL_DIR",
                        os.path.join(data_dir, "installed_plugins"), raising=False)
    monkeypatch.setattr(paths, "MEMORY_DIR",
                        os.path.join(data_dir, "memory"), raising=False)
    monkeypatch.setattr(paths, "LIFE_DIR",
                        os.path.join(data_dir, "her_life"), raising=False)
    monkeypatch.setattr(paths, "UPLOAD_DIR",
                        os.path.join(data_dir, "upload"), raising=False)
    monkeypatch.setattr(paths, "PLUGIN_DATA_DIR",
                        os.path.join(data_dir, "plugins"), raising=False)
    monkeypatch.setattr(paths, "KB_DIR",
                        os.path.join(data_dir, "kb"), raising=False)
    monkeypatch.setattr(paths, "DEVICES_PATH",
                        os.path.join(data_dir, "devices.json"), raising=False)
    monkeypatch.setattr(paths, "MANAGEMENT_PATH",
                        os.path.join(data_dir, "management.json"), raising=False)
    monkeypatch.setattr(paths, "AGENT_CENTER_DIR",
                        os.path.join(data_dir, "agent_center"), raising=False)
    monkeypatch.setattr(paths, "RUN_DIR",
                        os.path.join(data_dir, "run"), raising=False)
    monkeypatch.setattr(paths, "DB_DIR",
                        os.path.join(data_dir, "db"), raising=False)
    monkeypatch.setattr(paths, "MAIN_DB_PATH",
                        os.path.join(data_dir, "db", "vigil.db"), raising=False)
    monkeypatch.setattr(paths, "LOGS_DB_PATH",
                        os.path.join(data_dir, "db", "vigil_logs.db"), raising=False)
    monkeypatch.setattr(paths, "CHAT_LOG",
                        os.path.join(data_dir, "chat_history.jsonl"), raising=False)

    # --- 3. config 目录（人设、world.json 等）---
    config_dir = os.path.join(home, "config")
    monkeypatch.setattr(paths, "CONFIG_DIR", config_dir, raising=False)
    monkeypatch.setattr(paths, "CONFIG_PATH",
                        os.path.join(config_dir, "config.json"), raising=False)
    monkeypatch.setattr(paths, "WORLD_PATH",
                        os.path.join(config_dir, "world.json"), raising=False)
    monkeypatch.setattr(paths, "PERSONA_DIR",
                        os.path.join(config_dir, "personas"), raising=False)

    # --- 4. ROOT 派生目录 ---
    # 注意：这里**不**动 CORE_FEATURES_DIR / PLUGINS_DIR。
    # 它们在源码目录下（brain/core_features、brain/plugins），属于**代码**
    # 而不是用户数据 —— core_features 里是核心能力适配器，插件的 builtin
    # 判定（plugin_manager._source → plugin_host._CORE_PLUGIN_ROOT）依赖它。
    # 隔离到临时目录会让 builtin 插件被误判成非 builtin，插件相关测试全挂。
    # 它们是只读的，测试不会往里写东西。
    monkeypatch.setattr(paths, "LOGS_DIR",
                        os.path.join(home, "logs"), raising=False)
    monkeypatch.setattr(paths, "ARCHIVE_DIR",
                        os.path.join(home, "archive"), raising=False)

    # --- 5. 数据库目录 ---
    #  **不钉 `_DB_DIR`，让它跟随 `paths.DATA_DIR`。**
    #
    # `storage.db._dir()` 的优先级是 `_DB_DIR > paths.db_dir()`。如果我们
    # 用 `set_dir()` 硬钉，测试自己 patch `paths.DATA_DIR` 就会失效 ——
    # `tests/test_p12_protocol.py` 正是这么干的：
    #
    # ```python
    # with patch.object(runs.paths, 'DATA_DIR', str(tmp_path)), ...
    # ```
    #
    # 它想让 agent_runs 的库落在自己的 tmp_path 下。硬钉 `_DB_DIR` 时，
    # 库仍写在我们指定的公共目录，测试断言 `count == 1` 读到的就不是
    # 这一次运行的数据了（表现为「单跑通过、全量失败」）。
    #
    # 所以这里只保证 `paths.db_dir()` 会落到 tmp_path（靠上面 patch 过的
    # `paths.DATA_DIR` 与 `paths.DB_DIR`），让 `storage.db` 自然跟随。
    # 需要单独指定库目录的测试自己调 `storage.db.set_dir()`。
    store.set_dir(None)

    # --- 6. 模块加载期拷贝了 paths 常量的地方 ---
    # plugin_host 在 import 期执行 `from paths import PLUGINS_DIR, ...`，
    # 把值拷成自己的模块常量。之后 patch paths 对它无效 —— 插件目录判定
    # 会继续用生产目录。这里同步它已经加载了的副本。
    #
    # 只同步**数据目录**类的副本：_INSTALLED_DIR（市场安装的插件）属于
    # 运行时数据，要隔离；_DIR / _CORE_PLUGIN_ROOT 是代码目录，保持原值。
    #
    # 只在模块已在 sys.modules 里时才同步：没加载的模块在后续 import 时
    # 会自然拿到已 patch 过的 paths 值。
    try:
        import plugin_host
        _sync_module_copies(monkeypatch, plugin_host, {
            "_INSTALLED_DIR": os.path.join(data_dir, "installed_plugins"),
            "_DDIR": os.path.join(data_dir, "plugins"),
            "_ROOT": config_dir,
        })
    except Exception:
        pass

    def _cleanup():
        # 复位成 None（不是旧值）：set_dir(None) 才让 _dir() 回到
        # 「跟随 paths.DATA_DIR」的状态。
        store.set_dir(None)
        store.close_all()

    return _cleanup


def _sync_module_copies(monkeypatch, module, mapping):
    """把 module 上已有的同名属性改成新值（属性不存在就跳过）。"""
    for name, value in mapping.items():
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value, raising=False)
