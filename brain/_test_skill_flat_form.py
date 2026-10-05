# -*- coding: utf-8 -*-
"""技能工具的**平铺写法**能不能跑通 —— 这条线以前是死的。

## 背景

模型写标签最熟练的形状是 `[tool:名字:参数]`（天气、搜索都这么写）。
技能却要求 `[tool:skill_call:{"name":"..","args":{..}}]` —— JSON 套 JSON。
实测 DeepSeek 一次都不肯这么写，于是它自己编了个运势交差。

更糟的是：就算它照 have written 了托平铺名 `[tool:daily-fortune__today:...]`，
`plugin_host.run_tool` 查静态索引（=`TOOLS` 装载时的快照，只有
skill_list/skill_call 两个壳）**也查不到** → 返回 None → 外层拼
「查询失败，照实说查不到，别编」。
症状一律是"技能没生效"，而宿主活着、工具在册、渠道开着。

## 判据

这个测试不看"能不能调用 API"，而是复现**模型写的原话字符串**，
从 tools.TOOL_TAG_RE 开始一路走到技能返回的内容。任何一环断了，
下面某个 check 就会红。

需要本地技能宿主活着（127.0.0.1:8790），否则不能测。
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

OK, BAD = [], []


def check(label, cond, extra=""):
    # 参数顺序跟仓库里其它 _test_*.py 一致：check(判据说明, 布尔值)
    # —— 反过来写的话 label 是个非空字符串、恒为真，页面会打出
    # 「OK False」这种东西（我第一版就写反了）。
    (OK if cond else BAD).append(label)
    print("  %-4s %s%s" % ("OK" if cond else "BAD", label,
                           ("  <- " + repr(extra)[:120]) if extra else ""))


def main():
    print("== 前置：本地技能宿主要在 ==")
    import json
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        f = op.open(urllib.request.Request("http://127.0.0.1:8790/health"),
                    timeout=10)
        health = json.loads(f.read().decode("utf-8"))
    except Exception as e:
        print("  宿主没起来（%s）—— 先在 brain/skill_host 跑 node server.js" % e)
        return 1
    print("  宿主:", health)
    if not health.get("ok"):
        print("  宿主说不正常，不往下测")
        return 1

    import features
    features.set_current_src("app")

    import plugin_host
    import tools

    # skill 是 plugin，必须真装载，否则下面的模块一个都拿不到。
    # all_plugins() 返回 {name: Plugin} 的副本，直接用 load() 的返回值
    # 少一层间接（也更难错用）。
    loaded = plugin_host.load()
    names = sorted(loaded.keys())
    print("  已装载插件:", names)
    check("skill 插件真的装载了", "skill" in names, names)

    p = plugin_host.get("skill")
    check("拿得到 skill 插件对象", p is not None)
    if p is None:
        return 1
    mod = p.mod

    real = sorted(mod._all_tools_now().keys())
    print("  宿主上的工具:", real)
    check("宿主至少有一个技能工具", len(real) > 0, real)

    print("\n[1] 认领：宿主上真的有，才认")
    check("认领 daily-fortune__today", mod.owns_tool("daily-fortune__today"))
    check("不认领空气", not mod.owns_tool("根本不存在__xx"))
    check("不认领内置工具（别抢 weather/search）",
          not mod.owns_tool("weather") and not mod.owns_tool("search"))
    check("不认领没有 __ 分隔符的裸名", not mod.owns_tool("date-now"))

    print("\n[2] 从模型写的原话字符串一路走到结果")
    line = '[tool:daily-fortune__today:{"who": "验证"}]'
    tags = [(m.group(1) or "", m.group(2) or "")
            for m in tools.TOOL_TAG_RE.finditer(line)]
    print("   正则切出:", tags)
    check("正则能从模型原话里切出带连字符+双下划线的名字",
          len(tags) == 1 and tags[0][0] == "daily-fortune__today", tags)

    name, arg = tags[0]
    res = plugin_host.run_tool(name, arg)
    print("   plugin_host.run_tool ->", repr(res)[:220])
    check("动态兜底真接住了（不是返回 None）", res is not None, res)
    txt = str(res or "")

    markers = ["大吉", "吉", "中吉", "未", "小吉", "平", "综合", "宜", "忌",
               "幸运色", "幸运数字", "幸运方位", "综合分"]
    hit = [m for m in markers if m in txt]
    check("返回的是技能产出的真实运势（不是「查不到」）", bool(hit), txt[:160])
    check("没有混进「查不到/失败」这类兜底话",
          ("查不到" not in txt) and ("查询失败" not in txt)
          and ("没有这个技能工具" not in txt), txt[:160])

    print("\n[3] 不写参数时不能被「参数不合法」怼回去")
    r2 = mod.invoke_tool("daily-fortune__today", "")
    print("   无参 ->", repr(r2)[:140])
    check("无参也能跑出运势", bool([m for m in markers if m in str(r2 or "")]),
          str(r2)[:140])

    print("\n[4] 老写法仍然好使（兼容，不许回归）")
    old = mod.skill_call(None, "daily-fortune__today", '{"who": "验证"}')
    check("skill_call 还能用", bool([m for m in markers if m in str(old or "")]),
          str(old)[:140])

    print("\n[5] 提示词教的是平铺写法")
    import ast
    src = io.open(os.path.join(HERE, "plugins", "skill.py"),
                  encoding="utf-8").read()
    tree = ast.parse(src)
    th = ""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if getattr(t, "id", "") == "TOOL_HELP":
                    th = node.value.value
    check("TOOL_HELP 里有平铺写法的示例",
          "[tool:daily-fortune__today:" in th, th[:120])
    check("TOOL_HELP 不再把 skill_list 当必经第一步",
          "先调 `skill_list`" not in th and "1. 先调" not in th)

    print("\n== %d OK / %d BAD ==" % (len(OK), len(BAD)))
    if BAD:
        print("失败:", BAD)
        return 1
    print("全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())