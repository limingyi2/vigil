// Start a real installed historical/current Electron + Brain pair and verify
// the isolated profile through its authenticated renderer bridge.
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const { spawn, execFileSync } = require('child_process');
const WS = require('ws');

const [install, phase, conversationId = ''] = process.argv.slice(2);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let app, ws, next = 0;
const pending = new Map();

async function evaluate(expression) {
  return new Promise((resolve, reject) => {
    const id = ++next;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: {
      expression, awaitPromise: true, returnByValue: true
    } }));
  });
}

async function run() {
  const executable = fs.readdirSync(install).find(name => /^Kestrel\.exe$/i.test(name)) ||
    fs.readdirSync(install).find(name => /^Vigil\.exe$/i.test(name));
  assert.ok(executable, 'Installed Electron executable was not found');
  app = spawn(path.join(install, executable), ['--remote-debugging-port=0'], {
    env: process.env, windowsHide: true, stdio: 'ignore'
  });
  let connected = false;
  for (let i = 0; i < 480; i++) {
    if (app.exitCode != null) throw Error(`Installed Electron exited ${app.exitCode}`);
    try {
      const active = path.join(process.env.VIGIL_USER_DATA, 'DevToolsActivePort');
      const port = Number(fs.readFileSync(active, 'utf8').split(/\r?\n/)[0]);
      const tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = tabs.find(item => item.type === 'page' && item.url.includes('index.html') && !item.url.includes('settings'));
      if (page) {
        ws = new WS(page.webSocketDebuggerUrl);
        await new Promise((resolve, reject) => { ws.once('open', resolve); ws.once('error', reject); });
        connected = true;
        break;
      }
    } catch (_) { }
    await pause(250);
  }
  assert.ok(connected, 'Installed Electron renderer did not become available');
  ws.on('message', raw => {
    const message = JSON.parse(raw);
    const item = pending.get(message.id);
    if (!item) return;
    pending.delete(message.id);
    if (message.error || message.result?.exceptionDetails) {
      item.reject(Error(message.error?.message || message.result?.exceptionDetails?.text || 'renderer evaluation failed'));
    } else item.resolve(message.result?.result?.value);
  });
  let bridge = false;
  for (let i = 0; i < 100; i++) {
    try { bridge = await evaluate('!!window.vigil?.sidecar'); } catch (_) { }
    if (bridge) break;
    await pause(100);
  }
  assert.ok(bridge, 'Installed preload bridge was not available');
  let health;
  for (let i = 0; i < 480; i++) {
    health = await evaluate('vigil.sidecar.status()');
    if (health.status === 'ready' || health.status === 'failed') break;
    await pause(250);
  }
  if (health.status !== 'ready') {
    const diagnostic = {
      status: health.status, failureKind: health.failureKind, lastErr: health.lastErr,
      lastFailure: health.lastFailure, pid: health.pid, exe: health.exe, home: health.home,
      diagnosis: health.diagnosis, startupPhases: health.startupPhases,
      logTail: (health.logTail || []).map(line => String(line)
        .replace(/((?:api[_-]?key|token|password|secret|authorization)\s*[:=]\s*)[^\s,;]+/gi, '$1[redacted]')
        .replace(/Bearer\s+[^\s]+/gi, 'Bearer [redacted]')),
    };
    console.error('HISTORICAL_BRAIN_STARTUP_DIAGNOSTIC ' + JSON.stringify(diagnostic));
  }
  assert.equal(health.status, 'ready', health.lastErr || JSON.stringify(health));
  assert.ok(health.exe.startsWith(install), 'Brain did not start from the installed application');
  const home = health.home;
  if (phase === 'old-install') {
    const created = await evaluate("vigil.api('POST','/api/conversations',{src:'pc'})");
    assert.equal(created.status, 201, JSON.stringify(created.json));
    const id = created.json?.item?.id;
    assert.ok(id, 'Historical Brain did not create a user conversation');
    console.log(JSON.stringify({ result: 'passed', phase, home, conversation_id: id, brain_health: health.status }));
  } else if (phase === 'verify') {
    const plugins = await evaluate("vigil.api('GET','/api/plugins')");
    assert.equal(plugins.status, 200);
    for (const name of ['websearch', 'kb']) {
      assert.ok((plugins.json.plugins || []).some(plugin => plugin.name === name),
        `Historical plugin was not migrated and loaded: ${name}`);
    }
    console.log(JSON.stringify({ result: 'passed', phase, home, conversation_id: conversationId,
      brain_health: health.status, conversation_preserved: true, plugins_loaded: ['websearch', 'kb'] }));
  } else if (phase === 'uninstall-websearch') {
    const removed = await evaluate("vigil.api('POST','/api/plugins/uninstall',{name:'websearch'})");
    assert.equal(removed.status, 200, JSON.stringify(removed.json));
    assert.equal(removed.json?.ok, true, JSON.stringify(removed.json));
    console.log(JSON.stringify({ result: 'passed', phase, home, plugin_uninstalled: 'websearch' }));
  } else if (phase === 'verify-uninstalled') {
    const plugins = await evaluate("vigil.api('GET','/api/plugins')");
    assert.equal(plugins.status, 200);
    assert.ok(!(plugins.json.plugins || []).some(plugin => plugin.name === 'websearch'),
      'Uninstalled historical plugin reappeared after upgrade');
    assert.ok((plugins.json.plugins || []).some(plugin => plugin.name === 'kb'),
      'Unrelated migrated plugin disappeared');
    console.log(JSON.stringify({ result: 'passed', phase, home, websearch_stayed_uninstalled: true }));
  } else throw Error('Unknown lifecycle phase: ' + phase);
}

(async () => {
  try { await run(); }
  finally {
    if (ws) ws.close();
    if (app?.pid) {
      try { execFileSync('taskkill', ['/PID', String(app.pid), '/T', '/F'], { stdio: 'ignore' }); } catch (_) { }
    }
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
