# -*- coding: utf-8 -*-
"""Cloud diag #2: persona sizes + her recent wechat replies + planner flag."""
import paramiko

KEY = r"F:\Vigil\deploy\sshkeys\linzhixia_deploy"
HOST = "203.0.113.10"
USER = "Administrator"
PY = r"C:\Vigil\.venv\Scripts\python.exe"

CODE = """# -*- coding: utf-8 -*-
import json, io, os, glob

print("== persona files ==")
for d in (r"C:\\Vigil\\config\\personas",):
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            dd = json.load(io.open(p, encoding="utf-8"))
            sizes = {k: len(str(v)) for k, v in dd.items()}
            print(os.path.basename(p).encode("ascii", "replace").decode(),
                  json.dumps(sizes))
        except Exception as ex:
            print(p, "ERR", ex)

print("== planner/behavior flags in config ==")
cfg = json.load(io.open(r"C:\\Vigil\\config\\config.json", encoding="utf-8"))
for k in sorted(cfg.keys()):
    v = cfg[k]
    if isinstance(v, dict):
        print(k, "->", json.dumps({kk: vv for kk, vv in v.items()
                                    if not str(kk).startswith("_")},
                                   ensure_ascii=True)[:300])
    else:
        s = str(v)
        print(k, "=", s[:80])

print("== her last wechat replies (chat_history.jsonl) ==")
rows = []
with io.open(r"C:\\Vigil\\data\\chat_history.jsonl",
             encoding="utf-8", errors="replace") as f:
    for ln in f:
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:
            pass
print("total", len(rows))
for r in rows[-8:]:
    role = r.get("role", "?")
    src = r.get("src", "?")
    txt = str(r.get("content") or r.get("text") or "")[:160]
    t = r.get("t") or r.get("ts") or ""
    print(json.dumps({"t": t, "role": role, "src": src, "txt": txt},
                     ensure_ascii=True))
"""

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, key_filename=KEY, timeout=30,
                banner_timeout=30, auth_timeout=30)
    sftp = ssh.open_sftp()
    with sftp.open("C:/Vigil/_diag2.py", "w") as f:
        f.write(CODE)
    _o, out, err = ssh.exec_command(r"%s C:\Vigil\_diag2.py" % PY, timeout=90)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    print(o[:6000])
    if e.strip():
        print("ERR:", e[-800:])
    ssh.exec_command(r"del /q C:\Vigil\_diag2.py")
    sftp.close()
    ssh.close()
    print("DIAG2 DONE")

if __name__ == "__main__":
    main()
