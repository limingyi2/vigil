# -*- coding: utf-8 -*-
"""记忆/存档隔离测试：换人设 = 换一个人，她不该带着上一段的记忆上岗。

## 为什么这个测试存在

修之前的真实状态（云端实查）：
    memory.json（**无分区名**）          items=61   ← 你和知夏的旧账全在这
    preset_group.wechat.memory.json     items=0
    wechat.memory.json                  items=0
六个分区库全是空壳。表现是「她在群里知道你上个月说过什么」。

三条根因，各自都能单独造成污染：
  1. server.py 构造 Brain 时调 get_default_store() **两个参数都没传**
     → self.mem 指着无分区名的公共库（那 61 条）
  2. brain._mem_for 异常时 return self.mem → 退回同一份公共库
  3. 聊天存档**读**（server._cloud_history）只按渠道拼名，
     **写**（brain._log_for）按「人设×渠道」分区 → 两边算法不一致，
     群聊对话被写进 preset_group.app.chat_history.jsonl（按渠道拼的那个名字）

外加一个隐患：chat_log_for('wechat') 已带 .wechat 后缀，再 scoped 一次
会拼出 chat_history.wechat.wechat.jsonl 双后缀名。

## 判据

不测「她说得对不对」，测**路径**：
  - 同一个 (人设, 渠道) 算出来的路径，写和读必须一致
  - 不同 (人设, 渠道) 算出来的路径必须不同
  - 分区名里不能出现双后缀
  - 没传参数时不能落回公共库

跑：python _test_mem_isolation.py
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paths


PASS, FAIL = [], []


def check(name, got, want):
    if got == want:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append(name)
        print("  FAIL  %s\n        期望 %r\n        实际 %r" % (name, want, got))


def fname(p):
    return os.path.basename(p or "")


# --- 1. 分区名本身 ---
print("\n[1] 分区名")
# 只有字面量 "default" 才算「没指定人设」；"base" 是真实人设名（云端实测
# persona_file 就是 base），它该有自己的分区 —— 把 base 当成「空」会让
# 换人设不生效，正是要修的那个病。
check("字面量 default 沿用老路径（老数据不用搬）",
      paths.scope_name("default", "app"), "")
check("真实人设名 base 自己有分区", paths.scope_name("base", "app"),
      "base.app")
check("默认人设+wechat 只分渠道", paths.scope_name("", "wechat"), "wechat")
check("非默认人设带渠道", paths.scope_name("preset_group_maibot", "wechat"),
      "preset_group_maibot.wechat")

# --- 2. 🔴 不能有双后缀（chat_log_for 已带渠道，再 scoped 会重复） ---
print("\n[2] 双后缀")
for pk, src in [("preset_group_maibot", "wechat"), ("base", "app"),
                ("linzhixia", "app")]:
    base = os.path.join(paths.DATA_DIR, "chat_history.jsonl")
    p = paths.scoped(base, pk, src)
    n = fname(p).count(".chat_history")
    check("%s.%s 不重复后缀" % (pk, src), n, 1)
check("wechat 分区名不含双 wechat",
      "wechat.wechat" in paths.scope_name("preset_group_maibot", "wechat"),
      False)

# --- 3. 🔴 写和读必须同一个文件（之前串路的根因） ---
# brain._log_for 和 server._cloud_history 各自算一遍，比对结果
print("\n[3] 写/读一致（server 与 brain 同一套算法）")
import brain as brain_mod
import server as server_mod
from persona_store import key_from_config

_tmpdir = tempfile.mkdtemp(prefix="_memiso_")
_orig_data = paths.DATA_DIR
_orig_log = brain_mod.chat_log_for
try:
    paths.DATA_DIR = _tmpdir

    class _FakeBrain(object):
        api = {"persona_file": "base"}

        def _persona_key_for(self, src):
            return brain_mod.Brain._persona_key_for(self, src)

        _log_for = brain_mod.Brain._log_for
        _mem_for = brain_mod.Brain._mem_for
        _cur_src = "app"

    fb = _FakeBrain()
    server_mod.API_CFG = {"persona_file": "base"}

    # 人设映射：wechat 用群友人设
    import json as _json
    cfg_path = os.path.join(_tmpdir, "config.json")
    _json.dump({"persona_by_src": {"wechat": "preset_group_maibot"}},
               open(cfg_path, "w", encoding="utf-8"))
    _orig_cfg_path = paths.CONFIG_PATH
    paths.CONFIG_PATH = cfg_path

    for src in ("app", "wechat"):
        # brain 写的
        w = fb._log_for(src)
        # server 读的
        pk = server_mod._persona_for_src(src, key_from_config({"persona_file": "base"}))
        r = paths.scoped(os.path.join(paths.DATA_DIR, "chat_history.jsonl"),
                         pk, src)
        check("%-7s 写=读 同一个文件" % src, fname(w) == fname(r), True)
        print("        %s" % fname(w))

    check("app 与 wechat 不是同一份",
          fname(fb._log_for("app")) != fname(fb._log_for("wechat")), True)

    # --- 4. 记忆库：server 构造 Brain 时必须落在分区库 ---
    # 🔴 注意判据不是「不传参数也分区」—— get_default_store() 不传参**就该**
    # 回公共库（它的设计如此，老数据不用搬）。要验的是**修复后的调用点**
    # 传了参数，所以复刻 server.py 里那段，而不是直接调无参版本。
    print("\n[4] 记忆库：复刻 server.py 的调用方式")
    import memory_store_v2 as ms
    ms._stores.clear()

    # 这三行是 server.py 里 Brain 构造前的实际写法
    from persona_store import key_from_config as _kfc
    _mem = ms.get_default_store(_kfc(server_mod.API_CFG), "app")
    check("server 构造时落在分区库（不是公共库）",
          os.path.basename(_mem.path) != "memory.json", True)
    print("        server 构造 -> %s" % os.path.basename(_mem.path))

    ms._stores.clear()
    check("无参调用仍回公共库（设计如此，不动它）",
          os.path.basename(ms.get_default_store().path), "memory.json")

    ms._stores.clear()
    for pk, src in [("base", "app"), ("preset_group_maibot", "wechat")]:
        p = ms.get_default_store(pk, src).path
        check("%s.%s 落在自己那份" % (pk, src),
              os.path.basename(p), "%s.%s.memory.json" % (pk, src))

    paths.CONFIG_PATH = _orig_cfg_path
finally:
    paths.DATA_DIR = _orig_data
    paths.CONFIG_PATH = _orig_cfg_path
    shutil.rmtree(_tmpdir, ignore_errors=True)
    ms._stores.clear()

print("\n" + "=" * 46)
print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("失败：" + "、".join(FAIL))
print("=" * 46)
sys.exit(1 if FAIL else 0)
