# -*- coding: utf-8 -*-
"""入站钩子的**否决通道**：插件可以一票否决，让这一轮整条不成立。

## 为什么是独立测试文件

闸门（gate）装上以后一直是"看着在跑、其实没生效"的状态：判定确实跑了、
统计确实在涨，但她在群里每次都回。这条链上踩过两个坑，一个比一个隐蔽，
而**两个都是"两边都自证清白"**：

1. 空串被 `.strip()` 当成"没返回东西"过滤掉 → 宿主根本不知道要否决
2. 反过来：把空串当否决信号 → `reminder` 插件"没有提醒"返回空串、
   `join([])`（全都放行）也是空串 → **她一句话都说不出来**
   而每个插件单独看都正常

第 2 个坑是我自己改第1 个坑时踩进去的：修好第 1 个后部署到云端，
三条消息全被拦下，其中一条明明该放行。所以第 5 组那条
"所有人都放行时必须能说话"是这个文件最重要的断言。

## 判据

不看任何一方的日志，只看「最终有没有真的跳过追问」：
- 源码层：否决靠 ctx.veto()、server 读 ctx.vetoed
- 行为层：造假插件走完整条链，看 on_turn_in + ctx.vetoed 的组合
"""
import io
import os
import re
import sys
import types

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_OK = 0
_BAD = 0


def check(label, cond, extra=""):
    global _OK, _BAD
    if cond:
        _OK += 1
        print("  PASS  %s" % label)
    else:
        _BAD += 1
        print("  FAIL  %s%s" % (label, ("\n        " + str(extra)) if extra else ""))


def group(t):
    print("\n[%s]" % t)


def _body_of(path, defname):
    """取函数体（跳过 docstring，只留代码）。

    🔴 必须切docstring：文件头讲坑的注释里就写着被淘汰的写法，
    按全文 find 会匹配到注释，然后报"没改"的假失败。
    """
    lines = open(path, encoding="utf-8").read().splitlines()
    st = None
    for i, ln in enumerate(lines):
        if ln.startswith(defname):
            st = i
            break
    if st is None:
        return None, lines
    rest = lines[st:]
    ci = 0
    for i, ln in enumerate(rest):
        if ln.strip() == '"""':
            ci = i
            break
    return [ln for ln in rest[ci + 1:ci + 40]
            if ln.strip() and not ln.strip().startswith("#")], lines


def make(name, fn):
    mod = types.ModuleType("fakep_" + name)
    mod.NAME = name
    mod.DESC = "假插件"
    mod.on_turn_in = fn
    return mod


def reg(mod):
    import plugin_host
    p = plugin_host.Plugin(mod, "<fake>")
    with plugin_host._lock:
        plugin_host._registry[mod.NAME] = p


def unreg(*names):
    import plugin_host
    with plugin_host._lock:
        for n in names:
            plugin_host._registry.pop(n, None)


def main():
    import plugin_host
    hp = os.path.join(HERE, "plugin_host.py")
    sp = os.path.join(HERE, "server.py")
    gp = os.path.join(HERE, "plugins", "gate.py")

    group("1. 源码层：否决走 ctx.veto()，不靠返回值")
    code, _ = _body_of(hp, "def on_turn_in(ctx):")
    check("找得到 on_turn_in 定义", code is not None)
    cb = "\n".join(code)
    check("on_turn_in 判的是 ctx.vetoed", "ctx.vetoed" in cb, cb[:200])
    check("**没有**用返回值判否决（v == \"\" 那种写法已废弃）",
          'v == ""' not in cb, cb[:200])
    check("Turn 上有 veto 方法", "def veto(" in open(hp, encoding="utf-8").read())
    check("Turn 有 vetoed 字段",
          "self.vetoed = False" in open(hp, encoding="utf-8").read())

    group("2. 源码层：server 读 ctx.vetoed，不是判空串")
    ssrc = open(sp, encoding="utf-8").read()
    check("server 里有 _tctx.vetoed", "_tctx.vetoed" in ssrc)
    check("server 不再用 _extra == \"\" 判否决", '_extra == ""' not in ssrc)
    check("否决时 return（不再追问大脑）",
          re.search(r"vetoed.*?_json\(200.*?return", ssrc, re.S) is not None)

    group("3. 🔴 最重要：所有插件都放行时，她必须能说话")
    # 这条是我自己改完第 1 个坑后踩进去的第 2 个坑。
    # 空串当否决信号时，"全都放行" = join([]) = "" = 被当成否决。
    unreg("__t_none")
    reg(make("__t_none", lambda ctx: None))
    ctx = plugin_host.Turn(text="马麦", src="wechat")
    out = plugin_host.on_turn_in(ctx)
    check("全部放行时 ctx.vetoed = False", ctx.vetoed is False)
    check("全部放行时返回空串（确实没有提示）", out == "", repr(out))
    check("🔴 但宿主看的是 vetoed 而不是返回值，所以会照常问大脑",
          ctx.vetoed is False and out == "")
    unreg("__t_none")

    group("4. 行为层：真否决与放行不混淆")
    unreg("__t_v")
    reg(make("__t_v", lambda ctx: ctx.veto("测试")))
    ctx2 = plugin_host.Turn(text="今天被裁了", src="wechat")
    out2 = plugin_host.on_turn_in(ctx2)
    check("调了 veto -> vetoed = True", ctx2.vetoed is True)
    check("调了 veto -> on_turn_in 返回空串", out2 == "", repr(out2))
    check("宿主会因此跳过追问", ctx2.vetoed is True)
    check("veto 记了原因（调试要看）",
          getattr(ctx2, "veto_reason", "") == "测试",
          getattr(ctx2, "veto_reason", ""))
    unreg("__t_v")

    group("5. 🔴 reminder 那种「返回空串」不能变成否决")
    # reminder 没提醒要注入时返回 ""。这个值必须纯粹是"没提示"。
    unreg("__t_rm")
    reg(make("__t_rm", lambda ctx: ""))
    ctx3 = plugin_host.Turn(text="在吗", src="app")
    out3 = plugin_host.on_turn_in(ctx3)
    check("返回空串（模拟 reminder 没提醒）-> 不否决", ctx3.vetoed is False)
    check("返回空串 -> 也不进system", out3 == "", repr(out3))
    unreg("__t_rm")

    group("6. 插件抛异常 -> 放行（闸门炸了不能让她哑掉）")
    bad = types.ModuleType("fakep_bad")
    bad.NAME = "__t_bad"
    bad.DESC = "会炸的"

    def boom(ctx):
        raise RuntimeError("故意炸")

    bad.on_turn_in = boom
    reg(bad)
    ctx4 = plugin_host.Turn(text="hi", src="wechat")
    out4 = plugin_host.on_turn_in(ctx4)
    check("异常不否决", ctx4.vetoed is False)
    unreg("__t_bad")

    group("7. 🔴 多插件：一个否决，其余的不再跑")
    calls = []
    unreg("__t_a", "__t_b", "__t_c")
    reg(make("__t_a", lambda ctx: (calls.append("a"), "甲提示")[1]))
    reg(make("__t_b", lambda ctx: (calls.append("b"), ctx.veto("乙拦了"))[1]))
    reg(make("__t_c", lambda ctx: (calls.append("c"), "丙提示")[1]))
    ctx5 = plugin_host.Turn(text="x", src="wechat")
    out5 = plugin_host.on_turn_in(ctx5)
    check("有否决时 vetoed=True", ctx5.vetoed is True)
    check("否决之后的插件不再跑（省掉它们的活）", "c" not in calls, calls)
    check("返回空串（不往 system 塞东西）", out5 == "", repr(out5))
    unreg("__t_a", "__t_b", "__t_c")

    group("8. 提示照常拼（别为了加否决把注入功能弄坏）")
    unreg("__t_p1", "__t_p2")
    reg(make("__t_p1", lambda ctx: "提示甲"))
    reg(make("__t_p2", lambda ctx: "提示乙"))
    ctx6 = plugin_host.Turn(text="x", src="app")
    out6 = plugin_host.on_turn_in(ctx6)
    check("两个插件的提示都拼上了", "提示甲" in out6 and "提示乙" in out6, out6)
    check("没被当成否决", ctx6.vetoed is False)
    unreg("__t_p1", "__t_p2")

    group("9. 闸门插件的代码符合新约定")
    gsrc = open(gp, encoding="utf-8").read()
    gcode, _ = _body_of(gp, "def on_turn_in(ctx):")
    gb = "\n".join(gcode or [])
    check("闸门用 ctx.veto(", "ctx.veto(" in gsrc)
    check("闸门不再 return \"\"（旧写法已废弃）", 'return ""' not in gb, gb[-200:])
    check("放行路径返回 None", gb.count("return None") >= 2, gb.count("return None"))
    check("被叫名字/太短/未开/非微信 四个早退都放行",
          gb.count("return None") >= 4, gb.count("return None"))

    group("10. 🔴 作用域 key 不能再是人设全文")
    # 踩过的：_scope 里 getattr(brain, "persona_key") 不存在，
    # 于是退到 getattr(brain, "persona") 拿到整个 dict，
    # str() 出来是几千字人设的 repr，连 PASS 计数按"全文"分组 = 永不触发。
    g2 = open(gp, encoding="utf-8").read()
    check("_scope 用 persona_key_of（Brain 上真实存在的方法）",
          "persona_key_of" in g2)
    check("_scope 不再直接 getattr persona dict",
          re.search(r'getattr\(p,\s*"persona"', g2) is None)
    check("_nicknames 也用 persona_key_of", g2.count("persona_key_of") >= 2,
          g2.count("persona_key_of"))
    check("昵称去重了（配置+人设本来就重叠）",
          "seen = set()" in g2)

    print("\n" + "=" * 46)
    print("通过 %d / %d" % (_OK, _OK + _BAD))
    print("=" * 46)
    return 0 if _BAD == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
