// 本地视频的播放通道：把用户选中的那一个文件，作成一条带票据的回环地址。
//
// 为什么不用 blob: URL —— 大文件拖进度条时 blob 要靠渲染进程整体持有格式，
// 而 HTML5 播放器真正依赖的是 HTTP 的 Range 语义。这里只做三件事：
// 票据只对 127.0.0.1 生效、只放行被选中的那一个文件、按 Range 回 206。
// 视频内容不经网络，也不进日志。
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

// 只认这些容器；扩展名不在表里就直接拒，别让任意文件顺带被读出来。
const MIME = {
  '.mp4': 'video/mp4',
  '.m4v': 'video/mp4',
  '.mov': 'video/quicktime',
  '.webm': 'video/webm',
  '.mkv': 'video/x-matroska',
  '.ogv': 'video/ogg',
  '.ogg': 'video/ogg',
  '.avi': 'video/x-msvideo',
};

const VIDEO_EXTENSIONS = Object.keys(MIME).map(ext => ext.slice(1));

function createVideoMedia({ idleMs = 6 * 60 * 60 * 1000 } = {}) {
  const tickets = new Map(); // ticket -> { file, size, type, expiresAt }
  let server = null;
  let port = 0;
  let opening = null;

  function listen() {
    if (server) return Promise.resolve(port);
    if (opening) return opening;
    opening = new Promise((resolve, reject) => {
      const candidate = http.createServer(handle);
      candidate.once('error', (error) => {
        opening = null;
        try { candidate.close(); } catch (e) { /* 还没起来 */ }
        reject(error);
      });
      candidate.listen(0, '127.0.0.1', () => {
        server = candidate;
        port = candidate.address().port;
        opening = null;
        resolve(port);
      });
    });
    return opening;
  }

  function fail(res, code, message) {
    if (res.headersSent) { res.destroy(); return; }
    res.writeHead(code, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' });
    res.end(message);
  }

  // 单区间 Range 足够：Chromium 拖进度条只会发 "bytes=N-" 或 "bytes=N-M"。
  function parseRange(header, size) {
    const match = /^bytes=(\d*)-(\d*)$/.exec(String(header || '').trim());
    if (!match) return null;
    const hasStart = match[1] !== '';
    const hasEnd = match[2] !== '';
    if (!hasStart && !hasEnd) return null;
    let start;
    let end;
    if (!hasStart) {                      // bytes=-N：最后 N 字节
      const suffix = Number(match[2]);
      if (!suffix) return null;
      start = Math.max(0, size - suffix);
      end = size - 1;
    } else {
      start = Number(match[1]);
      end = hasEnd ? Number(match[2]) : size - 1;
    }
    if (!Number.isFinite(start) || !Number.isFinite(end) || start > end || start >= size) return null;
    return { start, end: Math.min(end, size - 1) };
  }

  function stream(res, entry, range, headOnly) {
    const base = {
      'Content-Type': entry.type,
      'Accept-Ranges': 'bytes',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
    };
    if (range) {
      const length = range.end - range.start + 1;
      res.writeHead(206, {
        ...base,
        'Content-Range': 'bytes ' + range.start + '-' + range.end + '/' + entry.size,
        'Content-Length': String(length),
      });
      if (headOnly) { res.end(); return; }
      const reader = fs.createReadStream(entry.file, { start: range.start, end: range.end });
      reader.on('error', () => res.destroy());
      res.on('close', () => reader.destroy());
      reader.pipe(res);
      return;
    }
    res.writeHead(200, { ...base, 'Content-Length': String(entry.size) });
    if (headOnly) { res.end(); return; }
    const reader = fs.createReadStream(entry.file);
    reader.on('error', () => res.destroy());
    res.on('close', () => reader.destroy());
    reader.pipe(res);
  }

  function handle(req, res) {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      fail(res, 405, 'Method not allowed');
      return;
    }
    let local;
    try { local = new URL(req.url, 'http://127.0.0.1'); } catch (e) { fail(res, 400, 'Bad request'); return; }
    const match = /^\/v\/([a-f0-9]{48})$/.exec(local.pathname);
    const entry = match && tickets.get(match[1]);
    if (!entry) { fail(res, 404, 'Unknown media'); return; }
    if (entry.expiresAt <= Date.now()) { tickets.delete(match[1]); fail(res, 410, 'Expired media'); return; }
    entry.expiresAt = Date.now() + idleMs;
    // 播放器会先发一个无 Range 的探测请求，再按需 206。两者都要给对长度。
    const range = req.headers.range ? parseRange(req.headers.range, entry.size) : null;
    if (req.headers.range && !range) {
      res.writeHead(416, {
        'Content-Type': 'text/plain; charset=utf-8',
        'Content-Range': 'bytes */' + entry.size,
        'Accept-Ranges': 'bytes',
      });
      res.end('Range not satisfiable');
      return;
    }
    stream(res, entry, range, req.method === 'HEAD');
  }

  // 把一个真实路径登记成可播地址。stat 失败就别谎称成功。
  async function share(filePath) {
    const resolved = path.resolve(String(filePath || ''));
    const type = MIME[path.extname(resolved).toLowerCase()];
    if (!type) throw new Error('不是支持的视频格式：' + (path.extname(resolved) || '无扩展名'));
    let stat;
    try { stat = await fs.promises.stat(resolved); } catch (e) { throw new Error('读不到这个文件：' + e.message); }
    if (!stat.isFile()) throw new Error('选中的不是文件');
    if (!stat.size) throw new Error('文件是空的');
    await listen();
    const token = crypto.randomBytes(24).toString('hex');
    tickets.set(token, { file: resolved, size: stat.size, type, expiresAt: Date.now() + idleMs });
    return {
      ok: true,
      path: resolved,
      name: path.basename(resolved),
      size: stat.size,
      url: 'http://127.0.0.1:' + port + '/v/' + token,
    };
  }

  function close() {
    tickets.clear();
    if (opening) { try { opening.catch(() => {}); } catch (e) { /* 忽略 */ } opening = null; }
    if (server) { try { server.close(); } catch (e) { /* 已关 */ } server = null; port = 0; }
  }

  return { share, close, extensions: VIDEO_EXTENSIONS };
}

module.exports = { createVideoMedia, VIDEO_EXTENSIONS };
