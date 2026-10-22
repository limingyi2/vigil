// Isolated v4 plugin test. Uses a real bundled upstream renderer and an
// authenticated Wallpaper Engine style Scene fixture; no user profile/files.
const { app, BrowserWindow } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');
const http = require('http');
const assert = require('node:assert/strict');
const { createPluginResources, shouldCancelLoopbackRequest } = require('../electron/plugin-resource');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-wallpaper-v4-'));
app.setPath('userData', path.join(tmp, 'userData'));
app.disableHardwareAcceleration();
const root = path.resolve(__dirname, '../..');
const plugin = path.join(root, 'market/plugins/wallpaper');
let backend, resources, win;
const seen = [];
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const until = async (fn, timeout = 20000) => { const end = Date.now() + timeout; while (Date.now() < end) { if (await fn()) return; await wait(100); } throw new Error('timeout'); };

async function main() {
  await app.whenReady();
  const project = { type: 'scene', title: 'V4 animated fixture', file: 'scene.json' };
  const scene = { general: { clearcolor: '0.02 0.04 0.08', orthogonalprojection: { width: 320, height: 180 } }, objects: [{ id: 1, name: 'animated solid', solid: true, size: '240 130', origin: '0 0 0', color: '0.2 0.7 1', alpha: { value: 1, animation: { c0: [{ frame: 0, value: 0.2 }, { frame: 10, value: 1 }, { frame: 20, value: 0.2 }], options: { fps: 20, length: 20, mode: 'loop', startpaused: false } } } }] };
  const preview = Buffer.from([137,80,78,71,13,10,26,10]);
  backend = http.createServer((req, res) => {
    seen.push(req.url);
    if (req.headers.authorization !== 'Bearer fixture-secret') { res.writeHead(401); return res.end(); }
    const u = new URL(req.url, 'http://fixture');
    if (u.pathname === '/api/plugin/wallpaper/library') return json(res, { ok: true, we_path: 'Fixture Workshop', wallpapers: [{ id: '123', title: 'V4 Scene', type: 'scene', file: 'scene.json', preview: 'preview.png' }] });
    if (u.pathname === '/api/plugin/wallpaper/config') return json(res, { ok: true, config: { enabled: false, opacity: .52, scrim: .24, blur: 0, fit: 'cover', fps: 60, muted: true, quality: 'high' } });
    if (u.pathname === '/api/plugin/wallpaper/resource') {
      const file = u.searchParams.get('file');
      if (file === 'project.json') return json(res, project);
      if (file === 'scene.json') return json(res, scene);
      if (file === 'preview.png') { res.writeHead(200, {'Content-Type':'image/png','Access-Control-Allow-Origin':'*'}); return res.end(preview); }
    }
    res.writeHead(404); res.end();
  });
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve));
  resources = createPluginResources({ getBase: () => 'http://127.0.0.1:' + backend.address().port, getToken: async () => 'fixture-secret' });
  fs.writeFileSync(path.join(tmp, 'preload.js'), `const{contextBridge,ipcRenderer}=require('electron');contextBridge.exposeInMainWorld('vigil',{plugins:{resourceUrl:p=>ipcRenderer.invoke('resource',p),releaseResources:()=>ipcRenderer.invoke('release')},api:(m,p,b)=>ipcRenderer.invoke('api',m,p,b)});`);
  require('electron').ipcMain.handle('resource', async (_e, p) => { try { return {ok:true,url:await resources.url(p)}; } catch (e) { return {ok:false,err:e.message}; } });
  require('electron').ipcMain.handle('release', () => { resources.releaseResources(); return {ok:true}; });
  require('electron').ipcMain.handle('api', async (_e, method, p, body) => {
    if (method === 'GET' && p.startsWith('/api/plugin/wallpaper/library')) return {ok:true,json:{ok:true,we_path:'Fixture Workshop',wallpapers:[{id:'123',title:'V4 Scene',type:'scene',file:'scene.json',preview:'preview.png'}]}};
    if (method === 'GET') return {ok:true,json:{ok:true,config:{enabled:false,opacity:.52,scrim:.24,blur:0,fit:'cover',fps:60,muted:true,quality:'high'}}};
    return {ok:true,json:{ok:true,config:{enabled:true,...body}}};
  });
  const html = `<!doctype html><meta charset="utf-8"><style>html,body{margin:0;width:100%;height:100%;background:#12151c}.app{position:relative;width:100%;height:100%}</style><div class="app"><div id="view"></div></div>`;
  fs.writeFileSync(path.join(tmp, 'index.html'), html);
  win = new BrowserWindow({show:false,width:900,height:600,webPreferences:{preload:path.join(tmp,'preload.js'),contextIsolation:true,sandbox:true,backgroundThrottling:false}});
  win.webContents.session.webRequest.onBeforeRequest({urls:['http://127.0.0.1/*','http://localhost/*','http://[::1]/*']}, (details, cb) => cb(shouldCancelLoopbackRequest(details, win.webContents.id, resources.isApprovedUrl.bind(resources)) ? {cancel:true} : {}));
  win.webContents.on('console-message', (_e, level, message) => { if (level >= 2) console.error('[renderer]', message); });
  await win.loadFile(path.join(tmp, 'index.html'));
  await win.webContents.insertCSS(fs.readFileSync(path.join(plugin, 'frontend.css'), 'utf8'));
  await win.webContents.executeJavaScript(`window.VigilAPI={get:p=>vigil.api('GET',p),post:(p,b)=>vigil.api('POST',p,b)};window.VigilPlugin={registerPage:p=>window.__page=p,registerCleanup:(o,f)=>window.__dispose=f};void 0`);
  await win.webContents.executeJavaScript(fs.readFileSync(path.join(plugin, 'frontend.js'), 'utf8'));
  const ev = expression => win.webContents.executeJavaScript(expression);
  await until(() => ev('!!window.__page'));
  await ev(`window.__page.render({view:document.getElementById('view')})`);
  await until(() => ev('document.querySelectorAll(".vigil-wallpaper-card").length===1'));
  assert.equal(await ev('document.querySelector(".vigil-wallpaper-card-title").textContent'), 'V4 Scene');
  await ev('document.querySelector("[data-wp4-id=\'123\']").click()');
  await until(() => ev('!!document.querySelector("#vigil-wallpaper-stage iframe")'), 10000);
  await until(() => Promise.resolve(seen.some(x => x.includes('file=scene.json'))), 10000);
  await until(() => ev('document.querySelector("#vigil-wallpaper-live-badge")?.hidden===false'), 20000);
  assert.equal(await ev('document.body.classList.contains("vigil-wallpaper-active")'), true);
  assert(seen.some(x => x.includes('file=scene.json')));
  const iframeCount = await ev('document.querySelectorAll("#vigil-wallpaper-stage iframe").length');
  assert.equal(iframeCount, 1);
  await ev('window.__dispose()');
  assert.equal(await ev('!!document.getElementById("vigil-wallpaper-root")'), false);
  assert.equal(resources.isApprovedUrl((await resources.url('/api/plugin/wallpaper/resource?id=123&file=scene.json'))), false);
  console.log('PASS: v4 plugin page, authenticated Scene resource, bundled renderer iframe, live badge and cleanup');
}
function json(res, body) { res.writeHead(200, {'Content-Type':'application/json','Access-Control-Allow-Origin':'*'}); res.end(JSON.stringify(body)); }
main().then(() => cleanup(0), e => { console.error(e); cleanup(1); });
function cleanup(code) { if (win) win.destroy(); resources?.close(); backend?.close(); app.exit(code); }
