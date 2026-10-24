// 内置壁纸契约测试（真客户端页面 + 真插件前端 + 真 ticket 化 loopback 文档）
//
// 验的是行为，不是字符串：
//   ① 没装 Wallpaper Engine（workshop 为空）时，壁纸页必须给出三张内置壁纸 + 引导条；
//   ② 点其中一张必须真的挂上 iframe，且文档经 loopback 票据服务取得（与应用 CSP 一致）；
//   ③ 三张内置壁纸的 HTML 单独渲染出来必须"有内容"（像素方差，不是纯色/纯黑）；
//   ④ id 必须是纯数字 —— 客户端的 plugins:resource-url 只认 \d+。
//
// 跑法：env -u ELECTRON_RUN_AS_NODE ./node_modules/.bin/electron tools/test-wallpaper-builtin-electron.js
const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const assert = require('assert');

const CLIENT = path.resolve(__dirname, '..');
const PLUGIN = 'F:/zhixia/market/plugins/wallpaper';
const SHOTS = 'F:/zhixia/_tmp/wallpaper-fix';

app.disableHardwareAcceleration();
app.on('window-all-closed', () => {});
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const docs = new Map();   // name -> html（模拟客户端的插件内联文档）
let port = 0;

function serveDocument(name, html) {
  const id = Math.random().toString(16).slice(2, 10);
  docs.set(id, { name, html });
  return 'http://127.0.0.1:' + port + '/r/' + id + '/' + encodeURIComponent(name);
}

const FIXTURE = `
(() => {
  const state = {
    config: { enabled: false, wallpaper_id: null, wallpaper_type: null, wallpaper_file: null,
      opacity: 1, scrim: 0, blur: 0, fit: 'cover', fps: 60, muted: true, quality: 'high',
      transparent: true, carousel_enabled: false, carousel_minutes: 30, carousel_shuffle: false,
      we_path: null },
    patches: [],
  };
  window.__wpTest = state;
  const reply = (json) => ({ ok: true, json });
  window.__wpStubApi = {
    get(p) {
      if (String(p).indexOf('/config') >= 0) return Promise.resolve(reply({ ok: true, config: state.config }));
      if (String(p).indexOf('/library') >= 0) return Promise.resolve(reply({ ok: true, we_path: null, count: 0, wallpapers: [] }));
      return Promise.resolve(reply({ ok: true }));
    },
    post(p, body) {
      state.config = Object.assign({}, state.config, body || {});
      state.patches.push(body || {});
      return Promise.resolve(reply({ ok: true, config: state.config }));
    },
  };
  window.VigilAPI = window.__wpStubApi;
  // 应用壳启动时会摸这些成员（见 js/app.js）；这里给最小可用的空实现，
  // 免得本测试被"客户端桥缺件"的噪音淹掉真正的插件错误。
  window.vigil = {
    autoConnect: () => Promise.resolve({ ok: true }),
    appVersion: () => Promise.resolve('test'),
    cfg: { get: () => Promise.resolve({ src: 'pc' }), set: (p) => Promise.resolve(p) },
    win: { minimize() {}, maximize() {}, close() {}, onState() {} },
    settings: { open: () => Promise.resolve({ ok: true }), onNavigate() {} },
    local: { answer() {}, onConfirm() {} },
    agentCenter: { onOpen() {} },
    sidecar: { status: () => Promise.resolve({ ready: true }) },
    runJS: () => Promise.resolve(null),
    plugins: {
      documentUrl: (name, html) => window.__testDocumentUrl(name, html).then((url) => ({ ok: true, url })),
      resourceUrl: () => Promise.resolve({ ok: false, err: '测试环境没有媒体资源' }),
      releaseResources() {},
    },
  };
  window.VigilPlugin = {
    registerPage(def) { window.__wpTestPage = def; },
    registerCleanup() {},
  };
})();
`;

async function renderDocWithVariance(html, tag) {
  const win = new BrowserWindow({
    // 壁纸页在 document.hidden 时会自己暂停，隐藏窗口只会拿到第一帧；
    // 放到屏幕外 + showInactive 既能跑动画又不打扰用户。
    show: true, x: -5000, y: 0, width: 1100, height: 660, frame: false,
    webPreferences: { backgroundThrottling: false, sandbox: true },
  });
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'wp-builtin-')), tag + '.html');
  fs.writeFileSync(file, html);
  await win.loadFile(file);
  win.showInactive();
  await wait(1800);
  const img = await win.webContents.capturePage();
  fs.writeFileSync(path.join(SHOTS, 'builtin-' + tag + '.png'), img.toPNG());
  const { width, height } = img.getSize();
  const bmp = img.getBitmap();           // BGRA
  let n = 0, sum = 0, sum2 = 0;
  for (let i = 0; i < bmp.length; i += 4 * 13) {   // 抽样，别把 1.4M 像素全算一遍
    const g = bmp[i + 1];
    n += 1; sum += g; sum2 += g * g;
  }
  const mean = sum / n;
  const variance = Math.max(0, sum2 / n - mean * mean);
  win.destroy();
  await wait(300);
  return { width, height, mean, variance };
}

(async () => {
  await app.whenReady();
  fs.mkdirSync(SHOTS, { recursive: true });

  const server = http.createServer((req, res) => {
    const m = /^\/r\/([a-f0-9]+)\//.exec(req.url || '');
    const doc = m && docs.get(m[1]);
    if (!doc) { res.writeHead(404); res.end('nope'); return; }
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
    res.end(doc.html);
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  port = server.address().port;
  ipcMain.handle('wp-test:document', (_e, name, html) => serveDocument(name, html));

  // ── 真客户端页面（脚本替换成夹具，其余 DOM/样式与线上一致）
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wp-builtin-page-'));
  const dst = path.join(tmp, 'src');
  fs.cpSync(path.join(CLIENT, 'src'), dst, { recursive: true });
  fs.writeFileSync(path.join(dst, '_wp_fixture.js'), FIXTURE);
  const preload = path.join(tmp, 'preload.js');
  fs.writeFileSync(preload, `
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('__testDocumentUrl', (name, html) => ipcRenderer.invoke('wp-test:document', name, html));
`);
  const html = fs.readFileSync(path.join(dst, 'index.html'), 'utf8')
    .replace('<script src="js/ui.js"></script>',
      '<script src="_wp_fixture.js"></script>\n<script src="js/ui.js"></script>');
  fs.writeFileSync(path.join(dst, '_wp_index.html'), html);

  const win = new BrowserWindow({
    show: true, x: -5000, y: 0, width: 1440, height: 860, frame: false,
    webPreferences: {
      contextIsolation: true, sandbox: true, backgroundThrottling: false,
      preload,
    },
  });
  const errors = [];
  win.webContents.on('console-message', (_e, level, message) => {
    if (level >= 3) errors.push(message);
  });
  await win.loadFile(path.join(dst, '_wp_index.html'));
  win.showInactive();
  await wait(600);

  // 插件前端：真 CSS + 真 JS，注入方式与线上一致（<style id="plugin-css-wallpaper">）
  console.log('step: read plugin');
  const css = fs.readFileSync(path.join(PLUGIN, 'frontend.css'), 'utf8');
  const js = fs.readFileSync(path.join(PLUGIN, 'frontend.js'), 'utf8');
  console.log('step: insertCSS');
  await win.webContents.insertCSS(fs.readFileSync(path.join(dst, 'styles.css'), 'utf8'));
  await win.webContents.executeJavaScript(
    `(() => { const s = document.createElement('style'); s.id = 'plugin-css-wallpaper'; s.textContent = ${JSON.stringify(css)}; document.head.appendChild(s); })()`);
  // app.js 加载后会用真实现覆盖 window.VigilPlugin，所以要在注入插件前重新占住它：
  // 真实现会把页面挂进导航，这里只需要拿到 render 入口。
  await win.webContents.executeJavaScript(`(() => {
    window.VigilPlugin = {
      registerPage(def) { window.__wpTestPage = def; },
      registerCleanup() {},
    };
    // app.js 也会把 window.VigilAPI 换成走 global.vigil.api 的真实现，_devstub 还可能
    // 覆盖 window.vigil —— 注入插件前把这两个入口重新钉回夹具。
    if (window.__wpStubApi) window.VigilAPI = window.__wpStubApi;
    window.vigil = window.vigil || {};
    window.vigil.plugins = Object.assign({}, window.vigil.plugins, {
      documentUrl: (name, html) => window.__testDocumentUrl(name, html).then((url) => ({ ok: true, url })),
      resourceUrl: () => Promise.resolve({ ok: false, err: '测试环境没有媒体资源' }),
      releaseResources() {},
    });
  })()`);
  console.log('step: inject plugin js');
  // 包一层 try：executeJavaScript 自己的报错信息（"Script failed to execute"）什么也说明不了
  const injected = await win.webContents.executeJavaScript(`(() => {
    try {
${js}
      return 'ok';
    } catch (e) { return 'ERR ' + String(e && (e.stack || e.message) || e); }
  })()`);
  assert.equal(injected, 'ok', '插件前端注入失败：' + String(injected).slice(0, 500));
  await wait(900);

  // ① 三张内置壁纸 + 引导条
  console.log('step: read builtin');
  const builtin = await win.webContents.executeJavaScript('window.__vigilWallpaperBuiltin || []');
  assert.equal(builtin.length, 3, '必须随包分发三张内置壁纸，实际 ' + builtin.length);
  for (const item of builtin) {
    assert.match(String(item.id), /^\d+$/, '内置壁纸 id 必须是纯数字（客户端资源桥只认 \\d+）：' + item.id);
  }
  assert.equal(new Set(builtin.map((x) => x.id)).size, 3, '内置 id 不能重复');

  await win.webContents.executeJavaScript(`(() => {
    const view = document.getElementById('view') || document.body;
    const page = window.__wpTestPage;
    if (!page) throw new Error('插件没有注册壁纸页');
    page.render({ view, topbar: document.getElementById('topbar'), panel: document.getElementById('panel'),
      src: 'pc', navigate() {}, refresh() {}, onDispose() {} });
  })()`);
  await wait(800);

  const pageState = await win.webContents.executeJavaScript(`(() => ({
    cards: document.querySelectorAll('#wp4-grid [data-wp4-id]').length,
    builtinCards: [...document.querySelectorAll('#wp4-grid [data-wp4-id]')].map((b) => b.dataset.wp4Id),
    tags: document.querySelectorAll('#wp4-grid .vigil-wallpaper-tag').length,
    hint: !!document.querySelector('.vigil-wallpaper-hint'),
    thumbs: [...document.querySelectorAll('#wp4-grid img[data-preview]')].map((i) => String(i.src).slice(0, 22)),
    status: (document.querySelector('.vigil-wallpaper-status') || {}).textContent || '',
  }))()`);
  console.log('page:', JSON.stringify(pageState));
  assert.equal(pageState.cards, 3, '没有 Workshop 时库里应当正好是三张内置壁纸');
  assert.equal(pageState.tags, 3, '内置卡片都要有「内置」角标');
  assert.ok(pageState.hint, '没装 Wallpaper Engine 且未启用壁纸时必须有引导条');
  assert.ok(pageState.thumbs.every((s) => s.startsWith('data:image/jpeg')), '缩略图必须是内联 data URI');
  assert.ok(/内置壁纸可用/.test(pageState.status), '状态行要说明内置壁纸可用');
  fs.writeFileSync(path.join(SHOTS, 'builtin-page.png'), (await win.webContents.capturePage()).toPNG());

  // ② 点第一张必须真的挂上 iframe，且文档来自 loopback 票据
  for (const [index, item] of builtin.entries()) {
    const before = new Set(docs.keys());
    await win.webContents.executeJavaScript(
      `document.querySelector('#wp4-grid [data-wp4-id="${item.id}"]').click();`);
    await wait(1200);
    const mounted = await win.webContents.executeJavaScript(`(() => {
      const f = document.querySelector('#vigil-wallpaper-stage iframe');
      return { has: !!f, src: f ? f.getAttribute('src') : '', count: document.querySelectorAll('#vigil-wallpaper-stage iframe').length };
    })()`);
    assert.ok(mounted.has, item.title + '：点选后必须有壁纸 iframe');
    assert.match(mounted.src, /^http:\/\/127\.0\.0\.1:\d+\/r\//, item.title + '：壁纸文档必须走 loopback 票据，实际 ' + mounted.src);
    assert.equal(mounted.count, 1, item.title + '：同一时刻只允许一个壁纸层');

    const fresh = [...docs.entries()].filter(([id]) => !before.has(id));
    assert.equal(fresh.length, 1, item.title + '：一次点选应当只索取一份文档');
    const docHtml = fresh[0][1].html;
    assert.ok(docHtml.indexOf('<!doctype html') === 0, item.title + '：内置文档必须是完整 HTML');

    // ③ 内联文档单独渲染必须有内容
    const shot = await renderDocWithVariance(docHtml, item.key);
    console.log('render', item.key, JSON.stringify(shot));
    assert.ok(shot.variance > 12, item.title + '：画面是纯色/纯黑，壁纸没画出来（variance ' + shot.variance.toFixed(1) + '）');
    if (index === 0) fs.writeFileSync(path.join(SHOTS, 'builtin-mounted.png'), (await win.webContents.capturePage()).toPNG());
  }

  const patches = await win.webContents.executeJavaScript('window.__wpTest.patches');
  const last = patches[patches.length - 1] || {};
  assert.equal(last.enabled, true, '点选内置壁纸必须把 enabled 打开');
  assert.ok(String(last.wallpaper_id || '').length > 0, '点选内置壁纸必须记住 id');
  const fatal = errors.filter((m) => /Uncaught|TypeError|ReferenceError/.test(m));
  assert.deepEqual(fatal, [], '插件前端不应抛未捕获异常：' + fatal.join(' | '));

  console.log('PASS 内置壁纸：三张随包分发、可点选挂载、文档经 loopback、画面非纯色');
  win.destroy();
  server.close();
  app.quit();
})().catch((e) => {
  console.error('FAIL', e && e.stack || e);
  app.exit(1);
});
