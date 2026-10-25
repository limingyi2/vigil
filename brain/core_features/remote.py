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
没法干活了。渠道裁剪统一交给 localtools.specs()，本文件不自己维护名单。
"""
# 注意：核心模块叫 localtools，不叫 remote —— 这个文件自己就叫
# core_features/remote.py 由文件路径动态加载；这里的模块名与顶层 remote.py 不冲突
# （kb 踩过同一个坑）。名字不一样，谁先谁后都不会出错。
import localtools

NAME = "remote"
DESC = "本地操控：她可以用这台电脑看文件、截图、打开网页"
FEATURE = None               # 始终启用——这是 agent 的核心能力，不是可选功能

# 渠道裁剪的唯一真源在 localtools.specs()，执行层第二道关卡是
# localtools.clearance()。本插件不自己维护渠道黑名单，免得两份名单漂移。
# （core 只跑 pc，那两处现在对 pc 一律放行。）


def tool_specs(src=None):
    """按渠道返回工具集。规则全部由 localtools.specs() 裁决。

    plugin_host.tool_specs 检测到 TOOL_SPECS 是函数时会调用它并传入 src。
    """
    return localtools.specs(src=str(src).lower() if src else None)


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
    """给每个远程工具生成一个工具函数：参数原样透传，实际动作在客户端做。

    两种调用形状都要认：
    - 老式标签 / 单参数原生工具：`_fn("F:/某目录")`；
    - 多参数原生工具（grep 的 path+pattern、write 的 path+text）：host 按
      `fn(ctx, **arg_dict)` 调用，**关键字全在 kwargs 里**。以前只认第一种，
      多参数工具于是丢掉了除第一个以外的所有参数。
    """
    def _fn(first=None, **kwargs):
        if kwargs:
            return localtools.call(name, {k: v for k, v in kwargs.items()})
        return localtools.call(name, _args_text(name, first))
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


def _principal():
    """本次 HTTP 路由的已认证身份 (kind, id)。

    由 server 派发插件路由前写进 plugin_host 的请求级 ContextVar。
    进程内直接调 handler（测试/内部脚本）时是空身份，按"本机可信"处理 ——
    空身份不可能从 HTTP 进来（server 对所有插件路由都先鉴权）。
    """
    try:
        import plugin_host
        return tuple(plugin_host.principal()) or ("", "")
    except Exception:
        return ("", "")


def _h_poll(body, query):
    """客户端轮询：有活就派给它。

    能力从 query 里带（caps=local_file_list,local_file_read）。
    **必须按能力过滤**：手机没有"打开桌面文件"这个能力，
    派过去只会白等一轮。

    还会跟**注册时声明过**的能力取交集：客户端当次多报一个，
    也不至于拐到它本来没有的能力上去。声明过的是空表（老客户端）就不卡它。

    🔴 归属校验：设备 token 轮询时，query 的 device 必须就是凭据绑定的
    那台设备（缺省也按自己算）—— 否则一台设备能认领派给别的设备的活。
    """
    who, who_id = _principal()
    caps = [x for x in str(query.get("caps") or "").split(",") if x]
    dev = str(query.get("device") or "")
    if who == "device":
        if dev and dev != who_id:
            return {"ok": False,
                    "err": "device 与设备凭据不一致，只能认领自己的任务",
                    "_code": 403}
        dev = who_id
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
    approval_mode = str(query.get("approval_mode") or "").strip().lower()
    c = localtools.take(dev, caps, approval_mode=approval_mode)
    if not c:
        return {"ok": True, "call": None, "wait": 1}
    return {"ok": True, "call": c, "wait": 0}


def _h_result(body, query):
    """提交结果。设备凭据只能给自己认领的那单提交（localtools.resolve 校验）。"""
    who, who_id = _principal()
    ok = localtools.resolve(
        body.get("call_id"), body.get("result"), body.get("err"),
        device_id=(who_id if who == "device" else None))
    if not ok:
        return {"ok": False, "err": "这条调用不在了，或不属于这台设备",
                "_code": 403}
    return {"ok": True, "err": ""}


def _h_cancel(body, query):
    """停止等待中的调用。设备凭据只能取消自己名下的单，不能全局停。"""
    who, who_id = _principal()
    cid = str(body.get("call_id") or "")
    ok = localtools.cancel(
        call_id=cid or None,
        device_id=(who_id if who == "device" else None))
    if not ok:
        return {"ok": False, "err": "没有可取消的调用，或不能取消别人的调用",
                "_code": 403}
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
