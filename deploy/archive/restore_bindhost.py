# -*- coding: utf-8 -*- (ASCII only)
"""Restore bind_host=0.0.0.0 (lost in the 10-08 gatefix cleanup).
App has been unable to reach her since then. ASCII only."""
import json
import shutil

CFG = r"C:\Vigil\config\config.json"
BAK = CFG + ".bak_bindhost_restore_20261009"

shutil.copy2(CFG, BAK)
print("backed up:", BAK)

d = json.load(open(CFG, encoding="utf-8"))
old = d.get("bind_host", "<absent>")
d["bind_host"] = "0.0.0.0"
assert d.get("brain_token"), "brain_token missing - refusing to bind 0.0.0.0"

tmp = CFG + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
import os
os.replace(tmp, CFG)
print("bind_host:", repr(old), "-> '0.0.0.0'")
print("brain_token present: yes")
