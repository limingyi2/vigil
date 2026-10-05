# -*- coding: utf-8 -*-
"""语音泄漏测试：群里不该出现 mp3 文件名。

背景：微信群发出去的是纯文本，`[voice:v_xxx.mp3]` 会被当正文原样发进群。
根因有三条同时成立——
  1. 提示词（persona_store）无条件教她用 [voice]，不分渠道
  2. resolve_voice_tag 一句开关都没查，标了必合成
  3. features.DEFAULTS 里没有 voice 键，配置里写什么都拦不住

修法：在合成入口判 features.on("voice")，关掉就只剥标记不合成。
这样不用改提示词（模型标不标记都出不了事）。

🔴 本测试把 features 指向临时 config.json 走完整链路，**不手改内存缓存**。
上一版就是手改缓存的，结果 invalidate() 触发重载把手改的值冲掉，
第 2 组「通过」其实是合成自己失败（无凭据）蒙对的，第 3 组立刻就抓出来了。
闸门类测试必须打真实配置读取，否则测的是缓存不是闸门。

合成器全程打桩返回成功名——绝不让真调 TTS（要花钱、要 1.5~3s，
而且真失败会被误读成「闸门生效了」）。

运行：python _test_voice_gate.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import features
import reply


PASS, FAIL = [], []


def check(name, got, want):
    if got == want:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append(name)
        print("  FAIL  %s\n        期望 %r\n        实际 %r" % (name, want, got))


def has_voice_mark(s):
    """输出里还有没有语音协议的痕迹。三种都算：标记、文件名、情绪标记。"""
    low = (s or "").lower()
    if "[voice" in low or "[语音" in low:
        return True
    return reply._MOOD_RE.search(s or "") is not None


# --- 临时配置：群聊关语音，App 保持开（这才是真实部署形态） ---
_tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                    encoding="utf-8")
json.dump({
    "features": {"voice": True},
    "features_by_src": {"wechat": {"voice": False}},
}, _tmp, ensure_ascii=False)
_tmp.close()

_old_cfg, _old_synth = features._CFG, None
try:
    import voice as real_voice
    _old_synth = real_voice.synth
    _calls = []
    real_voice.synth = lambda *a, **k: (_calls.append(a[0]) or "v_ok.mp3")

    features._CFG = _tmp.name
    features.invalidate()
    features.set_current_src("wechat")

    # 前置：确认配置真读到了（下面所有断言都建立在"voice 确实是关的"上）
    print("\n[0] 前置：临时配置读到了")
    check("群聊 voice 关", features.on("voice"), False)
    check("App voice 开", features.on("voice", src="app"), True)

    # --- 1. 开关确实登记了（不登记的话配置里写什么都无效） ---
    print("\n[1] 开关登记")
    check("DEFAULTS 里有 voice", "voice" in features.DEFAULTS, True)

    # --- 2. 群聊：只剥标记不合成，且剥得干净 ---
    print("\n[2] 群聊（voice 关）-> 只剥标记不合成")
    for label, src_in, want in [
        ("裸标记被剥", "[voice]今天好累", "今天好累"),
        ("中文标记也剥", "[语音]嗯", "嗯"),
        ("已合成写法也剥（用户实际遇到的）",
         "[voice:v_d2a9f418a095d92e.mp3]你那才7度吧", "你那才7度吧"),
        ("全角冒号也剥", "[语音：困]", "困"),
    ]:
        check(label, has_voice_mark(reply.resolve_voice_tag(src_in, {})), False)
        check(label + "·正文留下", reply.resolve_voice_tag(src_in, {}), want)

    out = reply.resolve_voice_tag("[voice]困死了（语气：犯困）", {})
    check("情绪标记也被剥（它只给 TTS 看）", has_voice_mark(out), False)
    check("只剩话", out, "困死了")

    check("无标记不动", reply.resolve_voice_tag("就普通一句话", {}),
          "就普通一句话")

    # 闸门生效的话，合成器一次都不该被调
    check("群聊全程没调 TTS", len(_calls), 0)

    # --- 3. App：语音条照旧（别为了修群聊把 App 的功能也关了） ---
    print("\n[3] App（voice 开）-> 语音条照旧")
    features.set_current_src("app")
    out = reply.resolve_voice_tag("[voice]今天好累（语气：犯困）", {})
    check("App 拿到语音条", out.splitlines()[0], "[voice:v_ok.mp3]")
    check("情绪标记不进转写", "语气" in out, False)
    check("App 确实调了 TTS", len(_calls), 1)

    # 切回群聊：缓存是 30 秒的，验证切渠道立刻生效而不是等缓存过期
    print("\n[4] 切回群聊")
    features.set_current_src("wechat")
    check("切回后立刻生效", has_voice_mark(
        reply.resolve_voice_tag("[voice]又漏了", {})), False)

    # --- 5. 降级 ---
    print("\n[5] 降级")
    features._CFG = r"Z:\__nonexistent__\config.json"
    features.invalidate()
    check("配置读不到时按开处理（不拖累功能）",
          features.on("voice_never_registered_xxx"), True)

finally:
    features._CFG = _old_cfg
    features.invalidate()
    features.set_current_src("app")
    if _old_synth:
        real_voice.synth = _old_synth
    try:
        os.unlink(_tmp.name)
    except Exception:
        pass

print("\n" + "=" * 46)
print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("失败：" + "、".join(FAIL))
print("=" * 46)
sys.exit(1 if FAIL else 0)
