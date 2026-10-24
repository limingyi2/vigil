// 在线壁纸清单契约测试（真清单 + 真插件前端，不联网）
//
// 发版前跑这个：清单是拿脚本生成的，一旦某条 url/哈希/缩略图写坏，
// 用户那边就是「点了没反应」或「下载失败」，本地很难看出来。
// 这里把 market/plugins/wallpaper/cloud-walls.json 原样喂给插件前端：
//   ① 每一条都要变成卡片，带「在线」角标与体积提示；
//   ② 每张卡片都要真的把内嵌缩略图解码出来（naturalWidth > 0）；
//   ③ 标题要能看见，不能是 id。
//
// 跑法：env -u ELECTRON_RUN_AS_NODE ./node_modules/.bin/electron tools/test-wallpaper-manifest-electron.js
const { app, BrowserWindow } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert');

const CLIENT = path.resolve(__dirname, '..');
const PLUGIN = 'F:/zhixia/market/plugins/wallpaper';
const MANIFEST = path.join(PLUGIN, 'cloud-walls.json');
const SHOTS = 'F:/zhixia/_tmp/wallpaper-fix';

app.disableHardwareAcceleration();
app.on('window-all-closed', () => {});
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'));
const items = (manifest.items || []).map((it) => ({
  id: String(it.id),
  title: it.title,
  type: it.type,
  file: it.file,
  bytes: it.bytes,
  total: it.bytes,
  credit: it.credit || '',
  // 后端 _cloud_view() 就是把清单的 poster 透传成 preview；这里照抄这一步。
  preview: it.poster || '',
  state: 'remote',
  got: 0,
  percent: 0,
}));

assert.ok(items.length > 0, '清单是空的：' + MANIFEST);

const FIXTURE = `
(() => {
  const state = {
    config: { enabled: false, wallpaper_id: null, wallpaper_type: null, wallpaper_file: null,
      opacity: 1, scrim: 0, blur: 0, fit: 'cover', fps: 60, muted: true, quality: 'high',
      transparent: true, carousel_enabled: false, carousel_minutes: 30, carousel_shuffle: false,
      we_path: null },
    patches: [],
  };
  const CLOUD = ${JSON.stringify(items)};
  window.__wpTest = state;
  const reply = (json) => ({ ok: true, json });
  window.__wpStubApi = {
    get(p) {
      const s = String(p);
      if (s.indexOf('/config') >= 0) return Promise.resolve(reply({ ok: true, config: state.config }));
      if (s.indexOf('/library') >= 0) return Promise.resolve(reply({ ok: true, we_path: null, count: 0, wallpapers: [] }));
      if (s.indexOf('/cloud') >= 0) return Promise.resolve(reply({ ok: true, count: CLOUD.length, wallpapers: CLOUD }));
      return Promise.resolve(reply({ ok: true }));
    },
    post(p, body) {
      state.config = Object.assign({}, state.config, body || {});
      state.patches.push(body || {});
      return Promise.resolve(reply({ ok: true, config: state.config }));
    },
  };
  window.VigilAPI = window.__wpStubApi;
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
      documentUrl: () => Promise.reject(new Error('在线壁纸不该走内联文档')),
      resourceUrl: () => Promise.reject(new Error('未缓存时不该去取媒体')),
      releaseResources() {},
    },
  };
  window.VigilPlugin = {
    registerPage(def) { window.__wpTestPage = def; },
    registerCleanup() {},
  };
})();
`;

(async () => {
  await app.whenReady();
  fs.mkdirSync(SHOTS, { recursive: true });

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wp-manifest-page-'));
  const dst = path.join(tmp, 'src');
  fs.cpSync(path.join(CLIENT, 'src'), dst, { recursive: true });
  fs.writeFileSync(path.join(dst, '_wp_fixture.js'), FIXTURE);
  const html = fs.readFileSync(path.join(dst, 'index.html'), 'utf8')
    .replace('<script src="js/ui.js"></script>',
      '<script src="_wp_fixture.js"></script>\n<script src="js/ui.js"></script>');
  fs.writeFileSync(path.join(dst, '_wp_index.html'), html);

  const win = new BrowserWindow({
    show: true, x: -5000, y: 0, width: 1440, height: 900, frame: false,
    webPreferences: { contextIsolation: true, sandbox: true, backgroundThrottling: false },
  });
  const errors = [];
  win.webContents.on('console-message', (_e, level, message) => {
    if (level >= 3) errors.push(message);
  });
  await win.loadFile(path.join(dst, '_wp_index.html'));
  win.showInactive();
  await wait(600);

  const css = fs.readFileSync(path.join(PLUGIN, 'frontend.css'), 'utf8');
  const js = fs.readFileSync(path.join(PLUGIN, 'frontend.js'), 'utf8');
  await win.webContents.executeJavaScript(
    `(() => { const s = document.createElement('style'); s.id = 'plugin-css-wallpaper'; s.textContent = ${JSON.stringify(css)}; document.head.appendChild(s); })()`);
  await win.webContents.executeJavaScript(`(() => {
    window.VigilPlugin = { registerPage(def) { window.__wpTestPage = def; }, registerCleanup() {} };
    if (window.__wpStubApi) window.VigilAPI = window.__wpStubApi;
    window.vigil = window.vigil || {};
    window.vigil.plugins = Object.assign({}, window.vigil.plugins, {
      documentUrl: () => Promise.reject(new Error('在线壁纸不该走内联文档')),
      resourceUrl: () => Promise.reject(new Error('未缓存时不该去取媒体')),
      releaseResources() {},
    });
  })()`);
  const injected = await win.webContents.executeJavaScript(`(() => {
    try {
${js}
      return 'ok';
    } catch (e) { return String(e && e.message || e); }
  })()`);
  assert.equal(injected, 'ok', '插件前端注入失败：' + injected);

  await win.webContents.executeJavaScript(`(() => {
    const view = document.getElementById('view') || document.body;
    const page = window.__wpTestPage;
    if (!page) throw new Error('插件没有注册壁纸页');
    page.render({ view, topbar: document.getElementById('topbar'), panel: document.getElementById('panel'),
      src: 'pc', navigate() {}, refresh() {}, onDispose() {} });
  })()`);
  await wait(1500);

  const state = await win.webContents.executeJavaScript(`(() => {
    const cards = [...document.querySelectorAll('#wp4-grid [data-wp4-id]')];
    const cloud = cards.filter((b) => b.dataset.wp4Cloud);
    return {
      total: cards.length,
      cloud: cloud.length,
      rows: cloud.map((b) => {
        const img = b.querySelector('.vigil-wallpaper-thumb img');
        return {
          id: b.dataset.wp4Cloud,
          title: (b.querySelector('.vigil-wallpaper-card-title') || {}).textContent || '',
          meta: (b.querySelector('.vigil-wallpaper-card-meta') || {}).textContent || '',
          tag: (b.querySelector('.vigil-wallpaper-tag') || {}).textContent || '',
          w: img ? img.naturalWidth : -1,
          h: img ? img.naturalHeight : -1,
          src: img ? String(img.getAttribute('src') || '').slice(0, 16) : '',
        };
      }),
    };
  })()`);
  console.log('cards:', JSON.stringify(state, null, 1));

  assert.equal(state.cloud, items.length, '清单里 ' + items.length + ' 条，页面上只出现 ' + state.cloud + ' 张在线卡片');
  assert.equal(state.total, 3 + items.length, '总卡片数 = 3 内置 + ' + items.length + ' 在线，实际 ' + state.total);
  for (const row of state.rows) {
    assert.equal(row.tag, '在线', row.id + ' 缺少「在线」角标');
    assert.ok(/点击下载/.test(row.meta), row.id + ' 未下载时应提示体积，实际 ' + row.meta);
    assert.ok(/^data:image\//.test(row.src), row.id + ' 缩略图必须是内嵌 data URI，实际 ' + row.src);
    assert.ok(row.w > 0 && row.h > 0, row.id + ' 缩略图没解码出来（' + row.w + 'x' + row.h + '）');
    assert.ok(row.title && row.title !== row.id, row.id + ' 的标题没填（显示成 id 了）');
  }

  fs.writeFileSync(path.join(SHOTS, 'manifest-real.png'), (await win.webContents.capturePage()).toPNG());
  const fatal = errors.filter((m) => /Uncaught|TypeError|ReferenceError/.test(m));
  assert.deepEqual(fatal, [], '插件前端不应抛未捕获异常：' + fatal.join(' | '));

  console.log('PASS 真清单 ' + items.length + ' 条全部渲染成卡片，缩略图与标题齐全');
  win.destroy();
  app.quit();
})().catch((e) => {
  console.error('FAIL', (e && e.stack) || e);
  app.exit(1);
});
