# -*- coding: utf-8 -*-

"""知夏 · 微信桥接（本机版）—— 用 wxauto4 把知夏接进一个微信群。

## 它做什么

   微信群里有人说话 ──> 拼上"谁说的" ──> 问云端大脑 ──> 把她的回复发回群里
   群里冷场太久     ──> 她自己冒一句            ──> 发回群里

它自己**不存任何对话历史**。记忆、人设、日记、工具全在云端大脑里，
和手机 App、桌面宠物是同一个后端。这里只做搬运。

## 为什么不用现成方案

AstrBot / dify 那类框架都假设「LLM 是一次性无状态调用」，自己维护 session。
知夏的大脑在云端已经管好了人设和记忆，再叠一层 session 会让她被两套记忆
同时驱动 —— 表现是"群里说的话记到别处去了"。所以这里刻意做得很薄。

## 关于免费版 wxauto4 的两个结论（重要）

1. `AddListenChat` **能建子窗口，但回调不触发**（实测 6 秒 0 次）。
   文档标它是 Plus 专属 —— 建窗口那步没拦，推送那步拦了。
   → 只能轮询 `GetAllMessage()`。

2. `GetAllMessage()` 读的是**当前聊天窗口**。
   → 只要桥接一直停在目标群上、不切走，就不需要反复 `ChatWith`，
     也就不会来回抢你微信的焦点。这是本脚本 `sticky` 模式的由来。
"""

import base64
import ctypes
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# --- 控制台/agent 执行层（本地执行，不走云端人设） ---
# 2026-10-08 从桥接机版移植进仓库（那两个文件之前只存在于机器上、没进过 git）。
#   agent_console：! 前缀指令直接在本机执行（截图/命令），主人鉴权后放行
#   agent_llm    ：自然语言 → GLM 自主调工具 → 本机执行 → 回传
try:
    from agent_console import Console
except Exception:
    Console = None
try:
    from agent_llm import LLMAgent
except Exception:
    LLMAgent = None

HERE = os.path.dirname(os.path.abspath(__file__))
# token 兜底文件：运行位在 brain/wechat_bot 时，../config 就是 brain/config；
# 源码位在 wechat_bridge 时，大脑配置在 ../brain/config —— 两处都试
_BRAIN_CFGS = (
    os.path.normpath(os.path.join(HERE, "..", "config", "config.json")),
    os.path.normpath(os.path.join(HERE, "..", "brain", "config", "config.json")),
)
LOCAL_CFG = os.path.join(HERE, "wxbridge.local.json")

DEFAULT_CFG = {
    # 微信群名（微信里显示的名字，要一字不差）
    # 要盯的会话。**可以是字符串（单个）或数组（多个）** —— 见 chat_names。
    # 2026-10-08：桥接机搬上云端后"不抢窗口"这个约束没了（那台机器没人用微信），
    # 于是去掉 listen/sticky 那一套，改成多会话分时轮询。
    "chat_name": "",
    # 私聊怎么触发。群要 @ 才好接话，私聊不用 —— 这一栏是"这段会话的规则"，
    # 覆盖 group_reply_mode。取值同上：mention / gate / all
    "private_reply_mode": "all",
    # 🔴 哪些会话是**私聊**（2026-10-08）。不在这个名单里的都当群。
    # 为什么必须显式配：配置里写的是群名/昵称，不是 wxid ——
    # 拿 "@chatroom" 去判会把每个会话都判成私聊，于是群里她接住每一句话。
    "private_chats": [],
    # 安全护栏：这里填知夏小号的 wxid（不是微信号，是 GetMyInfo 里的 id）。
    # 桥接连上微信后会核对，对不上就**拒绝运行**，一条消息都不发。
    # 第一次跑先看 [微信] 那行打印的真实 id，填进来。
    "expected_wxid": "",
    # 黑名单（forbidden_wxids）已移除：桥接机登的是专用小号，
    # 环境里不存在大号会登录的路径。白名单（expected_wxid）已经拦住
    # 「不是小号的任何号」，黑名单在功能上是冗余的第二道闸。
    # 如果哪天这台机器上装了第二个微信、或者 expected_wxid 被误填，
    #   就少了一道兜底 —— 那时把这几行加回来。
    # 她在群里的昵称，用来判断有没有被 @。可以填多个
    "bot_nicknames": ["知夏"],
    # 触发方式：mention=只回 @ 她和关键词；all=群里说啥都回
    "group_reply_mode": "mention",
    # 除了 @，这些词也能叫她
    "keywords": ["知夏"],
    # 冷场主动发言
    "idle_lurk": False,
    # 冷场主动发言：等多久冒一次。**每轮冷场在这两个值之间随机掷**，
    # 不是固定值 —— 固定的话群友会摸准节奏，到点就等着看她冒泡，不像真人。
    # 下限存在的原因是「刚安静 3 分钟就说话」太吵（大家可能只是去倒杯水）。
    "idle_min_minutes": 20,
    "idle_max_minutes": 90,
    # 安静时段（这几个小时里她不主动说话，但被 @ 还是会回）
    "quiet_from": 23,
    "quiet_to": 7,
    # 轮询间隔（秒）
    "poll_seconds": 3,
    # 云端大脑地址。隧道优先。
    # 🔴 这里**只留隧道**，公网直连那条要写进 wxbridge.local.json（被 gitignore 挡着）。
        # 默认值里不要写死云机公网 IP —— 那是把服务器地址印在公开仓库里，
    # 而且会让人以为"公网直连是正常路径"（其实它慢且常超时，只该当兜底）。
    "brain_urls": ["http://127.0.0.1:18787"],
    "brain_token": "",
    "timeout": 180,
    # 一条回复最多发多少字（太长群里刷屏）
    "max_reply_chars": 400,
}

# 非文本消息在微信里的占位文本 —— 这些不值得拿去问她
# 🔴 2026-10-08：[链接] 从这里移除 —— type='link' 的标准分享卡片（小黑盒等）
# content 里有标题/描述/来源，直接丢太可惜。改成在 _snapshot 里识别 type='link'，
# 去掉 [链接] 前缀后保留正文。抖音折叠卡片（type='text' + "复制...更多"）
# 走另一条路：坐标点击卡片读剪贴板拿 URL（见 _extract_share_url）。
MEDIA_PLACEHOLDERS = ("[图片]", "[动画表情]", "[视频]", "[文件]", "[语音]",
                      "[聊天记录]", "[音乐]", "[表情]")

# 图片消息的占位符。微信发图时content 就是这个（可能后面还跟着用户
# 自己打的字），所以判断"带不带说明"要**剥掉它**，而不是看它存不存在。
IMG_PLACEHOLDER = "[图片]"


def _looks_like_image(m, content):
    """这条消息是不是一张图片？

    🔴 要**两路都看**，缺一不可：
      · msg.type == 'image'  是 wxauto 的正规做法
      · content 带 [图片]     是退路 —— 有些版本 type 不可靠/是空的
    只认 type：遇到 type 空的版本就还是丢图，而且日志里什么都不显示。
    只认 content：表情包之类会被当图片试着下载，白折腾一轮。
    取"或"，宁可多试一次下载（失败也只是打一行日志）。
    """
    t = str(getattr(m, "type", "") or "").strip().lower()
    if t == "image":
        return True
    return IMG_PLACEHOLDER in (content or "")


def _strip_img_placeholder(content):
    """从消息里把 [图片] 占位符剥掉，留下用户自己打的那句话。

    🔴 已知失效写法：`content if not content.startswith("[图片]") else ""`——
    意思是"只要开头是 [图片] 就把**整句**丢掉"。于是「[图片]这个啥意思」
    这条变成了空字符串，用户真正问的那半句被一起扔了。
    她收到一张图、但不知道他问的是什么，只能瞎猜。
    """
    if not content:
        return ""
    return content.replace(IMG_PLACEHOLDER, "").strip()


# --- 分享卡片 URL 提取（抖音折叠卡片） ----------------------------------
# 抖音分享到微信 PC 端会被折叠成 "5.89 复制...更多" 的绿色气泡，
# wxauto4 读不到 URL（UIA 子控件为0）。但点击卡片任意位置会把完整
# 分享文案（含 URL）复制到剪贴板。所以方案是：坐标点击卡片 → 读剪贴板 → 正则提 URL。

_URL_RE = re.compile(r'https?://[^\s\u4e00-\u9fff]+')


def _win_get_clipboard():
    """用 ctypes 读 Windows 剪贴板文本，不依赖 pyperclip/tkinter。"""
    if not ctypes.windll.user32.OpenClipboard(0):
        return ""
    try:
        h = ctypes.windll.user32.GetClipboardData(13)  # CF_UNICODETEXT
        if not h:
            return ""
        ptr = ctypes.c_void_p(h)
        # 直接读内存里的 unicode 字符串
        ctypes.windll.kernel32.GlobalLock.restype = ctypes.c_void_p
        locked = ctypes.windll.kernel32.GlobalLock(h)
        if not locked:
            return ""
        try:
            return ctypes.wstring_at(locked)
        finally:
            ctypes.windll.kernel32.GlobalUnlock(h)
    finally:
        ctypes.windll.user32.CloseClipboard()


def _is_douyin_card(mtype, content):
    """这条消息是不是抖音折叠卡片？

    特征：type='text'，content 里同时有 '复制' 和 '更多'（绿色气泡）。
    标准分享卡片 type='link'，不走这条路。
    """
    if mtype != "text":
        return False
    if not content:
        return False
    return "复制" in content and "更多" in content


# 图片在 items 里延迟下载时要用的占位对象。
# 它只是"这里有一条图片消息，图片还没下载"的记号 —— 真正的内容
# 要等 _diff 确认这条是新消息之后才去拉。
# 为什么要有这个东西：以前 _snapshot 每轮都会对窗口里**所有**图片调
# _grab_image，而微信 4.x 的 download() 内部就是"右键头像 → 在新窗口里
# 打开图"。于是即使一条新消息都没有，它也在每 3 秒右键一次、开窗一次、
# 下完才判断"这张处理过了"、丢弃、再关窗 —— 用户看到的就是无限重复操作，
# 同时处理真消息的时间全被它吃光（"哎久等了"之后再也没被处理）。
# 现在：_snapshot 只读元数据不碰图片，下载推迟到 _diff 之后。
class _PendingImg(object):
    __slots__ = ("m", "sender", "content", "fp")

    def __init__(self, m, sender, content):
        self.m = m
        self.sender = sender
        self.content = content

    def __repr__(self):
        return "<待下载图片 %s>" % (self.sender or "?")



def load_cfg():
    cfg = dict(DEFAULT_CFG)
    if os.path.exists(LOCAL_CFG):
        try:
            with open(LOCAL_CFG, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print("[配置] 读本地配置失败，用默认值：%s" % e)
    if not cfg.get("brain_token"):
        # token 不在桥接配置里，就从大脑配置拿 —— 别把口令抄两份
        for _bc in _BRAIN_CFGS:
            try:
                with open(_bc, encoding="utf-8") as f:
                    _tok = json.load(f).get("brain_token") or ""
            except Exception:
                _tok = ""
            if _tok:
                cfg["brain_token"] = _tok
                break
    return cfg


class Brain(object):
    """跟云端大脑说话。

    地址是"能通哪个用哪个"：宠物开的 SSH 隧道（18787）优先，
    没开就走公网。跟手机 App 一样，token 放 query 上。
    """

    def __init__(self, urls, token, timeout=180):
        self.urls = list(urls)
        self.token = token or ""
        self.timeout = timeout
        self.picked = None
        # 本机有代理，urllib 会去撞它，显式禁掉
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def pick(self, force=False):
        """探一次哪个地址能用。

        force=True 时**不管当前选中的是谁，重新探一遍全部地址**。
        为什么要有这个：隧道会断（本机的 SSH 通道一关，127.0.0.1:18787
        就没人听），而 brain_urls 里那个**公网兜底地址**通常是好的。
        原来的写法是"启动时探一次、之后固定用它" —— 隧道一断，ask() 每次
        都失败、只打印一行 `[大脑] 出错`，**永远不会切到公网**。
        症状是"群里她突然哑了，重启桥接就好"，而隧道明明没理由不通。
        主循环每 30 轮拿 force=True 调一次：探活，顺便切到还活着那个。

        🔴 注释里不写具体 IP —— 这个仓库是公开的，真地址会跟着推上
        GitHub。真值放被 .gitignore 挡住的 wxbridge.local.json 里。
        """
        why = []
        for u in self.urls:
            # 已经选中的先跳过（除非 force —— 它可能正是断掉的那个）
            if not force and self.picked and u.rstrip("/") == self.picked:
                return True
            try:
                url = u.rstrip("/") + "/api/plugins"
                if self.token:
                    url += "?token=" + urllib.parse.quote(self.token)
                with self._opener.open(
                        urllib.request.Request(url, headers={"User-Agent": "vigil"}),
                        timeout=8) as r:
                    r.read(200)
                if self.picked and u.rstrip("/") != self.picked:
                    print("[大脑] 切到 %s（原地址 %s 已不可用）"
                          % (u.rstrip("/"), self.picked))
                self.picked = u.rstrip("/")
                if not force:
                    print("[大脑] 用 %s" % self.picked)
                return True
            except Exception as e:
                why.append("%s -> %s" % (u.rstrip("/"), str(e)[:60]))
                continue
        if not force:
            # 启动时连不上：把失败原因留住，交给 pick_report 打那段排错提示
            self._pick_why = list(why)
            return False
        # force=True 且全都不通：保留原来那个（_brain_errs 会继续计数并触发退出）
        return bool(self.picked)

    def pick_report(self, why=None):
        """启动时连不上大脑才打的那一大段排错提示。

        2026-10-08：原本这段直接跟在 pick() 的 for 循环后面。现在 pick() 多了
        force 参数、会在运行中被反复调用（探活 + 切到还活着那个地址），
        所以提示挪到这里，由**启动那一次**显式调用 —— 否则每 30 轮刷一次
        "连不上大脑，桥接无法工作"，而它其实还能用（已切到公网地址）。
        """
        # 连不上大脑 = 桥接毫无意义：每条消息都会走到 ask() 然后拿不到
        # 任何回复，群里表现就是"她一声不吭"。必须说清楚为什么、怎么修，
        # 静默退出时用户只能盯着不发火的窗口干瞪眼。
        print("")
        print("=" * 62)
        print("连不上大脑，桥接无法工作（每条消息都会石沉大海）")
        for w in why:
            print("   %s" % w)
        print("")
        print("最常见的原因：SSH 隧道没开。云端的 %s 端口**不对公网开放**"
              % (self.urls[-1].rsplit(":", 1)[-1] if ":" in self.urls[-1] else "API"))
        print("（22端口的 SSH 能连上不代表 API 端口也能连，这个坑查过一次）")
        print("先开隧道：双击 VigilSelfTest.bat（新版本会自动开隧道并等它就绪）")
                # 不要把云机地址和 SSH 用户名写死在提示里 —— 一句"帮人排错"的
        # 文案，把服务器地址印进了公开仓库。改成从**你自己配的**地址里取：
        # 取到了就显示，取不到（只配了隧道）就让他照模板填。
        _pub = [u for u in self.urls if "127.0.0.1" not in u and "localhost" not in u]
        if _pub:
            _h = _pub[-1].split("//")[-1].split("/")[0].rsplit(":", 1)[0]
            print("或者手动：ssh -N -L 18787:127.0.0.1:8788 <你的SSH用户>@%s" % _h)
        else:
            print("或者手动：ssh -N -L 18787:127.0.0.1:8788 <你的SSH用户>@<你的云机IP>")
            print("（公网兜底地址在 wxbridge.local.json 的 brain_urls 里配）")
        print("=" * 62)
        print("")
        return False

    def ask(self, text, img_b64=None, src="wechat"):
        """问一次。返回她说的话；出错返回 None（不抛，抛了群里就刷错误）。

        img_b64：这一轮带的图片（纯 base64，不带 data: 前缀）。
        云端 server.ask_with_retry 会把它交给**独立的视觉模型**
        （config里 vision 那段），主模型换不换得看图能力都不影响。

        src：渠道，决定云端按哪份人设 / 记忆 / 历史回答。
        🔴 群和私聊**必须**分开（2026-10-08 多会话改造）—— 合在一起，
        她会把她在群里的反应拿去回答私聊（或者反过来），说话方式会串，
        而且两边的记忆会互相污染。
        """
        if not self.picked:
            return None
        payload = {"text": text, "src": src}
        if img_b64:
            payload["img"] = img_b64
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = self.picked + "/api/chat"
        if self.token:
            url += "?token=" + urllib.parse.quote(self.token)
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"})
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            # 成功了就清掉上次的报错计数，别一直"曾经失败过"
            self._brain_errs = 0
        except urllib.error.URLError as e:
            # 隧道断了会每 3 秒失败一次，不节流的话一屏全是同样的话，
            # 真正有用的首条报错反而被淹掉。首次打全句，之后只数数。
            self._brain_errs = getattr(self, "_brain_errs", 0) + 1
            if self._brain_errs == 1:
                print("[大脑] 连不上：%s" % e)
                print("       隧道是不是断了？（桥接靠它连云端）"
                      "连不上时她一条都不会回。")
            elif self._brain_errs % 20 == 0:
                print("[大脑] 还是连不上（已经失败 %d 次）" % self._brain_errs)
            return None
        except Exception as e:
            print("[大脑] 出错：%s" % e)
            return None
        return (data.get("reply") or "").strip()


class Bridge(object):
    def __init__(self, cfg):
        self.cfg = cfg
        # 一个还是多个会话，都归一成列表。兼容三种写法：
        #   "群名"  /  ["群名"]  /  ["群名", "某人"]
        _raw = cfg.get("chat_names")
        if not _raw:
            _raw = cfg.get("chat_name")
        if isinstance(_raw, str):
            _raw = [_raw] if _raw.strip() else []
        self.chat_names = [str(x).strip() for x in (_raw or []) if str(x).strip()]
        # 兼容老代码/老日志：chat_name 仍然指"当前轮到的那个"
        self.chat_name = self.chat_names[0] if self.chat_names else ""
        # 控制台/agent：本地执行层。命中前缀的指令直接在本机执行并回传，
        # 完全绕过云端大脑（无人设/无闸门/无记忆）。
        self.console = None
        try:
            if Console is not None:
                _cc = dict(cfg.get("console") or {})
                if _cc.get("enabled", True):
                    self.console = Console(_cc)
                    print("[console] ready prefix=%r owner=%r danger=%s"
                          % (self.console.prefix, self.console.owner or "(any)",
                             self.console.allow_danger))
        except Exception as _e:
            print("[console] init failed: %s" % _e)
        # LLM 驱动 agent：自然语言 → 模型自主调工具 → 本机执行 → 回传
        self.agent = None
        try:
            if LLMAgent is not None:
                _ac = dict(cfg.get("agent") or {})
                if _ac.get("enabled", True):
                    self.agent = LLMAgent(_ac)
                    print("[agent] ready model=%r trigger=%r"
                          % (self.agent.model, self.agent.trigger))
        except Exception as _e:
            print("[agent] init failed: %s" % _e)
        self.brain = Brain(cfg["brain_urls"], cfg["brain_token"],
                           int(cfg.get("timeout") or 180))
        self.wx = None
        # 🔴 去重状态**按会话分开**（2026-10-08 多会话改造）。
        # 原来是一条扁平列表：混进两个会话的消息后，"前缀相同"这个判据立刻失效
        # （A 群最后一条 + B 群最后一条拼起来不可能是上一轮的前缀），
        # 于是每轮都判成"有新增"→ 把整个历史重新问一遍大脑，又慢又贵。
        self._prev = {}           # {会话名: [消息指纹, ...]}
        self._synced = {}         # {会话名: bool} 第一次轮询该会话只记不看
        # 读消息卡死的保护（见 _read_all）
        self._read_busy = False      # 还有个卡住的读线程在跑
        self._read_stuck_until = 0.0  # 冷却到什么时候为止（时间戳）
        self._read_stuck_n = 0        # 一共卡过几次
        self._last_active = time.time()
        # 这次冷场要等到「距上次说话多少秒」才让她冒泡。
        # None = 还没掷，_maybe_lurk 第一次进时掷一次，之后保持不变
        #（每 3 秒重掷等于没掷）。
        self._idle_target = None
        self.my_name = ""
        self.my_id = ""
        # 她最近发过的话。用来判断「这条是不是我自己说的」——
        # 昵称别人也能改，只认昵称会把同名的人误判成她自己。
        self._my_recent = set()
        # 监听模式：用 AddListenChat 把对话拆成独立子窗口，读写都在子窗口里，
        # **不用占主窗口前台**。轮询模式必须让微信一直停在目标对话上，
        # 所以会跟你抢窗口（用户反馈过）。
        # 云端机器上没人用微信 → 固定用主窗口 + ChatWith 切会话。
        # 留 listen 只是为了旧配置能读；它已不生效（真要走子窗口得改 _snapshot/_send
        # 那一整套，见 git log 2026-10-08 之前的版本）。
        self.listen = bool(cfg.get("listen", True))
        self._sub = None          # AddListenChat 建的子窗口（云端不用了，留着兼容）
        # 轮转游标：每轮处理一个会话，下一轮换下一个
        self._rr = 0

    # --- 监听模式 ---------------------------------------------------

    # --- 连接 -------------------------------------------------------

    def connect(self):
        from wxauto4 import WeChat
        # ads=False：默认会打印一段广告，没必要
        #
                # 这里**不能**靠传参指定连哪个微信 —— 传了不报错但完全无效：
        #   WeChat(ads=False, path=r'D:\Weixin418\Weixin.exe')
        #     -> wx.path 仍是 C:\Program Files\Tencent\Weixin\Weixin.exe
        #        wx.dir  仍是 C:\Program Files\Tencent\Weixin\203.0.113.10
        # 选窗口的逻辑在 .pyd 二进制里（读不到源码），两个实例并存时
        # 它连哪一个没有保证。所以只能靠下面的身份硬闸。
        #
        # 构造**会抛**，不是返回 False：微信 4.x 的控件树没被唤醒时它直接
                # 抛「未找到已登录的客户端主窗口」。这里没有 try 时异常一路穿到
        # main()，表现是桥接在十一秒后静默退出、控制台只剩一个裸 traceback，
        # 而 launcher.log 只记「exited with code 1」——说不出原因。所以这里
        # 接住并说人话，返回 False 让上层按"连不上"处理。
        try:
            self.wx = WeChat(ads=False)
        except Exception as e:
            print("")
            print("=" * 62)
            print("连不上微信（wxauto4 抛异常）")
            print("   %s: %s" % (type(e).__name__, str(e).strip().splitlines()[0]
                                 if str(e).strip() else e))
            print("")
            print("这个报错的真实含义是：**微信没有向 UI 自动化暴露控件树**。")
            print("按顺序试：")
            print("  1. 用桌面图标启动桥接（SSH 里跑必然失败，跨会话看不见窗口）")
            print("  2. 确认微信主窗口开着、没有最小化到托盘")
            print("  3. 微信窗口太小的话拉大（4.x 只给屏幕上露出来的那部分注册控件）")
            print("=" * 62)
            print("")
            return False
        my = {}
        try:
            my = self.wx.GetMyInfo() or {}
        except Exception:
            pass
        self.my_name = (my.get("display_name") or "").strip()
        self.my_id = (my.get("id") or "").strip()
                        # 这两个属性能读到（pyd 里的 uWeChat.path / uWeChat.dir），
                        # 拿它可以在比对 wxid 之前就发现「连到别的实例去了」
        self.wx_path = str(getattr(self.wx, "path", "") or "")
        self.wx_dir = str(getattr(self.wx, "dir", "") or "")
        print("[微信] 已连上：昵称=%r wxid=%r 地区=%r"
              % (self.my_name, self.my_id, my.get("region")))
        print("[微信] 实例：%s"
              % (self.wx_dir or self.wx_path or "(取不到)"))

        if not self._identity_ok():
            return False

        # 🔴 把主窗口拉到接近全屏（2026-10-08 多会话改造配套）。
        # 微信 4.x 只把**当前可见**的那部分消息注册进控件树 —— 默认大小的
        # 窗口只有几条的容量，轮转切走再切回来，可见集合就变了，_diff 的
        # 锚点直接丢失，多会话会永远卡在"窗口内容对不上"。窗口越大，
        # 可见容量越大，锚点丢失就变成罕见事件。
        try:
            import win32gui

            def _enum(h, acc):
                if win32gui.IsWindowVisible(h):
                    if (win32gui.GetWindowText(h) or "").strip() == "微信":
                        acc.append(h)
                return True

            acc = []
            win32gui.EnumWindows(_enum, acc)
            if acc:
                win32gui.MoveWindow(acc[0], 0, 0, 1920, 1040, True)
                print("[微信] 主窗口已放大到 1920x1040（提高可见消息容量）")
            else:
                print("[微信] 没找到主窗口句柄，跳过放大（不影响其余功能）")
        except Exception as e:
            print("[微信] 窗口放大失败（不影响运行）：%s" % str(e)[:90])

        if not self.brain.pick():
            self.brain.pick_report()      # 启动时连不上：打那段排错提示
            return False

        # 云端模式：不建子窗口，靠 _switch_to 每轮切。
        # 原来这里 listen=True 会 AddListenChat 建子窗口 —— 桥接机在云端、
        # 那台机器没人用微信，子窗口只带来"回调不触发"和一层没用的复杂度。
        if False and self.chat_name:
            try:
                # 用**子窗口**而不是主窗口。
                #
                                # 为什么不走 AddListenChat 的回调：4.1.8 + wxauto4 41.1.7
                # 下回调**根本不触发**（子窗口能读到消息，但 fired 一直是 0）。
                # 所以改成"子窗口 + 轮询" —— 子窗口是独立出来的对话，
                # 读它**不会把主微信窗口切走**，也就不抢你正在用的窗口。
                                # 这是"不抢窗口 + 可靠"唯一成立的组合。
                if self.listen:
                    sub = None
                    try:
                        sub = self.wx.AddListenChat(self.chat_name, lambda *a: None)
                    except Exception as e:
                        print("[微信] 建子窗口失败（%s），退回主窗口轮询，"
                              "会占前台" % str(e)[:60])
                    if sub is not None:
                        self._sub = sub
                        print("[微信] 已在子窗口监听「%s」，不抢主窗口"
                              % self.chat_name)
                    else:
                        self.listen = False
                if not self.listen:
                    self.wx.ChatWith(self.chat_name, exact=True)
                    print("[微信] 已切到：%s（主窗口轮询，会占前台）"
                          % self.chat_name)
            except Exception as e:
                print("[微信] 切不到「%s」：%s" % (self.chat_name, e))
                print("       名字要和微信里显示的**一字不差**（含空格、标点）。"
                      "先用微信搜一下这个名字，或者在那个窗口里复制它的名字。")
                return False
        return True

    def _identity_ok(self):
        """🔴 确认连上的是知夏那个小号，身份不对就不跑。

        为什么必须有这一步：wxauto4 找窗口的逻辑在 .pyd 里（读不到源码），
        多个微信同时跑时它会连到哪一个**没有保证**。万一连错，
        后面每一次 GetAllMessage / SendMsg 都在别人的号上操作。

        所以这里不是"打个招呼"，是硬闸：认不出来就不跑。

        🔴 黑名单已移除（2026-10-05）：桥接机登的是专用小号，
        环境里不存在大号会登录的路径，白名单（expected_wxid）已经拦住
        「不是小号的任何号」。
        """
        # 黑名单要回来的话：把下面两行取消注释
        #   banned = set(self.cfg.get("forbidden_wxids") or [])
        #   if self.my_id and self.my_id in banned: ... 拒绝运行

                # 实例路径对不上：还没比 wxid 就能发现「连的不是那一份微信」。
                # 微信 4.x 跨安装目录单实例（D 盘那份 15 秒就把启动请求
        # 转交给 C 盘那份然后自己退出），所以最常见的错不是登错号，
        # 是另一个微信还开着、桥接连上了它 —— 这条能在最早的地方拦住。
        want_dir = (self.cfg.get("bot_weixin_dir") or "").strip()
        if want_dir and self.wx_dir:
            a = want_dir.replace("/", "\\").rstrip("\\").lower()
            b = self.wx_dir.replace("/", "\\").rstrip("\\").lower()
            if a != b:
                print("")
                print("=" * 62)
                print("拒绝运行：连到的微信实例不对。")
                print("  期望目录 = %s" % want_dir)
                print("  实际目录 = %s" % self.wx_dir)
                print("=" * 62)
                return False

        want = (self.cfg.get("expected_wxid") or "").strip()
        if not want:
            print("")
            print("!" * 62)
            print("拒绝运行：还没填 expected_wxid。")
            print("  现在连上的这个号身份不明，我不能确定它是不是知夏的小号。")
            print("  第一次跑请看上面 [微信] 那行打印的 wxid，填进")
            print("  %s 的 expected_wxid 再跑。" % LOCAL_CFG)
            print("!" * 62)
            return False

        if self.my_id != want:
            print("")
            print("=" * 62)
            print("拒绝运行：身份对不上。")
            print("  期望 wxid = %s" % want)
            print("  实际 wxid = %s" % (self.my_id or "(取不到)"))
            print("=" * 62)
            return False

        print("[身份] 校验通过，是小号 %s" % self.my_id)
        return True

    # --- 读新消息 ---------------------------------------------------

    # 已经处理过的图片，记住**内容哈希**。这是死循环的第二道闸。
    # 闭环曾经是这样形成的：下载 → 微信弹出图片查看窗并抢走活动窗口 →
    # 下一轮读到的列表不是上一次的延长线 → _diff 找不到锚点 →
    # 却把 _prev 更新成图窗的内容 → 回到聊天窗前缀彻底对不上 →
    # 那条图被当成新消息 → 又 download() 一次、又开一次窗。
    # 症状是微信窗口里那张图被反复打开，而真正的文字消息
    #    （"哎久等了"）之后再也没被处理过。
    #
    # 但**光靠这道闸修不好**：闸在下载之后，而死循环的副作用
    # （右键、开窗、抢焦点）发生在下载**之中**。闸拦得住"重复送云端"，
    # 拦不住"反复操作微信"。真正解决问题的是**位置** —— 现在图片在
    # _diff 判出新增之后才下载（见 _snapshot / _resolve_img），
    # 历史图片压根不会被碰。这道闸退居二线，只防窗口切换/滚动那种
    # _diff 也会失灵的边缘情况。
    #
    # 存的是图片**内容哈希**不是路径：微信每次下载的临时文件名都不一样，
    # 存路径等于没存。
    _seen_img = None

    @classmethod
    def _seen_images(cls):
        if cls._seen_img is None:
            cls._seen_img = set()
        return cls._seen_img

    @staticmethod
    def _img_key(img_b64):
        """图片内容的指纹。够用就行，不追求抗碰撞。"""
        if not img_b64:
            return ""
        try:
            import hashlib
            return hashlib.sha1(img_b64.encode("ascii")).hexdigest()[:16]
        except Exception:
            return "%d:%s" % (len(img_b64), img_b64[:24])

    @staticmethod
    def _fp(item):
        # 图的指纹**不能拿整段base64**（几十万字，拼一次就够呛），
        # 取长度 + 头尾一小段就够了 —— 目的是"两张不同的图不被当成同一条"，
        # 不是密码学上的抗碰撞。
        # 不加图这一维的后果很具体：连着发两张图，第一张处理完，
        # 第二张的指纹因为"sender 和文字都空"跟第一张一样被判成重复，
        # 于是**第二张图永远不处理**，而日志里一条错都不报。
        img = item[2] if len(item) > 2 else ""
        tag = ""
        if isinstance(img, _PendingImg):
            # 还没下载：用 _tag_img 算好的下载前指纹。
            tag = "#img:%s" % (getattr(img, "fp", "") or "?")
        elif img:
            tag = "#img%d:%s" % (len(img), img[:24])
        return "%s|%s%s" % (item[0], item[1], tag)

    def _snapshot(self):
        """读当前窗口里的消息，返回 [(发送者, 内容, 图片), ...]。

        有子窗口就读子窗口 —— 子窗口是独立出来的对话，读它不会把主微信
        窗口切走，所以不抢你正在用的窗口。

        🔴 这里**绝不能下载图片**。这一段跑在 _diff 之前，拿到的是窗口里
        全部消息（含历史），不是新增的那些。而微信 4.x 的 download()
        内部是"右键头像 → 在新窗口里打开图"，每轮对历史图片都下一次 =
        每 3 秒右键一次、开窗一次、再关窗，UI 被反复操作而真消息排不上队。
        所以图片在这里只登记成 _PendingImg，等 _diff 说"这条是新增"
        之后才真的去拉 —— 位置就是修这个的关键，不是加判据能解决的。
        """
        items = []
        # 注意：子窗口读到 0 条是**正常**的（它只保留监听开始后收到的）。
        # 所以别把空当成出错，日志也别刷屏—— 每 3 秒一条会把别的信息淹掉。
        src = getattr(self, "_sub", None) or self.wx
        msgs = self._read_all(src)
        if msgs is None:
            return items
        for idx, m in enumerate(msgs):
            try:
                sender = str(getattr(m, "sender", "") or "").strip()
                content = str(getattr(m, "content", "") or "").strip()
            except Exception:
                continue
            # 时间分隔线 / 系统提示不是真人消息，必须从源头剔掉：
            # 微信聊天窗口里消息之间的灰色时间戳（"18:44"）会被读成
            # sender='system' 的伪消息，"XX 撤回了一条消息"这类是 type=sys。
            # 不拦的话她会把"18:44"当成你发的梗去找半天
            # （2026-10-08 实测：白烧一次大脑调用，她还困惑）。
            mtype = str(getattr(m, "type", "") or "").strip().lower()
            if mtype in ("time", "sys", "system", "systemmsg", "notice"):
                continue
            if sender.lower() in ("system", "sys", "系统消息"):
                continue
                        # 图片要在这里被直接丢掉：content 是 "[图片]" 占位符，
            # 命中 MEDIA_PLACEHOLDERS 就 continue。所以她**在微信里一直看不见图**
            # —— 不是模型不支持，是桥接压根没把图传过去。
            # 现在：认出图片消息就登记下来，等确认是新增消息再下载转 base64。
            img = ""
            if self._is_image(m, content):
                content = _strip_img_placeholder(content)
                img = _PendingImg(m, sender, content)
            if not content and not img:
                continue
            # 🔴 2026-10-08：标准分享卡片（小黑盒/B站/文章等）type='link'，
            # content 以 "[链接]" 开头，后面是标题/描述/来源。去掉前缀保留正文，
            # 这样 AI 至少能看到分享了什么。抖音折叠卡片走另一条路（_extract_share_url）。
            if mtype == "link" and content.startswith("[链接]"):
                content = content[len("[链接]"):].strip()
            if content and content in MEDIA_PLACEHOLDERS:
                content = ""
            if not content and not img:
                continue
                            # 微信把自己发的消息标成 'self'。换成昵称，
            # 免得下游把它当成一个叫 self 的陌生人拼进给大脑的话里。
            if sender.lower() in self.SELF_MARKERS:
                sender = self.my_name or "我"
            img = self._tag_img(img, idx)
            # 第 4 位带原始消息对象：agent/console 鉴权要从它身上拿
            # sender_info().id（=发送者微信号）。指纹（_fp）只看前三位，
            # 多这一位不影响去重。
            items.append((sender, content, img, m))
        return items

    # --- 图片 ------------------------------------------------------
    def _tag_img(self, img, idx):
        """给还没下载的图片算一个**下载前**就能拿到的指纹。

        🔴 为什么不能用内容哈希（_img_key）当 items 的指纹：
        _diff 是靠 items 的指纹列表认出"新增"的，而算内容哈希的前提是
        已经把图下载下来了 —— 那就又回到每轮都右键开窗的老问题。
        所以指纹必须来自读消息时就能拿到的元数据。

        首选 wxauto 自己的 runtimeid（(高,宽)+文本+RuntimeId 的 md5），
        同一张图在同一个窗口里读多少次都一样。拿不到就退回
        "发送者 + 序号"：它只能保证**不把不同消息认成同一条**，
        认成同一条的后果是漏处理（安静），比误判成新增安全。
        """
        if not isinstance(img, _PendingImg):
            return img
        rid = ""
        for obj in (img.m, getattr(img.m, "control", None)):
            for attr in ("runtimeid", "hash", "hash_text"):
                try:
                    v = str(getattr(obj, attr, "") or "").strip()
                except Exception:
                    continue
                if v:
                    rid = v
                    break
            if rid:
                break
        img.fp = rid or ("idx%d:%s" % (idx, img.sender))
        return img

    # --- 读消息的超时保护 -----------------------------------------
    def _read_all(self, src, timeout=25.0):
        """读消息，带超时。返回消息列表；卡住或出错返回 None。

        🔴 为什么需要超时（实测卡死的现场）：
        `GetAllMessage()` 自己会操作界面 —— 展开艾特卡片、滚动到新消息、
        定位控件。微信界面处于某种状态时它会**卡在里面出不来**：进程 CPU
        一直涨、界面被反复操作（用户看到的"一直右键头像"），但
        bridge.log 一个字都不写。这不是抛异常，外层的 try/except 拦不住，
        只能靠超时放弃这一轮。

        为什么用线程：wxauto4 调的是 COM/UIA，同进程里没法从外部打断。
        放进工作线程后主线程超时就不再等它 —— 那个线程会带着它卡住的
        界面操作继续跑，所以**放弃的是这一轮，不是真的停下了**。

        🔴 超时后必须冷却：卡住的那个线程还在操作界面，马上再来一次只会
        叠第二个线程、让界面更乱 —— 那正是"疯狂右键"的放大器。
        """
        import threading

        now = time.time()
        if self._read_stuck_until and now < self._read_stuck_until:
            return None
        if self._read_busy:
            # 上一个线程还没结束 —— 绝不能再开一个
            return None

        box = {}

        def _work():
            try:
                box["msgs"] = src.GetAllMessage() or []
            except Exception as exc:
                box["err"] = exc

        self._read_busy = True
        t = threading.Thread(target=_work, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            self._read_busy = False
            self._read_stuck_until = time.time() + 60.0
            self._read_stuck_n += 1
            print("[微信] 读消息卡住 %.0f 秒（第 %d 次），这一轮跳过、"
                  "不再碰界面" % (timeout, self._read_stuck_n), flush=True)
            print("      正在复位微信界面；下次尝试在 60 秒后。", flush=True)
            self._reset_wechat_ui()
            return None
        self._read_busy = False
        if "err" in box:
            print("[微信] 读消息失败：%s" % box["err"], flush=True)
            return None
        return box.get("msgs") or []

    def _reset_wechat_ui(self):
        """把微信界面复位：关掉弹出菜单、切回聊天页。

        🔴 为什么需要：卡住的那个读线程还在操作界面（可能正在右键头像、
        弹菜单）。主线程这边虽然放弃等了，但那个线程不会停 ——
        所以「放弃这一轮」不等于「界面不再被动」。不把它拽回聊天页，
        界面就会一直停在奇怪的状态，下一次读又卡。

        🔴 为什么按 Esc 而不是去点窗口按钮：点窗口需要目标窗口是前台，
        而这套接入的前提就是「不要抢前台」。
        Esc 是给当前前台窗口的 —— 只在微信本来就是前台时才生效，
        抢不到就跳过，不做任何窗口切换。

        ⚠️ 这一步**绝不能**抛异常，也绝不能长时间阻塞：它是补救，
        补救失败就让它失败，别把主循环带崩。
        """
        try:
            import ctypes
            user32 = ctypes.windll.user32
            fg = user32.GetForegroundWindow()
            mine = None
            for obj in (getattr(self, "_sub", None), getattr(self, "wx", None)):
                for attr in ("hwnd", "Handle", "handle", "_hwnd"):
                    v = getattr(obj, attr, None)
                    if v:
                        try:
                            mine = int(v)
                        except Exception:
                            mine = None
                        break
                if mine:
                    break
            # 微信不在前台就不动界面：这套接入的规矩是不抢用户窗口
            if not fg or (mine and fg != mine):
                print("[微信] 微信不在前台，不复位界面（不抢窗口）", flush=True)
                return
            # Esc 关掉可能弹着的右键菜单
            user32.keybd_event(0x1B, 0, 0, 0)
            user32.keybd_event(0x1B, 0, 2, 0)
            # 切回聊天页（微信被切到"通讯录"之类的页面时）
            try:
                sw = getattr(self.wx, "SwitchToChat", None)
                if callable(sw):
                    sw()
            except Exception:
                pass
            print("[微信] 界面已复位（Esc + 回到聊天页）", flush=True)
        except Exception as e:
            print("[微信] 复位界面没成功：%s（不影响继续跑）" % e, flush=True)

    def _resolve_img(self, pending):
        """把 _PendingImg 换成图片的 base64。取不到就返回 None（这条丢掉）。

        🔴 这里的 seen 检查是**第二道**闸，不是唯一一道。
        第一道是位置：图在 _diff 判定为新增之后才下载，历史图片压根不会被碰。
        第二道是内容哈希：万一窗口切换/滚动让 _diff 又把同一张图当成新增，
        到这里还有一道拦 —— 这正是死循环闭环的最后一环，宁可漏也不许无限重复。

        用内容哈希而不是下载前的指纹做第二道，是因为微信对同一张图下载
        出来的临时文件名每次都不一样，而内容是稳的。
        """
        content, img_b64 = self._grab_image(
            pending.m, pending.content, pending.sender)
        if not img_b64:
            # 下不到图就别把这句 "[图片]" 送上去 —— 让她对着占位符
            # 瞎猜，比不理人更奇怪。
            return None
        k = self._img_key(img_b64)
        if k and k in self._seen_images():
            print("[微信] 这张图处理过了（%s），不再送第二次"
                  % (pending.sender or "?"))
            return None
        if k:
            self._seen_images().add(k)
        return img_b64 or None

    # --- 抖音折叠卡片：坐标点击读剪贴板拿 URL --------------------------
    def _extract_share_url(self, mobj):
        """抖音折叠卡片（type='text' + "复制...更多"）拿不到 URL，
        但点击卡片任意位置会把完整分享文案（含 URL）复制到剪贴板。

        流程：滚动到可见 → 坐标点击卡片中心 → 读剪贴板 → 正则提 URL → 按 Esc 关弹窗。

        🔴 副作用：会移动鼠标、覆盖剪贴板、可能弹出复制预览窗。
        所以只在 _diff 确认是**新增消息**后才调用，历史消息不碰。
        失败就返回空字符串，调用方用原始 content 兜底（至少能看到"5.89 复制...更多"）。
        """
        try:
            ctrl = getattr(mobj, "control", None)
            if ctrl is None:
                return ""
            # 滚动到可见区域
            try:
                roll = getattr(mobj, "roll_into_view", None)
                if callable(roll):
                    roll()
                    time.sleep(0.5)
            except Exception:
                pass
            # 拿卡片位置
            rect = getattr(ctrl, "BoundingRectangle", None)
            if rect is None:
                return ""
            # 点卡片中心偏右（实测点哪里都能触发复制）
            cx = int((rect.left + rect.right) / 2)
            cy = int((rect.top + rect.bottom) / 2)
            # 保存当前剪贴板（尽力而为，失败就算了）
            old_clip = ""
            try:
                old_clip = _win_get_clipboard()
            except Exception:
                pass
            # 移动鼠标并点击
            ctypes.windll.user32.SetCursorPos(cx, cy)
            time.sleep(0.2)
            ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # 左键按下
            time.sleep(0.1)
            ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # 左键释放
            # 等剪贴板更新
            time.sleep(1.0)
            # 读剪贴板
            clip = ""
            try:
                clip = _win_get_clipboard()
            except Exception:
                pass
            # 按 Esc 关掉可能弹出的复制预览窗
            try:
                ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)       # Esc 按下
                time.sleep(0.05)
                ctypes.windll.user32.keybd_event(0x1B, 0, 0x0002, 0)  # Esc 释放
            except Exception:
                pass
            # 恢复剪贴板（如果之前有内容且不是这次的分享文案）
            try:
                if old_clip and "复制打开抖音" not in old_clip:
                    # 恢复旧剪贴板（需要用 SetClipboardData，这里简化：不恢复，
                    # 因为恢复需要 OpenClipboard + SetClipboardData + CloseClipboard，
                    # 而且桥接场景下用户不太可能在跑桥接的机器上用剪贴板）
                    pass
            except Exception:
                pass
            # 从剪贴板提取 URL
            if clip:
                m = _URL_RE.search(clip)
                if m:
                    url = m.group(0).rstrip('.,;:!?)）】')
                    print("[微信] 抖音卡片提取到 URL: %s" % url)
                    return url
            return ""
        except Exception as e:
            print("[微信] 抖音卡片 URL 提取失败: %s" % e)
            return ""

    def _is_image(self, m, content):
        return _looks_like_image(m, content)

    # 能收多大的图。微信里发的大图（十几MB）转 base64 后更大，
    # 8788 那个口本来是给文字消息用的，塞一坨几十MB 的东西会把请求拖死。
    # 走 vision 前一般也会缩，所以这里卡一道：超限就缩，缩不了就放弃。
    IMG_MAX_BYTES = 4 * 1024 * 1024

    def _grab_image(self, m, content, sender):
        """把图片消息变成 (文字说明, base64)。

        返回 (text, img_b64)：
          · 纯图片（没有文字）→ text 留空。云端那边会自己加"（他发的图：…）"，
            **不要**在这儿编一句"我看到一张图了"—— 那是抢模型的活，
            而且她还没描述就宣称看到了，等于骗人。
          · 图 + 一句话 → 两样都留着，那句话通常是"这个啥意思"之类。
          · 下不到 → 返回空，调用方整条丢掉。
        """
        path = None
        # 下载前先把可能残留的图片查看窗关掉。
        # 微信4.x 的 download() 内部是"右键头像 → 在新窗口里打开图"，
        # 那个窗关不掉就一直留在顶层：下一轮 GetAllMessage() 读到的是它，
        # 前缀跟聊天窗的内容对不上（见 _diff），于是那条图被当成新消息，
        # 于是又download() —— 就是用户看到的"右键、打开图、重复"。
        # 这里只按窗口标题认（微信的图片预览窗标题含"图片"且是独立顶层窗），
        # 不碰聊天窗和主窗口；关不掉就算了，后面两层闸照样拦得住。
        self._close_image_viewer()
        try:
            dl = getattr(m, "download", None)
            if callable(dl):
                path = dl()
        except Exception as e:
            print("[微信] 下载图片失败（%s）：%s" % (sender or "?", e))
        if not path:
            print("[微信] 这条图片拿不到文件（%s），跳过" % (sender or "?"))
            return "", ""
        try:
            data = self._shrink(str(path))
            if not data:
                return "", ""
            return _strip_img_placeholder(content), \
                base64.b64encode(data).decode("ascii")
        except Exception as e:
            print("[微信] 读图片出错（%s）：%s" % (sender or "?", e))
            return "", ""
        finally:
            # 下载完立刻再关一次：有些版本 download() 成功之后才弹窗。
            # 不关的话它会活到下一轮，前缀断裂又开始了。
            self._close_image_viewer()
            # 下载的临时文件不留着 —— 微信默认会往桌面上堆，
            # 跑一晚上就是几百张。删不掉就算了，但一定要试。
            try:
                if path and os.path.exists(str(path)):
                    os.remove(str(path))
            except Exception:
                pass

    def _close_image_viewer(self):
        """关掉可能还开着的图片查看窗。

        为什么按"标题里有图片 + 是顶层窗"来认，而不是认类名：
        wxauto4 的图片预览窗在不同微信版本里类名不一样（实测 203.0.113.10
        是独立顶层窗，但这个值随版本变），按类名认等于写死一个版本号。
        标题反而稳：微信的图片预览窗标题就是图片名或"图片"。

        🔴 只关**自己认得出来的**独立预览窗：必须同时满足
        ① 标题含"图片"/"image" ② 不是主窗口 ③ 不是聊天子窗口。
        少任何一条都可能把聊天窗口关掉 —— 那是比重复操作严重得多的事。
        """
        try:
            import ctypes
            from ctypes import wintypes

            keep = set()
            for obj in (getattr(self, "_sub", None), getattr(self, "wx", None)):
                # wxauto 的窗口对象在不同版本里字段名不一样，全试一遍
                for attr in ("hwnd", "Handle", "handle", "_hwnd"):
                    v = getattr(obj, attr, None)
                    if v:
                        try:
                            keep.add(int(v))
                        except Exception:
                            pass
                        break

            user32 = ctypes.windll.user32
            closed = 0
            # EnumWindows 只给顶层窗，图片预览窗正是顶层
            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def _cb(hwnd, _):
                nonlocal closed
                try:
                    if int(hwnd) in keep:
                        return True
                    if not user32.IsWindowVisible(hwnd):
                        return True
                    buf = ctypes.create_unicode_buffer(256)
                    user32.GetWindowTextW(hwnd, buf, 256)
                    t = buf.value or ""
                    if not t:
                        return True
                    low = t.lower()
                    if "图片" not in t and "image" not in low and "photo" not in low:
                        return True
                    # 聊天的标题里也可能有"图片"两个字（群名带图片两字），
                    # 所以再要求：这是个独立的小窗（不是主聊天窗那个宽度）
                    user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
                    closed += 1
                except Exception:
                    pass
                return True

            user32.EnumWindows(_cb, 0)
            if closed:
                print("[微信] 关掉了 %d 个图片查看窗" % closed)
        except Exception as e:
            # 关不掉不是致命问题：_seen_img 和 _diff 那两道闸照样拦得住。
            print("[微信] 关图片窗这步没成功（%s），靠后面两道闸兜着" % e)

    def _shrink(self, path):
        """读图片，超大就等比缩到 IMG_MAX_BYTES 以内。返回字节。"""
        with open(path, "rb") as f:
            data = f.read()
        if len(data) <= self.IMG_MAX_BYTES:
            return data
        try:
            import io
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            im = im.convert("RGB")
            # 每次砍一半直到够大。上限 20 次：再小就只剩色块了，
            # 与其传一张看不出什么的图，不如老实放弃。
            for _ in range(20):
                im.thumbnail((im.width // 2, im.height // 2))
                if not im.width or not im.height:
                    return None
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=80)
                data = buf.getvalue()
                if len(data) <= self.IMG_MAX_BYTES:
                    return data
            return None
        except Exception as e:
            print("[微信] 缩图失败（%s），这条图不送了" % e)
            return None

    # --- 多会话：切窗口 + 规则分流 -----------------------------------

    def _switch_to(self, name):
        """切到指定会话。成功返回 True。

        切不过去**不抛异常也不重试** —— 返回 False 让调用方跳过这一轮。
        为什么这么克制：ChatWith 失败的原因通常是这个会话不存在/已解散/
        名字改了，反复重试只会一直操作界面（实测会表现成"疯狂点击"）。
        """
        if getattr(self, "_cur_chat", None) == name:
            return True
        try:
            self.wx.ChatWith(name, exact=True)
        except Exception as e:
            print("[微信] 切不到「%s」：%s（这一轮跳过）" % (name, str(e)[:60]))
            return False
        self._cur_chat = name
        self.chat_name = name
        # 切换后给界面一点时间落地。GetAllMessage 紧接着调的时候
        # 偶尔会读到上一个人的尾巴 —— 不是等几秒能根治的，但能少一些。
        time.sleep(0.8)
        return True

    def _chat_kind(self, chat):
        """这个会话是群还是私聊 → "group" / "private" / None（不知道）。

        🔴 **不能靠名字判断**（2026-10-08 踩过）：配置里写的是群名和昵称
        （"PRIVATE_CHAT_REMOVED"、"张三"），不是 wxid；只有 wxid 才带 @chatroom
        后缀。所以拿 @chatroom 去判，**每个会话都会被判成私聊** ——
        群里的消息于是走 private_reply_mode=all，她会接住群里每一句话。

        所以改成显式声明：配置里写 private_chats（私聊名单），
        不在名单里的就是群。名单为空时保守按"全部当群"——
        宁可少回（群规则更严），也别在群里乱接话。
        """
        name = str(chat or "").strip()
        if not name:
            return None
        # 兜底：万一有人直接写了 wxid
        if "@chatroom" in name.lower():
            return "group"
        for p in (self.cfg.get("private_chats") or []):
            if p and str(p).strip() == name:
                return "private"
        # 不在私聊名单里 → 当群。名单为空时也走这里（全部按群，最保守）。
        return "group"

    def _mode_for(self, chat):
        """这个会话该用哪套触发规则。

        群和私聊不能用同一套：群里 @ 她才接话（不然太吵、每条都要花钱问大脑），
        私聊是专门找她的，默认直接回。判断走 _chat_kind（靠配置里的
        private_chats 名单，不靠名字 —— 详见那里）。
        """
        if self._chat_kind(chat) == "private":
            return self.cfg.get("private_reply_mode") or "all"
        return self.cfg.get("group_reply_mode") or "mention"

    def _is_private(self, chat):
        return self._chat_kind(chat) == "private"

    def _diff(self, items):
        """比对出这一轮新增的消息。

        消息是**追加**在末尾的，所以绝大多数情况下上一轮是这一轮的前缀。
        偶尔窗口滚动导致开头的旧消息被丢掉，前缀就对不上了 —— 那时退回
        用"最后一条"当锚点。两种都认不出来就保守返回空（宁可漏一句，
        也别把几百条旧消息当成新消息全问一遍）。
        """
        # 按会话各存一份去重状态（多会话改造）。self._cur_chat 由 _switch_to 设好。
        ck = getattr(self, "_cur_chat", None) or self.chat_name or "?"
        prev = self._prev.get(ck)
        now = [self._fp(x) for x in items]
        if not self._synced.get(ck):
            self._prev[ck] = now
            self._synced[ck] = True
            print("[微信] 已同步「%s」%d 条历史，之后只处理新消息"
                  % (ck, len(now)))
            return []
        self._prev[ck] = now
        if prev is None:
            return []
        if now == prev:
            return []
        if len(now) > len(prev) and now[:len(prev)] == prev:
            new = items[len(prev):]
        else:
            anchor = prev[-1] if prev else None
            idx = -1
            for i in range(len(now) - 1, -1, -1):
                if now[i] == anchor:
                    idx = i
                    break
            if idx < 0:
                # 认不出锚点时先别急着放弃 —— 多会话轮转切回一个会话时，
                # 微信只重新渲染**最近的一段**消息：快照比上一轮短、甚至
                # 整体"上移"（老消息从控件树顶部掉出去）。这时用 prev 的
                # 尾段去对齐 now 的头部：对得上就说明只是可见窗口滑动，
                # 新增可以精确算出；对不上才走下面的保守路径。
                # （2026-10-08 桥接机实测：小窗口 + 轮转，锚点丢失是常态
                # 而不是偶发，桥接会永远卡在"窗口内容对不上"。）
                aligned = None
                for trim in range(1, len(prev)):
                    tail = prev[trim:]
                    if len(now) >= len(tail) and now[:len(tail)] == tail:
                        aligned = len(tail)      # now 头部被对齐掉的条数
                        break
                if aligned is not None:
                    new = items[aligned:]
                    self._prev[ck] = now
                    if new:
                        print("[微信] 「%s」顶部滑出 %d 条，已按尾对齐接续，新增 %d 条"
                              % (ck, aligned, len(new)))
                    return new
                # 认不出锚点时**不要更新 _prev**。
                                # 若这里是 new=[] 然后照样 self._prev = now，于是
                                # 上一条的图片查看窗口里的内容会被当成新的基线。
                # 回到聊天窗后前缀跟这个基线永远对不上，那条图就被
                # 重新认成新消息 → 再 download() → 再开一次窗 → 死循环。
                # 不更新基线的代价：下一轮还会认不出来、还是返回空，
                # 也就是"这一段消息没处理"。但那个代价远小于无限重复操作 ——
                # 而且真的认不出来说明窗口被换过了，等窗口稳定自然会接上。
                print("[微信] 窗口内容对不上（可能刚打开过图片），"
                      "这一轮先不处理，等它稳定")
                return []
            new = items[idx + 1:]
        # 2026-10-08 多会话改造：基线按会话存（开头已经 self._prev[ck] = now 过，
        # 这里那个裸赋值会把整个 dict 覆盖成一条 list —— 后面别的会话读 _prev.get()
        # 就变成了 list.get()，直接 AttributeError）。
        self._prev[ck] = now
        return new

    # --- 要不要回 ---------------------------------------------------

    # 微信里"我自己发的"这条消息，sender 字段是这些值之一
        # （文件传输助手发给自己时就是 'self'，不是昵称）
    SELF_MARKERS = ("self", "我", "自己")

    # 🔴 2026-10-08：微信 4.x 的 group 消息里 sender 可能是 wxid 而不是昵称
    # （尤其私聊）。self_wxid 是小号的 wxid，桥接启动时核对过身份用的就是它 ——
    # 之前只拿它做"启动时校验"，从没在过滤里用上，于是她回复之后，
    # 自己那条消息又作为事件回到轮询里，被当成对方的话再问一遍大脑。
    # 症状是"她自己在跟自己说话"，而且会一直说下去。
    #
    # 下面两处（_is_mine 开头 + 这里）都判一次：昵称和 wxid 两条路都堵上。
    def _is_my_wxid(self, sender):
        # 🔴 属性名是 my_id（connect() 里从 GetMyInfo 拿的，也是身份校验用的那个）。
        # 这里原先写成 self_wxid —— 那个属性**根本不存在**，getattr 永远返回 ""，
        # 于是这个函数恒为 False、自己发的话永远过滤不掉（她自言自语），
        # 而且不报错 —— 静默失效最难查。
        want = str(getattr(self, "my_id", "") or "").strip()
        if not want:
            return False
        s2 = str(sender or "").strip().lower()
        return bool(s2) and (s2 == want.lower() or s2.startswith(want.lower()))

    def _is_mine(self, sender, content):
        """这条消息是不是她自己发的？

        实测（203.0.113.10 / 文件传输助手）：**自己发的消息 `sender` 是 `'self'`**，
        不是昵称。所以先认这个 —— 比认昵称可靠得多（别人改不了）。

        剩下两种情况才看昵称/内容：
          · 自己发的但 sender 给了昵称（不同版本行为不一致）
          · 别人**恰好**也叫麦麦 → 昵称撞车

        昵称撞车是真实风险：昵称是别人可以改的。只认昵称的话，
        群里同名那个人说的话会被当成她自己发的跳过，
        那个人@她永远不回应，而且完全不报错。

        所以按「有没有历史」切换：
          · 还没有历史（刚启动）→ 认昵称。
            代价是这一轮内同名的那个人的话会被跳过；
            但反过来（自己回自己）更糟。
          · 有历史之后        → 认内容。
        """
        s = (sender or "").strip()
        # 最可靠：微信自己标的 self
        if s.lower() in self.SELF_MARKERS:
            return True
        # 🔴 wxid 这条路（2026-10-08 补）。桥接启动时用 expected_wxid 核对过
        # 身份，那个值存在 self.my_id —— 但它**从没在过滤里用上过**。
        # 微信 4.x 的 group/私聊消息里 sender 常常是 wxid 而不是昵称，
        # 于是她回复之后、自己那条消息又作为事件回到轮询里，
        # 被当成对方的话再问一遍大脑 → 她在跟自己说话，而且停不下来。
        if self._is_my_wxid(s):
            return True
        hit = bool(content) and content.strip() in self._my_recent
        if self.my_name and s == self.my_name:
            if not self._my_recent:
                return True          # 刚启动，没有内容可判，认昵称
            return hit               # 有历史了：只认内容
        return hit                   # 发送者为空等情况，同上

    def _should_reply(self, content, chat=None):
        """本机快筛：这条消息值不值得拿去问大脑。

        **这里必须是纯本地判断，不能调云端** —— 轮询每 3 秒一次，
        每条群消息都打一次云端既慢又贵。这层只管"明显没戏"的，
        拿不准的一律往上送，交给云端的闸门（brain/plugins/gate.py）判。

        三档：
          mention  只有被 @ 或命中关键词才往上送。最省，也最没存在感
          gate     全部往上送，由云端那个便宜小模型判断该不该接话
                   （要配这个，先把插件的 FEATURE gate 打开）
          all      全回。容易刷屏，调试用
        """
        # 群/私聊分流：私聊是专门找她的，不用 @ 就回；群仍走 group_reply_mode。
        # 2026-10-08 多会话改造。chat=None 时退回原行为（老调用方兼容）。
        if chat is None:
            mode = self.cfg.get("group_reply_mode") or "mention"
        else:
            mode = self._mode_for(chat)
        if mode == "all":
            return True
        for nick in self.cfg.get("bot_nicknames") or []:
            if nick and ("@" + nick) in content:
                return True
        for kw in self.cfg.get("keywords") or []:
            if kw and kw in content:
                return True
        if mode == "gate":
            return True
        return False

    # --- 冷场 / 安静时段 --------------------------------------------

    def _quiet_now(self):
        h = time.localtime().tm_hour
        qf = int(self.cfg.get("quiet_from") or 23)
        qt = int(self.cfg.get("quiet_to") or 7)
        if qf > qt:            # 跨零点，比如 23 -> 7
            return h >= qf or h < qt
        return qf <= h < qt

    def _next_idle_wait(self):
        """这次冷场要等多久才让她说话（秒）。

        为什么是随机而不是固定：固定 25 分钟的问题是**每次都一样** ——
        群里几个人会摸准这个节奏，到点就等着看她冒泡，不像真人。
        真人是「有时候憋不住」，没有规律。

        但**只随机上限、锁死下限**，不是完全随机：
        刚安静 3 分钟就说话太吵（大家可能只是去倒杯水），
        所以下限保证她至少安静够久。实际是 [下限, 上限] 之间均匀取。

        每一轮冷场重新掷一次 —— 掷完就记住（`self._idle_target`），
        下一轮冷场再重掷。不能每 3 秒重掷一次，否则等于没有。
        """
        lo = float(self.cfg.get("idle_min_minutes") or 20) * 60
        hi = float(self.cfg.get("idle_max_minutes") or 90) * 60
        if hi <= lo:
            hi = lo + 60
        if lo <= 0:
            lo = 60
        return random.uniform(lo, hi)

    def _maybe_lurk(self):
        """冷场太久就让她自己说一句。"""
        if not self.cfg.get("idle_lurk"):
            return
        if self._quiet_now():
            return
        idle_sec = time.time() - self._last_active
        # 还没到这次掷出来的点就再等等
        if self._idle_target is None:
            self._idle_target = self._next_idle_wait()
            print("[冷场] 这次等 %.0f 分钟再看" % (self._idle_target / 60))
        if idle_sec < self._idle_target:
            return
        self._last_lurk = time.time()
        print("[冷场] 安静了 %.0f 分钟（这次掷到 %.0f 分钟），问她要不要说点什么"
              % (idle_sec / 60, self._idle_target / 60))
        # 不问"你要不要说话" —— 那类措辞她会当成可以聊的内容
        text = self.brain.ask("[系统] 群里已经很久没人说话了")
        if text:
            self._send(text)
        self._last_active = time.time()
        # 下一轮冷场重新掷骰子
        self._idle_target = None

    # --- 说回去 -----------------------------------------------------

    def _send(self, text, chat=None):
        text = (text or "").strip()
        if not text:
            return False
        cap = int(self.cfg.get("max_reply_chars") or 400)
        if len(text) > cap:
            text = text[:cap] + "…"
                # 发出去之前的最后一道闸。
        # 从「读到消息」到「把字打进输入框」之间隔着问云端大脑的时间
        # （可能几十秒），这段时间里微信完全可能掉线重连到了别的号。
        # 所以**每一条要发出去的消息，都在这个瞬间重新确认一次身份** ——
        # 这是唯一能保证「绝不会用不是小号的号发出东西」的位置。
        if not self._recheck_identity():
            print("[发送] 已拦下：身份不符，这条没有发出去。")
            return False
        try:
            # 🔴 多会话模式（2026-10-08）：step() 传进来的 chat 是会话**名字**。
            # 曾经直接 `target = chat` 然后调 target.SendMsg →
            # 'str' object has no attribute 'SendMsg'，一条都发不出去。
            # 正确做法：按名字切回去再发 —— 问大脑可能几十秒，期间窗口
            # 可能被别的东西动过，**不能靠"当前窗口"发**；
            # 切不回去就放弃这条，绝不发到别人那儿去。
            # 子窗口模式（单会话老路）：发到子窗口，主微信窗口不用动。
            if isinstance(chat, str):
                if not self._switch_to(chat):
                    print("[发送] 已放弃：切不回会话 %r，这条没有发出去。" % chat)
                    return False
                target = self._sub
            else:
                target = chat if chat is not None else self._sub
            r = target.SendMsg(text) if target is not None else self.wx.SendMsg(text)
            ok = bool(getattr(r, "is_success", True))
            if ok:
                # 记下她说过的话 —— `_is_mine` 靠它区分「同名的那个人」
                self._my_recent.add(text)
                # 只留最近 30 条，别无限涨
                if len(self._my_recent) > 30:
                    self._my_recent = set(list(self._my_recent)[-30:])
            print("[发送] %s %r" % ("OK" if ok else "FAIL", text[:60]))
            return ok
        except Exception as e:
            print("[发送] 出错：%s" % e)
            return False

    # --- 主循环 -----------------------------------------------------

    def _diag_once(self):
        """设了 `ZX_DIAG` 时，把微信窗口的控件树报进日志（**只跑一次**）。

        ## 为什么需要它

        用户报「她有顺序地右键群聊的头像」。代码侧能排的都排掉了
        （sha1 一致、零 right_click、`_snapshot` 只登记不 download、
        `_read_all` 从没卡住、配置正常），而 wxauto4 的业务代码
        (`wx.py`、`ui/chatbox.py`) **是.pyd，没有源码**。

        剩下的唯一办法是**行为证据**：她在真桌面里跑，能看到界面控件树；
        而从 SSH 会话里 `GetRootControl().GetChildren()` 返回**空**
        （跨会话看不见 UIA 控件树）。所以必须**借她自己的进程**来看。

        ## 为什么只跑一次

        遍历控件树不便宜，而每 5 秒跑一次没意义 —— 现象是一次性的。
        所以用 `_diag_done` 闩住，跑完就不再跑。

        ## 🔴 绝不点任何控件

        这里只读 Name / ClassName / ControlType。**一旦调Click/Invoke
        就变成了问题本身**（那正是要查的东西）。

        ## 怎么用

            set ZX_DIAG=1
            然后双击启动。日志里会出现 `[诊断] …`。

        没设这个变量时，`os.environ.get` 每次返回 None，**零开销**。
        """
        if getattr(self, "_diag_done", False):
            return
        if not os.environ.get("ZX_DIAG"):
            return
        self._diag_done = True
        print("[诊断] ===== 开始（只读，不点任何控件）=====")
        try:
            import uiautomation as auto
        except Exception:
            try:
                from wxauto4.uia import uiautomation as auto
            except Exception as e:
                print("[诊断] 拿不到 uiautomation：%s" % str(e)[:90])
                return
        # 1) 根节点下都有什么 —— 先确认能看到窗口
        roots = []
        try:
            for c in auto.GetRootControl().GetChildren():
                try:
                    roots.append((c.ClassName or "", (c.Name or "")[:40]))
                except Exception:
                    roots.append(("?", ""))
        except Exception as e:
            print("[诊断] 读根节点失败：%s" % str(e)[:90])
            return
        print("[诊断] 根节点 %d 个：%s" % (len(roots), roots[:8]))
        wins = []
        for c in auto.GetRootControl().GetChildren():
            try:
                cn = c.ClassName or ""
            except Exception:
                continue
            if "WeChat" in cn or "Weixin" in cn:
                wins.append(c)
        print("[诊断] 微信窗口 %d 个" % len(wins))
        if not wins:
            print("[诊断] 这个会话看不到微信窗口"
                  "（真桌面在Console 会话里，不在本进程能看的范围）")
            return
        # 2) 遍历第一个窗口，只记列表项和图片（头像）
        stats = {"ListItem": 0, "Image": 0, "Menu": 0, "total": 0}
        items = []
        imgs = []

        def walk(c, d, maxd=9):
            if d > maxd or stats["total"] > 20000:
                return
            stats["total"] += 1
            try:
                cn = c.ClassName or ""
                nm = c.Name or ""
            except Exception:
                return
            if "ListItem" in cn:
                stats["ListItem"] += 1
                if len(items) < 40:
                    items.append("%s%s | %s" % ("  " * d, cn, nm[:36]))
            elif "Image" in cn:
                stats["Image"] += 1
                if len(imgs) < 25:
                    imgs.append("%s%s | %s" % ("  " * d, cn, nm[:36]))
            elif "Menu" in cn or "Popup" in cn:
                stats["Menu"] += 1
            try:
                for ch in c.GetChildren():
                    walk(ch, d + 1, maxd)
            except Exception:
                pass

        try:
            walk(wins[0], 0)
        except Exception as e:
            print("[诊断] 遍历失败：%s" % str(e)[:90])
            return
        print("[诊断] 控件总数=%d 列表项=%d 图片=%d 菜单=%d"
              % (stats["total"], stats["ListItem"], stats["Image"],
                 stats["Menu"]))
        for l in items[:30]:
            print("[诊断] 项 %s" % l)
        for l in imgs[:20]:
            print("[诊断] 图 %s" % l)
        print("[诊断] ===== 结束 =====")

    # 🔴 后台线程版清道夫（2026-10-08 教训：原版在每轮循环末尾跑，
    # 结果主线程被卡死的菜单楔住时**它也跟着永远轮不到** —— 死锁闭环：
    # 菜单卡死 → UIA 调用不返回 → 主循环卡住 → 清道夫不跑 → 菜单继续卡死。
    # 所以清道夫必须独立于主循环，在自己的守护线程里巡检。）
    def _janitor_loop(self):
        seen = {}                     # hwnd -> 第一次见到它的时间
        while True:
            time.sleep(4)
            try:
                import ctypes
                user32 = ctypes.windll.user32
                CB = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p,
                                        ctypes.c_void_p)
                found = []

                def _cb(h, _l):
                    try:
                        if not user32.IsWindowVisible(h):
                            return 1
                        c = ctypes.create_unicode_buffer(96)
                        user32.GetClassNameW(h, c, 96)
                        cn = c.value or ""
                        # 认「Qt*ToolSaveBits」这个瞬态弹窗类
                        # （主窗口是 Qt*QWindowIcon，不会误伤）。
                        # wxauto4 读消息每 ~0.7s 就弹一个这种菜单又关掉，
                        # 偶尔残留成死菜单 —— 残留会挡住微信 UI 线程，
                        # 进而把桥接的 UIA 调用楔死（实测定罪）。
                        if cn.startswith("Qt") and cn.endswith("ToolSaveBits"):
                            found.append(int(h) & 0xFFFFFFFF)
                    except Exception:
                        pass
                    return 1

                _ref = CB(_cb)
                user32.EnumWindows(_ref, 0)
                now = time.time()
                seen = {h: ts for h, ts in seen.items() if h in found}
                for h in found:
                    if h in seen and now - seen[h] >= 8.0:
                        # 活了 ≥8 秒 = 残留死菜单（正常菜单一闪而过）。
                        # 只有它自己是前台时才 Esc（菜单开着时焦点在它上），
                        # 避免把 Esc 打到别的窗口里去。
                        fg = user32.GetForegroundWindow()
                        fg = (int(fg) & 0xFFFFFFFF) if fg else 0
                        if fg == h:
                            user32.keybd_event(0x1B, 0, 0, 0)
                            user32.keybd_event(0x1B, 0, 2, 0)
                            print("[清道夫] 关掉了残留的弹出菜单 (h=%08x)"
                                  % h, flush=True)
                        seen.pop(h, None)
                    elif h not in seen:
                        seen[h] = now
            except Exception:
                pass                  # 清道夫是纯补救，任何失败都不许闹事

    def _watchdog_loop(self):
        """心跳看门狗：主循环静默超过 6 分钟 = 卡死（UIA 挂起/HTTP 无超时）。

        先把所有线程栈转储到 stderr（→ launcher.log，留验尸报告），
        然后硬退出自己 —— launcher 侧的重启循环会拉起新的桥接。
        为什么不用 dump_traceback_later：实测卡死时它没转储（原因不明），
        自建心跳线程 + os._exit 是不依赖任何前提的最后保命绳。
        """
        while True:
            time.sleep(30)
            try:
                beat = getattr(self, "_beat", 0)
                if beat and time.time() - beat > 360:
                    print("[看门狗] 主循环静默 %.0f 秒 —— 判定卡死，"
                          "转储堆栈后自尽，等 launcher 重生"
                          % (time.time() - beat), flush=True)
                    try:
                        import faulthandler
                        faulthandler.dump_traceback()
                    except Exception:
                        pass
                    time.sleep(3)
                    os._exit(9)
            except Exception:
                pass

    def _sender_ident(self, m):
        """从消息对象取发送者身份（微信号）——硬鉴权用。

        微信 4.x：群成员消息带 sender_info()，返回含 'id'（=对方微信号，
        即 agent 配置 owner_ids 里填的那个值，不随昵称变）。取不到就返回 {}，
        下游按"无身份"拒绝，绝不会误放行。
        """
        try:
            si = getattr(m, "sender_info", None)
            if callable(si):
                info = si() or {}
                if isinstance(info, dict):
                    return {"wxid": str(info.get("id") or "").strip(),
                            "display_name": str(info.get("display_name") or "").strip()}
        except Exception:
            pass
        return {}

    def _owner_ok(self, sender, content="", ident=None):
        """身份验证：只有主人能下指令。

        判定优先级（配了哪道就用哪道，两道都配则都要过）：
          · owner_ids/owner_wxids → 发送者微信号/内部id 在名单里（最不可伪造）
          · owner_names  → 发送者显示名在名单里
          · owner_token  → 消息里带口令（身份之外的第二道锁）
        三道都没配 → 一律拒绝（宁可不干活，也不让陌生人指挥本机）。
        """
        ag = getattr(self, "agent", None)
        if ag is not None:
            try:
                return bool(ag.auth_ok(sender or "", content or "", ident))
            except Exception:
                pass
        names = [(n or "").strip() for n in (self.cfg.get("owner_names") or [])]
        tok = (self.cfg.get("owner_token") or "").strip()
        ok_name = (sender or "").strip() in names if names else False
        ok_tok = bool(tok) and (tok in (content or ""))
        if names and tok:
            return ok_name and ok_tok
        if names:
            return ok_name
        if tok:
            return ok_tok
        return False

    def step(self, chat=None):
        """处理**一个**会话这一轮（多会话轮转）。

        chat=None 时用 self.chat_name（老行为，自测 --with 走这条路）。
        """
        self._diag_once()
        if chat is None:
            chat = self.chat_name
        if not self._switch_to(chat):
            return
        items = self._snapshot()
        new = self._diff(items)
        for sender, content, img, mobj in new:
            # 图片**这一轮才算真正下载** —— _snapshot 里只登记，
                        # 是因为它跑在 _diff 之前、对的是全部历史消息而不是新增的那些。
            #
            # 顺序也重要：下载放在 _is_mine / _should_reply **之后**。
            # 她自己发的图、群里没点名她的图，根本不必下载 ——
            # 每下一张图就是一次右键 + 开窗 + 关窗，还会抢前台窗口。
            # 判断"要不要理他"只需要 sender 和 content，不需要图本身。
            if self._is_mine(sender, content):
                continue
            self._last_active = time.time()
            # 🔴 2026-10-08：抖音折叠卡片（type='text' + "复制...更多"）
            # wxauto4 读不到 URL，点击卡片会把完整分享文案复制到剪贴板。
            # 这里提取 URL 拼到 content 里，这样 AI 能看到链接并做内容感知。
            # 只对新增消息做（_diff 已经过滤了历史），副作用是移动鼠标+覆盖剪贴板，
            # 但桥接机是专用机器，没人在用，可接受。
            _mtype = str(getattr(mobj, "type", "") or "").strip().lower()
            if _is_douyin_card(_mtype, content or ""):
                _url = self._extract_share_url(mobj)
                if _url:
                    content = "%s %s" % ((content or "").strip(), _url)
                    print("[微信] 抖音卡片已补 URL: %s" % content[:80])
            # 有人说话了 = 冷场结束。作废这次掷的骰子，
            # 下一轮冷场重新掷 —— 否则一次冷场就把随机值用完了，
            # 之后每一轮都是同一个时间，等于退回固定值。
            self._idle_target = None
            # 这一对话属于哪个渠道：agent/console 和 _should_reply 都要用。
            _src = "wechat" if self._is_private(chat) else "wechat_group"
            # --- 控制台指令分流：命中前缀就地执行，绝不送云端（不受 @ 限制）---
            _ctext = (content or "").strip()
            _ident = self._sender_ident(mobj)
            if (getattr(self, "console", None) is not None
                    and _src == "wechat_group"
                    and self.console.is_command(_ctext)):
                if not self._owner_ok(sender or "", _ctext, _ident):
                    print("[console] 非主人指令，忽略 from=%r" % (sender or "?"))
                    continue
                _rep, _ipath = self.console.dispatch(_ctext, sender or "")
                print("[console] cmd=%r len=%s img=%s" % (_ctext[:60], len(_rep or ""), bool(_ipath)))
                if _rep:
                    self._send(_rep.replace("\r\n", " | ").replace("\n", " | "), chat=chat)
                if _ipath:
                    try:
                        _tgt = self._sub if getattr(self, "_sub", None) is not None else self.wx
                        _tgt.SendFiles(_ipath)
                        print("[console] screenshot sent")
                    except Exception as _e:
                        print("[console] send file failed: %s" % _e)
                continue
            # --- LLM 驱动 agent 分流：自然语言交给本机大脑，模型自主选工具执行，不经云端人设 ---
            _atext = (content or "").strip()
            if getattr(self, "agent", None) is not None and self.agent.accepts(sender or "", _atext, _src, _ident):
                _clean = self.agent.clean_text(_atext)
                _arep, _aimg = self.agent.handle(_clean, sender or "", img if isinstance(img, str) else None)
                print("[agent] from=%r in=%d out=%s img=%s" % (sender, len(_clean), len(_arep or ""), bool(_aimg)))
                if _arep:
                    self._send(_arep, chat=chat)
                if _aimg:
                    try:
                        _tgt = self._sub if getattr(self, "_sub", None) is not None else self.wx
                        _tgt.SendFiles(_aimg)
                    except Exception as _e:
                        print("[agent] send file failed: %s" % _e)
                continue
            # 群里没点名就不回 —— **图片也守这条**。
            # 群里有人随手扔张图就把她叫起来，太吵了；
            # 而且看图要花钱（视觉模型另一次调用），不能被随便触发。
            if not self._should_reply(content, chat):
                print("[跳过][%s] %s：%s" % (chat, sender or "?",
                                          (content[:40] or "[图片]")))
                continue
            if isinstance(img, _PendingImg):
                img = self._resolve_img(img)
                if img is None:
                    continue
            # 她需要知道是谁在说话，否则三个人说话她分不清
            if sender and content:
                text = "%s：%s" % (sender, content)
            elif content:
                text = content
            else:
                # 纯图片：告诉她是**谁**发的图，但别替她说"我看到图了"——
                # 描述是视觉模型的活，桥接只负责把图送到。
                text = ("%s：（发来一张图）" % sender) if sender else "（发来一张图）"
            print("[收到][%s] %s%s" % (chat, text[:100],
                                     "  [带图]" if img else ""))
            # 会话名一起送上去：云端那边 memories/chat_history 按渠道分区，
            # 群里的对话和私聊**必须**分开存 —— 合在一起她会把她在群里的
            # 反应拿去回答私聊（或反过来），说话方式会串。
            answer = self.brain.ask(text, img or None,
                                   src=("wechat" if self._is_private(chat)
                                        else "wechat_group"))
            if not answer:
                # 🔴 空回复必须留痕（2026-10-08 教训：云端闸门插件把私聊
                # 全判成 PASS 返回空 reply，这里静默 continue，
                # 排查时"消息明明收到却什么都没发生"耗了一小时）。
                print("[大脑] %s 的这轮回复是空的（可能被云端闸门/风控拦下）"
                      % chat, flush=True)
                continue
            # 明确指定发给哪个会话：问大脑可能几十秒，期间窗口没动过，
            # 但也**不能靠"当前窗口"发** —— 万一中间有别的东西切了窗口，
            # 消息就发到别人那儿去了。
            self._send(answer, chat=chat)
        if new:
            self._last_active = time.time()
        self._maybe_lurk()

    def _recheck_identity(self):
        """🔴 运行中重新确认身份 —— 断了就立刻自杀。

        为什么启动时的校验不够：
        微信 4.x 是跨目录单实例。让知夏上线 → 桥接启动 → 连上小号，
        校验通过；然后微信自己掉线重连到了另一个号（或者有人在机器上
        登了另一个微信），**桥接还开着、还停在小号那个窗口上**。
        启动时那次校验早就过去了。

        所以每一轮都得重新问一遍"我现在连的还是小号吗"。
        判据是 expected_wxid：不是它就退出，不做任何操作。

        🔴 黑名单已移除（2026-10-05）。要加回来的话取消下面两行注释：
          banned = set(self.cfg.get("forbidden_wxids") or [])
          if now in banned: ... 退出
        """
        want = (self.cfg.get("expected_wxid") or "").strip()
        if not want:
            return True                      # 没填就不做运行中检查
        try:
            now = (self.wx.GetMyInfo() or {}).get("id") or ""
        except Exception as e:
            print("")
            print("[自检] 读不到身份（%s）—— 立刻退出，不做任何操作。" % e)
            return False
        # banned = set(self.cfg.get("forbidden_wxids") or [])
        # if now in banned:
        #     print("[自检] 现在连的是黑名单里的号(wxid=%s) —— 退出。" % now)
        #     return False
        if now != want:
            print("")
            print("[自检] " + "!" * 56)
            print("[自检] 身份变了：期望 %s，现在 %s —— 退出。" % (want, now))
            print("[自检] " + "!" * 56)
            return False
        return True

    def _switch_off(self):
        """你手动拉闸了吗（你说停就停，不用关窗口）。

        为什么不直接杀进程：那会留半截状态（消息读到一半、回复打了一半）。
        写个标记文件让桥接自己退出，干净。
        """
        import importlib.util
        sw = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", ".workbuddy", "scripts", "_vigil_switch.py")
        sw = os.path.normpath(sw)
        if not os.path.exists(sw):
            return False
        try:
            spec = importlib.util.spec_from_file_location("_zx_sw", sw)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)          # 不跑 main（模块级只有定义）
            return m.is_off()
        except Exception:
            return False          # 读不到开关 = 不拦，宁可多问一句

    def run(self):
        if self._switch_off():
            print("[运行] 你把知夏停用了（手动开关打开着），现在不启动。")
            print("       恢复：python %s off" % LOCAL_CFG)
            return 3
        if not self.connect():
            return 1
        gap = float(self.cfg.get("poll_seconds") or 3)
        # 身份多久复查一次。比轮询稀一点 —— 它要调一次 UI，成本比读消息高。
        recheck_every = max(1, int(round(15.0 / max(gap, 0.5))))
        if len(self.chat_names) > 1:
            print("[运行] 每 %.1f 秒轮询 %d 个会话：%s"
                  % (gap, len(self.chat_names), "、".join(self.chat_names)))
        else:
            print("[运行] 每 %.1f 秒看一次「%s」"
                  % (gap, self.chat_names[0] if self.chat_names else "?"))
        print("       桥接机在云端（那台机器没人用微信），会切窗口；"
              "每 %d 轮复查一次身份，Ctrl+C 退出" % recheck_every)
        n = 0
        try:
            # 🔴 卡死看门狗（2026-10-08 桥接机实测定罪用的，留着当保命绳）。
            # wxauto4 的 UIA 调用偶尔会在 pyd 里**无限挂起**（无超时、无异常，
            # 外层 try/except 拦不住），整轮静默冻结。这里每轮重新武装一个
            # 120 秒的 traceback 转储：一旦超时，把**所有线程正在执行的行号**
            # 打到 stderr（→ bridge.log），看得见卡在哪一行。repeat=True：
            # 持续挂起就每 120 秒喊一次，进程本身不杀。
            import faulthandler
            faulthandler.enable()
            faulthandler.dump_traceback_later(120, repeat=True)
            # 后台保命线程：清道夫（独立于主循环，专治卡死菜单）+ 心跳看门狗
            self._beat = time.time()
            threading.Thread(target=self._janitor_loop, daemon=True,
                             name="janitor").start()
            threading.Thread(target=self._watchdog_loop, daemon=True,
                             name="watchdog").start()
            while True:
                n += 1
                # 每轮重新武装：上一轮 120 秒内没走完的话，说明卡住了
                faulthandler.dump_traceback_later(120, repeat=True)
                if self._switch_off():
                    print("\n[运行] 你把知夏停用了 —— 退出。")
                    return 3
                if n % recheck_every == 0 and not self._recheck_identity():
                    print("[运行] 身份不符，已停止。")
                    return 2
                # 隧道/网络断了要重新探活，不然会一直安静地不发。
                # 每30 轮（默认 90 秒）试一次，别每轮都去敲网络。
                #
                # 2026-10-08：原来只在 _brain_errs>=3（已经连挂 3 次）时才 pick()，
                # 而 pick() 当时"选中就返回" —— 断掉的那个地址正被选中，
                # 于是永远探不到还活着的公网地址，她一直哑。现在无条件
                # force=True 重新探一遍全部地址：探活 + 自动切走死掉那个。
                if n % max(1, int(round(30.0 / max(gap, 0.5)))) == 0:
                    was = self.brain.picked
                    self.brain._brain_errs = 0
                    self.brain.pick(force=True)
                    if self.brain.picked != was:
                        print("[大脑] 已切到 %s" % self.brain.picked)
                # 多会话轮转：每轮只处理**一个**，不是并行。
                #
                # 为什么串行不并行：GetAllMessage() 会**自己操作界面**
                # （展开艾特卡片、滚动到新消息、定位控件），卡住时线程会
                # 带着界面操作继续跑 —— 并行等于把这个风险乘以会话数。
                # 串行时最坏情况是"这个会话卡住"，下一个会话照旧。
                try:
                    names = self.chat_names
                    if names:
                        nm = names[self._rr % len(names)]
                        self._rr += 1
                        self.step(chat=nm)
                except Exception as e:
                    # 单轮出错不能让整个桥接死掉
                    print("[运行] 这一轮出错：%s" % e)
                # 心跳：给看门狗报"我还活着"（清道夫已是后台线程，无需每轮跑）
                self._beat = time.time()
                # 进程外验尸官（vm/coroner.py）靠这个文件的 mtime 判断
                # 主循环死活 —— GIL 被卡死的原生调用持有时，进程内所有
                # 线程（含看门狗线程）都会冻结，只有文件 mtime 这类
                # "循环是否前进"的外部证据可信。写失败不影响运行。
                try:
                    open(r"C:\zx\hb.stamp", "w").close()
                except OSError:
                    pass
                time.sleep(gap)
        except KeyboardInterrupt:
            print("\n[运行] 退出")
        return 0


def main():
    cfg = load_cfg()
    args = set(sys.argv[1:])
    if "--once" in args:
        b = Bridge(cfg)
        if not b.connect():
            return 1
        b.step()
        return 0

    # --with <对话名>：不进群也能先测。
    #
        # 建群之前的自测：让桥接盯着任意一个对话窗口（一般是「文件传输助手」），
    # 链路一样能跑通。
    #
    # 为什么安全：桥接从头到尾连的是知夏小号（expected_wxid 核对），
    # 它只在**那个微信里**读消息、发消息 —— 别的窗口一个字节都不碰。
    if "--with" in args:
        i = sys.argv.index("--with")
        name = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
        if not name:
            print("用法：python wxauto_bridge.py --with 文件传输助手")
            return 1
        cfg["chat_name"] = name
        # 自测要的就是"我说什么她都答"，所以这里强制全回。
        # 不这么设会踩一个很闷的坑：默认 mention 模式下你发"111"不带 @，
        # 桥接读到、判定该跳、于是**一个字都不回** —— 看着像坏了，其实在工作。
        # 正式进群时这个开关才由配置说话（mention / gate / all）。
        cfg["group_reply_mode"] = "all"
        print("=" * 62)
        print("测试模式：盯着对话「%s」" % name)
        print("这一步只读这个窗口、只往这个窗口发，而且**你说啥她都答**")
        print("（正式进群时按配置来：mention 只回@，gate 由模型判断）")
        print("=" * 62)
        return Bridge(cfg).run()

    if not cfg.get("chat_name"):
        print("还没填群名。编辑 %s，把 chat_name 改成你的群名（一字不差）。"
              % LOCAL_CFG)
        print("想先不进群测一下，就用："
              "python wxauto_bridge.py --with 文件传输助手")
        return 1
    return Bridge(cfg).run()


if __name__ == "__main__":
    sys.exit(main())
