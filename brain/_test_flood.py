# -*- coding: utf-8 -*-
"""刷屏抑制的判据测试。

测的是"该拦的拦住了、该放行的放行了"，不是测函数返回值好看。
每个 case 写清**为什么**是这个期望 —— 判错的后果不对称：
误拦（该说不说）比漏拦（刷屏）严重得多，所以期望值偏保守。
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"F:\Vigil\brain")
sys.path.insert(0, r"F:\Vigil\brain\plugins")   # gate.py 在 plugins/ 下

import gate


class FakeBrain(object):
    def __init__(self, msgs, log_path=None):
        self.recent_msgs = msgs
        self.persona_key = "preset_group_maibot"
        self._p = log_path

    def _log_for(self, src):
        return self._p


class FakeCtx(object):
    """字段与 plugin_host.Turn 对齐：只给真实存在的。"""

    def __init__(self, msgs, log_path=None, src="wechat"):
        self.text = "哈哈哈笑死"
        self.src = src
        self.api_config = {}
        self.brain = FakeBrain(msgs, log_path)
        self.meta = {}


def m(role, body):
    return {"role": role, "content": body}


def write_log(path, rows):
    with io.open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


OK = []


def check(name, got, want):
    passed = (got == want)
    OK.append(passed)
    print("%s %-46s got=%-6s want=%s" % ("PASS" if passed else "FAIL",
                                        name, repr(got), repr(want)))


tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "_flood_log.jsonl")

# --- 1 自己占比计算 ---
check("全是他说的-> 占比 0",
      gate._self_share(FakeCtx([m("user", "a"), m("user", "b")])), 0.0)
check("一半自己-> 占比 0.5",
      gate._self_share(FakeCtx([m("user", "a"), m("assistant", "b")])), 0.5)
check("全是自己-> 占比 1.0",
      gate._self_share(FakeCtx([m("assistant", "a"), m("assistant", "b")])), 1.0)
check("没有历史 -> 占比 0",
      gate._self_share(FakeCtx([])), 0.0)

# --- 2 刷屏抑制：占比高 + 刚说完 = 拦 ---
write_log(tmp, [{"role": "assistant", "content": "x", "ts": time.time() - 5}])
check("刚说完5秒、自己占一半 -> 拦",
      bool(gate._flood_guard(FakeCtx(
          [m("assistant", "a"), m("user", "b")], tmp))), True)

# --- 3 关键：被 @ 的必须无条件放行（这是最不能错的一条）---
write_log(tmp, [{"role": "assistant", "content": "x", "ts": time.time() - 3}])
c = FakeCtx([m("assistant", "a"), m("assistant", "b")], tmp)
c.text = "@麦麦 在吗"
check("被@了就算刚说过也不拦（走 _called_by_name）",
      gate._called_by_name(c, c.text), True)

# --- 4 隔了很久才说 + 只是偶尔说 -> 不拦 ---
write_log(tmp, [{"role": "assistant", "content": "x",
                 "ts": time.time() - 1800}])
check("半小时前说的、自己占三成 -> 不拦",
      bool(gate._flood_guard(FakeCtx(
          [m("assistant", "a"), m("user", "b"), m("user", "c")], tmp))), False)

# --- 5 连着说很久 -> 还是该拦 ---
write_log(tmp, [{"role": "assistant", "content": "x",
                 "ts": time.time() - 60}])
check("一分钟前说的、自己占七成 -> 拦",
      bool(gate._flood_guard(FakeCtx(
          [m("assistant", "a"), m("assistant", "b"), m("assistant", "c")],
          tmp))), True)

# --- 6 读不到存档时必须放行，不能因为数据缺失就哑掉 ---
check("存档不存在 -> 不拦（降级放行）",
      bool(gate._flood_guard(FakeCtx(
          [m("assistant", "a"), m("user", "b")], "C:/no/such/file.jsonl"))),
      False)

# --- 7 群里全是别人在聊 -> 永远不拦 ---
write_log(tmp, [{"role": "user", "content": "x", "ts": time.time() - 1}])
check("最后一条是别人说的 + 自己占一半 -> 不拦",
      bool(gate._flood_guard(FakeCtx(
          [m("assistant", "a"), m("user", "b")], tmp))), False)

# --- 8 关掉开关 -> 一定放行 ---
saved = gate._cfg
gate._cfg = lambda: {"flood": {"enabled": False}}
write_log(tmp, [{"role": "assistant", "content": "x", "ts": time.time() - 2}])
check("enabled=False -> 不拦",
      bool(gate._flood_guard(FakeCtx(
          [m("assistant", "a"), m("assistant", "b")], tmp))), False)
gate._cfg = saved

if os.path.exists(tmp):
    os.remove(tmp)

print("")
print("通过 %d / %d" % (sum(1 for x in OK if x), len(OK)))
sys.exit(0 if all(OK) else 1)
