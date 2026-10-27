// 本地视频的播放通道：把用户选中的那一个文件，作成一条带票据的回环地址。
//
// 为什么不用 blob: URL —— 大文件拖进度条时 blob 要靠渲染进程整体持有格式，
// 而 HTML5 播放器真正依赖的是 HTTP 的 Range 语义。这里只做三件事：
// 票据只对 127.0.0.1 生效、只放行被选中的那一个文件、按 Range 回 206。
// 视频内容不经网络，也不进日志。
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { spawn } = require('child_process');

// 只认这些容器；扩展名不在表里就直接拒，避免任意文件顺带被读出来。
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

// ── 为什么需要这一层
// Chromium 只认 MP4/MOV 里的 H.264 / AV1 / VP9，以及 WebM 里的 VP8/VP9/AV1。
// 用户随手一个 MKV（哪怕是 H.264）或 HEVC 的 MP4，播放器都会直接报
// 「无法播放」。与其让人自己去转码，不如在打开时先看一眼真实编码：
//   能播         → 原样给地址
//   封装不对     → -c copy 重封装成 MP4（几秒，画质不变）
//   编码不认     → 转 H.264（带进度，慢但能看）
// 找不到 ffmpeg 就只能照原样播，并如实告诉用户为什么可能放不了。
const PREPARE_DIR = path.join(os.tmpdir(), 'vigil-media');
const FFMPEG_MAX_WAIT = 12 * 60 * 1000;

// Chromium 能解的编码组合。容器用 ffmpeg 报的格式来判，不看扩展名（.mkv 里也可能是 webm）。
const PLAYABLE = {
  mp4: ['h264', 'av1', 'vp9', 'vp8'],
  mov: ['h264', 'av1', 'vp9', 'vp8'],
  webm: ['vp8', 'vp9', 'av1'],
  ogg: ['theora', 'vp8'],
};

function locateFfmpeg(egoRoot) {
  const tried = [];
  const ok = (p) => {
    try { return p && fs.statSync(p).isFile(); } catch (e) { return false; }
  };
  if (ok(process.env.VIGIL_FFMPEG)) return process.env.VIGIL_FFMPEG;
  tried.push('VIGIL_FFMPEG');
  // 随包位置（将来若要一起发，把 exe 放这儿就行）
  const bundled = path.join(process.resourcesPath || '', 'ffmpeg.exe');
  if (ok(bundled)) return bundled;
  // EgoAgent 环境里 imageio-ffmpeg 自带一份（视频问答本来就依赖它）
  const root = egoRoot || process.env.VIGIL_EGO_ROOT || 'F://EgoAgent-Eval';
  const bin = path.join(root, '.venv', 'Lib', 'site-packages', 'imageio_ffmpeg', 'binaries');
  try {
    const hit = fs.readdirSync(bin).filter((n) => /^ffmpeg.*\.exe$/i.test(n)).sort().pop();
    if (hit && ok(path.join(bin, hit))) return path.join(bin, hit);
  } catch (e) { /* 没有就算了 */ }
  return '';
}

// ffmpeg -i 会把信息打到 stderr，退出码非 0 是正常的（没给输出文件）
function inspect(ffmpeg, file) {
  return new Promise((resolve) => {
    let text = '';
    let child;
    try {
      child = spawn(ffmpeg, ['-hide_banner', '-i', file], { windowsHide: true });
    } catch (e) { resolve(null); return; }
    const timer = setTimeout(() => { try { child.kill(); } catch (e) { /* 已退 */ } }, 15000);
    child.stdout.on('data', (b) => { text += b.toString('utf8'); });
    child.stderr.on('data', (b) => { text += b.toString('utf8'); });
    child.on('error', () => { clearTimeout(timer); resolve(null); });
    child.on('close', () => {
      clearTimeout(timer);
      const v = /Stream #\d+:\d+.*?: Video: ([a-z0-9_]+)/i.exec(text);
      if (!v) { resolve(null); return; }
      // 同样是 H.264，10bit / 4:2:2 的 Chromium 也解不了（手机和相机的 MP4 常见）。
      // 像素格式在 Video: 那一行的逗号串里。
      const vLine = (/Video: [^\n]*/i.exec(text) || [''])[0];
      const pix = (vLine.split(',').map(s => s.trim()).find(s => /^(yuv|nv|p0|gray|gbr|rgb)/i.test(s)) || '')
        .split('(')[0].toLowerCase();
      const fmt = /Input #0, ([^,]+(?:,[^,]+)*), from/.exec(text);
      const dur = /Duration: (\d+):(\d+):(\d+(?:\.\d+)?)/.exec(text);
      resolve({
        codec: v[1].toLowerCase(),
        pix,
        format: (fmt ? fmt[1] : '').toLowerCase(),
        seconds: dur ? (Number(dur[1]) * 3600 + Number(dur[2]) * 60 + Number(dur[3])) : 0,
      });
    });
  });
}

function containerOf(info, ext) {
  const f = info && info.format ? info.format : '';
  if (f.includes('matroska')) return 'mkv';
  if (f.includes('webm')) return 'webm';
  if (f.includes('mp4') || f.includes('mov')) return 'mp4';
  if (f.includes('avi')) return 'avi';
  if (f.includes('ogg')) return 'ogg';
  return String(ext || '').replace('.', '').toLowerCase();
}

// 跑 ffmpeg 并从 -progress 读数；onProgress(percent)
function convert(ffmpeg, args, seconds, onProgress, tracker) {
  return new Promise((resolve) => {
    let err = '';
    let child;
    try {
      child = spawn(ffmpeg, args, { windowsHide: true });
    } catch (e) { resolve({ ok: false, err: e.message }); return; }
    if (tracker) tracker.add(child);
    const timer = setTimeout(() => { try { child.kill(); } catch (e) { /* 已退 */ } }, FFMPEG_MAX_WAIT);
    child.stdout.on('data', (b) => {
      const text = b.toString('utf8');
      const m = /out_time_ms=(\d+)/.exec(text);
      if (m && seconds > 0 && onProgress) {
        onProgress(Math.min(99, Math.max(0, Math.round((Number(m[1]) / 1e6 / seconds) * 100))));
      }
    });
    child.stderr.on('data', (b) => { err += b.toString('utf8'); });
    child.on('error', (e) => {
      clearTimeout(timer);
      if (tracker) tracker.delete(child);
      resolve({ ok: false, err: e.message });
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (tracker) tracker.delete(child);
      if (code === 0) resolve({ ok: true });
      else resolve({ ok: false, err: (err.trim().split('\n').pop() || ('ffmpeg 退出码 ' + code)) });
    });
  });
}

function cachePath(file, stat, suffix) {
  const key = crypto.createHash('sha1')
    .update(file + '|' + stat.size + '|' + stat.mtimeMs).digest('hex').slice(0, 24);
  return path.join(PREPARE_DIR, key + '.' + suffix);
}

function createVideoMedia({ idleMs = 6 * 60 * 60 * 1000 } = {}) {
  const tickets = new Map(); // ticket -> { file, size, type, expiresAt }
  let server = null;
  let port = 0;
  let opening = null;
  let ffmpegCache = null;              // 找不到是 ''，避免每次都扫盘
  const running = new Set();           // 正在跑的 ffmpeg，close() 要收干净

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

  // 转换目录会越攒越大，每次开工顺手清掉一周前的
  function pruneOld() {
    try {
      const now = Date.now();
      for (const name of fs.readdirSync(PREPARE_DIR)) {
        const p = path.join(PREPARE_DIR, name);
        try { if (now - fs.statSync(p).mtimeMs > 7 * 24 * 3600 * 1000) fs.unlinkSync(p); } catch (e) { /* 别人的文件 */ }
      }
    } catch (e) { /* 目录还不存在 */ }
  }

  function ffmpegPath() {
    if (ffmpegCache === null) ffmpegCache = locateFfmpeg();
    return ffmpegCache;
  }

  // 判断这个文件能不能直接播；不能就把它弄成能播的。
  // 返回 { file, converted, note }，file 就是该给播放器（和问答）的那个路径。
  async function prepare(resolved, stat, report) {
    const say = (phase, percent, message) => {
      try { report({ phase, percent, message }); } catch (e) { /* 渲染层没了 */ }
    };
    const ffmpeg = ffmpegPath();
    const ext = path.extname(resolved).toLowerCase();
    const direct = { file: resolved, converted: '', note: '' };
    if (!ffmpeg) {
      return Object.assign(direct, {
        note: '没找到 ffmpeg，按原文件播放。如果放不出来，多半是编码或封装不被支持，装个 ffmpeg 就能自动转。',
      });
    }

    say('probing', 0, '正在看这个视频的编码…');
    const info = await inspect(ffmpeg, resolved);
    if (!info) return direct;   // 探不出来就别自作聪明，交给播放器自己报错

    const container = containerOf(info, ext);
    const family = container === 'ogg' ? 'ogg' : container;
    const allowed = PLAYABLE[family] || [];
    // 8bit 4:2:0 以外（10bit / 4:2:2 / 4:4:4）Chromium 的 H.264 解不了，
    // 光换封装没用，得老实重编码。
    const chromaOk = !info.pix || ['yuv420p', 'yuvj420p', 'nv12'].includes(info.pix);
    const label = container.toUpperCase() + ' / ' + info.codec.toUpperCase()
      + (info.pix && !chromaOk ? ' ' + info.pix : '');
    if (allowed.includes(info.codec) && chromaOk) {
      return Object.assign(direct, { note: '', container, codec: info.codec });
    }

    try { fs.mkdirSync(PREPARE_DIR, { recursive: true }); } catch (e) { /* 已存在 */ }
    pruneOld();
    const out = cachePath(resolved, stat, 'mp4');
    if (fs.existsSync(out)) {
      say('ready', 100, '用上次转好的副本播放');
      return { file: out, converted: 'cached', note: label + '，已用上次转好的副本', container, codec: info.codec };
    }

    // H.264（8bit 4:2:0）只是装在 MKV/AVI 里：换个壳就行，不重编码，几秒
    if (info.codec === 'h264' && chromaOk) {
      say('remuxing', 0, label + '：正在换封装（不重编码）…');
      const part = out + '.part';
      const r = await convert(ffmpeg, [
        '-hide_banner', '-loglevel', 'error', '-y', '-i', resolved,
        '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy', '-sn', '-dn',
        '-movflags', '+faststart', '-f', 'mp4', '-progress', 'pipe:1', '-nostats', part,
      ], info.seconds, null, running);
      if (r.ok) {
        try { fs.renameSync(part, out); } catch (e) { /* 覆盖失败就下次再说 */ }
        say('ready', 100, '换封装完成');
        return { file: out, converted: 'remux', note: label + ' → 换封装为 MP4', container, codec: info.codec };
      }
      try { fs.unlinkSync(part); } catch (e) { /* 没有就算了 */ }
      throw new Error('这个 ' + label + ' 文件换封装失败：' + r.err);
    }

    // 编码本身 Chromium 就不认（HEVC / MPEG4 / ProRes…）：只能重编码
    say('transcoding', 0, label + ' 播放器解不了，正在转成 H.264（画质基本无损，慢一些）…');
    const part = out + '.part';
    const r = await convert(ffmpeg, [
      '-hide_banner', '-loglevel', 'error', '-y', '-i', resolved,
      '-map', '0:v:0', '-map', '0:a:0?',
      '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22', '-pix_fmt', 'yuv420p',
      '-c:a', 'aac', '-b:a', '160k', '-sn', '-dn',
      '-movflags', '+faststart', '-f', 'mp4', '-progress', 'pipe:1', '-nostats', part,
    ], info.seconds, (percent) => say('transcoding', percent, '正在转成 H.264：' + percent + '%'), running);
    if (!r.ok) {
      try { fs.unlinkSync(part); } catch (e) { /* 没有就算了 */ }
      throw new Error('这个 ' + label + ' 文件转码失败：' + r.err);
    }
    try { fs.renameSync(part, out); } catch (e) { /* 覆盖失败就下次再说 */ }
    say('ready', 100, '转换完成');
    return { file: out, converted: 'transcode', note: label + ' → 转码为 H.264 MP4', container, codec: info.codec };
  }

  // 把一个真实路径登记成可播地址。stat 失败就别谎称成功。
  // onProgress 会被调用若干次（探编码 / 换封装 / 转码进度），渲染层拿它画状态。
  async function share(filePath, options) {
    const opts = options || {};
    const report = typeof opts.onProgress === 'function' ? opts.onProgress : () => {};
    const resolved = path.resolve(String(filePath || ''));
    const type = MIME[path.extname(resolved).toLowerCase()];
    if (!type) throw new Error('不是支持的视频格式：' + (path.extname(resolved) || '无扩展名'));
    let stat;
    try { stat = await fs.promises.stat(resolved); } catch (e) { throw new Error('读不到这个文件：' + e.message); }
    if (!stat.isFile()) throw new Error('选中的不是文件');
    if (!stat.size) throw new Error('文件是空的');

    let ready;
    try {
      ready = await prepare(resolved, stat, report);
    } catch (error) {
      report({ phase: 'error', percent: 0, message: String((error && error.message) || error) });
      throw error;
    }

    let playable = stat;
    if (ready.file !== resolved) {
      try { playable = await fs.promises.stat(ready.file); } catch (e) { throw new Error('转换后的文件不见了：' + e.message); }
    }
    await listen();
    const token = crypto.randomBytes(24).toString('hex');
    const outType = MIME[path.extname(ready.file).toLowerCase()] || type;
    tickets.set(token, { file: ready.file, size: playable.size, type: outType, expiresAt: Date.now() + idleMs });
    return {
      ok: true,
      // 问答读的也是这个路径：转换过就给转换后的（内容一样，但编码一定能解）
      path: ready.file,
      source: resolved,
      name: path.basename(resolved),
      size: playable.size,
      converted: ready.converted || '',
      note: ready.note || '',
      url: 'http://127.0.0.1:' + port + '/v/' + token,
    };
  }

  // 会话级 webRequest 闸门会拦掉所有非白名单的 127.0.0.1 请求。
  // 播放器自己去取媒体字节时（不走 IPC），要能被认出来放行，
  // 否则 Range 请求在到达本服务器之前就被 cancel，界面只会看到
  // 「此视频无法播放 / MEDIA_ELEMENT_ERROR: Format error」。
  function isApprovedUrl(raw) {
    if (!server || !server.listening) return false;
    try {
      const u = new URL(raw);
      if (u.origin !== 'http://127.0.0.1:' + port || u.search || u.hash) return false;
      const match = /^\/v\/([a-f0-9]{48})$/.exec(u.pathname);
      const entry = match && tickets.get(match[1]);
      return !!(entry && entry.expiresAt > Date.now());
    } catch (e) { return false; }
  }

  function close() {
    for (const child of running) { try { child.kill(); } catch (e) { /* 已退 */ } }
    running.clear();
    tickets.clear();
    if (opening) { try { opening.catch(() => {}); } catch (e) { /* 忽略 */ } opening = null; }
    if (server) { try { server.close(); } catch (e) { /* 已关 */ } server = null; port = 0; }
  }

  return { share, close, isApprovedUrl, extensions: VIDEO_EXTENSIONS };
}

module.exports = { createVideoMedia, VIDEO_EXTENSIONS };
