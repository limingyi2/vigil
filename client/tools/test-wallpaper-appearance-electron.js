// 壁纸模式外观契约：玻璃主题只作用在"面板"上，列表项与输入框内的控件一律
// 透明无边框；设置窗口不挂壁纸也不挂玻璃主题。
// 跑法：env -u ELECTRON_RUN_AS_NODE ./node_modules/.bin/electron tools/test-wallpaper-appearance-electron.js
const { app, BrowserWindow } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');
const assert = require('node:assert/strict');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-wallpaper-appearance-'));
app.setPath('userData', path.join(tmp, 'userData'));
app.disableHardwareAcceleration();
const root = path.resolve(__dirname, '../..');
const plugin = path.join(root, 'market/plugins/wallpaper');
let win;
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const until = async (fn, timeout = 10000) => {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await fn()) return; await wait(50); }
  throw new Error('timeout');
};

// 夹具照着真 DOM 写：侧栏是 .li/.session-row → .session-open，输入框是
// .cbox → textarea + .cbar（.ibtn/.chip/.composer-model/#sendBtn），
// 底部那排是 .foot-chip。少写一层，测试就测不到"内框"。
const HTML = `<!doctype html><html lang="zh-CN" data-theme="light"><head><meta charset="utf-8"></head>
<body data-vigil-page="chat"><div class="app"><div class="titlebar"></div><div class="body">
  <nav class="gnav"><button class="gbtn on"></button></nav>
  <aside class="panel">
    <div class="session-new"><button class="session-new-button" id="newTopicBtn">新对话</button></div>
    <div class="li session-row on"><button class="session-open"><span class="tx"><b>会话一</b></span></button>
      <button class="ibtn session-rename">···</button></div>
    <div class="ws-row on"><button class="ws-open"><span class="tx"><b>zhixia</b></span></button></div>
    <div class="li session-row"><button class="session-open"><span class="tx"><b>会话二</b></span></button></div>
  </aside>
  <main class="main"><div class="chatwrap"><div class="chat">
    <div class="chat-messages"></div>
    <div class="composer"><div class="cbox">
      <textarea id="ta" rows="2"></textarea>
      <div class="cbar">
        <button class="ibtn cbar-add" id="clipBtn">+</button>
        <button class="chip" id="creatorBtn">创作：关</button>
        <button class="ibtn" id="localBtn">shield</button>
        <button class="btn composer-model" id="modelBtn">mimo</button>
        <button class="btn fill" id="sendBtn" disabled>send</button>
      </div>
    </div>
    <div class="composer-caption composer-foot">
      <button class="foot-chip">mimo</button><span class="foot-brand">Vigil</span><span class="ccount">0 字</span>
    </div></div></div>
    <div class="ac-card"></div>
    <div class="persona-view"><div class="pcard"></div><input class="input"></div>
    <div id="view"></div>
  </div></main>
</div><footer class="statusbar"></footer></div></body></html>`;

async function main() {
  await app.whenReady();
  fs.writeFileSync(path.join(tmp, 'index.html'), HTML);
  win = new BrowserWindow({ show: false, width: 900, height: 600, webPreferences: { contextIsolation: true, sandbox: true, backgroundThrottling: false } });
  win.webContents.on('console-message', (_e, level, message) => { if (level >= 2) console.error('[renderer]', message); });
  await win.loadFile(path.join(tmp, 'index.html'));
  for (const stylesheet of ['styles.css', 'chat.css', 'persona.css', 'agent-center.css']) {
    await win.webContents.insertCSS(fs.readFileSync(path.join(root, 'client/src', stylesheet), 'utf8'));
  }
  // 插件 CSS 必须走 <style>（真客户端就是这么注入的）。insertCSS 的规则在变量
  // 级联上会输给 chat.css 的 body[data-vigil-page="chat"]，用它测出来的配色是假的。
  const pluginCss = fs.readFileSync(path.join(plugin, 'frontend.css'), 'utf8');
  await win.webContents.executeJavaScript(`(()=>{const s=document.createElement('style');s.id='plugin-css-wallpaper';s.textContent=${JSON.stringify(pluginCss)};document.head.appendChild(s);})()`);
  await win.webContents.executeJavaScript(`
    const wallpaper = { id: '123', title: 'Appearance fixture', type: 'image', file: 'fixture.svg', preview: 'fixture.svg' };
    window.VigilAPI = {
      get: async path => path.startsWith('/api/plugin/wallpaper/library')
        ? {ok:true,json:{ok:true,we_path:'Fixture Workshop',wallpapers:[wallpaper]}}
        : {ok:true,json:{ok:true,config:{enabled:false,opacity:.52,scrim:.24,blur:0,fit:'cover',fps:60,muted:true,quality:'high'}}},
      post: async (_path, body) => ({ok:true,json:{ok:true,config:{enabled:true,...body}}})
    };
    window.vigil = {plugins:{resourceUrl:async()=>({ok:true,url:'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"%3E%3Crect width="64" height="64" fill="%2300aaff"%3E%3C/rect%3E%3C/svg%3E'}),releaseResources(){}}};
    window.VigilPlugin = {registerPage:page=>{window.__wallpaperPage=page;},registerCleanup:(_owner,fn)=>{window.__wallpaperDispose=fn;}};
    void 0;
  `);
  const pluginJs = fs.readFileSync(path.join(plugin, 'frontend.js'), 'utf8');
  await win.webContents.executeJavaScript(pluginJs);
  const ev = expression => win.webContents.executeJavaScript(expression).catch(error => { console.error('[eval]', expression.slice(0, 120), error); throw error; });
  await until(() => ev('!!window.__wallpaperPage'));
  await ev(`window.__wallpaperPage.render({view:document.getElementById('view')})`);
  await until(() => ev('document.querySelectorAll(".vigil-wallpaper-card").length===1'));
  await ev(`document.querySelector('[data-wp4-id="123"]').click()`);
  await until(() => ev('document.querySelector("#vigil-wallpaper-stage img")?.complete && document.querySelector("#vigil-wallpaper-stage img")?.naturalWidth===64'));

  assert.equal(await ev('document.body.classList.contains("vigil-wallpaper-active")'), true);
  assert.equal(await ev('document.body.classList.contains("vwp-active")'), true);

  await wait(500);   // 等 160ms 的 transition 跑完，否则量到的是中间值
  const style = await ev(`(() => {
    const probe = selector => {
      const el = document.querySelector(selector);
      if (!el) return { alpha: -1, border: -1 };
      const cs = getComputedStyle(el);
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
      const ctx = canvas.getContext('2d'); ctx.fillStyle = cs.backgroundColor; ctx.fillRect(0, 0, 1, 1);
      return { alpha: ctx.getImageData(0, 0, 1, 1).data[3], border: parseFloat(cs.borderTopWidth) || 0 };
    };
    return {
      shell: { app: probe('.app'), main: probe('.main'), panel: probe('.panel'), nav: probe('.gnav') },
      cards: { agent: probe('.ac-card'), persona: probe('.pcard'), control: probe('.vigil-wallpaper-control') },
      frames: { row: probe('.session-row:not(.on)'), sessionOpen: probe('.session-open'), wsOpen: probe('.ws-open'),
        newTopic: probe('#newTopicBtn'), chip: probe('#creatorBtn'), add: probe('#clipBtn'),
        model: probe('#modelBtn'), send: probe('#sendBtn'), foot: probe('.foot-chip') },
      outer: { cbox: probe('.cbox') },
    };
  })()`);

  // ① 壳必须全透，壁纸才看得见
  for (const [name, value] of Object.entries(style.shell)) {
    assert.equal(value.alpha, 0, `${name} must be transparent so the wallpaper shows through; got alpha ${value.alpha}`);
  }
  // ② 列表项 / 输入框内控件：不许有底色，也不许有边框（这几处就是"黑框"的来源）
  for (const [name, value] of Object.entries(style.frames)) {
    if (name === 'newTopic') continue;   // 侧栏顶部那颗"新对话"是独立按钮，保留细描边
    assert.equal(value.alpha, 0, `${name} must not paint its own surface (inner frame regression); got alpha ${value.alpha}`);
    assert.equal(value.border, 0, `${name} must not draw a border (inner frame regression); got ${value.border}px`);
  }
  // 侧栏顶部的"新对话"：可以有描边，但不许是实心块
  assert.equal(style.frames.newTopic.alpha, 0, 'the sidebar "新对话" button must not be a filled box');
  // ③ 卡片仍然是玻璃面板
  for (const [name, value] of Object.entries(style.cards)) {
    assert(value.alpha > 0 && value.alpha < 200, `${name} must stay visibly translucent; got alpha ${value.alpha}`);
  }
  // ④ 输入框只保留最外面那一个框
  assert(style.outer.cbox.alpha > 0, 'the composer must still have its single outer frame');
  assert(style.outer.cbox.border > 0, 'the composer outer frame must keep a hairline border');

  const layerOrder = await ev(`({ wallpaper: getComputedStyle(document.getElementById('vigil-wallpaper-root')).zIndex,
    app: getComputedStyle(document.querySelector('.app')).zIndex })`);
  assert.equal(layerOrder.wallpaper, '0');
  assert.equal(layerOrder.app, '1');

  // ⑤ 卸载要把状态收干净
  await ev('window.__vigilWallpaperV4Dispose()');
  assert.equal(await ev('document.body.classList.contains("vigil-wallpaper-active")'), false);
  assert.equal(await ev('document.body.classList.contains("vwp-active")'), false);
  assert.equal(await ev('!!document.getElementById("vigil-wallpaper-root")'), false);

  // ⑥ 设置窗口：只做清理，既不挂玻璃主题也不建壁纸层
  await ev(`history.replaceState(null, '', location.pathname + '?window=settings')`);
  await win.webContents.executeJavaScript(pluginJs);
  await wait(300);
  assert.equal(await ev('document.body.classList.contains("vigil-wallpaper-active")'), false,
    'the settings window must not wear the wallpaper glass theme (dark gradient + light page = black frame)');
  assert.equal(await ev('!!document.getElementById("vigil-wallpaper-root")'), false,
    'the settings window must not build a wallpaper layer');

  console.log('PASS: shell transparent, no inner frames on list/composer, single composer frame, settings window stays on the plain theme');
}

main().then(() => cleanup(0), error => { console.error(error); cleanup(1); });
function cleanup(code) { if (win) win.destroy(); app.exit(code); }
