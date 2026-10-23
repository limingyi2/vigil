# -*- coding: utf-8 -*-
"""M3 e2e：转发通道 + 双大脑路由，全部跑在本机、临时家，不碰真实微信。

A = 云权威（server.py，带 /ws/brain + /api/route + /api/chat 路由拦截）
fake = python 手写的"本地客户端"（模拟 client/electron/forward.js 的行为）
B（真本地大脑）不需要起 —— 转发链路里"答话"这一步是客户端干的，
fake 自己回固定文本，重点验的是**路由语义**：
  在线转发 / 渠道过滤 / flag 秒切 / 离线兜底 / 超时话术 / 迟到只入历史。
"""
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN = os.path.join(ROOT, "brain")
SERVER = os.path.join(BRAIN, "server.py")
PY = sys.executable

PASS = []
FAIL = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [PASS] " if ok else "  [FAIL] ") + name +
          ("" if ok else "  ← %s" % str(detail)[:200]), flush=True)
    return ok


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def api(port, tok, method, path, body=None):
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                               method=method)
    r.add_header("Authorization", "Bearer " + tok)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, data, timeout=40) as resp:
        return resp.status, json.load(resp)


def raw_get(path):
    """不带鉴权的原始 GET（测 401 用）。返回 (status, body_bytes)。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s"
                                    % (PORT, path), timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ── WS 客户端侧（masked 写帧）─────────────────────────────────────
def ws_handshake(port, tok, path="/ws/brain"):
    """HTTP Upgrade 握手。返回 socket 或 (False, status)。"""
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.settimeout(15)
    key = "dGhlIHNhbXBsZSBub25jZQ=="       # 固定 key（RFC 示例），响应可预期
    req = ("GET %s?token=%s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
           % (path, tok, port, key))
    s.sendall(req.encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    head = buf.split(b"\r\n\r\n")[0].decode("latin1", "replace")
    if " 101 " in head:
        return s
    return ("rejected", head.split("\r\n")[0] if head else "no response")


def client_send(sock, s):
    data = s.encode("utf-8")
    mask = os.urandom(4)
    masked = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
    n = len(data)
    if n < 126:
        head = bytes([0x81, 0x80 | n])
    elif n < 65536:
        head = bytes([0x81, 0x80 | 126]) + struct.pack(">H", n)
    else:
        head = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", n)
    sock.sendall(head + mask + masked)


def client_recv(sock, timeout=10):
    """读一帧文本（复用服务端的解帧器：服务端→客户端帧不 masked）。"""
    sock.settimeout(timeout)
    from ws_forward import read_frame
    b1, payload = read_frame(sock)
    return b1 & 0x0F, payload


def wait_msg(sock, timeout=10):
    """等下一条 msg（跳过 ping/pong）。"""
    dead = time.time() + timeout
    while time.time() < dead:
        try:
            op, payload = client_recv(sock, timeout=max(0.2, dead - time.time()))
        except Exception:
            return None
        if op == 0x1:
            d = json.loads(payload.decode("utf-8"))
            if d.get("t") == "msg":
                return d
    return None


def drained(sock, wait=1.0):
    """等 wait 秒确认**没有** msg 进来（渠道过滤 / flag 直答用）。"""
    return wait_msg(sock, timeout=wait) is None


# ── 主流程 ────────────────────────────────────────────────────────
PORT = None
PROC = None


def main():
    global PORT, PROC
    sys.path.insert(0, BRAIN)
    print("[1] 起 A（云权威 + ws_forward）", flush=True)
    PORT = free_port()
    home = tempfile.mkdtemp(prefix="fwdA_")
    os.makedirs(os.path.join(home, "config"), exist_ok=True)
    with open(os.path.join(home, "config", "config.json"), "w",
              encoding="utf-8") as f:
        json.dump({"brain_token": "e2e-token-a", "bind_host": "127.0.0.1"}, f)
    env = dict(os.environ, VIGIL_HOME=home, VIGIL_PORT=str(PORT),
               BRAIN_MUTEX="Global\\VigilFwdE2E_A", VIGIL_ACK_TIMEOUT="2")
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    PROC = subprocess.Popen(
        [PY, SERVER], cwd=BRAIN, env=env,
        stdout=open(os.path.join(ROOT, "logs", "e2e_fwd_a.log"), "w",
                    encoding="utf-8"), stderr=subprocess.STDOUT)

    dead = time.time() + 40
    ready = False
    while time.time() < dead:
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/api/health" % PORT, timeout=3) as r:
                if json.load(r).get("ok"):
                    ready = True
                    break
        except Exception:
            pass
        time.sleep(0.6)
    if not check("A 就绪", ready):
        return 1

    tok = "e2e-token-a"

    print("[2] 握手与鉴权", flush=True)
    st, _ = raw_get("/ws/brain?token=wrong-token")
    # 普通非 Upgrade 请求不会到 401 那步？—— Upgrade 头缺失会 400；给足信息即可
    check("非 WS 请求被明确拒绝（4xx）", 400 <= st < 500, "status=%s" % st)

    hs = ws_handshake(PORT, "bad-token")
    check("坏 token 握手被拒（401）", hs[0] == "rejected" and
          "401" in hs[1], hs)

    s1 = ws_handshake(PORT, tok)
    check("好 token 握手成功（101）", isinstance(s1, socket.socket), s1)

    st, d = api(PORT, tok, "GET", "/api/route")
    check("A 报告转发通道在线", st == 200 and d.get("forward", {})
          .get("online") is True, d)

    print("[3] flag 与路由", flush=True)
    st, d = api(PORT, tok, "POST", "/api/route",
                {"wechat_route": "local"})
    check("flag 切 local（秒切不重启）", st == 200 and
          d.get("wechat_route") == "local", d)
    st, d = api(PORT, tok, "GET", "/api/route")
    check("GET /api/route 读回 local", d.get("wechat_route") == "local", d)

    print("[4] 在线转发：微信渠道 → 本地答", flush=True)

    def responder_once(sock, reply, timeout=8):
        """后台等一条 msg 并回 result —— api() 是阻塞的，收+回必须并发。"""
        got = {}

        def run():
            m = wait_msg(sock, timeout=timeout)
            got["msg"] = m
            if m:
                client_send(sock, json.dumps(
                    {"t": "ack", "id": m["id"]}))
                client_send(sock, json.dumps(
                    {"t": "result", "id": m["id"], "reply": reply,
                     "err": ""}))
        threading.Thread(target=run, daemon=True).start()
        return got

    g = responder_once(s1, "本地答的")
    st, d = api(PORT, tok, "POST", "/api/chat",
                {"text": "在吗", "src": "wechat"})
    time.sleep(0.3)
    check("转发链路：本地收到 msg（原文+渠道）",
          bool(g.get("msg")) and g["msg"].get("text") == "在吗" and
          g["msg"].get("src") == "wechat", g.get("msg"))
    check("云端回包=本地答案 + mode=本地大脑", st == 200 and
          d.get("reply") == "本地答的" and d.get("mode") == "本地大脑", d)

    big = "x" * 300000
    g = responder_once(s1, "big-ok")
    st, d = api(PORT, tok, "POST", "/api/chat",
                {"text": big, "src": "wechat"})
    time.sleep(0.3)
    check("大消息转发（300KB 文本）", bool(g.get("msg")) and
          g["msg"].get("text") == big and d.get("reply") == "big-ok",
          (str((g.get("msg") or {}).get("text", ""))[:30], d.get("reply")))

    print("[5] 渠道过滤：app 渠道不转发", flush=True)
    st, d = api(PORT, tok, "POST", "/api/chat",
                {"text": "直答渠道", "src": "app", "dry_run": True})
    check("app 渠道不走转发", d.get("mode") != "本地大脑" and
          drained(s1, wait=1.5), d)

    print("[6] flag 切回 cloud：微信也直答", flush=True)
    api(PORT, tok, "POST", "/api/route", {"wechat_route": "cloud"})
    st, d = api(PORT, tok, "POST", "/api/chat",
                {"text": "谁答", "src": "wechat", "dry_run": True})
    check("flag=cloud 微信直答", d.get("mode") != "本地大脑" and
          drained(s1, wait=1.5), d)
    api(PORT, tok, "POST", "/api/route", {"wechat_route": "local"})

    print("[7] 离线兜底：本地断开 → 云端直答", flush=True)
    s1.close()
    dead = time.time() + 6
    off = False
    while time.time() < dead:
        st, d = api(PORT, tok, "GET", "/api/route")
        if d.get("forward", {}).get("online") is False:
            off = True
            break
        time.sleep(0.4)
    check("断开后 online=False", off)
    st, d = api(PORT, tok, "POST", "/api/chat",
                {"text": "离线谁答", "src": "wechat", "dry_run": True})
    check("离线 → 云端直答（不是本地大脑）", st == 200 and
          d.get("mode") != "本地大脑", d)

    print("[8] 超时话术 + 迟到答案只入历史", flush=True)
    s2 = ws_handshake(PORT, tok)
    if not isinstance(s2, socket.socket):
        check("重连成功", False, s2)
        return 1
    check("重连成功", True)
    st, d = api(PORT, tok, "POST", "/api/chat",
                {"text": "装死的消息", "src": "wechat"})
    got4 = wait_msg(s2, timeout=5)
    # 收到但不回 —— 装死。VIGIL_ACK_TIMEOUT=2 → 云端 2s 后放弃
    check("超时：mode=本地大脑超时 + 话术", st == 200 and
          d.get("mode") == "本地大脑超时" and got4 is not None and
          "掉线" in (d.get("reply") or ""), (d, got4 is not None))
    if got4:
        time.sleep(0.5)     # 确保云端已 closed
        client_send(s2, json.dumps({"t": "result", "id": got4["id"],
                                    "reply": "迟到的答案", "err": ""}))
        time.sleep(0.8)
    st, d = api(PORT, tok, "GET", "/api/history?src=wechat&limit=20")
    items = d.get("items") or d.get("messages") or []
    has_late = any("迟到的答案" in json.dumps(i, ensure_ascii=False)
                   for i in items)
    check("迟到答案已写入云端历史", has_late, items[-2:] if items else "空")

    print("[9] 收尾", flush=True)
    s2.close()
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)), flush=True)
    if FAIL:
        print("失败项：", FAIL, flush=True)
        return 1
    print("E2E_FORWARD_OK", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        if PROC:
            PROC.terminate()
