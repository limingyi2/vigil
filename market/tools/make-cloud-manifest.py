#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 wallpaper 插件的在线壁纸清单（cloud-walls.json）。

画面不随插件打包（插件市场安装包 4 MB 上限，一条视频就顶穿了），而是放在
GitHub Release 上，用户点卡片时才下载。这份清单负责告诉插件：从哪个地址取、
文件多大、SHA-256 是多少、缩略图长什么样。

先把文件传到 Release，再跑：

    python market/tools/make-cloud-manifest.py \\
        --base https://github.com/<user>/<repo>/releases/download/walls-v1 \\
        --credit "Wallpaper Engine 创意工坊" \\
        _tmp/walls/*.mp4

脚本会：① 用 ffmpeg 抽首帧做内嵌小缩略图（不额外联网）② 算 SHA-256 与字节数
③ 用插件自己的 _clean_cloud_entry 复核一遍 ④ 合并写回清单。

⚠️ 换过一次视频就必须重跑：SHA-256 对不上时插件会拒收整个下载。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_DIR = HERE.parent / "plugins" / "wallpaper"
DEFAULT_OUT = PLUGIN_DIR / "cloud-walls.json"
DEFAULT_WORKSHOP = Path("F:/SteamLibrary/steamapps/workshop/content/431960")

VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
GIF_EXT = {".gif"}
THUMB_WIDTH = 240      # 卡片是 16:9 小图，240 宽足够且单张约 6~10 KB


def load_plugin():
    """加载同仓库的插件模块，借用它自己的清单校验函数做体检。"""
    path = PLUGIN_DIR / "__init__.py"
    if not path.is_file():
        sys.exit("找不到插件模块：%s" % path)
    spec = importlib.util.spec_from_file_location("_vigil_wallpaper_plugin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def guess_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in GIF_EXT:
        return "gif"
    if ext in IMAGE_EXT:
        return "image"
    sys.exit("不认识的扩展名：%s（支持 %s）"
             % (path.name, "、".join(sorted(VIDEO_EXT | IMAGE_EXT | GIF_EXT))))


def ffmpeg_exe() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe")
    return str(local) if local.is_file() else None


def encode_thumb(source: Path) -> str:
    """把一张图缩到卡片宽度并压成 jpeg data URI。"""
    try:
        from PIL import Image
    except ImportError:
        print("  ! 没有 Pillow，跳过缩略图（卡片会显示成黑块）")
        return ""
    try:
        with Image.open(source) as img:
            img = img.convert("RGB")
            img.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 4))   # 只限宽，保持原比例
            buffer = io.BytesIO()
            img.save(buffer, "JPEG", quality=62, optimize=True)
    except Exception as exc:
        print("  ! 缩略图压缩失败：%s" % exc)
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def thumb_from_video(path: Path) -> str:
    exe = ffmpeg_exe()
    if not exe:
        print("  ! 没有 ffmpeg，跳过缩略图（卡片会显示成黑块）")
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "thumb.png"
        # 先从第 1 秒抽（避开片头黑场），抽不到再退回第一帧。
        # 这里不交给 ffmpeg 缩放：部分 Windows 构建不认 scale=W:-2，
        # 解码出原帧再让 Pillow 缩，跨版本稳。
        for seek in ("1", "0"):
            cmd = [exe, "-v", "error", "-y", "-ss", seek, "-i", str(path),
                   "-frames:v", "1", str(out)]
            subprocess.run(cmd, capture_output=True)
            if out.is_file() and out.stat().st_size > 512:
                return encode_thumb(out)
            out.unlink(missing_ok=True)
    print("  ! ffmpeg 抽帧失败，跳过缩略图")
    return ""


def thumb_from_image(path: Path) -> str:
    return encode_thumb(path)


def make_thumb(path: Path, kind: str, override: str) -> str:
    if override:
        return override
    return thumb_from_video(path) if kind == "video" else thumb_from_image(path)


def digest(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def workshop_title(stem: str, root: Path) -> str:
    """转码后的文件名就是创意工坊 id，标题得回原目录的 project.json 里找。"""
    if not stem.isdigit() or not root.is_dir():
        return ""
    try:
        meta = json.loads((root / stem / "project.json").read_text(encoding="utf-8-sig"))
    except Exception:
        return ""
    return str(meta.get("title") or "")[:60]


def build_entry(path: Path, wid: str, base: str, title: str, credit: str,
                poster: str, limit: int) -> dict:
    kind = guess_type(path)
    sha, size = digest(path)
    if size <= 0:
        sys.exit("%s 是空文件" % path.name)
    if size > limit:
        sys.exit("%s 有 %.1f MB，超过插件的单张上限 %.0f MB"
                 % (path.name, size / 1048576, limit / 1048576))
    url = base.rstrip("/") + "/" + urllib.parse.quote(path.name)
    return {
        "id": wid,
        "title": title or path.stem,
        "type": kind,
        "file": wid + path.suffix.lower(),     # 本机落盘名，与远端文件名解耦
        "url": url,
        "sha256": sha,
        "bytes": size,
        "poster": make_thumb(path, kind, poster),
        "credit": credit,
    }


def check_remote(url: str, expect_bytes: int, hosts: set[str]) -> str:
    """HEAD 一下确认文件真在线上、大小对得上；返回空串表示通过。"""
    request = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "Vigil-Wallpaper/4.2"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            final = urllib.parse.urlparse(response.geturl())
            if (final.hostname or "").lower() not in hosts:
                return "重定向到了未授权主机 %s" % (final.hostname or "?")
            if response.status != 200:
                return "HTTP %s" % response.status
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) != expect_bytes:
                return "线上大小 %s 字节，本地 %d 字节（传的是旧文件？）" % (declared, expect_bytes)
    except urllib.error.HTTPError as exc:
        return "HTTP %s（Release 里没有这个文件，或 tag 写错了）" % exc.code
    except Exception as exc:
        return "%s: %s" % (type(exc).__name__, exc)
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成 wallpaper 插件的在线壁纸清单",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n  python market/tools/make-cloud-manifest.py \\\n"
               "    --base https://github.com/user/repo/releases/download/walls-v1 \\\n"
               "    _tmp/walls/*.mp4")
    parser.add_argument("files", nargs="+", help="要上架的壁纸文件（视频/图片）")
    parser.add_argument("--base", required=True,
                        help="Release 资产的前缀 URL，如 .../releases/download/walls-v1")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="清单输出路径")
    parser.add_argument("--id-start", type=int, default=92001, help="起始 id（默认 92001）")
    parser.add_argument("--title", action="append", default=[],
                        metavar="文件=标题", help="给某个文件指定卡片标题，可重复")
    parser.add_argument("--credit", default="", help="统一署名，显示在卡片上")
    parser.add_argument("--poster", default="", help="统一缩略图（data URI 或 URL），不给就自动抽帧")
    parser.add_argument("--workshop", default=str(DEFAULT_WORKSHOP),
                        help="创意工坊目录；文件名是 id 时从这里取标题")
    parser.add_argument("--replace", action="store_true", help="清空旧条目，只保留这次给的")
    parser.add_argument("--verify", action="store_true", help="联网 HEAD 校验线上文件（推荐上传后带上）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    args = parser.parse_args()

    plugin = load_plugin()
    out_path = Path(args.out)

    parsed_base = urllib.parse.urlparse(args.base)
    if parsed_base.scheme not in plugin.CLOUD_SCHEMES:
        sys.exit("--base 必须是 %s（现在是 %r）"
                 % ("/".join(sorted(plugin.CLOUD_SCHEMES)), parsed_base.scheme or "空"))
    if (parsed_base.hostname or "").lower() not in plugin.CLOUD_HOSTS:
        sys.exit("--base 的主机 %r 不在插件白名单里。可用：%s"
                 % (parsed_base.hostname, "、".join(sorted(plugin.CLOUD_HOSTS))))

    # 读旧清单：默认合并，同 id 或同 url 的条目被这次的新版本覆盖。
    doc: dict = {"version": 1, "items": []}
    if out_path.is_file():
        try:
            doc = json.loads(out_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            sys.exit("现有清单解析失败，先修好再跑：%s" % exc)
    old_items = [] if args.replace else [x for x in (doc.get("items") or [])
                                         if isinstance(x, dict)]
    kept = [x for x in old_items
            if isinstance(x.get("id"), str) and x["id"].isdigit()]

    titles = {}
    for spec in args.title:
        name, _, value = spec.partition("=")
        if not name or not value:
            sys.exit("--title 要写成 文件=标题：%r" % spec)
        titles[Path(name).name] = value

    next_id = max([args.id_start] + [int(x["id"]) + 1 for x in kept])
    fresh: list[dict] = []
    for raw in args.files:
        path = Path(raw)
        if not path.is_file():
            sys.exit("找不到文件：%s" % path)
        # 同一个文件重复上架时沿用旧 id，免得用户已经缓存的那份变孤儿。
        sha, size = digest(path)
        reused = next((x for x in kept
                       if x.get("sha256") == sha and x.get("bytes") == size), None)
        wid = str(reused["id"]) if reused else str(next_id)
        if not reused:
            next_id += 1
        # 显式 --title 优先；否则文件名是创意工坊 id 时自动取那边的原始标题。
        title = titles.get(path.name) or workshop_title(path.stem, Path(args.workshop))
        entry = build_entry(path, wid, args.base, title,
                            args.credit, args.poster, plugin.CLOUD_MAX_BYTES)
        print("%-28s id=%s  %.1f MB  %s"
              % (path.name, wid, entry["bytes"] / 1048576,
                 "复用已有 id" if reused else "新增"))
        fresh.append(entry)

    fresh_ids = {x["id"] for x in fresh}
    fresh_urls = {x["url"] for x in fresh}
    items = [x for x in kept if x["id"] not in fresh_ids and x.get("url") not in fresh_urls]
    items.extend(fresh)
    items.sort(key=lambda x: int(x["id"]))

    # 用插件自己的校验函数过一遍：清单写得再好看，过不了这道闸等于没写。
    problems = []
    for entry in items:
        cleaned = plugin._clean_cloud_entry(entry)
        if cleaned is None:
            problems.append("id=%s 通不过插件校验（检查 url 主机/哈希/体积/type）" % entry.get("id"))
        elif cleaned["sha256"] != entry["sha256"] or cleaned["bytes"] != entry["bytes"]:
            problems.append("id=%s 的哈希或体积被插件改写了" % entry.get("id"))
    if problems:
        for line in problems:
            print("✗ " + line)
        return 1

    if args.verify:
        print("\n联网校验线上文件…")
        bad = 0
        for entry in fresh:
            err = check_remote(entry["url"], entry["bytes"], plugin.CLOUD_HOSTS)
            if err:
                bad += 1
                print("  ✗ %s → %s" % (entry["url"], err))
            else:
                print("  ✓ %s" % entry["url"])
        if bad:
            return 1

    doc["version"] = 1
    doc.setdefault("_comment", "")
    doc["items"] = items
    payload = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    payload_kb = len(payload.encode("utf-8")) / 1024
    if payload_kb > 1536:
        # 安装器单文件上限 2 MB（brain/plugin_ops.py 的 MAX_FILE_BYTES），
        # 超了整包会被拒装，用户连壁纸库都进不去。
        print("\n! 清单已到 %.0f KB，逼近安装器 2 MB 单文件上限。" % payload_kb)
        print("  缩略图占大头：调小 THUMB_WIDTH、降 quality，或减少条目。")

    if args.dry_run:
        print("\n--- dry-run，未写入 ---\n" + payload)
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(payload, encoding="utf-8")
    thumb_kb = sum(len(x.get("poster") or "") for x in items) / 1024
    print("\n已写入 %s" % out_path)
    print("共 %d 条，缩略图占 %.0f KB，清单 %.0f KB"
          % (len(items), thumb_kb, payload_kb))
    return 0


if __name__ == "__main__":
    sys.exit(main())
