# -*- coding: utf-8 -*-
"""M3 转发通道：挂在现有 8788 HTTP 服务上的 WebSocket 端点。

本地大脑（client 的 Electron 主进程）作为 WS 客户端**出站**连上来，
云端把微信渠道的对话转发给本地答，答完经同一连接送回。为什么是本地出站：
云上开新入站端口要动安全组（8791 实测被挡），而 8788 本来就对外（bind_host
0.0.0.0 + token），WS 挂在同端口零新增暴露面，鉴权/日志全部复用。

协议（应用层全是 JSON 文本帧）：
  云 -> 本  {"t":"msg","id":..,"text":..,"display":..,"src":..,"img":b64?,"dry_run":bool}
  本 -> 云  {"t":"ack","id":..}                      收到了，开始答（仅日志）
  本 -> 云  {"t":"result","id":..,"reply":..,"err":..} 答完（reply 为加工后的成品文本）
心跳用 WS 协议层 ping/pong（Node ws 客户端自动 pong），不占应用层。

设计取舍（为什么不用规划里的"独立进程 8791"）：
  1) 8791 安全组实测入站不通；
  2) /api/chat 的路由判断需要"本地在不在线"这个状态，同进程就是一次
     字典查询，跨进程反而要再发明一条 IPC；
  3) 大脑重启断 WS 的问题由客户端指数退避重连兜底（本来就要实现）。
"""
import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time
import uuid

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# 本地大脑的总作答时限。环境变量只给测试/联调用（e2e 里调成 2s 免得干等 25s），
# 生产里谁也别去碰它。
ACK_TIMEOUT = float(os.environ.get("VIGIL_ACK_TIMEOUT") or 25)

# ── 全局 hub：云端一侧的全部转发状态 ──────────────────────────────
class Hub:
    def __init__(self):
        self._lock = threading.Lock()
        self._sock = None            # 当前活跃的 WS socket（就一个本地大脑）
        self._pending = {}           # mid -> {"ev": Event, "result": None}
        self._closed = {}            # mid -> {"payload": 原请求, "src":.., "display":..}
        self.on_late_result = None   # server 注入：fn(src, display, reply)
        self._n = 0                  # 累计转发条数（观测用）

    # ── 状态 ──
    def online(self):
        with self._lock:
            return self._sock is not None

    def stats(self):
        with self._lock:
            return {"online": self._sock is not None,
                    "pending": len(self._pending),
                    "forwarded": self._n}

    # ── 握手成功后由 handler 线程登记自己 ──
    def register(self, sock):
        with self._lock:
            old = self._sock
            self._sock = sock
        if old is not None and old is not sock:
            try:
                old.close()          # 新连接顶掉旧连接（本地重启竞态）
            except Exception:
                pass

    def unregister(self, sock):
        with self._lock:
            if self._sock is sock:
                self._sock = None

    # ── 云端 -> 本地：发消息并等结果 ──
    def ask_local(self, payload, timeout=25.0):
        """返回 {"ok":True,"reply":..} / {"ok":False,"reason":"offline"|"timeout"|"send"}"""
        mid = uuid.uuid4().hex[:12]
        payload = dict(payload)
        payload["t"] = "msg"
        payload["id"] = mid
        ev = threading.Event()
        slot = {"ev": ev, "result": None}
        with self._lock:
            sock = self._sock
            if sock is None:
                return {"ok": False, "reason": "offline"}
            self._pending[mid] = slot
            self._n += 1
        try:
            send_text(sock, json.dumps(payload, ensure_ascii=False))
        except Exception:
            self._drop_pending(mid)
            return {"ok": False, "reason": "send"}
        if not ev.wait(timeout):
            # 超时：标记 closed，迟到的 result 走 on_late_result 只写历史
            with self._lock:
                self._pending.pop(mid, None)
                self._closed[mid] = payload
            # closed 表别无限涨：只留最近 200 条
            with self._lock:
                if len(self._closed) > 200:
                    for k in list(self._closed.keys())[:-200]:
                        self._closed.pop(k, None)
            return {"ok": False, "reason": "timeout", "id": mid}
        res = slot["result"] or {}
        if res.get("err"):
            return {"ok": False, "reason": "local_err", "err": res.get("err")}
        return {"ok": True, "reply": res.get("reply") or "", "id": mid}

    def _drop_pending(self, mid):
        with self._lock:
            self._pending.pop(mid, None)

    # ── 本地 -> 云端：读帧循环里调用 ──
    def on_text(self, raw):
        try:
            d = json.loads(raw)
        except Exception:
            return
        t = d.get("t")
        mid = str(d.get("id") or "")
        if t == "result":
            with self._lock:
                slot = self._pending.pop(mid, None)
                cl = self._closed.pop(mid, None)
            if slot is not None:
                slot["result"] = d
                slot["ev"].set()
            elif cl is not None and self.on_late_result is not None:
                # 迟到的答案：对话已经用超时话术回了，这条只进历史不再外发
                try:
                    self.on_late_result(
                        cl.get("src"), cl.get("display") or cl.get("text") or "",
                        d.get("reply") or "")
                except Exception:
                    pass
        # ack / 其他类型：目前只用于排查，不驱动状态机


HUB = Hub()


# ── WS 帧层（服务端只收 masked、发 unmasked 文本帧）────────────────
def send_text(sock, s):
    data = s.encode("utf-8")
    head = bytearray([0x81])        # FIN=1, opcode=1 (text)
    n = len(data)
    if n < 126:
        head.append(n)
    elif n < 65536:
        head.append(126)
        head += struct.pack(">H", n)
    else:
        head.append(127)
        head += struct.pack(">Q", n)
    sock.sendall(bytes(head) + data)


def send_pong(sock, payload):
    head = bytearray([0x8A])        # FIN=1, opcode=10 (pong)
    head.append(len(payload) & 0x7F)
    sock.sendall(bytes(head) + payload)


def send_close(sock):
    try:
        sock.sendall(bytes([0x88, 0x00]))
    except Exception:
        pass


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


def read_frame(sock):
    """返回 (b1, payload)。b1 是帧头原始首字节：低 4 位 opcode、高位 FIN。
    🔴 别在这里把 FIN 剥掉 —— 调用方要靠它区分单帧和分片（第一版把
    FIN 剥了，serve 永远以为没 FIN，单帧消息也卡进分片循环等下一帧）。
    客户端帧按 RFC6455 必须 masked，服务端负责解。"""
    b1, b2 = _recv_exact(sock, 2)
    masked = bool(b2 & 0x80)
    ln = b2 & 0x7F
    if ln == 126:
        ln = struct.unpack(">H", _recv_exact(sock, 2))[0]
    elif ln == 127:
        ln = struct.unpack(">Q", _recv_exact(sock, 8))[0]
    mask = _recv_exact(sock, 4) if masked else b""
    payload = _recv_exact(sock, ln) if ln else b""
    if masked and payload:
        payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
    return b1, payload


def serve_connection(sock, hub=None):
    """握手之后的帧循环：dispatch 到 hub，断开时清理。

    hub：帧事件的目标 Hub。测试里自建 Hub 时必须显式传入 —— 不传就用
    模块级 HUB（生产路径）。socketpair 是全双工独立流，别想着"读自己
    发的"：msg（云端→本地）走的发送方向，serve 只收本地→云端的 result。
    读超时 35s：期间任何帧（含 pong）都算活着；连续两次超时先 ping 再判死。
    """
    if hub is None:
        hub = HUB
    sock.settimeout(35)
    try:
        while True:
            try:
                b1, payload = read_frame(sock)
            except socket.timeout:
                # 半开连接探测：先 ping 一次，再超时就是真死了
                try:
                    sock.sendall(bytes([0x89, 0x00]))   # ping
                    b1, payload = read_frame(sock)
                except Exception:
                    break
            except (ConnectionError, OSError):
                break
            opcode = b1 & 0x0F
            if opcode == 0x8:            # close
                send_close(sock)
                break
            if opcode == 0x9:            # ping -> pong
                send_pong(sock, payload)
                continue
            if opcode == 0xA:            # pong：心跳，什么都不用做
                continue
            if opcode in (0x1, 0x2):     # text/binary
                # 🔴 FIN=1 是单帧完整消息，直接处理 —— 别进分片组装！
                # 第一版这里无脑进组装循环等 FIN，结果**单帧消息会把
                # 下一帧吞进来拼一起**：msg 后紧跟着的 result 被 append
                # 进 frags，两个 JSON 拼一块解析失败被吞，ask_local 白等。
                op = opcode
                frags = [payload]
                fin = bool(b1 & 0x80)
                while not fin:
                    nb1, payload = read_frame(sock)
                    nopcode = nb1 & 0x0F
                    if nopcode == 0x8:
                        send_close(sock)
                        return
                    if nopcode == 0x9:
                        send_pong(sock, payload)
                        continue
                    fin = bool(nb1 & 0x80)
                    if nopcode == 0x0:            # continuation 帧
                        frags.append(payload)
                    # FIN=1 且 opcode!=0 是对端协议违规（新消息没让路），
                    # 丢弃这一帧，把已收的片收尾，不让它破坏当前消息
                if op == 0x1:
                    try:
                        hub.on_text(b"".join(frags).decode("utf-8"))
                    except Exception:
                        pass
            # 其他 opcode 忽略
    except Exception:
        pass
    finally:
        HUB.unregister(sock)
        try:
            sock.close()
        except Exception:
            pass


# ── 握手：在 do_GET 里最先调用 ────────────────────────────────────
def try_upgrade(handler, want_token=""):
    """是 /ws/brain 的 Upgrade 请求就完成握手并接管线程；否则返回 False 让
    server 照常路由。token 不对返回 401 后直接 return True（已接管）。

    want_token 由 server 传入（brain_token）—— 这里刻意不 import server：
    `python server.py` 跑在 __main__ 名下，import server 会把整个模块**再
    加载一遍**（顶层副作用全部重演）。"""
    if handler.path.split("?")[0] != "/ws/brain":
        return False
    if (handler.headers.get("Upgrade") or "").lower() != "websocket":
        handler._json(400, {"ok": False, "err": "不是 WebSocket 升级请求"})
        return True
    # 鉴权：query ?token= 优先（浏览器端带 header 不方便），header 兜底。
    # 主人钥匙（brain_token）或已注册设备的 device_token 都放行 ——
    # 客户端首启走 registerThisDevice 后手里只有设备 token。
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(handler.path).query)
    tok = (q.get("token") or [""])[0]
    if not tok:
        auth = handler.headers.get("Authorization") or ""
        tok = auth[7:] if auth.lower().startswith("bearer ") else ""
    ok = False
    if want_token and tok == want_token:
        ok = True
    else:
        try:
            import devices as _dev
            ok = _dev.verify(tok, handler.client_address[0]) is not None
        except Exception:
            ok = False
    if not ok:
        handler.send_response(401)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.end_headers()
        handler.wfile.write(json.dumps(
            {"ok": False, "err": "token 不对，连不上转发通道"}).encode("utf-8"))
        return True
    key = handler.headers.get("Sec-WebSocket-Key") or ""
    accept = base64.b64encode(
        hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    handler.connection.sendall(
        ("HTTP/1.1 101 Switching Protocols\r\n"
         "Upgrade: websocket\r\n"
         "Connection: Upgrade\r\n"
         "Sec-WebSocket-Accept: %s\r\n\r\n" % accept).encode("ascii"))
    sock = handler.connection
    handler.close_connection = True   # 别让 BaseHTTPRequestHandler 再碰这条流
    HUB.register(sock)
    serve_connection(sock)            # 占住 handler 线程直到断开
    return True
