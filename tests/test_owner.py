# -*- coding: utf-8 -*-
"""M4 owner 鉴权：clearance 三档矩阵 / call() 拦截 / specs 群聊裁剪 / 审计日志。

全部不起真客户端（需要客户端的用例用线程里 take+resolve 模拟），
runtime.api_config 用 monkeypatch 覆盖，不碰真实 config。
"""
import os
import sys
import threading
import time

import pytest

BRAIN = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "brain"))
sys.path.insert(0, BRAIN)

import localtools  # noqa: E402
import runtime     # noqa: E402

OWNER = "wxid_owner"
STRANGER = "wxid_stranger"


@pytest.fixture(autouse=True)
def _clean():
    """每个用例前后：清队列、清取消标志、清渠道上下文 —— 用例间隔离。"""
    localtools.reset()
    localtools.reset_cancel()
    localtools.set_context("", "")
    yield
    localtools.reset()
    localtools.reset_cancel()
    localtools.set_context("", "")


@pytest.fixture
def owner_only(monkeypatch):
    """云端 config owner_ids 只配了主人一个微信号。"""
    monkeypatch.setattr(runtime, "api_config", {"owner_ids": [OWNER]})


# ── clearance 三档矩阵 ────────────────────────────────────────────

def test_clearance_wechat_owner_all_pass(owner_only):
    """私聊 + owner：三档全放（坐在他对面聊天的人就是他本人）。"""
    for r in ("safe", "confirm", "dangerous"):
        ok, why = localtools.clearance(r, src="wechat", sender_id=OWNER)
        assert ok, "%s 档不该拒：%s" % (r, why)


def test_clearance_group_owner_safe_only(owner_only):
    """群聊即使 owner 也只放 safe —— 改动类一律拒。"""
    ok, _ = localtools.clearance("safe", src="wechat_group", sender_id=OWNER)
    assert ok
    for r in ("confirm", "dangerous"):
        ok, why = localtools.clearance(r, src="wechat_group", sender_id=OWNER)
        assert not ok, "%s 档在群里必须拒" % r
        assert why


def test_clearance_wechat_stranger_denied(owner_only):
    """私聊非 owner（含空身份）：三档全拒 —— 宁可错杀。"""
    for sid in (STRANGER, "", None):
        for r in ("safe", "confirm", "dangerous"):
            ok, why = localtools.clearance(r, src="wechat", sender_id=sid)
            assert not ok, "sid=%r %s 档不该放" % (sid, r)
            assert why


def test_clearance_no_owner_ids_denies_wechat(monkeypatch):
    """owner_ids 没配：微信渠道谁都不放行（宁可错过不可错杀）。"""
    monkeypatch.setattr(runtime, "api_config", {})
    for r in ("safe", "confirm", "dangerous"):
        ok, _ = localtools.clearance(r, src="wechat", sender_id=OWNER)
        assert not ok


def test_clearance_client_channels_trusted(owner_only):
    """app/pc 等带客户端的渠道：能在这条渠道聊天的就是主人本人，全放。"""
    for src in ("app", "pc", "web", "probe", ""):
        for r in ("safe", "confirm", "dangerous"):
            ok, why = localtools.clearance(r, src=src, sender_id="")
            assert ok, "src=%s %s 档不该拒：%s" % (src, r, why)


def test_clearance_unknown_risk_treated_safe(owner_only):
    """risk 值非法时按 safe 处理（保守降档，而不是放行或崩溃）。"""
    ok, _ = localtools.clearance("explode", src="wechat_group", sender_id=OWNER)
    assert ok


# ── call() 拦截（denied 不进队列）─────────────────────────────────

def test_call_denied_for_stranger(owner_only):
    localtools.set_context("wechat", STRANGER)
    out = localtools.call("local_file_write", {"path": "x", "content": "y"})
    assert "拦" in out and "别重试" in out
    events = localtools.audit()
    assert events and events[0]["event"] == "denied"
    assert events[0]["tool"] == "local_file_write"
    assert events[0]["src"] == "wechat"
    assert events[0]["sender_id"] == STRANGER
    assert localtools.take("dev_test") is None   # 没进队列，客户端无感


def test_call_denied_in_group_even_owner(owner_only):
    localtools.set_context("wechat_group", OWNER)
    before = localtools.stats()["denied"]
    out = localtools.call("local_exec", {"cmd": "dir"})
    assert "别重试" in out
    assert localtools.stats()["denied"] == before + 1


def test_call_unknown_tool(owner_only):
    localtools.set_context("wechat", OWNER)
    out = localtools.call("local_no_such_tool", {})
    assert "没有这个本地工具" in out


# ── specs 群聊裁剪（模型看不到改动类工具）─────────────────────────

def test_specs_all_have_risk():
    sp = localtools.specs()
    assert len(sp) == 20
    assert all("risk" in v and v["risk"] in ("safe", "confirm", "dangerous")
               for v in sp.values())


def test_specs_group_safe_only():
    sp = localtools.specs(src="wechat_group")
    assert sp, "群里至少还能看到查询类工具"
    assert all(v["risk"] == "safe" for v in sp.values())
    for t in ("local_exec", "local_file_delete", "local_process_kill",
              "local_file_write", "local_clipboard_write"):
        assert t not in sp, "%s 不该暴露给群聊" % t
    assert "local_system_info" in sp and "local_file_read" in sp


def test_specs_group_names_filtered():
    sp = localtools.specs(names=["local_exec", "local_system_info"],
                          src="wechat_group")
    assert list(sp) == ["local_system_info"]


def test_specs_private_and_client_full():
    assert "local_exec" in localtools.specs(src="wechat")
    assert "local_exec" in localtools.specs(src="app")
    assert "local_exec" in localtools.specs(src=None)


# ── call() 放行路径（线程模拟客户端 take+resolve）─────────────────

def _client_once(result="42 个文件", err=""):
    """模拟客户端：轮询队列直到有活可领（call() 入队有先后，不能只 take 一次），
    领到就回结果。10 秒领不到就放弃（让用例走到超时分支自己暴露）。"""
    def run():
        deadline = time.time() + 10
        while time.time() < deadline:
            c = localtools.take("dev_test")
            if c:
                localtools.resolve(c["call_id"], result, err)
                return
            time.sleep(0.05)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_call_allowed_for_owner(owner_only):
    localtools.set_context("wechat", OWNER)
    _client_once(result="42 个文件")
    out = localtools.call("local_file_list", {"path": "C:/Users"}, wait=10)
    assert "42 个文件" in out
    events = localtools.audit()
    assert events[0]["event"] == "queued"
    assert events[0]["tool"] == "local_file_list"


def test_call_allowed_in_group_safe(owner_only):
    localtools.set_context("wechat_group", OWNER)
    _client_once(result="CPU 占用 12%")
    out = localtools.call("local_system_info", {}, wait=10)
    assert "12%" in out


def test_call_client_refusal(owner_only):
    """客户端弹窗被他点了"不允许"：refused 话术返回，不重试。"""
    localtools.set_context("wechat", OWNER)
    _client_once(result="__refused__")
    out = localtools.call("local_clipboard_write", {"text": "hi"}, wait=10)
    assert "拒绝" in out


# ── cancel 修复回归（_CANCEL 曾被局部赋值架空）────────────────────

def test_cancel_breaks_pending_call(owner_only):
    localtools.set_context("wechat", OWNER)
    box = {}

    def runner():
        box["out"] = localtools.call("local_screenshot", {}, wait=20)

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and localtools.stats()["pending"] < 1:
        time.sleep(0.05)
    assert localtools.stats()["pending"] >= 1, "调用没进队列"
    localtools.cancel()
    t.join(15)
    assert not t.is_alive(), "call() 没被 cancel 唤醒"
    assert "停止" in box.get("out", "")
