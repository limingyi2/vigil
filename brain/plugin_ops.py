# -*- coding: utf-8 -*-
"""插件的安装 / 卸载 / 元数据 —— 管理面。运行时在 `plugin_host`。

安装事务只负责静态校验和文件提交；提交成功返回 `restart_needed=true`，由受控重启完成动态加载。
安装器不会为了校验而 import 新插件，避免在事务中触发 `start()`、网络或文件副作用。

## 包形状

zip 接受两种形状：

    xxx.zip
    ├── xxx.py                # 插件本体
    └──（可选）zxplugin.json  # 作者/版本等元信息

单文件插件保留原格式。目录插件必须在单一插件目录内包含 `__init__.py`，
可以附带 `frontend.js`、`frontend.css` 和 Manifest；加载器按目录读取这些资源。
"""
import io
import json
import os
import re
import shutil
import time
import zipfile
import tempfile
import plugin_transaction as transaction
import restart_state

try:
    import paths
    _PLUG_DIR = str(paths.PLUGIN_INSTALL_DIR)
except Exception:
    _PLUG_DIR = os.path.join(os.getcwd(), "data", "installed_plugins")

REGISTRY = os.path.join(_PLUG_DIR, "_registry.json")

# --- 安装的硬边界（改这些要同时改 docs/插件系统.md）---
MAX_ZIP_BYTES = 4 * 1024 * 1024# 压缩包上限
MAX_UNPACKED = 16 * 1024 * 1024    # 解开后总大小上限（防 zip 炸弹）
MAX_FILE_BYTES = 2 * 1024 * 1024   # 单文件上限
MAX_FILES = 64                     # 条目数上限
ALLOWED_EXT = (".py", ".json", ".md", ".txt", ".js", ".css")

_PY_NAME_OK = re.compile(r"^[A-Za-z0-9_-]+\.py$")


# --- 元数据 ---

def _blank():
    return {"version": 1, "plugins": {}}


def read_registry():
    """读元数据。读不出来就当空的 —— 宁可没信息，不能装不了插件。"""
    try:
        with open(REGISTRY, encoding="utf-8") as fh:
            d = json.load(fh)
        if not isinstance(d, dict) or not isinstance(d.get("plugins"), dict):
            return _blank()
        return d
    except Exception:
        return _blank()


def write_registry(reg):
    try:
        transaction.atomic_bytes(REGISTRY, json.dumps(
            reg, ensure_ascii=False, indent=2).encode("utf-8"))
        return True
    except Exception:
        return False


def _meta_of(name):
    return (read_registry().get("plugins") or {}).get(name) or {}


def _set_meta(name, patch):
    reg = read_registry()
    plugins = reg.setdefault("plugins", {})
    cur = plugins.get(name) or {}
    cur.update(patch or {})
    plugins[name] = cur
    return write_registry(reg)


# --- 渠道策略（复用 features，不新造机制）---

def channels_of(feature_key, src_names):
    """这个插件此刻在哪些渠道开着。返回 {渠道: bool}。

    判据是 `features.all_features(src)` —— 也就是**运行时真正问的那个值**。
    面板显示的必须等于实际生效的，不然又会出现"界面显示开着、实际没生效"。
    """
    out = {}
    if not feature_key:
        # 不声明 FEATURE = 契约上是"永远开"，没有渠道差异可言
        return {s: True for s in src_names}
    try:
        import features
        for s in src_names:
            out[s] = bool(features.all_features(s).get(feature_key, True))
    except Exception:
        out = {s: True for s in src_names}
    return out


def _feature_of(py_path):
    """从一个插件 .py 里静态读出 FEATURE 键。读不到返回空串。

    为什么用正则而不 import：import 会真的执行插件代码（装卸这种管理动作
    不该有副作用），而且插件可能正在被另一个线程用着。
    只取 `FEATURE = "xxx"` 这种字面量，认不出来就返回空 ——
    大不了渠道开关的键留在配置里，那只是多一个没人用的条目，
    比猜错了把别人的开关摘掉安全得多。
    """
    if not os.path.isfile(py_path):
        directory_entry = os.path.join(
            os.path.dirname(py_path),
            os.path.splitext(os.path.basename(py_path))[0],
            "__init__.py",
        )
        if os.path.isfile(directory_entry):
            py_path = directory_entry
    try:
        with open(py_path, encoding="utf-8", errors="ignore") as fh:
            txt = fh.read()
    except Exception:
        return ""
    m = re.search(r'^\s*FEATURE\s*=\s*["\']([A-Za-z0-9_\-]{1,64})["\']',
                  txt, re.M)
    return m.group(1) if m else ""


def set_channels(feature_key, src_names, on_map):
    """按勾选写 features 段。**只写差异**，不动没勾到的渠道。

    写出的形状：
        勾了的渠道  -> features_by_src.<src>.<key> = true
        没勾的渠道  -> features_by_src.<src>.<key> = false
    为什么要显式写 false：只写 true 的话，插件装之前全局是 true，
    取消勾选就"没写=跟全局走=还开着"—— 界面上关了、实际没关。
    """
    if not feature_key:
        return False
    try:
        import features
        for s in src_names:
            features.set_features({feature_key: bool(on_map.get(s))}, src=s)
        return True
    except Exception:
        return False


# --- zip 形状校验 ---

class Bad(Exception):
    """安装被拒。message 直接给用户看，别写内部术语。"""


def _norm_entry(name):
    """把 zip 条目名收拾成能判定的形状。返回 (ok, 原因) / (名字, None) / None。"""
    n = str(name or "").replace("\\", "/").strip()
    if not n or n.endswith("/"):
        return None                     # 目录条目，忽略
    if n.startswith("/") or n.startswith("~"):
        return None, "zip 里不许有绝对路径：%s" % n[:60]
    if re.match(r"^[A-Za-z]:", n):
        return None, "zip 里不许有盘符路径：%s" % n[:60]
    if ".." in n.split("/"):
        return None, "zip 里不许用 .. 跳目录：%s" % n[:60]
    if ":" in n:                        # NTFS 的 ADS：evil.py:hidden
        return None, "文件名里不许有冒号：%s" % n[:60]
    # 顶层（要落成 plugins/ 下那个东西）的名字不能是 _ 或 . 开头 ——
    # 不是安全问题，是**装了不会生效**：加载器只收不以 "_" 开头的 .py，
    # 点开头的同理（被当隐藏文件跳过）。放进来只会让人以为装上了。
    #
    # 但**目录内部**的文件名随便用 _ 开头：__init__.py 是目录插件的
    # 必需文件，_helper.py 也是正常写法。规则只管顶层。
    if "/" not in n:
        base = os.path.basename(n)
        if base.startswith("_") or base.startswith("."):
            return None, ("文件名不能以 _ 或 . 开头（加载器会跳过，装了不生效）：%s"
                          % base[:40])
    else:
        segs = n.split("/")
        # 目录名本身也不能是隐藏/下划线（它会成为 plugins/ 下的一个入口）
        if segs[0].startswith("_") or segs[0].startswith("."):
            return None, "插件目录名不能以 _ 或 . 开头：%s" % segs[0][:40]
    return n


def inspect_zip(path):
    """验形状，返回 (ok, 条目列表, 错误原因)。不解压。

    全部判断都基于 zip 的原始条目名，所以调用方可以先看结果再决定要不要落盘。
    """
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return False, [], "读不到文件：%s" % e
    if size > MAX_ZIP_BYTES:
        return False, [], "压缩包 %.1f MB，超过上限 %d MB" % (
            size / 1048576.0, MAX_ZIP_BYTES // 1048576)

    try:
        zf = zipfile.ZipFile(path)
    except Exception as e:
        return False, [], "不是有效的 zip：%s" % str(e)[:80]

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            return False, [], "包里有 %d 个文件，超过上限 %d" % (len(infos), MAX_FILES)
        total = 0
        for inf in infos:
            if inf.flag_bits & 0x1:
                return False, [], "不支持加密的包"
            total += int(inf.file_size or 0)
        if total > MAX_UNPACKED:
            return False, [], "解开后 %.1f MB，超过上限 %d MB（防 zip 炸弹）" % (
                total / 1048576.0, MAX_UNPACKED // 1048576)

        names = []
        for inf in infos:
            n = _norm_entry(inf.filename)
            if isinstance(n, tuple):
                return False, [], n[1]
            if n is None:
                continue
            ext = os.path.splitext(n)[1].lower()
            if ext not in ALLOWED_EXT:
                return False, [], "不许的文件类型 %s（只准 %s）" % (
                    ext or "(无扩展名)", " ".join(ALLOWED_EXT))
            if inf.file_size > MAX_FILE_BYTES:
                return False, [], "%s 有 %.1f MB，单文件上限 %d MB" % (
                    n[:40], inf.file_size / 1048576.0, MAX_FILE_BYTES // 1048576)
            names.append(n)

        if not names:
            return False, [], "包里是空的"

        # 目录插件只能有一层包目录，入口必须是 __init__.py；这和
        # plugin_host.load() 的扫描及 frontend.js/css 的读取布局一致。
        if any("/" in n for n in names):
            tops = {n.split("/", 1)[0] for n in names}
            if len(tops) != 1:
                return False, [], "目录插件只能包含一个顶层插件目录"
            top = next(iter(tops))
            reserved = top.split(".", 1)[0].rstrip(" .").upper() in {
                "CON", "PRN", "AUX", "NUL",
                *("COM%d" % i for i in range(1, 10)),
                *("LPT%d" % i for i in range(1, 10)),
            }
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", top) or reserved:
                return False, [], "插件目录名不合法：%s" % top[:40]
            children = [n.split("/", 1)[1] for n in names]
            if any("/" in n for n in children):
                return False, [], "目录插件不支持嵌套子目录"
            py_files = [n for n in children if n.lower().endswith(".py")]
            if py_files != ["__init__.py"]:
                return False, [], "目录插件必须且只能包含 __init__.py 作为 Python 入口"
            if len({n.casefold() for n in names}) != len(names):
                return False, [], "包里存在重复或仅大小写不同的文件名"
            return True, names, ""
        # 平铺：.py 走插件名校验，.json/.md/.txt 是附属文件（原样接受）
        for n in names:
            base = os.path.basename(n)
            if base.lower().endswith(".py") and not _PY_NAME_OK.match(base):
                return False, [], "插件文件名不合法（只准英文/数字/下划线/短横线）：%s" % base[:40]
        # 多个 .py 一律拒。装是能装上的（取第一个），但第二个会被
        # 静默丢掉 —— 那种"装上了但少了一半"比直接拒绝难查得多。
        # 附属文件（json/md/txt）不算。
        pys = [n for n in names if n.lower().endswith(".py")]
        if len(pys) > 1:
            return False, [], (
                "包里塞了 %d 个 .py，插件只能是单文件。请只留插件本身"
                "（多余的依赖文件暂不支持）：%s" % (len(pys),
                                          "、".join(pys[:4])))
        if not pys:
            return False, [], "包里没有 .py（这不是一个插件包）"
        #名字按**后缀**定，不靠 names 的顺序。
        # zip 里的条目顺序由打包工具决定（先写 json 还是先写 py 都能打包成功），
        # 而 install 一度用 names[0] 当插件本体 —— 碰上"json 在前"的包，
        # 就会把 zxplugin.json 当插件装成 zxplugin.py，语法检查照样通过
        # （JSON 本身就是合法 Python 字面量），用户看到"安装成功"，
        # 实际装进去一个不加载的空壳。**判据必须是语义（后缀），不是位置。**
        if len({n.casefold() for n in names}) != len(names):
            return False, [], "包里存在重复或仅大小写不同的文件名"
        names = pys + [n for n in names if n not in pys]
        return True, names, ""


def _guess_plugin_name(names):
    """从条目名猜插件名。目录插件取目录名，平铺取 .py 的文件名。"""
    top = names[0].split("/")[0]
    if "/" in names[0]:
        return top
    return os.path.splitext(top)[0]


def _find_manifest(names):
    """找 zxplugin.json（可选）。没有就用 .py 里的 NAME/DESC。"""
    for n in names:
        if n.lower().endswith("zxplugin.json"):
            return n
    return ""


@transaction.serialized
def install(zip_path, note="", replace=False):
    """Validate and commit files transactionally.

    ``replace`` is reserved for the unified manager's update path. The legacy
    upload path keeps duplicate installs rejected, while updates use the same
    protected backup/rollback transaction instead of deleting the old plugin.
    """
    ok, names, err = inspect_zip(zip_path)
    if not ok:
        return False, {"err": err, "static_validated": False}
    pname = _guess_plugin_name(names)
    stage = None
    keep = False
    try:
        transaction.reject_links(_PLUG_DIR)
        os.makedirs(_PLUG_DIR, exist_ok=True)
        stage = tempfile.mkdtemp(prefix="_tmp_install_", dir=_PLUG_DIR)
        source = os.path.join(stage, pname)
        os.mkdir(source)
        is_directory = any("/" in name for name in names)
        with zipfile.ZipFile(zip_path) as zf:
            for name in names:
                relative = name.split("/", 1)[1] if is_directory else name
                dst_path = os.path.join(source, *relative.split("/"))
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                with zf.open(name) as src, open(dst_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        from plugin_structure import check_plugin_directory
        report = check_plugin_directory(source)
        if not report["ok"]:
            return False, {"err": "; ".join(e["message"] for e in report["errors"]),
                           "static_validated": False}
        if report["layout"] == "directory":
            if os.path.lexists(os.path.join(_PLUG_DIR, pname + ".py")) and not replace:
                raise ValueError("同名单文件插件已存在，拒绝跨布局覆盖")
            replacements = {pname: source}
            old = [pname]
        else:
            if os.path.lexists(os.path.join(_PLUG_DIR, pname)) and not replace:
                raise ValueError("同名目录插件已存在，拒绝跨布局覆盖")
            main = report["entry"]
            replacements = {pname + ".py": os.path.join(source, main)}
            for name in names:
                if name != main:
                    replacements["_%s_%s" % (pname, name)] = os.path.join(source, name)
            old = [n for n in os.listdir(_PLUG_DIR)
                   if n == pname + ".py" or n.startswith("_" + pname + "_")]
        ok, result = _commit_install(stage, pname, replacements, old,
                                     report["manifest"], note)
        keep = result.get("rollback") == "failed"
        if ok:
            result.update(files=[os.path.join(_PLUG_DIR, n) for n in replacements],
                          layout=report["layout"],
                          frontend_assets=any(
                              n.lower().endswith(("/frontend.js", "/frontend.css"))
                              for n in names))
        return ok, result
    except Exception as exc:
        return False, {"err": str(exc), "static_validated": False}
    finally:
        if stage and not keep:
            shutil.rmtree(stage, ignore_errors=True)


def _commit_install(stage, name, replacements, old, manifest, note):
    # Read registry strictly: never silently replace a malformed user registry.
    raw = transaction.snapshot(REGISTRY)
    reg = json.loads(raw.decode("utf-8")) if raw is not None else _blank()
    if not isinstance(reg, dict) or not isinstance(reg.get("plugins"), dict):
        raise ValueError("注册表格式错误，拒绝覆盖")
    meta = dict(reg["plugins"].get(name) or {})
    meta.update({"installed_at": time.strftime("%Y-%m-%d %H:%M"),
                 "author": manifest.get("author") or "",
                 "version": manifest.get("version") or "",
                 "desc": manifest.get("desc") or "",
                 "source": note or "zip 上传", "package": name})
    reg["plugins"][name] = meta
    ok, result = transaction.commit(_PLUG_DIR, stage, replacements, old,
                                    REGISTRY, reg, write_registry)
    result.update(static_validated=True, files_installed=ok,
                  dynamic_loaded=False, restart_needed=ok,
                  activation="restart_required" if ok else "unchanged")
    if ok:
        result.update(ok=True, package=name, name=name, manifest=manifest)
        # 落一个持久的"待重启"：安装只提交文件、不 import，所以磁盘状态和
        # 运行状态从这一刻起就不一致，要一直提示到大脑重启为止。
        try:
            ver = str((manifest or {}).get("version") or "")
            restart_state.note_change(
                "plugin", _PLUG_DIR,
                "安装插件 %s%s" % (name, (" v" + ver) if ver else ""))
        except Exception:
            pass
    return ok, result


@transaction.serialized
def install_market_directory(repo, relative, name, note="market"):
    """Static market validation plus the same filesystem transaction as zip."""
    from plugin_structure import check_plugin_directory, _filename_issue
    stage = None
    keep = False
    try:
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name)
                or _filename_issue(name)):
            raise ValueError("市场插件名称不合法或为 Windows 保留名称")
        if (not isinstance(relative, str) or not relative or
                relative.startswith(("/", "\\", "~")) or
                ":" in relative or "\\" in relative or
                any(p in ("", ".", "..") for p in relative.split("/"))):
            raise ValueError("市场源路径必须是仓库内的相对目录，禁止路径穿越")
        repo = os.path.abspath(repo)
        src = os.path.abspath(os.path.join(repo, relative))
        if os.path.commonpath([repo, src]) != repo or src == repo:
            raise ValueError("市场源目录逃逸")
        transaction.reject_links(repo)
        transaction.reject_links(src)
        transaction.reject_links(_PLUG_DIR)
        if not os.path.isdir(src):
            raise ValueError("插件源目录不存在")
        report = check_plugin_directory(src)
        if not report["ok"] or report["layout"] != "directory":
            raise ValueError("市场目录结构无效：" + "; ".join(
                e["message"] for e in report["errors"]))
        os.makedirs(_PLUG_DIR, exist_ok=True)
        stage = tempfile.mkdtemp(prefix="_tmp_install_", dir=_PLUG_DIR)
        staged = os.path.join(stage, name)
        # Generated interpreter caches are not source and can otherwise make
        # an otherwise valid local market package fail staged validation.
        shutil.copytree(src, staged, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
        # Validate the immutable staging copy under its target identity too.
        report = check_plugin_directory(staged)
        if not report["ok"]:
            raise ValueError("暂存目录结构无效：" + "; ".join(
                e["message"] for e in report["errors"]))
        if os.path.lexists(os.path.join(_PLUG_DIR, name + ".py")):
            raise ValueError("同名单文件插件已存在，拒绝跨布局覆盖")
        # Directory rename over a non-empty directory requires first moving
        # the old directory. commit handles this with a protected backup.
        ok, result = _commit_install(stage, name, {name: staged}, [name],
                                     report["manifest"], note)
        keep = result.get("rollback") == "failed"
        return ok, result
    except Exception as exc:
        return False, {"err": str(exc), "static_validated": False}
    finally:
        if stage and not keep:
            shutil.rmtree(stage, ignore_errors=True)


def re_name_ok_safe(n):
    try:
        import plugin_host
        return bool(plugin_host.re_name_ok(n))
    except Exception:
        return bool(n) and all(c.isalnum() or c in "_-" for c in str(n))


def _syntax_check(tmp_root, names):
    """py_compile 每一个 .py，返回第一个错误（没有则 None）。

    🔴 cfile **不能写 os.devnull**。Windows 上 devnull 是"nul"这个设备名，
    py_compile 拿它当输出文件会直接抛
    「nul is a non-regular file」—— 于是**每个包都装不进去**，
    而且错误信息里完全看不出是这个原因（第一次跑就被这个坑拦住过）。
    用临时文件，验完删。
    """
    import py_compile
    import tempfile
    for n in names:
        if not n.lower().endswith(".py"):
            continue
        p = os.path.join(tmp_root, *n.split("/"))
        cf = os.path.join(tempfile.gettempdir(), "_zxpc_%d.pyc" % os.getpid())
        try:
            py_compile.compile(p, doraise=True, cfile=cf)
        except Exception as e:
            where = n
            msg = str(e).replace(tmp_root, "包内").replace(cf, "缓存")
            return "%s 语法有问题：%s" % (where[:40], msg[:160])
        finally:
            try:
                if os.path.isfile(cf):
                    os.remove(cf)
            except OSError:
                pass
    return ""


def _read_manifest(tmp_root, names):
    """读 zxplugin.json（可选）。文件存在但格式错误时返回 None。"""
    p = _find_manifest(names)
    if not p:
        return {}
    try:
        with open(os.path.join(tmp_root, *p.split("/")), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def manifest_from_zip(zip_path):
    """Read a package manifest without importing or extracting plugin code."""
    ok, names, err = inspect_zip(zip_path)
    if not ok:
        return None, err
    entry = _find_manifest(names)
    if not entry:
        return {}, ""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            value = json.loads(archive.read(entry).decode("utf-8"))
        return (value, "") if isinstance(value, dict) else (None, "zxplugin.json 必须是 JSON 对象")
    except Exception as exc:
        return None, "zxplugin.json 无法读取：%s" % str(exc)[:120]


_MANIFEST_TEXT_LIMITS = {
    "name": 64,
    "author": 60,
    "version": 32,
    "desc": 120,
    "license": 80,
    "homepage": 240,
    "repository": 240,
}


def validate_manifest(manifest, package_name=""):
    """Validate optional ``zxplugin.json`` metadata without importing plugin code.

    The manifest is descriptive metadata, not a permission declaration. Unknown
    keys remain allowed for forward compatibility, while known fields get stable
    scalar and length checks. A declared name must match the install package so
    registry identity cannot silently diverge from the file being installed.
    Returns a user-facing error string or an empty string when valid.
    """
    if manifest is None:
        return "zxplugin.json 必须是 JSON 对象"
    if not isinstance(manifest, dict):
        return "zxplugin.json 必须是 JSON 对象"
    for key, limit in _MANIFEST_TEXT_LIMITS.items():
        if key not in manifest or manifest[key] is None:
            continue
        value = manifest[key]
        if not isinstance(value, str):
            return "zxplugin.json 的 %s 必须是字符串" % key
        stripped = value.strip()
        if key == "name" and value != stripped:
            return "zxplugin.json 的 name 不能包含首尾空白"
        value = stripped
        if len(value) > limit:
            return "zxplugin.json 的 %s 超过 %d 个字符" % (key, limit)
        if key == "name" and value:
            if not re_name_ok_safe(value):
                return "zxplugin.json 的 name 只能用英文/数字/下划线/短横线"
            if package_name and value != package_name:
                return "zxplugin.json 的 name 必须与插件文件名一致：%s" % package_name
    return ""


def _has_start_hook(name):
    """这个插件是不是声明了 `start` 钩子 —— 也就是装卸后要重启大脑才真正生效。

    必须在文件被删掉**之前**问。`plugin_host` 的清单是从 plugins/ 现扫的，
    文件一没它就再也问不出来；而"卸完了后台线程还在跑、要重启才停"这个
    信息只有这一刻拿得到。错过这一刻，界面就永远只会说"已卸载"，
    用户以为线程也跟着停了 —— 那是骗人。
    """
    try:
        import plugin_host as _ph
        p = (_ph.all_plugins() or {}).get(str(name))
        return bool(p is not None and callable(p.hook("start")))
    except Exception:
        # 问不出来时按"需要重启"处理：多提示一次重启的代价，
        # 远小于漏提示一次（用户以为停了，实际还在跑）。
        return True


@transaction.serialized
def uninstall(name, purge_data=False):
    """卸一个插件。返回 (ok, 信息)。

    删的是 `plugins/<name>.py`（以及一并存在的同名目录，
    那是早期允许过目录包时留下的）；顺带清元数据。
    插件自己的数据目录（data/plugins/<name>/）默认留着 —— 那里面可能有
    它的配置和状态，误删不可恢复，要删得显式 purge_data。
    """
    name = str(name or "").strip()
    if not name or not re_name_ok_safe(name):
        return False, {"err": "插件名不合法"}
    hit = ""
    f = os.path.join(_PLUG_DIR, name + ".py")
    d = os.path.join(_PLUG_DIR, name)
    # 删文件之前先记下它带不带后台线程（上面 _has_start_hook 的注释解释了
    # 为什么必须赶在删除前）。卸载的返回里要带这个信息，否则前端
    # 只会显示"已卸载"，而那个 start() 起的线程还在跑。
    had_bg = _has_start_hook(name)
    # 必须在删文件**之前**读出 FEATURE：文件一删就再也读不到，
    # 而渠道开关的键（features_by_src.<src>.<FEATURE>）得靠它才能摘掉。
    feat = _feature_of(f)
    # Keep a private snapshot until both file removal and registry persistence
    # succeed. The previous order could delete code, ignore a failed registry
    # write, and report success while status() still saw a registry entry.
    owned = []
    for path in (f, d):
        if os.path.isfile(path) or os.path.isdir(path):
            owned.append(path)
    try:
        for fn in os.listdir(_PLUG_DIR):
            if fn.startswith("_" + name + "_"):
                path = os.path.join(_PLUG_DIR, fn)
                if os.path.isfile(path) or os.path.isdir(path):
                    owned.append(path)
    except OSError:
        pass
    if not owned:
        return False, {"err": "没找到这个插件（可能已经被删了）"}

    stage = tempfile.mkdtemp(prefix=".uninstall-%s-" % name, dir=_PLUG_DIR)
    snapshots = {}
    try:
        for path in owned:
            staged = os.path.join(stage, os.path.basename(path))
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.copytree(path, staged, symlinks=True)
            else:
                shutil.copy2(path, staged, follow_symlinks=False)
            snapshots[path] = staged
        for path in owned:
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except Exception as remove_exc:
                if path == d:
                    raise OSError("插件目录清理失败：%s" % str(remove_exc)[:100])
                raise
            if os.path.lexists(path):
                raise OSError("plugin path remains after removal")

        reg = read_registry()
        reg.setdefault("plugins", {}).pop(name, None)
        if not write_registry(reg):
            raise OSError("插件注册表写入失败")
    except Exception as exc:
        restore_errors = []
        for path, staged in snapshots.items():
            if not os.path.lexists(staged):
                continue
            try:
                if os.path.lexists(path):
                    if os.path.isdir(path) and not os.path.islink(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
                if os.path.isdir(staged) and not os.path.islink(staged):
                    shutil.copytree(staged, path, symlinks=True)
                else:
                    shutil.copy2(staged, path, follow_symlinks=False)
            except Exception as restore_exc:
                restore_errors.append("%s: %s" % (os.path.basename(path), str(restore_exc)[:100]))
        shutil.rmtree(stage, ignore_errors=True)
        message = "卸载失败，插件文件和注册表已恢复：%s" % str(exc)[:140]
        if restore_errors:
            message += "；恢复失败：" + "; ".join(restore_errors)
        return False, {"err": message, "state": "error"}

    hit = owned[0]
    shutil.rmtree(stage, ignore_errors=True)
    extra = ""
    # 渠道开关的键一起摘掉。
    # 不摘的话：插件没了，features_by_src.<src>.<FEATURE> 还在，
    # 而 features.on(key, src) 只看这个键、不看插件装没装—— 于是配置里
    # 挂着一个谁也关不掉的"功能"，功能开关页还会列出一个不存在的条目。
    # 每装卸一轮测试就多留一份，配置很快就攒出一堆说不清是什么的键。
    if feat:
        try:
            import features as _ft
            n = _ft.drop(feat)
            if n:
                extra += "，渠道开关也清了（%d 处）" % n
        except Exception as e:
            print("[插件] 渠道开关没清掉：%s" % str(e)[:60], flush=True)
    if purge_data:
        try:
            import paths
            pd = os.path.join(str(paths.DATA_DIR), "plugins", name)
            if os.path.isdir(pd):
                shutil.rmtree(pd)
                if os.path.exists(pd):
                    raise OSError("plugin data directory remains after removal")
                extra += "，插件数据已清理"
        except Exception as e:
            # The plugin code is already uninstalled, but user data must never
            # be reported as removed unless the purge actually completed.
            extra += "，插件数据清理未完成，请检查残留数据"
            print("[插件] 数据目录清理失败：%s" % type(e).__name__, flush=True)
    # 持久记一笔"待重启"。卸载之后磁盘与运行态就不一致了：文件没了，
    # 但已经 import 的那份（连同它 start() 起的线程）还在内存里。
    # 这个不一致要一直提示到用户重启为止 —— 只靠 toast 说一次等于没说。
    try:
        restart_state.note_change(
            "plugin", _PLUG_DIR,
            "卸载插件 %s%s" % (name, "（带后台线程）" if had_bg else ""))
    except Exception:
        pass
    return True, {"ok": True, "removed": os.path.basename(hit),
                  "msg": "已卸载" + extra,
                  "restart_needed": bool(had_bg)}


# --- 给面板的完整视图 ---

def overview(src_names):
    """一次给全：装了哪些、每个的能力/开关/渠道状态/元信息。

    面板只要一个接口就能画完整个页面 —— 拆成四五个接口的话，
    页面得等好几轮才能填满，切换渠道时还要重新拉一遍。
    """
    try:
        import plugin_host as ph
        listing = ph.listing()
    except Exception:
        listing = []
    reg = (read_registry().get("plugins") or {})
    out = []
    for p in listing:
        name = p.get("name") or ""
        feat = str(p.get("feature") or "")
        meta = reg.get(name) or {}
        out.append({
            "name": name,
            "desc": p.get("desc") or "",
            "feature": feat,
            "tools": p.get("tools") or [],
            "routes": p.get("routes") or [],
            "hooks": p.get("hooks") or [],
            "file": p.get("file") or "",
            "err": p.get("err") or "",
            "channels": channels_of(feat, src_names),
            # 不声明 FEATURE = 契约上永远开，界面上要说清不能按渠道关
            "channelable": bool(feat),
            "meta": meta,
        })
    return out
