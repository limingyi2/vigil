// 在线壁纸契约测试（真客户端页面 + 真插件前端 + 真 HTTP 媒体流）
//
// 验的是行为，不是字符串：
//   ① 清单里的在线壁纸要出现在库里，带「在线」角标和体积提示；
//   ② 点它必须先下载（卡片进 downloading 态、出现进度条），而不是直接挂空 src；
//   ③ 下载完成后自动挂上 <video>，src 指向媒体地址，且真的在播放（currentTime 前进）；
//   ④ 媒体请求必须带 Range —— 没有 206 就没法循环/seek。
//
// 跑法：env -u ELECTRON_RUN_AS_NODE ./node_modules/.bin/electron tools/test-wallpaper-cloud-electron.js
const { app, BrowserWindow } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const assert = require('assert');

const CLIENT = path.resolve(__dirname, '..');
const PLUGIN = 'F:/zhixia/market/plugins/wallpaper';
const SHOTS = 'F:/zhixia/_tmp/wallpaper-fix';
const MEDIA = 'F:/zhixia/_tmp/wallpaper-cloud/assets/test-93001.mp4';
const MEDIA_BYTES = fs.statSync(MEDIA).size;
const MEDIA_DATA = fs.readFileSync(MEDIA);
// 清单里的 poster（真清单由 make-cloud-manifest.py 抽帧生成），这里缩到 48 宽。
const THUMB = 'data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAA4KCw0LCQ4NDA0QDw4RFiQXFhQUFiwgIRokNC43NjMuMjI6QVNGOj1OPjIySGJJTlZYXV5dOEVmbWVabFNbXVn/2wBDAQ8QEBYTFioXFypZOzI7WVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVlZWVn/wAARCAAbADADASIAAhEBAxEB/8QAGAABAAMBAAAAAAAAAAAAAAAABAIDBQb/xAAnEAACAQQCAQMEAwAAAAAAAAABAgMABBESITEFQVFhExUisTNScf/EABkBAQACAwAAAAAAAAAAAAAAAAMCBAABBf/EABwRAAIDAAMBAAAAAAAAAAAAAAABAhEhAxIxQf/aAAwDAQACEQMRAD8A4iZVEuJFGw9R0af4mKS5vYra2QtucamhxySEanBHtXQWc/2PxxuUCi+u+I+P44x23+k/quhxrbKsvKNu+u7TwEC2kWGc/k3PLH3PxXL+R8rc+RYqJtVOfxXgYoN5cG4YyPsznsk91Cy+mHdpdwupxrSKdPrElHzCq+iEUoQMGwB160cQOVdyMBTg0p7YqElYHSUHQmo3EMaBdX2/dZyRzsPVI3IIoGkRpI275we6R5WJL25LQo6qAFUE9AVRZMSuTyfkUieZ0XKnBx6ClUYvShvhmS+LlUjAJU+vVQXx8ig7ED57qye7nZuZW6oM1zMQQZGOK01BbQkVJ/R7WsQtlkLLvkrrgccd0WSOP+yKB7nk0GSR8A7HOtHd2PZNQfIop4N1b+n/2Q==';

app.disableHardwareAcceleration();
app.on('window-all-closed', () => {});
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const FIXTURE = `
(() => {
  const state = {
    config: { enabled: false, wallpaper_id: null, wallpaper_type: null, wallpaper_file: null,
      opacity: 1, scrim: 0, blur: 0, fit: 'cover', fps: 60, muted: true, quality: 'high',
      transparent: true, carousel_enabled: false, carousel_minutes: 30, carousel_shuffle: false,
      we_path: null },
    patches: [],
  };
  // 模拟一次真实下载：分 4 步推进，约 800ms 完成，好让测试观察到"下载中"这一态。
  const cloud = { fetched: false, cached: false, got: 0, total: ${MEDIA_BYTES} };
  window.__wpTest = state;
  window.__wpCloud = cloud;
  const reply = (json) => ({ ok: true, json });
  window.__wpStubApi = {
    get(p) {
      const s = String(p);
      if (s.indexOf('/config') >= 0) return Promise.resolve(reply({ ok: true, config: state.config }));
      if (s.indexOf('/library') >= 0) return Promise.resolve(reply({ ok: true, we_path: null, count: 0, wallpapers: [] }));
      if (s.indexOf('/cloud') >= 0) {
        const item = { id: '93001', title: '流星（测试）', type: 'video', file: '93001.mp4',
          bytes: cloud.total, total: cloud.total, credit: 'test', preview: '${THUMB}',
          state: cloud.cached ? 'cached' : (cloud.fetched ? 'downloading' : 'remote'),
          got: cloud.got, percent: cloud.cached ? 100 : Math.round(cloud.got * 100 / cloud.total) };
        return Promise.resolve(reply({ ok: true, count: 1, wallpapers: [item] }));
      }
      return Promise.resolve(reply({ ok: true }));
    },
    post(p, body) {
      const s = String(p);
      if (s.indexOf('/cloud/fetch') >= 0) {
        if (!cloud.fetched) {
          cloud.fetched = true;
          let step = 0;
          const timer = setInterval(() => {
            step += 1;
            cloud.got = Math.min(cloud.total, Math.round(cloud.total * step / 4));
            if (step >= 4) { clearInterval(timer); cloud.cached = true; }
          }, 200);
        }
        return Promise.resolve(reply({ ok: true }));
      }
      state.config = Object.assign({}, state.config, body || {});
      state.patches.push(body || {});
      return Promise.resolve(reply({ ok: true, config: state.config }));
    },
  };
  window.VigilAPI = window.__wpStubApi;
  // 应用壳启动时会摸这些成员（见 js/app.js），给最小可用空实现即可。
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
      // 真客户端的 resourceUrl 会把 /api/plugin/... 换成票据地址；这里直接给媒体端。
      resourceUrl: () => Promise.resolve({ ok: true, url: window.__mediaUrl }),
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

  const hits = [];   // 记录媒体请求的 Range 覆盖情况
  const server = http.createServer((req, res) => {
    if ((req.url || '').startsWith('/media/')) {
      const range = req.headers.range || '';
      hits.push(range);
      if (!range) {
        res.writeHead(200, { 'Content-Type': 'video/mp4', 'Content-Length': MEDIA_DATA.length, 'Accept-Ranges': 'bytes' });
        res.end(MEDIA_DATA);
        return;
      }
      const m = /^bytes=(\d*)-(\d*)$/.exec(range.trim());
      const start = m && m[1] ? Number(m[1]) : 0;
      const end = m && m[2] ? Math.min(Number(m[2]), MEDIA_DATA.length - 1) : MEDIA_DATA.length - 1;
      res.writeHead(206, {
        'Content-Type': 'video/mp4',
        'Content-Length': end - start + 1,
        'Content-Range': 'bytes ' + start + '-' + end + '/' + MEDIA_DATA.length,
        'Accept-Ranges': 'bytes',
      });
      res.end(MEDIA_DATA.subarray(start, end + 1));
      return;
    }
    res.writeHead(404);
    res.end('nope');
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const port = server.address().port;

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wp-cloud-page-'));
  const dst = path.join(tmp, 'src');
  fs.cpSync(path.join(CLIENT, 'src'), dst, { recursive: true });
  fs.writeFileSync(path.join(dst, '_wp_fixture.js'),
    'window.__mediaUrl = "http://127.0.0.1:' + port + '/media/93001.mp4";\n' + FIXTURE);
  const html = fs.readFileSync(path.join(dst, 'index.html'), 'utf8')
    .replace('<script src="js/ui.js"></script>',
      '<script src="_wp_fixture.js"></script>\n<script src="js/ui.js"></script>');
  fs.writeFileSync(path.join(dst, '_wp_index.html'), html);

  const win = new BrowserWindow({
    show: true, x: -5000, y: 0, width: 1440, height: 860, frame: false,
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
  await win.webContents.insertCSS(fs.readFileSync(path.join(dst, 'styles.css'), 'utf8'));
  await win.webContents.executeJavaScript(
    `(() => { const s = document.createElement('style'); s.id = 'plugin-css-wallpaper'; s.textContent = ${JSON.stringify(css)}; document.head.appendChild(s); })()`);
  await win.webContents.executeJavaScript(`(() => {
    window.VigilPlugin = { registerPage(def) { window.__wpTestPage = def; }, registerCleanup() {} };
    if (window.__wpStubApi) window.VigilAPI = window.__wpStubApi;
    window.vigil = window.vigil || {};
    window.vigil.plugins = Object.assign({}, window.vigil.plugins, {
      documentUrl: () => Promise.reject(new Error('在线壁纸不该走内联文档')),
      resourceUrl: () => Promise.resolve({ ok: true, url: window.__mediaUrl }),
      releaseResources() {},
    });
  })()`);
  const injected = await win.webContents.executeJavaScript(`(() => {
    try {
${js}
      return 'ok';
    } catch (e) { return 'ERR ' + String(e && (e.stack || e.message) || e); }
  })()`);
  assert.equal(injected, 'ok', '插件前端注入失败：' + String(injected).slice(0, 500));
  await wait(600);

  await win.webContents.executeJavaScript(`(() => {
    const view = document.getElementById('view') || document.body;
    const page = window.__wpTestPage;
    if (!page) throw new Error('插件没有注册壁纸页');
    page.render({ view, topbar: document.getElementById('topbar'), panel: document.getElementById('panel'),
      src: 'pc', navigate() {}, refresh() {}, onDispose() {} });
  })()`);
  await wait(1000);

  // ① 在线壁纸出现在库里
  const pageState = await win.webContents.executeJavaScript(`(() => {
    const cards = [...document.querySelectorAll('#wp4-grid [data-wp4-id]')];
    const cloudCard = document.querySelector('#wp4-grid [data-wp4-cloud="93001"]');
    const thumb = cloudCard ? cloudCard.querySelector('.vigil-wallpaper-thumb img') : null;
    return {
      cards: cards.length,
      ids: cards.map((b) => b.dataset.wp4Id),
      hasCloud: !!cloudCard,
      cloudTag: cloudCard ? (cloudCard.querySelector('.vigil-wallpaper-tag') || {}).textContent || '' : '',
      cloudMeta: cloudCard ? (cloudCard.querySelector('.vigil-wallpaper-card-meta') || {}).textContent || '' : '',
      // 缩略图必须在下载之前就显示：靠清单里的 poster → preview → img.src
      thumbSrc: thumb ? String(thumb.getAttribute('src') || '').slice(0, 23) : '',
      thumbW: thumb ? thumb.naturalWidth : -1,
      thumbH: thumb ? thumb.naturalHeight : -1,
    };
  })()`);
  console.log('page:', JSON.stringify(pageState));
  assert.equal(pageState.cards, 4, '3 张内置 + 1 张在线，实际 ' + pageState.cards + ' 张：' + pageState.ids.join(','));
  assert.ok(pageState.hasCloud, '在线壁纸必须出现在壁纸库里');
  assert.equal(pageState.cloudTag, '在线', '在线卡片要有「在线」角标，实际 ' + pageState.cloudTag);
  assert.ok(/点击下载/.test(pageState.cloudMeta) && /MB/.test(pageState.cloudMeta),
    '未下载时应提示体积，实际 ' + pageState.cloudMeta);
  assert.ok(/^data:image\//.test(pageState.thumbSrc),
    '在线卡片要有缩略图，src 必须是清单内嵌的 data URI，实际 ' + pageState.thumbSrc);
  assert.ok(pageState.thumbW > 0 && pageState.thumbH > 0,
    '缩略图必须真的解码出来（naturalWidth/Height > 0），实际 '
    + pageState.thumbW + 'x' + pageState.thumbH);
  fs.writeFileSync(path.join(SHOTS, 'cloud-page.png'), (await win.webContents.capturePage()).toPNG());

  // ② 点它 → 先进入下载态（不能直接挂空 src）
  await win.webContents.executeJavaScript(`document.querySelector('#wp4-grid [data-wp4-cloud="93001"]').click();`);
  await wait(350);
  const during = await win.webContents.executeJavaScript(`(() => {
    const card = document.querySelector('#wp4-grid [data-wp4-cloud="93001"]');
    return {
      meta: card ? (card.querySelector('.vigil-wallpaper-card-meta') || {}).textContent || '' : '',
      bar: !!(card && card.querySelector('.vigil-wallpaper-progress')),
      busy: !!(card && card.classList.contains('is-downloading')),
      stageVideos: document.querySelectorAll('#vigil-wallpaper-stage video').length,
      fetched: window.__wpCloud.fetched,
    };
  })()`);
  console.log('during:', JSON.stringify(during));
  assert.ok(during.fetched, '点选后必须发起下载');
  assert.ok(/下载中|排队中/.test(during.meta), '下载期间卡片要显示进度，实际 ' + during.meta);
  assert.ok(during.bar && during.busy, '下载期间要有进度条和 is-downloading 标记');
  fs.writeFileSync(path.join(SHOTS, 'cloud-downloading.png'), (await win.webContents.capturePage()).toPNG());

  // ③ 下载完成 → 自动挂上 video 并真的在播
  await wait(2200);
  const mounted = await win.webContents.executeJavaScript(`(() => {
    const v = document.querySelector('#vigil-wallpaper-stage video');
    return {
      has: !!v,
      count: document.querySelectorAll('#vigil-wallpaper-stage video').length,
      src: v ? String(v.currentSrc || v.src).slice(0, 60) : '',
      paused: v ? v.paused : null,
      muted: v ? v.muted : null,
      loop: v ? v.loop : null,
      time: v ? v.currentTime : 0,
      ready: v ? v.readyState : -1,
      cached: window.__wpCloud.cached,
    };
  })()`);
  console.log('mounted:', JSON.stringify(mounted));
  assert.ok(mounted.has, '下载完成后必须自动挂上 <video>');
  assert.equal(mounted.count, 1, '同一时刻只允许一个壁纸层');
  assert.match(mounted.src, /^http:\/\/127\.0\.0\.1:\d+\/media\//, 'video.src 必须指向媒体地址，实际 ' + mounted.src);
  assert.equal(mounted.loop, true, '壁纸视频必须循环');
  assert.equal(mounted.muted, true, '壁纸视频默认静音');

  await wait(1200);
  const playing = await win.webContents.executeJavaScript(`(() => {
    const v = document.querySelector('#vigil-wallpaper-stage video');
    return { time: v ? v.currentTime : 0, ready: v ? v.readyState : -1, paused: v ? v.paused : null };
  })()`);
  console.log('playing:', JSON.stringify(playing));
  assert.ok(playing.ready >= 2, 'video 没有可播放数据（readyState ' + playing.ready + '）');
  assert.ok(playing.time > 0.05, '视频没有在播放（currentTime ' + playing.time + '）');

  // ④ 必须带 Range：没有 206 就没法循环/seek
  assert.ok(hits.length > 0, '媒体应当至少被请求一次');
  assert.ok(hits.some((r) => /^bytes=/.test(r)), '媒体请求必须带 Range 头，实际 ' + JSON.stringify(hits));
  fs.writeFileSync(path.join(SHOTS, 'cloud-mounted.png'), (await win.webContents.capturePage()).toPNG());

  const patches = await win.webContents.executeJavaScript('window.__wpTest.patches');
  const last = patches[patches.length - 1] || {};
  assert.equal(last.enabled, true, '点选在线壁纸必须把 enabled 打开');
  assert.equal(String(last.wallpaper_id), '93001', '必须记住在线壁纸 id，实际 ' + last.wallpaper_id);
  assert.equal(last.wallpaper_file, '93001.mp4', '必须记住在线壁纸文件名');

  const fatal = errors.filter((m) => /Uncaught|TypeError|ReferenceError/.test(m));
  assert.deepEqual(fatal, [], '插件前端不应抛未捕获异常：' + fatal.join(' | '));

  console.log('PASS 在线壁纸：入库带角标、点选先下载、完成自动播放、媒体走 Range');
  win.destroy();
  server.close();
  app.quit();
})().catch((e) => {
  console.error('FAIL', (e && e.stack) || e);
  app.exit(1);
});
