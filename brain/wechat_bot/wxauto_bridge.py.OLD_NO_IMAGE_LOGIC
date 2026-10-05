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

## 关于免费版 wxauto4 的两个实测结论（重要）

1. `AddListenChat` **能建子窗口，但回调不触发**（实测 6 秒 0 次）。
   文档标它是 Plus 专属 —— 建窗口那步没拦，推送那步拦了。
   → 只能轮询 `GetAllMessage()`。

2. `GetAllMessage()` 读的是**当前聊天窗口**。
   → 只要桥接一直停在目标群上、不切走，就不需要反复 `ChatWith`，
     也就不会来回抢你微信的焦点。这是本脚本 `sticky` 模式的由来。
"""

import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN_CFG = os.path.normpath(os.path.join(HERE, "..", "config", "config.json"))
LOCAL_CFG = os.path.join(HERE, "wxbridge.local.json")

DEFAULT_CFG = {
    # 微信群名（微信里显示的名字，要一字不差）
    "chat_name": "",
    # 🔴 安全护栏：这里填知夏小号的 wxid（不是微信号，是 GetMyInfo 里的 id）。
    # 桥接连上微信后会核对，对不上就**拒绝运行**，一条消息都不发。
    # 第一次跑先看 [微信] 那行打印的真实 id，填进来。
    "expected_wxid": "",
    # 🔴 黑名单：大号 wxid，永远拒绝。填错 expected_wxid 也拦得住。
    "forbidden_wxids": ["PRIVATE_CHAT_REMOVED"],
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
    # 云端大脑地址。隧道优先，不通就走公网
    "brain_urls": ["http://127.0.0.1:18787", "http://203.0.113.10:8788"],
    "brain_token": "",
    "timeout": 180,
    # 一条回复最多发多少字（太长群里刷屏）
    "max_reply_chars": 400,
}

# 非文本消息在微信里的占位文本 —— 这些不值得拿去问她
MEDIA_PLACEHOLDERS = ("[图片]", "[动画表情]", "[视频]", "[文件]", "[语音]",
                      "[聊天记录]", "[链接]", "[音乐]", "[表情]")


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
        try:
            with open(BRAIN_CFG, encoding="utf-8") as f:
                cfg["brain_token"] = json.load(f).get("brain_token") or ""
        except Exception:
            pass
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

    def pick(self):
        """启动时探一次哪个地址能用，之后固定用它。"""
        why = []
        for u in self.urls:
            try:
                url = u.rstrip("/") + "/api/plugins"
                if self.token:
                    url += "?token=" + urllib.parse.quote(self.token)
                with self._opener.open(
                        urllib.request.Request(url, headers={"User-Agent": "zhixia"}),
                        timeout=8) as r:
                    r.read(200)
                self.picked = u.rstrip("/")
                print("[大脑] 用 %s" % self.picked)
                return True
            except Exception as e:
                why.append("%s -> %s" % (u.rstrip("/"), str(e)[:60]))
                continue
        # 🔴 连不上大脑 = 桥接毫无意义：每条消息都会走到 ask() 然后拿不到
        # 任何回复，群里表现就是"她一声不吭"。必须说清楚为什么、怎么修，
        # 静默退出让人只能盯着不发火的窗口干瞪眼（这个坑踩过一次）。
        print("")
        print("=" * 62)
        print("连不上大脑，桥接无法工作（每条消息都会石沉大海）")
        for w in why:
            print("   %s" % w)
        print("")
        print("最常见的原因：SSH 隧道没开。云端的 %s 端口**不对公网开放**"
              % (self.urls[-1].rsplit(":", 1)[-1] if ":" in self.urls[-1] else "API"))
        print("（22端口的 SSH 能连上不代表 API 端口也能连，这个坑查过一次）")
        print("先开隧道：双击 知夏自测.bat（新版本会自动开隧道并等它就绪）")
        print("或者手动：ssh -N -L 18787:127.0.0.1:8788 "
              "administrator@203.0.113.10")
        print("=" * 62)
        print("")
        return False

    def ask(self, text):
        """问一次。返回她说的话；出错返回 None（不抛，抛了群里就刷错误）。"""
        if not self.picked:
            return None
        body = json.dumps({"text": text, "src": "wechat"},
                          ensure_ascii=False).encode("utf-8")
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
        self.chat_name = cfg["chat_name"]
        self.brain = Brain(cfg["brain_urls"], cfg["brain_token"],
                           int(cfg.get("timeout") or 180))
        self.wx = None
        self._prev = []          # 上一轮看到的消息指纹
        self._synced = False     # 第一次轮询只记不看
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
        self.listen = bool(cfg.get("listen", True))
        # 你在用别的软件时暂停的轮数（只为打印"让路/回来"用）
        self._paused = 0
        self._sub = None          # AddListenChat 建的子窗口（不抢主窗口）

    # --- 监听模式 ---------------------------------------------------

    # --- 连接 -------------------------------------------------------

    def connect(self):
        from wxauto4 import WeChat
        # ads=False：默认会打印一段广告，没必要
        #
        # 🔴 这里**不能**靠传参指定连哪个微信 —— 实测传了不报错但完全无效：
        #   WeChat(ads=False, path=r'D:\Weixin418\Weixin.exe')
        #     -> wx.path 仍是 C:\Program Files\Tencent\Weixin\Weixin.exe
        #        wx.dir  仍是 C:\Program Files\Tencent\Weixin\203.0.113.10
        # 选窗口的逻辑在 .pyd 二进制里（读不到源码），两个实例并存时
        # 它连哪一个没有保证。所以只能靠下面的身份硬闸。
        #
        # 🔴 构造**会抛**，不是返回 False：微信 4.x 的控件树没被唤醒时它直接
        # 抛「未找到已登录的客户端主窗口」。原来这里没有 try，异常一路穿到
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
        # 这两个属性实测能读到（pyd 里的 uWeChat.path / uWeChat.dir），
        # 拿它可以在比对 wxid 之前就发现「连到别的实例去了」
        self.wx_path = str(getattr(self.wx, "path", "") or "")
        self.wx_dir = str(getattr(self.wx, "dir", "") or "")
        print("[微信] 已连上：昵称=%r wxid=%r 地区=%r"
              % (self.my_name, self.my_id, my.get("region")))
        print("[微信] 实例：%s"
              % (self.wx_dir or self.wx_path or "(取不到)"))

        if not self._identity_ok():
            return False

        if not self.brain.pick():
            return False

        if self.chat_name:
            try:
                # 用**子窗口**而不是主窗口。
                #
                # 为什么不走 AddListenChat 的回调：实测 4.1.8 + wxauto4 41.1.7
                # 下回调**根本不触发**（子窗口能读到消息，但 fired 一直是 0）。
                # 所以改成"子窗口 + 轮询" —— 子窗口是独立出来的对话，
                # 读它**不会把主微信窗口切走**，也就不抢你正在用的窗口。
                # 这是"不抢窗口 + 可靠"唯一实测成立的组合。
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
        """🔴 确认连上的是知夏那个小号，不是大号。

        为什么必须有这一步：wxauto4 找窗口的逻辑在 .pyd 里（读不到源码），
        两个微信同时跑时它会连到哪一个**没有保证**。万一连错，
        后面每一次 GetAllMessage / SendMsg 都是在大号上操作 ——
        读别人的聊天记录、往别人的群里发消息。

        所以这里不是"打个招呼"，是硬闸：认不出来就不跑。
        """
        banned = set(self.cfg.get("forbidden_wxids") or [])
        if self.my_id and self.my_id in banned:
            print("")
            print("=" * 62)
            print("拒绝运行：连上的是【大号】(wxid=%s)" % self.my_id)
            print("  这个号绝不能碰。")
            print("  切换式的正确做法：托盘右键退出大号 → 启动 D 盘那份")
            print("  （%s）→ 登小号 → 再启动本桥接。" % (self.cfg.get("bot_weixin_dir") or "D:\\Weixin418"))
            print("=" * 62)
            return False

        # 实例路径对不上：还没比 wxid 就先发现了「连的不是那一份微信」。
        # 微信 4.x 跨安装目录单实例（D 盘那份实测 15 秒就把启动请求
        # 转交给 C 盘那份然后自己退出），所以最常见的错不是登错号，
        # 是大号还开着、桥接连上了大号 —— 这条能在最早的地方拦住。
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
                print("  大号是不是还开着？先退掉它再启 D 盘那份。")
                print("=" * 62)
                return False

        want = (self.cfg.get("expected_wxid") or "").strip()
        if not want:
            print("")
            print("!" * 62)
            print("拒绝运行：还没填 expected_wxid。")
            print("  现在连上的这个号身份不明，我不能确定它不是大号。")
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

    @staticmethod
    def _fp(item):
        return "%s|%s" % (item[0], item[1])

    def _foreground_is_wechat(self):
        """当前前台窗口是不是微信。

        为什么需要：wxauto 读消息靠操作微信界面，而切窗口/读消息都会把微信
        顶到前台。用户反馈"他抢窗口我怎么用电脑" —— 这是这套接入方式的
        硬限制，调不掉，只能**让开**：你在认真用别的软件时就别动微信。

        读不到（权限/窗口句柄拿不到）一律当"不是微信" → 继续干活。
        判错的后果是她偶尔晚点回一句，不是不回。
        """
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return False
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
            title = buf.value or ""
            if self.my_name and self.my_name in title:
                return True
            return "微信" in title or "WeChat" in title
        except Exception:
            return False

    def _should_pause_for_user(self):
        """你在用别的软件吗？是就这一轮不碰微信。

        只看"前台是不是微信"这一条，不猜你在干什么。
        开着 `pause_when_away` 时才生效。
        """
        if not self.cfg.get("pause_when_away", True):
            return False
        return not self._foreground_is_wechat()

    def _snapshot(self):
        """读当前窗口里的文本消息，返回 [(发送者, 内容), ...]。

        有子窗口就读子窗口 —— 子窗口是独立出来的对话，读它不会把主微信
        窗口切走，所以不抢你正在用的窗口。
        """
        items = []
        # 注意：子窗口读到 0 条是**正常**的（它只保留监听开始后收到的）。
        # 所以别把空当成出错，日志也别刷屏—— 每 3 秒一条会把别的信息淹掉。
        src = getattr(self, "_sub", None) or self.wx
        try:
            msgs = src.GetAllMessage() or []
        except Exception as e:
            print("[微信] 读消息失败：%s" % e)
            return items
        for m in msgs:
            try:
                sender = str(getattr(m, "sender", "") or "").strip()
                content = str(getattr(m, "content", "") or "").strip()
            except Exception:
                continue
            if not content or content in MEDIA_PLACEHOLDERS:
                continue
            # 微信把自己发的消息标成 'self'（实测）。换成昵称，
            # 免得下游把它当成一个叫 self 的陌生人拼进给大脑的话里。
            if sender.lower() in self.SELF_MARKERS:
                sender = self.my_name or "我"
            items.append((sender, content))
        return items

    def _diff(self, items):
        """比对出这一轮新增的消息。

        消息是**追加**在末尾的，所以绝大多数情况下上一轮是这一轮的前缀。
        偶尔窗口滚动导致开头的旧消息被丢掉，前缀就对不上了 —— 那时退回
        用"最后一条"当锚点。两种都认不出来就保守返回空（宁可漏一句，
        也别把几百条旧消息当成新消息全问一遍）。
        """
        now = [self._fp(x) for x in items]
        if not self._synced:
            self._prev = now
            self._synced = True
            print("[微信] 已同步 %d 条历史，之后只处理新消息" % len(now))
            return []
        if now == self._prev:
            return []
        if len(now) > len(self._prev) and now[:len(self._prev)] == self._prev:
            new = items[len(self._prev):]
        else:
            anchor = self._prev[-1] if self._prev else None
            idx = -1
            for i in range(len(now) - 1, -1, -1):
                if now[i] == anchor:
                    idx = i
                    break
            if idx < 0:
                new = []
            else:
                new = items[idx + 1:]
        self._prev = now
        return new

    # --- 要不要回 ---------------------------------------------------

    # 微信里"我自己发的"这条消息，sender 字段是这些值之一
    # （实测文件传输助手发给自己时就是 'self'，不是昵称）
    SELF_MARKERS = ("self", "我", "自己")

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
        hit = bool(content) and content.strip() in self._my_recent
        if self.my_name and s == self.my_name:
            if not self._my_recent:
                return True          # 刚启动，没有内容可判，认昵称
            return hit               # 有历史了：只认内容
        return hit                   # 发送者为空等情况，同上

    def _should_reply(self, content):
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
        mode = self.cfg.get("group_reply_mode") or "mention"
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
        # 不问"你要不要说话"，那类措辞她会当成可以聊的内容（踩过）
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
        # 🔴 发出去之前的最后一道闸。
        # 从「读到消息」到「把字打进输入框」之间隔着问云端大脑的时间
        # （可能几十秒），这段时间里你完全可能已经把微信切回大号了。
        # 所以**每一条要发出去的消息，都在这个瞬间重新确认一次身份** ——
        # 这是唯一能保证「绝不会在大号群里发出东西」的位置。
        if not self._recheck_identity():
            print("[发送] 已拦下：身份不符，这条没有发出去。")
            return False
        try:
            # 子窗口模式：发到子窗口，主微信窗口不用动（不抢你正在用的窗口）。
            # 主窗口模式：不带 who，发给当前聊天（就是那个群），这样不用切窗口。
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

    def step(self):
        # 🔴 你正在用别的软件吗？是就这一轮完全不碰微信。
        # 这套接入靠界面操作，不这么做就会不停把微信顶到前台抢你窗口。
        # **这一轮直接返回、不读消息** —— 所以回来时那些消息还在窗口里，
        # 会被下一轮读到，不会丢。
        if self._should_pause_for_user():
            self._paused += 1
            if self._paused == 1:
                print("[让路] 你在用别的软件，这一轮不碰微信（不会丢消息）")
            return
        if self._paused:
            print("[让路] 回到微信了，继续读（暂停了 %d 轮）" % self._paused)
            self._paused = 0
        items = self._snapshot()
        new = self._diff(items)
        for sender, content in new:
            if self._is_mine(sender, content):
                continue
            self._last_active = time.time()
            # 有人说话了 = 冷场结束。作废这次掷的骰子，
            # 下一轮冷场重新掷 —— 否则一次冷场就把随机值用完了，
            # 之后每一轮都是同一个时间，等于退回固定值。
            self._idle_target = None
            if not self._should_reply(content):
                print("[跳过] %s：%s" % (sender or "?", content[:40]))
                continue
            # 她需要知道是谁在说话，否则三个人说话她分不清
            text = ("%s：%s" % (sender, content)) if sender else content
            print("[收到] %s" % text[:100])
            answer = self.brain.ask(text)
            if not answer:
                continue
            self._send(answer)
        if new:
            self._last_active = time.time()
        self._maybe_lurk()

    def _recheck_identity(self):
        """🔴 运行中重新确认身份 —— 断了就立刻自杀。

        为什么启动时的校验不够：
        微信 4.x 是跨目录单实例。你让知夏上线 → 桥接启动 → 连上小号，
        校验通过；然后你把微信切回大号（或者微信自己掉线重连到了另一个号），
        **桥接还开着、还停在小号那个窗口上**。启动时那次校验早就过去了。

        所以每一轮都得重新问一遍"我现在连的还是小号吗"。
        判据比启动时更严 —— 任何一项不满足就退出，不做任何操作。
        """
        want = (self.cfg.get("expected_wxid") or "").strip()
        if not want:
            return True                      # 没填就不做运行中检查
        banned = set(self.cfg.get("forbidden_wxids") or [])
        try:
            now = (self.wx.GetMyInfo() or {}).get("id") or ""
        except Exception as e:
            print("")
            print("[自检] 读不到身份（%s）—— 立刻退出，不做任何操作。" % e)
            return False
        if now in banned:
            print("")
            print("[自检] " + "!" * 56)
            print("[自检] 现在连的是【大号】(wxid=%s) —— 退出。" % now)
            print("[自检] " + "!" * 56)
            return False
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
                          "..", "..", ".workbuddy", "scripts", "_zhixia_switch.py")
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
        if getattr(self, "_sub", None) is not None:
            print("[运行] 每 %.1f 秒读一次子窗口「%s」（**不抢主微信窗口**），"
                  "每 %d 轮复查一次身份，Ctrl+C 退出"
                  % (gap, self.chat_name, recheck_every))
        else:
            print("[运行] 每 %.1f 秒看一次群「%s」，每 %d 轮复查一次身份，"
                  "Ctrl+C 退出（**会占微信前台**）"
                  % (gap, self.chat_name, recheck_every))
        n = 0
        try:
            while True:
                n += 1
                if self._switch_off():
                    print("\n[运行] 你把知夏停用了 —— 退出。")
                    return 3
                if n % recheck_every == 0 and not self._recheck_identity():
                    print("[运行] 身份不符，已停止。")
                    return 2
                # 隧道/网络断了要重新探活，不然会一直安静地不发。
                # 每30 轮（默认 90 秒）试一次，别每轮都去敲网络。
                if n % max(1, int(round(30.0 / max(gap, 0.5)))) == 0:
                    self.brain._brain_errs = getattr(self.brain, "_brain_errs", 0)
                    if self.brain._brain_errs >= 3:
                        print("[大脑] 连不上，重试一次…")
                        if self.brain.pick():
                            print("[大脑] 恢复了：%s" % self.brain.picked)
                # 两种模式都轮询：主窗口读会抢你正在用的微信窗口，
                # 子窗口读不会。回调那条路实测不触发，不考虑。
                try:
                    self.step()
                except Exception as e:
                    # 单轮出错不能让整个桥接死掉
                    print("[运行] 这一轮出错：%s" % e)
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
    # 建群之前的自测：让桥接盯着任意一个对话窗口（一般是「文件传输助手」，
    # 或者你自己和大号小号之间的私聊），链路一样能跑通。
    #
    # 为什么安全：**大号只是消息的来源，不是操作对象**。桥接从头到尾
    # 连的是小号（expected_wxid 核对、forbidden_wxids 拦黑名单），
    # 它只在**小号这个微信里**读消息、发消息。有人在别的号上给小号发消息，
    # 桥接读到的就是小号窗口里的那条 —— 大号的窗口一个字节都不碰。
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
