import socket, time
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", 8791))
s.listen(2)
print("LISTEN_OK", flush=True)
s.settimeout(25)
try:
    c, a = s.accept()
    c.sendall(b"PROBE_OK")
    print("GOT_CONN", a, flush=True)
    time.sleep(3)
    c.close()
except Exception as e:
    print("NO_CONN", e, flush=True)
