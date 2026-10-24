#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Wallpaper Engine 创意工坊的视频壁纸转成适合上架的 mp4。

创意工坊里的视频壁纸动辄 4K / 200~900 MB，直接传上去别人下一个壁纸要等半天，
而且插件对单张有 256 MB 上限。这个脚本统一压到 1080p 以内、H.264、无音轨、
moov 前置（+faststart，边下边能播），通常一张 10~25 MB。

用法：

    # 先看库里有啥
    python market/tools/prepare-walls.py --list

    # 按创意工坊 id 转（可多个）
    python market/tools/prepare-walls.py --out _tmp/walls 3571932651 3571946323

    # 也可以直接给文件
    python market/tools/prepare-walls.py --out _tmp/walls D:/some/clip.mp4
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_WORKSHOP = Path("F:/SteamLibrary/steamapps/workshop/content/431960")
DEFAULT_OUT = Path("_tmp/walls")
VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v"}


def tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    raise SystemExit("找不到 %s，先装 ffmpeg 并加进 PATH" % name)


def probe(path: Path) -> dict:
    """ffprobe 一次拿到宽高、时长、帧率，后面算目标尺寸和体积预估都用它。"""
    result = subprocess.run(
        [tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True)
    try:
        data = json.loads(result.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        num, _, den = str(stream.get("r_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
        return {
            "width": int(stream.get("width") or 0),
            "height": int(stream.get("height") or 0),
            "duration": float((data.get("format") or {}).get("duration") or 0),
            "fps": fps,
        }
    except Exception:
        return {"width": 0, "height": 0, "duration": 0.0, "fps": 0.0}


def target_size(width: int, height: int, max_h: int) -> tuple[int, int]:
    """等比缩到 max_h 以内，宽高都取偶数（H.264 4:2:0 的硬要求）。"""
    if not width or not height:
        return 0, 0
    scale = min(1.0, max_h / float(height))
    return max(2, int(round(width * scale / 2)) * 2), max(2, int(round(height * scale / 2)) * 2)


def find_video(folder: Path) -> Path | None:
    files = [p for p in sorted(folder.iterdir()) if p.is_file() and p.suffix.lower() in VIDEO_EXT]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_size)


def list_workshop(root: Path) -> None:
    rows = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        video = find_video(folder)
        if not video:
            continue
        meta: dict = {}
        try:
            meta = json.loads((folder / "project.json").read_text(encoding="utf-8-sig"))
        except Exception:
            pass
        info = probe(video)
        rows.append((folder.name, str(meta.get("title") or "")[:34],
                     str(meta.get("contentrating") or ""), video.stat().st_size / 1048576,
                     info["duration"], "%dx%d" % (info["width"], info["height"])))
    if not rows:
        sys.exit("这个目录里没有视频型壁纸：%s" % root)
    print("%-12s %-36s %-13s %9s %7s  %s" % ("创意工坊 id", "标题", "分级", "体积", "时长", "分辨率"))
    for wid, title, rating, mb, duration, res in rows:
        print("%-12s %-36s %-13s %7.0f MB %6.0fs  %s"
              % (wid, title, rating, mb, duration, res))
    print("\n共 %d 个视频型壁纸（scene 型不能直接转，要在 Wallpaper Engine 里录屏）" % len(rows))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把壁纸视频压成适合上架的 1080p mp4",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：python market/tools/prepare-walls.py --list")
    parser.add_argument("items", nargs="*", help="创意工坊 id 或视频文件路径")
    parser.add_argument("--list", action="store_true", help="只列出可转的视频型壁纸")
    parser.add_argument("--workshop", default=str(DEFAULT_WORKSHOP), help="创意工坊目录")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    parser.add_argument("--height", type=int, default=1080, help="目标高度上限，默认 1080")
    parser.add_argument("--fps", type=int, default=30, help="目标帧率上限，默认 30")
    parser.add_argument("--crf", type=int, default=25, help="质量因子，越大越糊越小，默认 25")
    parser.add_argument("--force", action="store_true", help="已有输出也重转")
    args = parser.parse_args()

    workshop = Path(args.workshop)
    if args.list:
        list_workshop(workshop)
        return 0
    if not args.items:
        parser.error("要么给 --list，要么给至少一个 id / 文件路径")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = tool("ffmpeg")
    done = 0
    for raw in args.items:
        source = Path(raw)
        if not source.exists():
            candidate = find_video(workshop / raw)
            if not candidate:
                print("✗ %s：既不是文件，在创意工坊里也没找到视频" % raw)
                continue
            source = candidate
        info = probe(source)
        if not info["width"]:
            print("✗ %s：ffprobe 读不出画面尺寸（不是视频？）" % source.name)
            continue
        width, height = target_size(info["width"], info["height"], args.height)
        # 创意工坊里的源文件名常常是 `snapdouyin.app-xxx` 这类垃圾名，上架时会
        # 直接变成下载地址。在 workshop/<id>/ 下就直接用 id 命名，干净且稳定。
        if source.parent.parent == workshop:
            stem = source.parent.name
        else:
            stem = source.stem[:60]
        dest = out_dir / (stem + ".mp4")
        if dest.is_file() and not args.force and dest.stat().st_mtime >= source.stat().st_mtime:
            print("· %s 已存在且比源文件新，跳过（--force 可重转）" % dest.name)
            continue

        # 无音轨：壁纸默认静音，留着音轨只是白占体积。
        command = [
            ffmpeg, "-v", "error", "-y", "-i", str(source),
            "-vf", "scale=%d:%d:flags=lanczos" % (width, height),
            "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf),
            "-pix_fmt", "yuv420p", "-r", str(args.fps),
            "-an", "-movflags", "+faststart", str(dest),
        ]
        print("→ %s  %dx%d %ds → %dx%d" % (source.name, info["width"], info["height"],
                                           int(info["duration"]), width, height))
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0 or not dest.is_file():
            print("✗ 转码失败：%s" % (result.stderr or "").strip()[:300])
            continue
        before = source.stat().st_size / 1048576
        after = dest.stat().st_size / 1048576
        print("  ✓ %s  %.1f MB → %.1f MB（%d%%）" % (dest.name, before, after,
                                                     round(after * 100 / max(before, 0.01))))
        done += 1

    print("\n完成 %d 个，输出目录 %s" % (done, out_dir))
    if done:
        print("下一步：gh release upload walls-v1 %s/*.mp4 --repo <user>/vigil-walls --clobber"
              % out_dir.as_posix())
    return 0 if done or not args.items else 1


if __name__ == "__main__":
    sys.exit(main())
