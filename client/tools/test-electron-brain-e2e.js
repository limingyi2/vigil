// End-to-end smoke: real Electron main/preload/renderer -> real isolated Brain
// HTTP/SSE route -> CloudProvider -> test-only model fixture -> plugin host.
// All profile, Brain state, logs, and fixture plugin files live in one owned temp dir.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const WebSocket = require('ws');

const client = path.resolve(__dirname, '..');
const repo = path.resolve(client, '..');
const brainDir = path.join(repo, 'brain');
const token = 'e2e-' + require('node:crypto').randomBytes(24).toString('hex');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-p5-joint-'));
const home = path.join(root, 'brain-home');
const profile = path.join(root, 'electron-profile');
const modelRequests = [];
const modelWaiters = [];
let modelServer, brain, electron, cdp;
let cdpSeq = 0;
const cdpPending = new Map();
let passCount = 0;

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
function pass(label) { passCount++; console.log('PASS ' + label); }
function freePort() {
  return new Promise((resolve, reject) => {
    const s = http.createServer();
    s.once('error', reject);
    s.listen(0, '127.0.0.1', () => {
      const port = s.address().port;
      s.close(error => error ? reject(error) : resolve(port));
    });
  });
}
function json(res, status, value) {
  const body = Buffer.from(JSON.stringify(value));
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': body.length });
  res.end(body);
}
function requestStarted(predicate) {
  const found = modelRequests.find(predicate);
  if (found) return Promise.resolve(found);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Timed out waiting for local model request')), 10000);
    modelWaiters.push({ predicate, resolve: value => { clearTimeout(timer); resolve(value); } });
  });
}

async function startModel() {
  modelServer = http.createServer(async (req, res) => {
    if (req.method !== 'POST' || req.url !== '/v1/chat/completions') return json(res, 404, {});
    let raw = '';
    for await (const chunk of req) raw += chunk;
    let payload;
    try { payload = JSON.parse(raw); } catch (_) { return json(res, 400, { error: 'bad json' }); }
    const messages = Array.isArray(payload.messages) ? payload.messages : [];
    const lastUser = [...messages].reverse().find(m => m && m.role === 'user');
    const userText = String(lastUser && lastUser.content || '');
    const call = { payload, messages, userText, toolNames: (payload.tools || []).map(t => t.function && t.function.name) };
    modelRequests.push(call);
    for (let i = modelWaiters.length - 1; i >= 0; i--) {
      if (modelWaiters[i].predicate(call)) modelWaiters.splice(i, 1)[0].resolve(call);
    }

    if (payload.stream) {
      res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'close' });
      if (userText === '晚安') {
        res.write('data: ' + JSON.stringify({ choices: [{ delta: { content: '断连前片段' } }] }) + '\n\n');
        await new Promise(resolve => res.once('close', resolve));
        return;
      }
      const parts = ['E2E_STREAM_PART_A', 'E2E_STREAM_PART_B'];
      for (const part of parts) {
        res.write('data: ' + JSON.stringify({ choices: [{ delta: { content: part } }] }) + '\n\n');
        await wait(60);
      }
      res.write('data: ' + JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' }] }) + '\n\n');
      res.end('data: [DONE]\n\n');
      return;
    }

    const toolMessage = [...messages].reverse().find(m => m && m.role === 'tool');
    if (toolMessage) {
      call.toolResult = String(toolMessage.content || '');
      if (call.toolResult === 'VIGIL_PLUGIN_CALL_OK') {
        return json(res, 200, { choices: [{ message: { role: 'assistant', content: 'VIGIL_PLUGIN_CALL_OK' } }] });
      }
      if (call.toolResult === '查询失败，照实说查不到，别编') {
        return json(res, 200, { choices: [{ message: { role: 'assistant', content: '插件执行失败，未返回成功标记。' } }] });
      }
      return json(res, 500, { error: { message: 'unexpected tool result' } });
    }

    if (!call.toolNames.includes('smoke_ping')) {
      return json(res, 500, { error: { message: 'smoke_ping schema missing' } });
    }
    const mode = userText.includes('E2E_TOOL_FAIL') ? 'fail' : 'ok';
    return json(res, 200, { choices: [{ message: {
      role: 'assistant', content: null,
      tool_calls: [{ id: 'e2e-smoke-' + modelRequests.length, type: 'function',
        function: { name: 'smoke_ping', arguments: JSON.stringify({ mode }) } }],
    } }] });
  });
  await new Promise((resolve, reject) => {
    modelServer.once('error', reject);
    modelServer.listen(0, '127.0.0.1', resolve);
  });
  return 'http://127.0.0.1:' + modelServer.address().port + '/v1';
}

function writeFixtures(modelBase, brainPort) {
  fs.mkdirSync(path.join(home, 'config'), { recursive: true });
  fs.mkdirSync(path.join(home, 'plugins'), { recursive: true });
  fs.mkdirSync(path.join(profile), { recursive: true });
  const pluginTrace = path.join(root, 'plugin-executions.jsonl');
  const brainConfig = {
    api_base: modelBase, api_key: 'fixture-only', model: 'local-fake-provider',
    provider: 'custom', model_mode: 'cloud', chat_mode: 'api', brain_token: token,
    agent_execution: { enabled: false }, features: { tools: true },
    persona_file: 'personas/default.json',
  };
  fs.writeFileSync(path.join(home, 'config', 'config.json'), JSON.stringify(brainConfig), 'utf8');
  fs.writeFileSync(path.join(home, 'plugins', 'vigil_smoke.py'), [
    'import json, os',
    'from pathlib import Path',
    'NAME = "vigil_smoke"',
    'DESC = "isolated Electron Brain integration fixture"',
    'def smoke_ping(mode):',
    '    with open(os.environ["VIGIL_E2E_PLUGIN_TRACE"], "a", encoding="utf-8") as f:',
    '        f.write(json.dumps({"tool":"smoke_ping","mode":mode}) + "\\n")',
    '    if mode == "fail": raise RuntimeError("fixture plugin execution failure")',
    '    return "VIGIL_PLUGIN_CALL_OK"',
    'TOOLS = {"smoke_ping": smoke_ping}',
    'TOOL_SPECS = {"smoke_ping": {"desc":"E2E plugin call", "params":[{"name":"mode","type":"string","desc":"test mode"}], "arg_param":"mode"}}',
    '',
  ].join('\n'), 'utf8');
  const clientConfig = {
    wizard_done: true, connection_mode: 'remote', remote_base: 'http://127.0.0.1:' + brainPort,
    base: 'http://127.0.0.1:' + brainPort, src: 'pc', token_kind: 'brain',
    device_id: '', roots: [], theme: 'light',
  };
  fs.writeFileSync(path.join(profile, 'config.json'), JSON.stringify(clientConfig), 'utf8');
  fs.writeFileSync(path.join(profile, 'token.json'), JSON.stringify({ token }), 'utf8');
  return pluginTrace;
}

async function startBrain(modelBase) {
  const port = await freePort();
  const pluginTrace = writeFixtures(modelBase, port);
  const python = process.env.VIGIL_TEST_PYTHON || path.join(repo, 'venv', 'Scripts', 'python.exe');
  if (!fs.existsSync(python)) throw new Error('Python interpreter not found: ' + python);
  const env = { ...process.env, VIGIL_HOME: home, VIGIL_PORT: String(port),
    VIGIL_E2E_PLUGIN_TRACE: pluginTrace, PYTHONDONTWRITEBYTECODE: '1', PYTHONUNBUFFERED: '1' };
  const logFile = fs.openSync(path.join(root, 'brain.log'), 'w');
  brain = spawn(python, ['server.py'], { cwd: brainDir, env, stdio: ['ignore', logFile, logFile], windowsHide: true });
  brain.once('error', error => { throw error; });
  const base = 'http://127.0.0.1:' + port;
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    if (brain.exitCode != null) throw new Error('Brain exited early; log is at ' + path.join(root, 'brain.log'));
    try {
      const r = await fetch(base + '/api/runtime', { headers: { Authorization: 'Bearer ' + token } });
      if (r.ok) {
        const runtime = await r.json();
        assert.equal(runtime.protocol, 1);
        return { base, pluginTrace };
      }
    } catch (_) {}
    await wait(250);
  }
  throw new Error('Brain did not become ready; log is at ' + path.join(root, 'brain.log'));
}

function cdpSend(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++cdpSeq;
    const timer = setTimeout(() => { cdpPending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 10000);
    cdpPending.set(id, { resolve, reject, timer });
    cdp.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await cdpSend('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error('Renderer evaluation failed: ' + (result.exceptionDetails.text || expression));
  return result.result && result.result.value;
}
async function connectRenderer(debugPort) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    try {
      const pages = await (await fetch('http://127.0.0.1:' + debugPort + '/json/list')).json();
      const page = pages.find(p => p.type === 'page' && String(p.url || '').includes('client/src/index.html'));
      if (page) {
        cdp = new WebSocket(page.webSocketDebuggerUrl, { origin: 'http://localhost' });
        await new Promise((resolve, reject) => { cdp.once('open', resolve); cdp.once('error', reject); });
        cdp.on('message', data => {
          let msg; try { msg = JSON.parse(String(data)); } catch (_) { return; }
          if (msg.id && cdpPending.has(msg.id)) {
            const item = cdpPending.get(msg.id); cdpPending.delete(msg.id); clearTimeout(item.timer);
            msg.error ? item.reject(new Error(msg.error.message)) : item.resolve(msg.result || {});
          }
        });
        await cdpSend('Runtime.enable');
        return;
      }
    } catch (_) {}
    await wait(150);
  }
  throw new Error('Could not attach to actual Electron renderer');
}
async function startElectron(brainBase) {
  const debugPort = await freePort();
  const electronExe = require(path.join(client, 'node_modules', 'electron'));
  const env = { ...process.env, VIGIL_USER_DATA: profile, VIGIL_TEST_MODE: '1',
    ELECTRON_ENABLE_LOGGING: '1', ELECTRON_DISABLE_SECURITY_WARNINGS: '1' };
  const logFile = fs.openSync(path.join(root, 'electron.log'), 'w');
  electron = spawn(electronExe, ['--remote-debugging-port=' + debugPort, '--remote-allow-origins=*', '--disable-gpu', client], {
    cwd: client, env, stdio: ['ignore', logFile, logFile], windowsHide: true,
  });
  const ready = connectRenderer(debugPort);
  await ready;
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    if (await evaluate("Boolean(document.querySelector('#ta') && document.querySelector('#sendBtn'))")) break;
    await wait(100);
  }
  assert.equal(await evaluate("Boolean(document.querySelector('#ta') && document.querySelector('#sendBtn'))"), true,
    'actual renderer chat view should be ready');
  await evaluate("window.__vigilE2EEvents=[]; window.vigil.onStreamEvent(e=>window.__vigilE2EEvents.push(e)); true");
  return { debugPort, brainBase };
}
async function submit(text) {
  await evaluate(`(()=>{const t=document.querySelector('#ta'); t.value=${JSON.stringify(text)}; t.dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('#sendBtn').click(); return true})()`);
}
async function waitFor(predicate, label, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await evaluate(predicate);
    if (value) return value;
    await wait(75);
  }
  throw new Error('Timed out waiting for ' + label + '; renderer=' + String(await evaluate("document.querySelector('#msgs')?.innerText || ''")));
}
async function assertToolCall(traceFile, mode) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (fs.existsSync(traceFile)) {
      const rows = fs.readFileSync(traceFile, 'utf8').trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
      if (rows.some(row => row.tool === 'smoke_ping' && row.mode === mode)) return rows;
    }
    await wait(50);
  }
  throw new Error('Plugin host did not write expected execution trace: ' + mode);
}
async function stopChild(child, force = false) {
  if (!child) return;
  if (process.platform === 'win32') {
    await new Promise(resolve => {
      const args = ['/PID', String(child.pid), '/T']; if (force) args.push('/F');
      const killer = spawn('taskkill', args, { windowsHide: true, stdio: 'ignore' });
      killer.once('exit', resolve); killer.once('error', resolve);
    });
  } else {
    if (child.exitCode != null) return;
    child.kill(force ? 'SIGKILL' : 'SIGTERM');
  }
  await Promise.race([new Promise(resolve => child.once('exit', resolve)), wait(4000)]);
  if (child.exitCode == null && !force) return stopChild(child, true);
}
async function safeCleanup() {
  const resolved = path.resolve(root), temp = path.resolve(os.tmpdir()) + path.sep;
  if (!resolved.startsWith(temp) || !path.basename(resolved).startsWith('vigil-p5-joint-'))
    throw new Error('Refusing to remove path outside this test-owned temporary directory');
  for (let attempt = 0; attempt < 10; attempt++) {
    try {
      fs.rmSync(resolved, { recursive: true, force: true });
      return;
    } catch (error) {
      if (!['EBUSY', 'ENOTEMPTY', 'EPERM', 'EACCES'].includes(error && error.code) || attempt === 9) throw error;
      await wait(100);
    }
  }
}

async function run() {
  const modelBase = await startModel();
  const { base: brainBase, pluginTrace } = await startBrain(modelBase);
  await startElectron(brainBase);

  await submit('E2E_TOOL_SUCCESS 请调用 smoke_ping');
  await waitFor("[...document.querySelectorAll('#msgs .turn:not(.me) .txt')].some(x=>x.innerText.includes('VIGIL_PLUGIN_CALL_OK'))", 'successful plugin reply');
  let calls = modelRequests.filter(x => x.toolNames.includes('smoke_ping'));
  assert.ok(calls.length >= 2, 'model request and tool-result follow-up must both reach Fake Provider');
  assert.ok(calls.some(x => x.toolNames.includes('smoke_ping')), 'Brain must send smoke_ping schema to the model');
  assert.ok(calls.some(x => x.messages.some(m => m.role === 'tool' && m.content === 'VIGIL_PLUGIN_CALL_OK')),
    'actual plugin result must be returned to the model');
  assert.ok((await assertToolCall(pluginTrace, 'ok')).some(row => row.mode === 'ok'), 'plugin host must execute the fixture tool');
  let events = await evaluate('window.__vigilE2EEvents');
  assert.equal(events.filter(e => e.type === 'done').length, 1, 'one terminal done event must reach renderer');
  pass('real Electron -> Brain tool schema -> plugin host -> tool result -> renderer');

  await submit('你好');
  await waitFor("[...document.querySelectorAll('#msgs .turn:not(.me) .txt')].some(x=>x.innerText.includes('E2E_STREAM_PART_AE2E_STREAM_PART_B'))", 'multi-event stream final text');
  await waitFor("window.__vigilE2EEvents.filter(e=>e.type==='done').length===2", 'stream terminal event');
  events = await evaluate('window.__vigilE2EEvents');
  assert.ok(events.filter(e => e.type === 'text' && String(e.text || '').startsWith('E2E_STREAM_PART_')).length >= 2,
    'renderer must receive both SSE text events');
  assert.equal(events.filter(e => e.type === 'done').length, 2,
    'each request must have exactly one done event; received ' + JSON.stringify(events));
  const rendered = await evaluate("[...document.querySelectorAll('#msgs .turn:not(.me) .txt')].map(x=>x.innerText).filter(x=>x.includes('E2E_STREAM_PART_AE2E_STREAM_PART_B')).length");
  assert.equal(rendered, 1, 'multi-event stream must render once without duplicate final content');
  pass('multiple SSE text events and one terminal event are rendered exactly once');

  const failureStart = modelRequests.length;
  await submit('E2E_TOOL_FAIL 请调用 smoke_ping');
  await waitFor("[...document.querySelectorAll('#msgs .turn:not(.me) .txt')].some(x=>x.innerText.includes('插件执行失败，未返回成功标记'))", 'tool failure reported accurately');
  calls = modelRequests.slice(failureStart);
  assert.ok(calls.some(x => x.messages.some(m => m.role === 'tool' && m.content === '查询失败，照实说查不到，别编')),
    'tool host failure must be sent back as the real failure result');
  assert.ok((await assertToolCall(pluginTrace, 'fail')).some(row => row.mode === 'fail'), 'failing fixture tool must execute in host');
  await waitFor("document.querySelector('#sendBtn').getAttribute('aria-busy')==='false'", 'tool failure releases composer');
  const failureState = await evaluate(`(()=>{const rows=Object.keys(localStorage).filter(k=>k.startsWith('vigil.display.v1:')).flatMap(k=>JSON.parse(localStorage.getItem(k)||'[]'));const r=rows.find(x=>x.input==='E2E_TOOL_FAIL 请调用 smoke_ping');return {status:r?.status,busy:document.querySelector('#sendBtn').getAttribute('aria-busy')}})()`);
  assert.equal(failureState.status, 'succeeded', 'the truthful error explanation is a completed assistant turn');
  assert.equal(failureState.busy, 'false', 'tool error response must release the running state');
  pass('plugin execution failure reaches model and renderer without success marker');

  await submit('晚安');
  await requestStarted(x => x.payload.stream === true && x.userText === '晚安');
  await wait(250);
  await stopChild(brain, true); brain = null;
  await waitFor("document.querySelector('#msgs').innerText.includes('连接中断') || document.querySelector('#msgs').innerText.includes('执行中断') || document.querySelector('#msgs').innerText.includes('失败')", 'Brain disconnect status');
  const state = await evaluate(`(()=>{const rows=Object.keys(localStorage).filter(k=>k.startsWith('vigil.display.v1:')).flatMap(k=>JSON.parse(localStorage.getItem(k)||'[]'));const r=rows.find(x=>x.input==='晚安');return {status:r?.status,disabled:document.querySelector('#sendBtn').disabled,text:document.querySelector('#msgs').innerText.slice(-240)}})()`);
  assert.equal(state.status, 'interrupted', 'Brain disconnect must persist interrupted status');
  assert.equal(await evaluate("document.querySelector('#sendBtn').getAttribute('aria-busy')"), 'false',
    'interrupted chat must not remain permanently running');
  pass('Brain process disconnect is surfaced as interrupted and unlocks chat');
  console.log('PASS integrated smoke total: ' + passCount);
}

run().catch(error => {
  console.error(error && error.stack || error);
  try { if (fs.existsSync(path.join(root, 'brain.log'))) console.error('Brain log retained until cleanup: ' + path.join(root, 'brain.log')); } catch (_) {}
  process.exitCode = 1;
}).finally(async () => {
  if (cdp && cdp.readyState === WebSocket.OPEN) {
    try { await cdpSend('Browser.close'); } catch (_) {}
  }
  try { if (cdp && cdp.readyState === WebSocket.OPEN) cdp.close(); } catch (_) {}
  await stopChild(electron);
  await stopChild(brain, true);
  if (modelServer) {
    if (typeof modelServer.closeAllConnections === 'function') modelServer.closeAllConnections();
    await new Promise(resolve => modelServer.close(resolve));
  }
  for (const item of cdpPending.values()) { clearTimeout(item.timer); item.reject(new Error('test ended')); }
  cdpPending.clear();
  try { await safeCleanup(); } catch (e) { console.error('Temp cleanup failed: ' + e.message); process.exitCode = 1; }
});
