# -*- coding: utf-8 -*-
"""发言闸门：让她先判断"这里真人会不会接话"，PASS 就不开口。

## 为什么要有这一层

原来"说不说话"是写在人设里的（「话头明显能接→主动接」）。人设一变长，
这条规则就必然变钝 —— 而且**没法测**。这一层把它挪到代码里：

    消息进来 → 本机朴素快筛（没@就没戏）→ **本插件判定** → OPEN 才问大脑

关键在**位置**：它在"要不要问大脑"之前，不在"她怎么回答"里。所以：

1. **大脑那4000 字人设一个字都不用动**，也不用在聊天链路里塞判定逻辑。
2. 判定用**便宜小模型 + temperature 0.3**，比主模型便宜一个数量级。
3. 判定结果可以事后统计 —— 她为什么没说、有多少轮被判PASS，落地成数据。

## 为什么温度必须压到 0.3

热采样（temperature 1.0）下，同一段对话跑两次可能一个 PASS 一个 OPEN，
群里表现就是"她说话没规律"。判定要的是**稳定**，不是创造力。

## 为什么给判据、不给场景映射

用户明确反对「什么场景用什么方案」那种规则 —— 每加一个场景就得补一条，
永远补不完，而且把她管死了。下面只给**判据**和一份 PASS 清单，
具体什么算"明显能接"是她自己的判断。

## 挂在哪个钩子上

`on_turn_in`。它是**唯一能一票否决**的入站钩子：往 /api/chat 返回空串
就等于整轮不成立，大脑根本不会被问。别的钩子都只能"往提示里加东西"。

注意：这跟"给判据≠给许可"是同一类错误，这里是反过来的 —— 闸门是**否决权**，
所以默认是 OPEN（放行），只有判得很确定时才拦。全程走降级：**任何不确定
都放行**，绝不能因为判定失败而让她哑掉。

## 慢就是失败（实测出来的）

2026-10-04 用硅基真测（`_test_gate_models.py`）：

| 模型 | 判对 | 超时 | 平均 |
|---|---|---|---|
| zai-org/GLM-5.3（主模型） | 8/10 | 2 | 6.9 秒 |
| **deepseek-ai/DeepSeek-V4-Flash** | **9/10** | 0 | **2.0 秒** |
| inclusionAI/Ling-flash-2.0 | 6/10 | 0 | 0.9 秒 |

最慢的**不是最差的**，但再快的判不准也不值 —— 判定错一次靠连 PASS 保底
能兜住，等4 秒没人兜。config 里默认填了 V4-Flash。

**实测两条反直觉的，别照抄别处的做法去改**：

1. **删掉系统提示会变慢**（4.2 秒）而且判错 —— 那些判据不是负担，是它
   判得准的原因。别为了"省 token"精简它。
2. **max_tokens 千万别掐到 5**。会截断模型的思考，吐出 `PASSPASS`、
   `OPEN</think>` 这种半截输出，反而要花一次去猜。给够空间（200）、
   靠提示词要求简短，实测输出干净且同速。
"""
import os
import re
import json
import time
import threading

NAME = "gate"
DESC = "发言闸门：拿不准该不该接话的时候，先判一下再开口"
# 声明开关：这是个可能误拦的判定层，出问题要能一键关掉退回旧行为。
FEATURE = "gate"

_TIMEOUT = 15                 # 秒。实测 flash 模型 1.8~2.0 秒，8 秒偶尔会网络抖动超时。
    # 超时不是错、是降级放行 —— 但放过多次统计就没意义了，所以给够。
    # 别设成 30：群里等 30 秒比不说话还假。
# 只要两个词，但**别把 max_tokens 掐到 5** —— 那样会把模型的思考过程截断在半路，
# 吐出 "PASSPASS" / "OPEN</think>" 这种半截输出，反而要多花一次去猜它想说什么。
# 给够空间，靠提示词要求简短（实测输出干净且同速）。
_MAX_TOKENS = 200
_CTX_CHARS = 1200# 塞给闸门的上下文上限

# 同一段对话里连着被判几次 PASS 就直接放行。防"她一直不接话"这种
# 越狱 —— 闸门误拦一次是运气，连着拦就说明她被关起来了。
_MAX_CONSECUTIVE_PASS = 3

# 统计按渠道分开存 —— 她在群里和App 里的"沉默率"完全是两回事，
# 混在一起看不出是哪边判错了。扁平 key 会让这个永远查不出来。
_STATS = {"wechat": {"open": 0, "pass": 0},
          "app": {"open": 0, "pass": 0},
          "other": {"open": 0, "pass": 0},
          "forced_open": 0}
_stats_lock = threading.Lock()
_pass_streak = {}            # 作用域名 -> 连着 PASS 了几次
_last_judge = {}             # 作用域名 -> 最近一次判定结果（给面板/调试看）


def _cfg():
    """闸门配置。读 config.json 的 gate 段。"""
    try:
        from runtime import CFG_PATH
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f).get("gate") or {}
    except Exception:
        return {}


def _model(api_config, cfg):
    """用哪个模型判定。没配就用主模型 —— 宁可用贵的模型，也不能不判。"""
    return str(cfg.get("model") or api_config.get("model") or "")


def _bump(src, field):
    """给某个渠道记一次 open/pass。"""
    try:
        with _stats_lock:
            key = src if src in _STATS else "other"
            _STATS.setdefault(key, {"open": 0, "pass": 0})
            _STATS[key][field] = _STATS[key].get(field, 0) + 1
    except Exception:
        pass


def _bump_global(field):
    """顶层计数（如forced_open），**不能**带渠道 —— 它不是某个渠道的量。

    跟 _bump 分开就是为了防止再出现"往渠道 dict 里塞了全局字段"那种错：
    那样查wechat 的数据里会混进forced_open，看着像她频繁被强制放行。
    """
    try:
        with _stats_lock:
            _STATS[field] = _STATS.get(field, 0) + 1
    except Exception:
        pass


def _scope(ctx):
    """判定的作用域。同一群是同一个闸门，不同群各自计连PASS 次数。

    按 (渠道, 人设) 分 —— 换人设就换一套人，落成一个人的连续判定会串味。

    🔴 **只取人设的 key，绝不能取 brain.persona 那个 dict**。
    brain.persona 是完整人设（几千字），str() 出来就是它的 repr ——
    于是 `_pass_streak` / `_last_judge` 的键变成一整段人设文本：
    连着PASS 的计数按"人设全文"分组，等于每次判定都是新的一组、
    连PASS 保护永远不触发；而 _last_judge 会把整个 dict 塞进 /api/gate/stats
    的返回值（几百字），面板直接被撑爆。
    取法用 persona_key_of(src)，那是 Brain 专门给的只读入口。
    """
    src = str(getattr(ctx, "src", "") or "app")
    persona = ""
    try:
        p = getattr(ctx, "brain", None)
        if p is not None:
            f = getattr(p, "persona_key_of", None)
            if callable(f):
                persona = str(f(src) or "")
            if not persona:
                # 没有这个方法就退回旧属性，但只接受 str ——
                # brain.persona 是 dict，拼进去就是上面那个坑。
                v = getattr(p, "persona_key", "")
                persona = str(v or "") if isinstance(v, str) else ""
    except Exception:
        pass
    return "%s.%s" % (src, persona)


def _recent_context(ctx):
    """闸门看的"最近在聊什么"。

    刻意只取最近几条：闸门要判断的是**这一刻**，不需要翻历史。
    它不是记忆检索，翻多了既慢又容易让它以为该回忆旧账。
    """
    brain = getattr(ctx, "brain", None)
    if brain is None:
        return ""
    for attr in ("recent_msgs", "_recent", "last_msgs"):
        v = getattr(brain, attr, None)
        if isinstance(v, (list, tuple)) and v:
            lines = []
            for m in list(v)[-8:]:
                try:
                    who = m.get("role") or m.get("sender") or "?"
                    body = str(m.get("content") or m.get("text") or "").strip()
                    if body:
                        lines.append("%s: %s" % (who, body[:120]))
                except Exception:
                    continue
            if lines:
                return "\n".join(lines)[-_CTX_CHARS:]
    return ""


def _nicknames(ctx):
    """她能被叫的名字：配置 + 人设里的 nicknames。

    两个来源都要。人设那份跟着换人设变（"麦麦"→"知夏"），只看配置会漏
    —— 而漏了就意味着被叫了也不回，那是最明显的 bug。
    拿不到就算，宁可多问一次模型。

    🔴 人设那份必须用 **persona_key_of(src)** 取，不能getattr(b, "persona_key")。
    Brain 上没有 persona_key 这个属性（只有 persona_key_of 方法），
    getattr 恒为 "" → 人设那份永远读不到 → 配置里叫什么她就被叫什么。
    更糟的是这一直是"看起来能用"的状态：配置那份刚好有名字，
    所以被叫名字照样有反应，只有换人设改昵称时才暴露。

    顺带去重：两个来源重叠是常态（配置和人设都写了同一个名字），
    重复项在 _called_by_name 里会白比一遍，无害但没必要。
    """
    out = []
    try:
        c = _cfg()
        for k in ("bot_nicknames", "nicknames"):
            out.extend(c.get(k) or [])
    except Exception:
        pass
    try:
        b = getattr(ctx, "brain", None)
        key = ""
        if b is not None:
            f = getattr(b, "persona_key_of", None)
            if callable(f):
                key = str(f(getattr(ctx, "src", "") or "app") or "")
        if key:
            import persona_store
            data = persona_store.load(key) or {}
            out.extend(data.get("nicknames") or [])
    except Exception:
        pass
    seen = set()
    res = []
    for n in out:
        s = str(n or "").strip()
        if s and s not in seen:
            seen.add(s)
            res.append(s)
    return res


def _called_by_name(ctx, msg):
    """这条消息里有没有人喊她。

    **代码层判，不问模型。** 见 _judge 里的说明：这条判错的后果最严重
    （被@ 却不回），而提示词里写"被叫了就OPEN"小模型不认。
    """
    t = str(msg or "")
    if not t:
        return False
    for n in _nicknames(ctx):
        if len(n) < 2:# 太短的词（单字）容易误命中，宁可漏
            continue
        if ("@" + n) in t or n in t:
            return True
    return False


def _self_share(ctx, lookback=6):
    """最近几条消息里她自己的占比，0.0 ~ 1.0。

    刷屏抑制的输入。取的是**她刚说得多不多**，不是"该不该回"——
    后者由模型判，前者是纯统计、零成本、不会抖。

    认不出的role 一律不算她的（宁可低估自己的占比，也别高估——
    高估会让她变得过度沉默，比刷屏更糟）。
    """
    brain = getattr(ctx, "brain", None)
    if brain is None:
        return 0.0
    for attr in ("recent_msgs", "_recent", "last_msgs"):
        v = getattr(brain, attr, None)
        if not isinstance(v, (list, tuple)) or not v:
            continue
        rows = list(v)[-lookback:]
        mine = 0
        total = 0
        for m in rows:
            try:
                who = str(m.get("role") or m.get("sender") or "").lower()
                body = str(m.get("content") or m.get("text") or "").strip()
            except Exception:
                continue
            if not body:
                continue
            total += 1
            # assistant / self / bot / 她的昵称 都算她自己写的
            if who in ("assistant", "self", "bot", "ai", "me"):
                mine += 1
                continue
            if any(n and n in who for n in _nicknames(ctx)):
                mine += 1
        if total:
            return float(mine) / float(total)
    return 0.0


def _last_self_ago(ctx):
    """她上一次发言距今多少秒。算不出来就返回 None。

    🔴 **不要用 ctx.last_reply_at —— Turn 里没有这个字段。** Turn 只有
    text / src / api_config / brain / meta 五个字段，meta 还是同轮内传递的，
    跨轮不可靠。硬取会永远拿到 None，于是这条判断等于没写（这种"看着
    实现了其实没生效"最难查）。

    所以改成读**聊天存档**：她自己的每一句都落在按人设×渠道分区的文件里，
    取最后一条的时间戳即可。读不到就返回 None，让调用方降级到只用占比。
    """
    import os
    import time as _t
    try:
        brain = getattr(ctx, "brain", None)
        if brain is None:
            return None
        p = brain._log_for(getattr(ctx, "src", "app"))   # 已按人设×渠道分区
        if not p or not os.path.isfile(p):
            return None
        rows = []
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        if not rows:
            return None
        last = rows[-1]
        if str(last.get("role") or "") not in ("assistant", "self"):
            return None            # 最后一条是别人说的，间隔无从算起
        ts = last.get("ts") or last.get("time") or last.get("timestamp")
        if not ts:
            return None
        # ts 可能是秒级或毫秒级；太大就是毫秒
        ts = float(ts)
        if ts > 1e11:
            ts /= 1000.0
        if ts > 1e9 + 5e8:         # 明显是毫秒（2033 年之后）
            ts /= 1000.0
        return max(0.0, _t.time() - ts)
    except Exception:
        return None


# 刷屏抑制阈值。**先拍一个保守值，等有真实数据再调** ——
# MaiBot 那个 +2.65 是从几百轮真实记录拟合出来的，我们没有那些数据，
# 硬套它的数会误判。宁可漏判（刷屏）也不误判（该说不说）。
#
# 三档而不是一档：人的刷屏感是渐进的，一次说 2 句不刺眼，连着 4 句就刺眼。
_SUPPRESS_AT = {        # 自己的占比 >= 这个值 -> 拦
    0.50: "连着说",
    0.34: "说得多",
    0.20: "刚说过",
}
# 距离上次发言多久之内也算"刚说过"（秒）。真人不会隔 5 秒再补一句。
_JUST_SAID_SECS = 90


def _flood_guard(ctx):
    """刷屏抑制。返回 None 放行，返回原因字符串则拦下。

    **纯代码，不问模型。** 两个理由：一是省一次调用（闸门本来就 2 秒），
    二是这条判错的后果是"该说的没说"，比刷屏更明显 —— 让小模型算这种
    可数的事是不必要的风险。

    被 @ 的**不走这里**（在 _judge 里更早放行了）：有人点名还沉默，比刷屏
    难看一百倍。
    """
    try:
        cfg = _cfg().get("flood") or {}
    except Exception:
        cfg = {}

    # 🔴 默认必须是开的 —— 配错一项就整个失效，那是白做。
    enabled = cfg.get("enabled", True)
    if not enabled:
        return None

    share = _self_share(ctx)
    gap = _last_self_ago(ctx)

    # 🔴 **时间信号是主判据，占比只是佐证。**
    # 第一版反着写了（占比到了就拦，读不到时间也拦），测试立刻抓出两个 case：
    # 存档读不到、以及最后一条是别人说的话时，她会因为"最近 6 句里她占一半"
    # 而闭嘴 —— 但这两种情况下"她刚说过"根本无从判断，拦她是瞎拦。
    # 所以：拿不到 gap 就一律放行，宁可漏判也不误判。
    if gap is None:
        return None

    for thr, label in sorted(_SUPPRESS_AT.items(), reverse=True):
        if share < thr:
            break
        # 占比高但隔了很久才回—— 那是"过了一阵才想接话"，不是刷屏。
        # 只在最高那档才不看时间：连着说 4 句就算隔几分钟也刺眼。
        if gap >= _JUST_SAID_SECS and thr < 0.50:
            return None
        return "%s（自己占 %d%%、距上次 %d 秒）" % (
            label, int(share * 100), int(gap))
    return None


def _recent_msgs(ctx):
    brain = getattr(ctx, "brain", None)
    if brain is None:
        return []
    for attr in ("recent_msgs", "_recent", "last_msgs"):
        v = getattr(brain, attr, None)
        if isinstance(v, (list, tuple)) and v:
            return list(v)
    return []


_JUDGE_PROMPT = """你在替一个人判断：群里的最新一条消息，如果她本人在这里，\
她会接话吗？

只回一个词：OPEN 或 PASS

（有人喊她名字的情况已经在代码里放行了，走不到你这里。）

## 一、这条消息是在跟谁说的？

明显是两个人之间的对话（其中一个在 @ 另一个、另一个在回他）→ PASS

### 二、他现在的处境是什么？（**决定分寸，别跳过**）

同一句负面的话要分两种，区别在于**说的是处境还是段子**：

| 他说的 | 处境 | 判定 |
|---|---|---|
| 今天被裁了，晚上去哪儿都挺没意思的 | 难受 | **PASS** 安静待着，插科打诨很讨打 |
| 说实话有点想辞职了 | 难受 | **PASS** |
| 搬了一下午砖，累麻了 | 累 | **PASS** |
| 说今天什么都不想做，谁也不想见 | 难受 | **PASS** |
| 这破程序又崩了，它是不是跟我有仇 | 段子 | **OPEN** 好接 |
| 我把闹钟设成震动，然后睡了十小时 | 段子 | **OPEN** |
| 我把生产库密码改成自己生日，昨天忘了 | 段子 | **OPEN** |
| 睡过头了，上班迟到了哈哈 | 段子 | **OPEN** |

分不清的时候当段子（宁可接，也不要在他难受的时候插嘴）。

### 三、这几类一律 PASS

- 客套收尾：哦 / 好的 / 行 / 睡了 / 晚安 / 回头聊
- 碎片消息：单个字母、乱码、纯标点、图片
- 同一个人还在连着发，最后一条明显没说完（"就是那个"、"然后…"）
- 同一个梗她已经接过两次了，接第三次就是复读
- 话题转到很技术的细节上，跟她没关系

### 四、剩下的都是 OPEN

分享好消息、提了个问题、说了件离谱的事、抛出个梗、说了句蠢话、
她刚冷过场想找个话头。**别因为"我也不确定"就 PASS** —— 你不是要完美
判断，是要像个真人。真人在群里也会多嘴一句。

真的判断不出来（信息太少、看不出在说什么），就 PASS。"""


def _judge(ctx, cfg):
    """问便宜模型一次。返回 (should_speak, reason)。任何异常都放行。"""
    api = getattr(ctx, "api_config", None) or {}
    if not api.get("api_key") or not api.get("api_base"):
        return True, "没有 api 配置"
    msg = str(getattr(ctx, "text", "") or "").strip()
    if not msg:
        return True, "空消息"
    if not _model(api, cfg):
        return True, "没配模型"

    #🔴 **被叫名字必须在代码层直接放行，不许问模型。**
    # 实测栽在这：提示词里写了「@ 了就回 OPEN、不用往下想」，模型照样
    # 判 PASS —— 小模型不吃这种"软指令"，思考两句就把它推翻了。
    # 而这条判错的后果是"她被@了却不回"，是最明显的不像人。
    # 关键词匹配在代码层做一次，零成本、不出错、不会抖。
    if _called_by_name(ctx, msg):
        return True, "有人喊了名字"

    # 🔴 刷屏抑制放在被@ 放行之后、问模型之前。
    # 顺序很要紧：被叫了名字必须无条件说话，别的都不管。
    why = _flood_guard(ctx)
    if why:
        return False, "刷屏抑制：" + why

    body = "最近在聊：\n%s\n\n最新一条：\n%s" % (
        _recent_context(ctx) or "（没历史）", msg[:600])

    try:
        import httpx
        r = httpx.post(
            api["api_base"].rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + api["api_key"]},
            json={"model": _model(api, cfg),
                  "messages": [
                      {"role": "system", "content": _JUDGE_PROMPT},
                      {"role": "user", "content": body}],
                  # 判定要稳定，不要创造力 —— 见文件头
                  "temperature": 0.3,
                  "max_tokens": _MAX_TOKENS},
            timeout=_TIMEOUT)
        r.raise_for_status()
        out = str(r.json()["choices"][0]["message"]["content"] or "").strip()
        # 取**最后**一个词，不是第一个。模型有时会先写一段再给结论，
        # 或者吐出 "PASSPASS" 这种重复 —— 最后一个才是它真正的答案。
        # 用 search 会让 "……所以 OPEN" 判成沉默，而她本该开口。
        hits = re.findall(r"PASS|OPEN", out.upper())
        if not hits:
            return True, "看不懂：" + out[:20]
        return (hits[-1] == "OPEN"), (hits[-1] + " | " + out[:20])
    except Exception as e:
        return True, "判不了：%s" % (str(e)[:60])


def on_turn_in(ctx):
    """入站闸门。要拦就调`ctx.veto(原因)`，别用返回值表达否决。

    ## 为什么不是"返回空串"

    空串在这条链路上没有信息量：`reminder` 插件"没有提醒要注入"返回空串，
    "所有插件都放行"时 `join([])` 也是空串。拿它当否决信号时，
    "她一句话都说不出来"这件事**每个插件单独看都正常**。
    所以改成显式声明 —— 见 `Turn.veto`。

    放行时返回 None：没有提示要注入，也不用否决。
    """
    msg = str(getattr(ctx, "text", "") or "").strip()
    if not msg:
        return None
    src = str(getattr(ctx, "src", "") or "app")
    cfg = _cfg()

    if not cfg.get("enabled", False):
        _bump(src, "open")
        return None
    # 群里才需要闸门；App 是一对一，她本来就该回。
    if src not in (cfg.get("sources") or ["wechat"]):
        _bump(src, "open")
        return None
    # 太短的（"嗯""?"）判不出东西，别为这个花一次调用
    if len(msg) < 2:
        return None

    scope = _scope(ctx)

    # 连着 PASS 太多次就强制放行。她不是不能说话，是闸门误判了。
    try:
        if _pass_streak.get(scope, 0) >= _MAX_CONSECUTIVE_PASS:
            _pass_streak[scope] = 0
            _bump_global("forced_open")
            _last_judge[scope] = "强制放行（连PASS 太多次）"
            return None
    except Exception:
        pass

    # 判定本身必须包 try —— 闸门炸了就等于她哑掉，比判错严重得多。
    # 出错一律放行，永远不拦。
    try:
        ok, reason = _judge(ctx, cfg)
    except Exception as e:
        ok, reason = True, "判定出错，放行：%s" % (str(e)[:60])
    try:
        _last_judge[scope] = ("OPEN" if ok else "PASS") + " · " + str(reason)
        if ok:
            _pass_streak[scope] = 0
        else:
            _pass_streak[scope] = _pass_streak.get(scope, 0) + 1
    except Exception:
        pass

    if ok:
        _bump(src, "open")
        return None
    _bump(src, "pass")
    # 拦。ctx.veto() 是显式否决，不是靠返回值 —— 理由见Turn.veto。
    return ctx.veto("闸门判定 PASS：" + str(reason)[:80])


# --- 给面板/调试看的 ---

def stats():
    return dict(_STATS)


def last_verdict(scope=None):
    return _last_judge.get(scope or "", "")


def _r_stats(body, query):
    """看板：她判了多少次、放行/拦下各多少、最近一次判定是什么。

    只读，不改任何状态。
    """
    scope = str(query.get("scope") or "")
    return {"ok": True, "stats": stats(),
            "streak": dict(_pass_streak),
            "last": _last_judge.get(scope, "") if scope else dict(_last_judge)}


# 路由只有 POST：core 的 GET 分派器也是拿这张表接的，别混两种方法
ROUTES = [("POST", "/api/gate/stats", _r_stats)]