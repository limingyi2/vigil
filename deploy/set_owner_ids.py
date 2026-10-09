# -*- coding: utf-8 -*- (ASCII only)
"""Add owner_ids to the cloud config (runtime reads config\\config.json ONLY).
Idempotent; asserts brain_token / bind_host survive the rewrite.
The real wxid comes from env ZX_OWNER_WXID (never committed).
ASCII only. Run on the cloud box."""
import json
import os
import shutil

CFG = r"C:\Vigil\config\config.json"
BAK = CFG + ".bak_m4_owner_20261009"
OWNERS = [os.environ["ZX_OWNER_WXID"].strip()]

if not os.path.exists(BAK):
    shutil.copy2(CFG, BAK)
    print("backed up:", BAK)
else:
    print("backup already exists:", BAK)

d = json.load(open(CFG, encoding="utf-8"))
old = d.get("owner_ids")
d["owner_ids"] = OWNERS

# guard keys that must survive (bind_host was lost this way on 10-08)
assert d.get("brain_token"), "brain_token missing - refusing to write"
assert d.get("bind_host") == "0.0.0.0", "bind_host lost - refusing to write"

tmp = CFG + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
os.replace(tmp, CFG)
print("owner_ids:", old, "->", OWNERS)
print("brain_token present: yes / bind_host ok")
