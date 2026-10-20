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
const pluginReleasePath = path.join(root, 'release-blocked-plugin');
const captureScreenshots = process.argv.includes('--packaged') || process.argv.includes('--screenshots');
const modelRequests = [];
const modelWaiters = [];
let modelServer, brain, electron, cdp;
let cdpSeq = 0;
const cdpPending = new Map();
let passCount = 0;
let cancelGate = null;
let windowExitGate = null;

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
    if (req.method !== 'POST' || req.url !== '/v1/chat/completions') {
      console.log('FAKE_PROVIDER unexpected=' + req.method + ' ' + req.url);
      return json(res, 404, {});
    }
    let raw = '';
    for await (const chunk of req) raw += chunk;
    let payload;
    try { payload = JSON.parse(raw); } catch (_) { return json(res, 400, { error: 'bad json' }); }
    const messages = Array.isArray(payload.messages) ? payload.messages : [];
    const lastUser = [...messages].reverse().find(m => m && m.role === 'user');
    const userText = String(lastUser && lastUser.content || '');
    const toolMessage = [...messages].reverse().find(m => m && m.role === 'tool');
    const call = { payload, messages, userText, toolNames: (payload.tools || []).map(t => t.function && t.function.name) };
    modelRequests.push(call);
    console.log('FAKE_PROVIDER request=' + modelRequests.length + ' stream=' + Boolean(payload.stream));
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
      if (userText.includes('P1_AGENT_CANCEL_DURING_MODEL')) {
        res.write('data: ' + JSON.stringify({ choices: [{ delta: { content: 'CANCEL_BEFORE_TOOL' } }] }) + '\n\n');
        if (!cancelGate) throw new Error('cancel gate was not armed');
        await cancelGate.promise;
        if (!res.destroyed) {
          res.write('data: ' + JSON.stringify({ choices: [{ delta: { tool_calls: [{ index: 0,
            id: 'p1-cancel-tool', type: 'function', function: { name: 'smoke_ping', arguments: '{"mode":"ok"}' } }] } }] }) + '\n\n');
          res.write('data: ' + JSON.stringify({ choices: [{ delta: {}, finish_reason: 'tool_calls' }] }) + '\n\n');
          res.end('data: [DONE]\n\n');
        }
        return;
      }
      if (userText.includes('P1_AGENT_WINDOW_EXIT')) {
        res.write('data: ' + JSON.stringify({ choices: [{ delta: { content: 'WINDOW_EXIT_IN_PROGRESS' } }] }) + '\n\n');
        if (!windowExitGate) throw new Error('window-exit gate was not armed');
        await windowExitGate.promise;
        if (!res.destroyed) {
          res.write('data: ' + JSON.stringify({ choices: [{ delta: { content: 'WINDOW_EXIT_COMPLETED' } }] }) + '\n\n');
          res.write('data: ' + JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' }] }) + '\n\n');
          res.end('data: [DONE]\n\n');
        }
        return;
      }
      if (userText.includes('P1_AGENT_TIMEOUT_BEFORE_TOOL')) {
        res.write('data: ' + JSON.stringify({ choices: [{ delta: { content: 'TIMEOUT_BEFORE_TOOL' } }] }) + '\n\n');
        await wait(1800);
        if (!res.destroyed) {
          res.write('data: ' + JSON.stringify({ choices: [{ delta: { tool_calls: [{ index: 0,
            id: 'p1-timeout-tool', type: 'function', function: { name: 'smoke_ping', arguments: '{"mode":"ok"}' } }] } }] }) + '\n\n');
          res.write('data: ' + JSON.stringify({ choices: [{ delta: {}, finish_reason: 'tool_calls' }] }) + '\n\n');
          res.end('data: [DONE]\n\n');
        }
        return;
      }
      if (userText.includes('P1_AGENT_EVENT_BURST')) {
        for (let i = 0; i < 300; i++) {
          if (res.destroyed) return;
          res.write('data: ' + JSON.stringify({ choices: [{ delta: { content: 'x' } }] }) + '\n\n');
          await wait(2);
        }
        res.write('data: ' + JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' }] }) + '\n\n');
        res.end('data: [DONE]\n\n');
        return;
      }
      if (userText.includes('P1_AGENT_TOOL_ONCE') || userText.includes('P1_AGENT_UNKNOWN_TOOL') || userText.includes('P1_AGENT_TOOL_CRASH')) {
        if (toolMessage) {
          for (const text of ['AGENT_TOOL_RESULT_RECORDED']) {
            res.write('data: ' + JSON.stringify({ choices: [{ delta: { content: text } }] }) + '\n\n');
          }
          res.write('data: ' + JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' }] }) + '\n\n');
          res.end('data: [DONE]\n\n');
          return;
        }
        const mode = userText.includes('P1_AGENT_TOOL_CRASH') ? 'crash'
          : userText.includes('P1_AGENT_UNKNOWN_TOOL') ? 'fail' : 'ok';
        res.write('data: ' + JSON.stringify({ choices: [{ delta: { tool_calls: [{ index: 0,
          id: userText.includes('P1_AGENT_TOOL_CRASH') ? 'p1-crash-tool'
            : userText.includes('P1_AGENT_UNKNOWN_TOOL') ? 'p1-unknown-tool' : 'p1-once-tool',
          type: 'function', function: { name: 'smoke_ping', arguments: JSON.stringify({ mode }) } }] } }] }) + '\n\n');
        res.write('data: ' + JSON.stringify({ choices: [{ delta: {}, finish_reason: 'tool_calls' }] }) + '\n\n');
        res.end('data: [DONE]\n\n');
        return;
      }
      const creatorOutput = JSON.stringify({files:{'p6_session.py':[
        'NAME = "p6_session"', 'DESC = "isolated session draft"', '',
        'def p6_session_status(arg):', '    return "fixture only"', '',
        'TOOLS = {"p6_session_status": p6_session_status}',
        'TOOL_SPECS = {"p6_session_status": {"desc": "fixture status", "params": [], "arg_param": None}}',
      ].join('\n')},summary:'P6 session scoped draft',risks:'fixture only'});
      const parts = userText.includes('P6_FIXTURE_PLUGIN')
        ? [creatorOutput.slice(0, Math.floor(creatorOutput.length / 2)), creatorOutput.slice(Math.floor(creatorOutput.length / 2))]
        : ['E2E_STREAM_PART_A', 'E2E_STREAM_PART_B'];
      for (const part of parts) {
        res.write('data: ' + JSON.stringify({ choices: [{ delta: { content: part } }] }) + '\n\n');
        await wait(60);
      }
      res.write('data: ' + JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' }] }) + '\n\n');
      res.end('data: [DONE]\n\n');
      return;
    }

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

    if (userText.startsWith('E2E_SEED_HISTORY_')) {
      return json(res, 200, { choices: [{ message: { role: 'assistant', content: 'SEEDED_HISTORY_OK' } }] });
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
  fs.mkdirSync(path.join(home, 'data'), { recursive: true });
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
  const pluginDir = path.join(home, 'plugins', 'vigil_smoke');
  fs.mkdirSync(pluginDir, { recursive: true });
  fs.writeFileSync(path.join(pluginDir, '__init__.py'), [
    'import json, os',
    'from pathlib import Path',
    'import time',
    'NAME = "vigil_smoke"',
    'DESC = "isolated Electron Brain integration fixture"',
    'def smoke_ping(mode):',
    '    with open(os.environ["VIGIL_E2E_PLUGIN_TRACE"], "a", encoding="utf-8") as f:',
    '        f.write(json.dumps({"tool":"smoke_ping","mode":mode}) + "\\n")',
    '    if mode == "fail": raise RuntimeError("fixture plugin execution failure")',
    '    if mode == "crash":',
    '        release = Path(os.environ["VIGIL_E2E_PLUGIN_RELEASE"] )',
    '        for _ in range(1500):',
    '            if release.exists(): break',
    '            time.sleep(0.01)',
    '        else: raise RuntimeError("fixture release wait timed out")',
    '    return "VIGIL_PLUGIN_CALL_OK"',
    'TOOLS = {"smoke_ping": smoke_ping}',
    'TOOL_SPECS = {"smoke_ping": {"desc":"E2E plugin call", "params":[("mode","test mode",True,"string")], "arg_param":"mode"}}',
    '',
  ].join('\n'), 'utf8');
  fs.writeFileSync(path.join(pluginDir, 'frontend.js'), [
    'window.VigilPlugin.registerPage({',
    '  id: "e2e-plugin-page", name: "E2E Plugin", icon: "plug",',
    '  render(ctx) { ctx.panel.innerHTML = "<section id=\\"e2e-plugin-visible\\">Directory plugin frontend loaded</section>"; }',
    '});',
  ].join('\n'), 'utf8');
  fs.writeFileSync(path.join(pluginDir, 'frontend.css'), '#e2e-plugin-visible { color: rgb(1, 2, 3); }', 'utf8');
  // P8.1: the desktop client only talks to its bundled Brain. An unpackaged run
  // may point at an already-running one through VIGIL_TEST_BRAIN (loopback only),
  // which is how this suite exercises the real main/preload/renderer without a
  // 160 MB package.
  const clientConfig = {
    wizard_done: true, src: 'pc', roots: [], theme: 'light',
  };
  fs.writeFileSync(path.join(profile, 'config.json'), JSON.stringify(clientConfig), 'utf8');
  fs.writeFileSync(path.join(profile, 'token.json'), JSON.stringify({ token }), 'utf8');
  return pluginTrace;
}

async function startBrain(modelBase, fixedPort) {
  const port = fixedPort || await freePort();
  const pluginTrace = writeFixtures(modelBase, port);
  const python = process.env.VIGIL_TEST_PYTHON || path.join(repo, 'venv', 'Scripts', 'python.exe');
  if (!fs.existsSync(python)) throw new Error('Python interpreter not found: ' + python);
  const env = { ...process.env, VIGIL_HOME: home, VIGIL_PORT: String(port),
    VIGIL_E2E_PLUGIN_TRACE: pluginTrace, VIGIL_E2E_PLUGIN_RELEASE: pluginReleasePath,
    PYTHONDONTWRITEBYTECODE: '1', PYTHONUNBUFFERED: '1' };
  const logFile = fs.openSync(path.join(root, 'brain.log'), 'w');
  brain = spawn(python, ['server.py'], { cwd: brainDir, env, stdio: ['ignore', logFile, logFile], windowsHide: true });
  fs.closeSync(logFile); // The child owns the inherited handle; Windows cleanup must not retain a parent lock.
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
        return { base, pluginTrace, port };
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
async function screenshot(name) {
  const result = await cdpSend('Page.captureScreenshot', {format:'png', captureBeyondViewport:false});
  const dir = path.join(repo, 'reports', 'p6.1-evidence');
  fs.mkdirSync(dir, {recursive:true});
  fs.writeFileSync(path.join(dir, name + '.png'), Buffer.from(result.data, 'base64'));
}
async function connectRenderer(debugPort) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    try {
      const pages = await (await fetch('http://127.0.0.1:' + debugPort + '/json/list')).json();
      const page = pages.find(p => p.type === 'page' && String(p.url || '').includes('index.html'));
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
        await cdpSend('Page.enable');
        return;
      }
    } catch (_) {}
    await wait(150);
  }
  throw new Error('Could not attach to actual Electron renderer');
}
async function startElectron(brainBase) {
  const debugPort = await freePort();
  const packaged = process.argv.includes('--packaged');
  const electronExe = packaged
    ? path.join(client, 'dist-release', 'win-unpacked', 'Vigil.exe')
    : require(path.join(client, 'node_modules', 'electron'));
  if (packaged && !fs.existsSync(electronExe)) throw new Error('Candidate packaged executable not found: ' + electronExe);
  const env = { ...process.env, VIGIL_USER_DATA: profile, VIGIL_TEST_MODE: '1',
    VIGIL_TEST_BRAIN: brainBase, VIGIL_TEST_TOKEN: token,
    ELECTRON_ENABLE_LOGGING: '1', ELECTRON_DISABLE_SECURITY_WARNINGS: '1' };
  const logFile = fs.openSync(path.join(root, 'electron.log'), 'w');
  // 两个开关都是必须的：传 --disable-gpu 会让这台机器的 GPU 进程反复崩
  // （exit_code=-1073741819 → FATAL），不带 --no-sandbox 则 GPU 子进程被外层
  // 沙箱掐掉、渲染进程跟着崩，CDP 永远连不上。
  const args = ['--remote-debugging-port=' + debugPort, '--remote-allow-origins=*',
    '--no-sandbox', '--disable-features=CalculateNativeWinOcclusion'];
  if (!packaged) args.push(client);
  electron = spawn(electronExe, args, {
    cwd: client, env, stdio: ['ignore', logFile, logFile], windowsHide: true,
  });
  fs.closeSync(logFile); // Release the parent's duplicate handle when the child exits.
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
  throw new Error('Timed out waiting for ' + label + '; renderer=' + String(await evaluate("document.querySelector('#msgs')?.innerText || ''")) +
    '; streamEvents=' + JSON.stringify(await evaluate('window.__vigilE2EEvents || []')) +
    '; fakeProviderRequests=' + modelRequests.length);
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
async function createAgentRun(intent, requestId, budget) {
  const response = await evaluate(`vigil.api('POST','/api/agent/runs',${JSON.stringify({
    request_id: requestId, intent, src: 'pc', ...(budget ? { budget } : {}),
  })})`);
  assert.equal(response.status, 200, 'Electron API bridge must create an Agent run: ' + JSON.stringify(response));
  assert.ok(response.json?.run_id, 'Brain must return a durable Agent run id');
  return response.json;
}
async function agentSnapshot(runId) {
  const response = await evaluate(`vigil.api('GET','/api/agent/runs/${encodeURIComponent(runId)}')`);
  assert.equal(response.status, 200, 'Electron API bridge must read Agent run status');
  return response.json;
}
async function brainAgentSnapshot(brainBase, runId) {
  const response = await fetch(`${brainBase}/api/agent/runs/${encodeURIComponent(runId)}?src=pc`, {
    headers: { Authorization: 'Bearer ' + token },
  });
  assert.equal(response.status, 200, 'Brain must expose durable Agent state without an Electron window');
  return response.json();
}
async function waitBrainAgentRun(brainBase, runId, timeout = 10000) {
  const deadline = Date.now() + timeout;
  let snapshot;
  while (Date.now() < deadline) {
    snapshot = await brainAgentSnapshot(brainBase, runId);
    if (['succeeded', 'failed', 'cancelled', 'interrupted'].includes(snapshot.status)) return snapshot;
    await wait(50);
  }
  throw new Error('Brain Agent run did not reach a terminal state after Electron exit: ' + JSON.stringify(snapshot));
}
async function waitAgentRun(runId, terminal = ['succeeded', 'failed', 'cancelled', 'interrupted'], timeout = 20000) {
  const deadline = Date.now() + timeout;
  let snapshot;
  while (Date.now() < deadline) {
    snapshot = await agentSnapshot(runId);
    if (terminal.includes(snapshot.status)) return snapshot;
    await wait(50);
  }
  throw new Error('Agent run did not reach a terminal state: ' + JSON.stringify(snapshot));
}
function providerTrace(pluginTrace) {
  if (!fs.existsSync(pluginTrace)) return [];
  return fs.readFileSync(pluginTrace, 'utf8').trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
}
async function disconnectAndResumeAgentEvents(brainBase, runId) {
  const controller = new AbortController();
  const response = await fetch(`${brainBase}/api/agent/runs/${encodeURIComponent(runId)}/events?src=pc&after=0`, {
    headers: { Authorization: 'Bearer ' + token }, signal: controller.signal,
  });
  assert.equal(response.status, 200, 'Brain event subscription must open');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let firstEvent = null;
  while (!firstEvent) {
    const { value, done } = await reader.read();
    assert.equal(done, false, 'event subscription ended before its first event');
    buffer += decoder.decode(value, { stream: true });
    const match = buffer.match(/(?:^|\n)data: ([^\n]+)/);
    if (match) firstEvent = JSON.parse(match[1]);
  }
  assert.equal(firstEvent.run_id, runId);
  assert.ok(Number.isInteger(firstEvent.seq) && firstEvent.seq > 0);
  await reader.cancel(); // Simulate a client network cut after the first durable cursor.
  controller.abort();
  const snapshot = await waitAgentRun(runId);
  const resumed = await fetch(`${brainBase}/api/agent/runs/${encodeURIComponent(runId)}/events?src=pc&after=${firstEvent.seq}`, {
    headers: { Authorization: 'Bearer ' + token },
  });
  assert.equal(resumed.status, 200);
  const raw = await resumed.text();
  const events = raw.split(/\r?\n/).filter(line => line.startsWith('data: '))
    .map(line => JSON.parse(line.slice(6)));
  assert.equal(events[0]?.type, 'snapshot', 'a cursor gap must return an authoritative snapshot');
  assert.equal(events[0]?.payload?.run_id, runId);
  assert.equal(events[0]?.payload?.status, snapshot.status);
  assert.equal(events[0]?.payload?.content, snapshot.content);
  assert.ok(snapshot.event_cursor > 256, 'fixture must exceed the bounded persistent event window');
  return { firstCursor: firstEvent.seq, resumedCursor: events[0].seq, snapshot };
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
  // 排查用：VIGIL_E2E_KEEP=1 时不删现场，日志（brain.log / electron.log）留着看
  if (process.env.VIGIL_E2E_KEEP === '1') { console.log('Kept test directory: ' + resolved); return; }
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
  await waitFor("!!document.querySelector('#gnav [data-page=\\\"e2e-plugin-page\\\"]')", 'directory plugin page registration');
  await evaluate("document.querySelector('#gnav [data-page=\\\"e2e-plugin-page\\\"]').click()");
  await waitFor("!!document.querySelector('#e2e-plugin-visible')", 'directory plugin page render');
  assert.equal(await evaluate("document.querySelector('#e2e-plugin-visible').textContent"), 'Directory plugin frontend loaded');
  assert.equal(await evaluate("getComputedStyle(document.querySelector('#e2e-plugin-visible')).color"), 'rgb(1, 2, 3)');
  pass('directory plugin frontend.js registers a page and frontend.css is applied in real Electron');
  await evaluate("document.querySelector('#gnav [data-page=\\\"chat\\\"]').click()");

  // A new Electron process always opens the welcome page. Listing sessions on
  // an empty Brain must not create the legacy placeholder or select a chat.
  await waitFor("document.querySelector('#hero') && !document.querySelector('#hero').hidden", 'cold-start welcome page');
  assert.equal(await evaluate('API.state.chatConversationId'), '', 'cold start must have no active conversation');
  assert.equal(await evaluate("document.querySelector('#msgs').innerText"), '');
  if (captureScreenshots) await screenshot('p6.3-cold-start-home');
  let listResponse = await fetch(brainBase + '/api/conversations?src=pc', {headers:{Authorization:'Bearer ' + token}});
  let listed = await listResponse.json();
  assert.deepEqual(listed.items, [], 'opening the welcome page must not create an empty Brain conversation');
  pass('cold start with no history opens a clean welcome page without creating a conversation');

  // Seed an old transcript, then create nine real chats through the renderer.
  // Empty conversation rows are intentionally hidden until their first turn.
  fs.writeFileSync(path.join(home, 'data', 'pc.chat_history.jsonl'), [
    {t:'2026-10-17 18:00',role:'user',text:'LEGACY_FIRST_PROMPT'},
    {t:'2026-10-17 18:00',role:'assistant',text:'legacy response'},
  ].map(row => JSON.stringify(row)).join('\n') + '\n', 'utf8');
  for (let i = 0; i < 9; i++) {
    await evaluate("document.querySelector('#newTopicBtn').click()");
    await waitFor("API.state.chatConversationId==='' && !document.querySelector('#hero').hidden", 'seed chat welcome state');
    await submit('E2E_SEED_HISTORY_' + i);
    await waitFor("API.state.chatConversationId && document.querySelector('#msgs').innerText.includes('SEEDED_HISTORY_OK') && !document.querySelector('#sendBtn').classList.contains('is-sending')", 'seeded chat first turn ' + i);
  }
  listResponse = await fetch(brainBase + '/api/conversations?src=pc', {headers:{Authorization:'Bearer ' + token}});
  listed = await listResponse.json();
  assert.equal(listed.items.length, 10, 'legacy transcript plus nine saved sessions should be listed');
  const legacyId = listed.items.find(item => item.legacy).id;
  const legacyFirstPrompt = 'LEGACY_FIRST_PROMPT';
  await evaluate("document.querySelector('#newTopicBtn').click()");
  await waitFor("API.state.chatConversationId==='' && !document.querySelector('#hero').hidden", 'return to welcome before renderer refresh');
  await evaluate("document.querySelector('#reloadBtn').click()");
  await waitFor("document.querySelectorAll('[data-session]').length===10 && !document.querySelector('#hero').hidden", 'ten saved sessions on welcome page');
  assert.equal(await evaluate('API.state.chatConversationId'), '', 'ten saved sessions must not auto-select the last chat');
  assert.equal(await evaluate("document.querySelector('#msgs').innerText"), '', 'home must not show saved messages or Creator cards');
  if (captureScreenshots) await screenshot('p6.3-ten-history-home');
  await evaluate(`document.querySelector('[data-session="${legacyId}"]').click()`);
  await waitFor(`API.state.chatConversationId===${JSON.stringify(legacyId)} && document.querySelector('#msgs').innerText.includes(${JSON.stringify(legacyFirstPrompt)})`, 'legacy history explicitly opened');
  assert.equal(await evaluate(`document.querySelector('[data-session="${legacyId}"] .tx b')?.textContent || ''`), legacyFirstPrompt.slice(0, 7));
  pass('ten saved conversations remain visible on welcome; legacy opens only after explicit selection');

  await evaluate("document.querySelector('#newTopicBtn').click()");
  await waitFor("API.state.chatConversationId==='' && !document.querySelector('#hero').hidden", 'new conversation welcome state');
  assert.equal(await evaluate("document.querySelector('#msgs').innerText"), '', 'new conversation must clear old messages and cards');
  await evaluate('window.__vigilE2EEvents=[]');
  if (captureScreenshots) await screenshot('p6.3-new-conversation-home');
  await submit('E2E_TOOL_SUCCESS 请调用 smoke_ping');
  await waitFor("[...document.querySelectorAll('#msgs .turn:not(.me) .txt')].some(x=>x.innerText.includes('VIGIL_PLUGIN_CALL_OK'))", 'successful plugin reply');
  let conversationA = await evaluate('API.state.chatConversationId');
  assert.ok(conversationA && conversationA !== legacyId, 'first sent message creates an independent conversation');
  let calls = modelRequests.filter(x => x.toolNames.includes('smoke_ping'));
  assert.ok(calls.length >= 2, 'model request and tool-result follow-up must both reach Fake Provider');
  assert.ok(calls.some(x => x.toolNames.includes('smoke_ping')), 'Brain must send smoke_ping schema to the model');
  assert.ok(calls.some(x => x.messages.some(m => m.role === 'tool' && m.content === 'VIGIL_PLUGIN_CALL_OK')),
    'actual plugin result must be returned to the model');
  assert.ok((await assertToolCall(pluginTrace, 'ok')).some(row => row.mode === 'ok'), 'plugin host must execute the fixture tool');
  let events = await evaluate('window.__vigilE2EEvents');
  assert.equal(events.filter(e => e.type === 'done').length, 1, 'one terminal done event must reach renderer');
  pass('first message from welcome creates a scoped conversation and completes a plugin call');

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

  assert.equal(await evaluate(`document.querySelector('[data-session="${legacyId}"] .tx b')?.textContent || ''`), legacyFirstPrompt.slice(0, 7), 'legacy row title comes from its first user question');
  await submit('A_ONLY');
  await waitFor("document.querySelector('#msgs').innerText.includes('A_ONLY') && document.querySelector('#sendBtn').getAttribute('aria-busy')==='false'", 'A-only message');
  assert.equal(conversationA, await evaluate('API.state.chatConversationId'));
  const aOnlyCall = modelRequests.filter(x => x.userText === 'A_ONLY').at(-1);
  assert.ok(aOnlyCall && !aOnlyCall.messages.some(m => String(m.content || '').includes('LEGACY_FIRST_PROMPT')),
    'a new chat must not inherit the explicitly opened legacy transcript');
  await evaluate("document.querySelector('#newTopicBtn').click()");
  await waitFor("API.state.chatConversationId==='' && !document.querySelector('#hero').hidden", 'empty B welcome page');
  assert.equal(await evaluate("document.querySelector('#msgs').innerText"), '', 'new B welcome must be blank');
  assert.equal((await (await fetch(brainBase + '/api/conversations?src=pc', {headers:{Authorization:'Bearer ' + token}})).json()).items.length, 11,
    'clicking new conversation alone must not create a Brain row');
  await submit('B_ONLY');
  const bOnlyCall = await requestStarted(x => x.userText === 'B_ONLY');
  await waitFor("document.querySelector('#msgs .turn:not(.me)') && document.querySelector('#sendBtn').getAttribute('aria-busy')==='false'", 'B response');
  const conversationB = await evaluate('API.state.chatConversationId');
  assert.ok(conversationB && conversationB !== conversationA, 'first B message creates its own conversation');
  let bCall = bOnlyCall;
  assert.ok(bCall, 'Fake Provider must receive B_ONLY');
  assert.equal(bCall.messages.some(m => m.role === 'user' && String(m.content).includes('A_ONLY')), false,
    'B model context must not contain A');
  await evaluate(`document.querySelector('[data-session="${conversationA}"]').click()`);
  await waitFor("document.querySelector('#msgs').innerText.includes('A_ONLY')", 'A transcript restore');
  assert.equal(await evaluate("document.querySelector('#msgs').innerText.includes('B_ONLY')"), false,
    'A transcript must not contain B');
  await submit('A_CONTINUE');
  await waitFor("document.querySelector('#msgs').innerText.includes('A_CONTINUE') && document.querySelector('#sendBtn').getAttribute('aria-busy')==='false'", 'A continuation');
  const aCall = modelRequests.filter(x => x.userText === 'A_CONTINUE').at(-1);
  assert.ok(aCall.messages.some(m => m.role === 'user' && String(m.content).includes('A_ONLY')));
  assert.equal(aCall.messages.some(m => m.role === 'user' && String(m.content).includes('B_ONLY')), false,
    'A model context must not contain B');

  await evaluate("document.querySelector('#creatorBtn').click()");
  await submit('P6_FIXTURE_PLUGIN');
  await waitFor("document.querySelector('.crturn')?.innerText.includes('P6 session scoped draft')", 'Creator draft in A', 30000);
  await evaluate(`document.querySelector('[data-session="${conversationB}"]').click()`);
  await waitFor("document.querySelector('#msgs').innerText.includes('B_ONLY')", 'B transcript after draft');
  assert.equal(await evaluate("document.querySelectorAll('#msgs .crturn').length"), 0,
    'A Creator card must not leak into B');
  if (captureScreenshots) await screenshot('packaged-b-isolated');
  await evaluate(`document.querySelector('[data-session="${conversationA}"]').click()`);
  await waitFor("document.querySelector('.crturn')?.innerText.includes('P6 session scoped draft')", 'A Creator draft restore');
  await evaluate("document.querySelector('#creatorBtn').click();document.querySelector('#creatorBtn').click()");
  await waitFor("document.querySelector('#creatorBtn').innerText.includes('关')", 'return to ordinary chat mode');
  const brainPort = Number(new URL(brainBase).port);
  await stopChild(brain, true); brain = null;
  await wait(500);
  const restarted = await startBrain(modelBase, brainPort);
  await evaluate('location.reload()');
  await wait(1200);
  await waitFor(`API.state.chatConversationId===${JSON.stringify(conversationA)} && document.querySelector('.crturn')?.innerText.includes('P6 session scoped draft')`,
    'A transcript and Creator card after Brain restart', 30000);
  if (captureScreenshots) await screenshot('packaged-a-draft-restored');
  await evaluate(`document.querySelector('[data-session="${conversationB}"]').click()`);
  await waitFor("document.querySelector('#msgs').innerText.includes('B_ONLY')", 'B transcript after Brain restart');
  assert.equal(await evaluate("document.querySelectorAll('#msgs .crturn').length"), 0);
  await evaluate(`document.querySelector('[data-session="${legacyId}"]').click()`);
  await waitFor(`API.state.chatConversationId===${JSON.stringify(legacyId)} && document.querySelector('#hero')`, 'legacy entry remains selectable');
  await waitFor(`document.querySelector('#msgs').innerText.includes(${JSON.stringify(legacyFirstPrompt)})`, 'legacy transcript remains readable');
  assert.equal(await evaluate(`document.querySelector('[data-session="${legacyId}"] .tx b')?.textContent || ''`), legacyFirstPrompt.slice(0, 7));
  await evaluate(`document.querySelector('[data-session="${conversationA}"]').click()`);
  await waitFor("document.querySelector('.crturn')?.innerText.includes('P6 session scoped draft')", 'A draft before normal app restart');
  if (cdp && cdp.readyState === WebSocket.OPEN) cdp.close();
  cdp = null;
  await stopChild(electron, true); electron = null;
  await wait(500);
  await startElectron(brainBase);
  await waitFor("document.querySelector('#hero') && !document.querySelector('#hero').hidden", 'cold launch after an existing session');
  await waitFor("document.querySelectorAll('[data-session]').length===12", 'saved session list after cold launch');
  assert.equal(await evaluate('API.state.chatConversationId'), '', 'normal app relaunch must return to welcome');
  assert.equal(await evaluate("document.querySelector('#msgs').innerText"), '', 'normal relaunch must not show old messages/cards');
  assert.equal(await evaluate("document.querySelectorAll('[data-session]').length"), 12, 'all twelve saved conversations remain listed');
  if (captureScreenshots) await screenshot('p6.3-restart-home-with-history');
  await evaluate(`document.querySelector('[data-session="${conversationA}"]').click()`);
  await waitFor("document.querySelector('#msgs').innerText.includes('A_ONLY') && document.querySelector('.crturn')?.innerText.includes('P6 session scoped draft')",
    'saved conversation and Creator draft reopen after app restart');
  for (let i = 0; i < 3 && !(await evaluate("document.querySelector('#creatorBtn').innerText.includes('关')")); i++) {
    await evaluate("document.querySelector('#creatorBtn').click()");
  }
  assert.ok(await evaluate("document.querySelector('#creatorBtn').innerText.includes('关')"), 'ordinary chat mode should be restored for remaining chat checks');
  pass('real Electron + Fake Provider: A/B isolation, Creator restore, renderer reload, Brain restart, and cold-launch welcome');

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

  // Keep the existing chat-SSE disconnect check next to the established live
  // chat session; later Agent recovery fixtures deliberately restart Electron.
  await submit('晚安');
  await requestStarted(x => x.payload.stream === true && x.userText === '晚安');
  await wait(250);
  await stopChild(brain, true); brain = null;
  await waitFor("document.querySelector('#msgs').innerText.includes('连接中断') || document.querySelector('#msgs').innerText.includes('执行中断') || document.querySelector('#msgs').innerText.includes('失败')", 'Brain disconnect status');
  const disconnectState = await evaluate(`(()=>{const rows=Object.keys(localStorage).filter(k=>k.startsWith('vigil.display.v1:')).flatMap(k=>JSON.parse(localStorage.getItem(k)||'[]'));const r=rows.find(x=>x.input==='晚安');return {status:r?.status,disabled:document.querySelector('#sendBtn').disabled,text:document.querySelector('#msgs').innerText.slice(-240)}})()`);
  assert.equal(disconnectState.status, 'interrupted', 'Brain disconnect must persist interrupted status');
  assert.equal(await evaluate("document.querySelector('#sendBtn').getAttribute('aria-busy')"), 'false',
    'interrupted chat must not remain permanently running');
  pass('Brain process disconnect is surfaced as interrupted and unlocks chat');
  await startBrain(modelBase, brainPort);
  if (cdp && cdp.readyState === WebSocket.OPEN) cdp.close();
  cdp = null;
  await stopChild(electron, true); electron = null;
  await startElectron(brainBase);

  // P1 live acceptance: the real Electron bridge creates/reads/cancels Brain
  // Agent runs. Provider and plugin effects remain local, deterministic fixtures.
  const traceBeforeOnce = providerTrace(pluginTrace).length;
  const onceId = 'p1_tool_once_' + require('node:crypto').randomUUID();
  const onceRun = await createAgentRun('P1_AGENT_TOOL_ONCE 请调用 smoke_ping', onceId);
  const onceFinal = await waitAgentRun(onceRun.run_id);
  assert.equal(onceFinal.status, 'succeeded', JSON.stringify(onceFinal.error));
  assert.equal(onceFinal.result, 'AGENT_TOOL_RESULT_RECORDED');
  assert.equal(providerTrace(pluginTrace).length, traceBeforeOnce + 1, 'one logical Agent run must perform one plugin side effect');
  const requestsAfterOnce = modelRequests.length;
  const onceDuplicate = await createAgentRun('P1_AGENT_TOOL_ONCE 请调用 smoke_ping', onceId);
  assert.equal(onceDuplicate.run_id, onceRun.run_id, 'reusing a request id must return the same durable run');
  assert.equal(modelRequests.length, requestsAfterOnce, 'duplicate request id must not make another Provider request');
  assert.equal(providerTrace(pluginTrace).length, traceBeforeOnce + 1, 'duplicate request id must not repeat the plugin side effect');
  pass('Electron→Brain Agent run is durable and duplicate request IDs do not repeat a tool side effect');

  const traceBeforeUnknown = providerTrace(pluginTrace).length;
  const unknownId = 'p1_unknown_tool_' + require('node:crypto').randomUUID();
  const unknownRun = await createAgentRun('P1_AGENT_UNKNOWN_TOOL 请调用 smoke_ping', unknownId);
  const unknownFinal = await waitAgentRun(unknownRun.run_id);
  assert.equal(unknownFinal.status, 'failed');
  assert.equal(unknownFinal.error?.reason, 'tool_unknown');
  assert.equal(providerTrace(pluginTrace).length, traceBeforeUnknown + 1,
    'the fixture records the external effect before returning an unknown result');
  const requestsAfterUnknown = modelRequests.length;
  const unknownDuplicate = await createAgentRun('P1_AGENT_UNKNOWN_TOOL 请调用 smoke_ping', unknownId);
  assert.equal(unknownDuplicate.run_id, unknownRun.run_id);
  assert.equal(modelRequests.length, requestsAfterUnknown, 'unknown tool outcome must not be replayed on request retry');
  assert.equal(providerTrace(pluginTrace).length, traceBeforeUnknown + 1,
    'unknown tool outcome must preserve evidence without repeating the external effect');
  pass('uncertain tool result remains failed/unknown and a repeated request does not replay its side effect');

  const traceBeforeCancel = providerTrace(pluginTrace).length;
  let releaseCancelProvider;
  cancelGate = { promise: new Promise(resolve => { releaseCancelProvider = resolve; }) };
  const cancelId = 'p1_cancel_run_' + require('node:crypto').randomUUID();
  const cancelRequest = requestStarted(x => x.userText.includes('P1_AGENT_CANCEL_DURING_MODEL'));
  const cancelRun = await createAgentRun('P1_AGENT_CANCEL_DURING_MODEL 请稍候再调用 smoke_ping', cancelId);
  await cancelRequest;
  assert.equal((await agentSnapshot(cancelRun.run_id)).status, 'running');
  const cancelResponse = await evaluate(`vigil.api('POST','/api/agent/runs/${encodeURIComponent(cancelRun.run_id)}/cancel',{})`);
  assert.equal(cancelResponse.status, 200);
  assert.equal(cancelResponse.json?.status, 'cancelled');
  releaseCancelProvider();
  const cancelFinal = await waitAgentRun(cancelRun.run_id);
  assert.equal(cancelFinal.status, 'cancelled');
  assert.equal(providerTrace(pluginTrace).length, traceBeforeCancel,
    'a tool returned after cancellation must not be dispatched');
  cancelGate = null;
  pass('live Electron cancellation leaves an explicit cancelled Brain state and prevents the next tool dispatch');

  const traceBeforeTimeout = providerTrace(pluginTrace).length;
  const timeoutId = 'p1_timeout_run_' + require('node:crypto').randomUUID();
  const timeoutRequest = requestStarted(x => x.userText.includes('P1_AGENT_TIMEOUT_BEFORE_TOOL'));
  const timeoutRun = await createAgentRun('P1_AGENT_TIMEOUT_BEFORE_TOOL 请稍候再调用 smoke_ping', timeoutId,
    { request_timeout: 1 });
  await timeoutRequest;
  const timeoutFinal = await waitAgentRun(timeoutRun.run_id, ['failed', 'cancelled', 'interrupted'], 10000);
  assert.notEqual(timeoutFinal.status, 'succeeded', 'a timed-out model request must not claim task success');
  assert.equal(providerTrace(pluginTrace).length, traceBeforeTimeout,
    'a tool call arriving after request timeout must not be dispatched');
  pass('bounded Provider timeout produces a non-success terminal state and blocks later tool dispatch');

  // Exit the actual Electron process while an Agent run remains in-flight.
  // The isolated Brain should finish once, persist its terminal state, and be
  // readable after a fresh Electron process attaches to the same profile.
  let releaseWindowExit;
  windowExitGate = { promise: new Promise(resolve => { releaseWindowExit = resolve; }) };
  const windowExitId = 'p1_window_exit_' + require('node:crypto').randomUUID();
  const windowExitRequest = requestStarted(x => x.userText.includes('P1_AGENT_WINDOW_EXIT'));
  const windowExitRun = await createAgentRun('P1_AGENT_WINDOW_EXIT complete after the desktop window closes', windowExitId);
  await windowExitRequest;
  assert.equal((await agentSnapshot(windowExitRun.run_id)).status, 'running');
  if (cdp && cdp.readyState === WebSocket.OPEN) cdp.close();
  cdp = null;
  await stopChild(electron, true); electron = null;
  releaseWindowExit();
  const windowExitFinal = await waitBrainAgentRun(brainBase, windowExitRun.run_id);
  assert.equal(windowExitFinal.status, 'succeeded');
  assert.equal(windowExitFinal.result, 'WINDOW_EXIT_IN_PROGRESSWINDOW_EXIT_COMPLETED');
  const windowExitProviderCalls = modelRequests.filter(x => x.userText.includes('P1_AGENT_WINDOW_EXIT')).length;
  await startElectron(brainBase);
  const windowExitRestored = await agentSnapshot(windowExitRun.run_id);
  assert.equal(windowExitRestored.status, 'succeeded');
  assert.equal(windowExitRestored.result, windowExitFinal.result);
  const windowExitDuplicate = await createAgentRun('P1_AGENT_WINDOW_EXIT complete after the desktop window closes', windowExitId);
  assert.equal(windowExitDuplicate.run_id, windowExitRun.run_id);
  assert.equal(modelRequests.filter(x => x.userText.includes('P1_AGENT_WINDOW_EXIT')).length, windowExitProviderCalls,
    'opening a new Electron process and retrying must not duplicate the completed model request');
  windowExitGate = null;
  pass('an in-flight Agent run finishes once after Electron exit and its terminal result restores after relaunch');

  const burstId = 'p1_event_burst_' + require('node:crypto').randomUUID();
  const burstRequest = requestStarted(x => x.userText.includes('P1_AGENT_EVENT_BURST'));
  const burstRun = await createAgentRun('P1_AGENT_EVENT_BURST return a bounded cursor trace', burstId);
  await burstRequest;
  const resumed = await disconnectAndResumeAgentEvents(brainBase, burstRun.run_id);
  assert.equal(resumed.snapshot.status, 'succeeded');
  assert.equal(resumed.snapshot.content, 'x'.repeat(300));
  const burstRequests = modelRequests.filter(x => x.userText.includes('P1_AGENT_EVENT_BURST')).length;
  const burstDuplicate = await createAgentRun('P1_AGENT_EVENT_BURST return a bounded cursor trace', burstId);
  assert.equal(burstDuplicate.run_id, burstRun.run_id);
  assert.equal(modelRequests.filter(x => x.userText.includes('P1_AGENT_EVENT_BURST')).length, burstRequests,
    'reconnecting from an event cursor must observe the same run instead of creating another model request');
  pass('SSE disconnect/resume crosses the bounded event window and returns an authoritative cursor-gap snapshot');

  // Force a Brain process exit after the fixture records a side effect but
  // before it returns. On restart, durable dispatched state must become
  // interrupted/unknown and a duplicate request must never replay the plugin.
  const traceBeforeCrash = providerTrace(pluginTrace).length;
  const crashId = 'p1_process_crash_' + require('node:crypto').randomUUID();
  const crashRequest = requestStarted(x => x.userText.includes('P1_AGENT_TOOL_CRASH'));
  const crashRun = await createAgentRun('P1_AGENT_TOOL_CRASH 请调用 smoke_ping', crashId);
  await crashRequest;
  await assertToolCall(pluginTrace, 'crash');
  const beforeCrash = await agentSnapshot(crashRun.run_id);
  assert.equal(beforeCrash.status, 'running', 'fixture must be in-flight when Brain is terminated');
  await stopChild(brain, true); brain = null;
  await startBrain(modelBase, brainPort);
  const afterCrash = await agentSnapshot(crashRun.run_id);
  assert.equal(afterCrash.status, 'interrupted', 'Brain restart must persist interrupted Agent status');
  assert.equal(afterCrash.error?.reason, 'interrupted');
  assert.equal(afterCrash.steps?.some(step => step.status === 'unknown'), true,
    'a dispatched tool without a returned result must be marked unknown');
  const requestsAfterCrash = modelRequests.filter(x => x.userText.includes('P1_AGENT_TOOL_CRASH')).length;
  const crashDuplicate = await createAgentRun('P1_AGENT_TOOL_CRASH 请调用 smoke_ping', crashId);
  assert.equal(crashDuplicate.run_id, crashRun.run_id);
  assert.equal(modelRequests.filter(x => x.userText.includes('P1_AGENT_TOOL_CRASH')).length, requestsAfterCrash,
    'retrying the same request after restart must not re-run the model');
  assert.equal(providerTrace(pluginTrace).length, traceBeforeCrash + 1,
    'retrying the same request after restart must not repeat the side effect');
  pass('Brain crash after durable tool dispatch recovers as interrupted/unknown without replaying a side effect');

  // The previous Electron process had an open connection to the Brain that was
  // forcibly restarted above. Relaunch the isolated desktop client so the
  // final chat-disconnect assertion starts from a known healthy connection.
  if (cdp && cdp.readyState === WebSocket.OPEN) cdp.close();
  cdp = null;
  await stopChild(electron, true); electron = null;
  await startElectron(brainBase);
  console.log('PASS integrated smoke total: ' + passCount);
}

run().catch(error => {
  console.error(error && error.stack || error);
  try { if (fs.existsSync(path.join(root, 'brain.log'))) console.error('Brain log retained until cleanup: ' + path.join(root, 'brain.log')); } catch (_) {}
  process.exitCode = 1;
}).finally(async () => {
  // Closing the browser target may close CDP before its response arrives, leaving
  // cdpSend waiting for its timeout. Close the test-owned socket directly; the
  // child process tree is terminated immediately below.
  try { if (cdp && cdp.readyState === WebSocket.OPEN) cdp.close(); } catch (_) {}
  // This is an isolated test profile; force-close the test-owned Electron tree
  // after the assertions so background renderer/utility processes cannot hold
  // temporary log handles open on Windows.
  await stopChild(electron, true);
  await stopChild(brain, true);
  if (modelServer) {
    if (typeof modelServer.closeAllConnections === 'function') modelServer.closeAllConnections();
    await new Promise(resolve => modelServer.close(resolve));
  }
  for (const item of cdpPending.values()) { clearTimeout(item.timer); item.reject(new Error('test ended')); }
  cdpPending.clear();
  try { await safeCleanup(); } catch (e) { console.error('Temp cleanup failed: ' + e.message); process.exitCode = 1; }
});
