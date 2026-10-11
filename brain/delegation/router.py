# -*- coding: utf-8 -*-
"""确定性轻量路由（效率优化 P1）：不新增 Agent、不训练分类器。

只在「用户显式给出已授权本地目录 + 任务形态明确」时做确定性决定；
识别不了的一律 cloud_direct，走现有云端主模型决策（包括由它调用
delegate_to_local）。所有判定可观测（返回 reason）、可覆盖
（delegation.router_enabled=false 整体关掉）、可回退（local_direct
执行失败由调用方退回云端，不影响原有链路）。

三条路由：
- cloud_direct     云端直接处理（开放式推理/实时外部信息/无法判定）
- local_direct     简单、有明确答案、可程序化校验的本地文件任务，
                   本地执行后直接回复，跳过云端总结
- hybrid_delegate  跨文件/多步勘察任务：本地取证、云端判断（现状链路）
"""
import os
import re

# 消息里的 Windows / POSIX 绝对路径
_ABS_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|/)[^\s，。；、,;\"'）)】\]]+")

# 实时 / 外部信息意图：本地沙箱无网络，碰这些绝不走本地
_CLOUD_HINT = re.compile(
    r"天气|气温|下雨|降水|带伞|快递|物流|外卖|新闻|热搜|头条|现在几点|"
    r"当前时间|汇率|股价|股票|行情|比特币|搜一下|搜索一下|帮我搜|百度|"
    r"谷歌|网址|网页|联网|上网查")

# 多步勘察 / 跨文件推理：本地取证据、云端做判断（= 现有委派链路）
_HYBRID_HINT = re.compile(
    r"根因|为什么.*(报错|失败|错)|连锁|首个?.*(错误|报错)|日志.*分析|"
    r"分析.*日志|排查|追踪|跨文件|修复(计划|步骤)?|步骤计划|怎么修|"
    r"列一个.*计划|帮我列.*计划")

# 简单、可程序化校验的本地查询：单值 / 定位 / 列目录 / 找文件
_LOCAL_DIRECT_HINT = re.compile(
    r"(是多少|有多少|是几|值是什么|配置项|"
    r"在哪个文件|哪个文件里|文件在哪|位置在哪|"
    r"找一?下?.*(文件|配置)|列出?|都有哪些|有什么(文件|配置)|"
    r"搜.*(在哪个|哪些文件)|包含.*的文件)")

# local_direct 只接短任务（一句话指令）；长描述里细节多，交云端判
_MAX_LOCAL_DIRECT_LEN = 160


def _within(root, target):
    try:
        root = os.path.realpath(root)
        target = os.path.realpath(target)
        return target == root or os.path.commonpath([root, target]) == root
    except Exception:
        return False


def find_authorized_workspace(text, allowed_roots):
    """从消息文本里找一个落在授权根内、真实存在的目录。找不到返回 ''。

    只认真实绝对路径 —— 不靠关键词猜目录名（任务书：有歧义的路径不得猜测）。
    """
    for m in _ABS_PATH.finditer(text or ""):
        p = m.group(0).rstrip(".。")
        if not p or len(p) < 4:
            continue
        try:
            p = os.path.abspath(os.path.expanduser(p))
        except Exception:
            continue
        if not os.path.isdir(p):
            continue
        for root in allowed_roots or []:
            if root and _within(root, p):
                return p
    return ""


def classify(text, *, allowed_roots):
    """纯函数：返回 {route, reason, workspace, kind}。永不抛异常。"""
    text = str(text or "").strip()
    fallback = {"route": "cloud_direct", "reason": "", "workspace": "",
                "kind": ""}
    if not text:
        fallback["reason"] = "empty"
        return fallback
    try:
        ws = find_authorized_workspace(text, allowed_roots)
        if not ws:
            fallback["reason"] = "no_authorized_path_in_text"
            return fallback
        if _CLOUD_HINT.search(text):
            return {"route": "cloud_direct",
                    "reason": "realtime_or_external_intent",
                    "workspace": ws, "kind": ""}
        if _HYBRID_HINT.search(text):
            return {"route": "hybrid_delegate",
                    "reason": "multi_step_investigation_hint",
                    "workspace": ws, "kind": ""}
        if len(text) <= _MAX_LOCAL_DIRECT_LEN and _LOCAL_DIRECT_HINT.search(text):
            return {"route": "local_direct",
                    "reason": "simple_verifiable_local_lookup",
                    "workspace": ws, "kind": "file_discovery"}
        fallback["reason"] = "ambiguous_keep_cloud_path"
        fallback["workspace"] = ws
        return fallback
    except Exception as e:
        return {"route": "cloud_direct",
                "reason": "classifier_error:%s" % type(e).__name__,
                "workspace": "", "kind": ""}
