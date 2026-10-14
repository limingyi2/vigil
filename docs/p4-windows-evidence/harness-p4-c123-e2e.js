// C1/C2/C3 修复的真实 UI 验收：真实 Electron(渲染层拖拽/IPC) + 真实 Brain HTTP
// + 真实 Node Skill Host(Bearer)。不 mock API、不用 fixture 替代渲染层。
//
// 启动机制从 harness-p4-e2e.js 原样复用（同一套隔离环境：临时 VIGIL_HOME /
// 临时 userData / 临时技能目录 / 随机测试 Token）。本文件只加 C123 用例。
// 证据落到 docs/p4-windows-evidence/<sha>-c123/（与 28 项轮互不覆盖）。
// 2026-10-13：目录名按实际 HEAD 动态生成（同 28 项 harness 的 commitNow），
// 修复提交复跑时自动进 <sha>-c123/，两轮证据并存。
const { spawn, execSync } = require('child_process');
const path = require('path'), fs = require('fs'), os = require('os'), http = require('http');
const WT = 'F:\\sync-checks\\creator-mode-p4-4b-2';
const CLIENT = path.join(WT, 'client');
const WebSocket = require(path.join(CLIENT, 'node_modules', 'ws'));
const ELECTRON = path.join(CLIENT, 'node_modules', 'electron', 'dist', 'electron.exe');
const ROOT = path.join(os.tmpdir(), 'vigil_p4');
const PKGS = path.join(ROOT, 'pkgs');

function commitNow() {
  try {
    const fsx = require('fs'), pathx = require('path');
    const gp = pathx.join(WT, '.git');
    let gd = gp;
    try {
      const dot = fsx.readFileSync(gp, 'utf8').trim();
      if (dot.startsWith('gitdir:')) gd = dot.slice(7).trim();
    } catch (_) {}
    let common = gd;
    try {
      const c = fsx.readFileSync(pathx.join(gd, 'commondir'), 'utf8').trim();
      common = pathx.resolve(gd, c);
    } catch (_) {}
    let head = fsx.readFileSync(pathx.join(gd, 'HEAD'), 'utf8').trim();
    if (head.startsWith('ref:')) {
      const ref = head.slice(4).trim();
      for (const base of [gd, common]) {
        const p = pathx.join(base, ref);
        if (fsx.existsSync(p)) { head = fsx.readFileSync(p, 'utf8').trim(); break; }
      }
    }
    return /^[0-9a-f]{40}$/.test(head) ? head.slice(0, 7) : 'c0346e5';
  } catch (_) { return 'c0346e5'; }
}
const EV = path.join(WT, 'docs', 'p4-windows-evidence', commitNow() + '-c123');
fs.mkdirSync(EV, { recursive: true });
const BRAIN_PORT = 18790, HOST_PORT = 8791, CDP = 9357;
const HOME = path.join(ROOT, 'home');
const PLUG_DIR = path.join(HOME, 'plugins');
const SKILL_DIR = path.join(ROOT, 'skills');
const HB = path.join(HOME, 'p4plugbg_heartbeat.log');
const ST_PLUGIN = path.join(PLUG_DIR, '_pending_restart.json');
const ST_SKILL = path.join(SKILL_DIR, '_pending_restart.json');
const tokens = Object.fromEntries(fs.readFileSync(path.join(ROOT, 'root.txt'), 'utf8')
  .split(/\r?\n/).filter(Boolean).map((l) => l.split('=')));
const BTOK = tokens.brain_token, STOK = tokens.skill_token;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(...a);
const results = [];
const rec = (id, ok, detail) => { results.push({ id, ok, detail }); log((ok ? '  OK  ' : '  FAIL ') + id + '  ' + String(detail || '').slice(0, 260)); };

// ── 隔离环境探针：证明凭据/目录都没碰到生产 ────────────────────────
function isolationProof() {
  const probe = {
    VIGIL_HOME: process.env.VIGIL_HOME || null,
    root_under_temp: ROOT.startsWith(os.tmpdir()),
    brain_token_is_40hex: /^[0-9a-f]{40}$/.test(BTOK || ''),
    skill_token_is_40hex: /^[0-9a-f]{40}$/.test(STOK || ''),
    brain_port: BRAIN_PORT, host_port: HOST_PORT,
  };
  log('  [隔离] ' + JSON.stringify(probe));
  return probe;
}

let brainProc = null, hostProc = null;

function killPort(port) {
  try {
    execSync('powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort ' + port
      + ' -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"',
      { stdio: 'ignore' });
  } catch (_) {}
}

// 🔴 同 28 项 harness：内建插件必须从 worktree 同步进隔离 HOME 并清掉
// __pycache__。2026-10-13 教训：临时 HOME 里的旧副本让大脑加载了
// c0346e5 之前的 skill.py（没有 note_change），C123 技能 scope 整条
// 假失败；PYTHONDONTWRITEBYTECODE=1 的环境里旧 .pyc 还会被继续使用。
// 不同步 + 不清缓存，"跑的哪份代码"说不清，证据无效。
function syncBuiltinPlugins() {
  const src = path.join(WT, 'brain', 'plugins');
  const dst = path.join(ROOT, 'home', 'plugins');
  fs.mkdirSync(dst, { recursive: true });
  fs.rmSync(path.join(dst, '__pycache__'), { recursive: true, force: true });
  let n = 0;
  for (const f of fs.readdirSync(src)) {
    if (!f.endsWith('.py') || f.startsWith('_test') || /^p4plug/.test(f)) continue;
    fs.copyFileSync(path.join(src, f), path.join(dst, f));
    n++;
  }
  log('  [同步] 内建插件 ' + n + ' 个 → 隔离 HOME（清掉旧副本与 __pycache__）');
}

function startBrain() {
  const env = {
    ...process.env,
    VIGIL_HOME: HOME, VIGIL_PORT: String(BRAIN_PORT),
    ZX_SKILL_AUTH_TOKEN: STOK, ZX_SKILL_DIR: SKILL_DIR,
    ZX_SKILL_HOST: '127.0.0.1', ZX_SKILL_PORT: String(HOST_PORT),
    PYTHONUNBUFFERED: '1',
  };
  const out = fs.openSync(path.join(ROOT, 'logs', 'brain_c123.log'), 'a');
  brainProc = spawn('python', [path.join(WT, 'brain', 'server.py')],
    { cwd: path.join(WT, 'brain'), env, stdio: ['ignore', out, out] });
  return brainProc;
}

function startHost(token) {
  const env = {
    ...process.env,
    ZX_SKILL_DIR: SKILL_DIR, ZX_SKILL_PORT: String(HOST_PORT),
    ZX_SKILL_HOST: '127.0.0.1', ZX_SKILL_AUTH_TOKEN: token,
  };
  const out = fs.openSync(path.join(ROOT, 'logs', 'host_c123.log'), 'a');
  hostProc = spawn('node', [path.join(WT, 'brain', 'skill_host', 'server.js')],
    { cwd: WT, env, stdio: ['ignore', out, out] });
  return hostProc;
}

async function waitPort(port, ms = 20000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const ok = await new Promise((r) => {
      const req = http.get({ host: '127.0.0.1', port, path: '/', timeout: 800 },
        (x) => { x.resume(); r(true); });
      req.on('error', () => r(false)); req.on('timeout', () => { req.destroy(); r(false); });
    });
    if (ok) return true;
    await sleep(400);
  }
  return false;
}

function brainApi(method, p, body) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const req = http.request({
      host: '127.0.0.1', port: BRAIN_PORT, path: p, method,
      headers: { Authorization: 'Bearer ' + BTOK,
        ...(data ? { 'Content-Type': 'application/json', 'Content-Length': data.length } : {}) },
      timeout: 60000,
    }, (x) => {
      let d = ''; x.on('data', (c) => (d += c));
      x.on('end', () => { try { resolve({ status: x.statusCode, json: JSON.parse(d) }); } catch (_) { resolve({ status: x.statusCode, raw: d }); } });
    });
    req.on('error', reject); req.on('timeout', () => { req.destroy(); reject(new Error('api timeout')); });
    if (data) req.write(data);
    req.end();
  });
}

function hostApi(method, p, body, token = STOK) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const headers = token ? { Authorization: 'Bearer ' + token } : {};
    if (data) { headers['Content-Type'] = 'application/json'; headers['Content-Length'] = data.length; }
    const req = http.request({ host: '127.0.0.1', port: HOST_PORT, path: p, method, headers, timeout: 30000 },
      (x) => { let d = ''; x.on('data', (c) => (d += c));
        x.on('end', () => { try { resolve({ status: x.statusCode, json: JSON.parse(d) }); } catch (_) { resolve({ status: x.statusCode, raw: d }); } });
      });
    req.on('error', reject); req.on('timeout', () => { req.destroy(); reject(new Error('host timeout')); });
    if (data) req.write(data);
    req.end();
  });
}

let app = null, ctl = null, sendCdp;
async function startElectron() {
  const prof = path.join(ROOT, 'profile');
  fs.rmSync(prof, { recursive: true, force: true });
  // 同 28 项 harness：摘掉 ELECTRON_RUN_AS_NODE 等 ELECTRON_* 覆盖，
  // 否则 electron.exe 退化成普通 Node，主进程起不来（CDP 连不上）。
  const eenv = { ...process.env, VIGIL_SIDECAR_DISABLE: '1' };
  delete eenv.ELECTRON_RUN_AS_NODE;
  for (const k of Object.keys(eenv)) {
    if (k.startsWith('ELECTRON_')) delete eenv[k];
  }
  const elog = fs.openSync(path.join(ROOT, 'logs', 'electron_c123.log'), 'a');
  app = spawn(ELECTRON, ['.', '--user-data-dir=' + prof, '--remote-debugging-port=' + CDP,
    '--disable-gpu', '--no-sandbox'],
    { cwd: CLIENT, stdio: ['ignore', elog, elog], env: eenv });
  let t = null;
  for (let i = 0; i < 60 && !t; i++) {
    try {
      const l = await new Promise((r, j) => http.get('http://127.0.0.1:' + CDP + '/json', (x) => {
        let d = ''; x.on('data', (c) => (d += c)); x.on('end', () => r(JSON.parse(d)));
      }).on('error', j));
      t = l.find((x) => x.type === 'page');
    } catch (_) { await sleep(500); }
  }
  if (!t) throw new Error('CDP 连不上');
  await new Promise((resolve, reject) => {
    ctl = new WebSocket(t.webSocketDebuggerUrl);
    let id = 0; const w = new Map();
    ctl.on('message', (raw) => { let m; try { m = JSON.parse(raw); } catch (_) { return; }
      if (w.has(m.id)) w.get(m.id)(m), w.delete(m.id); });
    ctl.on('open', resolve); ctl.on('error', reject);
    sendCdp = (method, params) => new Promise((r) => { const i = ++id; w.set(i, r);
      ctl.send(JSON.stringify({ id: i, method, params })); });
  });
  await sendCdp('Runtime.enable');
  await sendCdp('Page.enable');
}
async function ev(expr) {
  const r = await sendCdp('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) return { __error: (r.exceptionDetails.exception && r.exceptionDetails.exception.description) || r.exceptionDetails.text };
  return r.result && r.result.result ? r.result.result.value : undefined;
}
const until = async (expr, ms = 20000) => {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (await ev('Boolean(' + expr + ')')) return; await sleep(120); }
  throw new Error('等待超时: ' + expr);
};
async function shot(name) {
  await sleep(250);
  const r = await sendCdp('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(EV, name + '.png'), Buffer.from(r.result.data, 'base64'));
  log('  [截图] ' + name + '.png');
}
async function toastWatch() {
  return ev(`(function(){
    if (!window.__toasts) {
      window.__toasts = [];
      new MutationObserver(function(muts){
        muts.forEach(function(m){ m.addedNodes.forEach(function(n){
          window.__toasts.push({ t: Date.now(), x: n.textContent });
        });});
      }).observe(document.getElementById('toast'), { childList: true });
    }
    return window.__toasts.length;
  })()`);
}
async function waitToast(mark, ms = 12000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const v = await ev(`(function(){
      const a = window.__toasts || [];
      for (let i = a.length - 1; i >= 0; i--) {
        if (i >= ${mark} && a[i].x && !/正在装/.test(a[i].x)) return a[i].x;
      }
      return '';
    })()`);
    if (v) return v;
    await sleep(200);
  }
  return '';
}
async function gotoAbility(tab) {
  await ev(`document.querySelector('#gnav [data-page="ability"]').click()`);
  await until(`document.querySelector('.plist .li[data-k="${tab}"]')`);
  await ev(`document.querySelector('.plist .li[data-k="${tab}"]').click()`);
  await until(tab === 'pl' ? "document.querySelector('#plDrop')" : "document.querySelector('#skDrop')");
  await sleep(400);
}
async function dropZip(zoneSel, zipFile) {
  const b64 = fs.readFileSync(path.join(PKGS, zipFile)).toString('base64');
  await ev(`(async()=>{
    window.confirm=()=>true;
    const bin=Uint8Array.from(atob(${JSON.stringify(b64)}),c=>c.charCodeAt(0));
    const f=new File([bin],${JSON.stringify(zipFile)},{type:'application/zip'});
    const dt=new DataTransfer(); dt.items.add(f);
    const el=document.querySelector(${JSON.stringify(zoneSel)});
    el.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:dt}));
    el.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));
    return 1;
  })()`);
}
async function abText() {
  return ev(`(document.querySelector('#abView')||{textContent:''}).textContent`);
}
const pluginBanner = async () => /待重启大脑/.test(await abText());
const skillBanner = async () => /待重启技能宿主/.test(await abText());
async function waitAppReady() {
  await until(`window.vigil && document.querySelector('#gnav')`, 30000);
  for (let i = 0; i < 30; i++) {
    const who = await ev('window.vigil.device.whoami()');
    if (who && new RegExp(BRAIN_PORT).test(who.base || '')) return who;
    await sleep(700);
  }
  throw new Error('客户端没连上测试 Brain');
}
async function restartBrain() {
  log('  [Brain] 重启…');
  if (brainProc) { try { brainProc.kill(); } catch (_) {} brainProc = null; }
  killPort(BRAIN_PORT); await sleep(2500);
  startBrain();
  await untilBrainReady();
}
async function restartHost() {
  log('  [Host] 重启…');
  if (hostProc) { try { hostProc.kill(); } catch (_) {} hostProc = null; }
  killPort(HOST_PORT); await sleep(2000);
  startHost(STOK);
  if (!(await waitPort(HOST_PORT, 15000))) throw new Error('host 重启失败');
  await sleep(1200);
}
async function untilBrainReady() {
  const ok = await waitPort(BRAIN_PORT);
  for (let i = 0; i < 30; i++) {
    try { const r = await brainApi('GET', '/api/plugins'); if (r.status === 200) return; } catch (_) {}
    await sleep(500);
  }
  if (!ok) throw new Error('brain 未监听');
}
function readState(p) {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (_) { return null; }
}
function resetStates() {
  for (const p of [ST_PLUGIN, ST_SKILL]) { try { fs.unlinkSync(p); } catch (_) {} }
}
function hbSize() { try { return fs.statSync(HB).size; } catch (_) { return 0; } }
async function hbGrows(ms) {
  const a = hbSize(); await sleep(ms); const b = hbSize();
  return { grows: b > a, a, b };
}
function cleanTestArtifacts() {
  for (const n of fs.readdirSync(SKILL_DIR)) {
    if (n.startsWith('_') || n.startsWith('.')) continue;
    fs.rmSync(path.join(SKILL_DIR, n), { recursive: true, force: true });
  }
  for (const n of fs.readdirSync(PLUG_DIR)) {
    if (/^p4plug(bg)?\.py$/.test(n) || n.startsWith('_p4plug')) {
      fs.rmSync(path.join(PLUG_DIR, n), { force: true });
    }
  }
  try {
    const rp = path.join(PLUG_DIR, '_registry.json');
    const reg = JSON.parse(fs.readFileSync(rp, 'utf8'));
    delete (reg.plugins || {}).p4plug; delete (reg.plugins || {}).p4plugbg;
    fs.writeFileSync(rp, JSON.stringify(reg, null, 2));
  } catch (_) {}
  syncBuiltinPlugins();
  try { fs.unlinkSync(HB); } catch (_) {}
  resetStates();
}

(async () => {
  for (const f of ['brain_c123.log', 'host_c123.log', 'electron_c123.log']) {
    try { fs.writeFileSync(path.join(ROOT, 'logs', f), ''); } catch (_) {}
  }
  try {
    log('[0] 隔离环境探针');
    const iso = isolationProof();
    rec('ISO 凭据/目录全部隔离（临时 HOME、40 位随机测试 Token、非生产端口）',
      iso.root_under_temp && iso.brain_token_is_40hex && iso.skill_token_is_40hex,
      JSON.stringify(iso));

    log('[1] 接管 + 清场');
    if (brainProc) { try { brainProc.kill(); } catch (_) {} }
    if (hostProc) { try { hostProc.kill(); } catch (_) {} }
    killPort(BRAIN_PORT); killPort(HOST_PORT); await sleep(2500);
    cleanTestArtifacts();
    startHost(STOK);
    if (!(await waitPort(HOST_PORT))) throw new Error('host 启动失败');
    startBrain();
    await untilBrainReady();

    log('[2] 启动真实 Electron 并连接测试 Brain');
    await startElectron();
    await ev(`(async()=>{
      window.confirm=()=>true;
      await window.vigil.cfg.set({base:'http://127.0.0.1:${BRAIN_PORT}',wizard_done:true,roots:[]});
      await window.vigil.token.set(${JSON.stringify(BTOK)});
      return 1;})()`);
    await waitAppReady();
    await shot('c123-00-connected');

    // ══ C1：装卸文案按类型分流 ══
    log('[3] C1 文案：插件与技能不再混用');
    await gotoAbility('pl');
    let m = await toastWatch();
    await dropZip('#plDrop', 'p4plug-v1.zip');
    let t = await waitToast(m);
    rec('C1-1 插件安装 toast 说"插件/重启大脑"且不提宿主',
      /插件/.test(t) && /大脑/.test(t) && !/宿主/.test(t), t);
    m = await toastWatch();
    await dropZip('#plDrop', 'p4plug-v2.zip');
    t = await waitToast(m);
    rec('C1-2 插件更新 toast 同为大脑文案',
      /插件/.test(t) && /大脑/.test(t) && !/宿主/.test(t), t);
    await gotoAbility('sk');
    m = await toastWatch();
    await dropZip('#skDrop', 'p4skill-v1.zip');
    t = await waitToast(m);
    rec('C1-3 技能首次安装 toast 不误报需重启、不冒充插件文案',
      /已装上/.test(t) && !/重启/.test(t) && !/插件/.test(t), t);
    await gotoAbility('sk');
    m = await toastWatch();
    await dropZip('#skDrop', 'p4skill-v2.zip');
    t = await waitToast(m);
    rec('C1-4 技能更新 toast 说"重启技能宿主"且不提插件',
      /技能宿主/.test(t) && !/插件/.test(t), t);
    await shot('c123-01-c1-skill-wording');

    // ══ C2：卸载提示 ══
    log('[4] C2 技能卸载：宿主文案');
    // 注意：每次装卸成功客户端都会 ctx.refresh() 整页重绘，而能力页重绘后
    // 固定回到「插件」tab（renderAbility 末尾就是 plugins()）—— 这是产品
    // 既有行为、非本次修复范围，harness 只能每次操作前重新切 tab
    // （28 项 harness 一直是这么绕的，这里补齐漏掉的两处）。
    await gotoAbility('sk');
    await until("[...document.querySelectorAll('[data-sun]')].some(b=>b.dataset.sun==='p4skill')");
    m = await toastWatch();
    await ev(`[...document.querySelectorAll('[data-sun]')].find(b=>b.dataset.sun==='p4skill').click()`);
    t = await waitToast(m);
    rec('C2-5 技能卸载 toast 提示重启宿主（而非大脑/插件）',
      /宿主/.test(t) && !/后台线程/.test(t), t);

    log('[5] C2 反例：无 start 钩子的插件卸载不许谎称线程还在跑');
    await restartBrain();   // 加载 p4plug v2（无 start 钩子）
    await gotoAbility('pl');
    await until("[...document.querySelectorAll('[data-un]')].some(b=>b.dataset.un==='p4plug')");
    m = await toastWatch();
    await ev(`[...document.querySelectorAll('[data-un]')].find(b=>b.dataset.un==='p4plug').click()`);
    t = await waitToast(m);
    rec('C2-4 无后台线程的插件卸载用普通文案（不谎称线程还在跑）',
      /已卸载/.test(t) && !/后台线程/.test(t) && !/重启大脑才真正停止/.test(t), t);

    log('[6] C2 正例：带 start() 的插件卸载后线程真的还在跑');
    resetStates();
    m = await toastWatch();
    await dropZip('#plDrop', 'p4plugbg-v1.zip');
    t = await waitToast(m);
    rec('C2-0 带后台线程的插件安装 toast 为大脑文案',
      /插件/.test(t) && /大脑/.test(t), t);
    await restartBrain();   // start() 真的被调用，心跳开始
    await sleep(1500);
    let g0 = await hbGrows(1500);
    rec('C2-setup 重启后后台线程确已运行（心跳在长）', g0.grows,
      JSON.stringify(g0));
    // 安装不 import（只落盘），所以安装后的那次渲染里没有 p4plugbg 的
    // 卸载按钮；重启后客户端不会自动重画，必须显式切一次 tab 让
    // plugins() 重新拉清单，按钮才在 DOM 里。漏了这一步 click 会静默
    // 落空（find 返回 undefined），表现为"没有 toast"。
    await gotoAbility('pl');
    await until("[...document.querySelectorAll('[data-un]')].some(b=>b.dataset.un==='p4plugbg')");
    m = await toastWatch();
    await ev(`[...document.querySelectorAll('[data-un]')].find(b=>b.dataset.un==='p4plugbg').click()`);
    t = await waitToast(m);
    rec('C2-1 卸载带后台线程的插件：toast 明说线程还在跑、要重启大脑',
      /后台线程还在跑/.test(t) && /重启大脑/.test(t), t);
    let g1 = await hbGrows(2500);
    rec('C2-2 进程级证据：卸载后心跳仍在长（start() 的线程没跟着文件消失）',
      g1.grows, JSON.stringify(g1));
    await shot('c123-02-c2-bg-thread-alive-after-uninstall');
    await restartBrain();
    await sleep(1200);
    let g2 = await hbGrows(2500);
    rec('C2-3 大脑重启后心跳停止（线程随旧进程退出）', !g2.grows,
      JSON.stringify(g2));

    // ══ C3：持久待重启状态（插件 scope）══
    log('[7] C3 插件 scope：出现 → 刷新保留 → 真重启才消');
    resetStates();
    m = await toastWatch();
    await dropZip('#plDrop', 'p4plug-v1.zip');
    await waitToast(m);
    await gotoAbility('pl');
    rec('C3-1 安装后插件页出现持久"待重启大脑"提示（含原因）',
      await pluginBanner() && /安装|插件/.test(await abText()),
      (await abText()).slice(0, 120));
    await shot('c123-03-c3-plugin-banner');
    log('  [刷新] Page.reload 整页重载…');
    await sendCdp('Page.reload', { ignoreCache: false });
    await waitAppReady();
    await ev(`window.confirm=()=>true;`);
    await toastWatch();
    await gotoAbility('pl');
    rec('C3-2 整页刷新后提示仍在（来源是服务端判据，不是 toast/前端变量）',
      await pluginBanner(), 'reload 后 #abView 仍含"待重启大脑"');
    await shot('c123-04-c3-banner-survives-reload');
    await restartBrain();
    await gotoAbility('pl');
    rec('C3-3 大脑真正重启后提示正确消失', !(await pluginBanner()),
      '重启后 #abView 无"待重启大脑"');

    log('[8] C3-7 反例：大脑"重启失败"不许虚假清除提示');
    m = await toastWatch();
    await dropZip('#plDrop', 'p4plug-v1.zip');
    await waitToast(m);
    await gotoAbility('pl');
    const pendBefore = readState(ST_PLUGIN);
    rec('C3-7a 前置：安装后状态文件确为待重启',
      !!pendBefore && pendBefore.changed_at > pendBefore.started_at,
      JSON.stringify(pendBefore));
    // 杀掉大脑 → 占住端口 → 再起大脑（必然绑定失败）＝ 一次"重启失败"
    if (brainProc) { try { brainProc.kill(); } catch (_) {} brainProc = null; }
    killPort(BRAIN_PORT); await sleep(2000);
    const dummy = http.createServer((req, res) => {
      res.writeHead(404, { 'Content-Type': 'text/plain' }); res.end('dummy');
    });
    await new Promise((r) => dummy.listen(BRAIN_PORT, '127.0.0.1', r));
    const failed = startBrain();
    await sleep(3500);
    let apiBad = false, apiStatus = 0;
    try { const r = await brainApi('GET', '/api/plugins'); apiStatus = r.status; apiBad = r.status !== 200; }
    catch (_) { apiBad = true; }
    rec('C3-7b 重启失败确已发生（端口被占，大脑没真正起来）', apiBad,
      'brainApi status=' + apiStatus);
    const pendAfterFail = readState(ST_PLUGIN);
    rec('C3-7c 重启失败后状态文件仍为待重启（不得虚假清除）',
      !!pendAfterFail && pendAfterFail.changed_at > pendAfterFail.started_at,
      JSON.stringify(pendAfterFail));
    await gotoAbility('pl');
    rec('C3-7d 重启失败期间界面不出"已生效"的假象',
      !(await pluginBanner()), 'brain 不可达时插件页为空列表（见报告观察项）');
    await shot('c123-05-c3-brain-restart-failed');
    // 恢复：撤掉占位、杀掉半死大脑、正常重启
    await new Promise((r) => dummy.close(r));
    try { failed.kill(); } catch (_) {}
    killPort(BRAIN_PORT); await sleep(1500);
    startBrain();
    await untilBrainReady();
    await gotoAbility('pl');
    rec('C3-7e 正常重启后提示才消失', !(await pluginBanner()), '恢复正常服务后');

    // ══ C3：双 scope 独立性 + 宿主重启失败 ══
    log('[9] C3 双 scope：只重启一边不该清另一边的提示');
    resetStates();
    await gotoAbility('sk');
    m = await toastWatch();
    await dropZip('#skDrop', 'p4skill-v1.zip');
    await waitToast(m);
    await gotoAbility('pl');
    m = await toastWatch();
    await dropZip('#plDrop', 'p4plug-v1.zip');
    await waitToast(m);
    await gotoAbility('sk');
    rec('C3-4 安装技能后技能页出现持久"待重启技能宿主"提示',
      await skillBanner(), (await abText()).slice(0, 120));
    await gotoAbility('pl');
    rec('C3-4b 同时插件页有"待重启大脑"提示', await pluginBanner(), '');
    await shot('c123-06-c3-skill-banner');
    await restartBrain();
    await gotoAbility('sk');
    rec('C3-5a 只重启 Brain：技能提示仍在（宿主没重启过）',
      await skillBanner(), 'brain-only restart');
    await gotoAbility('pl');
    rec('C3-5b 只重启 Brain：插件提示已消', !(await pluginBanner()), '');
    await gotoAbility('pl');
    m = await toastWatch();
    await dropZip('#plDrop', 'p4plug-v2.zip');
    await waitToast(m);
    await gotoAbility('pl');
    rec('C3-5c 再改插件：插件提示重新出现', await pluginBanner(), '');
    await restartHost();
    await gotoAbility('sk');
    rec('C3-6a 只重启 Host：技能提示已消', !(await skillBanner()), 'host-only restart');
    await gotoAbility('pl');
    rec('C3-6b 只重启 Host：插件提示仍在（大脑没重启过）',
      await pluginBanner(), 'host-only restart 不清插件 scope');
    await restartBrain();
    await gotoAbility('pl');
    rec('C3-6c 再重启 Brain：插件提示才消', !(await pluginBanner()), '');

    log('[10] C3-8 反例：宿主"重启失败"不许虚假清除提示');
    await gotoAbility('sk');
    m = await toastWatch();
    await dropZip('#skDrop', 'p4skill-v1.zip');
    await waitToast(m);
    await gotoAbility('sk');
    rec('C3-8a 前置：技能页有待重启提示', await skillBanner(), '');
    // 用不合法（过短）Token 起宿主 → 启动自检拒绝、进程退出 ＝ 一次"重启失败"
    if (hostProc) { try { hostProc.kill(); } catch (_) {} hostProc = null; }
    killPort(HOST_PORT); await sleep(2000);
    startHost('short-token');
    const hostUp = await waitPort(HOST_PORT, 6000);
    let hostReach = false;
    try { await hostApi('GET', '/health'); hostReach = true; } catch (_) {}
    rec('C3-8b 宿主重启失败确已发生（非法 Token，拒绝启动）',
      !hostUp && !hostReach, 'port_up=' + hostUp);
    const skFail = readState(ST_SKILL);
    rec('C3-8c 宿主重启失败后技能状态文件仍为待重启',
      !!skFail && skFail.changed_at > skFail.started_at,
      JSON.stringify(skFail));
    await gotoAbility('sk');
    const offline = /没起来|不生效/.test(await abText());
    rec('C3-8d 宿主重启失败期间：界面显示离线红标，且待重启提示不消失',
      offline && (await skillBanner()),
      'offline=' + offline);
    await shot('c123-07-c3-host-restart-failed-banner-persists');
    // 恢复：正确 Token 重启宿主
    startHost(STOK);
    if (!(await waitPort(HOST_PORT, 15000))) throw new Error('host 恢复失败');
    await sleep(1200);
    await gotoAbility('sk');
    rec('C3-8e 宿主正常重启后提示才消失', !(await skillBanner()), '');

    log('[11] C3-9 重新操作后新状态复现');
    await gotoAbility('pl');
    m = await toastWatch();
    await dropZip('#plDrop', 'p4plug-v1.zip');
    await waitToast(m);
    await gotoAbility('pl');
    rec('C3-9 全部清除过后再次装卸，待重启提示重新出现',
      await pluginBanner(), 're-operate');

    rec('E2 Electron 全程无渲染层严重报错',
      !(fs.readFileSync(path.join(ROOT, 'logs', 'electron_c123.log'), 'utf8')
        .match(/preload-error|Uncaught \(the authors/g)),
      'electron_c123.log 已留存');
  } catch (e) {
    log('C123 异常：' + e.message + '\n' + (e.stack || ''));
    results.push({ id: 'EXC', ok: false, detail: e.message });
    try { await shot('c123-99-exception'); } catch (_) {}
  } finally {
    try { ctl && ctl.close(); } catch (_) {}
    if (app) app.kill();
    await sleep(1000);
    try { execSync('taskkill /F /IM electron.exe /T', { stdio: 'ignore' }); } catch (_) {}
    if (brainProc) { try { brainProc.kill(); } catch (_) {} }
    if (hostProc) { try { hostProc.kill(); } catch (_) {} }
    killPort(BRAIN_PORT); killPort(HOST_PORT);
    let commit = '', pyver = '', hsha = '';
    try {
      const gp = path.join(WT, '.git');
      let gd = gp;
      try {
        const dot = fs.readFileSync(gp, 'utf8').trim();
        if (dot.startsWith('gitdir:')) gd = dot.slice(7).trim();
      } catch (_) {}
      // worktree：.git 是指针文件，且分支 ref 不在 gd 里 —— 要顺
      // commondir 回主仓库找，否则 ref 读不到、commit 静默变空
      // （298e7c6-c123 首轮复跑实测：refpath exists=false）。
      let common = gd;
      try {
        const c = fs.readFileSync(path.join(gd, 'commondir'), 'utf8').trim();
        common = path.resolve(gd, c);
      } catch (_) {}
      let head = fs.readFileSync(path.join(gd, 'HEAD'), 'utf8').trim();
      if (head.startsWith('ref:')) {
        const ref = head.slice(4).trim();
        for (const base of [gd, common]) {
          const p = path.join(base, ref);
          if (fs.existsSync(p)) { head = fs.readFileSync(p, 'utf8').trim(); break; }
        }
      }
      if (/^[0-9a-f]{40}$/.test(head)) commit = head;
    } catch (_) {}
    try {
      // 裸 'python' 会撞 PATH 解析问题，按绝对路径候选依次试；
      // ⚠️ 必须用异步 spawn：这台机器上 spawnSync 一律 EBUSY
      // （实测 status=null，连绝对路径都起不来），同步采集只会静默变空。
      const cands = [
        process.env.VIGIL_PYTHON,
        path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Python', 'Python313', 'python.exe'),
        'C:\\Python313\\python.exe', 'python',
      ].filter(Boolean);
      for (const py of cands) {
        try {
          if (py !== 'python' && !fs.existsSync(py)) continue;
          pyver = await new Promise((res) => {
            const c = require('child_process').spawn(py, ['--version'],
              { cwd: WT, stdio: ['ignore', 'pipe', 'pipe'] });
            let o = '';
            c.stdout.on('data', (d) => { o += d; });
            c.stderr.on('data', (d) => { o += d; });
            c.on('error', () => res(''));
            c.on('exit', () => res(o.trim()));
          });
          if (pyver) break;
        } catch (_) {}
      }
    } catch (_) {}
    try { hsha = require('crypto').createHash('sha256')
      .update(fs.readFileSync(__filename)).digest('hex').slice(0, 16); } catch (_) {}
    fs.writeFileSync(path.join(EV, 'results-c123.json'), JSON.stringify({
      started: new Date().toISOString(), commit, python: pyver,
      harness_sha256_16: hsha,
      brain_port: BRAIN_PORT, host_port: HOST_PORT,
      electron: 'electron@' + (require(path.join(CLIENT, 'node_modules', 'electron', 'package.json')).version),
      node: process.version, results,
    }, null, 2));
  }
  const n = results.filter((r) => r.ok).length;
  log(`\nC123 结果 ${n}/${results.length}`);
  process.exit(n === results.length ? 0 : 1);
})();
