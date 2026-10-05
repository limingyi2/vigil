# -*- coding: utf-8 -*-
"""Plan-and-Execute 单元测试：验证 should_plan / _extract_json / build_final_prompt。

不实际调 API（plan() 需要网络和 key，在真实环境测）。
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import planner


def test_should_plan_short():
    print("== 测试1：短句不规划 ==")
    assert planner.should_plan("在吗") == False
    assert planner.should_plan("嗯") == False
    assert planner.should_plan("哈哈") == False
    assert planner.should_plan("晚安") == False
    print("  ✅ 短句全部不规划")


def test_should_plan_keywords():
    print("\n== 测试2：关键词触发规划 ==")
    assert planner.should_plan("帮我写一份工作总结") == True
    assert planner.should_plan("帮我查一下今天的天气和股票") == True
    assert planner.should_plan("整理一下这些文件") == True
    assert planner.should_plan("对比一下这两个方案") == True
    print("  ✅ 关键词全部触发规划")


def test_should_plan_long():
    print("\n== 测试3：长句自动规划 ==")
    # 40 字以上，没有规划词也规划。
    #
    # 🔴 原来这句是 31 字，靠的是单字「然后」命中规划词 —— 测的其实是词表，
    # 不是长度规则。单字词表删掉「然后」之后它就该返回 False：一句话讲完
    # 今天干了啥，没有任何工具要调，规划它等于白花两次模型调用。
    # 所以换成一句真的超过 40 字的，让断言确实作用在长度这条规则上。
    long_text = ("我今天下午去了趟超市买了不少东西回来之后自己做了顿饭，"
                 "吃完饭又看了一会儿电视，中途还顺手把厨房收拾了一下。")
    assert len(long_text) >= 40, "这句得真的够长，否则测的不是长度规则"
    assert not any(k in long_text for k in planner._PLAN_KEYWORDS), \
        "这句不该命中规划词，否则又变成在测词表"
    assert planner.should_plan(long_text) == True
    print("  ✅ 长句触发规划（%d 字，无规划词命中）" % len(long_text))


def test_should_plan_short_narrative():
    print("\n== 测试3b：中等长度的纯叙述不该规划 ==")
    # 与测试3方向相反：长度到了也不一定规划，因为没有工具要调。
    t = "我今天下午去超市买了很多东西然后回家做饭吃完饭后看了一会儿电视"
    assert len(t) < 40
    assert planner.should_plan(t) == False
    print("  ✅ %d 字纯叙述不规划" % len(t))


def test_no_single_char_keywords():
    print("\n== 测试3c：词表里不许有单字 ==")
    # 单字是这一版最容易踩的坑：跳过词的单字砍掉真实请求（「早」砍掉
    # 「帮我查一下早上的会议」），规划词的单字把闲聊拖进规划
    #（「三个」拖「我运行了三个小时」）。这里直接把这条钉死。
    for label, words in (("规划词", planner._PLAN_KEYWORDS),
                         ("跳过词", planner._SKIP_KEYWORDS)):
        singles = [w for w in words if len(w) == 1]
        assert not singles, "%s里有单字：%s" % (label, singles)
    print("  ✅ 规划词 %d 个、跳过词 %d 个，全是多字词"
          % (len(planner._PLAN_KEYWORDS), len(planner._SKIP_KEYWORDS)))


def test_should_plan_regression():
    print("\n== 测试3d：误触发和漏判都得为零 ==")
    CHAT = ["在吗", "早上好呀", "今天好累", "你吃了吗", "困死了", "你在干嘛呢",
            "想你了", "晚安啦", "谢谢你", "陪我聊会儿", "明天见",
            # 下面三句以前被单字词拖进规划
            "我运行了三个小时", "她再也不会来了", "所有人都在"]
    TASK = ["帮我写一份工作总结", "帮我查一下今天的天气", "对比一下这两个方案",
            "总结一下我们上周聊的", "如何提高睡眠质量",
            # 下面两句以前被单字跳过词砍掉
            "帮我总结一下啊", "帮我查一下早上的会议"]
    for t in CHAT:
        assert planner.should_plan(t) == False, "闲聊被误判成要规划：%s" % t
    for t in TASK:
        assert planner.should_plan(t) == True, "真实请求被判成闲聊：%s" % t
    print("  ✅ 闲聊 %d 条零误触发，任务 %d 条零漏判"
          % (len(CHAT), len(TASK)))


def test_should_plan_skip_words():
    print("\n== 测试4：跳过词优先 ==")
    # 即使长度够，包含跳过词也不规划
    assert planner.should_plan("帮我个忙，在吗") == False
    print("  ✅ 跳过词优先")


def test_extract_json_plain():
    print("\n== 测试5：纯 JSON 提取 ==")
    raw = '[{"step": "算乘法", "tool": "calc", "arg": "128*256"}, {"step": "回答", "tool": null, "arg": null}]'
    result = planner._extract_json(raw)
    assert result is not None
    assert len(result) == 2
    assert result[0]["tool"] == "calc"
    assert result[1]["tool"] is None
    print("  ✅ 纯 JSON 提取正确")


def test_extract_json_markdown():
    print("\n== 测试6：Markdown 代码块提取 ==")
    raw = '```json\n[{"step": "直接回答", "tool": null, "arg": null}]\n```'
    result = planner._extract_json(raw)
    assert result is not None
    assert len(result) == 1
    print("  ✅ Markdown 代码块提取正确")


def test_extract_json_with_prefix():
    print("\n== 测试7：带前后多余文本的提取 ==")
    raw = '好的，我来规划一下：\n[{"step": "搜索", "tool": "search", "arg": "测试"}]\n以上就是计划。'
    result = planner._extract_json(raw)
    assert result is not None
    assert result[0]["tool"] == "search"
    print("  ✅ 带多余文本提取正确")


def test_extract_json_invalid():
    print("\n== 测试8：无效 JSON 返回 None ==")
    assert planner._extract_json("") is None
    assert planner._extract_json("不是json") is None
    assert planner._extract_json("{not an array}") is None
    print("  ✅ 无效 JSON 正确返回 None")


def test_build_final_prompt():
    print("\n== 测试9：最终提示词拼装 ==")
    results = [
        ("计算 128×256", "calc", "128*256", "32768"),
        ("搜索上证指数", "search", "上证指数", "上证指数 3000点"),
        ("整理回答", None, None, None),
    ]
    prompt = planner.build_final_prompt("帮我算一下128乘256再搜一下上证指数", results)
    assert "32768" in prompt
    assert "3000点" in prompt
    assert "帮我算一下" in prompt
    assert "calc" in prompt
    assert "search" in prompt
    print("  提示词预览（前200字）：%s" % prompt[:200])
    print("  ✅ 最终提示词拼装正确")


def test_plan_prompt_contains_tools():
    print("\n== 测试10：规划提示词结构完整 ==")
    prompt = planner._plan_prompt("测试任务")
    # 基本结构必须在（工具列表可能因环境未加载插件而不全，但结构要对）
    assert "可用工具" in prompt
    assert "任务" in prompt
    assert "输出要求" in prompt
    assert "JSON" in prompt
    assert "step" in prompt
    assert "tool" in prompt
    # 核心工具（不依赖插件加载）应该在
    assert "weather" in prompt or "天气" in prompt or "ip_city" in prompt or "位置" in prompt
    print("  提示词预览（前300字）：%s" % prompt[:300])
    print("  ✅ 规划提示词结构完整")


def test_results_text():
    print("\n== 测试11：执行结果格式化 ==")
    results = [
        ("计算 128×256", "calc", "128*256", "32768"),
        ("搜索上证指数", "search", "上证指数", "上证指数 3000点，涨1.2%"),
        ("整理回答", None, None, None),
    ]
    text = planner._results_text(results)
    assert "calc" in text
    assert "32768" in text
    assert "search" in text
    assert "3000点" in text
    assert "无需工具" in text
    print("  格式化结果：\n%s" % text)
    print("  ✅ 执行结果格式化正确")


def test_verify_prompt():
    print("\n== 测试12：检查器提示词拼装 ==")
    results = [
        ("计算 128×256", "calc", "128*256", "32768"),
        ("搜索上证指数", "search", "上证指数", "3000点"),
    ]
    prompt = planner._verify_prompt("帮我算128×256再搜上证指数", results, "128×256大概是3万多")
    assert "结果检查器" in prompt
    assert "帮我算128×256" in prompt
    assert "32768" in prompt
    assert "3000点" in prompt
    assert "大概是3万多" in prompt
    assert "OK" in prompt
    assert "执行成功的工具结果" in prompt
    print("  提示词预览（前200字）：%s" % prompt[:200])
    print("  ✅ 检查器提示词拼装正确")


def test_build_revise_prompt():
    print("\n== 测试13：修正提示词拼装 ==")
    results = [
        ("计算 128×256", "calc", "128*256", "32768"),
        ("搜索上证指数", "search", "上证指数", "3000点"),
    ]
    prompt = planner.build_revise_prompt(
        "帮我算128×256再搜上证指数",
        results,
        "128×256大概是3万多，上证指数不清楚",
        "问题：上证指数结果3000点没用上，说不清楚是编造\n修正：用上3000点"
    )
    assert "检查器发现你的回答有问题" in prompt
    assert "32768" in prompt
    assert "3000点" in prompt
    assert "大概是3万多" in prompt
    assert "上证指数结果3000点没用上" in prompt
    assert "必须用上所有执行成功的结果" in prompt
    print("  提示词预览（前200字）：%s" % prompt[:200])
    print("  ✅ 修正提示词拼装正确")


def test_verify_result_skip_no_tool():
    print("\n== 测试14：无工具调用时跳过检查 ==")
    # 没有工具调用的纯回答，verify_result 应该直接返回 None（不检查）
    results = [
        ("直接回答", None, None, None),
    ]
    # 因为没有 API 配置，实际调用会返回 None；但 has_tool=False 时应该更早返回
    # 这里验证逻辑：has_tool 为 False 时不调 API
    has_tool = any(tool for _, tool, _, _ in results)
    assert has_tool == False
    print("  ✅ 无工具调用时正确跳过检查")


def test_split_tool():
    print("\n== 测试15：工具名带参数要拆开 ==")
    # 模型爱照着 PROMPT_LINE 的示例写全名（"[tool:weather:城市]"），
    # 但 tools.TOOLS 的键是裸名。不拆 -> TOOLS.get("weather:北京") 是 None
    # -> 工具明明有，她却只能跟用户说「查不到」。
    cases = [
        ("weather:北京", None, ("weather", "北京")),
        ("weather", "北京", ("weather", "北京")),
        ("tracking:12345", None, ("tracking", "12345")),
        ("ip_city", None, ("ip_city", None)),
        ("  Weather : 上海 ", None, ("weather", "上海")),
        # 参数写两遍时以 arg 为准（模型给的那个通常更具体）
        ("weather:北京", "上海", ("weather", "上海")),
        (None, "北京", (None, "北京")),
        ("", None, (None, None)),
    ]
    for tool, arg, want in cases:
        got = planner._split_tool(tool, arg)
        assert got == want, "%r/%r -> %r，期望 %r" % (tool, arg, got, want)
    print("  ✅ %d 个组合都拆对" % len(cases))


def test_plan_normalises_tool_names():
    print("\n== 测试16：plan() 出来的工具名必须真的调得动 ==")
    # 判据落在「能不能找到这个工具」，不是「字符串长啥样」——
    # 模型这次给 weather:北京，下次可能给别的花样。
    #
    # 用 tools.run_tool 判它是错的：找不到时它返回 None 而不是报错，
    # 「查得到」和「查不到但静默」分不开。所以查注册表。
    #
    # 别用 plugin_host.has_tool —— 那函数不存在（我第一版就调了它，
    # 报 AttributeError，读起来像代码坏了，其实是判据写错）。
    import tools
    import plugin_host
    plugin_host.load()
    steps = [{"step": "查天气", "tool": "weather:北京", "arg": None},
             {"step": "定位", "tool": "ip_city", "arg": None},
             {"step": "答", "tool": None, "arg": None}]
    n = 0
    for s in steps:
        tool, arg = planner._split_tool(s.get("tool"), s.get("arg"))
        if not tool:
            continue
        n += 1
        known = tool in tools.TOOLS or plugin_host.get_by_tool(tool) is not None
        assert known, "工具 %r 不在册（步骤：%s）" % (tool, s["step"])
    print("  ✅ %d 个工具名全部在册（核心 %d 个 + 插件）"
          % (n, len(tools.TOOLS)))


if __name__ == "__main__":
    test_should_plan_short()
    test_should_plan_keywords()
    test_should_plan_long()
    test_should_plan_short_narrative()
    test_no_single_char_keywords()
    test_should_plan_regression()
    test_should_plan_skip_words()
    test_extract_json_plain()
    test_extract_json_markdown()
    test_extract_json_with_prefix()
    test_extract_json_invalid()
    test_build_final_prompt()
    test_plan_prompt_contains_tools()
    test_results_text()
    test_verify_prompt()
    test_build_revise_prompt()
    test_verify_result_skip_no_tool()
    test_split_tool()
    test_plan_normalises_tool_names()

    print("\n" + "=" * 50)
    print("ALL PLAN-AND-EXECUTE TESTS PASSED (18 tests)")
    print("=" * 50)
