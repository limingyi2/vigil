// Media elements cannot use the JSON IPC bridge. Issue opaque loopback URLs;
// only this proxy adds the brain credential, never the renderer.
const http = require('http');
const https = require('https');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

// WebWallGL 场景渲染页的静态产物（打进了 asar：electron/webwallgl/**）。
// 由本进程供出，而不是让渲染层直接读文件 —— 渲染页要从票据地址取 scene.pkg，
// 只有和票据服务同源时这条 fetch 才不需要跨源放行。
const SCENE_LIVE_ROOT = path.join(__dirname, 'webwallgl');
const SCENE_LIVE_PREFIX = '/scene-live/';
const STATIC_MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.webp': 'image/webp',
};
// 渲染页要跑 ES module、fetch 同源票据、给 WebGL 纹理用 blob/data。
const SCENE_LIVE_CSP = "default-src 'self'; script-src 'self' 'unsafe-inline'; "
  + "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; "
  + "media-src 'self' blob:; worker-src 'self' blob:; object-src 'none'; frame-ancestors 'self'";

function createPluginMedia({ getBase, getToken, ticketIdleMs = 30 * 60 * 1000 }) {
  // An active stream renews its ticket; abandoned tickets expire when idle.
  const idleMs = Number.isFinite(ticketIdleMs) && ticketIdleMs > 0 ? ticketIdleMs : 30 * 60 * 1000;
  const tickets = new Map();
  const urls = new Map();
  const localFiles = new Map();
  const localUrls = new Map();
  const localStreams = new Map();
  let server = null;
  let opening = null;

  function parseResource(resource) {
    if (typeof resource !== 'string' || !resource.startsWith('/api/plugin/wallpaper/we/')) {
      throw new Error('不支持的插件媒体路径');
    }
    const u = new URL(resource, 'http://resource.invalid');
    const kind = u.pathname.split('/').pop();
    if (!['media', 'preview'].includes(kind) || u.pathname !== '/api/plugin/wallpaper/we/' + kind) {
      throw new Error('不支持的插件媒体接口');
    }
    const id = u.searchParams.get('id');
    const file = kind === 'media' ? u.searchParams.get('file') : 'preview';
    if (!/^\d+$/.test(id || '') || !file || /[\\\x00]/.test(file)
        || file.startsWith('/') || file.split('/').includes('..')) {
      throw new Error('非法壁纸资源路径');
    }
    return { kind, id, file };
  }

  // 场景渲染页：只读 asar 内的固定产物，不接受任何外部路径。
  function serveSceneLive(req, res, pathname) {
    let rel;
    try { rel = decodeURIComponent(pathname.slice(SCENE_LIVE_PREFIX.length)); }
    catch (_) { return false; }
    if (!rel) rel = 'index.html';
    if (/[\\\x00]/.test(rel)) return false;
    const root = path.resolve(SCENE_LIVE_ROOT);
    const target = path.resolve(root, rel);
    if (target !== root && !target.startsWith(root + path.sep)) return false;
    let body;
    try { body = fs.readFileSync(target); } catch (_) { return false; }
    const type = STATIC_MIME[path.extname(target).toLowerCase()] || 'application/octet-stream';
    const headers = {
      'Content-Type': type,
      'Content-Length': String(body.length),
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'no-referrer',
    };
    if (type.startsWith('text/html')) headers['Content-Security-Policy'] = SCENE_LIVE_CSP;
    res.writeHead(200, headers);
    if (req.method === 'HEAD') { res.end(); return true; }
    res.end(body);
    return true;
  }

  async function handle(req, res) {
    const fail = (code, message) => {
      if (!res.headersSent) res.writeHead(code, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end(message);
    };
    if (!['GET', 'HEAD'].includes(req.method)) return fail(405, 'Method not allowed');
    try {
      const local = new URL(req.url, 'http://127.0.0.1');
      if (local.pathname === '/scene-live' || local.pathname.startsWith(SCENE_LIVE_PREFIX)) {
        if (serveSceneLive(req, res, local.pathname === '/scene-live' ? SCENE_LIVE_PREFIX : local.pathname)) return;
        return fail(404, 'Unknown scene asset');
      }
      const match = /^\/r\/([a-f0-9]{48})\/(.+)$/.exec(local.pathname);
      const ticket = match && tickets.get(match[1]);
      if (!ticket) return fail(404, 'Unknown resource');
      if (ticket.expiresAt <= Date.now()) {
        tickets.delete(match[1]);
        return fail(410, 'Expired resource');
      }
      const file = decodeURIComponent(match[2]);
      if (/[\\\x00]/.test(file) || file.startsWith('/') || file.split('/').includes('..')) {
        return fail(400, 'Invalid path');
      }
      // A project.json ticket is a scoped Scene directory capability because
      // WebWallGL resolves sibling package/texture resources from that base.
      // Other tickets are exact-file capabilities (including previews).
      const directoryTicket = ticket.kind === 'media'
        && (ticket.file === 'project.json' || /\.html?$/i.test(ticket.file));
      if (!directoryTicket && file !== ticket.file) return fail(403, 'Ticket scope mismatch');
      if (ticket.kind === 'preview' && file !== 'preview') return fail(403, 'Ticket scope mismatch');
      if (ticket.kind === 'local') {
        const stat = await fs.promises.stat(ticket.localPath);
        // Long-running videos fetch new ranges; renew on each valid media request.
        ticket.expiresAt = Date.now() + idleMs;
        const range = /^bytes=(\d*)-(\d*)$/.exec(String(req.headers.range || ''));
        let start = 0, end = Math.max(0, stat.size - 1), status = 200;
        if (range) {
          if (!range[1] && !range[2]) return fail(416, 'Invalid range');
          if (!range[1]) { const suffix = Number(range[2]); start = Math.max(0, stat.size - suffix); }
          else { start = Number(range[1]); if (range[2]) end = Math.min(Number(range[2]), end); }
          if (start >= stat.size || start > end) return fail(416, 'Range not satisfiable');
          status = 206;
        }
        const headers = { 'Content-Type': ticket.mime, 'Content-Length': Math.max(0, end - start + 1),
          'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' };
        if (range) headers['Content-Range'] = `bytes ${start}-${end}/${stat.size}`;
        res.writeHead(status, headers);
        if (req.method === 'HEAD') return res.end();
        const stream = fs.createReadStream(ticket.localPath, { start, end });
        let active = localStreams.get(ticket.id);
        if (!active) localStreams.set(ticket.id, active = new Set());
        active.add(stream);
        const forget = () => { active.delete(stream); if (!active.size) localStreams.delete(ticket.id); };
        stream.on('close', forget); stream.on('end', forget); stream.on('error', forget);
        res.on('close', () => stream.destroy());
        stream.on('error', () => res.destroy());
        stream.pipe(res);
        return;
      }
      const upstreamPath = new URL('/api/plugin/wallpaper/we/' + ticket.kind, getBase());
      upstreamPath.searchParams.set('id', ticket.id);
      if (ticket.kind === 'media') upstreamPath.searchParams.set('file', file);
      else if (file !== 'preview') return fail(404, 'Unknown resource');
      ticket.expiresAt = Date.now() + idleMs;
      const token = await getToken();
      const headers = {};
      if (token) headers.Authorization = 'Bearer ' + token;
      if (req.headers.range) headers.Range = req.headers.range;
      const transport = upstreamPath.protocol === 'https:' ? https : http;
      const upstream = transport.request(upstreamPath, { method: 'GET', headers }, (response) => {
        const out = { 'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': '*',
          'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer' };
        for (const name of ['content-type', 'content-length', 'content-range', 'accept-ranges']) {
          if (response.headers[name]) out[name] = response.headers[name];
        }
        if ((out['content-type'] || '').includes('text/html')) {
          out['Content-Security-Policy'] = "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'";
        }
        res.writeHead(response.statusCode, out);
        response.on('error', () => res.destroy());
        if (req.method === 'HEAD') { response.resume(); res.end(); }
        else response.pipe(res);
      });
      upstream.setTimeout(30000, () => upstream.destroy(new Error('媒体请求超时')));
      upstream.on('error', () => fail(502, 'Media upstream unavailable'));
      res.on('close', () => upstream.destroy());
      upstream.end();
    } catch (_) { fail(400, 'Invalid resource request'); }
  }

  function ensureServer() {
    if (opening) return opening;
    opening = new Promise((resolve, reject) => {
      server = http.createServer(handle);
      server.once('error', reject);
      server.listen(0, '127.0.0.1', () => resolve(server.address().port));
      server.unref();
    });
    return opening;
  }

  return {
    registerLocal(id, filePath, mime) {
      if (!/^[a-f0-9-]{36}$/i.test(String(id))) throw new Error('非法导入素材 ID');
      const resolved = path.resolve(filePath);
      const stat = fs.lstatSync(resolved);
      if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('导入素材必须是普通文件');
      localFiles.set(String(id), { path: resolved, mime: String(mime) });
    },
    unregisterLocal(id) {
      const key = String(id); localFiles.delete(key); localUrls.delete(key);
      for (const [ticket, entry] of tickets) if (entry.kind === 'local' && entry.id === key) tickets.delete(ticket);
      for (const stream of localStreams.get(key) || []) stream.destroy();
      localStreams.delete(key);
    },
    async localUrl(id) {
      const key = String(id), entry = localFiles.get(key);
      if (!entry) throw new Error('导入素材不存在或已删除');
      for (const [ticket, item] of tickets) if (item.expiresAt <= Date.now()) tickets.delete(ticket);
      const cached = localUrls.get(key);
      if (cached && tickets.has(cached.ticket) && tickets.get(cached.ticket).expiresAt > Date.now()) return cached.url;
      if (cached) { tickets.delete(cached.ticket); localUrls.delete(key); }
      if (tickets.size >= 1024) throw new Error('插件媒体资源过多，请稍后重试');
      const port = await ensureServer();
      if (localFiles.get(key) !== entry) throw new Error('导入素材已删除');
      const ticket = crypto.randomBytes(24).toString('hex');
      tickets.set(ticket, { kind: 'local', id: String(id), file: path.basename(entry.path),
        localPath: entry.path, mime: entry.mime, expiresAt: Date.now() + idleMs });
      const result = 'http://127.0.0.1:' + port + '/r/' + ticket + '/' + encodeURIComponent(path.basename(entry.path));
      localUrls.set(key, { ticket, url: result });
      return result;
    },
    async url(resource) {
      const item = parseResource(resource);
      const key = JSON.stringify(item);
      const cached = urls.get(key);
      if (cached && tickets.has(cached.ticket) && tickets.get(cached.ticket).expiresAt > Date.now()) {
        return cached.url;
      }
      if (cached) { tickets.delete(cached.ticket); urls.delete(key); }
      for (const [id, entry] of tickets) {
        if (entry.expiresAt <= Date.now()) tickets.delete(id);
      }
      if (tickets.size >= 1024) throw new Error('插件媒体资源过多，请稍后重试');
      const port = await ensureServer();
      const ticket = crypto.randomBytes(24).toString('hex');
      tickets.set(ticket, { ...item, expiresAt: Date.now() + idleMs });
      const suffix = item.file.split('/').map(encodeURIComponent).join('/');
      const result = 'http://127.0.0.1:' + port + '/r/' + ticket + '/' + suffix;
      urls.set(key, { ticket, url: result });
      return result;
    },
    // The renderer request gate allows only URLs issued by this local proxy.
    isApprovedUrl(raw) {
      if (!server || !server.listening) return false;
      try {
        const u = new URL(raw);
        if (u.origin !== 'http://127.0.0.1:' + server.address().port || u.hash) return false;
        // 场景渲染页自身（响应上的查询参数就是要传进渲染器的壁纸配置）。
        // 只读 asar 里的固定产物，不涉及用户数据。
        if (u.pathname.startsWith(SCENE_LIVE_PREFIX)) return true;
        if (u.search) return false;
        const match = /^\/r\/([a-f0-9]{48})\/(.+)$/.exec(u.pathname);
        const ticket = match && tickets.get(match[1]);
        return !!(ticket && ticket.expiresAt > Date.now());
      } catch (_) { return false; }
    },
    // 渲染页地址：iframe 的入口。端口是动态的，所以前端每次现取。
    async sceneLiveUrl() {
      const port = await ensureServer();
      return 'http://127.0.0.1:' + port + SCENE_LIVE_PREFIX + 'index.html';
    },
    releaseResources() {
      tickets.clear();
      urls.clear();
      localUrls.clear();
    },
    close() {
      if (server) { server.close(); if (server.closeAllConnections) server.closeAllConnections(); }
      server = null; opening = null; tickets.clear(); urls.clear(); localUrls.clear(); localFiles.clear();
    },
  };
}
function shouldCancelLoopbackRequest(details, rendererId, isApprovedUrl) {
  let url;
  try { url = new URL(details.url); } catch (_) { return false; }
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) return false;
  // 白名单命中就放行，不再要求发起方是主渲染层：场景渲染页跑在跨源子框架里，
  // 它有自己的 webContents，而它要拿的只有不透明票据地址与本地只读渲染产物。
  // 端口仅监听回环，票据是 48 位随机串且过期即失效。
  return !isApprovedUrl(details.url);
}
module.exports = { createPluginMedia, shouldCancelLoopbackRequest };
