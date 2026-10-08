// -*- coding: utf-8 -*-
/** 全栈 CDP 诊断：临时 brain + 真客户端，逐条打印 CDP 原始响应（含异常栈）。
 *  用途：查渲染层 IPC 到底挂在哪。跑完自己清理。
 */
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const WebSocket = require('ws');

const ROOT = path.join(__dirname, '..', '..');
const CLIENT = path.join(__dirname, '..');
const PY = path.join(ROOT, 'venv', 'Scripts', 'python.exe');
const ELECTRON = path.join(CLIENT, 'node_modules', 'electron', 'dist', 'electron.exe');
const CDP_PORT = 9445;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const get = (url) => new Promise((res, rej) => http.get(url, (r) => {
  let d = ''; r.on('data', (c) => d += c); r.on('end', () => res(d));
}).on('error', rej));

function copyDir(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    if (e.name === '__pycache__' || e.name === '.git') continue;
    const s = path.join(src, e.name), d = path.join(dst, e.name);
    if (e.isDirectory()) copyDir(s, d);
    else fs.copyFileSync(s, d);
  }
}

(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil_diag_'));
  copyDir(path.join(ROOT, 'brain'), path.join(tmp, 'brain'));
  try { fs.unlinkSync(path.join(tmp, 'brain', 'data', 'devices.json')); } catch (e) {}

  const blog = fs.openSync(path.join(tmp, 'brain.log'), 'a');
  const brain = spawn(PY, ['server.py'], { cwd: path.join(tmp, 'brain'), stdio: ['ignore', blog, blog] });
  for (let i = 0; i < 80; i++) {
    try { await get('http://127.0.0.1:8788/api/persona'); break; } catch (_) { await sleep(400); }
  }
  console.log('[diag] brain up');

  const profile = path.join(tmp, 'profile');
  fs.mkdirSync(profile, { recursive: true });
  const elog = fs.openSync(path.join(tmp, 'electron.log'), 'a');
  const app = spawn(ELECTRON, ['.', '--user-data-dir=' + profile,
    '--remote-debugging-port=' + CDP_PORT, '--disable-gpu', '--no-sandbox'], {
    cwd: CLIENT,
    env: (() => { const e = { ...process.env }; delete e.ELECTRON_RUN_AS_NODE; return e; })(),
    stdio: ['ignore', elog, elog],
  });
  let target = null;
  for (let i = 0; i < 60 && !target; i++) {
    try {
      const l = JSON.parse(await get('http://127.0.0.1:' + CDP_PORT + '/json'));
      target = l.find((t) => t.type === 'page');
    } catch (_) { await sleep(500); }
  }
  if (!target) { console.log('no target'); process.exit(1); }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.on('open', res); ws.on('error', rej); });
  let id = 0;
  const evRaw = (expr) => new Promise((res) => {
    const i = ++id;
    ws.send(JSON.stringify({ id: i, method: 'Runtime.evaluate',
      params: { expression: expr, returnByValue: true, awaitPromise: true } }));
    const to = setTimeout(() => res({ TIMEOUT: true }), 15000);
    ws.on('message', function h(raw) {
      const m = JSON.parse(raw);
      if (m.id === i) { ws.off('message', h); clearTimeout(to); res(m.result); }
    });
  });
  const P = (s) => JSON.stringify(s).slice(0, 300);

  await sleep(8000);   // 等自动探测走完
  console.log('sync 1+1:', P((await evRaw('1+1')).result));
  console.log('promise:', P(await evRaw('Promise.resolve(42)')));
  console.log('has-token:', P(await evRaw('window.vigil.token.has()')));
  console.log('cfg:', P(await evRaw('window.vigil.cfg.get()')));
  console.log('persona:', P(await evRaw('window.vigil.api("GET","/api/persona")')));
  console.log('conn-state:', P(await evRaw('(function(){return document.getElementById("status").innerText})()')));

  ws.close();
  app.kill();
  brain.kill();
  console.log('[diag] temp: ' + tmp);
  process.exit(0);
})().catch((e) => { console.error('diag error', e); process.exit(1); });
