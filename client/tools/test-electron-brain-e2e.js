// End-to-end smoke: real Electron main/preload/renderer -> real isolated Brain
// HTTP/SSE route -> CloudProvider -> test-only model fixture -> plugin host.
// All profile, Brain state, logs, and fixture plugin files live in one owned temp dir.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const WebSocket = require('ws');

const client = path.resolve(__dirname, '..');
const repo = path.resolve(client, '..');
const brainDir = path.join(repo, 'brain');
const token = 'e2e-' + require('node:crypto').randomBytes(24).toString('hex');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-p5-joint-'));
const home = path.join(root, 'brain-home');
const profile = path.join(root, 'electron-profile');
const captureScreenshots = process.argv.includes('--packaged') || process.argv.includes('--screenshots');
const modelRequests = [];
const modelWaiters = [];
let modelServer, brain, electron, cdp;
let cdpSeq = 0;
let coreTarget = null;
const diag = (...args) => { if (process.env.VIGIL_DIAG_PLUGIN_LIFECYCLE === '1') console.log('[e2e-lifecycle-diag]', new Date().toISOString(), ...args); };
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
    diag('Fake Provider request', JSON.stringify({ userText, stream: !!payload.stream, tools: call.toolNames }));
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
      if (userText.startsWith('E2E_SEED_HISTORY_')) {
        res.write('data: ' + JSON.stringify({ choices: [{ delta: { content: 'SEEDED_HISTORY_OK' } }] }) + '\n\n');
        res.write('data: ' + JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' } ] }) + '\n\n');
        res.end('data: [DONE]\n\n');
        return;
      }
      if (userText === 'AFTER_PLUGIN_DISABLE') {
        res.write('data: ' + JSON.stringify({ choices: [{ delta: { content: 'CORE_AFTER_PLUGIN_DISABLE_OK' } }] }) + '\n\n');
        res.write('data: ' + JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' }] }) + '\n\n');
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

    if (userText.startsWith('E2E_SEED_HISTORY_')) {
      return json(res, 200, { choices: [{ message: { role: 'assistant', content: 'SEEDED_HISTORY_OK' } }] });
    }

    if (userText === 'AFTER_PLUGIN_DISABLE') {
      return json(res, 200, { choices: [{ message: { role: 'assistant', content: 'CORE_AFTER_PLUGIN_DISABLE_OK' } }] });
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
    agent_execution: { enabled: false }, features: { tools: true, vigil_smoke: true },
    persona_file: 'personas/default.json',
  };
  fs.writeFileSync(path.join(home, 'config', 'config.json'), JSON.stringify(brainConfig), 'utf8');
  const pluginDir = path.join(home, 'plugins', 'vigil_smoke');
  fs.mkdirSync(pluginDir, { recursive: true });
  fs.writeFileSync(path.join(pluginDir, '__init__.py'), [
    'import json, os',
    'from pathlib import Path',
    'NAME = "vigil_smoke"',
    'DESC = "isolated Electron Brain integration fixture"',
    'FEATURE = "vigil_smoke"',
    'def smoke_ping(mode):',
    '    with open(os.environ["VIGIL_E2E_PLUGIN_TRACE"], "a", encoding="utf-8") as f:',
    '        f.write(json.dumps({"tool":"smoke_ping","mode":mode}) + "\\n")',
    '    if mode == "fail": raise RuntimeError("fixture plugin execution failure")',
    '    return "VIGIL_PLUGIN_CALL_OK"',
    'TOOLS = {"smoke_ping": smoke_ping}',
    'TOOL_SPECS = {"smoke_ping": {"desc":"E2E plugin call", "params":[{"name":"mode","type":"string","desc":"test mode"}], "arg_param":"mode"}}',
    '',
  ].join('\n'), 'utf8');
  fs.writeFileSync(path.join(pluginDir, 'frontend.js'), [
    'if (window.parent !== window) { window.VigilPlugin.setSurfaceActive(true); document.body.style.background = "rgb(22, 90, 150)"; document.body.dataset.surfaceRuntime = JSON.stringify({require:typeof require,process:typeof process,fs:typeof fs,parentDom:(()=>{try{return !!parent.document.querySelector("#ta")}catch(_){return "blocked"}})()}); }',
    'window.VigilPlugin.registerPage({',
    '  id: "e2e-plugin-page", name: "E2E Plugin", icon: "plug",',
    '  render(ctx) { ctx.view.innerHTML = "<section id=\\"e2e-plugin-visible\\">Directory plugin frontend loaded</section>"; }',
    '});',
  ].join('\n'), 'utf8');
  if (!process.argv.includes('--wallpaper-surface-only')) {
    fs.writeFileSync(path.join(pluginDir, '__init__.py'), fs.readFileSync(path.join(pluginDir, '__init__.py'), 'utf8') + '\nUI_SURFACES = ["content-background"]\n', 'utf8');
  }
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
    VIGIL_E2E_PLUGIN_TRACE: pluginTrace, PYTHONDONTWRITEBYTECODE: '1', PYTHONUNBUFFERED: '1' };
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
  let result;
  try { result = await cdpSend('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }); }
  catch (error) { throw new Error(error.message + '; expression=' + expression.slice(0, 240)); }
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
        coreTarget = page;
        diag('Core target selected', JSON.stringify({id:page.id,type:page.type,url:page.url,title:page.title,webSocketDebuggerUrl:page.webSocketDebuggerUrl}));
        cdp = new WebSocket(page.webSocketDebuggerUrl, { origin: 'http://localhost' });
        await new Promise((resolve, reject) => { cdp.once('open', resolve); cdp.once('error', reject); });
        cdp.on('message', data => {
          let msg; try { msg = JSON.parse(String(data)); } catch (_) { return; }
          if (msg.method && ['Runtime.exceptionThrown','Log.entryAdded','Inspector.targetCrashed','Page.frameNavigated','Page.lifecycleEvent'].includes(msg.method)) diag('Core CDP event', msg.method, JSON.stringify(msg.params).slice(0,1000));
          if (msg.id && cdpPending.has(msg.id)) {
            const item = cdpPending.get(msg.id); cdpPending.delete(msg.id); clearTimeout(item.timer);
            msg.error ? item.reject(new Error(msg.error.message)) : item.resolve(msg.result || {});
          }
        });
        await cdpSend('Runtime.enable');
        await cdpSend('Page.enable');
        await cdpSend('Log.enable').catch(()=>{});
        await cdpSend('Inspector.enable').catch(()=>{});
        return;
      }
    } catch (_) {}
    await wait(150);
  }
  throw new Error('Could not attach to actual Electron renderer');
}
async function evaluateGuest(debugPort, expression) {
  const deadline = Date.now() + 10000;
  let page;
  while (Date.now() < deadline) {
    const pages = await (await fetch('http://127.0.0.1:' + debugPort + '/json/list')).json();
    page = pages.find(p => p.type === 'page' && String(p.url || '').startsWith('vigil-plugin://'));
    if (page) break;
    await wait(50);
  }
  if (!page) throw new Error('Isolated plugin renderer was not created on port ' + debugPort + '; targets=' + JSON.stringify(await targets(debugPort)));
  const socket = new WebSocket(page.webSocketDebuggerUrl, { origin: 'http://localhost' });
  await new Promise((resolve, reject) => { socket.once('open', resolve); socket.once('error', reject); });
  let id = 0;
  const pending = new Map();
  socket.on('message', raw => {
    let message; try { message = JSON.parse(String(raw)); } catch (_) { return; }
    const item = pending.get(message.id);
    if (item) { pending.delete(message.id); message.error ? item.reject(new Error(message.error.message)) : item.resolve(message.result || {}); }
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const commandId = ++id; pending.set(commandId, { resolve, reject });
    socket.send(JSON.stringify({ id: commandId, method, params }));
  });
  try {
    const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text || expression);
    return result.result && result.result.value;
  } finally { socket.close(); }
}
async function targets(debugPort) {
  return (await (await fetch('http://127.0.0.1:' + debugPort + '/json/list')).json());
}
async function waitForTarget(debugPort, predicate, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const page = (await targets(debugPort)).find(predicate);
    if (page) return page;
    await wait(100);
  }
  return null;
}
async function evaluateTarget(page, expression) {
  const socket = new WebSocket(page.webSocketDebuggerUrl, { origin: 'http://localhost' });
  await new Promise((resolve, reject) => { socket.once('open', resolve); socket.once('error', reject); });
  let id = 0;
  const pending = new Map();
  socket.on('message', raw => {
    let message; try { message = JSON.parse(String(raw)); } catch (_) { return; }
    const item = pending.get(message.id);
    if (item) { pending.delete(message.id); message.error ? item.reject(new Error(message.error.message)) : item.resolve(message.result || {}); }
  });
  try {
    const result = await new Promise((resolve, reject) => {
      const commandId = ++id; pending.set(commandId, { resolve, reject });
      socket.send(JSON.stringify({ id: commandId, method: 'Runtime.evaluate', params: { expression, awaitPromise: true, returnByValue: true } }));
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text || expression);
    return result.result && result.result.value;
  } finally { socket.close(); }
}
async function lifecycleSnapshot(label, debugPort) {
  if (process.env.VIGIL_DIAG_PLUGIN_LIFECYCLE !== '1') return;
  const boundedJson = async route => {
    const controller = new AbortController(); const timer = setTimeout(()=>controller.abort(), 1200);
    try { const response = await fetch('http://127.0.0.1:' + debugPort + route,{signal:controller.signal}); return await response.json(); }
    catch(error) { return {error:error.name + ': ' + error.message}; }
    finally { clearTimeout(timer); }
  };
  const pages = await boundedJson('/json/list');
  const version = await boundedJson('/json/version');
  diag(label, 'electronPid=' + electron?.pid, 'coreWs=' + cdp?.readyState,
    'selected=' + JSON.stringify(coreTarget && {id:coreTarget.id,url:coreTarget.url}),
    'targets=' + JSON.stringify(Array.isArray(pages) ? pages.map(p=>({id:p.id,type:p.type,url:p.url,title:p.title,ws:p.webSocketDebuggerUrl})) : pages),
    'browser=' + JSON.stringify(version));
}
async function startElectron(brainBase) {
  const debugPort = await freePort();
  const packaged = process.argv.includes('--packaged');
  const candidateId = String(process.env.VIGIL_CANDIDATE_ID || '');
  const packagedRoot = candidateId
    ? path.join(client, 'dist-release-candidates', candidateId)
    : path.join(client, 'dist-release');
  const electronExe = packaged
    ? path.join(packagedRoot, 'win-unpacked', 'Vigil.exe')
    : require(path.join(client, 'node_modules', 'electron'));
  if (packaged && !fs.existsSync(electronExe)) throw new Error('Candidate packaged executable not found: ' + electronExe);
  const env = { ...process.env, VIGIL_USER_DATA: profile, VIGIL_TEST_MODE: '1',
    VIGIL_TEST_BRAIN: brainBase, VIGIL_TEST_TOKEN: token,
    ELECTRON_ENABLE_LOGGING: '1', ELECTRON_DISABLE_SECURITY_WARNINGS: '1' };
  const logFile = fs.openSync(path.join(root, 'electron.log'), 'w');
  // Do not disable Chromium's sandbox in this security regression test.
  const args = ['--remote-debugging-port=' + debugPort, '--remote-allow-origins=*',
    '--disable-features=CalculateNativeWinOcclusion'];
  if (!packaged) args.push(client);
  electron = spawn(electronExe, args, {
    cwd: client, env, stdio: ['ignore', logFile, logFile], windowsHide: true,
  });
  if (process.env.VIGIL_DIAG_PLUGIN_LIFECYCLE === '1') electron.once('exit', (code, signal) => diag('Electron child exited', JSON.stringify({code,signal,pid:electron.pid})));
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
async function prepareWallpaperFixture() {
  const extensionSource = path.join(root, 'wallpaper-extension');
  const build = spawnSync(process.execPath, [path.join(client, 'tools/build-wallpaper-extension.js'), extensionSource], {
    cwd: client, encoding: 'utf8', windowsHide: true,
  });
  if (build.status !== 0) throw new Error('Could not build isolated Wallpaper fixture: ' + (build.stderr || build.stdout));
  const stateDir = path.join(profile, 'extensions');
  const version = require(path.join(extensionSource, 'manifest.json')).version;
  const installed = path.join(stateDir, 'programs', 'com.vigil.wallpaper', version);
  fs.mkdirSync(path.dirname(installed), { recursive: true });
  fs.cpSync(extensionSource, installed, { recursive: true, errorOnExist: true, force: false, dereference: false });
  fs.mkdirSync(stateDir, { recursive: true });
  fs.writeFileSync(path.join(stateDir, 'state.json'), JSON.stringify({ schema: 1, wallpaper: {
    installed: true, enabled: true, activeVersion: version,
    grantedPermissions: ['desktop.wallpaper.control', 'desktop.ui.contribute'],
  } }), 'utf8');
  const python = process.env.VIGIL_TEST_PYTHON || path.join(repo, 'venv', 'Scripts', 'python.exe');
  const imagePath = path.join(root, 'wallpaper-fixture.png');
  const makeImage = spawnSync(python, ['-c', 'from PIL import Image; Image.new("RGB", (640, 360), (22, 90, 150)).save(r"' + imagePath.replace(/\\/g, '\\\\') + '")'], {
    encoding: 'utf8', windowsHide: true,
  });
  if (makeImage.status !== 0) throw new Error('Could not create isolated Wallpaper fixture image: ' + makeImage.stderr);
  const { createWallpaperHost } = require(path.join(extensionSource, 'wallpaper-host.js'));
  const host = createWallpaperHost({ dataDir: path.join(profile, 'wallpaper-engine'), vendorDir: path.join(extensionSource, 'vendor', 'dsh-wallpaper-engine') });
  try {
    await host.importFile(imagePath);
    const inventory = (await host.request('GET', '/inventory')).data;
    const image = (inventory.wallpapers || []).find(item => item.type === 'image');
    if (!image) throw new Error('Wallpaper fixture import did not produce an image: ' + JSON.stringify(inventory).slice(0, 600));
    const mediaResponse = await fetch(image.media);
    if (!mediaResponse.ok || !(mediaResponse.headers.get('content-type') || '').startsWith('image/')) throw new Error('Wallpaper fixture media could not be read: HTTP ' + mediaResponse.status + ' ' + image.media);
    await mediaResponse.arrayBuffer();
    const saved = (await host.request('GET', '/settings')).data;
    await host.request('PUT', '/settings', { ...(saved.settings || {}), id: image.id });
  } finally { host.close(); }
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
  if (process.argv.includes('--wallpaper-surface-only')) await prepareWallpaperFixture();
  let { debugPort } = await startElectron(brainBase);
  if (process.argv.includes('--wallpaper-surface-only')) {
    await waitFor("document.body.classList.contains('plugin-surface-active') && !!document.querySelector('#plugin-surface-host iframe')", 'Wallpaper content surface registration', 30000);
    const surfaceFrame = await evaluate("(()=>{const f=document.querySelector('#plugin-surface-host iframe');let blocked=false;try{void f.contentWindow.document.body}catch(_){blocked=true}return {src:f.src,sandbox:f.getAttribute('sandbox'),blocked,transparent:getComputedStyle(document.querySelector('.app')).backgroundColor}})()");
    assert.match(surfaceFrame.src, /^vigil-plugin:\/\/p-[a-f0-9]{20}\/surface\.html$/);
    assert.equal(surfaceFrame.sandbox, 'allow-scripts'); assert.equal(surfaceFrame.blocked, true);
    assert.equal(surfaceFrame.transparent, 'rgba(0, 0, 0, 0)');
    const surfaceTarget = await waitForTarget(debugPort, target => target.type === 'iframe' && String(target.url || '').includes('/surface.html'), 15000);
    assert.ok(surfaceTarget, 'Wallpaper must render in a separate Chromium iframe target');
    await waitForTarget(debugPort, target => target.id === surfaceTarget.id && target.url.includes('/surface.html'));
    const imageLoad = JSON.parse(await evaluateTarget(surfaceTarget, "new Promise(resolve=>{const end=Date.now()+15000;const check=()=>{const i=document.querySelector('.vwp-stage img');if(i?.complete&&i.naturalWidth)return resolve(JSON.stringify({kind:'loaded',width:i.naturalWidth}));if(Date.now()>end)return resolve(JSON.stringify({kind:i?'timeout':'missing',width:i?.naturalWidth||0}));setTimeout(check,100)};check()})"));
    assert.equal(imageLoad.kind, 'loaded'); assert.equal(imageLoad.width, 640);
    let probe = await evaluateTarget(surfaceTarget, "JSON.stringify({url:location.href,node:typeof require,process:typeof process,fs:typeof fs,coreDom:(()=>{try{return !!parent.document.querySelector('#ta')}catch(_){return 'blocked'}})(),width:document.querySelector('.vwp-stage')?.getBoundingClientRect().width,height:document.querySelector('.vwp-stage')?.getBoundingClientRect().height,image:document.querySelector('.vwp-stage img')?.naturalWidth,fit:getComputedStyle(document.querySelector('.vwp-stage img')||document.body).objectFit,active:document.body.classList.contains('vwp-active'),webgl:(()=>{const c=document.createElement('canvas'),g=c.getContext('webgl2')||c.getContext('webgl');return {available:!!g,version:g?.getParameter(g.VERSION)||''}})()})");
    probe = JSON.parse(probe);
    assert.equal(probe.node, 'undefined'); assert.equal(probe.process, 'undefined'); assert.equal(probe.fs, 'undefined');
    assert.equal(probe.coreDom, 'blocked'); assert.ok(probe.width > 800 && probe.height > 500, 'Wallpaper stage must follow the full resized client viewport');
    assert.equal(probe.image, 640, 'imported image must decode at its source resolution on first load');
    assert.equal(probe.fit, 'cover'); assert.equal(probe.active, true);
    assert.equal(probe.webgl.available, true, 'isolated Wallpaper surface must be able to create a WebGL context');
    const layer = await evaluate("(()=>{const f=document.querySelector('#plugin-surface-host iframe');const app=document.querySelector('.app');const input=document.querySelector('#ta').getBoundingClientRect();return {pointer:getComputedStyle(f).pointerEvents,appZ:getComputedStyle(app).zIndex,inputHit:document.elementFromPoint(input.left+input.width/2,input.top+input.height/2)?.id}})()");
    assert.deepEqual(layer, { pointer: 'none', appZ: '1', inputHit: 'ta' }, 'Core input must remain the interactive layer above Wallpaper');
    pass('Wallpaper Engine renderer displays a sharp, correctly fitted image behind the Core UI');
    await submit('WALLPAPER_ACTIVE_CORE_CHAT'); await requestStarted(call => call.userText === 'WALLPAPER_ACTIVE_CORE_CHAT');
    await waitFor("document.querySelector('#msgs').innerText.includes('VIGIL_PLUGIN_CALL_OK') && document.querySelector('#sendBtn').getAttribute('aria-busy')==='false'", 'Core chat while Wallpaper is active');
    pass('Core chat controls remain usable while Wallpaper is active');
    const opened = await evaluate("window.vigil.plugins.openUi('desktop-wallpaper')"); assert.equal(opened.ok, true);
    await waitFor("window.vigil.plugins.runtimeTest().then(r=>r.ok && r.renderers.some(renderer=>renderer.id==='desktop-wallpaper' && renderer.ready))", 'Wallpaper management renderer startup');
    const managementRuntime = await evaluate('window.vigil.plugins.runtimeTest()');
    const management = managementRuntime.renderers.find(renderer => renderer.id === 'desktop-wallpaper');
    assert.ok(management && management.pid !== managementRuntime.corePid);
    const managementProbe = JSON.parse(await evaluateGuest(managementRuntime.pluginDebugPort,
      "new Promise(resolve=>{const end=Date.now()+10000;const check=()=>{const img=document.querySelector('.vwp-player img');if(img?.complete&&img.naturalWidth)return resolve(JSON.stringify({mode:window.__VIGIL_PLUGIN_UI_MODE__,stage:!!document.querySelector('.vwp-stage'),preview:img.naturalWidth,node:typeof require,process:typeof process}));if(Date.now()>end)return resolve(JSON.stringify({mode:window.__VIGIL_PLUGIN_UI_MODE__,stage:!!document.querySelector('.vwp-stage'),preview:img?.naturalWidth||0,node:typeof require,process:typeof process}));setTimeout(check,100)};check()})"));
    assert.deepEqual(managementProbe, { mode: 'management', stage: false, preview: 640, node: 'undefined', process: 'undefined' });
    pass('Wallpaper management UI shows a preview without starting a second playback surface');
    await evaluateGuest(managementRuntime.pluginDebugPort, "document.querySelector('#vwp-close').click(); true");
    await waitFor("!document.body.classList.contains('plugin-surface-active')", 'Wallpaper selection disabled from management UI');
    await evaluateGuest(managementRuntime.pluginDebugPort, "document.querySelector('#vwp-grid [data-id]')?.click(); true");
    await waitFor("document.body.classList.contains('plugin-surface-active')", 'Wallpaper selection restored from management UI');
    pass('Wallpaper selection changes update the client background through the Broker');
    const disabled = await evaluate('window.vigil.wallpaperExtension.disable()');
    assert.equal(disabled.enabled, false);
    await waitFor("!document.querySelector('#plugin-surface-host iframe') && !document.body.classList.contains('plugin-surface-active')", 'Wallpaper view and permissions revoked');
    await evaluate("document.querySelector('#gnav [data-page=\\\"chat\\\"]').click()");
    await submit('AFTER_PLUGIN_DISABLE'); await requestStarted(call => call.userText === 'AFTER_PLUGIN_DISABLE');
    await waitFor("document.querySelector('#msgs').innerText.includes('CORE_AFTER_PLUGIN_DISABLE_OK') && document.querySelector('#sendBtn').getAttribute('aria-busy')==='false'", 'Core Fake Provider chat after Wallpaper disable');
    pass('Core Fake Provider chat completes after Wallpaper is disabled');
    const enabled = await evaluate('window.vigil.wallpaperExtension.enable()'); assert.equal(enabled.enabled, true);
    await waitFor("document.body.classList.contains('plugin-surface-active') && !!document.querySelector('#plugin-surface-host iframe')", 'Wallpaper surface restored after re-enable', 30000);
    const dataFilesBefore = fs.readdirSync(path.join(profile, 'wallpaper-engine'), { recursive: true }).length;
    const removed = await evaluate('window.vigil.wallpaperExtension.uninstall()'); assert.equal(removed.installed, false);
    await waitFor("!document.querySelector('#plugin-surface-host iframe')", 'Wallpaper surface removed after uninstall');
    const dataFilesAfter = fs.readdirSync(path.join(profile, 'wallpaper-engine'), { recursive: true }).length;
    assert.ok(dataFilesBefore > 0 && dataFilesAfter === dataFilesBefore, 'uninstall must preserve Wallpaper user materials and settings');
    pass('Wallpaper disable, re-enable, uninstall cleanup and user-data retention pass');
    console.log('PASS Wallpaper surface integration total: ' + passCount);
    return;
  }
  await waitFor("!!document.querySelector('#gnav [data-page=\\\"plugin-ui-brain-vigil_smoke\\\"]')", 'directory plugin page registration');
  await waitFor("document.body.classList.contains('plugin-surface-active') && !!document.querySelector('#plugin-surface-host iframe')", 'plugin content background surface registration');
  const surfaceFrame = await evaluate("(()=>{const f=document.querySelector('#plugin-surface-host iframe');let blocked=false;try{void f.contentWindow.document.body}catch(_){blocked=true}return {sandbox:f.getAttribute('sandbox'),src:f.src,blocked,transparent:getComputedStyle(document.querySelector('.app')).backgroundColor}})()");
  assert.equal(surfaceFrame.sandbox, 'allow-scripts', 'content surface must be sandboxed without same-origin access');
  assert.equal(surfaceFrame.blocked, true, 'Core DOM must not be readable from the plugin surface');
  assert.equal(surfaceFrame.transparent, 'rgba(0, 0, 0, 0)', 'Core shell must let the plugin background show through');
  const surfaceTarget = await waitForTarget(debugPort, target => target.type === 'iframe' && String(target.url || '').includes('/surface.html'));
  assert.ok(surfaceTarget, 'content surface must appear as its own Chromium iframe target');
  const surfaceProbe = await evaluateTarget(surfaceTarget, "JSON.stringify({runtime:document.body.dataset.surfaceRuntime,coreDom:document.querySelector('#ta'),body:document.body.style.backgroundColor,origin:location.origin})");
  const surface = JSON.parse(surfaceProbe);
  assert.deepEqual(JSON.parse(surface.runtime), { require: 'undefined', process: 'undefined', fs: 'undefined', parentDom: 'blocked' });
  assert.equal(surface.body, 'rgb(22, 90, 150)', 'separate surface renderer must paint its background content');
  pass('generic plugin surface renders behind Core in a sandboxed isolated iframe');
  assert.equal(await evaluate("typeof window.VigilPlugin === 'undefined' && typeof window.VigilAPI === 'undefined'"), true,
    'Core renderer must not expose plugin execution API');
  assert.equal(await evaluate("!!document.getElementById('plugin-css-vigil_smoke')"), false,
    'plugin CSS must not enter Core document');
  await evaluate("document.querySelector('#gnav [data-page=\\\"plugin-ui-brain-vigil_smoke\\\"]').click()");
  await evaluate("document.querySelector('#view button')?.click()");
  await waitFor("window.vigil.plugins.runtimeTest().then(r=>r.ok && r.renderers.some(renderer=>renderer.id==='brain-vigil_smoke' && renderer.ready && renderer.url.startsWith('vigil-plugin://')))", 'plugin renderer loaded in isolated process', 15000);
  const livePreferences = await evaluate("window.vigil.plugins.runtimeTest()");
  diag('isolated host runtime', JSON.stringify(livePreferences));
  assert.equal(livePreferences.noSandboxSwitch, false, 'Electron test must run with Chromium sandbox enabled');
  const pluginRenderer = livePreferences.renderers.find(renderer => renderer.id === 'brain-vigil_smoke');
  assert.ok(pluginRenderer, 'isolated Electron process must identify the live plugin WebContents');
  assert.notEqual(pluginRenderer.pid, livePreferences.corePid, 'plugin renderer must use a process separate from Core renderer');
  assert.equal(pluginRenderer.preferences.nodeIntegration, false);
  assert.equal(pluginRenderer.preferences.contextIsolation, true);
  assert.equal(pluginRenderer.preferences.sandbox, true);
  assert.equal(pluginRenderer.preferences.webSecurity, true);
  let pluginDebugPort = livePreferences.pluginDebugPort;
  assert.ok(Number.isInteger(pluginDebugPort) && pluginDebugPort > 0, 'test plugin host must expose its isolated CDP endpoint');
  const guestBoundary = await evaluateGuest(pluginDebugPort, "JSON.stringify({url:location.href,ready:document.readyState,title:document.title,body:document.body?.innerHTML,content:document.querySelector('#plugin-view')?.innerText,markup:document.querySelector('#plugin-view')?.innerHTML,color:document.querySelector('#e2e-plugin-visible')?getComputedStyle(document.querySelector('#e2e-plugin-visible')).color:null,requireType:typeof require,processType:typeof process,fsType:typeof fs,bridge:Object.keys(window.vigilPlugin||{}),api:Object.keys(window.VigilAPI||{}),coreDom:!!document.querySelector('#ta')})");
  diag('isolated renderer DOM probe', guestBoundary);
  const guest = JSON.parse(guestBoundary);
  assert.match(guest.content, /Directory plugin frontend loaded/);
  assert.equal(guest.color, 'rgb(1, 2, 3)', 'plugin CSS must affect only the isolated renderer');
  assert.equal(guest.requireType, 'undefined');
  assert.equal(guest.processType, 'undefined');
  assert.equal(guest.fsType, 'undefined');
  assert.deepEqual(guest.api.sort(), ['get', 'post']);
  assert.equal(guest.coreDom, false);
  assert.deepEqual(guest.bridge.sort(), ['get', 'post', 'registerPage', 'resourceUrl']);
  pass('directory plugin UI runs in an independent Electron renderer without Node or Core DOM');
  const cspProbe = await evaluateGuest(pluginDebugPort, "new Promise(resolve=>{const done=e=>resolve({directive:e.violatedDirective,blocked:e.blockedURI});document.addEventListener('securitypolicyviolation',done,{once:true});const image=new Image();image.src='http://127.0.0.1:9/e2e-probe';setTimeout(()=>resolve(null),1200)})");
  assert.ok(cspProbe && cspProbe.directive.startsWith('img-src') && cspProbe.blocked.startsWith('http://127.0.0.1:9'),
    'plugin CSP must deny access to arbitrary loopback services');
  pass('plugin CSP blocks unauthorized local resource access');
  const denied = await evaluateGuest(pluginDebugPort, "window.vigilPlugin.get('/api/models').then(r=>JSON.stringify(r))");
  assert.equal(JSON.parse(denied).ok, false, 'unscoped Brain API request must be denied by Broker');
  const traversal = await evaluateGuest(pluginDebugPort, "window.vigilPlugin.get('/api/plugin/vigil_smoke/%2e%2e/models').then(r=>JSON.stringify(r))");
  assert.equal(JSON.parse(traversal).ok, false, 'path traversal must be denied by Broker');
  const forged = await evaluateGuest(pluginDebugPort, "window.vigilPlugin.get('/api/plugin/wallpaper/config').then(r=>JSON.stringify(r))");
  assert.equal(JSON.parse(forged).ok, false, 'a plugin renderer cannot borrow another plugin identity or grants');
  pass('plugin Broker rejects undeclared API operations and traversal');
  const crashed = await evaluate("window.vigil.plugins.crashTest('brain-vigil_smoke')");
  assert.equal(crashed.ok, true, 'test host must crash only the isolated plugin renderer');
  await wait(500);
  assert.equal(await evaluate('6 * 7'), 42, 'Core CDP must remain available after plugin renderer crash');
  await waitFor("window.vigil.plugins.runtimeTest().then(r=>r.ok && r.renderers.length===0)", 'crashed isolated host retirement', 15000);
  const recovered = await evaluate("window.vigil.plugins.openUi('brain-vigil_smoke')");
  assert.equal(recovered.ok, true, 'enabled plugin must reopen in a fresh isolated Electron host');
  await wait(500);
  const recoveredRuntime = await evaluate('window.vigil.plugins.runtimeTest()');
  pluginDebugPort = recoveredRuntime.pluginDebugPort;
  assert.ok(Number.isInteger(pluginDebugPort) && pluginDebugPort > 0, 'restarted plugin host must expose its isolated CDP endpoint');
  const crashedRuntime = await evaluate('window.vigil.plugins.runtimeTest()');
  assert.equal(crashedRuntime.renderers.some(renderer => renderer.id === 'brain-vigil_smoke' && renderer.ready), true,
    'crashed plugin renderer must be removed and restarted in the isolated host');
  await evaluate("document.querySelector('#gnav [data-page=\\\"chat\\\"]').click()");
  await submit('AFTER_PLUGIN_CRASH');
  await requestStarted(call => call.userText === 'AFTER_PLUGIN_CRASH');
  await waitFor("document.querySelector('#msgs').innerText.includes('VIGIL_PLUGIN_CALL_OK') && document.querySelector('#sendBtn').getAttribute('aria-busy')==='false'", 'Core tool chat after plugin renderer crash');
  pass('plugin renderer crash leaves Core chat usable and plugin UI recovers');
  await evaluate("document.querySelector('#gnav [data-page=\\\"chat\\\"]').click()");
  await waitFor("document.querySelector('#ta') && document.querySelector('#sendBtn')", 'Core chat before plugin disable');
  const disabled = await evaluate("API.post('/api/plugins/channels',{name:'vigil_smoke',channels:[]}).then(r=>JSON.stringify(r))");
  diag('disable API response', disabled);
  await lifecycleSnapshot('after disable response', debugPort);
  assert.equal(JSON.parse(disabled).ok, true, 'Core-owned plugin disable should complete');
  await wait(250);
  await lifecycleSnapshot('250ms after disable response', debugPort);
  await waitFor("!document.querySelector('#gnav [data-page=\\\"plugin-ui-brain-vigil_smoke\\\"]')", 'disabled plugin page unregister');
  assert.equal(await evaluate('1 + 1'), 2, 'Core remains available after plugin disable');
  assert.deepEqual((await evaluate('window.vigil.plugins.runtimeTest()')).renderers, [], 'disabled plugin host must contain no renderer');
  if (process.argv.includes('--quick-lifecycle')) {
    await submit('AFTER_PLUGIN_DISABLE');
    await requestStarted(call => call.userText === 'AFTER_PLUGIN_DISABLE');
    await waitFor("document.querySelector('#msgs').innerText.includes('CORE_AFTER_PLUGIN_DISABLE_OK') && document.querySelector('#sendBtn').getAttribute('aria-busy')==='false'", 'Fake Provider chat after plugin disable');
    pass('Core completes Fake Provider chat after plugin disable');
  }
  const reenabled = await evaluate("API.post('/api/plugins/channels',{name:'vigil_smoke',channels:['pc']}).then(r=>JSON.stringify(r))");
  assert.equal(JSON.parse(reenabled).ok, true, 'Core-owned plugin enable should complete');
  await waitFor("!!document.querySelector('#gnav [data-page=\\\"plugin-ui-brain-vigil_smoke\\\"]')", 're-enabled plugin page registration');
  pass('plugin UI unregisters on disable and registers again after enable');
  if (process.argv.includes('--quick-lifecycle')) {
    console.log('PASS quick plugin lifecycle smoke total: ' + passCount);
    return;
  }
  await evaluate("document.querySelector('#gnav [data-page=\\\"chat\\\"]').click()");

  // A new Electron process always opens the welcome page. The required chat
  // after renderer recovery remains saved without selecting it automatically.
  await waitFor("document.querySelector('#hero') && !document.querySelector('#hero').hidden", 'cold-start welcome page');
  assert.equal(await evaluate('API.state.chatConversationId'), '', 'cold start must have no active conversation');
  assert.equal(await evaluate("document.querySelector('#msgs').innerText"), '');
  if (captureScreenshots) await screenshot('p6.3-cold-start-home');
  let listResponse = await fetch(brainBase + '/api/conversations?src=pc', {headers:{Authorization:'Bearer ' + token}});
  let listed = await listResponse.json();
  assert.equal(listed.items.length, 1, 'post-crash chat should be the only saved conversation');
  assert.equal(listed.items[0].title, 'AFTER_PLUGIN_CRASH');
  pass('cold start opens a clean welcome page and preserves the post-crash chat without selecting it');

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
  assert.equal(listed.items.length, 11, 'post-crash chat, legacy transcript, and nine saved sessions should be listed');
  const legacyId = listed.items.find(item => item.legacy).id;
  const legacyFirstPrompt = 'LEGACY_FIRST_PROMPT';
  await evaluate("document.querySelector('#newTopicBtn').click()");
  await waitFor("API.state.chatConversationId==='' && !document.querySelector('#hero').hidden", 'return to welcome before renderer refresh');
  await evaluate("document.querySelector('#reloadBtn').click()");
  await waitFor("document.querySelectorAll('[data-session]').length===11 && !document.querySelector('#hero').hidden", 'eleven saved sessions on welcome page');
  assert.equal(await evaluate('API.state.chatConversationId'), '', 'saved sessions must not auto-select the last chat');
  assert.equal(await evaluate("document.querySelector('#msgs').innerText"), '', 'home must not show saved messages or Creator cards');
  if (captureScreenshots) await screenshot('p6.3-ten-history-home');
  await evaluate(`document.querySelector('[data-session="${legacyId}"]').click()`);
  await waitFor(`API.state.chatConversationId===${JSON.stringify(legacyId)} && document.querySelector('#msgs').innerText.includes(${JSON.stringify(legacyFirstPrompt)})`, 'legacy history explicitly opened');
  assert.equal(await evaluate(`document.querySelector('[data-session="${legacyId}"] .tx b')?.textContent || ''`), legacyFirstPrompt.slice(0, 7));
  pass('saved conversations remain visible on welcome; legacy opens only after explicit selection');

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
  assert.equal((await (await fetch(brainBase + '/api/conversations?src=pc', {headers:{Authorization:'Bearer ' + token}})).json()).items.length, 12,
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
  ({ debugPort } = await startElectron(brainBase));
  await waitFor("!!document.querySelector('#gnav [data-page=\\\"plugin-ui-brain-vigil_smoke\\\"]')", 'plugin page restored after app restart');
  await waitFor("document.querySelector('#hero') && !document.querySelector('#hero').hidden", 'cold launch after an existing session');
  assert.equal(await evaluate('API.state.chatConversationId'), '', 'normal app relaunch must return to welcome');
  assert.equal(await evaluate("document.querySelector('#msgs').innerText"), '', 'normal relaunch must not show old messages/cards');
  await waitFor("document.querySelectorAll('[data-session]').length===13", 'all thirteen saved conversations after cold launch');
  assert.equal(await evaluate("document.querySelectorAll('[data-session]').length"), 13, 'all thirteen saved conversations remain listed');
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

  await evaluate("document.querySelector('#gnav [data-page=\\\"plugin-ui-brain-vigil_smoke\\\"]').click()");
  await evaluate("document.querySelector('#view button')?.click()");
  await wait(300);
  const removed = await evaluate("API.post('/api/plugins/uninstall',{name:'vigil_smoke',purge_data:false}).then(r=>JSON.stringify(r))");
  assert.equal(JSON.parse(removed).ok, true, 'Core-owned plugin uninstall should complete while preserving plugin data');
  await waitFor("!document.querySelector('#gnav [data-page=\\\"plugin-ui-brain-vigil_smoke\\\"]')", 'uninstalled plugin page unregister');
  assert.equal((await targets(debugPort)).some(page => String(page.url || '').startsWith('vigil-plugin://')), false,
    'uninstalled plugin renderer must be destroyed');
  assert.equal(await evaluate("Boolean(document.querySelector('#ta') && document.querySelector('#sendBtn'))"), true,
    'Core chat remains available after plugin renderer teardown');
  diag('uninstall completed; Core chat controls are present');
  pass('uninstall revokes UI and preserves normal Core chat');

  diag('submitting post-uninstall chat');
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
  if (error && error.cause) console.error('Failure cause:', error.cause.stack || error.cause);
  try { if (fs.existsSync(path.join(root, 'brain.log'))) console.error('Brain log retained until cleanup: ' + path.join(root, 'brain.log')); } catch (_) {}
  try {
    const electronLog = path.join(root, 'electron.log');
    if (fs.existsSync(electronLog)) console.error('Electron log:\n' + fs.readFileSync(electronLog, 'utf8').slice(-12000));
  } catch (_) {}
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
  if (process.env.VIGIL_KEEP_TEST_ARTIFACTS === '1') console.error('Test artifacts retained: ' + root);
  else try { await safeCleanup(); } catch (e) { console.error('Temp cleanup failed: ' + e.message); process.exitCode = 1; }
});
