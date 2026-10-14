// P4 Windows 联动验收：真实 Electron(渲染层拖拽/IPC) + 真实 Brain HTTP 路由
// + 真实 Node Skill Host(Bearer)。不用 fixture、不 mock API。
// 临时脚本（位于 %TEMP%/vigil_p4），证据落到 worktree docs/p4-windows-evidence。
const { spawn, execSync } = require('child_process');
const path = require('path'), fs = require('fs'), os = require('os'), http = require('http');

const WT = 'F:\\sync-checks\\creator-mode-p4-4b-2';
const CLIENT = path.join(WT, 'client');
const WebSocket = require(path.join(CLIENT, 'node_modules', 'ws'));
const ELECTRON = path.join(CLIENT, 'node_modules', 'electron', 'dist', 'electron.exe');
const ROOT = path.join(os.tmpdir(), 'vigil_p4');
const PKGS = path.join(ROOT, 'pkgs');
// 本轮（c0346e5）证据进独立子目录：7bcc925 轮的证据原样留在上层，不被覆盖。
// 2026-10-12 改动仅此一处路径 +  results.json 元数据 + 进程清理，28 个用例逻辑零改动。
// 2026-10-13：目录名改成按**实际 HEAD** 动态生成（纯文件读，兼容 worktree 的
// .git 指针文件）—— 修复提交 298e7c6 复跑时证据自动进 298e7c6/，与
// c0346e5/ 两轮并存，不会互相覆盖。读不到才退回 c0346e5。
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
const EV = path.join(WT, 'docs', 'p4-windows-evidence', commitNow());
fs.mkdirSync(EV, { recursive: true });
const BRAIN_PORT = 18790, HOST_PORT = 8791, CDP = 9356;
const tokens = Object.fromEntries(fs.readFileSync(path.join(ROOT, 'root.txt'), 'utf8')
  .split(/\r?\n/).filter(Boolean).map((l) => l.split('=')));
const BTOK = tokens.brain_token, STOK = tokens.skill_token;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(...a);
const results = [];
const rec = (id, ok, detail) => { results.push({ id, ok, detail }); log((ok ? '  OK  ' : '  FAIL ') + id + '  ' + String(detail || '').slice(0, 240)); };

// 进程台账：brain/host 是本脚本的子进程，上一轮异常退出会留下孤儿
// （不监听端口的 killPort 抓不到，半死的 brain 会一直占着 VIGIL_HOME）。
// 落一个 pid 文件，启动时先按它清一遍，finally 再清一遍。
const PIDS = path.join(ROOT, 'pids.json');
function trackPid(k, pid) {
  let d = {};
  try { d = JSON.parse(fs.readFileSync(PIDS, 'utf8')); } catch (_) {}
  d[k] = pid;
  try { fs.writeFileSync(PIDS, JSON.stringify(d)); } catch (_) {}
}
function killTracked() {
  let d = {};
  try { d = JSON.parse(fs.readFileSync(PIDS, 'utf8')); } catch (_) {}
  for (const k of Object.keys(d)) {
    try { process.kill(d[k]); log('  [清理] 杀掉上个进程 ' + k + ' pid=' + d[k]); } catch (_) {}
  }
  try { fs.unlinkSync(PIDS); } catch (_) {}
}

function killPort(port) {
  try {
    execSync('powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort ' + port
      + ' -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"',
      { stdio: 'ignore' });
  } catch (_) {}
}

// 🔴 内建插件必须从 worktree 同步进隔离 HOME，且 __pycache__ 一起清。
// 2026-10-13 教训：临时 HOME 里的 plugins/*.py 是昨天复制进去的旧版，
// 大脑加载的 skill.py 停留在 c0346e5 之前的版本（没有 note_change），
// C123 整条技能 scope 的用例全部假失败。更阴的是 PYTHONDONTWRITEBYTECODE=1
// 的环境里旧 .pyc 不会重写、却会被继续使用 —— 不同步 + 不清缓存，
// "跑的哪份代码"根本说不清。验收前必须同步，否则证据无效。
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
    VIGIL_HOME: path.join(ROOT, 'home'), VIGIL_PORT: String(BRAIN_PORT),
    ZX_SKILL_AUTH_TOKEN: STOK, ZX_SKILL_DIR: path.join(ROOT, 'skills'),
    ZX_SKILL_HOST: '127.0.0.1', ZX_SKILL_PORT: String(HOST_PORT),
    PYTHONUNBUFFERED: '1',
  };
  const out = fs.openSync(path.join(ROOT, 'logs', 'brain_harness.log'), 'a');
  const p = spawn('python', [path.join(WT, 'brain', 'server.py')],
    { cwd: path.join(WT, 'brain'), env, stdio: ['ignore', out, out] });
  trackPid('brain', p.pid);
  return p;
}

function startHost(token) {
  const env = {
    ...process.env,
    ZX_SKILL_DIR: path.join(ROOT, 'skills'), ZX_SKILL_PORT: String(HOST_PORT),
    ZX_SKILL_HOST: '127.0.0.1', ZX_SKILL_AUTH_TOKEN: token,
  };
  const out = fs.openSync(path.join(ROOT, 'logs', 'host_harness.log'), 'a');
  const p = spawn('node', [path.join(WT, 'brain', 'skill_host', 'server.js')],
    { cwd: WT, env, stdio: ['ignore', out, out] });
  trackPid('host', p.pid);
  return p;
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
        x.on('end', () => { try { resolve({ status: x.statusCode, json: JSON.parse(d) }); } catch (_) { resolve({ status: x.statusCode, raw: d }); } }); });
    req.on('error', reject); req.on('timeout', () => { req.destroy(); reject(new Error('host timeout')); });
    if (data) req.write(data);
    req.end();
  });
}

let app = null, ctl = null, sendCdp;
async function startElectron() {
  const prof = path.join(ROOT, 'profile');
  fs.rmSync(prof, { recursive: true, force: true });
  // Electron 的启动环境必须干净：ELECTRON_RUN_AS_NODE=1 会让 electron.exe
  // 退化成普通 Node（require('electron') 拿到 npm 壳，app 是 undefined，
  // 主进程第 22 行就崩）。这个变量是宿主环境自带的，必须显式摘掉 ——
  // 2026-10-12 复跑就因为继承它，"CDP 连不上"卡了整轮。
  const eenv = { ...process.env, VIGIL_SIDECAR_DISABLE: '1' };
  delete eenv.ELECTRON_RUN_AS_NODE;
  for (const k of Object.keys(eenv)) {
    if (k.startsWith('ELECTRON_')) delete eenv[k];
  }
  const elog = fs.openSync(path.join(ROOT, 'logs', 'electron.log'), 'a');
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
  // MutationObserver 全程录制 #toast，返回录制起点标记
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
async function skillRows() {
  return ev(`[...document.querySelectorAll('#abView .pkg')].map(d=>({
    name:(d.querySelector('.pkg-h b')||{}).textContent||'',
    state:(d.querySelector('.pkg-h .tag')||{}).textContent||'',
    tools:[...d.querySelectorAll('.toolchip')].map(x=>x.textContent),
    id:(d.querySelector('[data-sun]')||{}).dataset?((d.querySelector('[data-sun]')||{}).dataset.sun||''):''}))`);
}
async function pluginRows() {
  return ev(`[...document.querySelectorAll('#abView .pkg')].map(d=>({
    name:(d.querySelector('.pkg-h b')||{}).textContent||'',
    state:(d.querySelector('.pkg-h .tag')||{}).textContent||'',
    file:(d.querySelector('.pkg-meta .mono')||{}).textContent||''}))`);
}
async function restartBrain() {
  log('  [Brain] 重启…');
  killPort(BRAIN_PORT); await sleep(2500);
  startBrain();
  await untilBrainReady();
}
async function untilBrainReady() {
  const ok = await waitPort(BRAIN_PORT);
  for (let i = 0; i < 30; i++) {
    try {
      const r = await brainApi('GET', '/api/plugins');
      if (r.status === 200) return;
      // 纯诊断日志（不影响判定）：非 200 时把状态和响应体打出来，
      // 上一轮 P4 就是在这里看不出 401 才卡的。
      log('  [诊断] /api/plugins status=' + r.status + ' body='
        + JSON.stringify(r.json || r.raw).slice(0, 300));
    } catch (e) { log('  [诊断] brainApi 异常: ' + e.message); }
    await sleep(500);
  }
  if (!ok) throw new Error('brain 未监听');
}

(async () => {
  fs.writeFileSync(path.join(ROOT, 'logs', 'brain_harness.log'), '');
  fs.writeFileSync(path.join(ROOT, 'logs', 'host_harness.log'), '');
  try {
    // ── 接管进程：杀掉先前终端起的 Brain/Host，由本脚本控制重启 ──
    log('[1] 接管 Brain/Host 进程…');
    killTracked();
    killPort(BRAIN_PORT); killPort(HOST_PORT); await sleep(2500);
    // 清掉上一轮残留：技能根所有正式目录（保留根本身）+ p4plug 插件与注册表项
    for (const n of fs.readdirSync(path.join(ROOT, 'skills'))) {
      if (n.startsWith('_') || n.startsWith('.')) continue;
      fs.rmSync(path.join(ROOT, 'skills', n), { recursive: true, force: true });
    }
    const pdir = path.join(ROOT, 'home', 'plugins');
    for (const n of fs.readdirSync(pdir)) {
      if (n === 'p4plug.py' || n.startsWith('_p4plug_')) fs.rmSync(path.join(pdir, n), { force: true });
    }
    syncBuiltinPlugins();
    try {
      const rp = path.join(pdir, '_registry.json');
      const reg = JSON.parse(fs.readFileSync(rp, 'utf8'));
      delete (reg.plugins || {}).p4plug;
      fs.writeFileSync(rp, JSON.stringify(reg, null, 2));
    } catch (_) {}
    startHost(STOK);
    if (!(await waitPort(HOST_PORT))) throw new Error('host 启动失败');
    startBrain();
    await untilBrainReady();
    const h0 = await hostApi('GET', '/health');
    rec('H0 Host 启动(Bearer/回环/临时目录)', h0.json.ok && /vigil_p4[\\/]skills/.test(h0.json.dir), JSON.stringify(h0.json));

    log('[2] 启动真实 Electron…');
    await startElectron();
    await ev(`(async()=>{
      window.confirm=()=>true;
      await window.vigil.cfg.set({base:'http://127.0.0.1:${BRAIN_PORT}',wizard_done:true,roots:[]});
      await window.vigil.token.set(${JSON.stringify(BTOK)});
      return 1;})()`);
    let who = null;
    for (let i = 0; i < 30; i++) {
      who = await ev('window.vigil.device.whoami()');
      if (who && new RegExp(BRAIN_PORT).test(who.base || '')) break;
      await sleep(700);
    }
    rec('E1 Electron 启动并连接测试 Brain', !!(who && new RegExp(BRAIN_PORT).test(who.base || '')),
      JSON.stringify(who));
    await shot('01-electron-connected');

    // ══ 插件 ══
    log('[3] 插件：非法包（路径穿越）');
    await gotoAbility('pl');
    let m1 = await toastWatch();
    await dropZip('#plDrop', 'p4plug-evil-traversal.zip');
    let t1 = await waitToast(m1);
    rec('P1 路径穿越包被拒且 toast 报错', /装失败|拒绝|非法|不允许/.test(t1), t1);
    await shot('02-plugin-evil-traversal');

    log('[4] 插件：非法包（双根）');
    let m2 = await toastWatch();
    await dropZip('#plDrop', 'p4plug-evil-tworoots.zip');
    let t2 = await waitToast(m2);
    rec('P2 双根结构包被拒', /装失败|拒绝|非法|结构/.test(t2), t2);
    let prBad = await pluginRows();
    rec('P2b 非法插件未进入列表', !prBad.some((x) => /p4plug/.test(x.name)), JSON.stringify(prBad));

    log('[5] 插件：安装 v1（restart_needed）');
    let m3 = await toastWatch();
    await dropZip('#plDrop', 'p4plug-v1.zip');
    let t3 = await waitToast(m3);
    let reg1 = {};
    try { reg1 = (JSON.parse(fs.readFileSync(path.join(ROOT, 'home', 'plugins', '_registry.json'), 'utf8')).plugins || {}).p4plug || {}; } catch (_) {}
    const v1OnDisk = fs.readFileSync(path.join(ROOT, 'home', 'plugins', 'p4plug.py'), 'utf8').includes('P4PONG-V1');
    rec('P3 v1 文件/注册表已提交且 toast 提示需重启',
      /已装上/.test(t3) && /重启/.test(t3) && reg1.version === '1.0.0' && v1OnDisk,
      t3 + ' | registry=' + reg1.version);
    await gotoAbility('pl');
    await shot('03-plugin-v1-installed-restart-required');

    log('[6] Brain 重启后插件运行中');
    await restartBrain();
    await gotoAbility('pl');
    let pr1 = await pluginRows();
    let pv1b = await brainApi('GET', '/api/plugins');
    if (!pv1b.json || !pv1b.json.manage) {
      log('  [诊断] P4 /api/plugins status=' + pv1b.status + ' body='
        + JSON.stringify(pv1b.json || pv1b.raw).slice(0, 400));
    }
    let it1 = (pv1b.json.manage.items || []).find((x) => x.name === 'p4plug');
    rec('P4 重启后 p4plug 运行中无装载错误', !!it1 && !it1.err && pr1.some((x) => /p4plug/.test(x.name) && /运行中/.test(x.state)),
      JSON.stringify({ err: it1 && it1.err, tools: it1 && it1.tools, version: it1 && it1.version }));
    await shot('04-plugin-v1-running');

    log('[7] 插件：更新 v2 + 重启生效');
    let m4 = await toastWatch();
    await dropZip('#plDrop', 'p4plug-v2.zip');
    let t4 = await waitToast(m4);
    await restartBrain();
    let pv2 = await brainApi('GET', '/api/plugins');
    if (!pv2.json || !pv2.json.manage) {
      log('  [诊断] P5 /api/plugins status=' + pv2.status + ' body='
        + JSON.stringify(pv2.json || pv2.raw).slice(0, 400));
    }
    let it2 = (pv2.json.manage.items || []).find((x) => x.name === 'p4plug');
    const regPath = path.join(ROOT, 'home', 'plugins', '_registry.json');
    let reg2 = {};
    try { reg2 = (JSON.parse(fs.readFileSync(regPath, 'utf8')).plugins || {}).p4plug || {}; } catch (_) {}
    rec('P5 更新 v2 重启后注册表 2.0.0 且运行中',
      /重启/.test(t4) && it2 && reg2.version === '2.0.0' && !it2.err,
      t4 + ' | registry=' + JSON.stringify({ version: reg2.version, err: it2 && it2.err }));
    const onDiskV2 = fs.readFileSync(path.join(ROOT, 'home', 'plugins', 'p4plug.py'), 'utf8').includes('P4PONG-V2');
    rec('P5b 落盘文件确为 v2', onDiskV2, 'P4PONG-V2 marker');
    await gotoAbility('pl');
    await shot('05-plugin-v2-running');

    log('[8] 插件：卸载');
    await ev(`[...document.querySelectorAll('[data-un]')].find(b=>b.dataset.un==='p4plug').click()`);
    await sleep(1500);
    await gotoAbility('pl');
    let pr3 = await pluginRows();
    let pv3 = await brainApi('GET', '/api/plugins');
    let gone = !(pv3.json.manage.items || []).some((x) => x.name === 'p4plug');
    rec('P6 卸载后清单无 p4plug（文件/注册表已移除）', gone && !pr3.some((x) => /p4plug/.test(x.name)),
      JSON.stringify(pr3.map((x) => x.name)));
    await shot('06-plugin-uninstalled');

    // ══ Skill ══
    log('[9] Skill：非法 entry 越界包');
    await gotoAbility('sk');
    let st0 = await ev(`document.querySelector('#abView .sech .tag') && document.querySelector('#abView .sech .tag').textContent`);
    rec('S0 宿主在线状态显示「可以装」', /可以装/.test(st0 || ''), st0);
    await shot('07-skill-host-alive');
    let m5 = await toastWatch();
    await dropZip('#skDrop', 'p4skill-evil-entry.zip');
    let s1 = await waitToast(m5);
    const evilDir = fs.existsSync(path.join(ROOT, 'skills', 'p4skill'));
    rec('S1 entry 越界包静态拒绝（toast + 不落地）',
      /装失败|拒绝|非法|越界/.test(s1) && !evilDir, s1 + ' | dir=' + evilDir);

    log('[10] Skill：无 setup 包 → reload 失败 → 回滚');
    let m6 = await toastWatch();
    await dropZip('#skDrop', 'p4skill-nosetup.zip');
    let s2 = await waitToast(m6);
    await sleep(500);
    const lsAfterNoSetup = fs.existsSync(path.join(ROOT, 'skills', 'p4skill'));
    rec('S2 无 setup 包安装失败且文件已回滚（目录不留存）', /装失败/.test(s2) && !lsAfterNoSetup,
      s2 + ' | dir_exists=' + lsAfterNoSetup);
    await shot('08-skill-nosetup-rolled-back');

    log('[11] Skill：安装 v1（热重载）');
    let m7 = await toastWatch();
    await dropZip('#skDrop', 'p4skill-v1.zip');
    let s3 = await waitToast(m7);
    let c1 = await hostApi('POST', '/skills/call', { tool: 'p4skill__echo', args: {}, ctx: {} });
    rec('S3 v1 热装载成功且工具可调用', /已装上/.test(s3) && c1.json.content === 'P4-SKILL-V1',
      s3 + ' | call=' + JSON.stringify(c1.json));
    await gotoAbility('sk');
    let rows1 = await skillRows();
    rec('S3b UI 列出 p4skill 和工具', rows1.some((x) => /p4skill|P4/.test(x.name) && x.tools.length >= 1),
      JSON.stringify(rows1));
    await shot('09-skill-v1-loaded');

    log('[12] Skill：更新 v2（restart_needed 但热重载立即生效）');
    let m8 = await toastWatch();
    await dropZip('#skDrop', 'p4skill-v2.zip');
    let s4 = await waitToast(m8);
    let c2 = await hostApi('POST', '/skills/call', { tool: 'p4skill__echo', args: {}, ctx: {} });
    rec('S4 更新 v2：提示 restart_needed 且新代码热重载立即生效',
      /已装上/.test(s4) && /重启/.test(s4) && c2.json.content === 'P4-SKILL-V2', s4 + ' | call=' + c2.json.content);
    await gotoAbility('sk');
    await shot('10-skill-v2-restart-needed-hot-reloaded');

    log('[13] 重启 Host：新版本持久生效');
    killPort(HOST_PORT); await sleep(2000); startHost(STOK); await waitPort(HOST_PORT);
    await sleep(1500);
    let c3 = await hostApi('POST', '/skills/call', { tool: 'p4skill__echo', args: {}, ctx: {} });
    let hl = await hostApi('GET', '/health');
    rec('S5 Host 重启后 v2 仍在（注册持久）', c3.json.content === 'P4-SKILL-V2' && hl.json.skills === 1,
      'call=' + c3.json.content + ' skills=' + hl.json.skills);

    log('[14] 错误 Token 重启 Host：UI 鉴权失败态');
    killPort(HOST_PORT); await sleep(2000); startHost('wrong-token-0000000000000000000000000000'); await waitPort(HOST_PORT);
    let wc = await hostApi('GET', '/health', null, null);
    rec('S6a 无认证仍 401', wc.status === 401, 'status=' + wc.status);
    await gotoAbility('sk');
    await sleep(800);
    let deadTag = await ev(`[...document.querySelectorAll('#abView .sech .tag')].map(t=>t.textContent).join('|')`);
    rec('S6 错误凭据时 UI 显示宿主离线', /没起来|不生效/.test(deadTag), deadTag);
    await shot('11-skill-host-wrong-token-offline');
    killPort(HOST_PORT); await sleep(2000); startHost(STOK); await waitPort(HOST_PORT); await sleep(1200);
    await gotoAbility('sk');
    let aliveTag = await ev(`[...document.querySelectorAll('#abView .sech .tag')].map(t=>t.textContent).join('|')`);
    rec('S7 正确凭据恢复后 UI 回在线', /可以装/.test(aliveTag), aliveTag);

    log('[15] Skill：坏 v2（语法错误）→ reload 失败 → 回滚到先前 v2.0.0');
    let m9 = await toastWatch();
    await dropZip('#skDrop', 'p4skill-v2-broken.zip');
    let s5 = await waitToast(m9);
    await sleep(500);
    let c4 = await hostApi('POST', '/skills/call', { tool: 'p4skill__echo', args: {}, ctx: {} });
    rec('S8 更新失败 toast 报错且运行时回滚为先前 v2.0.0（非 broken）',
      /装失败/.test(s5) && c4.json.content === 'P4-SKILL-V2',
      s5 + ' | call=' + c4.json.content);
    const brokenApi = await brainApi('POST', '/api/skill/status', {});
    rec('S8b status 清单仍为旧版 v2.0.0 条目（非 broken）',
      (brokenApi.json.skills || []).some((x) => x.id === 'p4skill' && (x.version || '') === '2.0.0'),
      JSON.stringify(brokenApi.json.skills.map((x) => ({ id: x.id, v: x.version }))));
    await gotoAbility('sk');
    await shot('12-skill-broken-update-rolled-back');

    log('[16] NTFS junction：Host reload 必须拒绝越界链接');
    const jdir = path.join(ROOT, 'skills', 'eviljunction');
    try { fs.rmSync(jdir, { recursive: true, force: true }); } catch (_) {}
    // junction 用 Node 原生 API 建，不再起 powershell/cmd：
    // 2026-10-13 复跑时 execSync  spawning cmd.exe 撞上 EBUSY 整轮崩在这里。
    // 测的是"Host 拒不拒越界链接"，怎么建的不影响被测对象。
    let jok = false, jerr = '';
    try {
      fs.symlinkSync(path.join(ROOT, 'pkgs'), jdir, 'junction');
      jok = true;
    } catch (e) { jerr = String(e && e.message || e); }
    if (!jok) {
      rec('S9 junction 创建失败（环境问题，非被测对象）', false, jerr);
    } else {
      const jr = await hostApi('POST', '/reload', {});
      rec('S9 含 junction 的技能树 reload 被拒(409)', jr.status === 409 && /junction|符号链接/.test(JSON.stringify(jr.json)),
        JSON.stringify(jr.json).slice(0, 200));
    }
    fs.rmSync(jdir, { recursive: true, force: true });
    await hostApi('POST', '/reload', {});

    log('[17] Skill：双层包（多一层同名目录）');
    let m10 = await toastWatch();
    await dropZip('#skDrop', 'p4skill-double.zip');
    let s6 = await waitToast(m10);
    let c5 = await hostApi('POST', '/skills/call', { tool: 'p4skill__echo', args: {}, ctx: {} });
    rec('S10 双层包自动钻层并装载（同包名更新）', /已装上/.test(s6) && c5.json.content === 'P4-SKILL-DOUBLE',
      s6 + ' | call=' + c5.json.content);

    log('[18] Skill：卸载（文件+渠道配置+注册表）');
    await gotoAbility('sk');
    await until("[...document.querySelectorAll('[data-sun]')].some(b=>b.dataset.sun==='p4skill')");
    await ev(`[...document.querySelectorAll('[data-sun]')].find(b=>b.dataset.sun==='p4skill').click()`);
    await sleep(1500);
    await gotoAbility('sk');
    let goneRows = await skillRows();
    let c6 = await hostApi('POST', '/skills/call', { tool: 'p4skill__echo', args: {}, ctx: {} });
    rec('S11 卸载后 UI 无条目、Host 无工具、目录删除',
      !goneRows.some((x) => /p4skill/i.test(x.name)) && /没有这个技能工具/.test(c6.json.content || '')
      && !fs.existsSync(path.join(ROOT, 'skills', 'p4skill')),
      JSON.stringify({ rows: goneRows.length, call: c6.json.content }));
    await shot('13-skill-uninstalled');

    log('[19] 渠道开关写入（skill channels 事务）');
    let m11 = await toastWatch();
    await dropZip('#skDrop', 'p4skill-v1.zip');
    await waitToast(m11);
    await gotoAbility('sk');
    await until("[...document.querySelectorAll('.chip[data-ch]')].some(x=>x.dataset.p==='p4skill'&&x.dataset.ch==='wechat')");
    const chResp = await ev(`(async()=>{
      const b=[...document.querySelectorAll('.chip[data-ch]')].find(x=>x.dataset.p==='p4skill'&&x.dataset.ch==='wechat');
      b.click(); await new Promise(r=>setTimeout(r,1500));
      return 'chip-clicked';
    })()`);
    const ch = await brainApi('POST', '/api/skill/status', {});
    const sk = (ch.json.skills || []).find((x) => x.id === 'p4skill');
    rec('S12 UI 渠道片点击即持久化（wechat 关、pc/群保持开）',
      sk && sk.channels && sk.channels.wechat === false && sk.channels.pc === true
        && sk.channels.wechat_group === true,
      chResp + ' | channels=' + JSON.stringify(sk && sk.channels));
    // 反向验证：服务端渠道白名单不认伪造渠道 'app'（真实路由拒绝）
    const chBad = await brainApi('POST', '/api/skill/channels',
      { skill: 'p4skill', channels: ['app', 'pc'] });
    rec('S12b 非法渠道 app 被服务端拒绝', chBad.json.ok === false && /不认识的渠道/.test(chBad.json.err || ''),
      JSON.stringify(chBad.json).slice(0, 120));
    await shot('14-skill-channel-toggle');
    // 收尾：卸载，保持环境干净
    await ev(`[...document.querySelectorAll('[data-sun]')].find(b=>b.dataset.sun==='p4skill').click()`);
    await sleep(1000);

    rec('E2 Electron 全程无渲染层严重报错',
      !(fs.readFileSync(path.join(ROOT, 'logs', 'electron.log'), 'utf8').match(/preload-error|Uncaught \(the authors/g)),
      'electron.log 已留存');
  } catch (e) {
    log('E2E 异常：' + e.message + '\n' + (e.stack || ''));
    results.push({ id: 'EXC', ok: false, detail: e.message });
    try { await shot('99-exception'); } catch (_) {}
  } finally {
    try { ctl && ctl.close(); } catch (_) {}
    if (app) app.kill();
    await sleep(1000);
    try { execSync('taskkill /F /IM electron.exe /T', { stdio: 'ignore' }); } catch (_) {}
    // 收尾把 Brain/Host 也收掉：不留孤儿进程占着临时 HOME 和端口
    killTracked();
    killPort(BRAIN_PORT); killPort(HOST_PORT);
    // 元数据：验收报告要能回答"这轮到底跑的哪份代码、什么版本"。
    // 两个都不走 execSync —— 沙箱里 cmd.exe 会 EBUSY / reg.exe 被拦截，
    // 元数据静默变空。commit 纯文件读（兼容 worktree：.git 可能是指针文件）；
    // python 版本用 spawnSync 直起，不借 cmd.exe。
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
    fs.writeFileSync(path.join(EV, 'results.json'), JSON.stringify({
      started: new Date().toISOString(), commit, python: pyver,
      harness_sha256_16: hsha,
      brain_port: BRAIN_PORT, host_port: HOST_PORT,
      electron: 'electron@' + (require(path.join(CLIENT, 'node_modules', 'electron', 'package.json')).version),
      node: process.version, results,
    }, null, 2));
  }
  const n = results.filter((r) => r.ok).length;
  log(`\n结果 ${n}/${results.length}`);
  process.exit(n === results.length ? 0 : 1);
})();
