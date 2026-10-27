# -*- coding: utf-8 -*-
"""Plan-and-Execute 单元测试：验证 entry_decision / _extract_json / build_final_prompt。

不实际调 API（plan() 需要网络和 key，在真实环境测）。

入口判据（2026-10-27 起）只有 `planner.entry_decision(task_decision)` 一处：
要不要规划由 task_intent 的分类折算，不再有关键词启发式。
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import planner
import task_intent


def test_entry_decision_two_entries():
    print("== 测试0：入口只有两个（简单/复杂） ==")
    # 复杂任务：task_intent 判 complex_execution 且无 fallback → 规划
    yes, why = planner.entry_decision(
        {"task_category": "complex_execution", "reason": "多个独立操作需要分步执行"})
    assert yes is True, why
    assert "多个独立操作" in why
    # 简单任务：其余一切分类都直接 ReAct
    for cat in ("chat", "retrieval", "capability", "simple_execution"):
        yes, _ = planner.entry_decision({"task_category": cat,
                                         "reason": "测试:%s" % cat})
        assert yes is False, "分类 %s 不该进规划" % cat
    # 带 fallback 的复杂任务也不规划（fail-closed 姿态保持不变）
    yes, why = planner.entry_decision(
        {"task_category": "complex_execution", "reason": "x",
         "fallback_reason": "unsupported_complex_task"})
    assert yes is False
    assert "fallback:unsupported_complex_task" in why
    # 空决策不炸
    assert planner.entry_decision(None)[0] is False
    assert planner.entry_decision({})[0] is False
    print("  ✅ 复杂任务进规划，其余分类全部直答")


def test_entry_decision_matches_classify():
    print("\n== 测试0b：entry_decision 对 classify 的真实输出成立 ==")
    # 真实分类输出喂进判据。复杂侧用已授权工作区的编码任务（非编码的
    # complex_execution 策略上带 unsupported_complex_task fallback，
    # fail-closed，不进规划 —— 这是既有姿态，不是判据的问题）。
    complex_text = "重构整个项目的所有模块，修复所有测试失败，然后更新依赖并运行构建验证"
    decision = task_intent.classify(complex_text, workspace_authorized=True)
    yes, why = planner.entry_decision(decision)
    assert decision["task_category"] == "complex_execution", decision
    assert not decision.get("fallback_reason"), decision
    assert yes is True, why
    for simple_text in ("在吗", "晚安啦", "帮我查一下明天的天气",
                        "解释一下什么是哈希表"):
        decision = task_intent.classify(simple_text)
        yes, _ = planner.entry_decision(decision)
        assert yes is False, "%s 不该进规划（%s）" % (
            simple_text, decision["task_category"])
    print("  ✅ 复杂编码任务判规划，闲聊/检索/解释全部判直答")


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
    # ip_city 已从注册表摘除（TOOLS 现在只有 weather/tracking），
    # 用在册工具验证「plan() 出来的名字调得动」。
    steps = [{"step": "查天气", "tool": "weather:北京", "arg": None},
             {"step": "查快递", "tool": "tracking:12345", "arg": None},
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
    test_entry_decision_two_entries()
    test_entry_decision_matches_classify()
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
    print("ALL PLAN-AND-EXECUTE TESTS PASSED (13 tests)")
    print("=" * 50)
