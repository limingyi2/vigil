// Adapt the upstream Cordis HTTP surface to an isolated local Electron service.
// Settings stay in userData; third-party wallpaper frames get read-only media capabilities.
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { pathToFileURL } = require('url');

function createWallpaperHost({ dataDir, vendorDir = path.resolve(__dirname, '../vendor/dsh-wallpaper-engine'), logger }) {
  const secret = crypto.randomBytes(32).toString('hex');
  const capability = '/w/' + crypto.randomBytes(24).toString('hex');
  let server, dispose, ready, origin, closing = false;
  const routes = [];
  const readOnly = /^\/(?:wallpaper-engine\/(?:scene-live|scene-files|media|preview|video-preview|scene-video|scene-audio|scene-frame|props|diag)(?:\/|$)|api\/local-assets(?:\/|$))/;
  async function start() {
    if (closing) throw new Error('壁纸服务已关闭');
    if (ready) return ready;
    ready = (async () => {
      process.env.DSH_WE_DATA_DIR = dataDir;
      const upstream = await import(pathToFileURL(path.join(vendorDir, 'lib/index.js')).href);
      dispose = upstream.apply({ webServer: { register(route) {
        routes.push(route);
        return () => { const i = routes.indexOf(route); if (i >= 0) routes.splice(i, 1); };
      } }, logger: () => logger || ({ info: (...a) => console.log('[wallpaper]', ...a),
        warn: (...a) => console.warn('[wallpaper]', ...a), error: (...a) => console.error('[wallpaper]', ...a), debug() {} }) });
      server = http.createServer(async (req, res) => {
        try {
          const url = new URL(req.url, 'http://localhost');
          const privileged = req.headers.authorization === 'Bearer ' + secret;
          const hasCapability = url.pathname.startsWith(capability + '/');
          if (hasCapability) url.pathname = url.pathname.slice(capability.length);
          const read = req.method === 'GET' || req.method === 'HEAD';
          if (!privileged && !(hasCapability && (read && readOnly.test(url.pathname)
            || req.method === 'POST' && url.pathname === '/wallpaper-engine/diag'))) {
            res.writeHead(403); res.end('forbidden'); return;
          }
          // Never dispatch untrusted payloads to mutating host endpoints.
          req.url = url.pathname + url.search;
          res.setHeader('Access-Control-Allow-Origin', '*');
          res.setHeader('Referrer-Policy', 'no-referrer');
          if (url.pathname.startsWith('/wallpaper-engine/scene-live/')) {
            const root = path.join(vendorDir, 'lib/webwallgl');
            const rel = decodeURIComponent(url.pathname.slice('/wallpaper-engine/scene-live/'.length));
            const file = path.resolve(root, rel || 'index.html');
            if (!file.startsWith(root + path.sep) || !/\.(html|js)$/.test(file)) {
              res.writeHead(403); res.end('forbidden'); return;
            }
            // The renderer uses absolute resource paths. Scope those to this session's capability.
            let content = await fs.promises.readFile(file, 'utf8');
            content = content.replaceAll('/wallpaper-engine', capability + '/wallpaper-engine')
              .replaceAll('/api/local-assets', capability + '/api/local-assets')
              .replaceAll('}/diag?', '}' + capability + '/wallpaper-engine/diag?');
            if (file.endsWith('index.html') && !file.includes('default-wallpaper')) {
              content = content.replace('</body>', `<script src="${capability}/wallpaper-engine/scene-live/vigil-control.js"></script></body>`);
            }
            res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : 'text/html');
            res.setHeader('Cache-Control', 'no-store');
            res.end(req.method === 'HEAD' ? undefined : content); return;
          }
          const route = routes.find(r => r.kind === 'exact' ? r.path === url.pathname
            : url.pathname === r.path || url.pathname.startsWith(r.path + '/'));
          if (!route) { res.writeHead(404); res.end('not found'); return; }
          await route.handler(req, res);
        } catch (e) {
          if (!res.headersSent) res.writeHead(500);
          if (!res.destroyed) res.end('wallpaper service error');
          console.error('[wallpaper-host]', e.message);
        }
      });
      // Our injected controller is a trusted renderer bridge; it never forwards host API access.
      const original = server.listeners('request')[0];
      server.removeListener('request', original);
      server.on('request', (req, res) => {
        if (req.url === capability + '/wallpaper-engine/scene-live/vigil-control.js') {
          res.setHeader('Content-Type', 'text/javascript');
          res.end(`(()=>{window.addEventListener('message',e=>{if(e.source!==parent||e.data?.vigilWallpaper!==true)return;const c=e.data,wp=window.__wp;if(!wp)return;try{if(c.pointer)wp.pushPointer?.(c.pointer.u,c.pointer.v,c.pointer.buttons,c.pointer.mods);else{c.paused?wp.pause():wp.resume();wp.setVolume?.(c.volume);wp.setFit?.(c.fit)}}catch(_){}});setInterval(()=>{const wp=window.__wp;if(!wp)return;let s,detail;try{s=window.__wpStats?.frame?.()||{};detail=wp.getState?.()||{};}catch(_){s={};detail={};}const fallback=!!document.querySelector('iframe[src*="default-wallpaper"]');parent.postMessage({vigilWallpaper:true,state:{...s,...detail},fallback,webReady:detail.iframeLoaded===true&&!detail.webError&&!fallback,ready:!!wp},'*');},1000);})();`);
          return;
        }
        original(req, res);
      });
      await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
      server.unref();
      origin = 'http://127.0.0.1:' + server.address().port;
      return origin + capability;
    })();
    try { return await ready; } catch (e) { ready = null; if (dispose) dispose(); throw e; }
  }
  async function request(method, resource, body) {
    if (!/^(GET|PUT|POST)$/.test(method) || !/^\/(?:inventory|settings|diag-log|upload-dir|we-assets-dir|props\/[^/?]+)$/.test(resource)) {
      throw new Error('不支持的壁纸操作');
    }
    await start();
    const value = await raw(method, '/wallpaper-engine' + resource, body && Buffer.from(JSON.stringify(body)), 'application/json');
    if (resource === '/inventory') {
      value.sceneMediaBase = origin + capability;
      for (const w of value.wallpapers || []) for (const key of ['media', 'preview', 'frameUrl', 'sceneVideo', 'sceneAudio', 'webLiveSrc', 'propsUrl']) {
        if (w[key]) w[key] = origin + capability + new URL(w[key], origin).pathname;
      }
    }
    return { data: value, base: origin + capability };
  }
  function raw(method, resource, bytes, contentType) {
    return new Promise((resolve, reject) => {
      const req = http.request(origin + resource, { method, headers: {
        Authorization: 'Bearer ' + secret, 'Content-Type': contentType,
        ...(bytes ? { 'Content-Length': bytes.length } : {}),
      } }, res => {
        const chunks = []; let length = 0;
        res.on('data', b => { length += b.length; if (length > 16 * 1024 * 1024) req.destroy(new Error('壁纸响应过大')); else chunks.push(b); });
        res.on('end', () => { try {
          const data = JSON.parse(Buffer.concat(chunks).toString());
          if (res.statusCode >= 400) reject(new Error(data.error || '壁纸请求失败')); else resolve(data);
        } catch (e) { reject(e); } });
        res.on('error', reject);
      });
      req.setTimeout(60000, () => req.destroy(new Error('壁纸请求超时')));
      req.on('error', reject); req.end(bytes);
    });
  }
  async function importFile(file) {
    const mime = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg' }[path.extname(file).toLowerCase()];
    if (!mime) throw new Error('仅支持 PNG 和 JPG 图片');
    const info = await fs.promises.stat(file);
    if (info.size > 512 * 1024 * 1024) throw new Error('文件不能超过 512 MB');
    await start();
    // Stream the selected file. No renderer-controlled arbitrary filesystem path.
    return new Promise((resolve, reject) => {
      const req = http.request(origin + '/wallpaper-engine/upload?title=' + encodeURIComponent(path.basename(file)), {
        method: 'POST', headers: { Authorization: 'Bearer ' + secret, 'Content-Type': mime, 'Content-Length': info.size },
      }, res => {
        let data = ''; res.on('data', b => data += b); res.on('end', () => { try {
          const parsed = JSON.parse(data); if (res.statusCode >= 400) reject(new Error(parsed.error)); else resolve(parsed);
        } catch (e) { reject(e); } }); res.on('error', reject);
      });
      req.setTimeout(120000, () => req.destroy(new Error('导入超时')));
      req.on('error', reject);
      const stream = fs.createReadStream(file); stream.on('error', e => req.destroy(e));
      req.on('close', () => stream.destroy()); stream.pipe(req);
    });
  }
  function close() { closing = true; if (dispose) dispose(); if (server) { server.close(); server.closeAllConnections?.(); } }
  return { start, request, importFile, close };
}
module.exports = { createWallpaperHost };
