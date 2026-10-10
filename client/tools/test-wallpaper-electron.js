// Isolated Electron fixture: real plugin/CSP, authenticated image/video/web media.
// Does not touch client credentials, conversations, Steam or user wallpaper config.
const { app, BrowserWindow, ipcMain, nativeImage } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');
const http = require('http');
const assert = require('assert/strict');
const { spawnSync } = require('child_process');
const { createPluginMedia } = require('../electron/plugin-media');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-wallpaper-test-'));
app.setPath('userData', path.join(tmp, 'electron'));
app.disableHardwareAcceleration();
const root = path.resolve(__dirname, '../..');
const plugin = path.join(root, 'brain/plugins/wallpaper');
const wait = ms => new Promise(r => setTimeout(r, ms));
let backend, media, win;

async function main() {
  await app.whenReady();
  const result = spawnSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=160x90:d=1',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-y', path.join(tmp, 'test.mp4')], { windowsHide: true });
  assert.equal(result.status, 0, result.stderr && result.stderr.toString());
  const video = fs.readFileSync(path.join(tmp, 'test.mp4'));
  const png = nativeImage.createFromBitmap(Buffer.from([30, 100, 200, 255]), { width: 1, height: 1 }).toPNG();
  const requests = [];
  backend = http.createServer((req, res) => {
    requests.push(req.url);
    if (req.headers.authorization !== 'Bearer fixture-secret') { res.writeHead(401); return res.end(); }
    const u = new URL(req.url, 'http://local');
    if (u.searchParams.get('file') === 'index.html') {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      return res.end('<img src="assets/image.png" onload="parent.postMessage({wallpaperFixture:true},\'*\')">');
    }
    const isVideo = u.searchParams.get('file') === 'test.mp4';
    const data = isVideo ? video : png;
    let start = 0, end = data.length - 1, status = 200;
    const range = /bytes=(\d+)-(\d*)/.exec(req.headers.range || '');
    if (range) { start = Number(range[1]); end = range[2] ? Math.min(Number(range[2]), end) : end; status = 206; }
    const headers = { 'Content-Type': isVideo ? 'video/mp4' : 'image/png', 'Content-Length': end - start + 1,
      'Accept-Ranges': 'bytes' };
    if (range) headers['Content-Range'] = `bytes ${start}-${end}/${data.length}`;
    res.writeHead(status, headers); res.end(data.subarray(start, end + 1));
  });
  await new Promise(r => backend.listen(0, '127.0.0.1', r));
  media = createPluginMedia({ getBase: () => 'http://127.0.0.1:' + backend.address().port,
    getToken: async () => 'fixture-secret' });
  ipcMain.handle('fixture:media', async (_, p) => ({ ok: true, url: await media.url(p) }));
  let config = { enabled: false, opacity: 0.7, scrim: 0.4, muted: true };
  const library = [{ id: '123', title: '<Test scene>', type: 'scene', file: 'scene.pkg' },
    { id: '456', title: 'Video', type: 'video', file: 'test.mp4' },
    { id: '789', title: 'Web', type: 'web', file: 'index.html' }];
  ipcMain.handle('fixture:api', async (_, method, resource, body) => {
    if (resource.includes('/we/library')) return { ok: true, json: { ok: true, wallpapers: library, we_path: 'Fixture' } };
    if (method === 'POST') config = { ...config, ...body };
    return { ok: true, json: { ok: true, config: { ...config } } };
  });
  fs.writeFileSync(path.join(tmp, 'preload.js'), `const {contextBridge,ipcRenderer}=require('electron');
    contextBridge.exposeInMainWorld('vigil',{plugins:{resourceUrl:p=>ipcRenderer.invoke('fixture:media',p)},
      api:(m,p,b)=>ipcRenderer.invoke('fixture:api',m,p,b)});`);
  const csp = fs.readFileSync(path.join(root, 'client/src/index.html'), 'utf8').match(/content="(default-src[^"]+)"/)[1];
  fs.writeFileSync(path.join(tmp, 'index.html'), `<meta http-equiv="Content-Security-Policy" content="${csp}">
    <div class="app"><div id="view"></div></div>`);
  win = new BrowserWindow({ show: false, width: 1100, height: 900,
    webPreferences: { preload: path.join(tmp, 'preload.js'), contextIsolation: true, sandbox: true,
      backgroundThrottling: false, offscreen: true } });
  win.webContents.on('console-message', (_, level, message) => { if (level >= 2) console.log('[renderer]', message); });
  await win.loadFile(path.join(tmp, 'index.html'));
  await win.webContents.insertCSS(fs.readFileSync(path.join(root, 'client/src/styles.css'), 'utf8') +
    fs.readFileSync(path.join(plugin, 'frontend.css'), 'utf8'));
  await win.webContents.executeJavaScript(`window.VigilAPI={get:p=>vigil.api('GET',p),post:(p,b)=>vigil.api('POST',p,b)};
    window.VigilPlugin={registerPage:p=>window.fixturePage=p};
    window.addEventListener('message', e=>{if(e.data&&e.data.wallpaperFixture)window.webReady=true;});`);
  await win.webContents.executeJavaScript(fs.readFileSync(path.join(plugin, 'frontend.js'), 'utf8'));
  const ev = expression => win.webContents.executeJavaScript(expression);
  async function until(expression) {
    for (let i = 0; i < 100; i++) { if (await ev(expression)) return; await wait(100); }
    throw new Error('Timeout: ' + expression);
  }
  await ev(`fixturePage.render({view:document.getElementById('view')})`);
  await until(`document.querySelectorAll('.wp-card').length===3`);
  assert.equal(await ev(`document.querySelector('.wp-title').textContent`), '<Test scene>');
  await until(`Array.from(document.querySelectorAll('.wp-thumb img')).every(i=>i.complete&&i.naturalWidth>0)`);
  await ev(`document.querySelector('[data-id="123"]').click()`);
  await until(`document.querySelector('.wallpaper-bg img')?.naturalWidth>0`);
  assert.equal(await ev(`getComputedStyle(document.querySelector('.app')).backgroundColor`), 'rgba(0, 0, 0, 0)');
  await wait(300); // Let Chromium paint the decoded background before capture.
  fs.mkdirSync(path.join(root, 'client/_shots'), { recursive: true });
  const shot = await win.webContents.capturePage();
  const corner = shot.crop({ x: 5, y: 5, width: 1, height: 1 }).toBitmap();
  assert(corner[0] < 245 || corner[1] < 245 || corner[2] < 245, 'Wallpaper must be painted behind the app');
  fs.writeFileSync(path.join(root, 'client/_shots/wallpaper-code11.png'), shot.toPNG());
  await ev(`document.querySelector('[data-id="456"]').click()`);
  await until(`document.querySelector('.wallpaper-bg video')?.readyState>=2`);
  await ev(`document.querySelector('[data-id="789"]').click()`);
  await until(`window.webReady===true`);
  assert(requests.some(p => p.includes('assets%2Fimage.png')));
  await ev(`document.getElementById('wp-close').click()`);
  await until(`!document.body.classList.contains('wallpaper-active')`);
  console.log('PASS: registration, library, escaped titles, thumbnails, scene, video decoding, nested web assets, disable');
}
main().then(() => cleanup(0), e => { console.error(e); cleanup(1); });
function cleanup(code) {
  if (win) win.destroy();
  if (media) media.close();
  if (backend) { backend.close(); if (backend.closeAllConnections) backend.closeAllConnections(); }
  app.exit(code);
}
