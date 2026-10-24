# -*- coding: utf-8 -*-
"""验 core_features/react.py：import 修好了没+ 解析逻辑没被改坏。

先查 import —— 原来的 bug 是 `from config import ...`，而 brain/ 下
没有 config.py，异常被 except 吞掉，_cfg() 永远返回 {}，插件静默不干活。
光看"插件加载成功"查不出这个：加载器 import 的是模块本身，
模块里的函数体 import 要等真被调用才执行。

所以这里直接**调用** _cfg() 和 _data_dir()，逼出那个 import。
"""
import importlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = os.path.dirname(HERE)
sys.path.insert(0, BRAIN)

FAIL = []


def chk(ok, label, detail=""):
    print("  %-4s %s%s" % ("OK" if ok else "BAD", label,
                           ("  -- " + detail) if detail else ""))
    if not ok:
        FAIL.append(label)


print("== 1. 模块能导入 ==")
try:
    react = importlib.import_module("core_features.react")
    chk(True, "import core_features.react")
except Exception as exc:
    chk(False, "import plugins.react", "%s: %s" % (type(exc).__name__, exc))
    print("\n导入就炸了，后面的测不了")
    sys.exit(1)

print("")
print("== 2. 🔴 函数体里的 import 真能跑通 ==")
print("   （原bug 在这里：模块能导入，但 _cfg() 一调用就 ImportError）")
cfg = react._cfg()
chk(isinstance(cfg, dict), "_cfg() 返回 dict，不是异常", repr(cfg)[:120])
d = react._data_dir()
chk(bool(d), "_data_dir() 拿到真实目录", repr(d))
if d:
    chk(os.path.isdir(d), "目录已建出来", d)
    chk("brain" in d.replace("\\", "/"),
        "落在 brain/data 下面（会被备份的地方）", d)

print("")
print("== 3. _cfg() 真的读到了 react 段 ==")
# 读不到和"读到但enabled=false"在 on_reply 里是同一个后果：直接 return。
# 所以要分清是"没读到"还是"读到了但关着"。
cfg2 = react._cfg()
chk("enabled" in cfg2, "cfg 里有 enabled 键（说明真的读到了 config.json）",
    "keys=%s" % sorted(cfg2.keys()))
print("   enabled 当前是 %r" % cfg2.get("enabled"))

print("")
print("== 4. 解析逻辑（这几条与 import 无关，防改坏）===")
cases = [
    ("完整 think + 工具 + 回答",
     "<think>她得查天气</think>\n[tool:weather:北京]\n北京现在 3 度。",
     ("她得查天气", [("weather", "北京")], "北京现在 3 度。")),
    ("只有 think",
     "<think>用户问心情</think>你今天还好吗？",
     ("用户问心情", [], "你今天还好吗？")),
    ("老格式：只有标签没有 think",
     "[tool:calc:1+1]\n答案是 2",
     ("", [("calc", "1+1")], "答案是 2")),
    ("全角冒号",
     "<think>x</think>[tool：websearch：杭州天气]\n今天热。",
     ("x", [("websearch", "杭州天气")], "今天热。")),
]
for label, raw, want in cases:
    got = react.parse_react(raw)
    chk(got == want, label, "got=%r" % (got,))

print("")
print("== 5. 🔴 解析不出来必须原样返回（失败模式只能是没变聪明）==")
for raw in ["就是普通一句话，没任何标签。",
            "",
            "<think>没闭合",
            "这里有个 [tool:x] 但不在开头"]:
    got = react.parse_react(raw)
    chk(got[0] == "" and got[1] == [] or raw.strip() == got[2],
        "回落安全: %r" % raw[:24], "got=%r" % (got,))

print("")
print("== 6. on_reply 端到端 ==")


class Ctx(object):
    src = "app"
    text = "北京现在几度"


# enabled=false 时必须**一字不改**返回
out = react.on_reply(Ctx(), "北京现在 3 度。")
chk(out == "北京现在 3 度。", "enabled=false 时原样返回", repr(out))

# 手动打开再跑一次，验证真能剥 think、且落了盘
real = react._cfg
react._cfg = lambda: {"enabled": True}
try:
    tmpd = tempfile.mkdtemp(prefix="reacttest_")
    react._data_dir = lambda: tmpd
    out2 = react.on_reply(Ctx(), "<think>要查天气</think>[tool:weather:北京]\n北京 3 度。")
    chk("<think>" not in out2, "think 被剥掉了", repr(out2))
    chk("[tool:" not in out2, "工具标签没漏给用户", repr(out2))
    chk("3 度" in out2, "回答内容还在", repr(out2))

    files = os.listdir(tmpd)
    chk(bool(files), "落了盘", str(files))
    if files:
        rec = json.loads(open(os.path.join(tmpd, files[0]),
                              encoding="utf-8").read().splitlines()[0])
        chk(rec.get("think") == "要查天气", "盘里记了 think", repr(rec.get("think")))
        chk(rec.get("tools") == [{"name": "weather", "arg": "北京"}],
            "盘里记了工具调用", repr(rec.get("tools")))
        chk(rec.get("user") == "北京现在几度", "盘里记了用户输入",
            repr(rec.get("user")))
        # 🔴 思维链不能带key/记忆库
        blob = json.dumps(rec, ensure_ascii=False)
        chk("api_key" not in blob and "brain_token" not in blob,
            "盘里没有凭据")
    st = react.stats()
    chk(st.get("turns", 0) >= 1, "stats 记了轮数", repr(st))
finally:
    react._cfg = real

print("")
print("== RESULT ==")
if FAIL:
    print("   %d check(s) FAILED:" % len(FAIL))
    for f in FAIL:
        print("     - " + f)
    sys.exit(1)
print("   all checks passed")
sys.exit(0)
