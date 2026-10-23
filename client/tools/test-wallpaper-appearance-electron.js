// Verifies the v4 wallpaper renderer against the real Vigil surface styles.
const { app, BrowserWindow } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');
const assert = require('node:assert/strict');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-wallpaper-appearance-'));
app.setPath('userData', path.join(tmp, 'userData'));
const root = path.resolve(__dirname, '../..');
const plugin = path.join(root, 'market/plugins/wallpaper');
let win;
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const until = async (fn, timeout = 10000) => {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await fn()) return; await wait(50); }
  throw new Error('timeout');
};

async function main() {
  await app.whenReady();
  const html = `<!doctype html><html lang="zh-CN" data-theme="light"><head><meta charset="utf-8"></head><body data-vigil-page="chat"><div class="app"><div class="titlebar"></div><div class="body"><nav class="gnav"></nav><aside class="panel"></aside><main class="main"><div class="chatwrap"><div class="chat"><div class="chat-messages"></div><div class="composer"><div class="cbox"></div></div></div><div class="ac-card"></div><div class="persona-view"><div class="pcard"></div><input class="input"></div><div id="view"></div></div></main></div><footer class="statusbar"></footer></div></body></html>`;
  fs.writeFileSync(path.join(tmp, 'index.html'), html);
  win = new BrowserWindow({ show: false, width: 900, height: 600, webPreferences: { contextIsolation: true, sandbox: true, backgroundThrottling: false } });
  win.webContents.on('console-message', (_e, level, message) => { if (level >= 2) console.error('[renderer]', message); });
  await win.loadFile(path.join(tmp, 'index.html'));
  for (const stylesheet of ['styles.css', 'chat.css', 'persona.css', 'agent-center.css']) {
    await win.webContents.insertCSS(fs.readFileSync(path.join(root, 'client/src', stylesheet), 'utf8'));
  }
  await win.webContents.insertCSS(fs.readFileSync(path.join(plugin, 'frontend.css'), 'utf8'));
  await win.webContents.executeJavaScript(`
    const wallpaper = { id: '123', title: 'Appearance fixture', type: 'image', file: 'fixture.svg', preview: 'fixture.svg' };
    window.VigilAPI = {
      get: async path => path.startsWith('/api/plugin/wallpaper/library')
        ? {ok:true,json:{ok:true,we_path:'Fixture Workshop',wallpapers:[wallpaper]}}
        : {ok:true,json:{ok:true,config:{enabled:false,opacity:.52,scrim:.24,blur:0,fit:'cover',fps:60,muted:true,quality:'high'}}},
      post: async (_path, body) => ({ok:true,json:{ok:true,config:{enabled:true,...body}}})
    };
    window.vigil = {plugins:{resourceUrl:async()=>({ok:true,url:'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"%3E%3Crect width="64" height="64" fill="%2300aaff"/%3E%3C/svg%3E'}),releaseResources(){}}};
    window.VigilPlugin = {registerPage:page=>{window.__wallpaperPage=page;},registerCleanup:(_owner,fn)=>{window.__wallpaperDispose=fn;}};
    void 0;
  `);
  await win.webContents.executeJavaScript(fs.readFileSync(path.join(plugin, 'frontend.js'), 'utf8'));
  const ev = expression => win.webContents.executeJavaScript(expression).catch(error => { console.error('[eval]', expression.slice(0, 120), error); throw error; });
  await until(() => ev('!!window.__wallpaperPage'));
  await ev(`window.__wallpaperPage.render({view:document.getElementById('view')})`);
  await until(() => ev('document.querySelectorAll(".vigil-wallpaper-card").length===1'));
  await ev(`document.querySelector('[data-wp4-id="123"]').click()`);
  await until(() => ev('document.querySelector("#vigil-wallpaper-stage img")?.complete && document.querySelector("#vigil-wallpaper-stage img")?.naturalWidth===64'));

  assert.equal(await ev('document.body.classList.contains("vigil-wallpaper-active")'), true);
  assert.equal(await ev('document.body.classList.contains("vwp-active")'), true);
  const surfaces = await ev(`(() => {
    const alpha = selector => {
      const color = getComputedStyle(document.querySelector(selector)).backgroundColor;
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
      const ctx = canvas.getContext('2d'); ctx.fillStyle = color; ctx.fillRect(0, 0, 1, 1);
      return ctx.getImageData(0, 0, 1, 1).data[3];
    };
    return { app: alpha('.app'), main: alpha('.main'), nav: alpha('.gnav'),
      agent: alpha('.ac-card'), persona: alpha('.pcard'), control: alpha('.vigil-wallpaper-control'),
      input: alpha('.vigil-wallpaper-toolbar input'), select: alpha('.vigil-wallpaper-toolbar select'),
      button: alpha('#wp4-refresh') };
  })()`);
  assert.equal(surfaces.app, 0, 'application shell must let wallpaper show through');
  assert.equal(surfaces.main, 0, 'main content must let wallpaper show through');
  for (const name of ['nav', 'agent', 'persona', 'control', 'input', 'select', 'button']) {
    assert(surfaces[name] > 0 && surfaces[name] < 200, `${name} surface must stay visibly translucent; got alpha ${surfaces[name]}`);
  }
  const layerOrder = await ev(`({ wallpaper: getComputedStyle(document.getElementById('vigil-wallpaper-root')).zIndex,
    app: getComputedStyle(document.querySelector('.app')).zIndex })`);
  assert.equal(layerOrder.wallpaper, '0');
  assert.equal(layerOrder.app, '1');
await ev('window.__wallpaperDispose()');
  assert.equal(await ev('document.body.classList.contains("vigil-wallpaper-active")'), false);
  assert.equal(await ev('document.body.classList.contains("vwp-active")'), false);
  console.log('PASS: wallpaper image is mounted; app surfaces are transparent/translucent; compatibility state is cleaned up');
}

main().then(() => cleanup(0), error => { console.error(error); cleanup(1); });
function cleanup(code) { if (win) win.destroy(); app.exit(code); }
