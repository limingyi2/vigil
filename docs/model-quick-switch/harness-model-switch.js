// 模型快速切换 Electron 界面联动测试
//
// 隔离一切：临时 VIGIL_HOME / 随机端口 / 随机 Token / 临时 profile；
// 两家假服务商（A=硅基流动、B=小米）都在本机随机端口上，**不碰任何真实厂商**。
// 被测链路全是真的：pages.js 的 composer 下拉 → API.get/post → ipcMain（加 Bearer）
// → server.py /api/models/{saved,quick-switch,forget,apply} → model_hub → 真配置落盘。
//
// 截图按惯例不入库（*.png 已被 .gitignore 全局挡住），文本证据进本目录 results.json。
const { spawn } = require('child_process');
const path = require('path'), fs = require('fs'), os = require('os'), http = require('http');
const crypto = require('crypto');

const WT = __dirname.includes('docs') ? path.resolve(__dirname, '..', '..') : process.cwd();
const CLIENT = path.join(WT, 'client');
const WebSocket = require(path.join(CLIENT, 'node_modules', 'ws'));
const ELECTRON = path.join(CLIENT, 'node_modules', 'electron', 'dist', 'electron.exe');
const ROOT = path.join(os.tmpdir(), 'vigil_model_switch');

function commitNow() {
  try {
    const gp = path.join(WT, '.git');
    let gd = gp;
    try {
      const dot = fs.readFileSync(gp, 'utf8').trim();
      if (dot.startsWith('gitdir:')) gd = dot.slice(7).trim();
    } catch (_) {}
    let common = gd;
    try { common = path.resolve(gd, fs.readFileSync(path.join(gd, 'commondir'), 'utf8').trim()); } catch (_) {}
    let head = fs.readFileSync(path.join(gd, 'HEAD'), 'utf8').trim();
    if (head.startsWith('ref:')) {
      const ref = head.slice(4).trim();
      for (const base of [gd, common]) {
        const p = path.join(base, ref);
        if (fs.existsSync(p)) { head = fs.readFileSync(p, 'utf8').trim(); break; }
      }
    }
    return /^[0-9a-f]{40}$/.test(head) ? head.slice(0, 7) : 'unknown';
  } catch (_) { return 'unknown'; }
}
const EV = path.join(WT, 'docs', 'model-quick-switch', 'ev-' + commitNow());
fs.rmSync(EV, { recursive: true, force: true });
fs.mkdirSync(EV, { recursive: true });
fs.rmSync(ROOT, { recursive: true, force: true });
fs.mkdirSync(path.join(ROOT, 'logs'), { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(...a);
const results = [];
// 证据要进版本库，所以先把"这台机器长什么样"抹掉：绝对路径（本机用户名、
// Python 安装目录）、traceback 的框架行一律不留。留在库里的是"她说了什么"。
function scrub(s) {
  return String(s || '')
    // 盘符路径（前面不能是字母，否则会把 http:// 里的 "p:/" 也吃掉，
    // 而 127.0.0.1 这种回环地址正是"全程没碰真实厂商"的直接证据）
    .replace(/(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s"']+/g, '<path>')
    .replace(/(?<![A-Za-z0-9])\/(?:home|Users)\/[^\s"']+/g, '<path>');
}
function tailLog(p, n) {
  let t = '';
  try { t = fs.readFileSync(p, 'utf8'); } catch (_) { return ''; }
  return t.split('\n')
    .filter((l) => !/^\s*File "/.test(l) && !/^\s*\^+\s*$/.test(l)
      && !/^\s*(return|self\.|raise)\b/.test(l))
    .slice(-(n || 60)).map(scrub).join('\n');
}
const rec = (id, ok, detail) => {
  results.push({ id, ok: !!ok, detail: String(detail === undefined ? '' : detail) });
  log((ok ? '  OK  ' : '  FAIL ') + id + '  ' + String(detail === undefined ? '' : detail).slice(0, 300));
};
function freePort() {
  const s = require('net').createServer();
  return new Promise((res) => {
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
  });
}

// ── 两家假服务商
// GET  /v1/models            → 给 probe_provider / 清单用（认密钥）
// POST /v1/chat/completions  → 记下"哪句话打到了哪家、用的是哪个模型"
function makeProvider(name, models) {
  const p = {
    name, models, secret: 'fx-' + crypto.randomBytes(12).toString('hex'),
    chats: [], probes: 0, port: 0, srv: null, chatDelay: 0,
    // 扣住回复用：pending > 0 表示"这一家确实有一个请求在飞"，
    // 好让"生成中"这段时间成为可确定的事实，而不是靠 sleep 猜。
    hold: false, holdQ: [], pending: 0,
  };
  p.release = () => {
    p.hold = false;
    const q = p.holdQ.splice(0);
    q.forEach((f) => f());
  };
  p.srv = http.createServer((req, res) => {
    const url = (req.url || '').split('?')[0];
    const auth = req.headers.authorization || '';
    if (req.method === 'GET' && /\/models$/.test(url)) {
      p.probes += 1;
      if (auth !== 'Bearer ' + p.secret) {
        res.writeHead(401, { 'Content-Type': 'application/json' });
        res.end('{"error":{"message":"invalid api key"}}'); return;
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ data: p.models.map((m) => ({ id: m })) }));
      return;
    }
    if (req.method !== 'POST' || !/\/chat\/completions$/.test(url)) {
      res.writeHead(404); res.end('{}'); return;
    }
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', () => {
      let parsed = {};
      try { parsed = JSON.parse(body); } catch (_) {}
      const msgs = parsed.messages || [];
      let userText = '';
      for (const m of msgs) if (m && m.role === 'user') userText = String(m.content || '');
      p.chats.push({ model: String(parsed.model || ''), text: userText.slice(0, 160),
        authOk: auth === 'Bearer ' + p.secret });
      if (auth !== 'Bearer ' + p.secret) {
        res.writeHead(401, { 'Content-Type': 'application/json' });
        res.end('{"error":{"message":"invalid api key"}}'); return;
      }
      const done = () => {
        p.pending = Math.max(0, p.pending - 1);
        let content;
        if (/ZXFIX_DRAFT/.test(userText)) {
          content = JSON.stringify({
            files: { 'probe_plugin.py': 'NAME = "probe_plugin"\nDESC = "演示用"\n' },
            summary: '证明草稿用的是当前选中的模型', risks: '无',
          });
        } else {
          content = p.name + ' 收到（模型 ' + parsed.model + '）';
        }
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ choices: [{ message: { role: 'assistant', content } }] }));
      };
      p.pending += 1;
      const fire = () => {
        if (p.chatDelay) setTimeout(done, p.chatDelay); else done();
      };
      if (p.hold) p.holdQ.push(fire); else fire();
    });
  });
  return new Promise((resolve, reject) => {
    p.srv.once('error', reject);
    p.srv.listen(0, '127.0.0.1', () => {
      p.port = p.srv.address().port;
      p.base = 'http://127.0.0.1:' + p.port + '/v1';
      resolve(p);
    });
  });
}
//  必须是真身引用，不能用 {name,base,…} 字面量 + Object.assign 抄一份：
// pending / hold / chatDelay 都是原始值属性，抄一份之后两边就各改各的
// （chats / probes 是数组才碰巧还对得上），"扣住回复"这类开关会静默失效。
let A = null, B = null;
const MODEL_SF = 'sf-chat-model';
const MODEL_SF2 = 'sf-second-model';
const MODEL_XM = 'xm-chat-model';

// ── Brain 子进程
async function pickPython() {
  const extra = String(process.env.VIGIL_PYTHON_CANDIDATES || '')
    .split(';').map((s) => s.trim()).filter(Boolean);
  const cands = [
    process.env.VIGIL_PYTHON,
    path.join(WT, 'venv', 'Scripts', 'python.exe'),
    ...extra,
    path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Python', 'Python313', 'python.exe'),
    'python',
  ].filter(Boolean);
  for (const py of cands) {
    if (py !== 'python' && !fs.existsSync(py)) continue;
    const ok = await new Promise((res) => {
      const c = spawn(py, ['-c', 'import httpx'], { stdio: 'ignore' });
      c.on('error', () => res(false));
      c.on('exit', (code) => res(code === 0));
    });
    if (ok) { log('    [Python] ' + py); return py; }
  }
  throw new Error('找不到带 httpx 的 Python（可用 VIGIL_PYTHON 指定）');
}
let PYEXE = 'python';
let brain = null;
const BTOK = crypto.randomBytes(20).toString('hex');
let BRAIN_PORT = 0;

async function startBrain() {
  const home = path.join(ROOT, 'home');
  const cfg = {
    api_base: A.base,
    api_key: A.secret,
    provider: 'siliconflow',
    model: MODEL_SF,
    // 一家有密钥（硅基）、一家**没有**（小米）—— "缺密钥"那条要看得见
    api_keys: { siliconflow: A.secret },
    // 清单预置三条：其中小米那条就是"没配密钥所以点不动"的样子
    saved_models: [
      { provider: 'siliconflow', base: A.base, model: MODEL_SF, name: MODEL_SF },
      { provider: 'siliconflow', base: A.base, model: MODEL_SF2, name: MODEL_SF2 },
      { provider: 'xiaomi', base: B.base, model: MODEL_XM, name: MODEL_XM },
    ],
    chat_mode: 'api',
    model_mode: 'cloud',
    brain_token: BTOK,
    persona_file: 'personas/default.json',
  };
  fs.mkdirSync(path.join(home, 'config'), { recursive: true });
  fs.mkdirSync(path.join(home, 'plugins'), { recursive: true });
  fs.writeFileSync(path.join(home, 'config', 'config.json'),
    JSON.stringify(cfg, null, 2), 'utf8');
  const out = fs.openSync(path.join(ROOT, 'logs', 'brain.log'), 'a');
  const env = { ...process.env, VIGIL_HOME: home, VIGIL_PORT: String(BRAIN_PORT),
    PYTHONDONTWRITEBYTECODE: '1', PYTHONUNBUFFERED: '1' };
  brain = spawn(PYEXE, [path.join(WT, 'brain', 'server.py')],
    { cwd: path.join(WT, 'brain'), env, stdio: ['ignore', out, out] });
}
async function waitPort(port, ms = 40000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const ok = await new Promise((r) => {
      const q = http.get({ host: '127.0.0.1', port, path: '/', timeout: 800 },
        (x) => { x.resume(); r(true); });
      q.on('error', () => r(false));
      q.on('timeout', () => { q.destroy(); r(false); });
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
      timeout: 90000,
    }, (x) => {
      let d = ''; x.on('data', (c) => (d += c));
      x.on('end', () => { try { resolve({ status: x.statusCode, json: JSON.parse(d) }); }
        catch (_) { resolve({ status: x.statusCode, raw: d }); } });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('api timeout')); });
    if (data) req.write(data);
    req.end();
  });
}

// ── Electron + CDP
let app = null, ctl = null, sendCdp;
let CDP = 0;
const PROF = path.join(ROOT, 'profile');
let elog = null;
async function startElectron(first) {
  if (first) fs.rmSync(PROF, { recursive: true, force: true });
  CDP = await freePort();
  const eenv = { ...process.env, VIGIL_SIDECAR_DISABLE: '1' };
  delete eenv.ELECTRON_RUN_AS_NODE;
  for (const k of Object.keys(eenv)) if (k.startsWith('ELECTRON_')) delete eenv[k];
  elog = fs.openSync(path.join(ROOT, 'logs', 'electron.log'), 'a');
  app = spawn(ELECTRON, ['.', '--user-data-dir=' + PROF, '--remote-debugging-port=' + CDP,
    '--disable-gpu', '--no-sandbox'], { cwd: CLIENT, stdio: ['ignore', elog, elog], env: eenv });
  let t = null;
  for (let i = 0; i < 80 && !t; i++) {
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
      if (w.has(m.id)) { w.get(m.id)(m); w.delete(m.id); } });
    ctl.on('open', resolve); ctl.on('error', reject);
    sendCdp = (method, params) => new Promise((r) => { const i = ++id; w.set(i, r);
      ctl.send(JSON.stringify({ id: i, method, params })); });
  });
  await sendCdp('Runtime.enable');
  await sendCdp('Page.enable');
}
async function ev(expr) {
  const r = await sendCdp('Runtime.evaluate',
    { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) {
    return { __error: (r.exceptionDetails.exception && r.exceptionDetails.exception.description)
      || r.exceptionDetails.text };
  }
  return r.result && r.result.result ? r.result.result.value : undefined;
}
const until = async (expr, ms = 40000) => {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (await ev('Boolean(' + expr + ')')) return;
    await sleep(150);
  }
  throw new Error('等待超时: ' + expr);
};
async function shot(name) {
  await sleep(350);
  const r = await sendCdp('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(EV, name + '.png'), Buffer.from(r.result.data, 'base64'));
  log('  [截图] ' + name + '.png');
}
async function fill(sel, text) {
  return ev(`(function(){const el=document.querySelector(${JSON.stringify(sel)});
    if(!el) return '__no_such_element__';
    el.value=${JSON.stringify(text)};
    el.dispatchEvent(new Event('input',{bubbles:true}));return el.value.length;})()`);
}
async function click(sel) {
  return ev(`(function(){const el=document.querySelector(${JSON.stringify(sel)});
    if(!el) return '__no_such_element__';
    el.click();return 'clicked';})()`);
}
// 下拉里第 n 行的状态快照
async function popState() {
  return ev(`(function(){
    const pop=document.querySelector('#modelPop');
    if(!pop) return {open:false};
    const rows=[...pop.querySelectorAll('.mqrow')].map(r=>({
      name:(r.querySelector('.lb b')||{}).textContent||'',
      sub:(r.querySelector('.lb span')||{}).textContent||'',
      on:r.classList.contains('on'),
      tag:(r.querySelector('.tag')||{}).textContent||'',
      hasX:!!r.querySelector('.mq-x'),
    }));
    return {open:!pop.hidden,rows,
      btn:(document.querySelector('#modelBtn')||{}).textContent||'',
      top:(document.querySelector('.chat-model')||{}).textContent||'',
      msg:(document.querySelector('#mqMsg')||{}).textContent||''};})()`);
}

// ── 主流程
async function main() {
  log('[0] 起两家假服务商…');
  A = await makeProvider('A硅基', [MODEL_SF, MODEL_SF2]);
  B = await makeProvider('B小米', [MODEL_XM]);
  // 安全闸：清单里任何地址都必须是本机回环，绝不允许打到真实厂商
  for (const p of [A, B]) {
    if (!/^http:\/\/127\.0\.0\.1:\d+\/v1$/.test(p.base)) {
      throw new Error('假服务商地址不是本机回环，拒绝继续：' + p.base);
    }
  }
  log('    A ' + A.base + '   B ' + B.base);

  PYEXE = await pickPython();
  BRAIN_PORT = await freePort();
  log('[1] 起隔离 Brain（端口 ' + BRAIN_PORT + '）…');
  await startBrain();
  if (!(await waitPort(BRAIN_PORT))) throw new Error('brain 启动失败');
  for (let i = 0; i < 40; i++) {
    try { const r = await brainApi('GET', '/api/models/saved'); if (r.status === 200) break; } catch (_) {}
    await sleep(500);
  }
  const s0 = (await brainApi('GET', '/api/models/saved')).json;
  rec('D0 Brain 就绪；预置清单 3 条、当前 = ' + MODEL_SF,
    s0.ok && s0.models.length === 3 && s0.current.model === MODEL_SF,
    JSON.stringify({ n: s0.models.length, cur: s0.current.model }));

  log('[2] 启动真实 Electron…');
  await startElectron(true);
  await ev(`(async()=>{
    window.confirm=()=>true;
    await window.vigil.cfg.set({base:'http://127.0.0.1:${BRAIN_PORT}',wizard_done:true,roots:[]});
    await window.vigil.token.set(${JSON.stringify(BTOK)});
    return 1;})()`);
  let who = null;
  for (let i = 0; i < 40; i++) {
    who = await ev('window.vigil.device.whoami()');
    if (who && String(who.base || '').includes(String(BRAIN_PORT))) break;
    await sleep(700);
  }
  rec('D1 Electron 连接隔离 Brain', !!(who && String(who.base || '').includes(String(BRAIN_PORT))),
    JSON.stringify(who));

  // 进聊天页
  await ev(`document.querySelector('#gnav [data-page="chat"]').click()`);
  await until(`document.querySelector('#modelBtn')`);
  await sleep(600);
  const label0 = await ev(`document.querySelector('#modelBtn').textContent`);
  const top0 = await ev(`document.querySelector('.chat-model').textContent`);
  rec('M1 composer 上显示当前模型名（与顶栏一致）',
    label0.indexOf(MODEL_SF) >= 0 && top0.indexOf(MODEL_SF) >= 0,
    JSON.stringify({ btn: label0, top: top0 }));
  await shot('01-home-composer');

  log('[3] 打开模型下拉…');
  await click('#modelBtn');
  await until(`document.querySelector('#modelPop') && !document.querySelector('#modelPop').hidden`);
  await until(`document.querySelectorAll('#modelPop .mqrow').length >= 3`);
  let ps = await popState();
  rec('M2 下拉列出已保存模型（3 条：两家服务商，同名/异名分得开）',
    ps.open && ps.rows.length === 3
    && ps.rows.some((r) => r.sub.indexOf('硅基流动') >= 0)
    && ps.rows.some((r) => r.sub.indexOf('小米 MiMo') >= 0),
    JSON.stringify(ps.rows.map((r) => r.name + '|' + r.sub)));
  rec('M3 当前那条有选中标记，且与按钮上的名字一致',
    ps.rows.filter((r) => r.on).length === 1
    && ps.rows.find((r) => r.on).name.indexOf(MODEL_SF) >= 0
    && ps.btn.indexOf(MODEL_SF) >= 0,
    JSON.stringify({ on: ps.rows.filter((r) => r.on).map((r) => r.name), btn: ps.btn }));
  rec('M4 缺密钥的服务商那一行有明确标记（不装作可用）',
    ps.rows.some((r) => r.sub.indexOf('小米') >= 0 && r.tag.indexOf('缺密钥') >= 0),
    JSON.stringify(ps.rows.map((r) => r.name + ':' + r.tag)));
  rec('M5 底部有「添加或管理模型」入口',
    await ev(`!!document.querySelector('#mqManage')`), '');
  await shot('02-model-dropdown');

  log('[4] 搜索过滤…');
  await fill('#mqSearch', MODEL_SF2);
  await sleep(250);
  ps = await popState();
  rec('M6 搜索只剩匹配项', ps.rows.length === 1 && ps.rows[0].name.indexOf(MODEL_SF2) >= 0,
    JSON.stringify(ps.rows.map((r) => r.name)));
  await fill('#mqSearch', '');
  await sleep(250);

  log('[5] 缺密钥那条：点了必须明说，不许静默换一个能用的…');
  const idxXm = ps.rows.findIndex ? ps.rows.findIndex((r) => r.name.indexOf(MODEL_XM) >= 0) : -1;
  await ev(`(function(){const rs=[...document.querySelectorAll('#modelPop .mqrow')];
    const i=rs.findIndex(r=>r.textContent.indexOf('${MODEL_XM}')>=0);
    if(i>=0) rs[i].click(); return i;})()`);
  await sleep(900);
  ps = await popState();
  const curAfterRefuse = (await brainApi('GET', '/api/models/saved')).json.current;
  rec('M7 缺密钥时明确报错且当前模型不变',
    /密钥/.test(ps.msg) && curAfterRefuse.model === MODEL_SF,
    JSON.stringify({ msg: ps.msg, cur: curAfterRefuse.model }));
  await shot('03-missing-key-refused');

  log('[6] 先在设置里给小米补一次 key，再回来一键切换…');
  let r = await brainApi('POST', '/api/models/apply',
    { provider: 'xiaomi', api_base: B.base, chat: MODEL_XM, api_key: B.secret });
  rec('M8 设置页保存小米模型（带上密钥）成功', r.json.ok === true, JSON.stringify(r.json.changed || []));
  r = await brainApi('POST', '/api/models/quick-switch',
    { provider: 'siliconflow', model: MODEL_SF });
  rec('M8b 切回硅基', r.json.ok === true, JSON.stringify(r.json.changed || []));

  await ev(`document.querySelector('#mqSearch').value='';document.querySelector('#mqSearch').dispatchEvent(new Event('input',{bubbles:true}))`);
  // 走真实 UI 路径把面板关掉再打开：openPop() 会重新拉一次清单，
  // 正好验证"在设置里补过 key 之后，下拉重新打开就能看到状态更新"。
  if (await ev(`!document.querySelector('#modelPop').hidden`)) await click('#mqClose');
  await until(`document.querySelector('#modelPop').hidden`);
  await click('#modelBtn');
  await until(`document.querySelectorAll('#modelPop .mqrow').length >= 3`);
  ps = await popState();
  const xmRow = ps.rows.find((r) => r.name.indexOf(MODEL_XM) >= 0) || {};
  const msgCountBefore = await ev(`document.querySelectorAll('#msgs .turn').length`);
  await ev(`(function(){const rs=[...document.querySelectorAll('#modelPop .mqrow')];
    const i=rs.findIndex(r=>r.textContent.indexOf('${MODEL_XM}')>=0);
    if(i>=0) rs[i].click(); return i;})()`);
  await sleep(2000);
  const curNow = (await brainApi('GET', '/api/models/saved')).json.current;
  const labelNow = await ev(`document.querySelector('#modelBtn').textContent`);
  const topNow = await ev(`document.querySelector('.chat-model').textContent`);
  rec('M9 点一下就切到小米（服务端 current + 按钮 + 顶栏三处一致）',
    curNow.model === MODEL_XM && curNow.provider === 'xiaomi'
    && labelNow.indexOf(MODEL_XM) >= 0 && topNow.indexOf(MODEL_XM) >= 0,
    JSON.stringify({ cur: curNow.provider + '/' + curNow.model, btn: labelNow,
      top: topNow, 补key后那条的状态: xmRow.tag, 刷新后的提示: ps.msg }));
  await shot('04-switched-to-xiaomi');

  log('[7] 切换后的下一句真的走新家新模型（问句打给谁看得见）…');
  const nB = B.chats.length;
  await fill('#ta', '切换之后的一句话');
  await click('#sendBtn');
  await until(`document.querySelectorAll('#msgs .turn').length > ${msgCountBefore}`, 60000);
  await sleep(1200);
  const hitB = B.chats.filter((c) => c.text.indexOf('切换之后的一句话') >= 0);
  const hitA = A.chats.filter((c) => c.text.indexOf('切换之后的一句话') >= 0);
  // 一条话可能引出多次调用（回复完之后 Brain 还会另起一个线程做记忆抽取，
  // 那份 prompt 里同样带着原话），所以按"哪句话打到了谁、用的是哪个模型"认，
  // 而不是数请求条数。
  rec('M10 这句话真的打在小米上、用的就是选中的模型',
    hitB.length >= 1 && hitB.every((c) => c.model === MODEL_XM) && hitA.length === 0,
    JSON.stringify({ B: hitB.map((c) => c.model), A: hitA.map((c) => c.model) }));
  rec('M11 切模型没有清空会话历史（消息流还在，且变多了）',
    (await ev(`document.querySelectorAll('#msgs .turn').length`)) > msgCountBefore,
    'before=' + msgCountBefore);
  await shot('05-chat-after-switch');

  log('[8] Creator 草稿必须用当前选中的模型…');
  const cntA = A.chats.length, cntB = B.chats.length;
  await click('#creatorBtn');
  await until(`!document.querySelector('#creatorBar').hidden`);
  await fill('#ta', 'ZXFIX_DRAFT 随便来一个插件');
  await click('#sendBtn');
  await until(`document.querySelector('.crturn') || /生成失败/.test(document.querySelector('#msgs').textContent)`, 90000);
  const card = await ev(`(function(){const c=document.querySelector('.crturn');
    return c?c.textContent:'';})()`);
  // 两条独立证据：①草稿请求实际打在哪家、用的哪个模型（传输层，最硬）
  //             ②草稿卡上写的模型名（界面层）
  // list_drafts() 的摘要字段里没有 model（MVP-1 的设计），所以不拿它当证据。
  const draftB = B.chats.slice(cntB).filter((c) => /ZXFIX_DRAFT/.test(c.text));
  const draftA = A.chats.slice(cntA).filter((c) => /ZXFIX_DRAFT/.test(c.text));
  rec('M12 Creator 草稿用的是当前选中的小米模型（传输层 + 卡片两处一致）',
    draftB.length === 1 && draftB[0].model === MODEL_XM && draftB[0].authOk
    && draftA.length === 0 && card.indexOf('模型：' + MODEL_XM) >= 0
    && card.indexOf('通过') >= 0,
    JSON.stringify({ B: draftB.map((c) => c.model), A: draftA.length,
      卡上模型: (card.match(/模型：(\S+)/) || [])[1] || (card ? '（卡片在但没有模型字样）' : '（没有卡片）') }));
  await shot('06-creator-uses-selected-model');
  // 创作模式是循环切换（关→插件→技能→关），点一次不一定回得到「关」，
  // 必须看到按钮真的写着「关」为止 —— 否则下一步的"聊天"会被送进创作流程。
  for (let i = 0; i < 3; i++) {
    if (/关/.test(await ev(`document.querySelector('#creatorBtn').textContent`))) break;
    await click('#creatorBtn');
    await sleep(250);
  }
  const modeNow = await ev(`document.querySelector('#creatorBtn').textContent`);
  if (!/关/.test(modeNow)) throw new Error('创作模式没关掉：' + modeNow);

  log('[9] 生成中不让切（避免"界面写的"和"实际用的"对不上）…');
  // 先回到硅基（A），这样"正在跑的那一轮"和"想切过去的那家"是两家，
  // 串台与否一眼看得出来。
  await click('#modelBtn');
  await until(`document.querySelectorAll('#modelPop .mqrow').length >= 2`);
  await ev(`(function(){const rs=[...document.querySelectorAll('#modelPop .mqrow')];
    const i=rs.findIndex(r=>r.textContent.indexOf('${MODEL_SF}')>=0);
    if(i>=0) rs[i].click(); return i;})()`);
  await sleep(1200);
  const backA = (await brainApi('GET', '/api/models/saved')).json.current;
  if (backA.model !== MODEL_SF) throw new Error('第 9 步前置：没切回硅基，实际 ' + backA.model);

  // 假服务商把回复扣住 → "正在生成"这段时间是确定的事实，不靠 sleep 猜。
  A.hold = true;
  const turnsBefore9 = await ev(`document.querySelectorAll('#msgs .turn').length`);
  await fill('#ta', '慢一点答这一句');
  await click('#sendBtn');
  let pending = 0;
  for (let i = 0; i < 150 && !pending; i++) { pending = A.pending; if (!pending) await sleep(80); }
  const dDisabled = await ev(`document.querySelector('#modelBtn').disabled`);
  await click('#modelBtn');
  await sleep(600);
  const popWhileBusy = await ev(`!document.querySelector('#modelPop').hidden`);
  rec('M13 生成中：模型按钮禁用、下拉打不开（这一轮跑完之前界面不许切）',
    pending > 0 && dDisabled === true && popWhileBusy === false,
    JSON.stringify({ 在飞的请求: pending, 按钮禁用: dDisabled, 下拉打开: popWhileBusy }));
  await shot('06b-busy-switch-blocked');

  // 界面拦住了，那就绕过界面从 API 直接切（这条路必须允许，而且必须伤不到在飞那一轮：
  // 服务端每轮取一次配置快照，切模型只影响"下一轮"）。
  const switchMid = await brainApi('POST', '/api/models/quick-switch',
    { provider: 'xiaomi', model: MODEL_XM });
  const midCur = (await brainApi('GET', '/api/models/saved')).json.current;
  A.release();
  await until(`document.querySelectorAll('#msgs .turn').length > ${turnsBefore9}`, 60000);
  await sleep(2500);
  const hitSlowA = A.chats.filter((c) => c.text.indexOf('慢一点答这一句') >= 0);
  const hitSlowB = B.chats.filter((c) => c.text.indexOf('慢一点答这一句') >= 0);
  rec('M14 生成中从 API 切走：在飞那一轮仍然打在原来的家、原模型（没有中途串台）',
    switchMid.json.ok === true && midCur.model === MODEL_XM
    && hitSlowA.length >= 1 && hitSlowA.every((c) => c.model === MODEL_SF)
    && hitSlowB.length === 0,
    JSON.stringify({ 切走成功: switchMid.json.ok, 切换后当前: midCur.model,
      onA: hitSlowA.map((c) => c.model), onB: hitSlowB.length }));
  A.chatDelay = 0;

  log('[10] 从清单里移除一条 → 下拉同步…');
  await until(`!document.querySelector('#modelBtn').disabled`);   // 上一轮生成结束、按钮解禁
  await click('#modelBtn');
  await until(`document.querySelectorAll('#modelPop .mqrow').length >= 3`);
  await ev(`(function(){const rs=[...document.querySelectorAll('#modelPop .mqrow')];
    const i=rs.findIndex(r=>r.textContent.indexOf('${MODEL_SF2}')>=0);
    if(i>=0) rs[i].querySelector('.mq-x').click(); return i;})()`);
  await sleep(1200);
  ps = await popState();
  const savedAfterForget = (await brainApi('GET', '/api/models/saved')).json.models;
  rec('M15 移除后下拉与服务端清单同步少一条',
    !ps.rows.some((r) => r.name.indexOf(MODEL_SF2) >= 0)
    && !savedAfterForget.some((m) => m.model === MODEL_SF2),
    JSON.stringify({ ui: ps.rows.map((r) => r.name), api: savedAfterForget.map((m) => m.model) }));
  await shot('07-after-remove');

  log('[10b] 移除**当前正在用的**那一条：当前配置不能跟着变…');
  await until(`!document.querySelector('#modelBtn').disabled`);
  if (await ev(`document.querySelector('#modelPop').hidden`)) await click('#modelBtn');
  await until(`document.querySelectorAll('#modelPop .mqrow').length >= 1`);
  await ev(`(function(){const rs=[...document.querySelectorAll('#modelPop .mqrow')];
    const i=rs.findIndex(r=>r.textContent.indexOf('${MODEL_XM}')>=0);
    if(i>=0) rs[i].querySelector('.mq-x').click(); return i;})()`);
  await sleep(1500);
  ps = await popState();
  const curAfterRm = (await brainApi('GET', '/api/models/saved')).json.current;
  const onRow = ps.rows.find((r) => r.on) || {};
  const turnsBefore15b = await ev(`document.querySelectorAll('#msgs .turn').length`);
  if (await ev(`!document.querySelector('#modelPop').hidden`)) await click('#mqClose');
  await fill('#ta', '移除当前模型之后再问一句');
  await click('#sendBtn');
  await until(`document.querySelectorAll('#msgs .turn').length > ${turnsBefore15b}`, 60000);
  await sleep(2500);
  const hitRm = B.chats.filter((c) => c.text.indexOf('移除当前模型之后再问一句') >= 0);
  rec('M15b 移除当前这条：配置不变、界面标「未保存」、聊天照旧走它（不会偷偷换模型）',
    curAfterRm.model === MODEL_XM && /未保存/.test(onRow.name)
    && onRow.name.indexOf(MODEL_XM) >= 0
    && hitRm.length >= 1 && hitRm.every((c) => c.model === MODEL_XM),
    JSON.stringify({ 移除后的当前: curAfterRm.model, 下拉里那条: onRow.name,
      聊天打到: hitRm.map((c) => c.model) }));
  await click('#modelBtn');
  await until(`document.querySelectorAll('#modelPop .mqrow').length >= 1`);
  await shot('07b-removed-active');
  await click('#mqClose');

  log('[11] 不泄露密钥：页面文本与响应里都不该出现 fixture 密钥…');
  const bodyText = await ev(`document.body.innerText`);
  const savedRaw = JSON.stringify((await brainApi('GET', '/api/models/saved')).json);
  rec('M16 页面与接口都没有出现任何 API Key',
    bodyText.indexOf(A.secret) < 0 && bodyText.indexOf(B.secret) < 0
    && savedRaw.indexOf(A.secret) < 0 && savedRaw.indexOf(B.secret) < 0
    && savedRaw.indexOf('api_key') < 0,
    'secrets hidden');

  log('[12] 重启 Electron：清单与当前选择都还在…');
  const curBefore = (await brainApi('GET', '/api/models/saved')).json.current;
  try { app.kill(); } catch (_) {}
  await sleep(2500);
  await startElectron(false);      // 同一个 user-data-dir
  await ev(`(async()=>{window.confirm=()=>true;return 1;})()`);
  await ev(`document.querySelector('#gnav [data-page="chat"]').click()`);
  await until(`document.querySelector('#modelBtn')`);
  await sleep(1500);
  const labelRe = await ev(`document.querySelector('#modelBtn').textContent`);
  await click('#modelBtn');
  await until(`document.querySelectorAll('#modelPop .mqrow').length >= 1`);
  ps = await popState();
  const curRe = (await brainApi('GET', '/api/models/saved')).json.current;
  rec('M17 Electron 重启后：清单还在、当前选择恢复、按钮显示正确',
    ps.rows.length >= 2 && labelRe.indexOf(curBefore.model) >= 0
    && curRe.model === curBefore.model && ps.rows.some((r) => r.on),
    JSON.stringify({ rows: ps.rows.map((r) => r.name), label: labelRe, cur: curRe.model }));
  await shot('08-after-restart');

  log('[13] 渲染层无严重报错…');
  const elogTxt = fs.readFileSync(path.join(ROOT, 'logs', 'electron.log'), 'utf8');
  const bad = elogTxt.split('\n').filter((l) =>
    /Uncaught|Unhandled|TypeError|ReferenceError/.test(l));
  rec('M18 electron.log 无未捕获异常', bad.length === 0,
    bad.slice(0, 3).join(' | ') || 'clean');
}

let fatal = '';
(async () => {
  try {
    await main();
  } catch (e) {
    fatal = String((e && e.message) || e);
    log('  !!! 异常：' + fatal);
    try { await shot('99-exception'); } catch (_) {}
  } finally {
    try { if (app && app.exitCode === null) app.kill(); } catch (_) {}
    try { if (brain && !brain.killed) brain.kill(); } catch (_) {}
    try { if (A && A.srv) A.srv.close(); } catch (_) {}
    try { if (B && B.srv) B.srv.close(); } catch (_) {}
    const okN = results.filter((r) => r.ok).length;
    log('\n==== 结果 ' + okN + '/' + results.length + ' ====');
    for (const r of results) if (!r.ok) log('  FAIL ' + r.id + ' :: ' + r.detail);
    const meta = {
      note: '模型快速切换 Electron 界面联动（隔离 HOME / 两家本机假服务商 / 随机 Token）。截图不入库。',
      commit: commitNow(),
      fatal,
      // 只留 Python 名与版本，不留本机安装路径（证据要进版本库）
      python: path.basename(PYEXE), node: process.version, electron: 'electron@28.3.3',
      brain_port: BRAIN_PORT,
      provider_probes: { A: A.probes, B: B.probes },
      chat_calls: { A: A.chats.length, B: B.chats.length },
      passed: okN, total: results.length,
      results,
    };
    fs.writeFileSync(path.join(EV, 'results.json'), JSON.stringify(meta, null, 2), 'utf8');
    fs.writeFileSync(path.join(EV, 'brain.log.tail'),
      tailLog(path.join(ROOT, 'logs', 'brain.log'), 60), 'utf8');
    process.exit(okN === results.length && !fatal ? 0 : 1);
  }
})();
