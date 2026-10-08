# -*- coding: utf-8 -*- (ASCII only)
"""Cloud-side WS endpoint check: handshake 101 + server ping -> pong.
No chat data touched. ASCII only.
Host/port/token all come from the local (gitignored) app_conn.json."""
import base64
import hashlib
import json
import socket
import sys
import time

sys.path.insert(0, r"F:\Vigil\brain")

_conn = json.load(open(r"F:\Vigil\brain\config\app_conn.json",
                       encoding="utf-8"))
TOK = _conn["token"]
_base = _conn["base"].split("//")[1]          # http://<ip>:<port> -> <ip>:<port>
HOST, PORT = (_base.split(":") + ["80"])[:2]
PORT = int(PORT)

# 1) plain HTTP sanity
with __import__("urllib.request", fromlist=["x"]).urlopen(
        "http://%s:%d/api/health" % (HOST, PORT), timeout=10) as r:
    print("health:", json.load(r))

# 2) WS handshake over the real public endpoint
s = socket.create_connection((HOST, PORT), timeout=15)
s.settimeout(38)
key = base64.b64encode(hashlib.sha1(b"probe").digest()).decode()
req = ("GET /ws/brain?token=%s HTTP/1.1\r\nHost: %s:%d\r\n"
       "Upgrade: websocket\r\nConnection: Upgrade\r\n"
       "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
       % (TOK, HOST, PORT, key))
s.sendall(req.encode("ascii"))
buf = b""
while b"\r\n\r\n" not in buf:
    c = s.recv(4096)
    if not c:
        break
    buf += c
head = buf.split(b"\r\n\r\n")[0].decode("latin1")
print("handshake:", head.split("\r\n")[0])
assert " 101 " in head, "handshake failed"

# 3) wait for the server's protocol-level ping (35s window), answer pong
data = buf.split(b"\r\n\r\n", 1)[1]
end = time.time() + 38
got_ping = False
while time.time() < end:
    if len(data) >= 2 and (data[0] & 0x0F) == 0x9:
        got_ping = True
        body = data[2:]
        print("server ping received (len=%d), replying pong" % len(body))
        mask = bytes((1, 2, 3, 4))
        masked = bytes(x ^ mask[i % 4] for i, x in enumerate(body))
        s.sendall(bytes([0x8A, 0x80 | len(body)]) + mask + masked)
        break
    try:
        c = s.recv(4096)
    except socket.timeout:
        break
    if not c:
        break
    data += c

print("PING_PONG_OK" if got_ping else "NO_PING_IN_WINDOW")
s.close()
