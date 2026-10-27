# -*- coding: utf-8 -*-
"""判据：规划路径不能把系统提示当用户的话留在历史和当日流水里。

## 为什么这条判据要用「真实的假 brain」而不是 mock

撤留逻辑读的是 brain.hist 和 life 的当日流水文件，两者的形状都得对上。
用 Mock 对象只能验「函数没崩」，验不出「真的撤干净了」——所以这里做一个
够用的假 brain：hist 是真列表，life 是真 life_engine（写临时目录），
然后调真的 server._restore_hist。

## 双向验证

只测「修复版通过」没有意义 —— 一个什么都不做的函数也能通过。
所以还跑一遍旧做法（只改最后一条 user），拿它当反例。
"""
import datetime
import importlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import planner  # noqa: E402

MARK = "（系统提示：你已经按计划完成了以下步骤"
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


class FakeLife(object):
    """life_engine 的最小替身：只提供 log_chat 和 _chat_path。"""

    def __init__(self, root):
        self.dir = root

    def _chat_path(self, day):
        return os.path.join(self.dir, "chat_%s.jsonl" % day)

    def log_chat(self, role, text):
        text = (text or "").strip()
        if not text:
            return
        with open(self._chat_path(datetime.date.today().isoformat()),
                  "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": "12:00", "role": role, "text": text},
                               ensure_ascii=False) + "\n")

    def today_chat(self):
        out = []
        p = self._chat_path(datetime.date.today().isoformat())
        if not os.path.isfile(p):
            return out
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
        return out


class FakeBrain(object):
    def __init__(self, life):
        self.hist = [{"role": "system", "content": "系统"}]
        self.life = life

    def _stamp(self):
        return datetime.datetime.now().strftime("[%m-%d %H:%M] ")

    def chat(self, text, log=True):
        """复刻 brain.chat 里那段：每调一次就 hist 和流水各加一条。"""
        self.hist.append({"role": "user",
                          "content": self._stamp() + text})
        self.hist.append({"role": "assistant",
                          "content": self._stamp() + "（她的回答）"})
        self.life.log_chat("user", text)
        self.life.log_chat("assistant", "（她的回答）")
        return "（她的回答）", "API"


def scenario_two_turns(brain, chatfn, text, results):
    """走一遍规划路径：final_prompt 一次 + 修正 prompt 一次。"""
    brain.chat(text)                      # 用户那轮本来就有的
    chatfn(planner.build_final_prompt(text, results))
    chatfn(planner.build_revise_prompt(text, results, "（她的回答）",
                                       ["漏用了结果"]))


RESULTS = [("查天气", "weather", "北京", "晴 25℃"),
           ("整理回答", None, None, None)]
TEXT = "帮我查一下北京天气"


def build_env():
    root = tempfile.mkdtemp(prefix="zxhist_")
    life = FakeLife(root)
    brain = FakeBrain(life)
    # 前面留一条真实的旧对话，验证不会被误删
    brain.hist.append({"role": "user",
                       "content": brain._stamp() + "在吗"})
    brain.hist.append({"role": "assistant",
                       "content": brain._stamp() + "在"})
    life.log_chat("user", "在吗")
    life.log_chat("assistant", "在")
    return root, brain, life


def load_server_funcs(names, consts=()):
    """只把 server.py 里指定的顶层定义抠出来执行，不 import 整个模块。

    import server 会连着 import brain -> httpx，本机这个解释器没装。
    判据只需要那几个纯函数（不碰模块级状态），所以按 AST 切出来 exec 就够，
    也顺带保证测的是文件里那份真代码，不是我另抄一遍的。
    连带把 _PLAN_PROMPT_MARK 一起切出来 —— 函数体在 exec 时就要能解析到它。
    """
    import ast
    src = io.open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    keep, found = [], set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            keep.append(node)
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            tgt = [t.id for t in node.targets
                   if isinstance(t, ast.Name) and t.id in consts]
            if tgt:
                keep.append(node)
                found.update(tgt)
    missing = set(names) - found
    assert not missing, "server.py 里没有这些函数：%s" % missing
    ns = {"os": os, "datetime": datetime, "print": print,
          "threading": threading}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "server.py", "exec"), ns)
    return ns


def leaks(brain, life):
    """历史和流水里还剩几条系统提示。"""
    h = sum(1 for m in brain.hist if MARK in str(m.get("content") or ""))
    f = sum(1 for r in life.today_chat() if MARK in str(r.get("text") or ""))
    return h, f


def old_restore(brain, text, ans):
    """修复前的做法：只把最后一条 user 改成原话。"""
    import re
    hist = brain.hist
    for i in range(len(hist) - 1, -1, -1):
        if hist[i].get("role") == "user":
            old = hist[i].get("content", "")
            m = re.match(r"^(\[\d{1,2}-\d{1,2} \d{1,2}:\d{2}\] )", old)
            pre = m.group(1) if m else ""
            hist[i]["content"] = pre + text
            break


def main():
    ns = load_server_funcs(["_restore_hist", "_drop_turn_log",
                            "_chat_with_tools"],
                           consts=["_PLAN_PROMPT_MARK"])
    restore = ns["_restore_hist"]
    chk("_PLAN_PROMPT_MARK" in ns, "_PLAN_PROMPT_MARK 定义在 server.py 里")
    mark = ns.get("_PLAN_PROMPT_MARK")
    chk(mark and mark in planner.build_final_prompt(TEXT, RESULTS),
        "final_prompt 带得上标记")
    chk(mark and mark in planner.build_revise_prompt(
        TEXT, RESULTS, "答", ["x"]),
        "revise_prompt 带得上标记（修复前它开头是另一句话，撤不掉）")

    print("\n== 1. 修复版：两轮规划之后撤干净 ==")
    root, brain, life = build_env()
    try:
        scenario_two_turns(brain, brain.chat, TEXT, RESULTS)
        before_h, before_f = leaks(brain, life)
        chk(before_h > 0, "撤之前历史里确实有系统提示（%d 条）" % before_h)
        chk(before_f > 0, "撤之前流水里确实有系统提示（%d 条）" % before_f)
        restore(brain, TEXT, "（她的回答）")
        after_h, after_f = leaks(brain, life)
        chk(after_h == 0, "历史里系统提示清零（剩 %d）" % after_h)
        chk(after_f == 0, "流水里系统提示清零（剩 %d）" % after_f)
        texts = [str(m.get("content") or "") for m in brain.hist
                 if m.get("role") == "user"]
        chk(any(t.endswith(TEXT) for t in texts),
            "用户的原话回到了历史里")
        chk(any(t.endswith("在吗") for t in texts),
            "上一轮真实的对话没被误删")
        rows = [r for r in life.today_chat() if r.get("role") == "user"]
        chk(any(str(r.get("text") or "").endswith(TEXT) for r in rows),
            "流水里也记了原话")
        chk(any(str(r.get("text") or "").endswith("在吗") for r in rows),
            "流水里上一轮没被误删")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n== 2. 反例：修复前那种做法撤不干净 ==")
    root, brain, life = build_env()
    try:
        scenario_two_turns(brain, brain.chat, TEXT, RESULTS)
        old_restore(brain, TEXT, "（她的回答）")
        h, f = leaks(brain, life)
        chk(h > 0,
            "旧做法在历史里留下 %d 条系统提示（所以判据有鉴别力）" % h)
        chk(f > 0,
            "旧做法在流水里留下 %d 条系统提示" % f)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n== 3. 只走一轮规划（没触发修正）==")
    root, brain, life = build_env()
    try:
        brain.chat(TEXT)
        brain.chat(planner.build_final_prompt(TEXT, RESULTS))
        restore(brain, TEXT, "（她的回答）")
        h, f = leaks(brain, life)
        chk(h == 0 and f == 0, "单轮也撤干净（hist %d / log %d）" % (h, f))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n== 4. planner 分支必须清扫工具标签 ==")
    # 用 AST 定位那段分支，别按注释文字切。
    # 原先切的是「Plan-and-Execute：」这句注释 —— 注释一改判据就失效，
    # 而判据失效是那种「跑出来全绿但其实什么都没测」的最坏情况。
    import ast as _a
    stree = _a.parse(io.open(os.path.join(HERE, "server.py"),
                             encoding="utf-8").read())
    fn = next(n for n in _a.walk(stree)
              if isinstance(n, _a.FunctionDef)
              and n.name == "_chat_with_tools")
    body = _a.dump(fn)
    chk("TOOL_TAG_ANY_RE" in body,
        "规划分支里有清扫（不然 [tool:x] 会直接发给用户）")
    # 规划失败（入口判据假阳性、plan 返回空、执行抛异常）必须落到一条
    # 能答的路径上。原先断言的是 tools.run_chat_with_tools（外层标签 ReAct），
    # 现在那条路已经并进 brain.draft_native，这里判的是 _once 直答。
    #
    # 注意： 别按字符串切（"except Exception as e" 在 ast.dump 里根本不是这句，
    # rfind 返回 -1，tail 变成末尾一个字符，判据恒红）。按 AST 结构取：
    # 最后一个 Try 的 handler 之后那些语句，就是兜底那段。
    stmts = fn.body
    last_try = None
    for st in stmts:
        if isinstance(st, _a.Try):
            last_try = st
    chk(last_try is not None, "规划分支包在 try 里（异常时才走得到兜底）")
    tail_stmts = []
    if last_try is not None:
        after = stmts[stmts.index(last_try) + 1:]
        tail_stmts = [_a.dump(s) for s in after]
    tail = " ".join(tail_stmts)
    chk("_once" in tail and "TOOL_TAG_ANY_RE" in tail,
        "规划异常之后还有一条能直接答的兜底（且同样清扫标签）")

    print("\n== 5. planner.py 的 print 里不许有 emoji ==")
    # 云端控制台 GBK，print emoji 抛 UnicodeEncodeError；检查器那行在 try 之外，
    # 每次判定通过都炸一次。
    #
    # 只查字符串字面量，别扫全文：注释里写「别用 ✅」是安全的，注释不参与
    # 编码。第一版扫全文，把那条注释当成违规，判据自己假失败了。
    import ast as _ast
    ptree = _ast.parse(io.open(os.path.join(HERE, "planner.py"),
                               encoding="utf-8").read())
    seen = set()
    for node in _ast.walk(ptree):
        if not isinstance(node, _ast.Constant):
            continue
        if not isinstance(node.value, str):
            continue
        for ch in node.value:
            try:
                ch.encode("gbk")       # CJK/全角标点 GBK 都有，不算
            except UnicodeEncodeError:
                seen.add(ch)
    chk(not seen, "字符串字面量里没有 GBK 编不了的字符（发现 %s）"
        % ("".join(sorted(seen))[:30] or "无"))

    print("\n== VERDICT ==")
    print("  OK=%d  BAD=%d" % (OK, BAD))
    print("  -> %s" % ("PASS" if BAD == 0 else "FAIL"))
    return 0 if BAD == 0 else 1


if __name__ == "__main__":
    sys.exit(main())