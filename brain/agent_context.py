# -*- coding: utf-8 -*-
"""Shared, tokenizer-independent context and token accounting primitives.

Both ordinary conversations and long-running Agent tasks use these helpers.
They deliberately keep provider-reported usage separate from estimates.
"""
import copy


MSG_OVERHEAD = 4


def estimate_tokens(text):
    """Conservative heuristic; never treats bytes or characters as tokens."""
    if not text:
        return 0
    text = str(text)
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return int(cjk * 0.9 + (len(text) - cjk) * 0.3) + 1


def estimate_messages(messages):
    return sum(estimate_tokens(m.get("content") or "") + MSG_OVERHEAD
               for m in messages if isinstance(m, dict))


def _usage_count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def usage_record(usage, input_text="", output_text=""):
    """Normalize common usage fields while retaining an explicit estimate flag."""
    usage = usage if isinstance(usage, dict) else {}
    in_count = _usage_count(usage.get("prompt_tokens", usage.get("input_tokens")))
    out_count = _usage_count(usage.get("completion_tokens", usage.get("output_tokens")))
    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    cached = _usage_count(prompt_details.get("cached_tokens")) if isinstance(prompt_details, dict) else None
    reasoning = _usage_count(completion_details.get("reasoning_tokens")) if isinstance(completion_details, dict) else None
    estimated = in_count is None or out_count is None
    return {
        "input_tokens": in_count if in_count is not None else estimate_tokens(input_text),
        "output_tokens": out_count if out_count is not None else estimate_tokens(output_text),
        "cached_tokens": cached,
        "reasoning_tokens": reasoning,
        "estimated": estimated,
        "source": "provider" if not estimated else "estimate",
    }


def compact_messages(messages, budget_tokens, keep_recent=8, stub_chars=1200):
    """Shrink reacquirable old tool output first; then old assistant prose.

    Message and tool-call structure is retained. A copied model-visible history
    is returned so durable/raw history remains untouched.
    """
    out = copy.deepcopy(messages)
    if not isinstance(budget_tokens, int) or budget_tokens <= 0:
        return out, {"before_tokens": estimate_messages(out),
                     "after_tokens": estimate_messages(out), "saved_tokens": 0,
                     "omitted_evidence": []}
    before = estimate_messages(out)
    if before <= budget_tokens or len(out) <= keep_recent + 2:
        return out, {"before_tokens": before, "after_tokens": before,
                     "saved_tokens": 0, "omitted_evidence": []}
    cut = max(1, len(out) - max(1, keep_recent))
    protected = {0}
    for i, msg in enumerate(out):
        if msg.get("role") == "user":
            protected.add(i)
            break
    omitted = []

    def shrink(i, cap):
        content = out[i].get("content")
        if not isinstance(content, str) or len(content) <= cap:
            return False
        removed = len(content) - cap
        note = "\n…[中间省略 %d 字符；需要时可重新读取原工具结果]…\n" % removed
        room = max(0, cap - len(note))
        head = int(room * 0.7)
        out[i]["content"] = content[:head] + note + content[-(room - head):]
        omitted.append({"index": i, "role": out[i].get("role"), "chars": removed})
        return True

    for roles, cap in (({"tool"}, stub_chars), ({"assistant"}, max(200, stub_chars // 2)),
                       ({"tool"}, max(200, stub_chars // 2))):
        for i, msg in enumerate(out):
            if i >= cut or i in protected or msg.get("role") not in roles:
                continue
            shrink(i, cap)
            if estimate_messages(out) <= budget_tokens:
                break
        if estimate_messages(out) <= budget_tokens:
            break
    after = estimate_messages(out)
    return out, {"before_tokens": before, "after_tokens": after,
                 "saved_tokens": max(0, before - after), "omitted_evidence": omitted}
