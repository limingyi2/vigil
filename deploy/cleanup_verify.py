# -*- coding: utf-8 -*- (ASCII only)
"""Cleanup probes + verify features key. ASCII only."""
import glob
import json
import os
import urllib.request

BASE = "http://127.0.0.1:8788"
for p in (r"C:\Vigil\_verify_m2.py", r"C:\Vigil\_probe_mem.py"):
    try:
        os.remove(p)
        print("removed", p)
    except OSError as e:
        print("skip", p, e)

tok = json.load(open(r"C:\Vigil\config\config.json",
                     encoding="utf-8"))["brain_token"]
r = urllib.request.Request(BASE + "/api/sync/behavior",
                           headers={"Authorization": "Bearer " + tok})
d = json.load(urllib.request.urlopen(r, timeout=8))
feat = (d.get("data") or {}).get("features") or {}
print("behavior version:", d.get("version"))
print("rand_img_moment in features:", "rand_img_moment" in feat)
print("features keys:", sorted(feat.keys()))
