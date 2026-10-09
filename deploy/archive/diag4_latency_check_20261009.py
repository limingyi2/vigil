# -*- coding: utf-8 -*-
"""Post-fix latency check: fresh trace rounds + what ms covers."""
import paramiko

KEY = r"F:\Vigil\deploy\sshkeys\linzhixia_deploy"
CLOUD = "203.0.113.10"
PYC = r"C:\Vigil\.venv\Scripts\python.exe"

CODE = """# -*- coding: utf-8 -*-
import json, io
rows = []
with io.open(r"C:\\Vigil\\data\\trace.jsonl", encoding="utf-8",
             errors="replace") as f:
    for ln in f:
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:
            pass
print("TOTAL", len(rows))
chat = [r for r in rows if r.get("kind") == "chat"]
print("chat rounds:", len(chat))
for r in chat[-14:]:
    d = {}
    for k in ("t", "src", "ms", "tok_in", "tok_out", "tool", "plan",
              "plan_ms", "model", "fallback"):
        if k in r:
            d[k] = r[k]
    b = r.get("blocks") or {}
    d["blocks_total"] = b.get("total")
    print(json.dumps(d, ensure_ascii=True))
"""

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(CLOUD, username="Administrator", key_filename=KEY,
            timeout=30, banner_timeout=30, auth_timeout=30)
sftp = ssh.open_sftp()
with sftp.open("C:/Vigil/_diag4.py", "w") as f:
    f.write(CODE)
_c, out, err = ssh.exec_command(r"%s C:\Vigil\_diag4.py" % PYC, timeout=60)
print(out.read().decode("utf-8", "replace")[:3000])
e = err.read().decode("utf-8", "replace")
if e.strip():
    print("ERR:", e[-300:])
ssh.exec_command(r"del /q C:\Vigil\_diag4.py")
sftp.close()
ssh.close()
print("DIAG4 DONE")
