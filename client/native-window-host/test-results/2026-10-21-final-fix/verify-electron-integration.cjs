const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const WebSocket = require('ws');

const root = __dirname;
const client = path.resolve(root, '..', '..', '..');
const electron = path.join(client, 'node_modules', 'electron', 'dist', 'electron.exe');
const entry = path.join(root, 'electron-test-entry.cjs');
const port = 9395;
const userData = path.join(root, 'electron-integration-user-data');
const resultFile = path.join(root, 'electron-integration-result.json');
const profileA = { id: 'p15-final-a', name: 'Character Map', exePath: path.join(process.env.WINDIR || 'C:\\Windows', 'System32', 'charmap.exe'), args: '' };
const profileB = { id: 'p15-final-b', name: 'System Information', exePath: path.join(process.env.WINDIR || 'C:\\Windows', 'System32', 'msinfo32.exe'), args: '' };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const child = spawn(electron, [entry, `--remote-debugging-port=${port}`], {
  cwd: client,
  env: { ...process.env, VIGIL_USER_DATA: userData, VIGIL_TEST_BRAIN: 'http://127.0.0.1:1', VIGIL_TEST_NO_UPDATES: '1', P15_ELECTRON_TEST_QUIT_MS: '40000' },
  stdio: ['ignore', fs.openSync(path.join(root, 'electron-integration.stdout.log'), 'w'), fs.openSync(path.join(root, 'electron-integration.stderr.log'), 'w')],
  windowsHide: false,
});

async function waitFor(predicate, description, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await predicate();
    if (value) return value;
    await delay(150);
  }
  throw new Error(`Timed out: ${description}`);
}

(async () => {
  const target = await waitFor(async () => {
    try { return (await fetch(`http://127.0.0.1:${port}/json/list`).then(response => response.json())).find(item => item.type === 'page'); }
    catch (_) { return null; }
  }, 'Vigil renderer DevTools target');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.once('open', resolve); ws.once('error', reject); });
  let requestId = 0;
  const pending = new Map();
  ws.on('message', bytes => {
    const message = JSON.parse(bytes);
    if (!message.id || !pending.has(message.id)) return;
    const done = pending.get(message.id); pending.delete(message.id); done(message);
  });
  const evaluate = expression => new Promise((resolve, reject) => {
    const id = ++requestId;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP evaluation timed out')); }, 10000);
    pending.set(id, message => {
      clearTimeout(timer);
      if (message.error) return reject(new Error(message.error.message));
      const result = message.result && message.result.result;
      if (message.result && message.result.exceptionDetails) return reject(new Error(message.result.exceptionDetails.exception?.description || message.result.exceptionDetails.text));
      resolve(result && result.value);
    });
    ws.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: { expression, returnByValue: true, awaitPromise: true } }));
  });
  const json = value => JSON.stringify(value);
  const readEvents = () => evaluate('JSON.stringify(window.__p15FinalEvents)') .then(JSON.parse);

  await waitFor(() => evaluate("[...document.querySelectorAll('.gbtn')].some(button=>button.textContent.includes('视频工作台'))"), 'Vigil UI ready');
  await evaluate('window.__p15FinalEvents=[]; window.vigil.videoWindow.onEvent(event=>window.__p15FinalEvents.push(event)); true');
  for (const profile of [profileA, profileB]) await evaluate(`window.vigil.videoWindow.saveProfile(${json(profile)})`);
  await evaluate("[...document.querySelectorAll('.gbtn')].find(button=>button.textContent.includes('视频工作台')).click(); 'opened'");
  await waitFor(() => evaluate("!!document.querySelector('#videoHostSlot')"), 'video workbench render');
  const layout = await evaluate("JSON.stringify((()=>{const host=document.querySelector('.video-host-frame').getBoundingClientRect();const agent=document.querySelector('.video-ai-workspace').getBoundingClientRect();return {hostHeight:Math.round(host.height),agentHeight:Math.round(agent.height),agentPresent:!!document.querySelector('.video-ai-workspace')}})())").then(JSON.parse);
  assert(layout.agentPresent && layout.hostHeight > layout.agentHeight, 'The confirmed top software and lower reserved workspace layout must remain present');
  const result = { layout, launches: [] };

  async function launch(profile, exerciseRepeat = false) {
    const before = await readEvents();
    const base = before.length;
    await evaluate(`document.querySelector('#videoProfileSelect').value=${json(profile.id)};document.querySelector('#videoProfileSelect').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#videoStart').click()`);
    if (exerciseRepeat) {
      await delay(100);
      await evaluate("document.querySelector('#videoStart').click(); true");
    }
    const attached = await waitFor(async () => {
      const events = await readEvents();
      return events.slice(base).find(event => event.type === 'attached') || null;
    }, `attached ${profile.name}`, 30000);
    await evaluate("document.querySelector('#videoStart').click(); true");
    const events = await waitFor(async () => {
      const rows = await readEvents();
      return rows.slice(base).some(event => event.type === 'start-reused') ? rows.slice(base) : null;
    }, `repeat START reuse for ${profile.name}`, 10000);
    let seenAttached = false;
    for (const event of events) {
      if (event.type === 'state' && event.state === 'attached') seenAttached = true;
      if (seenAttached && event.type === 'state' && event.state === 'starting') throw new Error(`${profile.name} regressed to starting after attached`);
    }
    const launchCount = events.filter(event => event.type === 'started').length;
    assert.equal(launchCount, 1, `${profile.name} should create one process for repeated START`);
    const state = await evaluate("document.querySelector('#videoStatus').textContent");
    result.launches.push({ name: profile.name, pid: attached.pid, hwnd: attached.hwnd, state, launchCount, reused: events.filter(event => event.type === 'start-reused').length });
    if (profile.id === profileA.id && exerciseRepeat) {
      await evaluate("document.querySelector('#videoDetach').click(); true");
      await waitFor(async () => (await readEvents()).some(event => event.type === 'detached' && event.restored), 'successful UI detach');
      await evaluate("document.querySelector('#videoStart').click(); true");
      await waitFor(async () => (await readEvents()).filter(event => event.type === 'attached' && event.pid === attached.pid).length >= 2, 'UI reattach to same running PID');
    }
    await evaluate('window.vigil.videoWindow.action("CLOSE_APP"); true');
    await waitFor(async () => (await readEvents()).slice(base).some(event => event.type === 'exited' || event.type === 'window-closed-resident'), `graceful close ${profile.name}`, 20000);
  }

  await launch(profileA, true);
  await launch(profileB);
  await launch(profileA);
  await evaluate('window.vigil.win.close(); true');
  ws.close();
  const exited = await new Promise(resolve => {
    if (child.exitCode !== null) return resolve(true);
    child.once('exit', () => resolve(true));
    setTimeout(() => resolve(false), 45000);
  });
  assert(exited, 'Vigil did not quit safely after the isolated integration run');
  result.appExitCode = child.exitCode;
  result.helperEventsPath = path.join(userData, 'native-window-host-events.jsonl');
  fs.writeFileSync(resultFile, JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ result: 'PASS', ...result }, null, 2));
})().catch(async error => {
  console.error(error.stack || error);
  process.exitCode = 1;
  // The isolated wrapper asks Electron to quit after 40 seconds; it will still honor the native restore guard.
});
