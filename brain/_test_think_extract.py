# -*- coding: utf-8 -*-
"""判据：brain.draft() 真的把 think 抠出来存进 self.last_think 了。

## 为什么已有的测试不够

brain/_test_think_chain.py 用 MockBrain 造了个带 last_think 的假大脑，
然后验 react.on_reply 能读到它。可它从没验过**谁往 last_think 里写的**——
而那正是原来的 bug：_clean() 先把 <think> 剥掉了，on_reply 拿到的是干净的
文本，parse_react 抠不出东西。

也就是说：把 brain.py 里新加的那段提取整个删掉，_test_think_chain.py 照样全绿。
一个测不出修复的测试等于没测。

所以这里直接抠 brain.py 里 draft() 的那段代码出来喂真的 API 输出格式。

## 双向

只测「现在能提取」没意义 —— 一个恒返回真值的函数也能通过。
所以还拿「不做提取、直接 _clean」当反例，证明判据看得出差别。
"""
import ast
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OK = 0
BAD = 0


def chk(cond, label):
    global OK, BAD
    if cond:
        OK += 1
        print("  OK   %s" % label)
    else:
        BAD += 1
        print("  BAD  %s" % label)


def extract_snippet():
    """从 brain.py 里抠出「提取 think 存进 last_think」的那几条语句。

    用 AST 定位再重新缩进，不用字符串切 —— 字符串切会把上一个 try 块的
    缩进一起带进来（抠出来直接 IndentationError）。
    按 AST 挑还有个好处：有人把提取挪到 _clean 之后，判据立刻失效，
    而那正是这个 bug 的形态。
    """
    src = io.open(os.path.join(HERE, "brain.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    draft = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "draft")

    # 候选：draft 里所有给 self.last_think 赋值、以及给 raw 赋值的语句
    picked = []
    for node in ast.walk(draft):
        if not isinstance(node, ast.Assign):
            continue
        tgt_attr = any(getattr(t, "attr", "") == "last_think"
                       for t in node.targets)
        tgt_raw = any(isinstance(t, ast.Name) and t.id == "raw"
                      for t in node.targets)
        if tgt_attr or tgt_raw:
            picked.append(node)
    if not any(any(getattr(t, "attr", "") == "last_think"
                   for t in n.targets) for n in picked):
        return None, "draft() 里没有给 self.last_think 赋值"

    first = min(n.lineno for n in picked)
    last = max(n.end_lineno for n in picked)
    lines = src.splitlines()
    body = "\n".join(lines[first - 1:last])

    # last_think 的第一次赋值必须早于 _clean(raw) 的调用
    clean_at = None
    for i in range(first - 1, len(lines)):
        if "_clean(raw" in lines[i]:
            clean_at = i + 1
            break
    assign_at = min(n.lineno for n in picked
                    if any(getattr(t, "attr", "") == "last_think"
                           for t in n.targets))
    if clean_at is not None and assign_at > clean_at:
        return None, ("提取 think 的代码在 _clean(raw) 之后 —— "
                      "行 %d vs %d，等于没提取" % (assign_at, clean_at))

    # 原文这些语句在 try 块里，整段切出来带着 20 空格缩进，直接 exec 报
    # unexpected indent。dedent 统一去掉公共前缀，剩下的相对缩进（if 的
    # 子句比 if 多 4 格）自然保留 —— 别手算空格数。
    import textwrap
    return textwrap.dedent(body), ""


def run(snippet, raw):
    """按 brain.py 那几行的原样跑一遍，返回 last_think。

    snippet 里的 raw = _j[...] 引用了 _j，所以给个假的 _j，形状跟
    httpx 拿到的一致（choices[0].message.content）。
    """
    j = {"choices": [{"message": {"content": raw}}]}
    self_obj = type("S", (), {})()
    ns = {"re": re, "raw": raw, "self": self_obj, "_j": j}
    exec(compile(snippet, "brain_snippet", "exec"), ns)
    return ns["self"].last_think


CASES = [
    ("标准", "<think>他在问北京天气，得查实时数据</think>北京今天晴，25℃",
     "他在问北京天气，得查实时数据"),
    ("think 里带换行", "<think>先想：\n1查天气\n2组织语言</think>晴",
     "先想：\n1查天气\n2组织语言"),
    ("只有开标签（被 max_tokens 截断）", "<think>还没想完就被砍断了",
     "还没想完就被砍断了"),
    ("没有 think", "今天天气不错", ""),
    ("空回复", "", ""),
]


def main():
    print("== 0. brain.py 里的提取代码在不在 ==")
    snippet, err = extract_snippet()
    if not snippet:
        chk(False, err)
        print("\nVERDICT FAIL")
        return 1
    chk(True, "找到了提取 think 的代码，且在 _clean(raw) 之前")
    print("  --- 抠出来的代码 ---")
    for l in snippet.splitlines():
        print("  | %s" % l.strip()[:76])

    print("\n== 1. 真跑一遍，抠出来对不对 ==")
    for label, raw, want in CASES:
        got = run(snippet, raw)
        chk(got == want, "%s -> %r" % (label, got[:40]))

    print("\n== 2. 反例：不提取直接清（原来的行为）==")
    # _clean 会把 think 删掉，所以「从清过的文本里再抠」只能得到空
    def _clean(s, keep_newlines=False):
        s = re.sub(r"<think>.*?</think>", "", s or "", flags=re.S)
        if "<think>" in s:
            s = s.split("<think>")[0]
        return s.replace("</think>", "").strip()

    raw = CASES[0][1]
    naive = re.search(r"<think>(.*?)</think>", _clean(raw), re.S)
    got = (naive.group(1).strip() if naive else "")
    chk(got == "", "从清过的文本里抠不到 think（这就是原 bug）")

    print("\n== 3. last_think 必须在 Brain.__init__ 里初始化 ==")
    src = io.open(os.path.join(HERE, "brain.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    init = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
    has_init = init is not None and "last_think" in ast.dump(init)
    chk(has_init, "__init__ 里有 last_think（否则第一轮读取会 AttributeError）")

    print("\n== 4. react.on_reply 必须优先读 brain.last_think ==")
    rsrc = io.open(os.path.join(HERE, "plugins", "react.py"),
                   encoding="utf-8").read()
    chk("last_think" in rsrc, "react.py 读 last_think")
    # 顺序：先 brain 后 parse_react，且 brain 拿到时不再无条件覆盖
    i_brain = rsrc.find("last_think")
    i_parse = rsrc.find("parse_react(reply)")
    chk(0 < i_brain < i_parse,
        "先读 last_think 再 parse_react（顺序反了兜底会盖掉真数据）")

    print("\n== VERDICT ==")
    print("  OK=%d  BAD=%d" % (OK, BAD))
    print("  -> %s" % ("PASS" if BAD == 0 else "FAIL"))
    return 0 if BAD == 0 else 1


if __name__ == "__main__":
    sys.exit(main())