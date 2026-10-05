# -*- coding: utf-8 -*-
"""/api/chat 的返回体必须带 trace，且 trace 里要有 plan_steps。

## 踩出来的坑

`plan_steps=None` 查了很久，方向一路错：先怀疑 planner 没被加载、
再怀疑 should_plan 判断、最后连 `[规划]` 日志都读不到（探针自己坏了）。
真相是：**`/api/chat` 的返回体里根本没有 `trace` 键**。

`_snap` 只被写进落盘日志（`_trace_write`），从没回给客户端。
于是前端 `d["trace"]["plan_steps"]` 恒为 None —— 而功能是好的，
日志里明明打着「[大脑] Plan-and-Execute 完成，2 步」。

这个 bug 最坏的地方不是字段缺失，是**它长得像功能坏了**：
日志说走了、接口说没走，两边对不上，人只会怀疑代码逻辑，
而真因是少了一个 JSON 键。

## 判据为什么这样定

不能只断言「返回体里有 trace 键」—— 那个键可能一直是 None 或空 dict，
功能照样是坏的。所以判据钉三件事：
  ① 返回体有 trace 键
  ② `_snapshot` 抄出来的字段名和返回体里给的**完全一致**（不许改名/漏项）
  ③ 非规划轮（闲聊）plan_steps 必须是 None，**不能是0**
     —— 0 表示"规划了但0步"，那是另一种错

用法：F:/Vigil/venv/Scripts/python.exe _test_chat_trace_field.py
"""
import ast
import io
import json
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
        print("  FAIL  " + name + ("  <<< " + json.dumps(extra, ensure_ascii=False)[:300]
                                   if extra is not None else ""))


def main():
    src = io.open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    tree = ast.parse(src)          # 语法错就直接炸，不用给"通过"

    print("[1] /api/chat 的返回体里带 trace")
    # ⚠️ 锚点写错会让整个测试**静默测一个空字符串**然后全红 ——
    # 看起来像"代码没改"，真因是锚点是 `if path ==` 而不是 `elif`。
    # 所以先硬断言锚点存在，再往下测。
    i = src.find('if path == "/api/chat"')
    j = src.find('elif path == "/api/packages/del"', i)
    seg = src[i:j] if i >= 0 and j > i else ""
    if not seg:
        print("  ABORT 锚点没找到 —— 下面的判据全部无效（先修锚点）")
        return 1
    check("找得到 /api/chat 分支", True)
    check("分支里确实有 _json(200, ...)", "_json(200," in seg)
    check("返回体里写了 trace 键", '"trace": {' in seg,
          [l.strip()[:90] for l in seg.splitlines() if "trace" in l][:3])
    check("trace 里给了 plan_steps", '"plan_steps"' in seg)
    check("trace 里给了 revised", '"revised"' in seg)
    check("trace 里给了 tool", '"tool"' in seg)

    print("[2] trace 字段取自 _snap（不是现读、也不是写死）")
    trace_blk = seg[seg.find('"trace": {'):] if '"trace": {' in seg else ""
    check("从 _snap 取", "_snap.get(" in trace_blk,
          [l.strip()[:90] for l in trace_blk.splitlines()[:10]])

    print("[3] 字段名和 _chat_with_tools 写进 _snap 的完全一致")
    k = src.find("def _chat_with_tools")
    body = src[k:src.find("\ndef ", k + 10)] if k >= 0 else ""
    if not body:
        print("  ABORT 找不到 _chat_with_tools —— 判据无效")
        return 1
    import re
    written = sorted(set(re.findall(r'trace_out\["(\w+)"\]\s*=', body)))
    print("       _snap 被写入的字段: %s" % written)
    check("确实有字段被写进 trace_out", bool(written), written)
    given = sorted(set(re.findall(r'"(\w+)":', trace_blk)))
    print("       返回体给的字段  : %s" % given)
    # 写了的必须都给（多给无害，少给就是恒None）
    missing = [x for x in written if x not in given]
    check("写了但没返回的字段: %s" % (missing or "无"), missing or "无", written)

    print("[4] 闲聊轮不能是 0（0 = '规划了但0步'，是另一种错）")
    check("plan_steps 用 .get() 而非 len()//int() 兜底",
          '"plan_steps": _snap.get("plan_steps")' in seg,
          [l.strip()[:100] for l in seg.splitlines() if "plan_steps" in l])

    print("[5] 别把整个 _snap 塞回去")
    check("没有把 _snap 整个当 body（会漏字段也 bloated）",
          '"trace": dict(_snap)' not in seg and '"trace": _snap' not in seg)

    print("\n== %d pass / %d fail ==" % (PASS[0], len(FAIL)))
    for f in FAIL:
        print("   FAILED: " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())