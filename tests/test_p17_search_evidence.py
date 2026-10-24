"""Search results must carry the evidence an answer needs — and nothing more.

The regression scenario: "我在阿里云领了300的学生券，你推荐我买哪个服务器？"
The failure mode is not a missing answer, it is a *confident* one: the model
recites a price from training data, treats a student coupon as an
unconditional discount, and links a purchase page nobody verified.

These tests cover the parts of that which live in code: what a result carries
(sources, retrieval time, truncation), that repeated identical searches do not
hit the network twice, and that a failed search never comes back looking like
data the model can quote.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "brain"))

import plugins.websearch as websearch  # noqa: E402


def setup_function(_func):
    websearch._mem.clear()


def _result(title, url, body, **extra):
    item = {"title": title, "url": url, "snippet": body}
    item.update(extra)
    return item


def test_a_failed_search_says_so_and_hands_back_no_numbers(monkeypatch):
    monkeypatch.setattr(websearch, "_post", lambda q, o: None)
    text = websearch._t_search("阿里云 300 学生券 能买哪个服务器")
    assert "搜不了" in text
    assert "未能核实" in text
    assert "不要把训练数据" in text
    # 没有结果就没有任何可被当成报价的东西
    assert "元" not in text
    assert "http" not in text


def test_an_empty_result_is_never_dressed_up_as_a_price(monkeypatch):
    monkeypatch.setattr(websearch, "_post", lambda q, o: [])
    text = websearch._t_search("阿里云 学生券 套餐")
    assert "没搜到" in text
    assert "元" not in text
    assert "http" not in text


def test_every_result_carries_a_source_and_a_retrieval_time(monkeypatch):
    monkeypatch.setattr(websearch, "_post", lambda q, o: [
        _result("云服务器 ECS 计费说明", "https://www.aliyun.com/price/ecs",
                "按量付费，实例规格不同价格不同。", date="2026-10-01"),
    ])
    text = websearch._t_search("阿里云 ECS 价格")
    assert "检索时间" in text, "没有检索时间，模型无法判断这条是不是已经过时"
    assert "来源：https://www.aliyun.com/price/ecs" in text
    assert "发布 2026-10-01" in text


def test_the_same_page_indexed_twice_only_counts_once(monkeypatch):
    monkeypatch.setattr(websearch, "_post", lambda q, o: [
        _result("ECS 计费", "https://www.aliyun.com/price/ecs", "价格 A"),
        _result("ECS 计费（镜像页）", "https://www.aliyun.com/price/ecs#tab=2", "价格 A"),
        _result("ECS 计费（带参）", "https://www.aliyun.com/price/ecs?from=home", "价格 A"),
        _result("学生优惠活动", "https://www.aliyun.com/student", "学生认证后可领"),
    ])
    text = websearch._t_search("阿里云 ECS 价格")
    assert "搜到 2 条" in text
    assert text.count("https://www.aliyun.com/price/ecs") == 1


def test_a_truncated_snippet_is_marked_as_truncated(monkeypatch):
    long_body = "活动规则：" + ("满减与叠加条件说明。" * 80)
    monkeypatch.setattr(websearch, "_post", lambda q, o: [
        _result("学生优惠活动规则", "https://www.aliyun.com/student", long_body),
    ])
    text = websearch._t_search("阿里云 学生优惠 规则")
    assert "摘要已截断" in text, "截断不留痕，模型会把半句话当成完整条件"


def test_asking_the_same_question_twice_does_not_search_twice(monkeypatch):
    calls = []

    def fake_post(q, o):
        calls.append(q)
        return [_result("ECS 计费", "https://www.aliyun.com/price/ecs", "按量付费")]

    monkeypatch.setattr(websearch, "_post", fake_post)
    first = websearch._t_search("阿里云 ECS 价格")
    second = websearch._t_search("阿里云 ECS 价格")
    assert first == second
    assert len(calls) == 1, "重复搜同一句是白花钱，还会让回答在两条不一致的数据上摇摆"


def test_a_failed_search_is_only_briefly_cached(monkeypatch):
    """网络抖动不该被缓存成半小时的"查不到"。"""
    calls = []

    def flaky(q, o):
        calls.append(q)
        return None if len(calls) == 1 else [
            _result("ECS 计费", "https://www.aliyun.com/price/ecs", "按量付费")]

    monkeypatch.setattr(websearch, "_post", flaky)
    assert "搜不了" in websearch._t_search("阿里云 ECS 价格")
    expire, _text = websearch._mem[next(iter(websearch._mem))][:2]
    import time
    assert expire - time.time() <= 61
