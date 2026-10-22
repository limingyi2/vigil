// Generic read-only resource bridge for frontend plugins.
// Plugins receive expiring loopback URLs; only this process adds Brain auth and
// streams bytes. No Wallpaper-specific renderer or user-file capability lives here.
const http = require('http');
const https = require('https');
const crypto = require('crypto');

function createPluginResources({ getBase, getToken, ticketIdleMs = 30 * 60 * 1000 }) {
  const idleMs = Number.isFinite(ticketIdleMs) && ticketIdleMs > 0 ? ticketIdleMs : 30 * 60 * 1000;
  const tickets = new Map();
  const urls = new Map();
  let server = null;
  let opening = null;

  function parseResource(resource) {
    if (typeof resource !== 'string') throw new Error('资源路径无效');
    const u = new URL(resource, 'http://resource.invalid');
    const match = /^\/api\/plugin\/([a-zA-Z0-9_-]+)\/resource$/.exec(u.pathname);
    const id = u.searchParams.get('id');
    const file = u.searchParams.get('file');
    if (!match || !/^\d+$/.test(id || '') || !file || /[\\\x00]/.test(file)
      || file.startsWith('/') || file.split('/').includes('..')) {
      throw new Error('资源路径非法');
    }
    return { plugin: match[1], id, file };
  }

  function purgeExpired() {
    const now = Date.now();
    for (const [id, entry] of tickets) if (entry.expiresAt <= now) tickets.delete(id);
    for (const [key, entry] of urls) if (!tickets.has(entry.ticket)) urls.delete(key);
  }

  async function handle(req, res) {
    const fail = (code, message) => {
      if (!res.headersSent) res.writeHead(code, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end(message);
    };
    if (!['GET', 'HEAD'].includes(req.method)) return fail(405, 'Method not allowed');
    try {
      const local = new URL(req.url, 'http://127.0.0.1');
      const match = /^\/r\/([a-f0-9]{48})\/(.+)$/.exec(local.pathname);
      const ticket = match && tickets.get(match[1]);
      if (!ticket) return fail(404, 'Unknown resource');
      if (ticket.expiresAt <= Date.now()) { tickets.delete(match[1]); return fail(410, 'Expired resource'); }
      const file = decodeURIComponent(match[2]);
      if (/[\\\x00]/.test(file) || file.startsWith('/') || file.split('/').includes('..')) return fail(400, 'Invalid path');
      if (!ticket.directory && file !== ticket.file) return fail(403, 'Resource scope mismatch');
      ticket.expiresAt = Date.now() + idleMs;
      const upstream = new URL('/api/plugin/' + ticket.plugin + '/resource', getBase());
      upstream.searchParams.set('id', ticket.id);
      upstream.searchParams.set('file', file);
      const token = await getToken();
      const headers = {};
      if (token) headers.Authorization = 'Bearer ' + token;
      if (req.headers.range) headers.Range = req.headers.range;
      const transport = upstream.protocol === 'https:' ? https : http;
      const request = transport.request(upstream, { method: req.method, headers }, response => {
        const out = { 'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': '*',
          'Access-Control-Expose-Headers': 'Content-Length,Content-Range,Accept-Ranges',
          'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer' };
        for (const name of ['content-type', 'content-length', 'content-range', 'accept-ranges', 'last-modified']) {
          if (response.headers[name]) out[name] = response.headers[name];
        }
        res.writeHead(response.statusCode || 502, out);
        response.on('error', () => res.destroy());
        response.pipe(res);
      });
      request.setTimeout(30000, () => request.destroy(new Error('资源请求超时')));
      request.on('error', () => { if (!res.headersSent) fail(502, 'Resource upstream unavailable'); else res.destroy(); });
      res.on('close', () => request.destroy());
      request.end();
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
    async url(resource) {
      const item = parseResource(resource);
      purgeExpired();
      const key = JSON.stringify(item);
      const cached = urls.get(key);
      if (cached && tickets.has(cached.ticket)) return cached.url;
      if (tickets.size >= 1024) throw new Error('插件媒体资源过多，请稍后重试');
      const port = await ensureServer();
      const ticket = crypto.randomBytes(24).toString('hex');
      const directory = item.file === 'project.json' || item.file === 'scene.json'
        || item.file.endsWith('.pkg') || /\.html?$/i.test(item.file);
      tickets.set(ticket, { ...item, directory, expiresAt: Date.now() + idleMs });
      const suffix = item.file.split('/').map(encodeURIComponent).join('/');
      const result = 'http://127.0.0.1:' + port + '/r/' + ticket + '/' + suffix;
      urls.set(key, { ticket, url: result });
      return result;
    },
    isApprovedUrl(raw) {
      if (!server || !server.listening) return false;
      try {
        const u = new URL(raw);
        if (u.origin !== 'http://127.0.0.1:' + server.address().port || u.search) return false;
        const match = /^\/r\/([a-f0-9]{48})\/.+$/.exec(u.pathname);
        const ticket = match && tickets.get(match[1]);
        return !!(ticket && ticket.expiresAt > Date.now());
      } catch (_) { return false; }
    },
    releaseResources() { tickets.clear(); urls.clear(); },
    close() {
      if (server) { server.close(); if (server.closeAllConnections) server.closeAllConnections(); }
      server = null; opening = null; tickets.clear(); urls.clear();
    },
  };
}

function shouldCancelLoopbackRequest(details, _rendererId, isApprovedUrl) {
  let url;
  try { url = new URL(details.url); } catch (_) { return false; }
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) return false;
  return !isApprovedUrl(details.url);
}

module.exports = { createPluginResources, shouldCancelLoopbackRequest };
