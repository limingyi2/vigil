"""Stream plugin media without loading whole videos into memory; support seeking."""
import os
import re


def send_file(handler, fpath, mime):
    with open(fpath, "rb") as source:
        size = os.fstat(source.fileno()).st_size
        start, end, status = 0, size - 1, 200
        requested = handler.headers.get("Range", "")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested.strip())
            valid = match is not None and any(match.groups()) and size > 0
            if valid:
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                else:
                    start = max(0, size - int(last))
                valid = 0 <= start <= end < size
            if not valid:
                handler.send_response(416)
                handler.send_header("Content-Range", "bytes */%d" % size)
                handler.send_header("Content-Length", "0")
                handler._cors()
                handler.end_headers()
                return
            status = 206
        remaining = max(0, end - start + 1)
        handler.send_response(status)
        handler.send_header("Content-Type", mime)
        handler.send_header("Content-Length", str(remaining))
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Cache-Control", "private, no-store")
        if status == 206:
            handler.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        handler._cors()
        handler.end_headers()
        source.seek(start)
        while remaining:
            chunk = source.read(min(256 * 1024, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)
