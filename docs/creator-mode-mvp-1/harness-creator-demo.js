// Creator Mode MVP-1 Electron 界面演示（v2：主页对话内创作）
// 隔离一切：临时 VIGIL_HOME / 随机端口 / 随机 Token / 临时 profile，
// 不连云、不碰生产服务器、不用真实凭据。
// 假模型 fixture 只做"按契约返回 canned JSON"的替身（与 pytest 层同款契约）；
// 被测链路（pages.js → API → ipcMain → Bearer → server.py /api/creator/* →
// creator.py → model_providers.chat → 静态检查器）全是真代码。
// 入口在主页：composer 的「创作」chip 切模式 → 同一输入框发需求 → 草稿卡气泡。
// 截图按惯例不入库（*.png 已被 .gitignore 全局挡住），文本证据进本目录。
const { spawn, execSync } = require('child_process');
const path = require('path'), fs = require('fs'), os = require('os'), http = require('http');
const crypto = require('crypto');

const WT = 'F:\\sync-checks\\creator-mode-p4-4b-2';
const CLIENT = path.join(WT, 'client');
const WebSocket = require(path.join(CLIENT, 'node_modules', 'ws'));
const ELECTRON = path.join(CLIENT, 'node_modules', 'electron', 'dist', 'electron.exe');
const ROOT = path.join(os.tmpdir(), 'vigil_creator_demo');

// ── 证据目录：按 HEAD commit 动态命名（纯文件读，兼容 worktree .git 指针）──
function commitNow() {
  try {
    const gp = path.join(WT, '.git');
    let gd = gp;
    try {
      const dot = fs.readFileSync(gp, 'utf8').trim();
      if (dot.startsWith('gitdir:')) gd = dot.slice(7).trim();
    } catch (_) {}
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
    return /^[0-9a-f]{40}$/.test(head) ? head.slice(0, 7) : 'unknown';
  } catch (_) { return 'unknown'; }
}
const EV = path.join(WT, 'docs', 'creator-mode-mvp-1', 'ev-' + commitNow());
fs.rmSync(EV, { recursive: true, force: true });
fs.mkdirSync(EV, { recursive: true });
fs.mkdirSync(path.join(ROOT, 'logs'), { recursive: true });

function freePort() {
  const s = require('net').createServer();
  return new Promise((res) => {
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(...a);
const results = [];
const rec = (id, ok, detail) => {
  results.push({ id, ok, detail: String(detail || '') });
  log((ok ? '  OK  ' : '  FAIL ') + id + '  ' + String(detail || '').slice(0, 300));
};

// ── 假模型 fixture：OpenAI 兼容 /chat/completions ──────────────
// 与 brain/tests/test_creator_mode.py 的 ModelFixture 同款契约：
// 按最后一条 user 消息里的 ZXFIX_* 标记切换行为；响应带请求计数，
// 用来证明"修改后重新生成"真的又打了一次模型。
const PLUGIN_PY = [
  '# -*- coding: utf-8 -*-',
  '"""每小时整点提醒喝水的示例插件（演示 fixture 生成）。"""',
  '',
  'NAME = "hourly_water"',
  'DESC = "每小时整点提醒喝水"',
  '',
  '',
  'def prompt(ctx):',
  '    return "用户希望每小时整点被提醒喝水。"',
  '',
].join('\n');
const PLUGIN_JSON = JSON.stringify({
  name: 'hourly_water', version: '0.1.0', desc: '每小时整点提醒喝水',
}, null, 2);
const SKILL_JS = [
  '// 文本反转技能（演示 fixture 生成）',
  'export function setup(api) {',
  '  api.registerTool({',
  "    id: 'reverse',",
  "    name: 'Reverse',",
  "    description: 'Reverse a string.',",
  "    parameters: { type: 'object', properties: { text: { type: 'string' } } },",
  '    async execute(ctx, args) {',
  "      return { content: String((args && args.text) || '').split('').reverse().join('') }",
  '    },',
  '  })',
  '}',
  '',
].join('\n');
const SKILL_JSON = JSON.stringify({
  id: 'text-reverse', name: 'Text Reverse', version: '0.1.0',
  description: 'Reverse a string.', entry: 'index.js',
}, null, 2);
const BAD_PY = 'def broken(:\n    return 1\n';

const fx = {
  requests: 0,
  secret: 'fx-' + crypto.randomBytes(16).toString('hex'),
  port: 0,
  srv: null,
};
function startFixture() {
  const owner = fx;
  fx.srv = http.createServer((req, res) => {
    if (req.method !== 'POST' || !/\/chat\/completions/.test(req.url || '')) {
      res.writeHead(404); res.end('{}'); return;
    }
    if ((req.headers.authorization || '') !== 'Bearer ' + owner.secret) {
      res.writeHead(401, { 'Content-Type': 'application/json' });
      res.end('{"error":"bad creds"}'); return;
    }
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', () => {
      let parsed = {};
      try { parsed = JSON.parse(body); } catch (_) {}
      // 🔴 只认最后一条 user 消息（与 pytest fixture 同款修正）
      const msgs = parsed.messages || [];
      const blob = (msgs.length && typeof msgs[msgs.length - 1] === 'object'
        && msgs[msgs.length - 1].content) || '';
      owner.requests += 1;
      let content;
      if (/ZXFIX_FAILURE/.test(blob)) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end('{"error":{"message":"fixture boom"}}'); return;
      }
      if (/ZXFIX_NOJSON/.test(blob)) {
        content = '我不打算返回 JSON，这是纯文本回复。';
      } else {
        let py;
        if (/ZXFIX_BADCODE/.test(blob)) py = BAD_PY;
        else py = PLUGIN_PY + '\n# fixture rev ' + owner.requests + '\n';
        let files;
        if (/ZXFIX_ESCAPE/.test(blob)) {
          files = { '../evil.py': "NAME = 'evil'", 'hourly_water.py': py };
        } else if (/技能|skill/i.test(blob)) {
          files = { 'index.js': SKILL_JS, 'skill.json': SKILL_JSON };
        } else {
          files = { 'hourly_water.py': py, 'zxplugin.json': PLUGIN_JSON };
        }
        content = JSON.stringify({
          files,
          summary: '演示 fixture 第 ' + owner.requests + ' 版：按需求生成的草稿',
          risks: 'fixture 固定风险说明：本演示不安装、不执行生成的代码。',
        });
      }
      const out = JSON.stringify({
        choices: [{ message: { role: 'assistant', content } }],
      });
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(out);
    });
  });
  return new Promise((resolve, reject) => {
    fx.srv.once('error', reject);
    fx.srv.listen(0, '127.0.0.1', () => { fx.port = fx.srv.address().port; resolve(); });
  });
}

// ── Brain 服务器子进程（隔离 HOME）────────────────────────────
// Python 解释器探测：本机 PATH 里的 python 是 WorkBuddy 托管版，没有 httpx；
// 依次试候选，第一个能 import httpx 的胜出。
async function pickPython() {
  const cands = [
    process.env.VIGIL_PYTHON,
    path.join(WT, 'venv', 'Scripts', 'python.exe'),
    'F:\\Vigil\\venv\\Scripts\\python.exe',
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
  throw new Error('找不到带 httpx 的 Python');
}
let PYEXE = 'python';
let brain = null;
const BTOK = crypto.randomBytes(20).toString('hex');
let BRAIN_PORT = 0;
async function startBrain() {
  const home = path.join(ROOT, 'home');
  const cfg = {
    api_base: 'http://127.0.0.1:' + fx.port + '/v1',
    api_key: fx.secret,
    model: 'fixture-model',
    chat_mode: 'api',
    model_mode: 'cloud',
    brain_token: BTOK,
    persona_file: 'personas/default.json',
  };
  fs.mkdirSync(path.join(home, 'config'), { recursive: true });
  fs.mkdirSync(path.join(home, 'plugins'), { recursive: true });
  fs.writeFileSync(path.join(home, 'config', 'config.json'),
    JSON.stringify(cfg, null, 2), 'utf8');
  const out = fs.openSync(path.join(ROOT, 'logs', 'brain_demo.log'), 'a');
  const env = {
    ...process.env,
    VIGIL_HOME: home, VIGIL_PORT: String(BRAIN_PORT),
    PYTHONDONTWRITEBYTECODE: '1', PYTHONUNBUFFERED: '1',
  };
  brain = spawn(PYEXE, [path.join(WT, 'brain', 'server.py')],
    { cwd: path.join(WT, 'brain'), env, stdio: ['ignore', out, out] });
}
async function waitPort(port, ms = 30000) {
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
      timeout: 60000,
    }, (x) => {
      let d = ''; x.on('data', (c) => (d += c));
      x.on('end', () => { try { resolve({ status: x.statusCode, json: JSON.parse(d) }); } catch (_) { resolve({ status: x.statusCode, raw: d }); } });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('api timeout')); });
    if (data) req.write(data);
    req.end();
  });
}

// ── Electron + CDP ────────────────────────────────────────────
let app = null, ctl = null, sendCdp;
const CDP = 9373;
async function startElectron() {
  const prof = path.join(ROOT, 'profile');
  fs.rmSync(prof, { recursive: true, force: true });
  // ELECTRON_RUN_AS_NODE 会让 electron.exe 退化成 Node（P4 实测教训）
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
  const r = await sendCdp('Runtime.evaluate',
    { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) {
    return { __error: (r.exceptionDetails.exception && r.exceptionDetails.exception.description) || r.exceptionDetails.text };
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
  await sleep(300);
  const r = await sendCdp('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(EV, name + '.png'), Buffer.from(r.result.data, 'base64'));
  log('  [截图] ' + name + '.png');
}
// 原生赋值 + input 事件，保证页面状态与真实输入一致
async function fill(sel, text) {
  return ev(`(function(){
    const el=document.querySelector(${JSON.stringify(sel)});
    if(!el) return '__no_such_element__';
    el.value=${JSON.stringify(text)};
    el.dispatchEvent(new Event('input',{bubbles:true}));
    return el.value.length;})()`);
}
async function click(sel) {
  return ev(`(function(){
    const el=document.querySelector(${JSON.stringify(sel)});
    if(!el) return '__no_such_element__';
    el.click(); return 'clicked';})()`);
}
// 草稿卡状态快照（生成结果/验证徽章/文件代码/版本号）
async function draftState() {
  return ev(`(function(){
    const cards=[...document.querySelectorAll('.crturn')];
    const last=cards[cards.length-1];
    if(!last) return {cards:0,files:[],names:[],meta:'',errs:[],badge:'',bad:'',kindtag:'',full:''};
    const badge=(last.querySelector('.tag.ok')||{}).textContent||'';
    const bad=(last.querySelector('.tag.err')||{}).textContent||'';
    const files=[...last.querySelectorAll('.cr-code .md pre code')].map(c=>c.textContent);
    const names=[...last.querySelectorAll('.pkg-h')].map(h=>h.textContent.trim());
    const meta=(last.querySelector('.mono')||{}).textContent||'';
    const errs=[...last.querySelectorAll('ul li b')].map(b=>b.textContent);
    const kindtag=(last.querySelector('.tag')||{}).textContent||'';
    const full=last.textContent||'';
    return {cards:cards.length,badge,bad,files,names,meta,errs,kindtag,full};})()`);
}
async function waitToast(text, ms = 15000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const v = await ev(`(function(){
      const t=document.getElementById('toast');
      return t&&t.textContent||'';})()`);
    if (v && new RegExp(text).test(v)) return v;
    await sleep(200);
  }
  return '';
}

(async () => {
  fs.writeFileSync(path.join(ROOT, 'logs', 'brain_demo.log'), '');
  fs.writeFileSync(path.join(ROOT, 'logs', 'electron.log'), '');
  try {
    log('[0] 起假模型 fixture…');
    await startFixture();
    log('    fixture 端口 ' + fx.port);
    PYEXE = await pickPython();
    BRAIN_PORT = await freePort();
    log('[1] 起隔离 Brain（端口 ' + BRAIN_PORT + '）…');
    await startBrain();
    if (!(await waitPort(BRAIN_PORT))) throw new Error('brain 启动失败');
    for (let i = 0; i < 30; i++) {
      try {
        const r = await brainApi('GET', '/api/creator/drafts');
        if (r.status === 200) break;
      } catch (_) {}
      await sleep(500);
    }
    rec('D0 Brain 就绪且 /api/creator/drafts 可鉴权访问', true, 'port=' + BRAIN_PORT);

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
      if (who && String(who.base || '').includes(String(BRAIN_PORT))) break;
      await sleep(700);
    }
    rec('D1 Electron 连接隔离 Brain', !!(who && String(who.base || '').includes(String(BRAIN_PORT))),
      JSON.stringify(who));
    await shot('01-home-chat');

    log('[3] 场景 C1：主页入口 —— composer 的「创作」chip…');
    await until(`document.querySelector('#creatorBtn')`);
    const c1btn = await ev(`document.querySelector('#creatorBtn').textContent`);
    rec('C1 主页 composer 有「创作」开关（默认关）', /创作：关/.test(c1btn || ''), c1btn);
    await click('#creatorBtn');   // 关 → 插件
    await until(`document.querySelector('#creatorBar') && !document.querySelector('#creatorBar').hidden`);
    const c1b = await ev(`(function(){
      const ph=document.querySelector('#ta').placeholder||'';
      const chips=[...document.querySelectorAll('#creatorBar .chip[data-kind]')].map(c=>c.dataset.kind+':'+(c.classList.contains('on')?'on':'off'));
      return {ph,chips,btn:document.querySelector('#creatorBtn').textContent};})()`);
    rec('C1b 切到创作-插件：提示条出现、placeholder 变、类型 chip 高亮插件',
      /描述你想要的插件/.test(c1b.ph || '') && /plugin:on/.test((c1b.chips || []).join(','))
      && /创作：插件/.test(c1b.btn || ''),
      JSON.stringify(c1b));
    await shot('02-creator-mode-on');

    log('[4] 场景 C2：对话内生成插件草稿（真实模型链路，fixture 替身）…');
    const n0 = fx.requests;
    const f1 = await fill('#ta', '一个每小时整点提醒我喝水的插件');
    rec('C2a 需求已填入同一输入框', f1 === '一个每小时整点提醒我喝水的插件'.length, 'len=' + f1);
    await click('#sendBtn');
    await until(`document.querySelector('.crturn') || /生成失败/.test(document.querySelector('#msgs').textContent)`, 60000);
    const s2 = await draftState();
    rec('C2 草稿卡气泡：验证通过徽章 + 两个文件的真实代码 + 需求回显',
      s2.cards >= 1 && s2.badge === '通过' && s2.files.length === 2
      && /NAME = "hourly_water"/.test((s2.files || []).join(''))
      && /每小时整点提醒我喝水/.test(s2.full || ''),
      JSON.stringify({ cards: s2.cards, badge: s2.badge, files: s2.files.map((x) => x.length) }));
    rec('C2b 生成真的走了模型链路（fixture 请求数 +1）', fx.requests === n0 + 1,
      'fixture requests ' + n0 + ' -> ' + fx.requests);
    const notice = await ev(`document.querySelector('.crturn').textContent`);
    rec('C2c 卡片带安全声明（静态验证 ≠ 沙箱安全）', /通过 ≠ 沙箱安全/.test(notice || ''), 'notice ok');
    // 草稿确实在独立工作区，不在正式插件目录
    const list1 = await brainApi('GET', '/api/creator/drafts');
    const did = (list1.json.drafts || [])[0] && list1.json.drafts[0].id;
    rec('C2d 草稿落独立工作区（data/creator_drafts），未进 plugins/',
      !!did
      && fs.existsSync(path.join(ROOT, 'home', 'data', 'creator_drafts', did, 'hourly_water.py'))
      && !fs.existsSync(path.join(ROOT, 'home', 'plugins', 'hourly_water.py')),
      'draft=' + did);
    await shot('03-plugin-draft-card');

    log('[5] 场景 C3：卡上「重新验证」…');
    await click('.crturn #crReval');
    await waitToast('静态验证通过');
    rec('C3 重新验证可用且 toast 报告通过', true, 'revalidate ok');
    await shot('04-revalidate');

    log('[6] 场景 C4：卡上提交修改 → v2…');
    const n1 = fx.requests;
    await fill('.crturn #crFb', '把提醒文案改成「该喝水了」');
    await click('.crturn #crRev');
    await until(`[...document.querySelectorAll('.crturn .mono')].some(m=>/v2/.test(m.textContent))`, 60000);
    await waitToast('已生成新版本 v2');
    const s4 = await draftState();
    rec('C4 修改后升 v2 且模型被再次调用（fixture rev 2 出现在代码里）',
      /v2/.test(s4.meta || '') && /fixture rev 2/.test((s4.files || []).join('')),
      s4.meta + ' | fixture requests=' + fx.requests);
    const histOk = fs.existsSync(path.join(ROOT, 'home', 'data', 'creator_drafts', '.history', did, 'v1'));
    const noInner = !fs.existsSync(path.join(ROOT, 'home', 'data', 'creator_drafts', did, 'history'));
    rec('C4b 旧版本存 .history/<id>/v1，草稿目录内无 history', histOk && noInner,
      'hist=' + histOk + ' inner=' + noInner);
    await shot('05-revised-v2');

    log('[7] 场景 C5：切创作-技能，生成技能草稿…');
    await click('#creatorBar .chip[data-kind="skill"]');
    await until(`document.querySelector('#creatorBar .chip[data-kind="skill"].on')`);
    await fill('#ta', '一个把一段文字反转后返回的技能');
    await click('#sendBtn');
    await until(`[...document.querySelectorAll('.crturn')].length>=2`, 60000);
    await sleep(600);
    const s5 = await draftState();
    rec('C5 技能草稿卡：index.js + skill.json，验证通过，类型标「技能」',
      /index\.js/.test((s5.names || []).join('')) && /skill\.json/.test((s5.names || []).join(''))
      && s5.badge === '通过' && /技能/.test(s5.kindtag || ''),
      JSON.stringify({ names: s5.names, badge: s5.badge, kindtag: s5.kindtag }));
    await shot('06-skill-draft-card');

    log('[8] 场景 C6：坏代码 → 静态验证失败（不执行代码）…');
    await click('#creatorBar .chip[data-kind="plugin"]');
    await fill('#ta', 'ZXFIX_BADCODE 生成一个语法错误的插件');
    await click('#sendBtn');
    await until(`[...document.querySelectorAll('.crturn .tag.err')].length>=1`, 60000);
    await sleep(500);
    const s6 = await draftState();
    rec('C6 坏代码草稿卡：验证未通过且错误码具体（python_syntax）',
      /未通过/.test(s6.bad || '') && (s6.errs || []).indexOf('python_syntax') >= 0,
      JSON.stringify({ bad: s6.bad, errs: s6.errs }));
    await shot('07-badcode-validation-failed');

    log('[9] 场景 C7：模型失败 → 明说，不假装生成…');
    const listBefore = (await brainApi('GET', '/api/creator/drafts')).json.drafts.length;
    await fill('#ta', 'ZXFIX_FAILURE 模拟模型挂掉');
    await click('#sendBtn');
    await until(`/生成失败/.test(document.querySelector('#msgs').textContent)`, 30000);
    const listAfter = (await brainApi('GET', '/api/creator/drafts')).json.drafts.length;
    rec('C7 模型失败：消息流里明说且草稿数不变',
      listAfter === listBefore && !/[.\s\S]*crturn[.\s\S]*/.test(''),
      'drafts ' + listBefore + '->' + listAfter);
    await shot('08-model-failure');

    log('[10] 场景 C8：右栏「我的草稿」列表 → 点条目回到对话流…');
    await click('#rpBtn');   // 打开右栏
    await until(`document.querySelector('#rpDrafts') && !document.querySelector('#rpDrafts').hidden`, 15000);
    const rpRows = await ev(`document.querySelectorAll('#rpDrafts [data-did]').length`);
    rec('C8 右栏草稿列表有条目', rpRows >= 3, 'rows=' + rpRows);
    const before8 = await ev(`document.querySelectorAll('.crturn').length`);
    await click('#rpDrafts [data-did]');
    await sleep(1200);
    const after8 = await ev(`document.querySelectorAll('.crturn').length`);
    rec('C8b 点草稿条目 → 对话流追加一张草稿卡', after8 === before8 + 1,
      'cards ' + before8 + ' -> ' + after8);
    await shot('09-drafts-panel-reopen');

    log('[11] 场景 C9：删除一张草稿卡…');
    // 删刚打开的那张（最后一张）
    const delCount = await ev(`document.querySelectorAll('.crturn').length`);
    await ev(`(function(){
      const cards=[...document.querySelectorAll('.crturn')];
      cards[cards.length-1].querySelector('#crDel').click();})()`);
    await until(`document.querySelectorAll('.crturn').length<${delCount}`, 15000);
    const listFinal = (await brainApi('GET', '/api/creator/drafts')).json.drafts.length;
    rec('C9 删除草稿生效（卡移除 + 列表减少一条）', listFinal === listBefore - 1,
      'drafts ' + listBefore + ' -> ' + listFinal);

    log('[12] 场景 C10：聊天历史刷新不冲掉草稿卡…');
    // 发一条普通消息（会改变历史条数，触发 loadMsgs 重绘）
    await click('#creatorBtn'); await sleep(200); // 技能 → 关
    await fill('#ta', '随便说一句测试历史刷新');
    await click('#sendBtn');
    await sleep(2500);
    const after10 = await ev(`document.querySelectorAll('.crturn').length`);
    rec('C10 新历史消息到达后草稿卡仍在消息流里', after10 >= 2, 'crturn=' + after10);

    rec('D3 Electron 全程无渲染层严重报错',
      !(fs.readFileSync(path.join(ROOT, 'logs', 'electron.log'), 'utf8')
        .match(/preload-error|Uncaught \(the authors/g)),
      'electron.log 已留存');
  } catch (e) {
    log('演示异常：' + e.message + '\n' + (e.stack || ''));
    results.push({ id: 'EXC', ok: false, detail: e.message });
    try { await shot('99-exception'); } catch (_) {}
  } finally {
    try { ctl && ctl.close(); } catch (_) {}
    if (app) app.kill();
    await sleep(800);
    try { execSync('taskkill /F /IM electron.exe /T', { stdio: 'ignore' }); } catch (_) {}
    if (brain && brain.exitCode === null) {
      try { brain.kill(); } catch (_) {}
    }
    try { fx.srv.close(); } catch (_) {}
    // 元数据
    let commit = '', pyver = '';
    try {
      const gp = path.join(WT, '.git');
      let gd = gp;
      try {
        const dot = fs.readFileSync(gp, 'utf8').trim();
        if (dot.startsWith('gitdir:')) gd = dot.slice(7).trim();
      } catch (_) {}
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
      pyver = await new Promise((res) => {
        const c = spawn(PYEXE, ['--version'], { stdio: ['ignore', 'pipe', 'pipe'] });
        let o = '';
        c.stdout.on('data', (d) => (o += d));
        c.stderr.on('data', (d) => (o += d));
        c.on('error', () => res(''));
        c.on('exit', () => res(o.trim()));
      });
    } catch (_) {}
    const dirty = [];
    try {
      const gs = await new Promise((res) => {
        const c = spawn('git', ['status', '--porcelain'], { cwd: WT, stdio: ['ignore', 'pipe', 'pipe'] });
        let o = '';
        c.stdout.on('data', (d) => (o += d));
        c.on('exit', () => res(o));
      });
      gs.split(/\r?\n/).filter(Boolean).forEach((l) => dirty.push(l));
    } catch (_) {}
    fs.writeFileSync(path.join(EV, 'results.json'), JSON.stringify({
      started: new Date().toISOString(),
      note: 'Creator Mode MVP-1 Electron 界面演示（主页对话内创作）。'
        + '工作树含未提交改动（下方 dirty），基线 commit 为 HEAD；截图不入库。',
      commit,
      dirty_files: dirty,
      python: pyver,
      fixture_requests: fx.requests,
      brain_port: BRAIN_PORT,
      electron: 'electron@' + require(path.join(CLIENT, 'node_modules', 'electron', 'package.json')).version,
      node: process.version,
      harness_sha256_16: crypto.createHash('sha256')
        .update(fs.readFileSync(__filename)).digest('hex').slice(0, 16),
      results,
    }, null, 2));
    log('\n结果 ' + results.filter((r) => r.ok).length + '/' + results.length
      + ' → ' + EV);
  }
  process.exit(results.length && results.every((r) => r.ok) ? 0 : 1);
})();
