# -*- coding: utf-8 -*-
"""Cloud diagnostics 2026-10-09: latency + persona check.

Pulls from cloud (key auth, no password):
  1. last full trace record (structure probe) + summary of last ~25 rounds
  2. persona_file config line + persona dir listing + persona field sizes
  3. data dir listing (find chat history / persona sync artifacts)
All output ASCII-safe (json with ensure_ascii=True, non-ascii escaped).
"""
import json
import sys

import paramiko

KEY = r"F:\Vigil\deploy\sshkeys\linzhixia_deploy"
HOST = "203.0.113.10"
USER = "Administrator"
PY = r"C:\Vigil\.venv\Scripts\python.exe"


def run(ssh, cmd, timeout=90):
    _o, out, err = ssh.exec_command(cmd, timeout=timeout)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    return o, e


def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, key_filename=KEY, timeout=30,
                banner_timeout=30, auth_timeout=30)

    # ---- 1. trace: structure probe + last rounds summary ----
    code = (
        "import json,io\n"
        "p=r'C:\\Vigil\\data\\trace.jsonl'\n"
        "rows=[]\n"
        "with io.open(p,encoding='utf-8',errors='replace') as f:\n"
        "    for ln in f:\n"
        "        ln=ln.strip()\n"
        "        if not ln: continue\n"
        "        try: rows.append(json.loads(ln))\n"
        "        except Exception: pass\n"
        "print('TOTAL',len(rows))\n"
        "if rows:\n"
        "    print('LAST_RECORD_FULL')\n"
        "    print(json.dumps(rows[-1],ensure_ascii=True)[:1800])\n"
        "    print('---- last 25 rounds ----')\n"
        "    for r in rows[-25:]:\n"
        "        d={}\n"
        "        for k in ('ts','time','t','src','ms','elapsed_ms','model',"
        "'tok_in','tok_out','tool','tools','gate','verdict','fell_back',"
        "'fallback','blocked','kind','route'):\n"
        "            if k in r: d[k]=r[k]\n"
        "        if 'blocks' in r:\n"
        "            try:\n"
        "                d['blocks']={k:v if isinstance(v,(int,float,str)) "
        "else len(str(v)) for k,v in list(r['blocks'].items())[:8]}\n"
        "            except Exception: pass\n"
        "        print(json.dumps(d,ensure_ascii=True)[:400])\n"
    )
    o, e = run(ssh, '%s -c "%s"' % (PY, code.replace('"', '\\"').replace("\n", "; ")))
    # exec_command with -c: embedded newlines inside quotes break cmd, use ;
    print("== TRACE ==")
    print(o[-4000:])
    if e.strip():
        print("ERR:", e[-500:])

    # ---- 1b. same but via temp file (multiline-safe) ----
    sftp = ssh.open_sftp()
    with sftp.open("C:/Vigil/_diag_trace.py", "w") as f:
        f.write(code)
    o, e = run(ssh, '%s C:\\Vigil\\_diag_trace.py' % PY, timeout=90)
    print("== TRACE(file) ==")
    print(o[-6000:])
    if e.strip():
        print("ERR:", e[-500:])

    # ---- 2. persona config + dirs ----
    for cmd in (
        'findstr /i "persona_file model" C:\\Vigil\\config\\config.json',
        'dir /b C:\\Vigil\\personas 2>nul',
        'dir /b C:\\Vigil\\config\\personas 2>nul',
        'dir /b C:\\Vigil\\data\\personas 2>nul',
        'dir /b C:\\Vigil\\data',
        'dir /b C:\\Vigil\\config',
    ):
        o, e = run(ssh, cmd, timeout=30)
        print("== %s ==" % cmd[:60])
        print((o or "").strip()[:1500])
        if e.strip() and "File Not Found" not in e:
            print("ERR:", e.strip()[:300])

    # ---- 3. persona content sizes ----
    code2 = (
        "import json,io,os,glob\n"
        "cands=[]\n"
        "for d in (r'C:\\Vigil\\personas',r'C:\\Vigil\\config\\personas',"
        "r'C:\\Vigil\\data\\personas'):\n"
        "    if os.path.isdir(d):\n"
        "        cands+=glob.glob(os.path.join(d,'*.json'))\n"
        "for p in cands:\n"
        "    try:\n"
        "        dd=json.load(io.open(p,encoding='utf-8'))\n"
        "        sizes={k:len(str(v)) for k,v in dd.items()}\n"
        "        print(p.encode('ascii','replace').decode(), json.dumps(sizes))\n"
        "        print('  keys:', list(dd.keys()))\n"
        "    except Exception as ex:\n"
        "        print(p.encode('ascii','replace').decode(), 'ERR', ex)\n"
    )
    with sftp.open("C:/Vigil/_diag_persona.py", "w") as f:
        f.write(code2)
    o, e = run(ssh, '%s C:\\Vigil\\_diag_persona.py' % PY, timeout=60)
    print("== PERSONA FILES ==")
    print(o[-2000:])
    if e.strip():
        print("ERR:", e[-400:])

    # cleanup diag files
    run(ssh, "del /q C:\\Vigil\\_diag_trace.py C:\\Vigil\\_diag_persona.py 2>nul")
    sftp.close()
    ssh.close()
    print("DIAG DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
