# -*- coding: utf-8 -*-
"""回复后处理：模型吐出原文到发出去之间要过的三道加工。

顺序固定：剥「说」标记 -> [gen:描述] 现场生图 -> [rand:分类] 甩现成图
（randimg.resolve_rand_tags，在 ask_with_retry 里单独调）-> [voice] 合成语音条。
每道都是同一个原则：加工失败就降级成纯文字，不让标记原文漏给用户
"""
import re

from runtime import GEN_TAG_RE


def strip_say_marker(text):
    """剥掉回复首行学主动搭话格式带出的「说」标记。

    「说」单独成行 -> 删掉该行；「说 xxx」-> 只去掉「说」保留后面的话。
    只处理第一条非空行，后面正文里正常的「说」字不动。
    """
    lines = (text or "").splitlines()
    for i, l in enumerate(lines):
        s = l.strip()
        if not s:
            continue
        if s == "说":
            lines[i] = ""
        elif s.startswith("说 ") or s.startswith("说：") or s.startswith("说:"):
            lines[i] = s[1:].lstrip(" ：:")
        break
    return "\n".join(lines).strip()


# 情绪标记：她想说语音时，在话后面用（语气：xxx）标一下，合成前抽走当
# TTS 的 instruction。不走 [voice:xxx] 那种改标记格式的路 ——
# [voice:文件名] 是前端解析音频的协议（chat.html 里
# /\[voice[:：]([^\]]+)\]/ 取出来就是 name 参数），动它三处联动，容易碎。
#
# 用半角括号而不是【】：（）在聊天里更自然，她不太会主动用方括号；
# 剥离时两种都收（模型偶尔会换成【语气：xxx】）。
_MOOD_RE = re.compile(r"[（(\[【]\s*(?:语气|情绪|口吻)\s*[:：]\s*([^）)\]】]{1,40})"
                      r"\s*[）)\]】]")

# 「已合成」写法的语音标记：[voice:v_xxx.mp3] / [语音：xxx]。
# 前面那段 [voice] 的判定漏了这种（那是给 App 前端解析的协议，不该再出现在
# 往外发的文本里），voice 关掉时必须连它带情绪标记一起剥干净。
_VOICE_FILE_TAG_RE = re.compile(r"\[(?:voice|语音)\s*[:：]([^\]]*)\]")
# 音频文件名长这样（v_xxx.mp3 / v_d2a9f418.mp3）。括号里不是它、
# 那就是模型把正文写进括号里了（[语音：困]），得把话留下而不是整条删掉。
_AUDIO_NAME_RE = re.compile(r"^[\w\-.]+\.(?:mp3|m4a|amr|wav|ogg)$", re.I)


def strip_voice_tags(text):
    """把语音标记全剥掉，只留她要说的话。

    分两种写法，处理方式不同：
      `[voice]困死了`      —— 标记在前，正文在外面，整体删标记
      `[voice:v_xxx.mp3]` —— 标记里是音频文件名，整个删掉（文件名绝不能外发）
      `[语音：困]`        —— 模型偶尔把正文写进括号里，只删标记保住话
    """
    raw = text or ""

    def _sub(m):
        inner = (m.group(1) or "").strip()
        return "" if _AUDIO_NAME_RE.match(inner) else inner

    raw = _VOICE_FILE_TAG_RE.sub(_sub, raw)
    raw = raw.replace("[voice]", "").replace("[语音]", "")
    return raw


def extract_mood(text):
    """从正文里抽出情绪描述。返回 (剥掉标记后的正文, 情绪描述)。

    抽不出来就返回原文和空串 —— 那样退回平读，不会因此不发语音。
    一次只取第一个标记：一句语音一个语气，本来也只该有一个。
    """
    raw = text or ""
    m = _MOOD_RE.search(raw)
    if not m:
        return raw, ""
    return _MOOD_RE.sub("", raw, count=1).strip(), m.group(1).strip()


def resolve_voice_tag(text, api_config):
    """把回复里的 [voice] 标记变成真语音条。

    她只在想用声音说的时候加这个标记。命中后把整段文字合成为 mp3，替换成
    `[voice:文件名]`，App 渲染成可播放的语音条。合成失败就把标记悄悄去掉、正文照发
    —— 语音是锦上添花，不能反过来害消息发不出去。

    正文里的（语气：xxx）在合成前抽走：它给 TTS 当 instruction，不该出现在
    语音条下方的转写文字里。

    🔴 受 features 的 voice 开关管（默认开）。关掉时**只剥标记不合成**，
    退化成纯文字。必须在这里判、而不是靠提示词教她别发：群里没有能渲染音频条的
    界面，`[voice:xxx.mp3]` 会被当纯文本原样发出去，真人不在群里甩 mp3 文件。
    提示词那段（persona_store 里）是所有渠道共用的，按渠道改它就要把整套提示词
    按渠道切一遍；在合成入口拦一道，模型标不标记都出不了事。
    """
    raw = (text or "")
    if "[voice]" not in raw and "[语音]" not in raw \
            and not _VOICE_FILE_TAG_RE.search(raw):
        return raw
    try:
        import features
        if not features.on("voice"):
            # 情绪标记一起剥：它是给 TTS 当 instruction 的，露在正文里
            # 就是一句没人看懂的「（语气：犯困）」。
            plain, _mood = extract_mood(raw)
            plain = strip_voice_tags(plain)
            plain = re.sub(r"[ \t]+", " ", plain)
            return re.sub(r"\n{3,}", "\n\n", plain).strip()
    except Exception:
        pass  # 开关系统自己坏了不能拖累语音，退回按开着处理
    if "[voice]" not in raw and "[语音]" not in raw:
        # 只剩 [voice:xxx] 这种"已合成"写法（不该出现）：剥掉文件名留正文，
        # 绝不把 [voice:xxx.mp3] 原样发出去。
        spoken, _mood = extract_mood(strip_voice_tags(raw))
        spoken = re.sub(r"[ \t]+", " ", spoken)
        return re.sub(r"\n{3,}", "\n\n", spoken).strip()
    raw, mood = extract_mood(raw)
    spoken = strip_voice_tags(raw)
    spoken = re.sub(r"\n+", " ", spoken).strip()
    try:
        import voice
        name = voice.synth(spoken, api_config, instruction=mood)
    except Exception as e:
        print("[语音] 调用失败：%s" % str(e)[:120], flush=True)
        name = ""
    if not name:
        return spoken
    return "[voice:%s]\n%s" % (name, spoken)


def resolve_gen_tags(text, look=""):

    import re as _re
    # 生图已停用（用户要求：暂时用不到、以后再加）——[gen:…] 直接删掉，
    # 既不画图也不产出 [img:]。恢复时还原原实现即可，功能位保留。
    return _re.sub(r"\[gen[:：][^\]]*\]", "", text or "", flags=_re.IGNORECASE).strip()
    """把回复里单独成行的 [gen:描述] 真的生成一张图，替换成 [img:文件名]。

    走 _cloud_gen_selfie()：用当前人设的参考图锁脸，生成的是"当前角色本人"的图。
    look 参数保留为向后兼容（旧调用方会传外貌描述），但不再拼进提示词 ——
    锁脸靠参考图，提示词里写长相反而会让模型偏离参考图。
    生成失败就把标签整行删掉，不让标签原文发出去。
    """
    def _sub(m):
        prompt = m.group(1).strip()
        import imggen
        n = imggen._cloud_gen_selfie(prompt)
        return ("[img:%s]" % n) if n else ""
    return GEN_TAG_RE.sub(_sub, (text or ""))
