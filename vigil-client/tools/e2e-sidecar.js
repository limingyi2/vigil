// -*- coding: utf-8 -*-
/**
 * sidecar e2e：不起新 brain，验证客户端把 vigil-brain.exe（PyInstaller 打包件）
 * 当子进程拉起并连上的整条链路。
 *
 * 验证的点：
 *   1. 客户端窗口起来
 *   2. sidecar 状态变 ready，端口从 stdout 就绪行拿到
 *   3. 客户端自动连上 sidecar（base = http://127.0.0.1:<port>）并注册设备
 *   4. 状态栏显示已连接
 *   5. 截图
 *
 * 隔离：客户端用临时 --user-data-dir；sidecar 的家就是 dist/vigil-brain（exe 旁），
 * 它的 config/config.json 是打包验证时布置的那份，聊天不发生就不写数据。
 * 跑法：node tools/e2e-sidecar.js
 */
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const WebSocket = require('ws');

const CLIENT = path.join(__dirname, '..');
const ELECTRON = path.join(CLIENT, 'node_modules', 'electron', 'dist', 'electron.exe');
const CDP_PORT = 9334;
const SHOT = path.join(CLIENT, '_shots', 'e2e-sidecar.png');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const get = (url) => new Promise((resolve, reject) => {
  http.get(url, (res) => { let d = ''; res.on('data', (c) => d += c); res.on('end', () => resolve(d)); })
    .on('error', reject);
});

function guiEnv(extra) {
  const e = { ...process.env, ...(extra || {}) };
  delete e.ELECTRON_RUN_AS_NODE;   // 有些环境全局设了它，electron 会被当纯 Node
  return e;
}

function cdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const waiters = new Map();
  ws.on('message', (raw) => {
    let m; try { m = JSON.parse(raw); } catch (_) { return; }
    if (waiters.has(m.id)) { waiters.get(m.id)(m); waiters.delete(m.id); }
  });
  const send = (method, params) => new Promise((res) => {
    const i = ++id;
    waiters.set(i, res);
    ws.send(JSON.stringify({ id: i, method, params: params || {} }));
    setTimeout(() => {
      if (waiters.has(i)) {
        waiters.delete(i);
        res({ result: { result: { value: undefined, description: 'CDP 超时：' + method } } });
      }
    }, 20000);
  });
  return new Promise((res, rej) => {
    ws.on('open', () => res({ send, close: () => ws.close() }));
    ws.on('error', rej);
  });
}

(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil_sidecar_e2e_'));
  const results = [];
  const ok = (n, pass, extra) => {
    results.push({ n, pass });
    console.log((pass ? '  OK   ' : '  FAIL ') + n + (extra ? '   ' + extra : ''));
  };
  let app = null, c = null;
  try {
    const profile = path.join(tmp, 'profile');
    fs.mkdirSync(profile, { recursive: true });

    console.log('\n[1] 起客户端（不设 VIGIL_SIDECAR_DISABLE，让它自己拉起 exe）');
    const elog = fs.openSync(path.join(tmp, 'electron.log'), 'a');
    app = spawn(ELECTRON, ['.', '--user-data-dir=' + profile,
      '--remote-debugging-port=' + CDP_PORT, '--disable-gpu', '--no-sandbox'], {
      cwd: CLIENT, stdio: ['ignore', elog, elog], env: guiEnv(),
    });
    let target = null;
    for (let i = 0; i < 60 && !target; i++) {
      try {
        const l = JSON.parse(await get('http://127.0.0.1:' + CDP_PORT + '/json'));
        target = l.find((t) => t.type === 'page' && /index\.html/.test(t.url || '')) || l.find((t) => t.type === 'page');
      } catch (_) { await sleep(500); }
    }
    ok('客户端窗口起来了', !!target);
    if (!target) throw new Error('连不上调试端口，看 ' + path.join(tmp, 'electron.log'));

    c = await cdp(target.webSocketDebuggerUrl);
    await c.send('Page.enable');
    const ev = async (expr) => {
      const r = await c.send('Runtime.evaluate', { expression: expr, returnByValue: true });
      return r.result && r.result.result && r.result.result.value;
    };
    const ev2 = async (expr) => {
      const r = await c.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
      return r.result && r.result.result && r.result.result.value;
    };

    console.log('\n[2] 等 sidecar 就绪 + 客户端自动连上（首启要解包，放宽到 90 秒）');
    let sc = null;
    for (let i = 0; i < 90; i++) {
      await sleep(1000);
      sc = await ev2("(window.vigil && window.vigil.sidecar) ? window.vigil.sidecar.status() : null");
      if (sc && sc.status === 'ready' && sc.port) break;
    }
    ok('sidecar 就绪（exe 被拉起、就绪行解析到端口）',
       !!sc && sc.status === 'ready' && !!sc.port,
       sc ? ('status=' + sc.status + ' port=' + sc.port + ' restarts=' + sc.restarts +
             (sc.logTail && sc.logTail.length ? ' | ' + sc.logTail.slice(-1)[0].slice(0, 60) : '')) : 'null');
    if (!sc || sc.status !== 'ready' || !sc.port) throw new Error('sidecar 没就绪');

    // 客户端应连到 sidecar 而不是隧道/云端。渲染层的 state.base 要等
    // loadCfg() 才有值，权威在主进程的 whoami()（device:whoami）
    let who = null;
    for (let i = 0; i < 30; i++) {
      await sleep(1000);
      who = await ev2("window.vigil ? window.vigil.device.whoami() : null");
      const st = await ev("document.getElementById('status').innerText");
      if (who && who.base === sc.base && /已连接/.test(st || '')) break;
    }
    const stTxt = await ev("document.getElementById('status').innerText.replace(/\\s+/g,' ')");
    ok('客户端连的是 sidecar（' + sc.base + '）', !!who && who.base === sc.base, 'base=' + (who ? who.base : who));
    ok('状态栏显示已连接', /已连接/.test(stTxt || ''), stTxt);

    console.log('\n[3] 设备注册应发生在 sidecar 上');
    ok('本机已注册为设备', !!who && !!who.device_id, JSON.stringify(who).slice(0, 120));

    console.log('\n[4] 截图');
    const shot = await c.send('Page.captureScreenshot', { format: 'png' });
    if (shot.result && shot.result.data) {
      fs.mkdirSync(path.dirname(SHOT), { recursive: true });
      fs.writeFileSync(SHOT, Buffer.from(shot.result.data, 'base64'));
      ok('截图已保存', true, SHOT);
    } else ok('截图已保存', false, JSON.stringify(shot).slice(0, 80));

    await ev("document.body.innerText.indexOf('这个页面出错了') >= 0") ;
    const errShown = await ev("document.body.innerText.indexOf('这个页面出错了') >= 0");
    ok('页面没有抛异常', !errShown);
  } catch (e) {
    console.log('出错了：' + e.message);
    results.push({ n: '整体流程', pass: false });
  } finally {
    if (c) c.close();
    if (app) app.kill();
  }
  const pass = results.filter((r) => r.pass).length;
  console.log('\n结果：' + pass + ' / ' + results.length + ' 通过');
  const failed = results.filter((r) => !r.pass).map((r) => r.n);
  if (failed.length) console.log('没过的：' + failed.join('、'));
  console.log('临时目录：' + tmp + '（electron.log 在里面）');
  process.exit(pass === results.length ? 0 : 1);
})();
