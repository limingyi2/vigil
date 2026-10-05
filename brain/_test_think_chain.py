# -*- coding: utf-8 -*-
"""验证思维链修复：think 从 brain.last_think 读取，react.on_reply 正确落盘。

不启动服务器，只测核心逻辑：
1. mock brain.last_think
2. react.on_reply 从 ctx.brain.last_think 读 think
3. 回复里不含 think
4. data/react/ 下落盘记录的 think 字段正确
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 临时开启 react（config 里已经开了，但测试环境可能读不到，直接改模块级）
import plugins.react as react

# mock ctx：带 brain（有 last_think）、text、src
class MockBrain:
    def __init__(self, think):
        self.last_think = think

class MockCtx:
    def __init__(self, brain, text="测试用户输入", src="app"):
        self.brain = brain
        self.text = text
        self.src = src

def test_think_from_brain():
    print("== 测试1：think 从 brain.last_think 读取 ==")
    think_text = "他问北京天气，训练数据里的天气是旧的，必须调weather工具查实时数据"
    brain = MockBrain(think_text)
    ctx = MockCtx(brain, text="北京现在天气怎么样")

    # 回复文本里已经没有 think 了（模拟 _clean 之后的状态）
    reply = "北京现在晴，15度，挺舒服的"
    result = react.on_reply(ctx, reply)

    print("  输入回复：%r" % reply)
    print("  输出结果：%r" % result)
    assert result == reply, "回复应该原样返回（think 不在文本里，不需要剥）"
    assert "天气" not in result or "天气" in reply, "think 内容不能泄漏到回复里"
    print("  ✅ 回复未被污染，think 未泄漏")

def test_think_fallback_from_text():
    print("\n== 测试2：brain 没有 last_think 时，从文本兜底解析 ==")
    brain = MockBrain("")  # 空 think
    ctx = MockCtx(brain, text="测试")

    # 文本里带 think（模拟某些路径没走 brain.draft 的情况）
    reply_with_think = "<think>这是兜底思考</think>正常回复内容"
    result = react.on_reply(ctx, reply_with_think)

    print("  输入回复：%r" % reply_with_think)
    print("  输出结果：%r" % result)
    assert "这是兜底思考" not in result, "think 应该被剥掉"
    assert "正常回复内容" in result, "正文应该保留"
    print("  ✅ 兜底解析正常，think 被剥掉，正文保留")

def test_data_written():
    print("\n== 测试3：落盘验证 ==")
    think_text = "落盘测试：需要查上海天气"
    brain = MockBrain(think_text)
    ctx = MockCtx(brain, text="上海天气怎么样", src="test")

    reply = "上海现在20度"
    react.on_reply(ctx, reply)

    # 检查 data/react/ 下有没有当天的文件
    from paths import DATA_DIR
    react_dir = os.path.join(DATA_DIR, "react")
    day_file = os.path.join(react_dir, time.strftime("%Y-%m-%d") + ".jsonl")
    print("  落盘路径：%s" % day_file)
    assert os.path.exists(day_file), "思维链文件应该存在"

    # 读最后一行，验证内容
    with open(day_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    last = json.loads(lines[-1])
    print("  最后一条记录：")
    print("    user: %s" % last.get("user"))
    print("    think: %s" % last.get("think"))
    print("    reply: %s" % last.get("reply"))
    print("    src: %s" % last.get("src"))

    assert last["think"] == think_text, "think 字段应该等于 brain.last_think"
    assert last["src"] == "test", "src 应该正确"
    assert "上海天气" in last["user"], "user 字段应该正确"
    print("  ✅ 落盘内容正确")

def test_stats():
    print("\n== 测试4：统计接口 ==")
    s = react.stats()
    print("  turns: %d" % s["turns"])
    print("  parsed: %d" % s["parsed"])
    print("  parse_rate: %d%%" % s["parse_rate"])
    print("  think_chars: %d" % s["think_chars"])
    assert s["turns"] > 0, "应该有对话计数"
    assert s["parsed"] > 0, "应该有解析成功的计数"
    print("  ✅ 统计正常")

if __name__ == "__main__":
    # 确保 react 开关开着（直接读 config）
    cfg = react._cfg()
    print("react.enabled = %s" % cfg.get("enabled"))
    if not cfg.get("enabled"):
        print("⚠️  react 未开启，测试会跳过（请检查 config.json）")
        sys.exit(1)

    test_think_from_brain()
    test_think_fallback_from_text()
    test_data_written()
    test_stats()

    print("\n" + "=" * 50)
    print("ALL TESTS PASSED ✅")
    print("=" * 50)
