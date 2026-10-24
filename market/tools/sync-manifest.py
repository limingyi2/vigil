#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""改完插件源码后，重算 manifest.json 里的版本号与逐文件 SHA-256。

市场安装时会按 manifest.json 的 files 逐条比对，任何一条 sha256 对不上就整包拒装。
改过 __init__.py / frontend.js / README 之类都必须跑一次，否则推上去用户装不上。

    python market/tools/sync-manifest.py                 # 只重算哈希
    python market/tools/sync-manifest.py --version 4.2.0 # 改版本 + 重算
    python market/tools/sync-manifest.py --check         # 只检查，不写

--check 适合放进发版前的自检：返回非 0 就说明包和清单不一致。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins" / "wallpaper"
ALLOWED_EXT = (".py", ".json", ".md", ".txt", ".js", ".css")


def sha256_of(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def collect() -> list[Path]:
    # manifest.json 记录的是别人的哈希，把自己列进去会自引用（写前算、写后就变）。
    files = [p for p in PLUGIN_DIR.rglob("*")
             if p.is_file() and p.suffix in ALLOWED_EXT
             and "__pycache__" not in p.parts and p.name != "manifest.json"]
    return sorted(files, key=lambda p: p.relative_to(PLUGIN_DIR).as_posix())


def main() -> int:
    parser = argparse.ArgumentParser(description="重算 wallpaper 插件清单的版本与哈希")
    parser.add_argument("--version", help="同时把版本号改成这个值（同步 version.json）")
    parser.add_argument("--check", action="store_true", help="只检查，不写文件")
    args = parser.parse_args()

    manifest_path = PLUGIN_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    version = args.version or manifest.get("version")
    if args.version:
        version_file = PLUGIN_DIR / "version.json"
        version_file.write_text(json.dumps({"version": version}, separators=(",", ":")),
                                encoding="utf-8")
        manifest["version"] = version

    files = collect()
    entries = [{"path": p.relative_to(PLUGIN_DIR).as_posix(), "sha256": sha256_of(p)}
               for p in files]
    before = {e["path"]: e["sha256"] for e in manifest.get("files") or []}
    changed = [e["path"] for e in entries if e["path"] in before and before[e["path"]] != e["sha256"]]
    added = [e["path"] for e in entries if e["path"] not in before]
    removed = [p for p in before if p not in {e["path"] for e in entries}]

    for path in changed:
        print("~ %s" % path)
    for path in added:
        print("+ %s" % path)
    for path in removed:
        print("- %s（文件已不存在）" % path)

    if args.check:
        if changed or added or removed or manifest.get("version") != version:
            print("\n清单与插件目录不一致，跑一次不带 --check 的同步")
            return 1
        print("清单与插件目录一致（version %s，%d 个文件）" % (version, len(entries)))
        return 0

    manifest["files"] = entries
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    total = sum(p.stat().st_size for p in files)
    print("\nversion = %s，%d 个文件，原始 %.2f MB"
          % (version, len(entries), total / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
