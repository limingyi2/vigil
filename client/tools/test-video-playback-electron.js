// 真 Electron 里验证「选片 → 播放」这条链能不能出画面。
//
// 这个夹具存在的理由：票据服务器本身（Range/206）已经有 node 级用例，
// 但真正决定能不能播的还有两道只在真窗口里成立的关卡 ——
// 会话级 webRequest 回环闸门、以及页面 CSP 的 media-src。
// 任何一道把地址拦掉，播放器都只会报「此视频无法播放」，而 node 用例全绿。
//
// 用法：
//   env -u ELECTRON_RUN_AS_NODE ./node_modules/.bin/electron tools/test-video-playback-electron.js <video>
const { app, BrowserWindow } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { createVideoMedia } = require('../electron/video-media');
const { createPluginResources, shouldCancelLoopbackRequest } = require('../electron/plugin-resource');

const SOURCE = process.argv.find((a, i) => i > 1 && /\.(mp4|mkv|avi|mov|webm)$/i.test(a))
  || path.join(__dirname, '..', '..', '_tmp', 'wallpaper-cloud', 'assets', 'test-93001.mp4');

const PAGE = `<!doctype html><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: http: https:; media-src 'self' blob: http://127.0.0.1:*; frame-src http://127.0.0.1:*; connect-src http://127.0.0.1:*; object-src 'none'; base-uri 'none'">
<video id="v" preload="auto"></video>`;

function log(...args) { process.stdout.write(args.join(' ') + '\n'); }

app.on('window-all-closed', () => { /* 手动收尾 */ });

app.whenReady().then(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-play-'));
  const page = path.join(dir, 'page.html');
  fs.writeFileSync(page, PAGE);

  const media = createVideoMedia();
  const pluginResources = createPluginResources({ getBase: () => 'http://127.0.0.1:1', getToken: async () => '' });

  const win = new BrowserWindow({
    width: 640, height: 400, show: false,
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
  });

  // 与 main.js 里那条闸门保持一致
  const rendererId = win.webContents.id;
  const approved = (raw) => pluginResources.isApprovedUrl(raw) || media.isApprovedUrl(raw);
  win.webContents.session.webRequest.onBeforeRequest(
    { urls: ['http://127.0.0.1/*', 'http://localhost/*', 'http://[::1]/*'] },
    (details, callback) => {
      const cancelled = shouldCancelLoopbackRequest(details, rendererId, approved);
      if (cancelled) log('[gate] cancel ' + details.url.replace(/[a-f0-9]{48}/, '<ticket>'));
      callback(cancelled ? { cancel: true } : {});
    });

  let failed = 0;
  win.webContents.on('console-message', (_e, level, message) => {
    if (level >= 2 || /fail|error|denied|refused/i.test(message)) log('[renderer] ' + message);
  });

  const info = await media.share(SOURCE, { onProgress: (s) => log('[prepare] ' + s.phase + ' ' + (s.percent || 0) + '% ' + s.message) });
  log('[share] ok=' + info.ok + ' size=' + info.size + ' converted=' + (info.converted || '-') + ' note=' + (info.note || '-'));

  await win.loadFile(page);
  const result = await win.webContents.executeJavaScript(`new Promise((resolve) => {
    const v = document.getElementById('v');
    const done = (tag) => resolve({ tag, readyState: v.readyState, width: v.videoWidth, height: v.videoHeight,
      duration: Number.isFinite(v.duration) ? v.duration : null,
      error: v.error ? v.error.code + ' ' + (v.error.message || '') : null, networkState: v.networkState });
    v.addEventListener('loadeddata', () => done('loadeddata'), { once: true });
    v.addEventListener('error', () => done('error'), { once: true });
    v.src = ${JSON.stringify(info.url)};
    v.load();
    setTimeout(() => done('timeout'), 15000);
  })`);

  log('[video] ' + JSON.stringify(result));
  if (result.tag !== 'loadeddata' || !result.width) failed = 1;

  try { win.destroy(); } catch (_) { /* 已关 */ }
  media.close();
  pluginResources.close();
  fs.rmSync(dir, { recursive: true, force: true });
  log(failed ? '[FAIL] 播放器出不了画面' : '[PASS] 播放器出画面 ' + result.width + 'x' + result.height);
  app.exit(failed);
});
