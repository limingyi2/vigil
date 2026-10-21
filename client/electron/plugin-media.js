// Media elements cannot use the JSON IPC bridge. Issue opaque loopback URLs;
// only this proxy adds the brain credential, never the renderer.
const http = require('http');
const https = require('https');
const crypto = require('crypto');

function createPluginMedia({ getBase, getToken }) {
  const tickets = new Map();
  const urls = new Map();
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
      const file = decodeURIComponent(match[2]);
      if (/[\\\x00]/.test(file) || file.startsWith('/') || file.split('/').includes('..')) {
        return fail(400, 'Invalid path');
      }
      const upstreamPath = new URL('/api/plugin/wallpaper/we/' + ticket.kind, getBase());
      upstreamPath.searchParams.set('id', ticket.id);
      if (ticket.kind === 'media') upstreamPath.searchParams.set('file', file);
      else if (file !== 'preview') return fail(404, 'Unknown resource');
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
    async url(resource) {
      const item = parseResource(resource);
      const key = JSON.stringify(item);
      if (urls.has(key)) return urls.get(key);
      if (tickets.size >= 4096) throw new Error('插件媒体资源过多，请重启客户端');
      const port = await ensureServer();
      const ticket = crypto.randomBytes(24).toString('hex');
      tickets.set(ticket, item);
      const suffix = item.file.split('/').map(encodeURIComponent).join('/');
      const result = 'http://127.0.0.1:' + port + '/r/' + ticket + '/' + suffix;
      urls.set(key, result);
      return result;
    },
    releaseResources() {
      tickets.clear();
      urls.clear();
    },
    close() {
      if (server) { server.close(); if (server.closeAllConnections) server.closeAllConnections(); }
      server = null; opening = null; tickets.clear(); urls.clear();
    },
  };
}
module.exports = { createPluginMedia };
