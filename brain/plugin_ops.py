# -*- coding: utf-8 -*-
"""插件的安装 / 卸载 / 元数据 —— 管理面。运行时在 `plugin_host`。

装完调 `plugin_host.load()` 重扫，这是两个模块唯一的交点。

## 包形状

一个 zip **只接受一种形状**：

    xxx.zip
    ├── xxx.py                # 插件本体
    └──（可选）zxplugin.json  # 作者/版本等元信息

**只能有一个 .py**。塞多个能装上（取第一个），第二个被静默丢掉 ——
"装上了但少了一半"比直接拒绝难查，所以明确拒。

只收单文件是因为加载器扫的是 `plugins/*.py`，目录一律看不见。
**装得上但跑不起来是最坏的失败方式**：界面显示"已安装"，功能永远不出现。
要放开目录支持，得先让加载器支持扫目录（见 `docs/插件系统.md`）。
"""
import io
import json
import os
import re
import shutil
import time
import zipfile

try:
    import paths
    _PLUG_DIR = str(paths.PLUGINS_DIR)
except Exception:
    _PLUG_DIR = os.path.join(os.getcwd(), "plugins")

REGISTRY = os.path.join(_PLUG_DIR, "_registry.json")

# --- 安装的硬边界（改这些要同时改 docs/插件系统.md）---
MAX_ZIP_BYTES = 4 * 1024 * 1024# 压缩包上限
MAX_UNPACKED = 16 * 1024 * 1024    # 解开后总大小上限（防 zip 炸弹）
MAX_FILE_BYTES = 2 * 1024 * 1024   # 单文件上限
MAX_FILES = 64                     # 条目数上限
ALLOWED_EXT = (".py", ".json", ".md", ".txt")

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
        os.makedirs(_PLUG_DIR, exist_ok=True)
        with open(REGISTRY, "w", encoding="utf-8", newline="\n") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
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

        # 形状：要么全是 .py 平铺，要么全是同一个目录
        tops = {n.split("/")[0] for n in names}
        if len(tops) == 1 and "/" in names[0]:
            top = list(tops)[0]
            # 单文件插件
            if _PY_NAME_OK.match(top):
                return True, names, ""
            # 目录插件**目前装不了**。
            # 看着"支持一下"只是一行判断的事，但真去实现要动 plugin_host.load()
            # 的扫描逻辑 —— 那是聊天链路每轮都在走的地方。
            # 与其在这里放一个"装得上、跑不起来"的承诺，不如现在就说清楚。
            # 解锁条件：加载器支持扫目录（见 docs/插件系统.md 的 TODO）。
            if not any(n.lower().endswith("/__init__.py") for n in names):
                return False, [], ("目录包里必须有 __init__.py：%s" % top[:40])
            return False, [], (
                "%s 是目录形式，但加载器当前只认单文件 .py（plugin_host.load "
                "只扫 *.py），装了不会生效。请把插件压成单个 .py 再上传。" % top[:40])
        # 平铺：.py 走插件名校验，.json/.md/.txt 是附属文件（原样接受）
        for n in names:
            if "/" in n:
                return False, [], ("这个 zip 看起来是个目录包（%s）；"
                                   "加载器只认单文件 .py，请压成单个 .py 再上传。"
                                   % n[:40])
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


def install(zip_path, note=""):
    """装一个插件。返回 (ok, 结果字典)。

    顺序是刻意安排的 —— **先在临时目录里全部验完，最后才动plugins/**：
    一个坏包不该让已有的插件跟着遭殃（zip 炸弹、超限、语法错都发生在落盘前）。
    """
    ok, names, err = inspect_zip(zip_path)
    if not ok:
        return False, {"err": err}
    # inspect_zip 已保证 .py 排在第一个，但仍显式挑一次：
    # 下一个靠 names[0] 的人不该重新踩一遍"顺序取决于打包工具"这个坑。
    main_py = next((n for n in names if n.lower().endswith(".py")), "")
    if not main_py:
        return False, {"err": "包里没有 .py（这不是一个插件包）"}

    pname = _guess_plugin_name(names)
    if not re_name_ok_safe(pname):
        return False, {"err": "插件名不合法（只准英文/数字/下划线/短横线）：%s" % pname[:40]}

    tmp = os.path.join(_PLUG_DIR, "_tmp_install_%d" % int(time.time()))
    try:
        if os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        with zipfile.ZipFile(zip_path) as zf:
            for n in names:
                dest = os.path.join(tmp, *n.split("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(n) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        # 语法检查在临时目录做：不通过就地丢掉，plugins/ 还没被碰过
        bad = _syntax_check(tmp, names)
        if bad:
            return False, {"err": bad}
        man = _read_manifest(tmp, names)
        if man is None:
            return False, {"err": "zxplugin.json 必须是 UTF-8 JSON 对象"}
        manifest_err = validate_manifest(man, pname)
        if manifest_err:
            return False, {"err": manifest_err}
        # 正式落盘。inspect_zip 已经保证只有单文件一种形状（目录包被拒了），
        # 所以这里只处理单文件 —— 别留一条走不到的分支，
        # 它会在"以后放开目录支持"时悄悄生效，而那时早就没人记得它写于何时。
        dest = os.path.join(_PLUG_DIR, pname + ".py")
        if os.path.isfile(dest):
            os.remove(dest)
        shutil.copy2(os.path.join(tmp, main_py), dest)
        # 附属文件也落盘（zxplugin.json / 说明文档）。名字以 _ 开头 ——
        # 加载器跳过下划线开头的文件，所以它永远不会被当成另一个插件。
        extras = [n for n in names if not n.lower().endswith(".py")]
        extra_dst = []
        for n in extras:
            src = os.path.join(tmp, *n.split("/"))
            # 别写成 dst = "_" + os.path.basename(dst) —— 那样把绝对路径
            # 换成了相对路径，文件会落到**当前工作目录**（云端是大脑根目录），
            # 而这里 except: pass 会把它静默吞掉：安装报"成功"、文件却没了。
            # 这种"看起来成功"的失败最难查，宁可多写一次 basename。
            base = "_%s_%s" % (pname, os.path.basename(n))
            dst = os.path.join(_PLUG_DIR, base)
            try:
                shutil.copy2(src, dst)
                extra_dst.append(dst)
            except Exception as e:
                print("[插件] 附属文件 %s 没落盘：%s" % (base[:30], str(e)[:60]),
                      flush=True)
    except Exception as e:
        return False, {"err": "装的时候出错：%s" % str(e)[:120]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    real = man.get("name") or ""
    _set_meta(real or pname, {
        "installed_at": time.strftime("%Y-%m-%d %H:%M"),
        "author": str(man.get("author") or "")[:60],
        "version": str(man.get("version") or "")[:20],
        "desc": str(man.get("desc") or "")[:120],
        "source": note or "zip 上传",
        "package": pname,
    })
    return True, {"ok": True, "package": pname, "name": real or pname,
                  "manifest": man, "files": [dest] + extra_dst}


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
        # 必须在删文件**之前**读出 FEATURE：文件一删就再也读不到，
    # 而渠道开关的键（features_by_src.<src>.<FEATURE>）得靠它才能摘掉。
    feat = _feature_of(f)
    if os.path.isfile(f):
        os.remove(f)
        hit = f
    if os.path.isdir(d):          # 有目录就一起清，别留半个空壳
        shutil.rmtree(d, ignore_errors=True)
        hit = hit or d
    if not hit:
        return False, {"err": "没找到这个插件（可能已经被删了）"}
    # 附属文件（安装时落的 _<name>_xxx.json / md）跟着清，
    # 不然每装一次留一批，重装几轮就攒出一堆同名不同代的文件。
    try:
        for fn in os.listdir(_PLUG_DIR):
            if fn.startswith("_" + name + "_"):
                try:
                    os.remove(os.path.join(_PLUG_DIR, fn))
                except OSError:
                    pass
    except OSError:
        pass
    reg = read_registry()
    (reg.get("plugins") or {}).pop(name, None)
    write_registry(reg)
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
                shutil.rmtree(pd, ignore_errors=True)
                extra = "，数据也清了"
        except Exception:
            pass
    return True, {"ok": True, "removed": os.path.basename(hit), "msg": "已卸载" + extra}


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
