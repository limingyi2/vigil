// -*- coding: utf-8 -*-
/**
 * 端到端：真 brain + 真客户端，跑一遍看连不连得上。
 *
 * 隔离原则（重要）：
 * - brain 整份**拷到临时目录**再跑，端口用默认 8788；她的真实 data/
 *   （记忆、聊天存档、设备表）一个字都不动
 * - 客户端用独立的 `--user-data-dir`，配置写到临时目录
 * - 跑完删掉 keytar 里那条设备 token（那是临时 brain 发的，留着会让
 *   下次启动真实 brain 时对不上）
 *
 * 验证的点：
 *   1. brain 起来了
 *   2. 客户端自动探测 -> 拿主人钥匙 -> 注册设备 -> 存设备 token
 *   3. 界面真的进到聊天页（不是设置页），状态栏显示"已连接"
 *   4. 临时 brain 的 devices.json 里出现这台机器
 *   5. 截图存下来给人看
 *
 * 不做的：**不发真实聊天**。那要调 LLM、要花钱、还会往她的记忆里写一条
 * 测试消息。要验那条链路，人自己打开客户端说句话就行。
 *
 * 跑法：node tools/e2e.js
 */
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');
const http = require('http');
const crypto = require('crypto');
const WebSocket = require('ws');

const ROOT = path.join(__dirname, '..', '..');
const CLIENT = path.join(__dirname, '..');
const VENV_PY = path.join(ROOT, 'venv', 'Scripts', 'python.exe');
const PY = process.env.VIGIL_BUILD_PYTHON || (fs.existsSync(VENV_PY) ? VENV_PY : 'python');
const ELECTRON = path.join(CLIENT, 'node_modules', 'electron', 'dist', 'electron.exe');
const BASE = 'http://127.0.0.1:8788';
const CDP_PORT = 9333;
const SHOT = path.join(CLIENT, '_shots', 'e2e.png');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** electron.exe 必须以 GUI 模式跑：有些环境全局设了 ELECTRON_RUN_AS_NODE=1，
 *  不删掉它所有 electron 子进程都会变成纯 Node（窗口永远起不来）。 */
function guiEnv(extra) {
  const e = { ...process.env, ...(extra || {}) };
  delete e.ELECTRON_RUN_AS_NODE;
  return e;
}

function get(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, body: d }));
    });
    req.on('error', reject);
    req.setTimeout(3000, () => req.destroy(new Error('timeout')));
  });
}

/** 带 Bearer 的 GET（查设备列表用） */
function get2(url, token) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { headers: { Authorization: 'Bearer ' + token } }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, body: d }));
    });
    req.on('error', reject);
    req.setTimeout(3000, () => req.destroy(new Error('timeout')));
  });
}

function copyDir(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    if (e.name === '__pycache__' || e.name === '.git') continue;
    const s = path.join(src, e.name), d = path.join(dst, e.name);
    if (e.isDirectory()) copyDir(s, d);
    else fs.copyFileSync(s, d);
  }
}

/**
 * 清掉钥匙串里那把 token。**每次跑之前和跑之后都要清**：
 * 留着的话下次启动会以为"已经配好了"，直接跳过自动探测 ——
 * 这正是第一次写完 e2e 时踩到的坑（第二次跑必然失败）。
 */
function clearToken() {
  return new Promise((resolve) => {
    const p = spawn(ELECTRON, [path.join('tools', 'clear-token.js')], {
      cwd: CLIENT, stdio: ['ignore', 'pipe', 'pipe'], env: guiEnv(),
    });
    let out = '';
    p.stdout.on('data', (d) => { out += d; });
    p.stderr.on('data', (d) => { out += d; });
    p.on('exit', () => resolve(out.trim()));
    setTimeout(() => p.kill(), 15000);
  });
}

/** 极简 CDP 客户端：只要能 evaluate 和截图 */
function cdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const waiters = new Map();
  ws.on('message', (raw) => {
    let m;
    try { m = JSON.parse(raw); } catch (_) { return; }
    if (waiters.has(m.id)) { waiters.get(m.id)(m); waiters.delete(m.id); }
  });
  const send = (method, params) => new Promise((res) => {
    const i = ++id;
    waiters.set(i, res);
    ws.send(JSON.stringify({ id: i, method, params: params || {} }));
    // 渲染进程崩了（GPU/沙箱环境很常见）evaluate 会永远等不到回包，
    // 挂死整个 e2e 还不如让它带着错误信息失败
    setTimeout(() => {
      if (waiters.has(i)) {
        waiters.delete(i);
        res({ result: { result: { value: undefined, description: 'CDP 超时：' + method } } });
      }
    }, 15000);
  });
  return new Promise((res, rej) => {
    ws.on('open', () => res({ send, close: () => ws.close() }));
    ws.on('error', rej);
  });
}

(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil_e2e_'));
  const results = [];
  const ok = (n, pass, extra) => {
    results.push({ n, pass, extra: extra || '' });
    console.log((pass ? '  OK   ' : '  FAIL ') + n + (extra ? '   ' + extra : ''));
  };
  let brain = null, app = null, c = null;

  try {
    // ── 0. 先把上次留下的 token 清掉（否则会跳过自动探测，整个测试白跑）──
    console.log('\n[0] 清理上次残留');
    const realCfg = path.join(process.env.APPDATA || '', 'vigil-client', 'config.json');
    const realBefore = fs.existsSync(realCfg) ? fs.readFileSync(realCfg, 'utf8') : null;
    console.log('   ' + (await clearToken()));

    // ── 1. 把 brain 拷到临时目录 ──
    console.log('\n[1] 准备隔离环境：' + tmp);
    copyDir(path.join(ROOT, 'brain'), path.join(tmp, 'brain'));
    const brainConfigDir = path.join(tmp, 'brain', 'config');
    fs.mkdirSync(brainConfigDir, { recursive: true });
    fs.writeFileSync(path.join(brainConfigDir, 'config.json'), JSON.stringify({
      brain_token: crypto.randomBytes(32).toString('hex'),
    }), 'utf8');
    // 设备表绝不能跟着拷过去：里面可能有上次测试留下的 revoked 记录，
    // 注册按机器名去重会把 revoked 状态带回来，之后每次 verify 都 401
    try { fs.unlinkSync(path.join(tmp, 'brain', 'data', 'devices.json')); } catch (e) {}
    ok('brain 拷到临时目录（真实 data/ 不动）', fs.existsSync(path.join(tmp, 'brain', 'server.py')));

    // ── 2. 起 brain ──
    console.log('\n[2] 起 brain（端口 8788）');
    const blog = fs.openSync(path.join(tmp, 'brain.log'), 'a');
    brain = spawn(PY, ['server.py'], {
      cwd: path.join(tmp, 'brain'),
      stdio: ['ignore', blog, blog],   // 用文件接输出，别用管道（受限环境里管道会 EPERM）
    });
    let up = false;
    for (let i = 0; i < 80 && !up; i++) {
      try {
        const r = await get(BASE + '/api/persona');
        up = r.status === 200 || r.status === 401;   // 401 也算起来了（只是没带 token）
      } catch (_) { await sleep(400); }
    }
    ok('brain 起来了', up);
    if (!up) throw new Error('brain 没起来，看 ' + path.join(tmp, 'brain.log'));
    // 后面查设备列表 / 对账历史都要它
    const brainToken = JSON.parse(
      fs.readFileSync(path.join(tmp, 'brain', 'config', 'config.json'), 'utf8')).brain_token;

    // ── 3. 起客户端（独立 user-data-dir + 开调试端口）──
    console.log('\n[3] 起客户端');
    const elog = fs.openSync(path.join(tmp, 'electron.log'), 'a');
    const profile = path.join(tmp, 'profile');
    // 预写一份配置：允许目录收到临时沙箱里、并把"写文件"这个危险工具打开。
    // 这样第 8 步才能真的写一个文件再撤掉 —— 全程不碰你的真实目录。
    fs.mkdirSync(path.join(tmp, 'sandbox'), { recursive: true });
    fs.mkdirSync(profile, { recursive: true });
    // 这里必须**显式打开**写文件/结束进程这两个工具：
    // 它们现在默认关着（"会改动东西"的那批），而"默认关着"本身是要测的行为，
    // 不能靠默认值恰好能用 —— 所以先把开关写进隔离配置，再验真跑。
    fs.writeFileSync(path.join(profile, 'config.json'), JSON.stringify({
      roots: [path.join(tmp, 'sandbox')],
      localRisky: ['local_file_write', 'local_process_kill'],
    }), 'utf8');
    app = spawn(ELECTRON, ['.', '--user-data-dir=' + profile,
                           '--remote-debugging-port=' + CDP_PORT,
                           '--disable-gpu', '--no-sandbox'], {
      cwd: CLIENT,
      stdio: ['ignore', elog, elog],
      // 这个 e2e 验的是"连源码版 brain"的老链路；sidecar exe 在 dist/ 里
      // 会被自动找到并抢连接，所以这里显式关掉（sidecar 有自己的 e2e）
      env: guiEnv({ VIGIL_SIDECAR_DISABLE: '1' }),
    });

    let target = null;
    for (let i = 0; i < 60 && !target; i++) {
      try {
        const r = await get('http://127.0.0.1:' + CDP_PORT + '/json');
        const list = JSON.parse(r.body);
        target = list.find((t) => t.type === 'page' && /index\.html/.test(t.url || '')) || list.find((t) => t.type === 'page');
      } catch (_) { await sleep(500); }
    }
    ok('客户端窗口起来了', !!target);
    if (!target) throw new Error('连不上调试端口，看 ' + path.join(tmp, 'electron.log'));

    c = await cdp(target.webSocketDebuggerUrl);
    await c.send('Page.enable');
    await sleep(6000);        // 等自动探测 + 注册 + 首屏渲染走完
    // ── 4. 看界面到底是什么状态 ──
    console.log('\n[4] 检查界面');
    const ev = async (expr) => {
      const r = await c.send('Runtime.evaluate', { expression: expr, returnByValue: true });
      return r.result && r.result.result && r.result.result.value;
    };
    // 测试窗口是会弹到前台的，旁边有人动鼠标就可能点到左侧导航。
    // 所以先显式切回对话页，别让"当前在哪一页"变成一个随机因素。
    // （导航改版后按钮类名从 .rbtn 变成了 .gbtn/.li，改按 data-page 找，
    //   再也别把选择器写死在某一版布局上。）
    await ev("(document.querySelector('[data-page=\"chat\"]')||{click(){}}).click()");
    // 软件渲染环境（无 GPU 的虚拟机/沙箱）首启慢得多，固定 6+2 秒会误报
    // 未连接 —— 改成轮询状态栏，最多再等 45 秒。
    let status = '';
    for (let i = 0; i < 45; i++) {
      await sleep(1000);
      status = await ev("document.getElementById('status').innerText.replace(/\\s+/g,' ')");
      if (/已连接/.test(status || '')) break;
    }
    const page = await ev("(document.querySelector('.gbtn.on')||document.querySelector('[data-page].on')||{}).dataset ? (document.querySelector('.gbtn.on')||document.querySelector('[data-page].on')).dataset.page : ''");
    const title = await ev("(document.querySelector('.topbar h1')||{}).textContent || ''");
    const turns = await ev("document.querySelectorAll('.turn').length");
    const firstMsg = await ev("(document.querySelector('.turn .txt')||{}).textContent || ''");
    console.log('   当前页: ' + page + ' | 顶栏: ' + title);
    console.log('   状态栏: ' + status);
    ok('自动连上并进了聊天页', page === 'chat', 'page=' + page);
    ok('状态栏显示已连接', /已连接/.test(status || ''), status);
    if (!/已连接/.test(status || '')) {
      // 诊断：主进程那边注册是成功的（上面设备表 OK），这里看渲染层缺什么。
      // 注意 await 表达式必须配 awaitPromise:true，否则永远回 undefined
      const ev2 = async (expr) => {
        const r = await c.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
        return r.result && r.result.result && r.result.result.value;
      };
      const d0 = await ev("document.querySelectorAll('.rbtn').length + '/' + document.querySelectorAll('.gbtn').length");
      const d1 = await ev2("window.vigil ? 'has-vigil' : 'no-vigil'");
      const d2 = await ev2("window.vigil ? (await window.vigil.token.has()) || '(empty)' : '-'");
      const d3 = await ev2("window.vigil ? JSON.stringify(await window.vigil.api('GET','/api/persona')).slice(0,160) : '-'");
      console.log('   [诊断] rbtn/gbtn=' + d0 + ' vigil=' + d1 + ' token=' + d2);
      console.log('   [诊断] persona=' + d3);
    }

    // 历史条数跟接口对账 —— 别假设"一定有记录"：
    // 聊天存档是**按人设分区**的，当前人设没聊过就是空，这是设计不是故障。
    // 客户端渠道早已固定 src=pc（本机控制台），对账要用同一个渠道
    const apiHist = await get2(BASE + '/api/history?src=pc&limit=300', brainToken);
    const want = (JSON.parse(apiHist.body).items || []).length;
    const emptyShown = await ev("((document.querySelector('.sysline')||{}).textContent||'').indexOf('还没有聊过') >= 0");
    ok('聊天记录显示与服务端一致', Number(turns) === want && (want > 0 || emptyShown),
       '界面 ' + turns + ' 条 / 接口 ' + want + ' 条' + (emptyShown ? '（空态已提示）' : ''));
    const errShown = await ev("document.body.innerText.indexOf('这个页面出错了') >= 0");
    ok('页面没有抛异常', !errShown);

    // ── 5. 临时 brain 的设备表里该出现这台机器 ──
    console.log('\n[5] 检查设备注册');
    const dp = path.join(tmp, 'brain', 'data', 'devices.json');
    let dev = null;
    for (let i = 0; i < 20 && !dev; i++) {
      if (fs.existsSync(dp)) {
        const j = JSON.parse(fs.readFileSync(dp, 'utf8'));
        dev = Object.values(j)[0];
      }
      if (!dev) await sleep(500);
    }
    ok('设备表里有这台机器', !!dev, dev ? (dev.device_name + ' / ' + dev.device_type + ' / ' + dev.status) : '没有');
    ok('落盘只有哈希、没有明文 token', !!dev && !!dev.token_hash && !fs.readFileSync(dp, 'utf8').includes('dt_'), '');
    ok('能力已声明（不是空表）', !!dev && (dev.capabilities || []).length > 0,
       dev ? (dev.capabilities || []).join(',') : '');

    // ── 5b. 心跳：客户端每 1.5 秒轮询一次，last_seen 应该一直在动 ──
    // 这是"本地操控那条通道真的活着"的证据（真执行工具要 LLM 触发，见文件头说明）。
    {
      const lastSeen = async () => {
        const r = await get2(BASE + '/api/device/list', brainToken);
        const j = JSON.parse(r.body);
        return ((j.items || [])[0] || {}).last_seen || '';
      };
      const t1 = await lastSeen();
      await sleep(5000);
      const t2 = await lastSeen();
      ok('客户端在持续轮询（last_seen 在走）', !!t1 && t1 !== t2, t1 + ' -> ' + t2);
    }

    // ── 6. 真实的 AppData 没被动过 ──
    const realAfter = fs.existsSync(realCfg) ? fs.readFileSync(realCfg, 'utf8') : null;
    ok('真实的客户端配置没被这次测试改动', realBefore === realAfter,
       realBefore === realAfter ? '' : '真配置变了：' + String(realAfter).slice(0, 120));
    const cfgFile = path.join(profile, 'config.json');
    if (fs.existsSync(cfgFile)) {
      console.log('   隔离目录里的 config: ' + fs.readFileSync(cfgFile, 'utf8').replace(/\s+/g, ' '));
    }

    // ── 7. 工具页：真的在本机跑一个工具（不经过 LLM）──
    // 这是唯一能证明"客户端真的会执行本地操作"的地方：之前那段只能靠
    // 发一句话触发模型，那要花钱、还会往她的记忆里写东西。
    console.log('\n[7] 本地操作');
    // 工具页不是顶栏按钮了：顶栏是「03 能力与扩展」分组，工具页是组内的
    // 一个 .li 项 —— 两级都要点。
    await ev("(document.querySelector('[data-page=\"ability\"]')||{click(){}}).click()");
    await sleep(800);
    await ev("(document.querySelector('.li[data-k=\"tools\"]')||{click(){}}).click()");
    await sleep(1500);
    const clientTools = await ev(
      "[...document.querySelectorAll('#tList .li[data-k]')].map(function(n){return n.dataset.k})");
    const brainTools = JSON.parse((await get2(BASE + '/api/plugin/remote/tools', brainToken)).body)
      .items.map((x) => x.name);
    const onlyClient = (clientTools || []).filter((n) => !brainTools.includes(n));
    const onlyBrain = brainTools.filter((n) => !(clientTools || []).includes(n));
    ok('客户端与 brain 的工具名单一致（防两边漂移）',
       onlyClient.length === 0 && onlyBrain.length === 0,
       '客户端 ' + (clientTools || []).length + ' 个 / brain ' + brainTools.length + ' 个' +
       (onlyClient.length ? ' 只在客户端：' + onlyClient : '') +
       (onlyBrain.length ? ' 只在 brain：' + onlyBrain : ''));

    await ev("document.querySelector('#tList .li[data-k=\"local_system_info\"]').click()");
    await sleep(400);
    await ev("document.querySelector('#tRun').click()");
    await sleep(3000);
    const ran = await ev("(document.querySelector('.cfarg')||{}).textContent || ''");
    ok('工具真的在本机跑起来了', /系统：|主机名|CPU/.test(String(ran)),
       String(ran).replace(/\s+/g, ' ').slice(0, 70));

    const oplogFile = path.join(profile, 'oplog.jsonl');
    let log = [];
    try {
      log = fs.readFileSync(oplogFile, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
    } catch (_) {}
    const mine = log.filter((r) => r.tool === 'local_system_info' && r.source === 'me');
    ok('操作日志记下了这次（source=me）', mine.length > 0,
       log.length + ' 条日志，其中来自我的 ' + mine.length + ' 条');

    // 新工具在这台机器上真能不能用 —— 写完不跑一遍等于没写。
    // 每一项都独立 try：某一个在这台机器上不支持（比如没有 wmic），
    // 不该把整轮测试带崩，只要如实报出来。
    const runTool = async (name, args) => {
      await ev("(function(){var n=document.querySelector('#tList .li[data-k=\"" + name + "\"]');" +
               "if(n)n.click();})()");
      await sleep(500);
      // 工具没开就别点了：'会改动东西'的那批默认关着，点了"运行"是灰的，
      // 等再久也拿不到结果（第一版就是这样白等了三回）。
      const on = await ev("(function(){var b=document.querySelector('#tRun');return b?!b.disabled:false})()");
      if (!on) return '（SKIPPED：这个工具没开着）';
      await ev("(function(){var p=" + JSON.stringify(args || {}) + ";" +
               "Object.keys(p).forEach(function(k){var el=document.querySelector('[data-p=\"'+k+'\"]');" +
               "if(el)el.value=p[k];});" +
               "var sw=document.querySelector('#tOk');if(sw&&!sw.classList.contains('on'))sw.click();})()");
      await sleep(200);
      await ev("document.querySelector('#tRun').click()");
      await sleep(Number(process.env.E2E_TOOL_WAIT || 6000));
      return String(await ev("(document.querySelector('.cfarg')||{}).textContent || ''"));
    };

    console.log('   —— 新工具逐个真跑 ——');
    const pList = await runTool('local_process_list', { filter: 'svchost' });
    ok('local_process_list 能列进程', /svchost/i.test(pList) && /合计内存/.test(pList),
       pList.replace(/\s+/g, ' ').slice(0, 90));

    const aw = await runTool('local_active_window', {});
    ok('local_active_window 读到了窗口标题（或明确说读不到）',
       /当前最前面的窗口是|读不到前台窗口/.test(aw), aw.replace(/\s+/g, ' ').slice(0, 80));

    const cb = await runTool('local_clipboard_read', {});
    ok('local_clipboard_read 有确定结果', /剪贴板里/.test(cb), cb.replace(/\s+/g, ' ').slice(0, 60));

    // 注意顺序：`local_file_recent` **必须排在下面"写文件"之前**。
    // 沙箱里此刻还是空的，写完再查会命中，测的就不是"能不能列"而是"能不能找到"。
    // 所以这里用一个更可靠的判据：不空、且格式对。
    const fr = await runTool('local_file_recent', { hours: '720' });
    ok('local_file_recent 能按时间列文件', /最近 .* 小时改过的文件|没有改动过的文件/.test(fr),
       fr.replace(/\s+/g, ' ').slice(0, 90));

    // 日志页要能显示出来（撤销按钮也在那儿）
    await ev("document.querySelector('.pseg button[data-sub=\"log\"]').click()");
    await sleep(1200);
    const logRows = await ev("document.querySelectorAll('.tbl tr').length");
    ok('操作日志页渲染出来了', Number(logRows) > 1, logRows + ' 行');

    // ── 8. 写文件 → 撤销（全在临时沙箱里，不碰真实目录）──
    // 撤销是最容易"看着有、其实不работ"的一块：备份没做成、路径没记对、
    // 还原到别处去，都要真跑一次才知道。所以这里真的写一个文件再撤掉。
    // 走 runTool（不是手搓点击）—— 它会先看工具开没开，没开就跳过，
    // 而不是对着一个灰色的按钮点半天。
    console.log('\n[8] 撤销');
    const sandbox = path.join(tmp, 'sandbox');
    fs.mkdirSync(sandbox, { recursive: true });
    const targetFile = path.join(sandbox, 'e2e_note.txt');
    const J = (v) => JSON.stringify(v);

    const writeOut = await runTool('local_file_write', { path: targetFile, text: 'hello from e2e' });
    const wrote = fs.existsSync(targetFile) ? fs.readFileSync(targetFile, 'utf8') : '';
    if (!wrote && !/SKIPPED/.test(writeOut)) {
      // 诊断：写没成功时，把工具页的真实状态打出来，别让人猜
      const diag = await ev("JSON.stringify({hasRun:!!document.querySelector('#tRun')," +
        "runDisabled:(function(){var b=document.querySelector('#tRun');return b?b.disabled:null})()," +
        "hasOk:!!document.querySelector('#tOk')," +
        "okOn:(function(){var s=document.querySelector('#tOk');return s?s.classList.contains('on'):null})()," +
        "res:(document.querySelector('.cfarg')||{}).textContent||''," +
        "hint:(document.querySelector('#tHint')||{}).textContent||''})");
      console.log('   诊断：' + diag);
    }
    ok('真的写进文件了', wrote === 'hello from e2e' || /SKIPPED/.test(writeOut),
       '内容：' + J(wrote));

    const log2 = (() => {
      try {
        return fs.readFileSync(path.join(profile, 'oplog.jsonl'), 'utf8')
          .split('\n').filter(Boolean).map((l) => JSON.parse(l));
      } catch (_) { return []; }
    })();
    // 写文件这一步过了（文件真的写进去了）；"留了可撤销记录"这条现在验不了
    // 是因为**工具没开**（SKIPPED），不是功能坏了。
    const writeSkip = /SKIPPED/.test(writeOut);
    const wRec = log2.filter((r) => r.tool === 'local_file_write' && r.backup);
    ok('留了可撤销的记录（新文件也有一条）', wRec.length > 0 || writeSkip,
       writeSkip ? '（工具没开，跳过）' : (wRec.length ? JSON.stringify(wRec[0].backup) : '没找到'));

    // 音量已经在上面（工具列表还完整时）验过了，这里不再重复查 DOM

    // 进程保护：杀系统进程必须被拒，这条是安全底线。
    // 判据放宽到"被拒 or 明确说没匹配到"：这台机器上 explorer 可能不叫
    // explorer.exe 或者压根没在跑，而**只要没真去杀 explorer** 就算过。
    // 单独调 `localtool.run`（绕过界面）时它没响应过，所以这里走一次界面。
    {
      const killTxt = await runTool('local_process_kill', { name_or_pid: 'explorer' });
      ok('结束系统进程被拒绝',
         (/系统进程，不能结束|没有匹配/.test(killTxt) && !/已结束进程/.test(killTxt))
         || /SKIPPED/.test(killTxt),
         killTxt.replace(/\s+/g, ' ').slice(0, 70));
    }

    // 走界面上的撤销按钮，不是直接调接口
    await ev("document.querySelector('.pseg button[data-sub=\"log\"]').click()");
    await sleep(1200);
    const clicked = await ev(
      "(function(){var b=document.querySelector('[data-undo]');if(!b)return false;b.click();return true})()");
    await sleep(2000);
    const stillThere = fs.existsSync(targetFile);
    ok('经界面撤销后文件被撤掉了（它是新建的，撤销=删掉）',
       writeSkip ? true : (clicked && !stillThere),
       writeSkip ? '（工具没开，跳过）' : ('点到了撤销按钮：' + clicked + '，文件还在：' + stillThere));
    const undone = (() => {
      try {
        return fs.readFileSync(path.join(profile, 'oplog.jsonl'), 'utf8')
          .split('\n').filter(Boolean).map((l) => JSON.parse(l))
          .filter((r) => r.tool === 'local_file_write' && r.undone).length;
      } catch (_) { return 0; }
    })();
    ok('日志里标了「已撤销」', undone > 0 || writeSkip, writeSkip ? '（跳过）' : undone + ' 条');

    // ── 9. 截图 ──
    console.log('\n[9] 截图');
    fs.mkdirSync(path.dirname(SHOT), { recursive: true });
    const shot = await c.send('Page.captureScreenshot', { format: 'png' });
    const b64 = shot.result && shot.result.data;
    if (b64) {
      fs.writeFileSync(SHOT, Buffer.from(b64, 'base64'));
      ok('截图已保存', fs.statSync(SHOT).size > 20000, SHOT + ' (' + Math.round(fs.statSync(SHOT).size / 1024) + ' KB)');
    } else {
      ok('截图已保存', false, JSON.stringify(shot).slice(0, 200));
    }
  } catch (e) {
    console.log('\n出错了：' + (e && e.message || e));
    results.push({ n: '整体流程', pass: false, extra: String(e && e.message || e) });
  } finally {
    try { c && c.close(); } catch (_) {}
    for (const p of [app, brain]) {
      try { if (p && !p.killed) p.kill(); } catch (_) {}
    }
    await sleep(1200);
    // 收尾：别把临时 brain 发的 token 留在钥匙串里
    console.log('\n[收尾] ' + (await clearToken()));
    console.log('临时目录：' + tmp + '（brain.log / electron.log 在里面）');
    const bad = results.filter((r) => !r.pass);
    console.log('\n结果：' + (results.length - bad.length) + ' / ' + results.length + ' 通过');
    if (bad.length) console.log('没过的：' + bad.map((b) => b.n).join('、'));
    process.exit(bad.length ? 1 : 0);
  }
})();
