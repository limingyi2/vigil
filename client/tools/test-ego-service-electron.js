// 真 Electron 回归：file:// 页面通过回环闸门建会话并读取 SSE。
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createPluginResources, shouldCancelLoopbackRequest } = require('../electron/plugin-resource');
const { createVideoMedia } = require('../electron/video-media');

app.on('window-all-closed', () => {});
app.whenReady().then(async () => {
  let win;
  let dir;
  const seen = [];
  const server = http.createServer((req, res) => {
    seen.push(req.method + ' ' + req.url);
    res.setHeader('Access-Control-Allow-Origin', '*');
    if (req.method === 'OPTIONS') {
      res.writeHead(204, { 'Access-Control-Allow-Headers': 'content-type',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS' });
      return res.end();
    }
    if (req.url === '/ask') {
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      return res.end('data: {"type":"final","payload":{"answer":"fixture"}}\n\n');
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, service: 'egoagent-vr' }));
  });
  const resources = createPluginResources({ getBase: () => '', getToken: async () => '' });
  const media = createVideoMedia();
  let failed = false;
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    process.env.VIGIL_EGO_PORT = String(server.address().port);
    const ego = require('../electron/ego-service');
    const base = 'http://127.0.0.1:' + ego.PORT;
    win = new BrowserWindow({ show: false,
      webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true } });
    const rendererId = win.webContents.id;
    const allowed = raw => resources.isApprovedUrl(raw) || media.isApprovedUrl(raw);
    win.webContents.session.webRequest.onBeforeRequest(
      { urls: ['http://127.0.0.1/*', 'http://localhost/*', 'http://[::1]/*'] },
      (details, callback) => callback(ego.isApprovedRequest(details, rendererId)
        || !shouldCancelLoopbackRequest(details, rendererId, allowed) ? {} : { cancel: true }));

    const request = { url: base + '/session', method: 'POST', resourceType: 'xhr',
      webContentsId: rendererId, frame: { parent: null } };
    for (const override of [
      { url: base + '/private' }, { url: base + '/session?extra=1' },
      { url: 'http://127.0.0.1:1/session' }, { url: base.replace('127.0.0.1', 'localhost') + '/session' },
      { method: 'GET' }, { resourceType: 'subFrame' }, { resourceType: 'image' },
      { webContentsId: rendererId + 1 }, { frame: { parent: {} } }, { frame: null },
    ]) assert.equal(ego.isApprovedRequest({ ...request, ...override }, rendererId), false,
      JSON.stringify(override));
    assert.equal(ego.isApprovedRequest(request, rendererId), true);

    dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-ego-test-'));
    const page = path.join(dir, 'page.html');
    const source = fs.readFileSync(path.join(__dirname, '../src/index.html'), 'utf8');
    const csp = source.match(/<meta http-equiv="Content-Security-Policy"[\s\S]*?>/)[0];
    fs.writeFileSync(page, '<!doctype html>' + csp);
    await win.loadFile(page);
    const result = await win.webContents.executeJavaScript(`(async () => {
      const base = ${JSON.stringify(base)};
      const result = {};
      result.health = (await (await fetch(base + '/health')).json()).ok;
      for (const endpoint of ['/session', '/cancel', '/close']) {
        const response = await fetch(base + endpoint, { method: 'POST',
          headers: { 'content-type': 'application/json' }, body: JSON.stringify({ video: 'fixture.mp4' }) });
        result[endpoint] = (await response.json()).ok;
      }
      const answer = await fetch(base + '/ask', { method: 'POST',
        headers: { 'content-type': 'application/json' }, body: JSON.stringify({ question: 'fixture' }) });
      const reader = answer.body.getReader();
      const decoder = new TextDecoder();
      result.sse = '';
      for (;;) { const chunk = await reader.read(); if (chunk.done) break;
        result.sse += decoder.decode(chunk.value, { stream: true }); }
      try { await fetch(base + '/private'); result.blocked = false; }
      catch { result.blocked = true; }
      return result;
    })()`);
    assert.equal(result.health, true);
    for (const endpoint of ['/session', '/cancel', '/close']) assert.equal(result[endpoint], true);
    assert.match(result.sse, /"answer":"fixture"/);
    assert.equal(result.blocked, true);
    assert(!seen.some(item => item.endsWith('/private')));
    console.log('PASS EgoAgent session, SSE, cancel, close and loopback restrictions');
  } catch (error) {
    failed = true;
    console.error(error);
  } finally {
    if (win) win.destroy();
    resources.close();
    media.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    if (dir) {
      // dir is created directly under the OS temp directory by mkdtempSync above.
      fs.rmSync(dir, { recursive: true, force: true });
    }
    app.exit(failed ? 1 : 0);
  }
});
