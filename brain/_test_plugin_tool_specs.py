# -*- coding: utf-8 -*-
"""插件工具必须进得了**原生 function calling** 的工具表。

## 症状

插件写成 `TOOLS = {"xxx": fn}` + `TOOL_HELP = ...`，面板上装得好好的、
清单里有、开关能点，可她就是不用 —— 而且是"连认都不认识"那种不用。

## 根因

原生工具清单原本是 `tools._STATIC_SPECS` 这一张**核心写死的表**：

    tool_specs = _tools.all_specs(...)     # brain.draft_native
      → _static_tool_array()               # 只遍历 _STATIC_SPECS
      → _dynamic_tool_array()              # 只收 JS 技能

`run_tool` 是能路由到插件的，所以老 `[tool:...]` 标签链路一直好使；
但主链路走原生 FC，模型只认 schema 里列出来的工具名 ——
**没登记的工具，在她眼里不存在**。于是"加功能只丢一个文件"这条契约，
在工具类插件上是假的。

## 判据怎么钉

① 插件自报的规格必须出现在 `_static_tool_array()` 里（根因判据）
② 并进来之后**内置工具一个都不能少**（改工具表最容易顺手弄丢几个）
③ 插件不许顶掉核心：重名时核心那份优先，宁可整条不要也不许静默覆盖 ——
   覆盖了就是"某个工具行为突然变了"这种查不出根因的 bug
④ 关掉的插件不许出现在表里（开关得真的管事）
⑤ 纯逻辑（挑词条、排版）不依赖网络，必须逐个钉死

用法：F:/Vigil/venv/Scripts/python.exe _test_plugin_tool_specs.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS = [0]
FAIL = []


def check(name, ok, extra=None):
    if ok:
        PASS[0] += 1
        print("  PASS  " + name)
    else:
        FAIL.append(name)
        print("  FAIL  " + name + ("  <<< " + str(extra)[:260]
                                   if extra is not None else ""))


def selftest_checker():
    """先验 check 本身。

    🔴 `check(cond, label)` 参数写反 = 恒真（非空字符串是真）→ 满屏 OK 一条没测。
    这个坑在本项目踩过两次，所以每个测试文件都得先自检一遍判据工具。
    """
    n0, f0 = PASS[0], len(FAIL)
    check("__selftest__", False)
    good = (len(FAIL) == f0 + 1) and (PASS[0] == n0)
    if FAIL:
        FAIL.pop()                       # 把这笔故意的失败撤掉
    print("  [0] 判据工具自检：传 False 确实记成失败 -> %s" % good)
    if not good:
        raise SystemExit("自检不通过：check 坏了，后面所有 PASS 都不可信")


def main():
    import plugin_host
    import tools

    selftest_checker()

    # --- [1] 装载 ---
    print("\n[1] 插件装载")
    got = plugin_host.load()
    check("meme 插件被装载", "meme" in got, sorted(got))

    # --- [2] 自报规格 ---
    print("\n[2] 插件自报的原生规格")
    specs = plugin_host.tool_specs()
    check("tool_specs() 收到 meme", "meme" in specs, sorted(specs))
    s = specs.get("meme") or {}
    check("规格里有给人看的 desc", bool(str(s.get("desc") or "").strip()), s)
    check("规格里 params 是列表", isinstance(s.get("params"), list), s)
    check("声明了 arg_param", bool(s.get("arg_param")), s)

    # --- [3] 并进原生工具表 ---
    print("\n[3] 原生工具表（根因判据）")
    arr = tools._static_tool_array()
    names = [t["function"]["name"] for t in arr]
    check("meme 出现在原生工具清单里", "meme" in names, names)
    entry = next((t for t in arr if t["function"]["name"] == "meme"), None)
    check("meme 那一条带 description",
          bool(entry and entry["function"]["description"]), entry)
    check("meme 那一条带 JSON Schema 形状的 parameters",
          bool(entry and entry["function"]["parameters"].get("type") == "object"),
          entry and entry["function"].get("parameters"))

    # ② 内置一个都不能少
    missing = [n for n in tools._STATIC_SPECS if n not in names]
    check("内置/原有工具一个都没丢（缺: %s）" % (missing or "无"),
          not missing, missing)
    check("工具表没有重复名字",
          len(names) == len(set(names)),
          [n for n in set(names) if names.count(n) > 1])

    # --- [4] 插件不许顶掉核心 ---
    print("\n[4] 重名时核心优先（不许静默覆盖）")


    class _FakeMod(object):
        NAME = "zzfaketest"
        DESC = "测试用假插件"
        TOOLS = {"calc": lambda a: "假的"}
        TOOL_SPECS = {"calc": {"desc": "假的 calc", "params": [], "arg_param": None}}
        TOOL_HELP = ""

    ph = plugin_host
    old = ph._registry.get("zzfaketest")
    ph._registry["zzfaketest"] = ph.Plugin(_FakeMod, "")
    try:
        arr2 = tools._static_tool_array()
        c = next(t for t in arr2 if t["function"]["name"] == "calc")
        check("calc 的描述仍是核心那份（未被插件覆盖）",
              c["function"]["description"] == tools._STATIC_SPECS["calc"]["desc"],
              c["function"]["description"][:60])
        check("假插件自己的工具名也不会凭空多出来",
              "zzfaketest" not in [t["function"]["name"] for t in arr2])
    finally:
        if old is None:
            ph._registry.pop("zzfaketest", None)
        else:
            ph._registry["zzfaketest"] = old

    # --- [5] 开关得真的管事 ---
    # 注意签名是 (p, src)：src 一路从聊天入口贯下来，按渠道判这个插件开没开。
    # 打桩写成单参数会在 tool_specs(src) 里 TypeError —— 那不是产品坏了，
    # 是判据没跟上新契约。
    print("\n[5] 插件被关掉后不该出现在表里")
    real_enabled = ph._enabled
    try:
        ph._enabled = lambda p, src=None: False
        check("全部关掉时 tool_specs() 返回空", not ph.tool_specs(),
              sorted(ph.tool_specs()))
    finally:
        ph._enabled = real_enabled
    check("恢复后 meme 又回来了", "meme" in ph.tool_specs())

    print("\n[5b] 按渠道过滤：同一个插件可以 App 开着、微信关着")
    # 工具清单是**按渠道**算的（tool_specs(src)），不是全局一张表。
    # 群里关掉的插件，她的工具表里就该没有它 —— 否则模型看得见、
    # 调起来又被 _enabled 拦掉，就成了"看着有、一调就失败"。
    try:
        ph._enabled = lambda p, src=None: str(src) == "app"
        check("src=app 时工具在表里", "meme" in ph.tool_specs("app"),
              sorted(ph.tool_specs("app")))
        check("src=wechat 时同一个工具不在表里", "meme" not in ph.tool_specs("wechat"),
              sorted(ph.tool_specs("wechat")))
    finally:
        ph._enabled = real_enabled
    check("恢复后两个渠道都看得到", "meme" in ph.tool_specs("app")
          and "meme" in ph.tool_specs("wechat"))

    # --- [6] 执行链路 ---
    print("\n[6] invoke_spec 真的能执行")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "meme_mod", os.path.join(HERE, "plugins", "meme.py"))
    mm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mm)

    out = tools.invoke_spec("meme", {"name": "夺笋"})
    check("invoke_spec('meme') 返回非空字符串（永远不许 None/空串）",
          isinstance(out, str) and bool(out.strip()), out)
    # 联网成功与失败都要能自圆其说，所以只钉"说了人话"，不钉具体内容
    check("返回里出现了「夺笋」或者一句明确的查不到说明",
          "夺笋" in (out or "") or "查不了" in (out or "")
          or "没查到" in (out or ""), (out or "")[:120])

    check("未知工具名仍返回 None（不许抛异常）",
          tools.invoke_spec("zz_no_such_tool", {}) is None)

    print("\n[7] 纯逻辑：挑词条 + 排版（不依赖网络）")
    cat = [{"id": 1, "name": "夺笋"}, {"id": 2, "name": "夺笋啊兄弟"},
           {"id": 3, "name": "我真的栓Q"}]
    check("精确命中不挑到更长的那个", (mm._pick("夺笋", cat) or {}).get("id") == 1,
          mm._pick("夺笋", cat))
    check("去掉标点后仍能精确命中",
          (mm._pick("夺笋！", cat) or {}).get("id") == 1, mm._pick("夺笋！", cat))
    check("给一半（用户只记得几个字）能包含命中",
          (mm._pick("栓Q", cat) or {}).get("id") == 3, mm._pick("栓Q", cat))
    check("完全对不上时返回 None", mm._pick("完全不存在的梗abc", cat) is None,
          mm._pick("完全不存在的梗abc", cat))
    check("空参数不挑词条（走随机分支）", mm._pick("", cat) is None)

    txt = mm._fmt({"name": "夺笋", "description": "解释",
                   "types": [{"name": "语言类"}], "related_words": ["熊猫"]})
    check("排版含名字/解释/相关词",
          all(k in txt for k in ("夺笋", "解释", "熊猫")), txt)
    bare = mm._fmt({"name": "只有名字"})
    check("字段缺失时排版仍给出非空文本", bool(bare.strip()), repr(bare))
    check("description 为空串也不会输出空小节",
          "含义与出处" not in mm._fmt({"name": "x", "description": "  "}),
          mm._fmt({"name": "x", "description": "  "}))

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项" % (PASS[0], len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAILED: " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
