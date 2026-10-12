# -*- coding: utf-8 -*-
"""云端大脑服务（组装与路由层）。

远程大脑 API（端口 8788）：手机 App 与桌宠都连它聊天，记忆/人设/上下文
只有云上这一份；历史摘要、主动搭话也都在这个进程里跑。

模块分工：runtime 共享配置｜reply 回复后处理｜randimg 随机图｜selfie 自拍｜
imggen 生图锁脸｜proactive 主动搭话｜jobs 后台循环。
本文件只管装配大脑、聊天管线、HTTP 路由、启动。用法：python server.py
"""
import os
import sys
import json
import time
import hmac
import base64
import threading
import contextlib
import http.server

from runtime import (
    api_config, API_CFG, MOB_CFG, BRAIN_TOKEN,
    _LOCAL, _trace_write, _trace_tail, load_config, config_path, data_file)
# 🔴 也把模块本身绑个名：换模型后刷新 API_CFG 要调 runtime.api_cfg_subset()，
# 而上面是 from...import，只把名字引进来了、模块名没绑。
# 不绑的话只能再抄一份白名单 —— 而"抄一份少一个键"正是这回的病根
# （vision/voice/persona_file/uapi 已经栽过四回）。
import runtime as _runtime

# 模型中枢 + 错误日志：App 在手机上换模型、导出日志都走这两个模块。
# 异常钩子装上后，线程里静默死掉的异常也会留下记录
import model_hub
import errlog
try:
    errlog.install_excepthook()
except Exception:
    pass

# --- 各领域模块（server 自己用的 + 给老脚本 re-export 的都在这） ---
from reply import strip_say_marker, resolve_voice_tag, resolve_gen_tags
from randimg import resolve_rand_tags, RAND_TAG_RE, _cloud_random_image
from selfie import (_SELFIE_STRONG, _SELFIE_MISS, _SELFIE_THING_RE,
                    _looks_like_selfie, _hour_seg,
                    _wear_for, _selfie_prompt, _selfie_caption)
from imggen import (GEN_NEGATIVE, _cloud_gen_image, _cloud_gen_selfie,
                    _cloud_sticker, _her_look, _look_ref_path, _look_ref_uri,
                    _sticker_path)
from proactive import (PROACTIVE_RULES, note_client_ip, _his_ip,
                       _proactive_conf, _in_quiet, _proactive_stats_load,
                       _proactive_note_sent, _proactive_note_user,
                       _proactive_interval_multiplier, _proactive_used_today,
                       _proactive_count_up, _recent_her_texts, _norm_text,
                       _is_repeat_of_recent, _cloud_append_assistant)
from jobs import start_background

# re-export 给外部脚本用（worldgen.py 与 _test_* 仍从 server 取这些名字）
__all__ = [
    "api_config", "API_CFG", "MOB_CFG", "BRAIN_TOKEN", "load_config",
    "strip_say_marker", "resolve_voice_tag", "resolve_gen_tags",
    "resolve_rand_tags", "RAND_TAG_RE", "_cloud_random_image",
    "_SELFIE_STRONG", "_SELFIE_MISS", "_SELFIE_THING_RE",
    "_looks_like_selfie", "_hour_seg", "_wear_for", "_selfie_prompt",
    "_selfie_caption", "GEN_NEGATIVE", "_cloud_gen_image", "_cloud_gen_selfie",
    "_cloud_sticker", "_her_look", "_look_ref_path", "_look_ref_uri",
    "_sticker_path", "PROACTIVE_RULES", "note_client_ip", "_his_ip",
    "_proactive_conf", "_in_quiet", "_proactive_stats_load",
    "_proactive_note_sent", "_proactive_note_user",
    "_proactive_interval_multiplier", "_proactive_used_today",
    "_proactive_count_up", "_recent_her_texts", "_norm_text",
    "_is_repeat_of_recent", "_cloud_append_assistant", "start_background",
    "build_local_brain", "ask_with_retry", "start_remote_api", "run_server",
]


# --- 自己养一个她（独立模式） ---

def build_local_brain(verbose=True):
    """按和桌宠完全一样的方式，在本进程里装配一个 Brain。

    只在 --standalone 时用（云端没有桌面，开不了桌宠）。两台进程各养一个"她"
    会互相覆盖记忆，所以同一时间只允许一个进程养她。
    """
    from memory_store_v2 import get_default_store
    from brain import Brain

    # 插件必须在 Brain() **之前**装载：人设里的"工具"清单在构造时拼进
    # draft_rules 的（persona_store._plugin_tools），晚于构造再装载，
    # 她就少了新插件的工具说明 —— 提示词不会自己补，得重跑 reload_persona
    load_plugins(verbose=verbose)

    # self.mem 只是**兜底**（brain._mem_for 异常时才用），但它得指向一份
    # 分区过的库。传空会让 get_default_store() 落回无分区名的公共库
    # memory.json —— 那份装着旧人设的旧账，于是换人设后她照样带着上一段的
    # 记忆开口（分区建了、文件也有，只有内容是错的）。
    # 走默认人设 + app 渠道：老数据在那儿，不用搬。
    try:
        from persona_store import key_from_config
        mem = get_default_store(key_from_config(api_config), "app")
    except Exception:
        mem = get_default_store()
    mode = api_config.get("chat_mode", "api")
    b = Brain(mem, api_config, mode=mode)
    if verbose:
        print(f"[大脑] 独立模式：她的脑子在本进程里（chat_mode={mode}）", flush=True)

    try:
        n = b.seed_history()
        if n and verbose:
            print(f"[大脑] 接上了上次的聊天记录（{n} 条）", flush=True)
    except Exception as e:
        if verbose:
            print(f"[大脑] 聊天记录没读回来（不影响聊天）：{e}", flush=True)

    return b


def load_plugins(verbose=False):
    """装载 brain/plugins/ 下的插件。进程启动时调一次。

    装不上只打一行 —— plugins/ 是空的（大部分时候就是空的）完全不影响聊天。
    装上了就登记成功能开关，App 设置页会自动多出对应条目。
    """
    try:
        import plugin_host
    except Exception as e:
        print(f"[插件] 加载器本身坏了（不影响聊天）：{e}", flush=True)
        return 0
    try:
        got = plugin_host.load(force=True)
        if verbose and got:
            print(f"[大脑] 插件：{len(got)} 个已装载", flush=True)
        return len(got)
    except Exception as e:
        print(f"[插件] 装载出错（不影响聊天）：{e}", flush=True)
        return 0


# 聊天锁的等待上限（秒），算出来的不是拍的：
#   一轮 chat 最坏 = 60s + 重试间隔 1.5s + 重试 60s = 121.5s
#   工具轮最多两次 chat = 243s
#   UAPI 调用最坏 = 6s × 2 = 12s
#   合计约 255s，图片/语音的合成在锁外。取 300 留余量。
# 超了明说"在忙"。以前无超时地挂着，一个卡住的请求能把后面全堵死。
CHAT_LOCK_WAIT = 300

# 单次请求体上限。带图 base64 也就几 MB，12MB 够用，
# 顺便挡掉"报个超大 Content-Length 把内存打满"。
MAX_BODY = 12 * 1024 * 1024

# ── 设备 Token 权限矩阵（默认拒绝）─────────────────────────────────
# 主人钥匙（brain token）：全部 API。
# 设备 token 分两种角色（devices.ROLES）：
#   admin —— 用户自己的桌面客户端，注册时由主人授予，API 面同主人钥匙；
#   device —— 受限端（手机/桌宠/新设备），只放行下面名单里的接口。
# 名单之外的一切（装插件、改人设/模型/语音、功能开关、路由切换、记忆编辑、
# 重置/清档、设备注册…）对受限设备一律 403。
# 新增管理类接口时**默认不在名单里** —— 要开放给设备必须显式加进来。
DEVICE_GET_ALLOW = frozenset({
    "/api/chat",                        # 极少数 GET 化的探测（兼容）
    "/api/health",
    "/api/history",
    "/api/features",
    "/api/chatty",
    "/api/stats",
    "/api/logs",
    "/api/plugins",                     # 桥接/启动器的存活探针
    "/api/plugins/frontend",            # 客户端要拉插件前端资源
    "/api/packages",
    "/api/stickers",
    "/api/voice",
    "/api/img",
    "/api/route",                       # 只读：当前路由状态
    "/api/persona",
    "/api/persona/full",
    "/api/voices",
    "/api/models",
    "/api/memory/stores",
    "/api/memory/list",
    "/api/device/list",
    "/api/app/version",
    "/api/app.apk",
    "/api/client/version",
    "/api/client/setup",
    # remote 插件：设备轮询/看工具目录/统计
    "/api/plugin/remote/poll",
    "/api/plugin/remote/tools",
    "/api/plugin/remote/stats",
})
DEVICE_GET_PREFIX = ("/api/sync/",)
DEVICE_POST_ALLOW = frozenset({
    "/api/chat",
    "/api/chat/stream",
    "/api/logs",                         # 客户端上报错误
    "/api/stickers/collect",
    "/api/sync/flush",                   # 客户端「立即同步」
    "/api/device/list",
    "/api/device/revoke",                # 只能解绑自己（路由内再校验）
    # remote 插件：认领任务 / 交结果 / 取消自己的单
    "/api/plugin/remote/poll",
    "/api/plugin/remote/result",
    "/api/plugin/remote/cancel",
})
DEVICE_POST_PREFIX = ("/api/sync/",)


def _device_path_allowed(method, path):
    """受限设备（role=device）能不能访问这个路径。纯函数，方便直接测。

    精确匹配优先，前缀只留给 /api/sync/ 这一整族同步账本接口。
    """
    p = str(path or "").split("?", 1)[0]
    if str(method).upper() == "GET":
        if p in DEVICE_GET_ALLOW:
            return True
        return any(p.startswith(x) for x in DEVICE_GET_PREFIX)
    if p in DEVICE_POST_ALLOW:
        return True
    return any(p.startswith(x) for x in DEVICE_POST_PREFIX)


def _device_role(device_id):
    """查一台设备的角色；查不到（已吊销/不存在）给最受限的 'device'。"""
    try:
        import devices
        rec = devices.get(device_id) or {}
        role = str(rec.get("role") or "device")
        return role if role in ("device", "admin") else "device"
    except Exception:
        return "device"


class _Busy(Exception):
    """等聊天锁超时：这一轮没轮到脑子。"""


def _norm_src(src):
    """把请求里的渠道名收拾成已知渠道。

    白名单而不是黑名单：渠道名会拼进存档文件名，写进配置也决定读哪份人设，
    认不出来的值一律归一化成主渠道 wechat。这是"app 已退役"后的明确政策，
    不是静默巧合：
      - 桌面客户端显式发 pc；微信桥接显式发 wechat/wechat_group；
      - 手机 App 框架不再有聊天链路（PollService 的历史轮询也已改默认值）；
      - 多传/拼错一个 src 不会出事，但 "../../evil" 也没机会碰到文件系统。
    """
    try:
        import paths
        allowed = paths.CHANNELS
    except Exception:
        allowed = ("wechat", "wechat_group", "pc")
    s = str(src or "").strip().lower()
    return s if s in allowed else "wechat"


def _persona_for_src(src, default_key=""):
    """这个渠道该用哪个人设（读 config.json 的 persona_by_src）。

    跟 brain._persona_key_for 同一套规则：渠道有人设专用就用那个，
    否则回默认人设。**必须共用一份实现** —— 两处各读一次 config，
    改了一处忘了另一处，存档路径和记忆路径就会算成两个名字，
    表现是「分区建了、文件也有，只有内容是错的」。
    """
    try:
        import paths, json as _json
        cfg = _json.loads(open(paths.CONFIG_PATH, encoding="utf-8").read())
        by = cfg.get("persona_by_src") or {}
        return str(by.get(src) or "").strip() or str(default_key or "")
    except Exception:
        return str(default_key or "")


@contextlib.contextmanager
def _chat_lock(timeout=CHAT_LOCK_WAIT):
    """有上限地拿聊天锁（超时抛 _Busy，不无限期阻塞）。"""
    lock = _LOCAL["lock"]
    if not lock.acquire(timeout=timeout):
        raise _Busy("等聊天锁超过 %d 秒" % timeout)
    try:
        yield
    finally:
        lock.release()


def _is_loopback(ip):
    """是不是本机回环地址（含 ::1）。"""
    s = str(ip or "").strip().strip("[]").lower()
    return s in ("127.0.0.1", "::1", "localhost") or s.startswith("127.")


def _auth_ok(auth_header, query_token, config_token, peer_ip):
    """鉴权判定（纯函数，方便直接测）。

    - 配了 token：Authorization 头优先，?token= 兼容老客户端（<img>/<audio>
      设不了头，只能走 query）；用 compare_digest 比，`==` 会在第一个不同的
      字符上短路，长度和前缀可以被计时探测出来。
    - **没配 token：只放行本机回环**。不能"没配就全放行"（fail-open），
      配上 bind_host: 0.0.0.0 就等于把整个 API 摆到公网 —— 而忘记填 token
      恰恰是最常见的情况。
    """
    if not config_token:
        return _is_loopback(peer_ip)
    got = str(auth_header or "")
    if got.startswith("Bearer "):
        got = got[7:]
    if not got:
        got = str(query_token or "")
    if not got:
        return False
    try:
        return hmac.compare_digest(got, str(config_token))
    except Exception:
        return False


def _snapshot(b):
    """把这一轮的 trace 字段从大脑上抄下来。

    **必须在持锁时调用**：last_sizes / last_hits / last_ms / last_usage 都长在
    共享的 Brain / MemoryStore 上，出锁再读会被并发那一轮覆盖。
    """
    hits = getattr(getattr(b, "mem", None), "last_hits", None) or []
    usage = getattr(b, "last_usage", None) or {}
    d = {
        "blocks": getattr(b, "last_sizes", None) or {},
        "mem_hits": len(hits),
        "mem_top": [round(float(s), 2) for s, _t in hits[:3]],
        "ms": getattr(b, "last_ms", None),
        "tok_in": usage.get("in"), "tok_out": usage.get("out"),
        "model": str((API_CFG or {}).get("model") or ""),
        # 思考过程：推理模型返回的 reasoning_content（MiMo/DeepSeek 等），
        # 客户端可以折叠显示，排查"她为啥这么答"时用。
        "think": getattr(b, "last_think", "") or "",
    }
    # 🔴 **顺带抄一份「她这轮实际看到的工具段」**。
    # 排查"技能装了却不调"时，光看 trace.tool 是空根本分不清三种情况：
    #   ① 提示词里压根没那个技能名（拼装时就没进来）
    #   ② 提示词里有，但缓存没刷新（她读的是旧那份）
    #   ③ 提示词里有、也是新的，模型看见了就是不用
    # 这三种修法完全不同，而从外面一个都看不出来。
    # 判据必须落在**运行中这个进程**的 draft_rules 上 —— 另起解释器拼一遍
    # 没用：它一个插件都没装载（load_plugins 没跑），拼出来必然是空的。
    try:
        r = str(b.draft_rules or "")
        i = r.find("JS 技能")
        d["rules_len"] = len(r)
        d["rules_has_skill"] = bool(i >= 0)
        d["rules_has_fortune"] = "daily-fortune__today" in r
        d["rules_has_must"] = "必须" in r
    except Exception:
        pass
    return d


# 规划/修正 prompt 的指纹。brain.chat 会把喂给它的 prompt 原样记进历史和当日
# 流水，那两条不是用户说的话，事后要认出来删掉（见 _restore_hist）。
_PLAN_PROMPT_MARK = "（系统提示：你已经按计划完成了以下步骤"
# D1：规划前确定性委派的收尾提示同样是系统注入的 user 消息，历史修正时
# 要一并撤掉、换成用户原话（与 Planner 注入提示同等待遇）
_DELEG_PROMPT_MARK = "（系统提示：本地子任务已在授权工作区内完成勘察"
_INJECTED_PROMPT_MARKS = (_PLAN_PROMPT_MARK, _DELEG_PROMPT_MARK)


def _restore_hist(brain, text, ans, mode=None):
    """把这一轮规划塞进历史和当日流水里的临时记录撤掉，换成原话 + 最终回答。

    brain.chat 每调一次就 hist.append 一对 user/assistant，user 那条的内容是
    喂给它的 prompt（"（系统提示：你已经按计划完成了…"）。留着的话，她下一轮
    读历史会以为他刚说了这种话。改「最后一条 user」也不够—— 走了检查器修正
    就是两对，改一条还剩一条。

    按内容认、不按下标切：brain.chat 里有历史截断
    （hist = [hist[0]] + hist[-turns:]），一下标就可能切错位置。

    🔴 **失败的兜底话不能写回历史。**
    上游挂了的时候 brain.chat 故意不 append（它自己判 _failed），而这里原来
    **无条件** append —— 于是那句"……我脑子有点转不动"被当成她说过的话存进
    历史。这正是 brain.chat 注释里记的那次事故（欠费那阵攒了 97 条一模一样的
    兜底话，重启灌回上下文后历史里全是同一句），在规划这条链路上原样复现。
    """
    try:
        keep, drop_next = [], False
        for m in brain.hist:
            if drop_next:
                drop_next = False
                continue
            if (m.get("role") == "user"
                    and any(mk in str(m.get("content") or "")
                            for mk in _INJECTED_PROMPT_MARKS)):
                drop_next = True
                continue
            keep.append(m)
        brain.hist = keep
        # 失败/空回答：临时记录照撤，但**不补**那一对（跟 brain.chat 一致）
        if mode == "API失败" or not (ans or "").strip():
            return
        stamp = brain._stamp()
        brain.hist.append({"role": "user", "content": stamp + text})
        brain.hist.append({"role": "assistant", "content": stamp + ans})
    except Exception as e:
        print("[大脑] 历史修正失败（不影响回复）：%s" % str(e)[:60], flush=True)


def _chat_with_tools(text, img_path, trace_out=None, src=None):
    """聊一轮。工具调用走大脑**内部的原生 function calling 循环**（见
    brain.draft_native）：模型在响应的 tool_calls 字段请求工具，结果在同一
    条 messages 链上以 role:tool 回灌，直到她给出最终回复，不靠正文标签。

    本函数只负责：加锁调大脑、可选的 Plan-and-Execute 前置规划、trace 记录、
    以及末尾对残留标签的兜底清扫。

    features.tools 的总开关**由 tools.all_specs() 判**（工具表的唯一出口）——
    这里不再单独分支。放在 all_specs 里，换哪条起草链路都盖得住。
    """
    def _once(t, no_tools=False):
        with _chat_lock():
            b = _LOCAL["brain"]
            ans, mode = b.chat(t, img=img_path, log=False, src=src,
                               no_tools=no_tools)
            # trace 字段长在共享的大脑对象上，得趁锁还在手里抄，出了锁并发
            # 那一轮会把它覆盖掉。
            if trace_out is not None:
                trace_out.update(_snapshot(b))
                # 原生 FC 用到的工具也记进 trace（供事后追"这轮为啥这么答"）
                _used = getattr(b, "last_tools_used", None)
                if _used:
                    trace_out["tool"] = ",".join(_used)
            return ans, mode

    # Plan-and-Execute：复杂任务先规划步骤再执行，简单任务跳过（零成本判断）
    #
    # 🔴 **这一节在观察期**（见 planner.note/stats）：将来判它"删还是留"要靠数据，
    # 所以三种结果**都必须能在 trace 里看见**。只给"成功"那种留痕
    # （plan_steps/revised），下面这两种只打一行日志、trace 里什么都没有：
    #   * 判要规划、但 plan() 一步都没解析出来 → 悄悄回退直答
    #   * 规划途中抛异常 → 悄悄回退直答
    # 结果就是"面板上看着像没走规划分支"，白花的那几次调用查不出来。
    _plan_why = ""
    _plan_t0 = time.time()
    _plan_calls = 0
    try:
        # D1：多步本地勘察任务在 Planner 之前确定性截获。Planner 的自由文本
        # 步骤无法可靠在「客户端 local_file_* / 进程内委派」间选通道（验收
        # 实测会给 F 盘授权工作区错规划客户端工具）。路由确认授权工作区后
        # 直接执行委派，云端只做一次收尾；失败透明回退下面的规划链路。
        # D2：每轮先复位路由锁（请求线程复用防串），下方 PC 旁路再按本轮
        # 目标路径决定是否上锁。
        try:
            from delegation import bridge as _dbr
            _dbr.lock_delegation(False)
            _dpre = _dbr.run_hybrid_delegation_direct(text, src=src)
        except Exception as _e:
            _dpre = {"ok": False, "reason": "precheck:%s" % type(_e).__name__}
        if _dpre.get("ok"):
            _dres = _dpre["result"]
            _prompt = (
                "（系统提示：本地子任务已在授权工作区内完成勘察，下面是经过程序"
                "核验的证据。请直接基于证据回答用户：指出结论与依据；证据不足或"
                "子任务标记低置信度处照实说明，不要编造文件/行号。）\n\n"
                "子任务结果：\n%s\n\n用户原话：%s"
                % (_dpre["evidence_text"], text))
            ans, mode = _once(_prompt, no_tools=True)
            _restore_hist(_LOCAL["brain"], text, ans, mode)
            if trace_out is not None:
                trace_out["plan_steps"] = 0
                trace_out["route"] = "hybrid_delegate_preplan"
                trace_out["tool"] = "delegate_to_local:preplan"
                trace_out["plan_ms"] = int((time.time() - _plan_t0) * 1000)
                trace_out["plan_calls"] = 1
                trace_out["delegation"] = {
                    "status": _dres.get("status"),
                    "verified": bool(_dres.get("verified")),
                    "elapsed_seconds": _dres.get("elapsed_seconds")}
            import tools as _td
            print("[大脑] 规划前确定性委派完成（%s, verified=%s, %.0fs）"
                  % (_dres.get("status"), _dres.get("verified"),
                     _dres.get("elapsed_seconds") or 0), flush=True)
            return _td.TOOL_TAG_ANY_RE.sub("", ans or "").strip(), mode

        import planner
        _yes, _plan_why = planner.plan_decision(text)
        # 🔴 **关掉之后要单独记一笔，不能当成"规划失败"。**
        # `plan()` 自己在 enabled=false 时返回 None，于是 server 会走下面
        # "判要规划但没解析出步骤"那条分支、写 `plan: "empty"` ——
        # 那样"配置关了"会被记成"它失败了"，观测数据直接废掉。
        # 所以这里先判开关：关着就只记 `would`（本来会触发多少次），
        # 那个数就是"将来要不要重开"的依据。
        if _yes and not planner.enabled():
            planner.note(would=1, why=_plan_why)
            if trace_out is not None:
                trace_out["plan"] = "off"
                trace_out["plan_why"] = _plan_why
            _yes = False
        # D2：PC 在线且消息带明确本地路径（非实时信息意图）时，跳过 Planner
        # 直走原生 FC。Planner 的工具参数是自由文本（arg 是字符串），
        # 驱动不了 local_file_grep/read 这类多参数工具（实测会把 dict 文本
        # 和自然语言占位当路径）；FC 的结构化 tool_calls 才能正确带 path/
        # pattern，配合 D2 的通道描述选对 local_file_*。这是路由逻辑，不是
        # 提示词：pc_online() 与绝对路径都是运行事实。
        # 进一步：只有本轮路径**不在**委派沙箱授权根内（属客户端域）时才
        # 上路由锁——工具表整项摘除 delegate_to_local，验收实测 MiMo 会拿
        # 历史里的 F 盘工作区连派 3 次烧光 270s 预算；沙箱根内的任务不上锁，
        # 保持委派可达（preplanner 没拦的非勘察形态仍可由 FC 选择）。
        if _yes:
            try:
                import localtools as _lt2
                from delegation import router as _rt
                from delegation import bridge as _dbr2
                if (_lt2.pc_online() and _rt._ABS_PATH.search(text or "")
                        and not _rt._CLOUD_HINT.search(text or "")):
                    _cfg2 = _dbr2.load_delegation_config()
                    _sandbox_ws = _rt.find_authorized_workspace(
                        text or "", _cfg2.get("allowed_roots") or [])
                    if not _sandbox_ws:
                        _dbr2.lock_delegation(True)
                        planner.note(would=1,
                                     why=_plan_why + "|d2_pc_online_bypass_locked")
                        if trace_out is not None:
                            trace_out["plan"] = "bypass_pc_online_locked"
                            trace_out["plan_why"] = _plan_why
                        _yes = False
                        print("[规划] PC 在线客户端域路径，摘除委派工具，"
                              "跳过规划直走 FC(local_file_*)", flush=True)
            except Exception:
                pass
        if _yes:
            planner.note(decided=1, why=_plan_why)
            if trace_out is not None:
                trace_out["plan_why"] = _plan_why
        if _yes:
            steps = planner.plan(text, src=src)
            _plan_calls += 1
            if steps:
                b = _LOCAL["brain"]
                # The model-visible tool list is not an authorization boundary.
                # Re-read the channel-filtered active specs and pass them to the
                # executor, which intersects them with its own active-spec check.
                import tools as _tools
                _allowed_tools = []
                for _spec in _tools.all_specs(brain=b, src=src):
                    try:
                        _allowed_tools.append(_spec["function"]["name"])
                    except (KeyError, TypeError):
                        continue
                results = planner.execute_plan(
                    steps, src=src, allowed_tools=_allowed_tools, brain=b)
                # no_tools=True：工具在 planner.execute_plan 那一步已经查过了，
                # 收尾这步再挂一遍工具表，主模型会拿同样的参数再查一次 ——
                # 同一条消息两次执行、两次计费（路由模式没有这个问题，
                # 因为那时候主模型本来就不挂工具）。
                ans, mode = _once(planner.build_final_prompt(text, results),
                                  no_tools=True)
                _plan_calls += 1
                # 结果检查器（轻量监督）：回答有没有用上执行结果、有没有编造。
                # 最多修一次，防止「修完还不合格 → 再修」成死循环。
                verify_feedback = planner.verify_result(text, results, ans)
                _plan_calls += 1
                revised = False
                if verify_feedback:
                    ans, mode = _once(planner.build_revise_prompt(
                        text, results, ans, verify_feedback),
                                      no_tools=True)
                    _plan_calls += 1
                    revised = True
                    print("[大脑] 检查器发现问题，已修正回答", flush=True)
                _restore_hist(b, text, ans, mode)
                _plan_ms = int((time.time() - _plan_t0) * 1000)
                planner.note(ok=1, steps_total=len(steps), ms_total=_plan_ms,
                             calls_total=_plan_calls,
                             revised=1 if revised else 0,
                             kind="ok")
                if trace_out is not None:
                    trace_out["plan_steps"] = len(steps)
                    trace_out["revised"] = revised
                    # `plan_final_no_tools`：收尾那一步是**不挂工具表**的。
                    # 这是规划路径已知的短板（收尾时想再调一次工具也调不了），
                    # 记下来才能数"有多少轮其实收尾还需要工具"。
                    trace_out["plan_final_no_tools"] = True
                    trace_out["plan_ms"] = _plan_ms
                    trace_out["plan_calls"] = _plan_calls
                    _used = [t for _, t, _, _ in results if t]
                    if _used:
                        trace_out["tool"] = ",".join(_used)
                print("[大脑] Plan-and-Execute 完成，%d 步%s" % (
                    len(steps), "（已修正）" if revised else ""), flush=True)
                import tools as _t2
                return _t2.TOOL_TAG_ANY_RE.sub("", ans or "").strip(), mode
            # 判要规划，但一步都没解析出来 —— 回退直答。**必须留痕**
            _plan_ms = int((time.time() - _plan_t0) * 1000)
            planner.note(empty=1, ms_total=_plan_ms, calls_total=_plan_calls,
                         kind="empty")
            if trace_out is not None:
                trace_out["plan"] = "empty"
                trace_out["plan_ms"] = _plan_ms
                trace_out["plan_calls"] = _plan_calls
            print("[大脑] 规划没解析出步骤，回退直答", flush=True)
    except _Busy:
        raise
    except Exception as e:
        _plan_ms = int((time.time() - _plan_t0) * 1000)
        try:
            planner.note(error=1, ms_total=_plan_ms, calls_total=_plan_calls,
                         kind="error", err=str(e)[:80])
        except Exception:
            pass
        if trace_out is not None:
            trace_out["plan"] = "error"
            trace_out["plan_ms"] = _plan_ms
            trace_out["plan_calls"] = _plan_calls
        print("[大脑] Plan-and-Execute 异常，回退直答：%s" % str(e)[:100],
              flush=True)


    # 默认 ReAct：大脑内部原生 FC 循环一次搞定，外层不再跑标签解析
    ans, mode = _once(text)
    import tools as _tools
    return _tools.TOOL_TAG_ANY_RE.sub("", ans or "").strip(), mode


# ── M3 双大脑路由（wechat_route = cloud | local）────────────────────
# cloud（默认）：微信消息由云上她自己答 —— 现状，随时可回。
# local：微信消息转发给用户电脑上的本地大脑（经 /ws/brain 长连），
#        本地不在线就落回云端直答，本地 25s 没答就用超时话术顶着。
# flag 是运行时热改的（POST /api/route 秒切 + 落盘），为的是"一键切回"。
_TIMEOUT_REPLY = "（我这边的脑子好像掉线了，消息先记下了，等会儿回来补你～）"


def _wechat_route():
    try:
        v = (API_CFG or {}).get("wechat_route")
        return v if v in ("cloud", "local") else "cloud"
    except Exception:
        return "cloud"


def _set_wechat_route(v):
    """热改 + 落盘。落盘照 /api/persona/apply 的写法：整份读出、改键、写回。"""
    if v not in ("cloud", "local"):
        raise ValueError("wechat_route 只能是 cloud | local")
    API_CFG["wechat_route"] = v
    cfg = {}
    try:
        cfg = json.load(open(config_path(), encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg["wechat_route"] = v
    with open(config_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _late_to_history(src, display, reply):
    """本地大脑的迟到答案：话术已经顶出去了，这条**只写历史不外发** ——
    她事后翻聊天记录能看到自己真正回了什么，微信端却不会冒出一条迟到的
    复读。挂到 ws_forward.HUB.on_late_result（run_server 装配完大脑后接上）。"""
    try:
        _LOCAL["brain"].use_src(_norm_src(src))
        _LOCAL["brain"]._log_turn(display, reply, img=None)
        print("[转发] 迟到答案已入历史（src=%s）" % src, flush=True)
    except Exception as e:
        print("[转发] 迟到答案写历史失败：%s" % e, flush=True)


def ask_with_retry(text, img_b64=None, display=None, trace_out=None, src=None,
                   dry_run=False):
    """让她回一句话。

    img_b64：这一轮带的图片（base64），交给视觉模型让她"看见"。
    display：存档/给用户看的那份原文（不含系统注入的提示），None 就用 text。
    trace_out：可选 dict；填上就在**持锁时**抄下这轮的 trace 字段（见 _snapshot）。
    src：渠道（app / wechat）。决定读哪份人设、哪份对话历史、哪个记忆分区 ——
    不传就是主渠道（手机 App / 桌宠）。
    dry_run：**不写聊天存档、也不改历史**。给"用 API 打探测句"用。

    🔴 **dry_run 是踩了坑才加的。** 我调试时用 `/api/chat` 直接打了一串探测句
    （"嘉豪""我买了新键盘""哈基米到底什么梗"…），它们**全进了她的正式聊天记录**。
    于是她"最近在聊什么"看到的是我的一串测试题 —— 群里真有人说话时，
    她答的是上一条测试句（实测同一句"嘉豪"六次里有一次回的是"新键盘？啥轴的"）。
    用户看到的现象是"她回复一点都不对、答非所问"。清了两遍记录才恢复。

    所以：**任何从接口打进去的探测句都会污染她的上下文。** 要测就带 dry_run。
    """
    orig_text = text if display is None else display
    # 她就在本进程里，直接问，不走网络
    if _LOCAL["brain"] is not None:
        try:
            img_path = ""
            if img_b64:
                try:
                    import vision
                    desc = vision.describe_bytes(
                        base64.b64decode(img_b64), API_CFG)
                    if desc:
                        text = (text + " " if text else "") + \
                            f"（他发的图：{desc}）"
                    else:
                        # 🔴 看不了图也要**说一声**，不能什么都不加。
                        # 人设规则里明确分了两档：带"（他发的图：…）"就是看到了，
                        # 写着"没看清"就是真看不到 —— 让她老实说看不到、别编。
                        # desc 为空时若直接跳过，提示词里既没有描述也没有
                        # "没看清"，她**根本不知道他发过图**，只会当没这回事；
                        # 而线上一次日志都不打，看起来像"她看不懂图"。
                        text = (text + " " if text else "") + "（他发了张图片，没看清）"
                        print("[大脑] 看图失败（视觉模型没返回描述），已按"
                              "'没看清'告诉她", flush=True)
                except Exception as e:
                    print(f"[大脑] 独立模式看图失败（不影响回复）：{e}", flush=True)
                # 把原图存档：聊天记录里留路径，App 重新进来还能看到图
                try:
                    from paths import UPLOAD_DIR
                    d = str(UPLOAD_DIR)
                    os.makedirs(d, exist_ok=True)
                    img_path = os.path.join(
                        d, "u_" + time.strftime("%Y%m%d_%H%M%S") + ".jpg")
                    with open(img_path, "wb") as f:
                        f.write(base64.b64decode(img_b64))
                    # 收藏群聊表情包：入站图片顺手入库（仅微信群渠道）。
                    # desc 直接用刚算好的视觉描述 —— 看图那步本来就有，
                    # 零额外调用；save_bytes 按内容哈希去重，重复收藏无害。
                    try:
                        if src and str(src).startswith("wechat"):
                            import stickers as _stk
                            _stk.save_bytes(base64.b64decode(img_b64),
                                            desc=str(desc or ""),
                                            src=str(src))
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[大脑] 图片存档失败（不影响回复）：{e}", flush=True)
            # 他要自拍：不指望模型"只打字不发图"，直接按她此刻的时间和地点
            # 现场生成一张，正文用第一人称短句
            try:
                _b = _LOCAL["brain"]
                if src:
                    _b.use_src(src)     # 存档走哪份文件，取决于这轮是哪个渠道
                if not img_b64 and _looks_like_selfie(text):
                    sp, place = _selfie_prompt()
                    name = _cloud_gen_selfie(sp, negative_prompt=GEN_NEGATIVE,
                                             size="768x1024")
                    if name:
                        ans = _selfie_caption(place) + "\n[img:" + name + "]"
                        if not dry_run:          # 探测轮不写存档（见 ask_with_retry）
                            try:
                                _b._log_turn(orig_text, ans, img=None)
                            except Exception as e:
                                print(f"[大脑] 自拍存档失败（不影响回复）：{e}",
                                      flush=True)
                        return ans, "本地自拍", ""
            except Exception as e:
                print(f"[大脑] 自拍生成失败，回退正常对话：{e}", flush=True)
            # 锁在 _chat_with_tools 的每轮 chat 里拿（带超时），别抱着锁等网络
            ans, mode = _chat_with_tools(text, img_path or None,
                                         trace_out=trace_out, src=src)
            # 剥掉学出来的「说」标记；[gen:xxx] 换成 [img:名字]
            look = _her_look()
            ans = resolve_gen_tags(strip_say_marker(ans), look)
            ans = resolve_rand_tags(ans, text)   # [rand:分类] 表情包/趣图
            ans = resolve_voice_tag(ans, API_CFG)
            # 插件的回复加工（on_reply）：核心那三道加工之后。
            # 放最后是有意的：插件拿到的已经是"能直接发出去"的文本
            # （[img:] 已是文件名、[voice:] 已是语音条），不用各自去解标记。
            # 炸了的插件已被 plugin_host 挡掉，这里拿到的永远是字符串。
            try:
                import plugin_host
                ans = plugin_host.on_reply(
                    plugin_host.Turn(text=text, src=src, api_config=API_CFG,
                                     brain=_LOCAL["brain"]), ans)
            except Exception as e:
                print(f"[插件] on_reply 跳过（不影响回复）：{e}", flush=True)
            # 存档记处理后的正文（带 [img:gen_xxx.png]，App 能直接显示）。
            # 用户侧只存他真正说的话，看图描述和快递提示别进他的气泡
            # dry_run 时**不存** —— 探测句混进她的聊天记录会污染她的上下文，
            # 症状是"她答的是上一条测试句"（见 ask_with_retry 的说明）。
            if not dry_run:
                try:
                    _LOCAL["brain"]._log_turn(orig_text, ans, img=img_path or None)
                except Exception as e:
                    print(f"[大脑] 存档失败（不影响回复）：{e}", flush=True)
            return ans, mode, ""
        except _Busy:
            # 排队超时：明确说出来，别让调用方无限期等
            return "", "忙碌", "她正在回上一条，几秒后再发一次就好"
        except Exception as e:
            return "", "", f"{type(e).__name__}: {e}"

    # 理论上到不了这里（run_server 一定会先装配大脑），但别让调用方拿到 None
    return "", "", "大脑没起来"


# split_text / split_messages 搬进了 brain.py，/api/chat 的 messages 和存档共用一份
from brain import split_messages


def _query_param(path, key):
    from urllib.parse import urlparse, parse_qs
    try:
        q = parse_qs(urlparse(path).query)
        return (q.get(key) or [""])[0]
    except Exception:
        return ""


def _stats_today():
    """今天的用量汇总（App 设置页显示"今天聊了多少轮、花了多少 token"）。"""
    today = time.strftime("%Y-%m-%d")
    rows = [r for r in _trace_tail(4000) if str(r.get("t", "")).startswith(today)]
    tok_in = sum(int(r.get("tok_in") or 0) for r in rows)
    tok_out = sum(int(r.get("tok_out") or 0) for r in rows)
    ms = [int(r.get("ms") or 0) for r in rows if r.get("ms")]
    mem_hit = sum(1 for r in rows if int(r.get("mem_hits") or 0) > 0)
    out = {
        "date": today,
        "turns": len(rows),
        "tok_in": tok_in,
        "tok_out": tok_out,
        "fallback": sum(1 for r in rows if r.get("fallback")),
        "mem_hit_turns": mem_hit,
        "avg_ms": int(sum(ms) / len(ms)) if ms else 0,
        "last_err": "",
    }
    try:
        b = _LOCAL.get("brain")
        out["last_err"] = (getattr(b, "last_err", "") or "") if b else ""
        out["model"] = str((API_CFG or {}).get("model") or "")
    except Exception:
        pass
    try:
        import voice
        out["voice"] = voice.describe(API_CFG)
    except Exception:
        pass
    return out






def _cloud_history(limit=60, src="wechat"):
    """最近 N 条聊天记录（t / role / text），给客户端历史页用。

    缺省主渠道 wechat（app 已退役）。按渠道读不同文件 —— 群聊里不该看见
    私聊那份对话。

    🔴 分区名必须跟 brain._log_for 算出来的一致（人设 × 渠道），不能只按渠道拼。
    两边各算一套的后果：群友人设在群里聊的对话被写进
    preset_group.app.chat_history.jsonl（按渠道拼的名字），而这里读的也是那份
    —— 看着「读到了」，读到的是错人设的对话。
    """
    try:
        import paths
        from persona_store import key_from_config
        pk = _persona_for_src(_norm_src(src), key_from_config(API_CFG))
        # 🔴 用不带渠道的基础名再 scoped：chat_log_for('wechat') 已经带了
        # ".wechat" 后缀，再 scoped 一次会拼出 chat_history.wechat.wechat.jsonl
        # 这种双后缀名 —— 跟 brain._log_for 同一个毛病，一并对齐成基础名。
        base = os.path.join(paths.DATA_DIR, "chat_history.jsonl")
        p = paths.scoped(base, pk, _norm_src(src))
    except Exception:
        p = data_file("chat_history.jsonl"
                      if _norm_src(src) == "app" else
                      "chat_history.%s.jsonl" % _norm_src(src),
                      r"C:\vigil\data\chat_history.jsonl")
    items = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                img = d.get("img") or ""
                if img:
                    img = os.path.basename(img.replace("\\", "/"))
                items.append({"t": d.get("t") or "", "role": d.get("role") or "",
                              "text": d.get("text") or "",
                              "img": img})
    except OSError:
        return []
    # 🔴 入参必须夹紧。直接 int(limit) 的写法：
    #   limit=abc → ValueError 冒到 do_GET，而 do_GET 没有兜底 except →
    #               连接直接断掉，**客户端收不到任何响应**（只有一个 traceback）
    #   limit=0   → items[-0:] 等于 items[0:]，**把整个存档全返回**
    #   负数      → 从开头切，同样不是调用方想要的
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 60
    n = max(1, min(n, 1000))
    return items[-n:]


def reload_plugins_now():
    """重扫 plugins/ 并重拼提示词。返回 (插件数, 是否需要重启大脑)。

    装/卸/改开关之后都要走这里 —— 少走一步就会出现
    "界面显示装上了、实际那轮对话里没有它"。
    模块级函数：路由和同步客户端（sync_client 拉到行为配置/人设变化后）
    两个入口共用，别留两份实现。
    """
    try:
        n = load_plugins(verbose=True)
    except Exception:
        n = 0
    need = False
    try:
        import plugin_host as _ph
        need = any(callable(p.hook("start"))
                   for p in _ph.all_plugins().values())
    except Exception:
        pass
    b = _LOCAL.get("brain")
    # 提示词里的工具清单是构造时拼的，重装后得重拼
    if b is not None:
        try:
            b.reload_persona()
        except Exception:
            pass
    return n, need


def _sync_route_get(path, qp):
    """同步 GET 路由（manifest / 记忆增量 / 人设清单 / 行为段）。

    拆成独立函数而不是塞进 handler 分支链：本地 exe 也开这套接口，
    e2e 对两个实例做断言时用的是同一段代码，口径一致。
    """
    import memory_store_v2 as ms
    if path == "/api/sync/manifest":
        import persona_store
        import sync_client
        stores = []
        for name in ms.list_store_names():
            st = ms.open_store_by_name(name)
            if st is None:
                continue
            try:
                stores.append(st.ledger())
            except Exception:
                continue
        beh = sync_client.behavior_snapshot()
        return 200, {"ok": True, "stores": stores,
                     "personas": persona_store.sync_snapshot(),
                     "behavior": {"version": sync_client.behavior_version(beh)}}
    if path == "/api/sync/memories":
        st = ms.open_store_by_name(qp.get("store"))
        if st is None:
            return 400, {"ok": False, "err": "store 不合法"}
        try:
            after = int(qp.get("after_seq") or 0)
        except (TypeError, ValueError):
            after = 0
        try:
            limit = int(qp.get("limit") or 500)
        except (TypeError, ValueError):
            limit = 500
        d = st.changes_after(after, limit=max(1, min(limit, 5000)))
        d["ok"] = True
        return 200, d
    if path == "/api/sync/personas":
        import persona_store
        d = persona_store.sync_snapshot()
        d["ok"] = True
        return 200, d
    if path == "/api/sync/status":
        import sync_client
        d = sync_client.status()
        d["ok"] = True
        return 200, d
    if path == "/api/sync/behavior":
        import sync_client
        data = sync_client.behavior_snapshot()
        return 200, {"ok": True, "version": sync_client.behavior_version(data),
                     "data": data}
    return None


def _sync_route_post(path, body):
    """同步 POST 路由（推送：记忆批量 / 人设 / 人设删除 / 行为段）。全部幂等。

    🔴 权威源语义：这几条是"本地 → 云端"的推入口，云端自己**不开**同步
    客户端（sync_client.cloud() 在云端是 None），所以这里收到什么就按
    账本规则应用什么，不会再转发出去 —— 没有回声环。
    """
    import memory_store_v2 as ms
    if path == "/api/sync/memories":
        st = ms.open_store_by_name((body or {}).get("store"))
        if st is None:
            return 400, {"ok": False, "err": "store 不合法"}
        items = (body or {}).get("items") or []
        deletes = (body or {}).get("deletes") or []
        if not isinstance(items, list) or not isinstance(deletes, list):
            return 400, {"ok": False, "err": "items/deletes 必须是数组"}
        # 单笔上限：一次推几万条不是同步，是攻击
        if len(items) > 2000 or len(deletes) > 2000:
            return 413, {"ok": False, "err": "批量太大，分批推"}
        stats = st.upsert_many(items, deletes)
        stats["ok"] = True
        stats["store"] = st.name
        stats["last_seq"] = st.ledger()["last_seq"]
        return 200, stats
    if path == "/api/sync/personas":
        import persona_store
        status, ver = persona_store.push_apply((body or {}).get("key"),
                                               (body or {}).get("fields"),
                                               (body or {}).get("version"))
        return 200, {"ok": status in ("applied", "same"), "status": status,
                     "version": ver}
    if path == "/api/sync/personas/del":
        import persona_store
        k = str((body or {}).get("key") or "")
        try:
            v = int((body or {}).get("version") or 0)
        except (TypeError, ValueError):
            v = 0
        if not k or v <= 0:
            return 400, {"ok": False, "err": "key/version 必填"}
        cur = int(persona_store.deleted_versions().get(k) or 0)
        if v < cur:
            return 200, {"ok": True, "status": "same", "version": cur}
        persona_store.record_deleted(k, v)
        try:
            os.remove(persona_store.path_of(k))
        except OSError:
            pass
        return 200, {"ok": True, "status": "applied", "version": v}
    if path == "/api/sync/behavior":
        import sync_client
        data = (body or {}).get("data")
        if not isinstance(data, dict):
            return 400, {"ok": False, "err": "data 必须是对象"}
        cur = sync_client.behavior_version(sync_client.behavior_snapshot())
        incoming = sync_client.behavior_version(data)
        if incoming == cur:
            return 200, {"ok": True, "status": "same",
                         "version": cur}       # 重推：内容一样，什么都不做
        if not sync_client.behavior_apply(data):
            return 500, {"ok": False, "err": "写配置失败"}
        # 行为变了，本进程的插件开关缓存/提示词跟着刷新（2.4 的 reload 钩子）
        try:
            reload_plugins_now()
        except Exception:
            pass
        return 200, {"ok": True, "status": "applied", "version": incoming}
    return None


def start_remote_api(brain):
    """给"远程桌宠"和手机 App 开的门：客户端是薄壳，经隧道/直连用云上的大脑
    聊天 —— 记忆 / 人设只有云上一份，天然同步。

    只绑 127.0.0.1 是默认值（公网碰不到）；要让手机直连得显式配
    bind_host: "0.0.0.0"，那时必须配 brain_token（没配就拒绝启动，见函数末尾）。
    同一时间只允许一个进程养她。
    """
    tok = BRAIN_TOKEN
    # 8788 是云端默认；sidecar（PyInstaller 打包的本地大脑）由 server_launch
    # 选好空闲端口后经 VIGIL_PORT 注入 —— 云端不设这个变量，行为不变。
    port = int(os.environ.get("VIGIL_PORT") or 8788)

    class _Handler(http.server.BaseHTTPRequestHandler):
        def _check(self):
            """鉴权（判定逻辑见模块级 _auth_ok：那边是纯函数，有测试盯着）。

            设备 token 是**叠加**上来的一条通道：先按老规矩比 brain_token，
            不匹配再当设备 token 查设备表 —— 老客户端一行都不用改。
            通过后记下 `self._who`，因为有的接口要区分
            "主人钥匙"（能管所有设备）和"某台设备"（只能解绑自己）。
            """
            try:
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                qtok = (q.get("token") or [""])[0]
            except Exception:
                qtok = ""
            auth = self.headers.get("Authorization") or ""
            if _auth_ok(auth, qtok, tok, self.client_address[0]):
                self._who = ("brain", "")
                return True
            got = str(auth or "")
            if got.startswith("Bearer "):
                got = got[7:]
            if not got:
                got = str(qtok or "")
            d = None
            if got.startswith("dt_"):
                try:
                    import devices
                    d = devices.verify(got, self.client_address[0])
                except Exception:
                    d = None
            if d:
                self._who = ("device", d.get("device_id", ""))
                return True
            self._who = ("", "")
            return False

        def _is_admin(self):
            """这个请求能不能动管理面：主人钥匙，或 admin 角色的设备。

            角色每请求查一次设备表（内存缓存，开销可忽略）：设备被吊销或
            角色被降档后，下一个请求立即生效。
            """
            who, who_id = getattr(self, "_who", ("", ""))
            if who == "brain":
                return True
            if who == "device":
                return _device_role(who_id) == "admin"
            return False

        def _deny_if_limited_device(self, method, path):
            """受限设备的默认拒绝闸门。命中拦截返回 True（已回 403）。"""
            who = getattr(self, "_who", ("", ""))[0]
            if who != "device" or self._is_admin():
                return False
            if _device_path_allowed(method, path):
                return False
            self._json(403, {"ok": False,
                             "err": "设备 Token 无权访问这个接口（需要管理员授权）"})
            return True

        def _cors(self):
            """跨域头：管理仪表盘从 file:// 或其他源访问时需要。
            鉴权靠 token 兜底，CORS 只放开浏览器侧的读取限制。"""
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods",
                             "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers",
                             "Content-Type, Authorization")
            self.send_header("Access-Control-Max-Age", "86400")

        def _json(self, code, out):
            data = json.dumps(out, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type",
                             "application/json; charset=utf-8")
            self._cors()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):
            """CORS 预检：浏览器发带 Authorization 的请求前会先问一声。"""
            self.send_response(200)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _plugin_reload_now(self):
            return reload_plugins_now()

        def _plugin_admin(self, method, path, body):
            """插件管理面：装/卸/渠道开关/清单。命中返回 True（已回包）。

            为什么不写进 do_POST 的 elif 链：那条链已经有六十多个分支，
            再往里塞四个"装机"分支，下一个来改的人就得在一屏缩进里找。
            整块收进独立方法，链上只留一个调用点。
            """
            if not path.startswith("/api/plugins"):
                return False
            # 装/卸/重载/渠道开关 = 在服务器上放置并执行代码，只允许管理员。
            # GET（清单/探针/前端资源）任何已认证身份都能读：前端资源是
            # 管理员已审核安装后的产物，真正的入口（install）已经在这里卡死。
            if method == "POST" and not self._is_admin():
                self._json(403, {"ok": False,
                                 "err": "插件安装/卸载/重载只允许管理员"})
                return True
            try:
                import plugin_ops as _po
            except Exception as e:
                self._json(500, {"ok": False, "err": "插件管理模块不可用：%s"
                                 % str(e)[:80]})
                return True

            # 渠道列表只有一个真源：paths.CHANNELS。以后加第三个渠道
            # （比如网页版）这里自动跟上，不用改这个文件。
            try:
                import paths
                chans = list(paths.CHANNELS)
            except Exception:
                chans = ["wechat", "wechat_group", "pc"]

            if method == "GET":
                # /api/plugins 已经被微信桥接和 VM 启动器当"大脑活着吗"
                # 的探针（只看 200），plugins 键保持原样别动。
                # 管理视图另起一个键 —— 老消费者读不到新东西，新页面
                # 不必知道那个键的存在。
                if path == "/api/plugins":
                    try:
                        import plugin_host as _ph
                        self._json(200, {
                            "plugins": _ph.listing(),
                            "manage": {
                                "channels": chans,
                                "items": _po.overview(chans),
                            },
                        })
                    except Exception as e:
                        self._json(200, {"plugins": [],
                                         "manage": {"channels": chans,
                                                    "items": []},
                                         "err": str(e)[:80]})
                    return True
                if path == "/api/plugins/frontend":
                    # 插件前端注入资源：返回所有带 frontend.js / frontend.css 的插件
                    # 版本号用时间戳，前端靠它判断是否需要热重载。
                    # 客户端 request() 已把响应体解析成 r.json，r.ok 是 HTTP 状态码，
                    # 所以这里直接返回 {version, plugins}，不要再包一层 {ok, json}。
                    try:
                        import plugin_host as _ph
                        self._json(200, _ph.frontend_assets())
                    except Exception as e:
                        self._json(200, {"version": 0, "plugins": [],
                                         "err": str(e)[:80]})
                    return True
                return False

            if method != "POST":
                return False

            if path == "/api/plugins/install":
                # zip 走 base64 塞在 JSON 里：能复用现成的 body 解析，
                # 不用在这个文件里另写一套 multipart（那是另一份能出 bug 的代码）。
                # 4MB 的包 base64 之后 5.6MB，还在 MAX_BODY 里。
                import base64
                raw = str(body.get("zip_b64") or "")
                if not raw:
                    self._json(400, {"ok": False, "err": "没收到插件包"})
                    return True
                if raw.startswith("data:"):          # 前端 FileReader 的原样
                    raw = raw.split(",", 1)[-1]
                try:
                    blob = base64.b64decode(raw, validate=True)
                except Exception:
                    self._json(400, {"ok": False, "err": "包是坏的（不是合法 zip）"})
                    return True
                import tempfile
                fd, fp = tempfile.mkstemp(prefix="zxplug_", suffix=".zip")
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(blob)
                    ok, res = _po.install(fp, note=str(body.get("source") or "")[:40])
                finally:
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
                if not ok:
                    self._json(400, {"ok": False, "err": res.get("err") or "装不上"})
                    return True
                # Installing files must not execute newly supplied code.
                # Explicit restart performs activation outside the transaction.
                import plugin_host as _ph
                self._json(200, {"ok": True, "installed": res,
                                 "count": len(_ph.all_plugins()), "restart_needed": True,
                                 "manage": {"channels": chans,
                                            "items": _po.overview(chans)}})
                return True

            if path == "/api/plugins/uninstall":
                ok, res = _po.uninstall(str(body.get("name") or ""),
                                        purge_data=bool(body.get("purge_data")))
                if not ok:
                    self._json(400, {"ok": False, "err": res.get("err") or "卸不掉"})
                    return True
                n, need = self._plugin_reload_now()
                self._json(200, {"ok": True, "uninstalled": res,
                                 "count": n, "restart_needed": need,
                                 "manage": {"channels": chans,
                                            "items": _po.overview(chans)}})
                return True

            if path == "/api/plugins/channels":
                # 传的是插件名而不是 FEATURE 键：界面上用户看得见的是插件名，
                # 让前端自己去查 FEATURE 等于把内部结构漏到前端去。
                name = str(body.get("name") or "")
                want = body.get("channels")
                want = want if isinstance(want, list) else []
                try:
                    import plugin_host as _ph
                    info = next((p for p in _ph.listing()
                                 if p.get("name") == name), None)
                except Exception:
                    info = None
                if not info:
                    self._json(404, {"ok": False, "err": "没这个插件"})
                    return True
                feat = str(info.get("feature") or "")
                if not feat:
                    # 不声明 FEATURE 是契约上的"永远开"，界面该置灰。
                    # 这里必须拒，不能"假装改了" —— 否则界面上能勾、
                    # 背后没写任何东西，用户以为关掉了其实一直开着。
                    self._json(400, {"ok": False,
                                     "err": "这个插件不声明 FEATURE，"
                                             "契约上永远开，没法按渠道关"})
                    return True
                bad = [s for s in want if s not in chans]
                if bad:
                    self._json(400, {"ok": False,
                                     "err": "不认识的渠道：%s" % ",".join(bad)[:60]})
                    return True
                if not _po.set_channels(feat, chans,
                                        {s: (s in want) for s in chans}):
                    self._json(500, {"ok": False, "err": "写不进配置"})
                    return True
                self._json(200, {"ok": True, "feature": feat,
                                 "channels": _po.channels_of(feat, chans),
                                 "manage": {"channels": chans,
                                            "items": _po.overview(chans)}})
                return True

            if path == "/api/plugins/reload":
                n, need = self._plugin_reload_now()
                try:
                    import plugin_host as _ph
                    listing = _ph.listing()
                except Exception:
                    listing = []
                self._json(200, {"ok": True, "count": n,
                                 "restart_needed": need,
                                 "plugins": listing})
                return True

            return False

        def _plugin_route(self, method, path, body):
            """插件的 HTTP 接口。命中返回 True（已经自己回过包）。

            处理函数签名统一 (body, query) -> dict；要发非 200 或纯文本就
            自己在 ROUTES 里拿 handler 之外的东西做（不鼓励）。查不到返回 False，
            调用方回 404。
            """
            try:
                import plugin_host
                tbl = plugin_host.route_table()
            except Exception:
                return False
            # 受限设备只能打 remote 插件那几条（在设备权限矩阵里），
            # 其余插件路由一律视为管理面：防止低权限凭据借插件路由越权。
            if not self._is_admin() and not _device_path_allowed(method, path):
                self._json(403, {"ok": False,
                                 "err": "设备 Token 无权访问这个插件接口"})
                return True
            for m, p, fn, owner in tbl:
                if m != method or p != path:
                    continue
                try:
                    from urllib.parse import urlparse, parse_qs
                    q = parse_qs(urlparse(self.path).query)
                    q = {k: (v[0] if v else "") for k, v in q.items()}
                    # 已认证身份按请求注入（ContextVar），handler 据此做
                    # 设备归属校验；handler 签名保持 (body, query) 不变。
                    with plugin_host.use_principal(
                            getattr(self, "_who", ("", ""))):
                        out = fn(body or {}, q)
                    # 插件返回 {"__file__": 路径, "__mime__": 类型} 时，
                    # 直接回传文件二进制（视频/图片等静态资源）。
                    if isinstance(out, dict) and "__file__" in out:
                        fpath = out["__file__"]
                        mime = out.get("__mime__", "application/octet-stream")
                        try:
                            from plugin_files import send_file
                            send_file(self, fpath, mime)
                        except (BrokenPipeError, ConnectionResetError):
                            pass  # 切换壁纸/拖动进度时客户端会取消旧请求。
                        except Exception as fe:
                            self._json(404, {"ok": False, "err": str(fe)[:80]})
                        return True
                    if not isinstance(out, dict):
                        out = {"ok": True, "result": out}
                    self._json(int(out.pop("_code", 200)), out)
                except Exception as e:
                    print(f"[插件] {owner} 的 {path} 出错："
                          f"{type(e).__name__}: {e}", flush=True)
                    self._json(500, {"ok": False, "err": str(e)[:120]})
                return True
            return False

        def do_POST(self):
            # Agent 心跳使用独立凭证，不能借用主人 Token / 设备 Token。
            # 仅此路由绕过通用鉴权；management.heartbeat 会按 node_id 校验独立令牌。
            if self.path.split("?")[0] == "/api/management/heartbeat":
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except Exception:
                    n = 0
                if n <= 0 or n > 64 * 1024:
                    self._json(413 if n > 64 * 1024 else 400,
                               {"ok": False, "err": "心跳内容为空或超过 64 KB"})
                    return
                try:
                    payload = json.loads(self.rfile.read(n).decode("utf-8"))
                except Exception:
                    self._json(400, {"ok": False, "err": "JSON 格式错误"})
                    return
                try:
                    import management as _management
                    code, out = _management.heartbeat(payload, self.headers.get("Authorization") or "")
                    self._json(code, out)
                except Exception as e:
                    print("[管理] 心跳处理失败：%s: %s" % (type(e).__name__, e), flush=True)
                    self._json(500, {"ok": False, "err": "心跳处理失败"})
                return
            if not self._check():
                self._json(401, {"err": "bad token"})
                return
            note_client_ip(self.client_address[0])
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except Exception:
                n = 0
            # 带图的 /api/chat 走 base64，正常也就几 MB。早先 n 多大就读多大，
            # 一个超大 Content-Length 就能把 2C2G 打满。
            if n > MAX_BODY:
                self._json(413, {"err": "body too large"})
                return
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            except Exception:
                body = {}
            path = self.path.split("?")[0]
            # query 也要给 POST 用：Electron 客户端把渠道 src 放在 query 里
            # （?src=wechat），body 里没有。不解析的话渠道设置页的改动
            # 会全写进 app 覆盖 —— 渠道错乱。
            try:
                from urllib.parse import parse_qs as _pqs, urlparse as _up
                qp = {k: v[0] for k, v in _pqs(_up(self.path).query).items()}
            except Exception:
                qp = {}
            # 受限设备（role=device）的默认拒绝闸门：不在权限矩阵里的一律 403。
            # 管理类接口的管理员要求在各自分支里再判（双重保险）。
            if self._deny_if_limited_device("POST", path):
                return
            if path.startswith("/api/agent-center/"):
                import codex_adapter as _codex_center
                code, out = _codex_center.handle_http(
                    "POST", path, body, qp, is_admin=self._is_admin(),
                    is_local=_is_loopback(self.client_address[0]))
                self._json(code, out)
                return
            try:
                if path == "/api/device/register":
                    # 只能用"主人钥匙"注册：设备 token 不能自己给自己发新设备，
                    # 否则丢一台等于丢全部。回环 + 没配 token 的情况算主人。
                    if getattr(self, "_who", ("", ""))[0] != "brain":
                        self._json(403, {"ok": False,
                                         "err": "注册需要用 brain_token，不能用设备 token"})
                        return
                    import devices
                    out = devices.register(body.get("device_name"),
                                           body.get("device_type") or "desktop",
                                           self.client_address[0],
                                           body.get("capabilities"),
                                           role=body.get("role") or "device")
                    if not out:
                        self._json(500, {"ok": False,
                                         "err": "设备表写盘失败，注册没生效，重试一次"})
                        return
                    print("[设备] 新绑定：%s（%s，角色 %s）"
                          % (out["device_name"], out["device_id"],
                             out.get("role", "device")), flush=True)
                    out["ok"] = True
                    self._json(200, out)
                    return
                if path == "/api/device/list":
                    import devices
                    me = getattr(self, "_who", ("", ""))
                    self._json(200, {"ok": True, "items": devices.list_all(),
                                     "me": me[1] if me[0] == "device" else "",
                                     "who": me[0]})
                    return
                if path == "/api/device/audit":
                    # M4：本地操控的操作日志（谁在哪个渠道动了什么、结果）。
                    # 只给主人钥匙 —— 设备 token 看别的设备的日志等于越权。
                    who = getattr(self, "_who", ("", ""))[0]
                    if who != "brain":
                        self._json(403, {"ok": False,
                                         "err": "只有主人钥匙能看操作日志"})
                        return
                    import localtools as _lt
                    self._json(200, {"ok": True, "items": _lt.audit()})
                    return
                if path == "/api/memory/edit":
                    # M5 记忆管理：改一条记忆的文本。写操作只允许管理员。
                    if not self._is_admin():
                        self._json(403, {"ok": False,
                                         "err": "记忆编辑只允许管理员"})
                        return
                    # 底层 edit 自带 outbox 钩子（本地记 memory_edit 上云；
                    # 云端是权威源，sync_outbox 没启用时 record 是 no-op）——
                    # 两端共用这一份代码，同步方向自动正确。
                    import memory_store_v2 as _ms
                    st = _ms.open_store_by_name(
                        str(body.get("store") or "memory"))
                    if st is None:
                        self._json(400, {"ok": False, "err": "分区名不合法"})
                        return
                    it = st.edit(str(body.get("id") or ""),
                                 str(body.get("text") or ""))
                    if it is None:
                        self._json(404, {"ok": False,
                                         "err": "没找到这条记忆，或新文本为空"})
                        return
                    self._json(200, {"ok": True, "item": it})
                    return
                if path == "/api/memory/delete":
                    # M5 记忆管理：删一条。写操作只允许管理员。
                    if not self._is_admin():
                        self._json(403, {"ok": False,
                                         "err": "记忆删除只允许管理员"})
                        return
                    # 底层 delete 自带墓碑 + outbox
                    # （本地删除要上云同步，不然增量拉取会把它复活）。
                    # 幂等：删不存在的 id 也返回 ok（REST DELETE 惯例）。
                    import memory_store_v2 as _ms
                    st = _ms.open_store_by_name(
                        str(body.get("store") or "memory"))
                    if st is None:
                        self._json(400, {"ok": False, "err": "分区名不合法"})
                        return
                    iid = str(body.get("id") or "").strip()
                    if not iid:
                        self._json(400, {"ok": False, "err": "没给 id"})
                        return
                    st.delete(iid)
                    self._json(200, {"ok": True})
                    return
                if path == "/api/sync/flush":
                    # M5 客户端的「立即同步」按钮：把 outbox 存货推上去 +
                    # 拉一轮云端增量。云端自己（权威源）调用时 cloud()
                    # 返回 None，两个函数都是 no-op —— 同样一份代码两端安全。
                    import sync_client as _sc
                    import sync_outbox as _so
                    pushed = _sc.flush_outbox_once()
                    pulled = _sc.pull_once()
                    self._json(200, {"ok": True, "pushed": pushed,
                                     "pulled": pulled,
                                     "pending": _so.pending_count()})
                    return
                if path == "/api/device/revoke":
                    import devices
                    did = str(body.get("device_id") or "").strip()
                    who, mine = getattr(self, "_who", ("", ""))
                    if not did:
                        self._json(400, {"ok": False, "err": "没给 device_id"})
                        return
                    # 主人能吊销任何一台；设备只能吊销**自己**（解绑本机）。
                    # 否则任意一台设备就能踢掉其它设备 —— 那是提权。
                    if who != "brain" and did != mine:
                        self._json(403, {"ok": False, "err": "只能解绑本机"})
                        return
                    ok = devices.revoke(did)
                    self._json(200 if ok else 404,
                               {"ok": ok, "err": "" if ok else "没有这台设备"})
                    return
                if path == "/api/route":
                    # M3：一键切"微信消息谁答"。热改 + 落盘，秒生效不重启。
                    _v = str(body.get("wechat_route") or "")
                    try:
                        _set_wechat_route(_v)
                    except Exception as e:
                        self._json(400, {"ok": False, "err": str(e)[:80]})
                        return
                    import ws_forward as _fwd
                    print("[路由] wechat_route -> %s（本地在线：%s）"
                          % (_v, _fwd.HUB.online()), flush=True)
                    self._json(200, {"ok": True, "wechat_route": _v,
                                     "forward": _fwd.HUB.stats()})
                    return
                if path == "/api/stickers/collect":
                    # 表情包改版（2026-10-09）：桥接把聊天里**每张**图送进
                    # 图库（不再只有"触发对话的图"才入库）。云端按内容哈希
                    # 去重；新图才花一次视觉调用打标签 —— 收藏是副业，
                    # 不能每张图都白烧一次 vision。
                    _b64 = str(body.get("img") or "").strip()
                    _csrc = str(body.get("src") or "").strip()[:20]
                    if not _b64:
                        self._json(400, {"ok": False, "err": "没给 img"})
                        return
                    try:
                        _blob = base64.b64decode(_b64)
                    except Exception:
                        self._json(400, {"ok": False, "err": "img 不是 base64"})
                        return
                    import stickers as _stk
                    _name = _stk.save_bytes(_blob, src=_csrc)
                    if not _name:
                        self._json(200, {"ok": False, "err": "不是可识别的图片"})
                        return
                    # 没标签就打一次（新图、以及早期入库没打上标签的老图都
                    # 能借这条路补齐）。打过的直接复用，不重复烧 vision。
                    _desc = _stk.describe_of(_name)
                    _labeled = bool(_desc)
                    if not _desc:
                        try:
                            import vision as _vis
                            _desc = str(_vis.describe_bytes(_blob, API_CFG) or "")
                            if _desc:
                                _stk.set_desc(_name, _desc)
                                _labeled = True
                        except Exception as e:
                            print("[表情包] 打标签失败（不影响收藏）：%s"
                                  % str(e)[:80], flush=True)
                    self._json(200, {"ok": True, "name": _name,
                                     "new": bool(_labeled), "desc": _desc})
                    return
                if path == "/api/chat":
                    _in_text = str(body.get("text") or "")
                    _disp = _in_text          # 存档用原文，注入的系统提示不带
                    # 渠道：桌面客户端传 pc，微信桥接传 wechat/wechat_group。
                    # 缺省/认不出来的值归一到主渠道 wechat（app 已退役；
                    # 未知值同时也是防路径穿越，绝不能原样拼进存档文件名）。
                    _src = _norm_src(body.get("src"))
                    # 把渠道记进 features：同一个功能在 App 和群聊里该有不同的
                    # 开合（群聊里她不该报天气、不该把别人的单号加进你的监控）。
                    # 全局那一份仍是基线，这里只查 features_by_src 的覆盖。
                    try:
                        import features as _feat
                        _feat.set_current_src(_src)
                    except Exception:
                        pass
                    # M4：本地操控的身份上下文 —— 这条消息是谁、从哪个渠道
                    # 发的。localtools.call() 里的 clearance() 靠它判
                    # （sender_id 是微信端透传的发送者微信号；app/pc 渠道没有
                    # sender，clearance 对这些渠道天然放行——坐在电脑前的人）。
                    try:
                        import localtools as _lt
                        _lt.set_context(_src, str(body.get("sender_id") or ""))
                    except Exception:
                        pass
                    # ── M3 双大脑路由：微信渠道可转发给本地大脑 ──
                    # 拦截点必须在云端自己的注入链（快递/插件/记忆）之前：
                    # 本地大脑会跑**它自己的**全链路，两边都跑一遍就成了
                    # 双重注入（快递单号注册两次、插件提醒说两遍）。
                    # 只有 wechat / wechat_group 参与路由；app 渠道的客户端
                    # 连的就是云端，永远直答。离线 → 落到下面正常直答。
                    if _src in ("wechat", "wechat_group") \
                            and _wechat_route() == "local":
                        import ws_forward as _fwd
                        if _fwd.HUB.online():
                            _loc = _fwd.HUB.ask_local(
                                {"text": _in_text, "display": _disp,
                                 "src": _src, "img": body.get("img") or "",
                                 "dry_run": bool(body.get("dry_run"))},
                                timeout=_fwd.ACK_TIMEOUT)
                            if _loc.get("ok"):
                                _fans = _loc.get("reply") or ""
                                # 云端权威历史：本地答的这段也要写进云上存档
                                # （本地大脑只写它自己那一份）。dry_run 照旧不写。
                                if not body.get("dry_run"):
                                    try:
                                        _LOCAL["brain"].use_src(_src)
                                        _LOCAL["brain"]._log_turn(
                                            _disp, _fans, img=None)
                                    except Exception:
                                        pass
                                try:
                                    _trace_write({
                                        "t": time.strftime("%Y-%m-%d %H:%M"),
                                        "kind": "chat", "src": _src,
                                        "mode": "本地大脑",
                                        "in_len": len(_in_text)})
                                except Exception:
                                    pass
                                self._json(200, {
                                    "reply": _fans,
                                    "messages": split_messages(_fans),
                                    "mode": "本地大脑", "err": "",
                                    "t": time.strftime("%Y-%m-%d %H:%M"),
                                    "trace": {"mode": "本地大脑"}})
                                return
                            if _loc.get("reason") in ("timeout", "local_err"):
                                # 超时：话术顶着（**不写存档**——迟到的真答案
                                # 会由 on_late_result 补进历史，话术和真答
                                # 都入档就成了两条复读）。
                                _msg = _TIMEOUT_REPLY if _loc.get("reason") == "timeout" \
                                    else "（本地大脑出了点小差子：%s）" \
                                    % str(_loc.get("err") or "")[:60]
                                try:
                                    _trace_write({
                                        "t": time.strftime("%Y-%m-%d %H:%M"),
                                        "kind": "chat", "src": _src,
                                        "mode": "本地大脑超时",
                                        "in_len": len(_in_text)})
                                except Exception:
                                    pass
                                self._json(200, {
                                    "reply": _msg,
                                    "messages": split_messages(_msg),
                                    "mode": "本地大脑超时", "err": "",
                                    "t": time.strftime("%Y-%m-%d %H:%M"),
                                    "trace": {"mode": "本地大脑超时"}})
                                return
                            # reason=offline / send → 掉到下面云端直答
                    # 话里带单号就进监控（每 30 分钟自动查），现状也塞给模型，
                    # 她回话时能顺口说一句"刚发出去了"
                    try:
                        import features as _feat
                        if _feat.on("packages"):
                            import packages as _pkg
                            _tok = (API_CFG.get("uapi") or {}).get("token") or ""
                            _regs = [_pkg.register(no, _tok)
                                     for no in _pkg.extract_tracking(_in_text)]
                            _regs = [r for r in _regs if r]
                            if _regs:
                                _in_text += "\n（系统提示：检测到快递单号 " + "、".join(
                                    _pkg.status_line(r) for r in _regs) + \
                                    "。已加入监控，每30分钟自动查一次，有进展你会主动告诉他）"
                    except Exception:
                        pass
                    try:
                        _proactive_note_user()   # 他说话了：给自适应频率喂反馈
                    except Exception:
                        pass
                    # ── 群聊上下文（2026-10-09）─────────────────────────
                    # 桥接随消息透传最近几条群聊（body.context）。以前她被
                    # 叫到名字才抬头，看到的就是孤零零一句"除了贵"——接什么
                    # 都靠猜；闸门更惨（它找的 brain.recent_msgs 属性从来
                    # 不存在，永远"最近在聊：（没历史）"）。
                    # 拼进 _in_text：大脑看得到；**不进 _disp**：存档里她的
                    # 历史仍是一句一条，不污染（display 机制，见
                    # ask_with_retry）。注意：放快递注册之后 —— 前情里别人
                    # 贴的单号不该被注册进监控。
                    _ctx_for_gate = ""
                    _raw_ctx = body.get("context")
                    if isinstance(_raw_ctx, (list, tuple)):
                        _ctx_lines = [str(x).strip() for x in _raw_ctx
                                      if str(x).strip()]
                    elif isinstance(_raw_ctx, str) and _raw_ctx.strip():
                        _ctx_lines = [x.strip() for x in _raw_ctx.splitlines()
                                      if x.strip()]
                    else:
                        _ctx_lines = []
                    if _ctx_lines:
                        _ctx_lines = _ctx_lines[-12:]
                        _ctx_for_gate = "\n".join(_ctx_lines)
                        _in_text = ("[群聊前情，仅供你了解大家在聊什么，"
                                    "别逐条回应]\n%s\n[最新一条消息]\n%s"
                                    % (_ctx_for_gate, _disp))
                        print("[群聊上下文] 带了 %d 条前文" % len(_ctx_lines),
                              flush=True)
                    # 插件的入站钩子（on_turn_in）：各插件看一句他说了什么，
                    # 需要就返回一段要注入 system 的提示（提醒插件靠它把
                    # "手上还有哪些到点要喊的事"喂进上下文）。
                    # 拼在系统提示里而不是塞进 _in_text —— 后者会被当他说的话存档。
                    #
                    # 🔴 **否决靠 ctx.veto() 显式声明，不靠返回值**。
                    # 不能用"on_turn_in 返回空串 = 否决"，看着省事实际立刻炸：
                    # reminder 插件"没有提醒要注入"返回的也是空串，
                    # 而"所有插件都放行"时 join([]) 同样是空串 ——
                    # 三种情况在返回值上一模一样，于是"她一句话都说不出来"，
                    # 且每个插件单独看都正常。空串这个值没有信息量。
                    try:
                        import plugin_host as _ph
                        _tctx = _ph.Turn(text=_disp, src=_src,
                                         api_config=API_CFG,
                                         brain=_LOCAL["brain"])
                        if _ctx_for_gate:
                            # 闸门的上下文单独给，**不能并进 text** ——
                            # _called_someone_else 扫全文会把前文里别人
                            # 之间的 @ 也算成"这条在 @ 别人"，整条误拦。
                            _tctx.context = _ctx_for_gate
                        _extra = _ph.on_turn_in(_tctx)
                        if _tctx.vetoed:
                            print("[闸门] 这一轮被否决，不问大脑"
                                  "（src=%s 原因=%s）"
                                  % (_src, getattr(_tctx, "veto_reason", "")),
                                  flush=True)
                            self._json(200, {"reply": "", "mode": "闸门拦下",
                                             "gated": True})
                            return
                        if _extra:
                            _in_text += "\n" + _extra
                    except Exception:
                        # 钩子链自己炸了不能连累聊天 —— 当没否决，照常问大脑
                        pass
                    _snap = {}
                    ans, mode, err = ask_with_retry(
                        _in_text, body.get("img") or None, display=_disp,
                        trace_out=_snap, src=_src,
                        # 带 dry_run=true 的一轮**不写聊天存档**。
                        # 给"用接口打探测句"用：探测句混进她的记录会污染她的上下文，
                        # 她会开始答那些测试句（症状："她回复一点都不对"）。
                        dry_run=bool(body.get("dry_run")))
                    # 出错就留一行（欠费 402、模型名写错、地址变了都在这儿现形）
                    if err:
                        try:
                            errlog.warn("api/chat", f"{mode}：{err}")
                        except Exception:
                            pass
                    # 每轮一行 trace：记忆命中 / 块大小 / 延迟 / token。
                    # 字段取自 _snap（持锁时抄的），别在这里现读共享对象
                    try:
                        _ag = 0
                        try:
                            import agenda
                            _ag = 1 if agenda.block(
                                persona_key=_persona_for_src(_src),
                                src=_src) else 0
                        except Exception:
                            pass
                        _trace_write({
                            "t": time.strftime("%Y-%m-%d %H:%M"),
                            "kind": "chat",
                            "src": _src,
                            "in_len": len(_in_text),
                            "blocks": _snap.get("blocks") or {},
                            "mem_hits": _snap.get("mem_hits", 0),
                            "mem_top": _snap.get("mem_top") or [],
                            "agenda": _ag,
                            "model": _snap.get("model")
                            or str((API_CFG or {}).get("model") or ""),
                            "ms": _snap.get("ms"),
                            "tok_in": _snap.get("tok_in"),
                            "tok_out": _snap.get("tok_out"),
                            "tool": _snap.get("tool") or "",
                            "mode": mode,
                            "fallback": 1 if err or mode == "API失败" else 0,
                            # 🔴 规划分支的观测字段**必须显式列出来**。
                            # 字段是白名单式的（上面每一条都得手写），所以
                            # trace_out 里塞了不等于落进 trace 文件 ——
                            # plan_steps 在 reply.trace 里看得见、
                            # 在 /api/trace 里却没有，就是因为漏了这一行。
                            # 不列出来的话，"判要规划但没解析出步骤""规划抛异常"
                            # 这两种回退在长期数据里永远查不到，而这正是
                            # 判定它"删还是留"要看的东西。
                            "plan_why": _snap.get("plan_why") or "",
                            "plan": _snap.get("plan") or (
                                "ok" if _snap.get("plan_steps") else ""),
                            "plan_steps": _snap.get("plan_steps"),
                            "plan_ms": _snap.get("plan_ms"),
                            "plan_calls": _snap.get("plan_calls"),
                            "revised": 1 if _snap.get("revised") else 0,
                        })
                    except Exception:
                        pass
                    # reply 整段保留给旧版 App；messages 按她的换行拆好段，
                    # 新版 App 逐条渲染成连发气泡，存档也按这个粒度拆
                    # 🔴 **trace 必须回给客户端**。只把 _snap 写进落盘日志，
                    # 返回体里一个 trace 键都没有 —— 于是前端
                    # `d.trace.plan_steps` 恒为 undefined。
                    # 症状特别有欺骗性：日志里明明打了
                    # 「[大脑] Plan-and-Execute 完成，2 步」，功能是好的，
                    # 而面板/接口看着像"没走规划分支"，白查了很久。
                    # 只给用得到的几个字段，别把 blocks/mem_top 全塞进去。
                    self._json(200, {"reply": ans, "messages": split_messages(ans),
                                     "mode": mode, "err": err,
                                     "t": time.strftime("%Y-%m-%d %H:%M"),
                                     "trace": {
                                         "plan_steps": _snap.get("plan_steps"),
                                         "revised": bool(_snap.get("revised")),
                                         "tool": _snap.get("tool") or "",
                                         "mode": mode,
                                         "ms": _snap.get("ms"),
                                         "model": _snap.get("model")
                                                 or str((API_CFG or {}).get("model") or ""),
                                         "mem_hits": _snap.get("mem_hits", 0),
                                         # 思考过程：推理模型的 reasoning_content，客户端折叠显示
                                         "think": _snap.get("think") or "",
                                         # 她这轮实际看到的工具段（排查"装了不调"用）
                                         "rules_len": _snap.get("rules_len"),
                                         "rules_has_skill": _snap.get("rules_has_skill"),
                                         "rules_has_fortune": _snap.get("rules_has_fortune"),
                                         "rules_has_must": _snap.get("rules_has_must"),
                                     }})
                elif path == "/api/chat/stream":
                    # 🔴 流式聊天：SSE 实时推送思考过程和正文。
                    # 客户端用 EventSource 或 fetch + ReadableStream 接收。
                    # 事件格式：data: {"type":"think"/"text"/"done"/"error", ...}\n\n
                    _in_text = str(body.get("text") or "")
                    _src = _norm_src(body.get("src"))
                    try:
                        import features as _feat
                        _feat.set_current_src(_src)
                        import localtools as _lt
                        _lt.set_context(_src, str(body.get("sender_id") or ""))
                    except Exception:
                        pass
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.send_header("X-Accel-Buffering", "no")
                    self.end_headers()
                    try:
                        with _chat_lock():
                            for evt in _LOCAL["brain"].stream_chat(_in_text, src=_src):
                                self.wfile.write(("data: " + json.dumps(evt, ensure_ascii=False) + "\n\n").encode("utf-8"))
                                self.wfile.flush()
                    except _Busy:
                        self.wfile.write(("data: " + json.dumps({"type": "error", "msg": "她正忙着，等会儿再说"}, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    except Exception as e:
                        self.wfile.write(("data: " + json.dumps({"type": "error", "msg": "%s: %s" % (type(e).__name__, str(e)[:120])}, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    return
                elif path == "/api/packages/del":
                    # 删掉就移出监控，她的提醒跟着停（poll 只扫这份存档）
                    import packages as _pkg
                    ok = _pkg.remove(str(body.get("number") or "").upper())
                    self._json(200, {"ok": bool(ok)})
                elif path == "/api/packages/note":
                    import packages as _pkg
                    p = _pkg.set_note(str(body.get("number") or "").upper(),
                                      str(body.get("note") or ""))
                    self._json(200, {"ok": bool(p), "rec": p})
                elif path == "/api/proactive":
                    try:
                        with _chat_lock():
                            ans, mode = brain.speak_on_scene(
                                str(body.get("scene") or ""))
                    except _Busy:
                        self._json(200, {"reply": "", "mode": "忙碌",
                                         "err": "她正忙着，等会儿再说"})
                        return
                    self._json(200, {"reply": ans, "mode": mode, "err": ""})
                elif path == "/api/reset":
                    try:
                        with _chat_lock():
                            if hasattr(brain, "reset"):
                                brain.reset()
                    except _Busy:
                        self._json(200, {"ok": False, "err": "她正忙着"})
                        return
                    self._json(200, {"ok": True})
                elif path == "/api/history/clear":
                    # 存档只有云上这一份。早先桌宠直接调本地 Brain.clear_history()
                    # 删的是本机文件，云上那份一动不动，看着清了其实没清。
                    # 分渠道后只清请求里指定的那份（不传=App），清微信的不影响手机。
                    _csrc = _norm_src(body.get("src") if body else None)
                    ok = False
                    try:
                        from brain import Brain
                        ok = bool(Brain.clear_history(_csrc))
                    except Exception as e:
                        print(f"[大脑] 清空聊天存档失败：{e}", flush=True)
                    try:
                        with _chat_lock():
                            _bl = _LOCAL.get("brain")
                            if _bl is not None:
                                _bl.clear_src_history(_csrc)
                    except _Busy:
                        pass
                    except Exception as e:
                        print(f"[大脑] 清内存历史失败：{e}", flush=True)
                    self._json(200, {"ok": ok})
                elif path == "/api/reload_persona":
                    try:
                        with _chat_lock():
                            if hasattr(brain, "reload_persona"):
                                brain.reload_persona()
                    except _Busy:
                        self._json(200, {"ok": False, "err": "她正忙着"})
                        return
                    self._json(200, {"ok": True})
                elif path == "/api/persona/split_raw":
                    # 从整段人设里认名字和小名。**只给面板做预览用** ——
                    # 真正的落点在 /api/persona/save：整段落在 notes。
                    # 拆解规则必须在服务端（persona_store.split_raw_persona），
                    # 前端不自己写一份 —— 两份规则迟早不一致，
                    # 表现是"面板说认出来了、存下去名字还是空的"。
                    try:
                        import persona_store
                        raw = str(body.get("raw") or "")
                        if len(raw) > 200000:
                            self._json(200, {"ok": False, "err": "太长了"})
                            return
                        nm, nk = persona_store.split_raw_persona(raw)
                        self._json(200, {"ok": True, "name": nm, "nicknames": nk})
                    except Exception as e:
                        errlog.log_exc("api/persona/split_raw", e)
                        self._json(200, {"ok": False, "err": str(e)[:120]})
                elif path == "/api/persona/save":

                    # 保存人设字段。与切换人设不同：改的是当前这套的文件本身。
                    # 落盘 + 热重载两处都要，只改一处会出现"界面变了、她没变"
                    try:
                        import persona_store
                        # 🔴 判据要用**这个渠道**当前那套人设，不是全局那份。
                        # 不能拿 key_from_config（= App 的 key）当判据：编辑
                        # 微信人设时 key 对不上，被当成"你在编辑别的模板"
                        # 故意不重载 —— 接口回 ok、界面弹"她马上就是这个设定"，
                        # 只有她不换。/api/persona/apply（切模板）那条已经按
                        # src 取了，save 这条还停在老逻辑上。
                        _ssrc = _norm_src(body.get("src") or "")
                        _skey = _persona_for_src(
                            _ssrc, persona_store.key_from_config(api_config))
                        key = os.path.basename(str(body.get("key") or "")
                                               or _skey)
                        if not key or not os.path.isfile(persona_store.path_of(key)):
                            self._json(200, {"ok": False, "err": "没有这套人设"})
                            return
                        fields = body.get("fields")
                        if not isinstance(fields, dict):
                            self._json(200, {"ok": False, "err": "字段不对"})
                            return
                        name = str(fields.get("name") or "").strip()
                        if not name:
                            self._json(200, {"ok": False, "err": "总得有个名字"})
                            return
                        persona_store.save(fields, key)
                        # 只有改的是这个渠道正在用的那套才热重载：
                        # 编辑别的模板不该动她
                        reloaded = (key == _skey)
                        if reloaded:
                            with _chat_lock():
                                brain.reload_persona()
                        print("[大脑] 人设已更新：%s（%s，渠道 %s%s）"
                              % (name, key, _ssrc,
                                 "，已热重载" if reloaded else "，非当前渠道"),
                              flush=True)
                        self._json(200, {"ok": True, "name": name,
                                         "key": key, "src": _ssrc,
                                         "reloaded": reloaded})
                    except Exception as e:
                        errlog.log_exc("api/persona/save", e)
                        self._json(200, {"ok": False, "err": str(e)[:120]})
                elif path == "/api/persona/apply":
                    # 改 config.json 的 persona_file 再热重载。key 只认 personas/
                    # 下已有的文件名（防路径穿越）
                    try:
                        import persona_store
                        key = os.path.basename(str(body.get("key") or ""))
                        if not key or not os.path.isfile(
                                persona_store.path_of(key)):
                            self._json(200, {"ok": False,
                                             "err": "没有这套人设"})
                            return
                        cfg = {}
                        try:
                            cfg = json.load(open(config_path(), encoding="utf-8"))
                        except Exception:
                            cfg = {}
                        # 🔴 带 src 时改的是**那个渠道的覆盖**（persona_by_src），
                        # 不是全局 persona_file。否则在面板里切到微信点「应用」，
                        # 会把 App 的默认人设也一起换掉 —— 两个渠道各换各的。
                        _asrc = _norm_src(body.get("src") or "")
                        if _asrc != "app":
                            by = cfg.get("persona_by_src") or {}
                            by[_asrc] = key
                            cfg["persona_by_src"] = by
                        else:
                            cfg["persona_file"] = "personas/%s.json" % key
                        with open(config_path(), "w", encoding="utf-8") as f:
                            json.dump(cfg, f, ensure_ascii=False, indent=2)
                        # 内存那份也要改：reload_persona() 读的是内存的 persona_file，
                        # 只写盘的话会载回旧人设
                        if _asrc == "app":
                            api_config["persona_file"] = cfg["persona_file"]
                        else:
                            api_config.setdefault("persona_by_src", {})[_asrc] = key
                        with _chat_lock():
                            brain.reload_persona()
                        her = ""
                        try:
                            her = brain.persona.get("name") or ""
                        except Exception:
                            pass
                        print(f"[大脑] 人设已切换为 {key}（{her}）",
                              flush=True)
                        self._json(200, {"ok": True, "name": her})
                    except Exception as e:
                        self._json(200, {"ok": False, "err": str(e)[:80]})
                elif path == "/api/persona/new":
                    # 新建一套人设模板（不自动切过去，省得她换脸换名字你还没看仔细）
                    try:
                        import persona_store
                        key = persona_store.create(
                            body.get("name"),
                            from_key=body.get("from") or None,
                            label=body.get("label") or "",
                            desc=body.get("desc") or "")
                        print("[大脑] 新建人设模板 %s" % key, flush=True)
                        self._json(200, {"ok": True, "key": key})
                    except ValueError as e:
                        self._json(200, {"ok": False, "err": str(e)[:80]})
                    except Exception as e:
                        errlog.log_exc("api/persona/new", e)
                        self._json(200, {"ok": False, "err": str(e)[:80]})
                elif path == "/api/persona/del":
                    # 删模板。不热重载：被删的不是当前那套（remove 已挡掉）
                    try:
                        import persona_store
                        ok, why = persona_store.remove(
                            body.get("key"),
                            current_key=persona_store.key_from_config(api_config))
                        if ok:
                            print("[大脑] 删除人设模板 %s"
                                  % str(body.get("key"))[:40], flush=True)
                        self._json(200, {"ok": ok, "err": why})
                    except Exception as e:
                        errlog.log_exc("api/persona/del", e)
                        self._json(200, {"ok": False, "err": str(e)[:80]})
                elif path == "/api/voice/apply":
                    # 同人设切换，但内存里要同步两处：api_config 和 API_CFG
                    # （voice.synth() 实际收到的浅拷贝）。只改一处会"看着换了、声音没变"
                    try:
                        key = os.path.basename(str(body.get("key") or ""))
                        import voice
                        sub, desc = voice.apply(api_config, key)
                        if not sub:
                            self._json(200, {"ok": False,
                                             "err": "没有这个声音"})
                            return
                        cfg = {}
                        try:
                            cfg = json.load(open(config_path(), encoding="utf-8"))
                        except Exception:
                            cfg = {}
                        cfg["voice"] = sub
                        # 老配置备份一次就够，别每次点都盖
                        bak = config_path() + ".bak_voice"
                        if not os.path.exists(bak):
                            try:
                                import shutil
                                shutil.copyfile(config_path(), bak)
                            except Exception:
                                pass
                        with open(config_path(), "w", encoding="utf-8") as f:
                            json.dump(cfg, f, ensure_ascii=False, indent=2)
                        api_config["voice"] = sub
                        API_CFG["voice"] = sub
                        # 不用重启也不用重建 Brain，synth() 每次现读配置。
                        # 缓存按 (文本, 模型, 音色) 做 key，换完不串音
                        print(f"[大脑] 声音已切换为 {desc}（API_CFG 已同步）",
                              flush=True)
                        self._json(200, {"ok": True, "key": key, "desc": desc,
                                         "voice": voice.describe(API_CFG)})
                    except Exception as e:
                        errlog.log_exc("api/voice/apply", e)
                        self._json(200, {"ok": False, "err": str(e)[:80]})
                elif path == "/api/voice/preview":
                    # 用一份临时配置去合成，别动全局 api_config / API_CFG，
                    # 否则"试听"就变成"直接换掉她的声音"
                    try:
                        key = os.path.basename(str(body.get("key") or ""))
                        import voice
                        sub, desc = voice.apply(api_config, key)
                        if not sub:
                            self._json(200, {"ok": False,
                                             "err": "没有这个声音"})
                            return
                        tmp = dict(API_CFG)
                        tmp["voice"] = sub
                        text = (str(body.get("text") or "").strip()
                                or voice.PREVIEW_TEXT)
                        name = voice.synth(text, tmp)
                        if not name:
                            self._json(200, {"ok": False,
                                             "err": "合成失败（余额或网络）"})
                            return
                        self._json(200, {"ok": True, "name": name,
                                         "text": text, "desc": desc})
                    except Exception as e:
                        errlog.log_exc("api/voice/preview", e)
                        self._json(200, {"ok": False, "err": str(e)[:80]})
                elif path == "/api/models/apply":
                    # 写盘之后要 clear + update 内存那份（Brain 拿的是启动时
                    # load_config() 的字典），不然换了模型她还用旧的
                    try:
                        new_cfg, changed = model_hub.apply_cfg(api_config, body)
                        if not changed:
                            self._json(200, {"ok": True, "changed": [],
                                             "msg": "没有改动"})
                        else:
                            model_hub.save(new_cfg)
                            api_config.clear()
                            api_config.update(new_cfg)
                            try:
                                API_CFG.clear()
                                # 🔴 这里**不能重抄一份白名单**。
                                # 比如漏掉 voice / uapi / persona_file ——
                                # update 是**整体替换**，少一个键那个键就当場消失。
                                # 后果：换一次模型，语音配置和 uapi 静默失效；
                                # 最要命的是 persona_file —— Brain 只认 API_CFG 里的它，
                                # 丢了之后 key_from_config 一律返回 "default"，
                                # 于是**所有分区名都算成空**：换人设不换记忆、
                                # 换渠道不换存档，看着分区建了，其实全写进同一份。
                                # 白名单抄一次错一次（vision、voice、persona_file 已栽过三回），
                                # 所以唯一真源只能是 runtime 那个列表本身。
                                API_CFG.update(_runtime.api_cfg_subset(api_config))
                            except Exception:
                                pass
                            print("[模型] " + "；".join(changed), flush=True)
                            self._json(200, {
                                "ok": True, "changed": changed,
                                "current": model_hub.public_current(api_config)})
                    except Exception as e:
                        errlog.log_exc("api/models/apply", e)
                        self._json(200, {"ok": False, "err": str(e)[:120]})
                elif path == "/api/logs":
                    # App 把手机上的报错报上来，跟云上的存一起（分开两个文件）
                    try:
                        items = body.get("items")
                        if not isinstance(items, list):
                            items = [body]
                        saved = 0
                        for it in items[:50]:
                            if not isinstance(it, dict):
                                continue
                            errlog.log(it.get("where") or "app",
                                       it.get("msg") or "",
                                       level=str(it.get("level") or "ERROR")[:8],
                                       src="client",
                                       trace=str(it.get("trace") or ""))
                            saved += 1
                        self._json(200, {"ok": True, "saved": saved})
                    except Exception as e:
                        errlog.log_exc("api/logs", e)
                        self._json(200, {"ok": False, "err": str(e)[:120]})
                elif path == "/api/logs/clear":
                    try:
                        src = str(body.get("src") or "server")
                        ok = errlog.clear(src) if src in ("server", "client") \
                            else False
                        self._json(200, {"ok": ok, "stats": errlog.stats()})
                    except Exception as e:
                        self._json(200, {"ok": False, "err": str(e)[:120]})
                elif path == "/api/gen":
                    # 她"拍一张照片"：prompt -> 生成图 -> 存云上，返回文件名。
                    # 走 _cloud_gen_selfie 而不是 _cloud_gen_image：这个接口出的图
                    # 也是"她本人的照片"，不锁参考图的话脸每次都变，和对话里
                    # [gen:] 触发生成的不是同一个人。
                    name = _cloud_gen_selfie(str(body.get("prompt") or "")[:400],
                                             negative_prompt=GEN_NEGATIVE)
                    self._json(200, {"name": name, "err": "" if name else "生图失败"})
                elif path == "/api/features":
                    # App 设置页的功能开关：传哪个改哪个，返回改后的全量。
                    # 带上 src 就改那个渠道的覆盖（只写这几个键，不动全局）；
                    # 不带 src 改的是全局那一份。
                    import features as _feat
                    patch = body if isinstance(body, dict) else {}
                    patch.pop("token", None)
                    # 渠道取值优先级：body 显式给的 > query 里的（Electron 放
                    # query）> 都没有 = 全局那一份。🔴 不能用 _norm_src("") 兜
                    # 底 —— 它把空串归成 "wechat"，"不带 src 改全局"就永远走不到。
                    _raw = str(patch.pop("src", "") or qp.get("src") or "").strip()
                    _fsrc = _norm_src(_raw) if _raw else None
                    cur = _feat.set_features(patch, _fsrc) if patch else None
                    if cur is None:
                        cur = _feat.all_features(_fsrc)
                    self._json(200, {"features": cur})
                elif path == "/api/chatty":
                    # 说话多少（1~10）。跟功能开关一样按渠道存：
                    # 带 src 改那个渠道，不带改 default。
                    import chatty as _ch
                    _c = body if isinstance(body, dict) else {}
                    _c.pop("token", None)
                    # 同 /api/features：body > query > 都没有 = default 档位
                    _craw = str(_c.pop("src", "") or qp.get("src") or "").strip()
                    _csrc = _norm_src(_craw) if _craw else None
                    if "level" in _c:
                        _ch.set_level(_c.get("level"), _csrc)
                        # 🔴 **必须清规则缓存**。draft_rules 是按 (人设,渠道)
                        # 缓存的，不清的话这次改动要等大脑重启才生效 ——
                        # 而界面会立刻显示新值，"改了没用"就是这么来的。
                        try:
                            _b = _LOCAL.get("brain")
                            if _b is not None:
                                _b._rules_cache = {}
                        except Exception:
                            pass
                    self._json(200, {"levels": _ch.all_levels(),
                                     "level": _ch.level(_csrc)})
                elif path.startswith("/api/sync/"):
                    # 同步推送（本地 → 权威源）：全部按账本幂等应用
                    r = _sync_route_post(path, body if isinstance(body, dict)
                                         else {})
                    if r:
                        self._json(r[0], r[1])
                    elif not self._plugin_route("POST", path, body):
                        self._json(404, {"err": "no such sync api"})
                else:
                    # 插件管理面（装/卸/渠道开关）优先于插件自带路由：
                    # 前者只在 /api/plugins* 这一小片里，前缀不重叠。
                    if not self._plugin_admin("POST", path, body):
                        # 插件自己的接口（ROUTES）。放最后当兜底：核心路由都叫
                        # /api/xxx，插件要求自己的路径带 /api/plugin/ 前缀，
                        # 撞名的可能交给名字去管，这里只做精确匹配
                        if not self._plugin_route("POST", path, body):
                            self._json(404, {"err": "no such api"})
            except Exception as e:
                print(f"[大脑] 接口异常 {path}：{type(e).__name__}: {e}",
                      flush=True)
                try:
                    errlog.log_exc("api" + str(path), e)
                except Exception:
                    pass
                self._json(500, {"err": f"{type(e).__name__}: {e}"})

        def do_GET(self):
            """兜底包一层再进真正的处理。

            do_POST 一直有 catch-all，do_GET 没有 —— 于是一个坏入参就能让处理
            线程带着 traceback 死掉，**客户端什么响应都收不到**（连接直接断），
            比返回 500 更难排查。典型触发：?last=abc、?limit=abc。
            """
            try:
                self._do_GET()
            except (BrokenPipeError, ConnectionResetError):
                pass                      # 客户端先走了，正常的
            except Exception as e:
                try:
                    self._json(500, {"err": f"{type(e).__name__}: {e}"})
                except Exception:
                    pass

        def _do_GET(self):
            # M3 转发通道：/ws/brain 的 WebSocket 升级在鉴权之前接管
            # （它自带 token 校验；普通 HTTP 鉴权对升级请求没有意义）。
            # 握手成功后这个 handler 线程就被帧循环占住了。
            try:
                import ws_forward as _fwd_up
                if _fwd_up.try_upgrade(
                        self, (api_config or {}).get("brain_token")
                        or BRAIN_TOKEN or ""):
                    return
            except (BrokenPipeError, ConnectionResetError):
                raise
            except Exception:
                pass
            # 🔴 /api/health 在鉴权之前放行：sidecar 的启动壳（server_launch）
            # 和 Electron 壳靠它做就绪探测 —— 此时可能还没配 token，
            # 走鉴权就永远探测不到。只回存活信息，不吐任何配置。
            if self.path.split("?")[0] == "/api/health":
                self._json(200, {"ok": True, "pid": os.getpid(),
                                 "frozen": bool(getattr(sys, "frozen", False))})
                return
            # 🔴 取连接配置这个接口必须**在鉴权之前**处理。
            # 它存在的意义就是"本地没有 token"，走正常鉴权必然 401 ——
            # 死锁，一次都拿不到。放行条件是回环地址，见该分支内的说明。
            _p0 = self.path.split("?")[0]
            if _p0 == "/api/panel/connect-config":
                if not _is_loopback(self.client_address[0]):
                    self._json(403, {"ok": False,
                                     "err": "只能从本机取连接配置"})
                    return
                _tok = str(api_config.get("brain_token") or BRAIN_TOKEN or "")
                if not _tok:
                    self._json(200, {"ok": False, "token": "",
                                     "err": "服务端没配 brain_token"})
                    return
                # 地址回本机能连上的那个，且用 Host 头反推端口
                # （`port` 是 run_remote_api 的局部变量，这里取不到；
                #   硬写常量会在换端口后静默指错）。
                # 不能把公网 IP 或隧道端口塞回去 —— 那等于把基础设施信息
                # 写进会落到浏览器存储里的东西。
                _host = (self.headers.get("Host") or "").strip()
                _port = _host.rsplit(":", 1)[-1] if ":" in _host else "8788"
                self._json(200, {"ok": True, "token": _tok,
                                 "server": "http://127.0.0.1:%s" % _port})
                return
            if not self._check():
                self._json(401, {"err": "bad token"})
                return
            note_client_ip(self.client_address[0])
            path = self.path.split("?")[0]
            # 受限设备的默认拒绝闸门（GET 版）：读接口也按权限矩阵裁。
            if self._deny_if_limited_device("GET", path):
                return
            if path.startswith("/api/agent-center/"):
                import codex_adapter as _codex_center
                from urllib.parse import parse_qs, urlparse
                query = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                code, out = _codex_center.handle_http(
                    "GET", path, query=query, is_admin=self._is_admin(),
                    is_local=_is_loopback(self.client_address[0]))
                self._json(code, out)
                return
            if path.startswith("/api/management/"):
                # 全局基础设施信息：主人钥匙或 admin 角色设备（桌面管理端），
                # 普通设备 Token 不得读取。
                if not self._is_admin():
                    self._json(403, {"ok": False, "err": "需要管理员权限"})
                    return
                try:
                    import management as _management
                    result = _management.get(path)
                    if result is not None:
                        self._json(result[0], result[1])
                    else:
                        self._json(404, {"ok": False, "err": "no such management api"})
                except Exception as e:
                    print("[管理] 查询失败：%s: %s" % (type(e).__name__, e), flush=True)
                    self._json(500, {"ok": False, "err": "管理数据读取失败"})
                return
            if path == "/api/device/list":
                import devices
                me = getattr(self, "_who", ("", ""))
                self._json(200, {"ok": True, "items": devices.list_all(),
                                 "me": me[1] if me[0] == "device" else "",
                                 "who": me[0]})
                return
            if path == "/api/device/audit":
                # M4：本地操控的操作日志（只读，GET 版本 —— 和 device/list
                # 一样读接口走 GET；POST 分支里那份留着管后台兼容）。
                # 只给主人钥匙 —— 设备 token 看别的设备的日志等于越权。
                who = getattr(self, "_who", ("", ""))[0]
                if who != "brain":
                    self._json(403, {"ok": False,
                                     "err": "只有主人钥匙能看操作日志"})
                    return
                import localtools as _lt
                self._json(200, {"ok": True, "items": _lt.audit()})
                return
            if path == "/api/memory/stores":
                # M5 记忆管理面板：列出全部分区（主库 memory + 各渠道分区）
                import memory_store_v2 as _ms
                self._json(200, {"ok": True, "items": _ms.list_store_names()})
                return
            if path == "/api/memory/list":
                # M5 记忆管理面板：看某个分区里的条目。
                # store 名来自 HTTP，必须过 sanitize_name 白名单（防路径穿越，
                # open_store_by_name 里做）。items 是 append 模式，尾部最新，
                # 返回时反转成新→旧给 UI。
                import memory_store_v2 as _ms
                from urllib.parse import urlparse, parse_qs
                _q = parse_qs(urlparse(self.path).query)
                st = _ms.open_store_by_name(
                    (_q.get("store") or ["memory"])[0])
                if st is None:
                    self._json(400, {"ok": False, "err": "分区名不合法"})
                    return
                try:
                    limit = min(500, max(1, int((_q.get("limit") or ["200"])[0])))
                except ValueError:
                    limit = 200
                items = st.list_items()
                self._json(200, {"ok": True, "store": st.name,
                                 "total": len(items),
                                 "items": items[-limit:][::-1]})
                return
            if path == "/api/persona/full":
                # 给人设编辑器用：读人设的全部字段（不是摘要）。
                # 放GET 分支：编辑器只是读，POST 那条路留给 save。
                try:
                    import persona_store
                    from urllib.parse import urlparse, parse_qs
                    _q = parse_qs(urlparse(self.path).query)
                    key = (_q.get("key") or [None])[0]
                    # key 来自查询串，走 basename + isfile双重校验，防路径穿越
                    if key:
                        cur = os.path.basename(str(key))
                    else:
                        # 不带 key = "读这个渠道正在用的那套"。**必须按 src 解析**：
                        # 只用 key_from_config 拿到的是全局那份（= App 的），
                        # 于是面板切到微信、编辑器打开的还是 App 的人设，
                        # 而弹窗标题还写着编辑麦麦 —— 人设区看着正常，内容全错。
                        _fsrc = _norm_src((_q.get("src") or [""])[0])
                        cur = _persona_for_src(_fsrc,
                                               persona_store.key_from_config(api_config))
                    if not cur or not os.path.isfile(persona_store.path_of(cur)):
                        self._json(200, {"ok": False, "err": "没有这套人设"})
                        return
                    _p = persona_store.load(cur)
                    self._json(200, {"ok": True, "key": cur,
                                     "fields": {k: _p.get(k) for k in
                                                persona_store.FIELDS}})
                except Exception as e:
                    self._json(200, {"ok": False, "err": str(e)[:80]})
            elif path == "/api/persona":
                # App 设置页的人设切换要用：当前是哪套 + 有哪些预设
                import persona_store
                name = ""
                cur = ""
                try:
                    name = brain.persona.get("name") or ""
                    cur = persona_store.key_from_config(api_config)
                except Exception:
                    pass
                #🔴 这里**必须用 list_templates**，别再手搓一份。
                # 下面这四行若自己读 json 取 `desc`，卡片上那句介绍
                # 只认预设写死的元数据 —— 而编辑器里根本没有这一栏，
                # 用户把整份人设换掉之后介绍还是老那句，看着像"换了没生效"。
                # `_card_desc`（从正文现算 + 分栏字段回退）就在 list_templates
                # 里。手搓一份 = 有两个真源，改一处忘一处。
                presets = []
                try:
                    for t in (persona_store.list_templates(cur) or []):
                        presets.append({"key": t.get("key"),
                                        "label": t.get("label"),
                                        "desc": t.get("desc")})
                except Exception:
                    pass
                # 带 src 时按那个渠道回答：微信和 App 是两套人设，
                # 「当前是谁」必须按渠道算，不然面板切到微信看到的还是 App 的。
                try:
                    from urllib.parse import parse_qs, urlparse
                    _qs = {k: v[0] for k, v in
                           parse_qs(urlparse(self.path).query).items()}
                except Exception:
                    _qs = {}
                _psrc = _norm_src(_qs.get("src") or "")
                _pkey = _persona_for_src(_psrc, cur) if _psrc != "app" else cur
                _pname = ""
                try:
                    _pname = (persona_store.load(_pkey) or {}).get("name") or ""
                except Exception:
                    pass
                self._json(200, {"name": _pname or name, "key": _pkey,
                                 "presets": presets,
                                 "relation": persona_store.relation_of(
                                     persona_store.load(_pkey)),
                                 "templates": persona_store.list_templates(_pkey),
                                 # 🔴 这个渠道在用哪套人设（面板靠它标「微信在用」）
                                 "src_persona_key": _pkey,
                                 "src": _psrc,
                                 "max": persona_store.MAX_TEMPLATES,
                                 "used": persona_store.template_count()})
            elif path == "/api/voices":
                # 清单是 VOICE_CATALOG 里的死数据，不联网 —— 设置页必须永远打得开
                try:
                    import voice
                    # 顺带扫一眼缓存目录，让 App 看到"这个声音攒了几条"，
                    # 只用来判断要不要清缓存
                    try:
                        n_cache = len([f for f in os.listdir(voice.VOICE_DIR)
                                       if f.startswith("v_")])
                    except Exception:
                        n_cache = 0
                    d = voice.catalog(API_CFG)
                    d["ok"] = True
                    d["cache_files"] = n_cache
                    d["current_desc"] = voice.describe(API_CFG)
                    self._json(200, d)
                except Exception as e:
                    errlog.log_exc("api/voices", e)
                    self._json(200, {"ok": False, "err": str(e)[:120],
                                     "current": "", "items": []})
            elif path == "/api/ollama/status":
                # 本地模型状态 + 连接测试（管理员；受限设备已被权限闸门挡掉）。
                # 不带 query = 体检当前配置；带 base/model = 保存前先试这组。
                from model_providers.ollama import OllamaProvider
                _obase = _query_param(self.path, "base")
                _omodel = _query_param(self.path, "model")
                if _obase:
                    _op = OllamaProvider({"base": _obase, "model": _omodel or ""})
                else:
                    _op = OllamaProvider(API_CFG.get("ollama")
                                         if isinstance(API_CFG.get("ollama"), dict)
                                         else {})
                ok, info = _op.health(timeout=5)
                self._json(200, {"ok": ok,
                                 "base": _op.base,
                                 "model_configured": _op.model,
                                 **info})
            elif path == "/api/delegations/recent":
                # 混合模式「每次委派状态」：本机 data/delegations/ 落盘记录的摘要。
                # 不进 DEVICE_GET_ALLOW —— 记录里有工作区路径与目标，只给管控端看。
                try:
                    _dn = int(_query_param(self.path, "n") or 20)
                except (TypeError, ValueError):
                    _dn = 20
                try:
                    from delegation import bridge as _dbridge
                    _items = _dbridge.recent_records(_dn)
                except Exception as _e:
                    errlog.log_exc("api/delegations/recent", _e)
                    _items = []
                self._json(200, {"ok": True, "items": _items})
            elif path == "/api/models":
                # force=1 = 用户点了"重新拉取"，不用缓存（换 key 之后必须这样拉一次）
                #
                # 🔴 **preview 参数是"按我刚填的地址和密钥试拉"，不读当前配置。**
                # 不这么做的话，前端点"硅基流动"只改了输入框里的文字，
                # 服务端照样拿**小米那把 key** 去拉 —— 界面显示"硅基流动"，
                # 列表里全是 mimo，看着像分类错了。
                # 缓存在 model_hub 里按 base 分键，所以切地址本来就能拿到各自的列表，
                # 缺的只是"用请求里的参数而不是配置里的参数"这一条路。
                #
                # 密钥留空时回落到当前那把：用户可能只想换个地址、key 沿用现有的。
                try:
                    force = str(_query_param(self.path, "force") or "") == "1"
                    p_base = str(_query_param(self.path, "base") or "").strip()
                    p_key = str(_query_param(self.path, "key") or "").strip()
                    p_prov = str(_query_param(self.path, "provider") or "").strip()
                    cur = model_hub.current(api_config)
                    if p_base:
                        # 预拉用哪把 key：显式传的 > 这家**自己存的** > 当前那把。
                        # 中间这层是给"切回存过密钥的老家"用的 —— 没有它，明知道
                        # 用户切到了某家、那家的 key 也存着，却还拿当前家（别家）
                        # 的 key 去试拉，必然 401，客户端只能看到一份兜底清单
                        # 冒充这家的模型（"换了服务商模型列表不变"的元凶）。
                        k = p_key
                        if not k and p_prov in model_hub.PROVIDERS \
                                and model_hub.has_provider_key(api_config, p_prov):
                            k = model_hub.provider_key(api_config, p_prov)
                        cur = dict(cur, api_base=p_base,
                                   api_key=k or cur.get("api_key") or "")
                    lst = model_hub.list_models(cur["api_base"],
                                                cur["api_key"], force=force)
                    # 回客户端用脱敏版：cur 里的真 key 只留在这一次调用里
                    self._json(200, {
                        "ok": True, "current": model_hub.public_current(api_config),
                        "providers": [{"key": k, "name": v["name"],
                                       "base": v["base"]}
                                      for k, v in model_hub.PROVIDERS.items()],
                        "groups": lst["groups"], "source": lst["source"],
                        "count": lst["count"], "err": lst["err"],
                    })
                except Exception as e:
                    errlog.log_exc("api/models", e)
                    self._json(200, {
                        "ok": False, "err": str(e)[:120],
                        "current": model_hub.public_current(api_config),
                        "providers": [{"key": k, "name": v["name"],
                                       "base": v["base"]}
                                      for k, v in model_hub.PROVIDERS.items()],
                        "groups": dict(model_hub.FALLBACK),
                        "source": "fallback", "count": 0})
            elif path == "/api/logs":
                # App「错误日志」页：src=server(云上)/client(手机)，fmt=text 是要导出
                try:
                    src = _query_param(self.path, "src") or "server"
                    if src not in ("server", "client"):
                        src = "server"
                    n = _query_param(self.path, "n") or 200
                    if str(_query_param(self.path, "fmt") or "") == "text":
                        self._json(200, {"ok": True,
                                         "text": errlog.as_text(n, src)})
                    else:
                        self._json(200, {"ok": True,
                                         "items": errlog.recent(n, src),
                                         "stats": errlog.stats()})
                except Exception as e:
                    errlog.log_exc("api/logs", e)
                    self._json(200, {"ok": False, "items": [],
                                     "err": str(e)[:120]})
            elif path == "/api/stats":
                # 用量与状态：App 设置页拿它显示"今天聊了多少轮、花了多少 token"
                out = _stats_today()
                try:
                    import agenda
                    out["agenda"] = agenda.stats()
                except Exception:
                    pass
                # 规划分支的观测（进程内累计，重启归零）。
                # 放这儿是因为它**本来就在观察期**：要判"删还是留"，
                # 先得能一眼看到"触发多少次、成不成、平均几步、多花多少毫秒"。
                # 进程重启会清零，所以长期数据还得靠 trace 里那几条 per-turn 字段。
                try:
                    import planner
                    out["plan"] = planner.stats()
                except Exception:
                    pass
                self._json(200, out)
            elif path == "/api/trace":
                n = _query_param(self.path, "last") or 20
                self._json(200, {"items": _trace_tail(n)})
            elif path == "/api/history":
                lim = _query_param(self.path, "limit") or 60
                # 缺省渠道是主渠道 wechat（app 已退役；老的 PollService 不带
                # src，给它微信这份才是她真正的对话历史）。
                self._json(200, {"items": _cloud_history(
                    lim, _norm_src(_query_param(self.path, "src")))})
            elif path == "/api/features":
                # App 设置页读功能开关。带 src=wechat 读的是那渠道覆盖后的结果。
                import features as _feat
                _q = {}
                try:
                    from urllib.parse import parse_qs, urlparse
                    _q = {k: v[0] for k, v in
                          parse_qs(urlparse(self.path).query).items()}
                except Exception:
                    _q = {}
                # 不带 src 读全局视角（_norm_src 会把空串兜成 "wechat"，别让它
                # 混进来 —— 渠道视角只在明确带了 src 时才生效）
                _fsrc = (_norm_src(_q.get("src"))
                         if (_q.get("src") or "").strip() else None)
                self._json(200, {"features": _feat.all_features(_fsrc)})
            elif path == "/api/chatty":
                # 说话多少（1~10）。带 src=wechat 读的是那个渠道的值。
                import chatty as _ch
                _q = {}
                try:
                    from urllib.parse import parse_qs, urlparse
                    _q = {k: v[0] for k, v in
                          parse_qs(urlparse(self.path).query).items()}
                except Exception:
                    _q = {}
                _csrc = (_norm_src(_q.get("src"))
                         if (_q.get("src") or "").strip() else None)
                self._json(200, {"levels": _ch.all_levels(),
                                 "level": _ch.level(_csrc)})
            elif path.startswith("/api/plugins"):
                # 插件清单 + 管理视图（渠道视图）+ 前端注入资源。
                # 微信桥接和 VM 启动器拿 /api/plugins 当"大脑还活着吗"
                # 的探针，只看 200 不看 body —— plugins 键的形状保持原样，
                # 管理视图另起一个键。/api/plugins/frontend 返回插件前端代码。
                if self._plugin_admin("GET", path, None):
                    return
                self._json(200, {"plugins": []})
            elif path == "/api/app/version":
                # App 内置更新检查：读版本文件（发布新 APK 时一起更新）
                try:
                    vf = r"C:\vigil\app\version.json"
                    self._json(200, json.load(open(vf, encoding="utf-8")))
                except Exception as e:
                    self._json(200, {"code": 0, "err": str(e)[:60]})
            elif path == "/api/app.apk":
                apk = r"C:\vigil\app\vigil.apk"
                try:
                    blob = open(apk, "rb").read()
                    self.send_response(200)
                    self.send_header("Content-Type",
                                     "application/vnd.android.package-archive")
                    self._cors()
                    self.send_header("Content-Length", str(len(blob)))
                    self.end_headers()
                    self.wfile.write(blob)
                except Exception as e:
                    self._json(404, {"err": str(e)[:60]})
            elif path == "/api/client/version":
                # 桌面客户端推送更新（2026-10-09，跟 App 一套思路）：
                # C:\Vigil\app\client\ 放安装包 + client_version.json
                # （code 递增，发布新包时一起更新）
                try:
                    vf = r"C:\Vigil\app\client\client_version.json"
                    self._json(200, json.load(open(vf, encoding="utf-8")))
                except Exception as e:
                    self._json(200, {"code": 0, "err": str(e)[:60]})
            elif path == "/api/client/setup":
                # 桌面客户端安装包下载。**分块流式**：安装包 70MB+，
                # 不能学 app.apk 整块 read() 进内存——每次更新都吃一次内存峰值
                fp = r"C:\Vigil\app\client\VigilSetup.exe"
                try:
                    import os as _os
                    n = _os.path.getsize(fp)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self._cors()
                    self.send_header("Content-Length", str(n))
                    self.end_headers()
                    with open(fp, "rb") as f:
                        while True:
                            chunk = f.read(262144)   # 256KB
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                except Exception as e:
                    self._json(404, {"err": str(e)[:60]})
            elif path == "/api/packages":
                # App 快递管理页：监控中的单号列表
                try:
                    import packages
                    self._json(200, {"items": packages.list_all()})
                except Exception as e:
                    self._json(200, {"items": [], "err": str(e)[:80]})
            elif path == "/api/stickers":
                # 只回表情包库（data/stickers）；upload 里是聊天/自拍图，倒进来
                # App 面板会塞满他没添加过的图
                names = []
                try:
                    import stickers
                    names = list(stickers.all_names())
                except Exception:
                    names = []
                self._json(200, {"names": names})
            elif path == "/api/voice":
                # 她发的语音条（voice.py 合成，文件在 data/voice/）
                name = _query_param(self.path, "name")
                try:
                    import voice
                    p = voice.path_of(name)
                except Exception:
                    p = None
                if not p:
                    self._json(404, {"err": "no such voice"})
                    return
                try:
                    with open(p, "rb") as f:
                        blob = f.read()
                except OSError:
                    self._json(404, {"err": "read fail"})
                    return
                # 后缀 → MIME。阿里那条线的输出格式可配（wav / opus 都合法），
                # 写死 audio/mpeg 会让 wav 播不出来
                _ct = {".wav": "audio/wav", ".mp3": "audio/mpeg",
                       ".opus": "audio/ogg", ".ogg": "audio/ogg"}
                self.send_response(200)
                self.send_header("Content-Type",
                                 _ct.get(os.path.splitext(p)[1].lower(),
                                         "application/octet-stream"))
                self._cors()
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
                return
            elif path == "/api/img":
                name = _query_param(self.path, "name")
                blob = _cloud_sticker(name)
                if blob is None:
                    self._json(404, {"err": "no such image"})
                    return
                ext = (os.path.splitext(name)[1].lower() or ".jpg")
                ctype = {".gif": "image/gif", ".png": "image/png",
                         ".webp": "image/webp"}.get(ext, "image/jpeg")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self._cors()
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
                return
            elif path == "/api/route":
                # M3：当前路由 + 转发通道状态（客户端设置页显示用）
                import ws_forward as _fwd
                self._json(200, {"wechat_route": _wechat_route(),
                                 "forward": _fwd.HUB.stats()})
            elif path.startswith("/api/sync/"):
                # 同步拉取（权威源 → 本地）。只读接口，设备 token 也能用：
                # 本地大脑拿的是主人钥匙，但将来别的只读端可能拿设备 token
                qp = {}
                try:
                    from urllib.parse import parse_qs, urlparse
                    qp = {k: v[0] for k, v in
                          parse_qs(urlparse(self.path).query).items()}
                except Exception:
                    qp = {}
                r = _sync_route_get(path, qp)
                if r:
                    self._json(r[0], r[1])
                elif not self._plugin_route("GET", path, {}):
                    self._json(404, {"err": "no such sync api"})
            else:
                if not self._plugin_route("GET", path, {}):
                    self._json(404, {"err": "no such api"})

        def log_message(self, *a):
            pass

    # 默认只绑 127.0.0.1（公网碰不到）。要让手机 App 直接连，必须配
    # `bind_host: "0.0.0.0"`，靠 token 兜底鉴权；旧配置里这个值在
    # mobile.remote_host，继续认
    host = str(api_config.get("bind_host")
               or MOB_CFG.get("remote_host") or "127.0.0.1")
    # 没配 token 就别往外绑：那等于开了一个谁都能用的接口（以前 _check 是空 token
    # 全放行，忘填 token 很常见，配上 0.0.0.0 就是公网裸奔）。宁可不开这个口。
    if not tok and not _is_loopback(host):
        print("[大脑] 拒绝启动远程 API：bind_host=%s 但没配 brain_token。"
              "这样会变成无鉴权的公开接口 —— 请在 config.json 里填一个"
              "随机长串（brain_token），或改回只绑 127.0.0.1。" % host,
              flush=True)
        return None
    if not tok:
        print("[大脑] 远程 API 未配 brain_token：只接受本机回环连接"
              "（开发模式，手机/公网连不进来）", flush=True)
    srv = http.server.ThreadingHTTPServer((host, port), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[大脑] 远程大脑 API 就绪：{host}:{port}", flush=True)
    return srv


def run_server():
    base = None
    # 🔴 把眼下这把密钥登记到当前服务商名下（老配置才有这问题）。
    # 不做的话界面会把每一家都显示成"还没存密钥"，
    # 于是"点一下就切换"永远切不动 —— 而实际有一家正拿着它在用。
    # 只在缺失时写、幂等，写完落盘所以重启后状态一致。
    try:
        import model_hub as _mh
        if _mh.adopt_current_key(api_config):
            _mh.save(api_config)
            print("[模型] 密钥已登记到当前服务商名下", flush=True)
    except Exception as e:
        print("[模型] 密钥登记跳过（不影响聊天）：%s" % str(e)[:100],
              flush=True)
    # 统一管理：本地电脑随大脑自动上报心跳（内嵌，无需单独开脚本）
    try:
        import management_agent as _magent
        _magent.start_from_config(api_config)
    except Exception as e:
        print("[管理] 心跳启动跳过（不影响聊天）：%s" % str(e)[:100], flush=True)
    if not base and _LOCAL["brain"] is None:
        # 云服务器 / 没开桌宠的场景：本进程自己养一个她（记忆、人设全带）
        print("[大脑] 没找到桌宠 —— 切换独立模式（本进程自带大脑）", flush=True)
        _LOCAL["brain"] = build_local_brain()
    if _LOCAL["brain"] is not None:
        # 独立模式才有"远程大脑 API"——本地桌宠走 SSH 隧道来用云上的大脑
        try:
            start_remote_api(_LOCAL["brain"])
        except Exception as e:
            print(f"[大脑] 远程大脑 API 没起来（不影响主服务）：{e}",
                  flush=True)
        # 她的全部后台循环：历史摘要 / 主动搭话 / 事件提醒
        start_background(_LOCAL["brain"])
        # M3：本地大脑的迟到答案只写云端历史（话术已经顶出去了，不外发）
        try:
            import ws_forward as _fwd
            _fwd.HUB.on_late_result = _late_to_history
        except Exception as e:
            print("[转发] ws_forward 初始化失败（不影响聊天）：%s" % e,
                  flush=True)
        # 插件（plugins/）：装完就起后台线程。装载失败只少几个功能，不影响主服务
        try:
            import plugin_host
            plugin_host.start_all(_LOCAL["brain"])
        except Exception as e:
            print("[插件] 启动失败（不影响聊天）：%s" % str(e)[:120],
                  flush=True)
        # 本地优先同步（M2）：云端是记忆/人设/行为配置的权威源。
        # 云端自己的 config 没有 sync 段 → cloud() 返回 None → 纯 no-op。
        # 只有本地大脑（sidecar / 本机源码家）会真正起同步线程。
        try:
            import sync_client as _sync
            _sync.set_reload_hook(lambda: reload_plugins_now())
            _sync.start()
        except Exception as e:
            print("[同步] 启动失败（不影响聊天）：%s" % str(e)[:120],
                  flush=True)

    print("=" * 58, flush=True)
    print(" 云端大脑", flush=True)
    print(" 远程 API / 历史摘要 / 主动搭话 照常运行。", flush=True)
    print("=" * 58, flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[大脑] 手动退出。", flush=True)
    return 0


def main():
    return run_server()


if __name__ == "__main__":
    # 单实例守卫：同一时间只允许一个大脑进程，否则两个实例会抢同一个端口。
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        mtx_name = os.environ.get(
            "BRAIN_MUTEX", "Global\\VigilBrainSingleInstance")
        mutex = kernel32.CreateMutexW(None, False, mtx_name)
        if kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
            print("[大脑] 已在运行，本实例退出。", flush=True)
            sys.exit(0)
    except Exception:
        pass
    sys.exit(main())
