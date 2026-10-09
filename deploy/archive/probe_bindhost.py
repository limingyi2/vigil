# -*- coding: utf-8 -*- (ASCII only)
"""Find bind_host traces in all config backups on cloud. ASCII only."""
import glob
import os

files = sorted(glob.glob(r"C:\Vigil\config\config.json*"))
for p in files:
    try:
        txt = open(p, encoding="utf-8").read()
        has = "bind_host" in txt
        val = ""
        if has:
            import json
            try:
                d = json.loads(txt)
                val = repr(d.get("bind_host"))
            except Exception:
                import re
                m = re.search(r'"bind_host"\s*:\s*([^,\n}]+)', txt)
                val = m.group(1) if m else "?"
        print(os.path.basename(p), "bind_host=", val if has else "<absent>")
    except Exception as e:
        print(os.path.basename(p), "ERR", e)
