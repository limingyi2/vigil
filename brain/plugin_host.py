# -*- coding: utf-8 -*-
"""能力加载器：把 brain/core_features/ 下的核心能力适配器与 data/installed_plugins/ 下的用户扩展接进运行链路。

**为什么有这个**：以前加一个功能（比如闹钟提醒）要新建 alarm.py、改 reply.py、
改 server.py、改 persona_store 的提示词，四处都动 —— 改多了 reply.py 就成一堆
if-else，改一个功能还可能弄坏别的。插件化之后，核心只认下面这几个钩子，
核心能力由 core_features/ 提供；可选扩展必须安装到 data/installed_plugins/，不从源码仓库目录直接加载。

## 一个插件文件长什么样

    NAME = "reminder"          # 必填，插件名（英文，代码里引用它）
    DESC = "闹钟/提醒"          # 必填，给人看的
    FEATURE = "reminder"       # 选填，声明后 App 设置页自动多一个开关

    def prompt(ctx): ...            # 选填 → 拼进 system：告诉她"你有这个本事"
    def on_turn_in(ctx): ...        # 选填 → 他发来消息时，返回要注入 system 的提示
    def on_reply(ctx, text): ...    # 选填 → 回复加工完（剥标记/生图/语音之后）
    def start(brain): ...           # 选填 → 自己的后台线程，进程启动时调一次
    TOOLS = {"名字": 函数}          # 选填 → 模型可以用 [tool:名字:参数] 调它
    TOOL_HELP = "..."               # 选填 → 教模型怎么用，随 tools 一起进提示
    ROUTES = [("POST", "/api/plugin/xxx", 处理函数)]   # 选填 → 自己的 HTTP 接口

钩子都是可选的，写哪个算哪个。返回值约定：
- prompt / on_turn_in 返回字符串（或 None），拼进给模型的 system
- on_reply 必须返回字符串；不想动就原样返回，**别返回 None**

## 三条纪律

1. **插件坏了不能拖累她**：每个钩子都单独 try/except，炸了只打印一行，
   on_reply 出错就返回原文。插件进程内隔离不了 import 副作用，所以插件里
   禁止在模块顶层做网络请求 —— 要用就在钩子里调。
2. **开关是黑名单式的**：FEATURE 声明了就受 features 段管（默认开，
   App 能关）；没声明就永远开。别自己在钩子里读 config 判开关，那等于
   把白名单又写死一遍。
3. **插件不认识就是不认识**：run_tool 查不到名字就返回 None，她照实说查不到。
   模型编出来的工具名由 tools.TOOL_TAG_ANY_RE 兜底扫掉，不会漏给用户。
"""
import contextlib
import contextvars
import hashlib
import importlib
import importlib.util
import os
import re
import sys
import threading
import traceback

try:
    from paths import CORE_FEATURES_DIR, PLUGIN_INSTALL_DIR, PLUGIN_DATA_DIR, CONFIG_DIR
    _DIR = str(CORE_FEATURES_DIR)
    _INSTALLED_DIR = str(PLUGIN_INSTALL_DIR)
    _DDIR = str(PLUGIN_DATA_DIR)
    _ROOT = str(CONFIG_DIR)
except Exception:
    _DIR = r"C:\vigil\core_features"
    _INSTALLED_DIR = r"C:\vigil\data\installed_plugins"
    _DDIR = r"C:\vigil\data\plugins"
    _ROOT = r"C:\vigil\config"

# 钩子名 → 必需参数个数。用于参数个数不对时打一行日志而不是抛 TypeError
# 到调用方栈里（插件作者看到的应该是"你这里少传了参数"，不是她的调用栈）。
_ARITY = {"prompt": 1, "on_turn_in": 1, "on_reply": 2, "start": 1}

_registry = {}        # name -> Plugin
_lock = threading.RLock()
_loaded = False
_runtime_brain = None
_started = set()


def _log(tag, msg):
    print("[插件] %s：%s" % (tag, msg), flush=True)


# --- 一轮对话的上下文：插件能看见什么、又能改什么 ---

class Turn(object):
    """插件拿到的一轮上下文。

    只放"插件真的可能需要"的字段 —— 塞满整个 Brain 等于把封装拆了，
    插件就会开始直接改她的内部状态，出问题没法查。
    """

    def __init__(self, text="", src="pc", api_config=None, brain=None):
        self.text = str(text or "")          # 他这一轮说的话（原值，含插件前处理）
        self.src = str(src or "pc")         # 渠道：app / wechat
        self.api_config = api_config or {}   # 共享的那份 API_CFG
        self.brain = brain# 大脑对象，只读用（问问题、查记忆）
        self.meta = {}                       # 插件自己的中途数据，同轮内互相传
        self.vetoed = False                  # 有插件否决了这一轮（见 veto）

    def veto(self, why=""):
        """**否决这一轮**：大脑根本不会被问，她这一轮彻底不说话。

        ## 为什么不靠返回值表达

        最早的方案是「on_turn_in 返回空串 = 否决」。看着更省事，实际立刻炸：
        `reminder` 插件"没有提醒要注入"时返回的也是空串 —— **同一个值两种意思**。
        两种情况在宿主层长得一模一样，于是：

        - 所有插件都放行 → `"\n".join([])` = `""` → 被当成否决 → **她一句话都说不出来**
        - reminder 没东西注入 → `""` → 也被当成否决

        表现是"她突然完全不回话了"，而每个插件单独看都正常。
        空串在两种语义下**无法区分**，所以改成显式声明：插件想否决就调
        `ctx.veto(原因)`，返回值只负责"要注入的提示文本"。

        返回值于是恢复成只有一种含义：非空字符串 = 注入 system 的提示。
        """
        self.vetoed = True
        self.veto_reason = str(why or "")[:120]
        _log("turn", "这一轮被否决：%s" % (self.veto_reason or "未说明原因"))
        return None

    def say(self, text):
        """让插件能主动开口（跟 proactive 那条路一样，App轮询会弹通知）。"""
        try:
            from proactive import _cloud_append_assistant
            _cloud_append_assistant(str(text or ""))
            return True
        except Exception:
            return False

    def ask(self, prompt, proactive=True):
        """让插件借她的嘴说一句（记忆/生活/摘要都会正常走一遍）。"""
        try:
            if self.brain is None:
                return ""
            ans, _mode = self.brain.chat(str(prompt or ""),
                                         proactive=proactive, log=False)
            return str(ans or "").strip()
        except Exception:
            return ""

    def data_dir(self):
        """插件自己的持久化目录（data/plugins/<name>/），不存在就建好。"""
        d = os.path.join(_DDIR, self._owner or "_shared")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        return d

    # data_dir 要知道是谁在问，但 Turn 是所有插件共用的对象；
    # 派发时临时挂一个 _owner，比每个钩子多传一个参数干净
    _owner = ""


# --- 插件对象 ---

class Plugin(object):
    def __init__(self, mod, path):
        self.name = str(getattr(mod, "NAME", "") or "").strip()
        self.desc = str(getattr(mod, "DESC", "") or "").strip()
        self.feature = str(getattr(mod, "FEATURE", "") or "").strip()
        self.mod = mod
        self.path = path
        raw_tools = getattr(mod, "TOOLS", {}) or {}
        self.generic_tool_names = set()
        self.generic_tool_dispatch = getattr(mod, "call_tool", None)
        if isinstance(raw_tools, dict):
            self.tools = dict(raw_tools)
        elif isinstance(raw_tools, (list, tuple, set)):
            # Compatibility for Creator drafts produced by the earlier list-based
            # contract: TOOLS = ["name", ...] + call_tool(name, args).
            names = list(raw_tools)
            if (not names or any(not isinstance(n, str) or not n.strip() for n in names)
                    or len(names) != len(set(names))):
                raise ValueError("TOOLS 列表必须包含不重复的非空工具名")
            if not callable(self.generic_tool_dispatch):
                raise ValueError("TOOLS 为名称列表时必须提供 call_tool(name, args)")
            self.generic_tool_names = {n.strip() for n in names}
            self.tools = {n: None for n in self.generic_tool_names}
        else:
            raise ValueError("TOOLS 必须是工具名到函数的字典，或配套 call_tool 的工具名列表")
        self.tool_help = str(getattr(mod, "TOOL_HELP", "") or "").strip()
        # 可选的**动态**说明提供者。工具清单会随安装变（面板装一个技能，
        # 宿主上就多几个工具），而 `TOOL_HELP` 是模块级字符串、装载时读一次
        # 就固化了 —— 于是"新装的技能没进提示词，模型不知道它存在"。
        # 插件提供 TOOL_HELP_FN(native=False) 就能每轮现取（一般自己带缓存）。
        self.tool_help_fn = getattr(mod, "TOOL_HELP_FN", None)
        self.routes = list(getattr(mod, "ROUTES", []) or [])
        self.err = ""
        # 前端注入资源（可选）：目录型插件同目录下的 frontend.js / frontend.css
        self.frontend_js = ""
        self.frontend_css = ""
        plugin_dir = os.path.dirname(path)
        for _fname, _attr in [("frontend.js", "frontend_js"),
                               ("frontend.css", "frontend_css")]:
            _fpath = os.path.join(plugin_dir, _fname)
            try:
                if os.path.isfile(_fpath):
                    with open(_fpath, "r", encoding="utf-8") as _f:
                        setattr(self, _attr, _f.read())
            except Exception:
                pass  # 读不到就算了，不影响插件加载

    def hook(self, name):
        """取钩子函数，没有就返回 None。"""
        return getattr(self.mod, name, None)

    def info(self):
        """给 /api/plugins 用的自述。"""
        return {
            "name": self.name,
            "desc": self.desc,
            "feature": self.feature,
            "tools": sorted(self.tools.keys()),
            "routes": [r[1] for r in self.routes if len(r) >= 2],
            "hooks": sorted(n for n in _ARITY if callable(self.hook(n))),
            "file": os.path.basename(self.path),
            "err": self.err,
        }


# --- 加载 ---

def _load_one(path):
    """把一个 .py 当插件模块导进来。返回 Plugin 或 None（失败只打一行）。

    支持两种形态：
    - 单文件插件：alarm.py → 模块名 alarm
    - 目录型插件：wallpaper/__init__.py → 模块名 wallpaper（用上级目录名）
    """
    # 目录型插件：path 是 xxx/__init__.py，用上级目录名作为插件名
    _parent = os.path.basename(os.path.dirname(path))
    if os.path.basename(path) == "__init__.py" and _parent:
        name0 = _parent
    else:
        name0 = os.path.splitext(os.path.basename(path))[0]
    try:
        spec = importlib.util.spec_from_file_location("zxplug_" + name0, path)
        if spec is None or spec.loader is None:
            raise ImportError("拿不到模块规格")
        mod = importlib.util.module_from_spec(spec)
        # 放进 sys.modules 再 exec：插件内部 import 自己或被别的插件 import 时才找得到
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        p = Plugin(mod, path)
        if not p.name:
            raise ValueError("模块里没写 NAME")
        if not re_name_ok(p.name):
            raise ValueError("NAME 只能用英文/数字/下划线/短横线：%r" % p.name)
        return p
    except Exception as e:
        _log(os.path.basename(path), "加载失败，已跳过：%s" % str(e)[:160])
        return None


def re_name_ok(n):
    """插件名的合法字符：只准字母数字下划线短横线。

    名字会进 features 段（config.json 的键）和提示词，白名单收到最紧 ——
    它不是给模型用的标识符，是给人看的开关名。
    """
    return bool(n) and all(c.isalnum() or c in "_-" for c in n)


def _scan_plugin_directory(directory):
    """Read plugin source files from one directory without mixing it with user installs."""
    found = {}
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return found
    for fn in names:
        fpath = os.path.join(directory, fn)
        # Directory plugin: name/__init__.py, with optional frontend resources.
        if os.path.isdir(fpath):
            init_file = os.path.join(fpath, "__init__.py")
            if os.path.isfile(init_file):
                plugin = _load_one(init_file)
                if plugin:
                    found[plugin.name] = plugin
            continue
        # Single-file plugin.
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        plugin = _load_one(fpath)
        if plugin:
            found[plugin.name] = plugin
    return found


def _plugin_entry_path(name):
    """Find one core-feature or installed-plugin entry without importing unrelated modules."""
    name = str(name or "").strip()
    for directory in (_INSTALLED_DIR, _DIR):
        if not directory:
            continue
        package_init = os.path.join(directory, name, "__init__.py")
        if os.path.isfile(package_init):
            return package_init
        module_file = os.path.join(directory, name + ".py")
        if os.path.isfile(module_file):
            return module_file
    return ""


def load(force=False, only=None):
    """Load core capability adapters first, then user-installed extensions as overrides.

    Optional extensions are read only from data/installed_plugins. The tracked
    brain/plugins source tree is never scanned by the runtime or included in releases.
    """
    global _loaded
    with _lock:
        if only:
            name = str(only).strip()
            _started.discard(name)
            _registry.pop(name, None)
            entry = _plugin_entry_path(name)
            if entry:
                plugin = _load_one(entry)
                if plugin and plugin.name == name:
                    _registry[name] = plugin
                elif plugin:
                    _log(name, "插件 NAME 与目标名称不一致，已拒绝定向重载")
            _reindex_tools()
            _loaded = True
            _sync_features()
            return dict(_registry)
        found = _scan_plugin_directory(_DIR)
        if os.path.abspath(_INSTALLED_DIR) != os.path.abspath(_DIR):
            # Installed plugins override a same-named core plugin intentionally.
            found.update(_scan_plugin_directory(_INSTALLED_DIR))
        # Remove stale module objects from sys.modules after hot reload.
        keep = set("zxplug_" + plugin.name for plugin in found.values())
        for key in [key for key in sys.modules if key.startswith("zxplug_")]:
            if key not in keep:
                sys.modules.pop(key, None)
        _registry.clear()
        _registry.update(found)
        # A forced scan creates fresh Plugin/module objects.  Do not let the
        # old name set make the new module skip its start(brain) hook.  The
        # lifecycle coordinator stops running hooks before a forced reload;
        # clearing this set here also keeps the state truthful for callers
        # that use load(force=True) directly.
        if force:
            _started.clear()
        _reindex_tools()
        _loaded = True
        _sync_features()
        _log("装载", "共 %d 个：%s" % (
            len(found), "、".join(sorted(found)) or "无"))
        return dict(found)


def _sync_features():
    """插件声明了 FEATURE 就登记成功能开关，App 设置页自动多一项。

    不声明的插件不受开关管（永远开）—— 想让它能被关掉，声明 FEATURE 就行，
    别自己在钩子里读 config 判开关。
    """
    try:
        import features
    except Exception:
        return
    for p in _registry.values():
        if p.feature:
            # 走 features.register 而不是直接改 DEFAULTS：那里还管着缓存失效
            features.register(p.feature, True)


def all_plugins():
    """当前在册的插件（name -> Plugin）。没装载过就返回空，不自动扫盘。"""
    with _lock:
        return dict(_registry)


def get(name):
    with _lock:
        return _registry.get(str(name or "").strip())


def listing():
    """给 /api/plugins：所有插件 + 开关状态。"""
    out = []
    for p in sorted(all_plugins().values(), key=lambda x: x.name):
        d = p.info()
        d["enabled"] = _enabled(p)
        out.append(d)
    return out


def frontend_assets():
    """所有带前端资源的插件，给 /api/plugins/frontend 用。

    返回 {"version": <资源指纹>, "plugins": [{name, js, css, enabled}, ...]}
    只返回有 frontend.js 或 frontend.css 的插件；version 根据实际资源生成，
    资源没变就保持不变，供客户端判断是否需要热重载。
    """
    items = []
    for p in sorted(all_plugins().values(), key=lambda x: x.name):
        if not (p.frontend_js or p.frontend_css):
            continue
        # Frontend resources are consumed by the PC client. Evaluate the same
        # explicit channel as its tool and route requests so ambient context
        # cannot expose a resource that is disabled for PC.
        enabled = _enabled(p, "pc")
        if not enabled:
            # Omit disabled code so it cannot be fetched and run by the renderer.
            # The changed fingerprint makes the client dispose the old contribution.
            continue
        items.append({
            "name": p.name,
            "js": p.frontend_js,
            "css": p.frontend_css,
            "enabled": True,
        })
    digest = hashlib.sha256()
    for item in items:
        for value in (item["name"], "1" if item["enabled"] else "0",
                      item["js"], item["css"]):
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
    return {"version": digest.hexdigest()[:16], "plugins": items}


# --- 派发 ---

def _call(p, hook_name, *args):
    """调一个插件钩子，带护栏。

    返回 (是否成功, 值)。失败时值一律是 None，调用方决定怎么降级。
    错误只留一行 —— 完整栈打给 stdout 是噪音，但插件名和异常类型必须留。
    """
    fn = p.hook(hook_name)
    if not callable(fn):
        return True, None
    try:
        need = _ARITY[hook_name]
        if need == 2:
            return True, fn(*args)
        return True, fn(args[0] if need == 1 else None)
    except TypeError as e:
        # 参数个数不对单独说，这类错在调用方栈里看起来像核心代码炸了
        if "positional argument" in str(e) or "argument" in str(e):
            return False, _log(p.name, "%s 参数个数不对（要 %d 个）：%s"
                               % (hook_name, need, str(e)[:100]))
        return False, _log(p.name, "%s 调用失败：%s" % (hook_name, str(e)[:100]))
    except Exception as e:
        return False, _log(p.name, "%s 出错，已跳过：%s: %s"
                           % (hook_name, type(e).__name__, str(e)[:100]))


def prompt_block(ctx=None, native=False):
    """所有插件的 prompt 拼成一段，塞进 system。

    只拼"她有什么本事、怎么用"，不教"什么场景必须用" ——
    场景映射是那种永远补不完的规则（见 docs/插件系统.md 第二节）。

    native=True（原生 function calling）：**跳过带 TOOLS 的工具类插件**。
    它们的 prompt 是 `[tool:...]` 标签语法教学，和结构化 tool_calls 通道
    冲突，模型看了可能两套都写。工具能力此时由 tools schema 的
    description 承载。react / gate 这类没有 TOOLS 的纯行为插件照常保留
    （react 教的 think 格式仍需要）。
    """
    parts = []
    for p in sorted(all_plugins().values(), key=lambda x: x.name):
        if not _enabled(p):
            continue
        if native and p.tools:
            continue
        ok, v = _call(p, "prompt", ctx)
        if ok and isinstance(v, str) and v.strip():
            parts.append(v.strip())
    if not parts:
        return ""
    return "\n" + "\n\n".join(parts)


def on_turn_in(ctx):
    """他发来消息时，各插件可返回一段要注入 system 的提示。

    对应以前硬写在 /api/chat 里的"检测到快递单号"那段 —— 现在那类东西
    归插件管，核心不认识具体功能。

    🔴 **否决整轮靠 `ctx.veto()`，不靠返回值。**
    试过"返回空串 = 否决"，立刻踩坑：`reminder` 插件"没有提醒要注入"
    时返回的也是空串，而"所有插件都放行"时 `join([])` 同样是空串 ——
    三种情况在返回值上完全一样，于是"她一句话都说不出来"，
    而每个插件单独看都正常。空串这个值本身没有信息量。
    详见 Turn.veto 的说明。
    """
    out = []
    for p in sorted(all_plugins().values(), key=lambda x: x.name):
        if not _enabled(p):
            continue
        ok, v = _call(p, "on_turn_in", ctx)
        if ctx.vetoed:          # 任何插件表态了就不用往下走
            return ""
        if ok and isinstance(v, str) and v.strip():
            out.append(v.strip())
    return "\n".join(out)


def on_reply(ctx, text):
    """回复加工链的最后一环：所有插件按名字顺序过一遍 text。

    出错的插件直接跳过，剩下的照走 —— 加工链的产物是要发给用户的，
    一个插件炸了不能让她发不出话。
    """
    cur = str(text or "")
    for p in sorted(all_plugins().values(), key=lambda x: x.name):
        if not _enabled(p):
            continue
        ctx._owner = p.name
        ok, v = _call(p, "on_reply", ctx, cur)
        ctx._owner = ""
        if ok and isinstance(v, str) and v:
            cur = v
    return cur


def run_tool(name, arg, src=None, arg_dict=None):
    """插件的工具。名字不在册返回 None（tools.run_tool 负责先问内置那三个）。

    arg       单字符串参数（旧链路，[tool:name:arg] 标签和单参数原生工具用）
    arg_dict  多参数 dict（原生 FC 多参数工具用，如 skill_call 的 name+args）。
              传了 arg_dict 就忽略 arg，调用 fn(ctx, **arg_dict)。

    🔴 **索引是装载时的快照，会有永远查不到的合法工具名。**
    技能插件每次真调只有一个正确形状，而宿主上的工具名（`包名__工具名`）
    是运行时才问出来的，`p.tools` 里只有 `skill_list` / `skill_call`
    两个壳。按 name 查不到就返回 None，外层会拼
    「查询失败，照实说查不到，别编」—— 用户看到的症状是"技能没生效"，
    而宿主明明活着、工具明明在。
    所以查不到时再逐个问插件认不认（`owns_tool` + `invoke_tool`）。
    """
    n = str(name or "").lower()

    p = get_by_tool(n)
    if p and _enabled(p, src):
        # Legacy Creator format dispatches every listed tool through one module
        # entry point. Native function-calling supplies the full argument dict.
        if n in getattr(p, "generic_tool_names", set()):
            try:
                payload = dict(arg_dict) if isinstance(arg_dict, dict) else None
                if payload is None:
                    spec = tool_specs(src).get(n, {})
                    arg_param = spec.get("arg_param")
                    payload = ({arg_param: arg} if arg_param and arg else
                               ({"input": arg} if arg else {}))
                return p.generic_tool_dispatch(n, payload) or None
            except Exception as e:
                return _log(p.name, "工具 %s 失败：%s" % (n, str(e)[:100]))
        fn = p.tools.get(n)
        if callable(fn):
            ctx = Turn(text=(arg or "") if not arg_dict else "")
            ctx._owner = p.name
            try:
                if arg_dict is not None:
                    return fn(ctx, **{k: v for k, v in arg_dict.items()}) or None
                return fn(arg) or None
            except Exception as e:
                return _log(p.name, "工具 %s 失败：%s" % (n, str(e)[:100]))

    for q in _registry.values():
        if not _enabled(q):
            continue
        owns = getattr(q.mod, "owns_tool", None)
        inv = getattr(q.mod, "invoke_tool", None)
        if not (callable(owns) and callable(inv)):
            continue
        try:
            if owns(n):
                ctx = Turn(text=arg or "")
                ctx._owner = q.name
                return inv(n, arg, ctx) or None
        except Exception as e:
            _log(q.name, "认领工具 %s 时出错：%s" % (n, str(e)[:100]))
    return None


_TOOL_INDEX = {}


def _reindex_tools():
    """工具名 -> 持有它的插件。装载/热重载后重建一次。"""
    idx = {}
    for p in _registry.values():
        for t in p.tools:
            idx[str(t).lower()] = p
    _TOOL_INDEX.clear()
    _TOOL_INDEX.update(idx)


def get_by_tool(name):
    return _TOOL_INDEX.get(str(name or "").lower())


def tool_specs(src=None):
    """插件自报的原生 function calling 规格（name -> spec）。

    声明形状和 `tools._STATIC_SPECS` 的一项一样：

        TOOL_SPECS = {"名字": {"desc": "给模型看的说明",
                               "params": [("参数名", "参数说明", 是否必填)],
                               "arg_param": "参数名"}}

    🔴 **不声明 = 她看不到这个工具。** 原生工具清单本来是核心写死的一张表，
    插件里 `TOOLS` 写得再全，也只进得了老的 `[tool:...]` 标签那条路；
    而主链路走的是原生 FC —— 没有 schema 的工具，模型压根不知道它存在，
    表现就是"装了、列表里有、开关能点、她就是不用"。
    所以插件要真被调到，得自己把规格报上来（由 tools 那边来问）。
    """
    out = {}
    for p in all_plugins().values():
        if not _enabled(p, src):
            continue
        try:
            got = getattr(p.mod, "TOOL_SPECS", {}) or {}
            # 支持 TOOL_SPECS(src)，供按渠道动态返回工具规格。
            if callable(got):
                got = got(src)
            if isinstance(got, dict):
                entries = list(got.items())
            elif isinstance(got, (list, tuple)):
                # List schemas are only paired with the generic call_tool dispatcher.
                if not getattr(p, "generic_tool_names", set()):
                    continue
                # Compatibility for earlier Creator output:
                # [{"name": "...", "description": "...", "parameters": {...}}]
                entries = []
                for item in got:
                    if not isinstance(item, dict):
                        continue
                    key = str(item.get("name") or "").strip()
                    desc = str(item.get("description") or item.get("desc") or "").strip()
                    schema = item.get("parameters")
                    if (not key or not desc or not isinstance(schema, dict)
                            or schema.get("type") != "object"
                            or not isinstance(schema.get("properties", {}), dict)):
                        continue
                    properties = schema.get("properties", {})
                    required = schema.get("required", [])
                    if not isinstance(required, list):
                        continue
                    params = []
                    valid = True
                    for pname, pdef in properties.items():
                        if not isinstance(pname, str) or not pname.strip() or not isinstance(pdef, dict):
                            valid = False
                            break
                        pdesc = str(pdef.get("description") or pname).strip()
                        params.append((pname, pdesc, pname in required))
                    if not valid:
                        continue
                    entries.append((key, {
                        "desc": desc,
                        "params": params,
                        "arg_param": params[0][0] if len(params) == 1 else None,
                        "multi_args": True,
                        "parameters": schema,
                    }))
            else:
                continue
        except Exception:
            continue
        for n, s in entries:
            key = str(n or "").strip()
            if (key and isinstance(s, dict) and s.get("desc") and key not in out
                    and (not getattr(p, "generic_tool_names", set()) or key in p.generic_tool_names)):
                out[key] = s
    return out


_TAG_EXAMPLE_RE = re.compile(r"\[tool[:：][^\]]*\]")


def _native_help(p, tag_line):
    """把一段标签式的工具说明换成原生 function calling 版。

    插件自己写了 `TOOL_HELP_NATIVE` 就用它 —— 手写的说法比正则抠出来的干净。
    没写就把 `[tool:x:y]` 这类示例抠掉：**标签语法必须消失**
    （线上链路不执行它，留着只会让模型照着写），
    而"什么时候用它"那些话要**留着**（那才是真正有用的部分）。
    """
    alt = getattr(p.mod, "TOOL_HELP_NATIVE", "")
    if isinstance(alt, str) and alt.strip():
        return alt
    return _TAG_EXAMPLE_RE.sub("", tag_line)


def _help_text(p, native):
    """一个插件这一段说明的正文（可能来自动态提供者）。

    插件给了 `TOOL_HELP_FN` 就用它现取 —— 那是"工具清单会变"的插件
    （典型是 skill：面板装一个技能，宿主上就多几个工具）。取不到/报错
    一律回落到静态的 `TOOL_HELP`：**提示词少一段也不能拼不出来**。
    """
    fn = getattr(p, "tool_help_fn", None)
    if callable(fn):
        try:
            try:
                v = fn(native=native)      # 想按协议分叉就接这个参数
            except TypeError:
                v = fn()
            if isinstance(v, str) and v.strip():
                return v.strip()
        except Exception as e:
            _log(p.name, "TOOL_HELP_FN 出错，改用静态说明：%r" % (e,))
    if not p.tool_help:
        return ""
    return _native_help(p, p.tool_help) if native else p.tool_help


def tool_help(native=False):
    """插件工具的用法说明，拼进"工具"那一段提示。

    每条以换行开头而不是直接接在分号后面 —— 拼进去的位置前面是
    "[tool:tracking:单号] 查物流；"，不加换行会读成"；- 算式：…���"，
    模型对这种黏在一起的列表分辨力明显变差。

    ## native=True（线上走的就是这条）

    **换成不带 `[tool:...]` 标签语法的说法。** 不要安排成"工具表由 tools
    schema 承载、插件这段说明整段跳过"（见 prompt_block），可是这套说明里
    除了标签示例，还写着**什么时候该用它**（"他嘴里蹦出个你没见过的说法就查一下"）
    —— 那部分 schema 的 description 装不下，一跳过就没了。
    结果是：工具在表里、可她不知道该什么时候用。所以这里不是"跳过"，
    而是**同一份说明换一种说法**：标签示例去掉，"什么时候用"留下。
    """
    out = []
    for p in sorted(all_plugins().values(), key=lambda x: x.name):
        if not _enabled(p):
            continue
        if not (p.tool_help or p.tools or callable(getattr(p, "tool_help_fn", None))):
            continue
        line = _help_text(p, native)
        if not line:
            # 动态提供者没给出东西、又没静态说明时，退回"有哪些工具"
            line = "、".join("[tool:%s]" % t for t in sorted(p.tools))
        if line:
            out.append("\n" + line.strip())
    return "".join(out)


# ── 插件 HTTP 路由的请求身份 ───────────────────────────────────
# server 在派发插件路由前用 use_principal() 写入本请求的已认证身份
# (kind, id)：("brain","") 主人钥匙 / ("device", device_id) 设备 token。
# ContextVar 按请求线程隔离，插件 handler 用 principal() 读，做设备归属校验
# （见 plugins/remote.py 的 poll/result/cancel）。
_PRINCIPAL = contextvars.ContextVar("plugin_principal", default=("", ""))


def principal():
    """当前插件路由请求的已认证身份 (kind, id)。非 HTTP 上下文是 ("","")。"""
    return tuple(_PRINCIPAL.get())


@contextlib.contextmanager
def use_principal(who):
    """在 with 块内把插件路由身份设成 who（请求级，退出自动复位）。"""
    token = _PRINCIPAL.set(tuple(who) if who else ("", ""))
    try:
        yield
    finally:
        _PRINCIPAL.reset(token)


def route_table():
    """所有插件的 HTTP 路由，格式 (method, path, handler, plugin_name)。"""
    out = []
    for p in sorted(all_plugins().values(), key=lambda x: x.name):
        for r in p.routes:
            if len(r) < 3:
                _log(p.name, "ROUTES 项不完整（要 method, path, handler）：%r" % (r,))
                continue
            out.append((str(r[0]).upper(), str(r[1]), r[2], p.name))
    return out


def runtime_brain():
    """Return the Brain instance registered by the running server, if any."""
    return _runtime_brain


def started_names():
    """Return a snapshot of plugins whose start hook completed successfully."""
    with _lock:
        return set(_started)


def is_started(name):
    with _lock:
        return str(name or "") in _started


def is_enabled(name, src=None):
    plugin = get(name)
    return bool(plugin is not None and _enabled(plugin, src))


def stop(name=None):
    """Stop one/all started plugins when they expose a stop-like hook."""
    names = [name] if name else list(_started)
    errors = []
    for item_name in names:
        plugin = get(item_name)
        if not plugin:
            # A stale registry entry must not affect the Brain reference or
            # another plugin's running state.
            continue
        stopped = False
        for hook_name in ("stop", "shutdown", "close"):
            hook = plugin.hook(hook_name)
            if callable(hook):
                try:
                    hook()
                    stopped = True
                except Exception as exc:
                    errors.append("%s.%s: %s" % (item_name, hook_name, str(exc)[:120]))
                break
        if stopped or not callable(plugin.hook("start")):
            _started.discard(item_name)
    return (not errors), errors


def start_all(brain, names=None, detailed=False):
    """把带 start 的插件的后台线程跑起来（run_server 装完大脑后调一次）。"""
    global _runtime_brain
    if brain is None:
        return 0
    _runtime_brain = brain
    n = 0
    started = []
    failed = []
    wanted = set(names or ())
    for p in sorted(all_plugins().values(), key=lambda x: x.name):
        if wanted and p.name not in wanted:
            continue
        if p.name in _started:
            continue
        if not _enabled(p) or not callable(p.hook("start")):
            continue
        ok, _v = _call(p, "start", brain)
        if ok:
            _started.add(p.name)
            started.append(p.name)
            n += 1
        else:
            failed.append(p.name)
    if n:
        _log("启动", "%d 个插件的后台循环已起" % n)
    if detailed:
        return {"count": n, "started": started, "failed": failed}
    return n


def _enabled(p, src=None):
    """这个插件此刻开没开。没声明 FEATURE = 永远开。

    src 不传就按"当前渠道"（features 的 ambient 语义）判；**调用方能传就传** ——
    当前渠道是个进程级全局，两个渠道并发时它可能已经被另一个请求改掉了。
    """
    # Unified lifecycle state is persisted by plugin_manager/plugin_ops. Keep
    # FEATURE channel semantics for compatibility, but let the manager disable
    # every plugin (including legacy featureless Python plugins) consistently.
    try:
        import plugin_ops
        meta = (plugin_ops.read_registry().get("plugins") or {}).get(p.name) or {}
        if meta.get("enabled") is False:
            return False
    except Exception:
        pass
    if not p.feature:
        return True
    try:
        import features
        return bool(features.on(p.feature, src))
    except Exception:
        return True
