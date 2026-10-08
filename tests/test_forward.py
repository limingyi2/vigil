# -*- coding: utf-8 -*-
"""M3 ws_forward：帧编解码 / Hub pending-closed 状态机 / 迟到结果。

不起 HTTP server，socket 层用线程内回环对拷，全部临时资源。
"""
import json
import os
import socket
import struct
import sys
import threading

BRAIN = os.path.dirname(os.path.abspath(__file__)) + os.sep + ".." + \
    os.sep + "brain"
BRAIN = os.path.normpath(BRAIN)
sys.path.insert(0, BRAIN)

import ws_forward  # noqa: E402


# ── 帧层 ──────────────────────────────────────────────────────────

def _sockpair():
    a, b = socket.socketpair()
    a.settimeout(5)
    b.settimeout(5)
    return a, b


def test_send_text_roundtrip_unmasked():
    """服务端 → 客户端方向不 mask；read_frame 兼容两种。"""
    a, b = _sockpair()
    ws_forward.send_text(a, "你好 forward")
    b1, payload = ws_forward.read_frame(b)
    assert b1 & 0x0F == 0x1 and b1 & 0x80
    assert payload.decode("utf-8") == "你好 forward"
    a.close()
    b.close()


def test_read_frame_accepts_masked_client_frame():
    """客户端 → 服务端必须 masked（RFC6455），服务端要能解。"""
    a, b = _sockpair()
    data = "masked hello".encode("utf-8")
    mask = b"\x11\x22\x33\x44"
    masked = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
    a.sendall(bytes([0x81, 0x80 | len(data)]) + mask + masked)
    b1, payload = ws_forward.read_frame(b)
    assert b1 & 0x0F == 0x1
    assert payload == data
    a.close()
    b.close()


def test_large_frame_16bit_len():
    """>125 字节走 16bit 长度；64KB+ 走 64bit。IMG base64 大帧的通路。"""
    a, b = _sockpair()
    big = "x" * 70000
    ws_forward.send_text(a, big)
    b1, payload = ws_forward.read_frame(b)
    assert b1 & 0x0F == 0x1 and payload.decode() == big
    a.close()
    b.close()


def test_fragmented_message_reassembled():
    """分片：第一帧 opcode=1 FIN=0，中间 0，最后一帧 FIN=1。Node 大消息可能分片。"""
    a, b = _sockpair()
    # 分三片发 "AA" "BB" "CC"
    a.sendall(bytes([0x01, 0x02]) + b"AA")     # FIN=0 text
    a.sendall(bytes([0x00, 0x02]) + b"BB")     # FIN=0 continuation
    a.sendall(bytes([0x80, 0x02]) + b"CC")     # FIN=1 continuation
    b1, payload = ws_forward.read_frame(b)     # b 侧按"读一帧"语义只拿到第一片
    assert b1 & 0x0F == 0x1 and not (b1 & 0x80) and payload == b"AA"
    # serve_connection 里的组装逻辑在 e2e 里验证全链路；这里验证帧可逐片读
    b2_, p2 = ws_forward.read_frame(b)
    assert b2_ & 0x0F == 0x0 and not (b2_ & 0x80) and p2 == b"BB"
    b3_, p3 = ws_forward.read_frame(b)
    assert b3_ & 0x0F == 0x0 and (b3_ & 0x80) and p3 == b"CC"
    a.close()
    b.close()


def test_ping_pong():
    a, b = _sockpair()
    a.sendall(bytes([0x89, 0x02]) + b"hi")     # ping
    b1, payload = ws_forward.read_frame(b)
    assert b1 & 0x0F == 0x9 and payload == b"hi"
    ws_forward.send_pong(a, b"hi")
    b1, payload = ws_forward.read_frame(b)
    assert b1 & 0x0F == 0xA
    a.close()
    b.close()


# ── Hub 状态机 ────────────────────────────────────────────────────

def _fake_local(sock, reply_text, ack=True, delay=0.0, reply_after=None):
    """模拟本地大脑侧：收 msg -> 回 result。独立线程。"""
    def run():
        try:
            while True:
                b1, payload = ws_forward.read_frame(sock)
                if b1 & 0x0F != 0x1:
                    continue
                d = json.loads(payload.decode("utf-8"))
                if d.get("t") == "msg":
                    if ack:
                        ws_forward.send_text(sock, json.dumps(
                            {"t": "ack", "id": d["id"]}))
                    if delay:
                        threading.Event().wait(delay)
                    if reply_after is not None:
                        threading.Event().wait(reply_after)
                        if reply_after > 100:   # 语义：永不回（用大值表示）
                            return
                    ws_forward.send_text(sock, json.dumps(
                        {"t": "result", "id": d["id"], "reply": reply_text,
                         "err": ""}, ensure_ascii=False))
        except Exception:
            pass
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _serve(sock, hub=None):
    """把 hub 侧的帧循环跑起来（真实 server 里这是 handler 线程干的活）。
    🔴 hub 参数必须跟 register 的是**同一个实例** —— serve 收到 result 后
    在 hub._pending 里找等待者；不传时用的是模块级 HUB，自建 Hub 的
    pending 永远等不到唤醒（第一版测试就栽在这，白查半天）。"""
    threading.Thread(target=ws_forward.serve_connection, args=(sock, hub),
                     daemon=True).start()


def test_hub_offline():
    h = ws_forward.Hub()
    r = h.ask_local({"text": "hi"}, timeout=1)
    assert r == {"ok": False, "reason": "offline"}


def test_hub_roundtrip():
    a, b = _sockpair()
    h = ws_forward.Hub()
    h.register(b)
    _serve(b, h)
    _fake_local(a, "本地答的")
    r = h.ask_local({"text": "你好"}, timeout=3)
    assert r["ok"] is True and r["reply"] == "本地答的"
    a.close()
    b.close()


def test_hub_timeout_then_late_result():
    """25s 没答 → timeout + 进 closed；迟到的 result 只走 on_late_result。"""
    a, b = _sockpair()
    h = ws_forward.Hub()
    late = []
    h.on_late_result = lambda src, disp, reply: late.append((src, disp, reply))
    h.register(b)
    _serve(b, h)
    # 模拟"只收不回"的本地端（在 a 侧收 —— b 侧是 serve 线程的地盘，
    # 在 b 侧 recv 会跟 serve 抢流，result 永远到不了 serve）
    stop = threading.Event()

    def silent():
        try:
            while not stop.is_set():
                b1, payload = ws_forward.read_frame(a)
                if b1 & 0x0F == 0x1:
                    pass          # 收到 msg 也不回
        except Exception:
            pass
    threading.Thread(target=silent, daemon=True).start()

    r = h.ask_local({"text": "慢点", "src": "wechat",
                     "display": "慢点"}, timeout=0.5)
    assert r["ok"] is False and r["reason"] == "timeout"
    assert r["id"] in h._closed

    # 迟到的 result：拿到 mid，从 a 侧（本地端）补发
    mid = r["id"]
    ws_forward.send_text(a, json.dumps(
        {"t": "result", "id": mid, "reply": "迟到的答案", "err": ""}))
    import time as _t
    _t.sleep(0.4)
    assert late == [("wechat", "慢点", "迟到的答案")]
    assert mid not in h._closed
    stop.set()
    a.close()
    b.close()


def test_hub_normal_result_not_double_lated():
    """正常答完的 mid 不应再进 closed（不触发 late 回调）。"""
    a, b = _sockpair()
    h = ws_forward.Hub()
    late = []
    h.on_late_result = lambda *args: late.append(args)
    h.register(b)
    _serve(b, h)
    _fake_local(a, "准点答")
    r = h.ask_local({"text": "快问"}, timeout=3)
    assert r["ok"] is True
    import time as _t
    _t.sleep(0.2)
    assert late == []
    a.close()
    b.close()


def test_hub_local_err():
    """本地端回 err 非空 → ok=False, reason=local_err。"""
    a, b = _sockpair()
    h = ws_forward.Hub()
    h.register(b)
    _serve(b, h)

    def erring():
        b1, payload = ws_forward.read_frame(a)
        d = json.loads(payload.decode("utf-8"))
        ws_forward.send_text(a, json.dumps(
            {"t": "result", "id": d["id"], "reply": "", "err": "炸了"}))
    threading.Thread(target=erring, daemon=True).start()
    r = h.ask_local({"text": "x"}, timeout=3)
    assert r["ok"] is False and r["reason"] == "local_err"
    a.close()
    b.close()


def test_register_replaces_old_socket():
    """本地重启竞态：新连接顶掉旧连接，旧 socket 被关闭。"""
    a1, b1 = _sockpair()
    a2, b2 = _sockpair()
    h = ws_forward.Hub()
    h.register(b1)
    h.register(b2)
    assert h.online()
    with h._lock:
        assert h._sock is b2
    a1.close()
    b1.close()
    a2.close()
    b2.close()
