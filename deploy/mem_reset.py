# -*- coding: utf-8 -*- (ASCII only)
"""Reset cloud memory: fix memory_dir, park orphan idx into a backup dir."""
import json
import os
import shutil
import time

CFG = r"C:\Vigil\config\config.json"
MEM = r"C:\Vigil\data\memory"
STAMP = "reset_20261009"
BAK = os.path.join(MEM, "_orphan_" + STAMP)

# 1) backup config
shutil.copy2(CFG, CFG + ".bak_" + STAMP)
print("config backed up")

# 2) fix memory_dir
d = json.load(open(CFG, encoding="utf-8"))
old = d.get("memory_dir")
d["memory_dir"] = MEM
tmp = CFG + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
os.replace(tmp, CFG)
print("memory_dir:", repr(old), "->", repr(d["memory_dir"]))

# 3) park orphan idx dirs / loose index files into backup dir
os.makedirs(BAK, exist_ok=True)
moved = []
for name in os.listdir(MEM):
    p = os.path.join(MEM, name)
    if name.startswith("_bak") or name.startswith("_orphan"):
        continue
    # everything currently in data\memory is stale (json was stripped
    # on 10-08, these are orphan .idx dirs / loose index files)
    dst = os.path.join(BAK, name)
    if os.path.isdir(p):
        shutil.move(p, dst)
    else:
        shutil.copy2(p, dst)
        os.remove(p)
    moved.append(name)
print("parked:", sorted(moved))
print("dir now:", sorted(os.listdir(MEM)))
