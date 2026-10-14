# -*- coding: utf-8 -*-
"""安全的聊天附件存储与读取。

附件不进入聊天 JSON。客户端按固定大小分块上传，服务端只允许受支持的
媒体/文档类型，并把最终文件和脱敏元数据放在 data/upload/attachments 下。
"""
import json
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import unquote

import paths


MAX_ATTACHMENT_SIZE = 256 * 1024 * 1024
MAX_CHUNK_SIZE = 2 * 1024 * 1024
MAX_NAME_LENGTH = 160
_ID_RE = re.compile(r"^att_[A-Za-z0-9_-]{8,80}$")
_LOCK = threading.RLock()

ALLOWED_EXTENSIONS = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown",
    ".csv": "text/csv", ".json": "application/json",
}


def _root():
    root = Path(paths.UPLOAD_DIR) / "attachments"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_id(value):
    value = str(value or "").strip()
    return value if _ID_RE.fullmatch(value) else ""


def _safe_name(value):
    value = unquote(str(value or "")).replace("\\", "/")
    value = value.rsplit("/", 1)[-1].replace("\x00", "").strip()
    if not value:
        return "attachment"
    return value[:MAX_NAME_LENGTH]


def validate_upload(name, mime, total, offset, chunk_size):
    name = _safe_name(name)
    ext = Path(name).suffix.lower()
    expected = ALLOWED_EXTENSIONS.get(ext)
    if not expected:
        raise ValueError("不支持的附件类型（仅支持图片、MP4/MOV/WebM、PDF、TXT 等）")
    try:
        total = int(total)
        offset = int(offset)
        chunk_size = int(chunk_size)
    except (TypeError, ValueError):
        raise ValueError("附件分块参数无效")
    if total <= 0 or total > MAX_ATTACHMENT_SIZE:
        raise ValueError("附件超过 256 MB 大小上限")
    if offset < 0 or offset > total or chunk_size <= 0 or chunk_size > MAX_CHUNK_SIZE:
        raise ValueError("附件分块大小无效")
    if offset + chunk_size > total:
        raise ValueError("附件分块超出声明大小")
    # 浏览器有时把未知扩展名标为 octet-stream；扩展名仍是服务端的主判断。
    mime = str(mime or "").split(";", 1)[0].strip().lower()
    if mime and mime != "application/octet-stream" and mime != expected:
        # text/* 的细分 MIME 允许按扩展名收口，不允许把视频伪装成文档。
        if not (expected.startswith("text/") and mime.startswith("text/")):
            raise ValueError("附件类型与文件扩展名不匹配")
    return name, expected, total, offset, chunk_size, ext


def _meta_path(root, aid):
    return root / (aid + ".json")


def _part_path(root, aid):
    return root / (aid + ".part")


def _final_path(root, aid, ext):
    return root / (aid + ext)


def _read_meta(root, aid):
    p = _meta_path(root, aid)
    try:
        with p.open("r", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("id") != aid:
        return None
    return d


def append_chunk(aid, name, mime, total, offset, data):
    """写入一个分块，返回当前进度或完成后的脱敏附件元数据。"""
    aid = _safe_id(aid)
    if not aid:
        raise ValueError("附件 ID 无效")
    data = bytes(data or b"")
    name, expected, total, offset, chunk_size, ext = validate_upload(
        name, mime, total, offset, len(data))
    if len(data) != chunk_size:
        raise ValueError("附件分块长度不正确")
    root = _root()
    with _LOCK:
        meta = _read_meta(root, aid)
        if meta is None:
            if offset != 0:
                raise ValueError("附件必须从第一个分块开始")
            meta = {
                "id": aid, "name": name, "mime": expected, "size": total,
                "ext": ext, "received": 0, "created_at": int(time.time()),
            }
            _meta_path(root, aid).write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        else:
            if (meta.get("name") != name or meta.get("mime") != expected
                    or int(meta.get("size") or 0) != total):
                raise ValueError("附件分块元数据不一致")
            if offset != int(meta.get("received") or 0):
                raise ValueError("附件分块偏移不连续")
        part = _part_path(root, aid)
        with part.open("ab") as f:
            f.write(data)
        meta["received"] = offset + len(data)
        if meta["received"] == total:
            final = _final_path(root, aid, ext)
            os.replace(str(part), str(final))
            meta["path"] = final.name
            meta["complete"] = True
            meta.pop("received", None)
            _meta_path(root, aid).write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            return public_meta(meta)
        _meta_path(root, aid).write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return {"id": aid, "received": meta["received"],
                "size": total, "complete": False}


def public_meta(meta):
    if not meta or not meta.get("complete"):
        return None
    return {"id": str(meta["id"]), "name": str(meta["name"]),
            "mime": str(meta["mime"]), "size": int(meta["size"]),
            "complete": True,
            "kind": "video" if str(meta["mime"]).startswith("video/")
                     else ("image" if str(meta["mime"]).startswith("image/")
                           else "file")}


def get(aid):
    aid = _safe_id(aid)
    if not aid:
        return None
    root = _root()
    with _LOCK:
        meta = _read_meta(root, aid)
    if not meta or not meta.get("complete"):
        return None
    path = root / str(meta.get("path") or "")
    try:
        if path.resolve().parent != root.resolve() or not path.is_file():
            return None
    except OSError:
        return None
    out = dict(meta)
    out["file_path"] = str(path)
    return out


def capabilities(api_config):
    vision_cfg = api_config.get("vision") if isinstance(api_config, dict) else {}
    if not isinstance(vision_cfg, dict):
        vision_cfg = {}
    model = str(vision_cfg.get("model") or "").strip()
    enabled = vision_cfg.get("enabled", True) is not False
    return {"ok": True, "image": bool(enabled and model), "video": False,
            "video_frames": bool(_find_tool("ffmpeg")),
            "video_note": "当前模型不直接读取视频；可在确认后提取最多 3 个关键帧。关键帧兼容处理仅使用本机已有 FFmpeg，不会自动安装。"}


def _find_tool(name):
    """只查找用户已配置的本机工具，不下载或安装依赖。"""
    env_name = "VIGIL_FFMPEG" if name == "ffmpeg" else "VIGIL_FFPROBE"
    configured = str(os.environ.get(env_name) or "").strip()
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(name)
    return str(found) if found else ""


def _probe_duration(ffprobe, path):
    if not ffprobe:
        return 0.0
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            timeout=15, creationflags=flags,
        )
        return max(0.0, float(out.stdout.decode("utf-8", errors="replace").strip() or 0))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def extract_frames(aid, count=3):
    """用本机已有 FFmpeg 生成少量 JPEG 关键帧并作为附件保存。"""
    rec = get(aid)
    if not rec or not str(rec.get("mime") or "").startswith("video/"):
        raise ValueError("视频附件不存在或尚未上传完成")
    ffmpeg = _find_tool("ffmpeg")
    if not ffmpeg:
        raise ValueError("未找到本机 FFmpeg；原视频已保存，暂无法提取关键帧")
    ffprobe = _find_tool("ffprobe")
    duration = _probe_duration(ffprobe, rec["file_path"])
    n = max(1, min(3, int(count or 3)))
    if duration > 0:
        times = [max(0.05, min(duration - 0.05, duration * (i + 1) / (n + 1)))
                 for i in range(n)]
    else:
        # 没有 ffprobe 时仍尝试少量固定时间点，失败不会影响原视频。
        times = [0.5 * (i + 1) for i in range(n)]
    flags = 0x08000000 if os.name == "nt" else 0
    stem = Path(rec["name"]).stem[:70] or "video"
    frames = []
    for index, timestamp in enumerate(times, 1):
        try:
            out = subprocess.run(
                [ffmpeg, "-loglevel", "error", "-ss",
                 "%.3f" % timestamp, "-i", rec["file_path"], "-frames:v", "1",
                 "-vf", "scale=768:-1", "-an", "-f", "image2pipe",
                 "-vcodec", "mjpeg", "pipe:1"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                timeout=45, creationflags=flags,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        blob = bytes(out.stdout or b"")
        if out.returncode != 0 or not blob or len(blob) > MAX_CHUNK_SIZE:
            continue
        frame_id = "att_" + uuid.uuid4().hex
        try:
            frames.append(append_chunk(
                frame_id, "%s.frame-%d.jpg" % (stem, index), "image/jpeg",
                len(blob), 0, blob))
        except ValueError:
            continue
    if not frames:
        raise ValueError("本机 FFmpeg 无法解析此视频；原视频已保存")
    return frames


def context_for(meta_list, api_config):
    """生成有限长度、可审计的模型上下文；不把二进制塞进聊天请求。"""
    lines = []
    first_image = None
    for item in meta_list or []:
        rec = get(item.get("id") if isinstance(item, dict) else item)
        if not rec:
            raise ValueError("附件不存在或尚未上传完成")
        name, mime, size = rec["name"], rec["mime"], rec["size"]
        lines.append("附件：%s（%s，%s 字节）" % (name, mime, size))
        if mime.startswith("image/"):
            if first_image is None:
                first_image = rec
            try:
                import vision
                with open(rec["file_path"], "rb") as f:
                    desc = vision.describe_bytes(f.read(8 * 1024 * 1024), api_config)
                lines.append("图片描述：" + (desc or "当前视觉模型没有返回描述。"))
            except Exception:
                lines.append("图片内容未能分析，仅保留附件元数据。")
        elif mime.startswith("text/") or mime == "application/json":
            try:
                with open(rec["file_path"], "rb") as f:
                    raw = f.read(128 * 1024)
                text = raw.decode("utf-8", errors="replace")
                lines.append("文件内容（前 128 KB）：\n" + text[:12000])
            except OSError:
                lines.append("文件内容读取失败，仅保留附件元数据。")
        elif mime == "application/pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(rec["file_path"])
                text = "\n".join((p.extract_text() or "") for p in reader.pages[:3])
                lines.append("PDF 文本摘录（前 3 页）：\n" + text[:12000])
            except Exception:
                lines.append("当前环境无法提取 PDF 文本，仅保留附件元数据。")
        elif mime.startswith("video/"):
            lines.append("当前模型不支持直接分析完整视频；未读取视频内容。")
    return "\n".join(lines), first_image
