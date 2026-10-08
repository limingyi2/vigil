# -*- coding: utf-8 -*-
"""本地操控：她可以用他的电脑做点事（列目录、读文件、打开网页、截图…）。

**这一段的信任前提**：能连上来的客户端是**他自己装的**，token 是他自己的。
真正的拦截在客户端那一侧（危险操作先弹确认、路径白名单、命令白名单），
服务端这里只做三件事：登记能力、派活、收结果。

**为什么危险工具还留着**：删文件、写文件、跑命令这些是真有用（"帮我把这个
日志删了"），一律禁掉等于这个功能没做。做法是**客户端默认关着**（写操作
和命令执行在 DEFAULT_OFF 里），要用的时候在客户端上开，而且每次执行
都要他点一下确认。

**为什么 FEATURE = None（不做成可开关的功能）**：本地操控是 agent 的核心
能力，不是"可选功能"。用户不应该能在设置里把它关掉——关了 agent 就
没法干活了。渠道过滤是硬编码的：微信渠道没有本地文件系统，TOOL_SPECS
在 wechat 渠道返回空；app 渠道（和其他有客户端的渠道）返回全部。
"""
# 注意：核心模块叫 localtools，不叫 remote —— 这个文件自己就叫
# plugins/remote.py，plugins/ 又在 sys.path 上，叫 remote 会 import 到自己
# （kb 踩过同一个坑）。名字不一样，谁先谁后都不会出错。
import localtools

NAME = "remote"
DESC = "本地操控：她可以用这台电脑看文件、截图、打开网页"
FEATURE = None               # 始终启用——这是 agent 的核心能力，不是可选功能

# 没有本地文件系统的渠道：这些渠道不应该出现 local_* 工具。
# 微信是手机端聊天，没有"操作本地电脑"这个概念。
_NO_LOCAL_SRC = {"wechat"}


def tool_specs(src=None):
    """按渠道返回工具集。微信等无本地文件系统的渠道返回空。

    plugin_host.tool_specs 检测到 TOOL_SPECS 是函数时会调用它并传入 src。
    """
    if src and str(src).lower() in _NO_LOCAL_SRC:
        return {}
    return localtools.specs()


# 兼容旧引用：有些地方可能直接读 remote.TOOL_SPECS（当字典用）。
# 函数本身也能被 dict() 调用吗？不能。所以这里保留一个模块级的别名，
# 指向默认渠道（app）的完整工具集，供不关心渠道的调用方用。
TOOL_SPECS = tool_specs


def _args_text(name, arg):
    """把单个参数（老式 [tool:name:arg] 调用只有一个参数）凑成 args 字典。"""
    spec = localtools.REMOTE_TOOLS.get(name) or {}
    p = spec.get("arg_param")
    return {p: arg} if p else {}


def _mk(name):
    """给每个远程工具生成一个工具函数：参数原样透传，实际动作在客户端做。"""
    def _fn(arg):
        return localtools.call(name, _args_text(name, arg))
    _fn.__name__ = "_t_" + name
    return _fn


TOOLS = {n: _mk(n) for n in localtools.REMOTE_TOOLS}

TOOL_HELP = "".join(
    "- %s：[tool:%s:%s]\n" % (localtools.REMOTE_TOOLS[n]["desc"].split("。")[0], n,
                             (localtools.REMOTE_TOOLS[n]["params"][0][0]
                              if localtools.REMOTE_TOOLS[n]["params"] else ""))
    for n in localtools.REMOTE_TOOLS)

TOOL_HELP_NATIVE = "".join(
    "- %s\n" % localtools.REMOTE_TOOLS[n]["desc"] for n in localtools.REMOTE_TOOLS)


def _h_poll(body, query):
    """客户端轮询：有活就派给它。

    能力从 query 里带（caps=local_file_list,local_file_read）。
    **必须按能力过滤**：手机没有"打开桌面文件"这个能力，
    派过去只会白等一轮。

    还会跟**注册时声明过**的能力取交集：客户端当次多报一个，
    也不至于拐到它本来没有的能力上去。声明过的是空表（老客户端）就不卡它。
    """
    caps = [x for x in str(query.get("caps") or "").split(",") if x]
    dev = str(query.get("device") or "")
    try:
        import devices
        rec = devices.get(dev) if dev else None
        declared = (rec or {}).get("capabilities") or []
        if declared:
            caps = [c for c in caps if c in declared]
    except Exception:
        pass
    if not caps:
        return {"ok": True, "call": None, "wait": 1}
    c = localtools.take(dev, caps)
    if not c:
        return {"ok": True, "call": None, "wait": 1}
    return {"ok": True, "call": c, "wait": 0}


def _h_result(body, query):
    ok = localtools.resolve(body.get("call_id"), body.get("result"), body.get("err"))
    return {"ok": ok, "err": "" if ok else "这条调用不在了（可能已超时）"}


def _h_cancel(body, query):
    """用户点"停止"：取消当前正在等待的工具调用。"""
    localtools.cancel()
    return {"ok": True}


def _h_stats(body, query):
    return {"ok": True, **localtools.stats()}


def _h_tools(body, query):
    """给客户端/面板看：现在登记了哪些远程工具。"""
    return {"ok": True,
            "items": [{"name": n, "desc": v["desc"], "danger": v["danger"],
                       "group": v["group"],
                       "params": [p[0] for p in v["params"]]}
                      for n, v in localtools.REMOTE_TOOLS.items()]}


ROUTES = [
    ("GET", "/api/plugin/remote/poll", _h_poll),
    ("POST", "/api/plugin/remote/result", _h_result),
    ("GET", "/api/plugin/remote/tools", _h_tools),
    ("GET", "/api/plugin/remote/stats", _h_stats),
    ("POST", "/api/plugin/remote/cancel", _h_cancel),
]
