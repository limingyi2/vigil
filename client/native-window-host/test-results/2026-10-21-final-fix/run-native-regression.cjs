const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

const clientRoot = path.resolve(__dirname, '..', '..', '..');
const electron = path.join(clientRoot, 'node_modules', 'electron', 'dist', 'electron.exe');
const pocMain = path.join(clientRoot, 'native-window-host', 'poc-electron', 'main.js');
const mode = process.argv[2] || 'lifecycle';
const testRoot = path.join(__dirname, mode);
const eventFile = path.join(testRoot, 'native-window-host-events.jsonl');
const charmap = path.join(process.env.WINDIR || 'C:\\Windows', 'System32', 'charmap.exe');
const taskmgr = path.join(process.env.WINDIR || 'C:\\Windows', 'System32', 'taskmgr.exe');
const secondApp = process.env.P15_TEST_SECOND_EXE || taskmgr;
const isQuitFault = mode === 'quit-fault';
const sequence = isQuitFault ? [charmap] : [charmap, secondApp, charmap];
const debugPort = isQuitFault ? '9392' : mode === 'lifecycle' ? '9391' : mode === 'lifecycle-msinfo' ? '9394' : '9393';

if (!fs.existsSync(electron)) throw new Error(`Electron executable not found: ${electron}`);
fs.mkdirSync(testRoot, { recursive: true });
try { fs.rmSync(eventFile); } catch (_) {}

const env = {
  ...process.env,
  P15_TEST_ROOT: testRoot,
  P15_TEST_HELPER_PATH: process.env.P15_TEST_HELPER_PATH || path.join(__dirname, 'build-latest', 'native-window-host-latest.exe'),
  P15_TEST_SEQUENCE: JSON.stringify(sequence),
  P15_TEST_DUPLICATE_STARTS: isQuitFault ? '' : '1',
  P15_TEST_REPEAT_DETACH: isQuitFault ? '' : '1',
  P15_TEST_FAULT_STAGE: isQuitFault ? '' : (process.env.P15_TEST_FAULT_STAGE || 'set-parent'),
  P15_TEST_FAULT_INDEX: '0',
  P15_TEST_QUIT_WITH_FAULT: isQuitFault ? '1' : '',
  P15_TEST_CLOSE_ON_COMPLETE: isQuitFault ? '' : '1',
  VIGIL_P15_TEST_FAULT_INJECTION: '1',
};
const child = spawn(electron, [pocMain, `--remote-debugging-port=${debugPort}`], {
  cwd: clientRoot,
  env,
  stdio: ['ignore', fs.openSync(path.join(testRoot, 'electron.stdout.log'), 'w'), fs.openSync(path.join(testRoot, 'electron.stderr.log'), 'w')],
  windowsHide: false,
});

function readEvents() {
  if (!fs.existsSync(eventFile)) return [];
  return fs.readFileSync(eventFile, 'utf8').split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line));
}

function waitForExit(timeoutMs) {
  return new Promise(resolve => {
    if (child.exitCode !== null) return resolve(true);
    const timer = setTimeout(() => { child.removeListener('exit', onExit); resolve(false); }, timeoutMs);
    const onExit = () => { clearTimeout(timer); resolve(true); };
    child.once('exit', onExit);
  });
}

async function requestGracefulClose() {
  try {
    const targets = await fetch(`http://127.0.0.1:${debugPort}/json/list`).then(r => r.json());
    const page = targets.find(item => item.type === 'page');
    if (!page) return;
    const WebSocket = require('ws');
    const ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.once('open', resolve); ws.once('error', reject); });
    ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression: "window.p15Host.action('CLOSE_WINDOW')" } }));
    setTimeout(() => ws.close(), 250);
  } catch (_) {}
}

(async () => {
  const exited = await waitForExit(90000);
  if (!exited) {
    await requestGracefulClose();
    const recovered = await waitForExit(15000);
    if (!recovered) throw new Error(`Timed out while preserving the test host. Test process ${child.pid} remains alive; no forced termination was used.`);
    throw new Error('Test run exceeded 90 seconds and was closed through the PoC UI.');
  }
  if (child.exitCode !== 0) throw new Error(`Electron PoC exited with code ${child.exitCode}; see ${testRoot}`);
  const events = readEvents();
  assert(events.length > 0, `No helper events were written to ${eventFile}`);

  if (isQuitFault) {
    assert(events.some(e => e.type === 'test-fault-armed' && e.stage === 'set-parent'), 'SetParent restoration fault was not armed');
    assert(events.some(e => e.type === 'restore-failed' && e.stage === 'set-parent' && e.valid), 'Expected a retained live HWND after injected SetParent failure');
    assert(events.some(e => e.type === 'shutdown-blocked'), 'The Electron PoC must remain open while restore fails');
    assert(events.some(e => e.type === 'restore-complete'), 'Automatic retry did not restore the window');
    assert(events.some(e => e.type === 'shutdown-complete'), 'Shutdown did not wait for restoration');
  } else {
    assert(events.some(e => e.type === 'lifecycle-sequence-complete' && e.steps === 3), 'A→B→A lifecycle did not finish');
    assert.equal(events.filter(e => e.type === 'started').length, 3, 'Duplicate START created an additional process');
    assert(events.filter(e => e.type === 'test-duplicate-starts-issued').length >= 6, 'Duplicate START probes did not run both during startup and while attached');
    assert(events.filter(e => e.type === 'start-reused').length >= 6, 'Repeated START did not reuse the existing session');
    assert(events.some(e => e.type === 'restore-failed' && e.stage === env.P15_TEST_FAULT_STAGE && e.valid), `Fault injection did not preserve the live HWND at ${env.P15_TEST_FAULT_STAGE}`);
    assert(events.some(e => e.type === 'restore-complete'), 'Restore retry did not complete');
    assert(events.filter(e => e.type === 'detached' && e.restored).length >= 3, 'Expected successful detach/restore for all three launch sessions');
    assert(events.filter(e => e.type === 'detached' && e.alreadyDetached).length >= 1, 'Repeated detach did not report already-detached state');
    const states = events.filter(e => e.type === 'state');
    let attached = false;
    for (const event of states) {
      if (event.state === 'attached') attached = true;
      if (attached && event.state === 'starting') throw new Error('State regressed to starting while the same HWND remained attached');
      if (event.state === 'idle') attached = false;
    }
  }
  const summary = { mode, pid: child.pid, exitCode: child.exitCode, events: events.length, eventFile, sequence };
  fs.writeFileSync(path.join(testRoot, 'summary.json'), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify(summary, null, 2));
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
