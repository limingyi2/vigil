# -*- coding: utf-8 -*-
"""后台循环：主动搭话、约定账本和事件提醒。

- 生活补算（半小时）      过日子（写经历流）
- 约定账本（20 分钟）    从长期记忆刷新带日期的约定
- 主动搭话（自适应间隔）  静默时段/每日上限/被回应率全算进去
- 事件提醒（30 分钟）    快递进展 + 天气突变，走主动搭话同一条路弹通知

所有循环的共同纪律：出错打印一行继续跑，别让任何一根线程把进程带崩
"""
import time
import threading

from runtime import _LOCAL, API_CFG, _trace_write
from reply import strip_say_marker, resolve_gen_tags, resolve_voice_tag
from proactive import (
    PROACTIVE_RULES, _proactive_conf, _in_quiet,
    _proactive_interval_multiplier, _proactive_stats_load,
    _proactive_used_today, _proactive_count_up, _proactive_note_sent,
    _is_repeat_of_recent, _cloud_append_assistant, _his_ip)


class _NullChannel(object):
    """features 还没 import 上时的占位（让 with 语句照常工作）。"""

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def _feat_channel(src):
    """features.with_channel 的安全版 —— features 导入失败不能连带提醒线程挂掉。"""
    try:
        import features
        return features.with_channel(src)
    except Exception:
        return _NullChannel()


def start_background(brain):
    """把她的后台循环全部跑起来（run_server 装配完大脑后调一次）。"""
    threading.Thread(target=_agenda_loop, args=(brain,), daemon=True).start()
    threading.Thread(target=_proactive_loop, args=(brain,), daemon=True).start()
    threading.Thread(target=_reminder_loop, args=(brain,), daemon=True).start()


def _trace_loop(kind, **kw):
    """自治循环也落一行 trace。

    以前 trace 只有 /api/chat 一条路有，"她为什么主动说了这句""这轮为什么没发"
    只能靠翻 stdout（重启就没了）。去重与静默闸门拦掉的每一轮同样记下来 ——
    拦了多少次是判断"频率合不合适"的唯一依据。
    """
    try:
        time_str = time.strftime("%Y-%m-%d %H:%M")
        rec = {"t": time_str, "kind": kind}
        rec.update(kw)
        _trace_write(rec)
    except Exception:
        pass


def _agenda_scopes(b):
    """列出约定账本需要刷新的 persona/channel 分区。"""
    try:
        import paths
        srcs = list(paths.CHANNELS)
    except Exception:
        srcs = ["wechat", "wechat_group"]
    out = []
    for s in srcs:
        try:
            out.append((b.persona_key_of(s), s))
        except Exception:
            out.append(("", s))
    return out


def _agenda_loop(b):
    """定时从长期记忆刷新约定账本，不读取或写入旧版历史摘要。"""
    time.sleep(60)
    while True:
        # 约定账本：从记忆库里把"有日期的说好的事"捞出来按日期注入。
        # 20 分钟刷新一次，纯读盘、不调模型。
        # 按 persona+src 分区，每个渠道各刷一份，互不串台。
        try:
            import agenda
            for _pk, _s in _agenda_scopes(b):
                agenda.refresh(persona_key=_pk, src=_s)
        except Exception as e:
            print(f"[大脑] 约定账本出错（不影响聊天）：{str(e)[:80]}", flush=True)
        time.sleep(1200)


def _refresh_world_caches():
    """顺路刷天气 + 热搜缓存（失败静默，聊天链路只读缓存文件）。

    天气城市跟他的 IP 走；热搜进 6 小时槽位才刷（0/6/12/18 点整）
    """
    try:
        tok = (API_CFG.get("uapi") or {}).get("token")
        fallback = ""
        if tok:
            import features as _feat
            import weather_cache
            if _feat.on("weather"):
                weather_cache.refresh_auto(_his_ip(), tok, fallback)
            # 热梗榜这里**不刷**：meme 插件自己是现拉的（20 分钟缓存、0.3 秒），
            # 榜单只在"她那一轮聊天"有用，后台刷一遍省不下什么，
            # 却要多一套缓存文件 + 一个过期判断要维护。
            # hotboard_cache（抖音榜）就是这么来的，已经删了。
    except Exception:
        pass




def _proactive_loop(b):
    """主动搭话：同样由她决定说不说；说了就写进聊天存档，
    手机 App 的后台轮询会拉到这条消息并弹通知。"""
    while True:
        try:
            # 每轮重新读配置：改完 config.json 不用重启大脑就生效
            pa = _proactive_conf()
            base = int(pa.get("interval_sec") or 2400)
            interval = base
            if pa.get("adaptive", True):
                # 伸缩上限 2 小时。再冷也别变成半小时一条，那已经算骚扰
                interval = int(base * _proactive_interval_multiplier())
                cap = int(pa.get("max_interval_sec") or 7200)
                interval = min(interval, cap)
                # 他刚说过话（15 分钟内）→ 人在，不需要"找"他
                _d = _proactive_stats_load()
                _gap = time.time() - int(_d.get("last_user_ts") or 0)
                if _gap < 900:
                    time.sleep(max(300, interval))
                    continue
            time.sleep(max(300, interval))
            if not pa.get("enabled", True):
                continue
            # 静默时段默认 23:00~07:00，必须限制，否则凌晨也会连发搭话
            qs = pa.get("quiet_start", "23:00")
            qe = pa.get("quiet_end", "07:00")
            if _in_quiet(qs, qe):
                print("[大脑] 静默时段（%s~%s），这轮不主动搭话"
                      % (qs, qe), flush=True)
                continue
            # 每日上限：一天几十条比不说话更烦人
            cap = int(pa.get("max_per_day") or 99)
            used = _proactive_used_today()
            if used >= cap:
                print("[大脑] 今天主动搭话已到上限（%d/%d）"
                      % (used, cap), flush=True)
                continue
            with _LOCAL["lock"]:
                ans, _mode = b.chat(PROACTIVE_RULES, proactive=True,
                                    log=False)
            ans = (ans or "").strip()
            # strip_say_marker 兼容「说」单独成行和「说 xxx」连写两种，
            # 旧写法 lines[1:] 会把「说 我找找，你等着」的正文一起丢掉
            text = strip_say_marker(ans)
            if not text or text == "无":
                _trace_loop("proactive", sent=0, reason="她自己不想说")
                continue
            text = resolve_gen_tags(text)
            if not text:
                _trace_loop("proactive", sent=0, reason="标签解析后为空")
                continue
            text = resolve_voice_tag(text, API_CFG)
            # 去重闸门：最近说过（或截一段说过）就不发第二遍。主动搭话会
            # 复述上下文里自己刚说的话，见 proactive._recent_her_texts
            if _is_repeat_of_recent(text):
                print("[大脑] 主动搭话：和最近说过的话重复，这轮不发",
                      flush=True)
                _trace_loop("proactive", sent=0, reason="和最近说过的话重复")
                continue
            _cloud_append_assistant(text)
            _proactive_count_up()
            _proactive_note_sent()      # 记下这次主动，等他反应（自适应频率用）
            _trace_loop("proactive", sent=1, chars=len(text),
                        n_today=used + 1, interval_sec=interval)
            print("[大脑] 她主动找他说话了（今天第 %d 次）"
                  % (used + 1), flush=True)
        except Exception as e:
            print(f"[大脑] 主动搭话循环出错：{str(e)[:80]}", flush=True)


def _reminder_loop(b):
    """事件提醒循环：快递每 30 分钟查一次（进监控的单号）、天气变化按规则触发。
    生成走 chat(proactive=True)，发出去走主动搭话同一条路（App 轮询拉到弹通知）。
    静默时段照躲：快递到了也不凌晨轰炸，睡醒那轮补说。"""

    def _say(prompt):
        # 事件提醒是**私聊**内容：天气变化、快递到货都是跟你一个人的事，
        # 发到群里会出戏（"该加衣服了"当着另外三个人说）。所以显式按微信私聊
        # 走，别让后台线程落在上一个处理过的渠道上。
        with _feat_channel("wechat"):
            with _LOCAL["lock"]:
                ans, _m = b.chat(prompt, proactive=True, log=False, src="wechat")
            text = strip_say_marker((ans or "").strip())
            if not text or text == "无":
                _trace_loop("reminder", sent=0, reason="她自己不想说")
                return
            _cloud_append_assistant(text)
        _trace_loop("reminder", sent=1, chars=len(text))
        print("[大脑] 事件提醒：%s" % text[:50], flush=True)

    time.sleep(90)           # 启动先让缓存和大脑站稳
    while True:
        try:
            pa = _proactive_conf()
            if _in_quiet(pa.get("quiet_start", "23:00"),
                         pa.get("quiet_end", "07:00")):
                time.sleep(1800)
                continue
            tok = (API_CFG.get("uapi") or {}).get("token") or ""
            import features as _feat
            # 天气：代码判档位（雨/雪/高温/低温/大风，一天一档只报一次）
            try:
                if _feat.on("weather_alert"):
                    import weather_cache
                    trg = weather_cache.check_trigger()
                    if trg:
                        event, detail = trg
                        city = weather_cache.city() or "他那边"
                        _say("[系统指令] 他那边现在天气有变化，你要主动发一条"
                             "消息关心他。\n[触发事件] 他那边（%s）%s（%s）。\n"
                             "[回复规则] 用你平时的口气关心一句（提醒带伞/"
                             "加衣服/别中暑都行），顺带说一句你此刻正在做的"
                             "小事，50字以内。别写成天气预报，别问他住哪。"
                             % (city, event, detail))
            except Exception as e:
                print("[大脑] 天气提醒出错：%s" % str(e)[:60], flush=True)
            # 快递：状态变到 派送中/签收/异常 才说
            try:
                if _feat.on("packages"):
                    import packages
                    for ev in packages.poll(tok):
                        rec = ev["rec"]
                        _say("[系统指令] 他的快递有新进展，你要主动告诉他。\n"
                             "[快递] %s\n[回复规则] 用你平时的口气说，一两句，"
                             "别报物流流水账。派送中就提醒他去取，签收了就说"
                             "到了，异常就让他找卖家。"
                             % packages.status_line(rec))
            except Exception as e:
                print("[大脑] 快递提醒出错：%s" % str(e)[:60], flush=True)
            time.sleep(30 * 60)
        except Exception as e:
            print(f"[大脑] 提醒循环出错：{str(e)[:80]}", flush=True)
            time.sleep(600)
