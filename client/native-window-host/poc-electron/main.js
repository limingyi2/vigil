const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const readline = require('readline');

const testRoot = process.env.P15_TEST_ROOT || path.join(process.env.LOCALAPPDATA || app.getPath('temp'), 'Vigil', 'P15-WindowHost-PoC');
const profilePath = path.join(testRoot, 'electron-user-data');
fs.mkdirSync(profilePath, { recursive: true });
app.setPath('userData', profilePath);

let win = null;
let helper = null;
let helperReady = false;
let pendingCommands = [];
let quitStarted = false;
let lifecycleSequence = [];
let lifecycleIndex = 0;
let lifecycleCloseTimer = null;
let lifecycleTimeoutTimer = null;
let lifecyclePhase = 'initial';
const duplicateStartingSteps = new Set();
const duplicateAttachedSteps = new Set();
const repeatedDetachSteps = new Set();
const helperPath = path.resolve(process.env.P15_TEST_HELPER_PATH || path.join(__dirname, '..', 'build', 'Release', 'native-window-host.exe'));

function emit(event) {
  if (win && !win.isDestroyed()) win.webContents.send('poc:event', event);
  if (event && event.type) {
    fs.mkdirSync(testRoot, { recursive: true });
    fs.appendFileSync(path.join(testRoot, 'native-window-host-events.jsonl'), JSON.stringify({ at: new Date().toISOString(), ...event }) + '\n');
  }
  if (!event || !event.type || !lifecycleSequence.length) return;
  if (event.type === 'ready') startLifecycleStep();
  if (event.type === 'started' && process.env.P15_TEST_DUPLICATE_STARTS === '1' && !duplicateStartingSteps.has(lifecycleIndex)) {
    duplicateStartingSteps.add(lifecycleIndex);
    for (let i = 0; i < 3; i++) send(startCommand(lifecycleSequence[lifecycleIndex]));
    emit({ type: 'test-duplicate-starts-issued', phase: 'starting', index: lifecycleIndex, count: 3 });
  }
  if (event.type === 'attached' && lifecycleCloseTimer === null) {
    if (lifecyclePhase === 'initial') {
      lifecyclePhase = 'detaching';
      send('FOCUS');
      if (process.env.P15_TEST_DUPLICATE_STARTS === '1' && !duplicateAttachedSteps.has(lifecycleIndex)) {
        duplicateAttachedSteps.add(lifecycleIndex);
        for (let i = 0; i < 3; i++) send(startCommand(lifecycleSequence[lifecycleIndex]));
        emit({ type: 'test-duplicate-starts-issued', phase: 'attached', index: lifecycleIndex, count: 3 });
      }
      if (process.env.P15_TEST_QUIT_WITH_FAULT === '1') {
        lifecyclePhase = 'quit-fault-arming';
        send('TEST_FAIL_RESTORE_ONCE\tset-parent');
      } else if (process.env.P15_TEST_FAULT_STAGE &&
          (process.env.P15_TEST_FAULT_INDEX === undefined || Number(process.env.P15_TEST_FAULT_INDEX) === lifecycleIndex)) {
        lifecyclePhase = 'fault-arming';
        send(`TEST_FAIL_RESTORE_ONCE\t${process.env.P15_TEST_FAULT_STAGE}`);
      } else {
        const detachDelay = Math.max(500, Math.min(30000, Number(process.env.P15_TEST_DETACH_DELAY_MS) || 1200));
        lifecycleCloseTimer = setTimeout(() => { lifecycleCloseTimer = null; send('DETACH'); }, detachDelay);
      }
    } else if (lifecyclePhase === 'reattaching') {
      lifecyclePhase = 'closing';
      lifecycleCloseTimer = setTimeout(() => { lifecycleCloseTimer = null; send('CLOSE_APP'); }, 1500);
    }
    clearTimeout(lifecycleTimeoutTimer);
    lifecycleTimeoutTimer = setTimeout(() => {
      emit({ type: 'lifecycle-step-timeout', index: lifecycleIndex, exePath: lifecycleSequence[lifecycleIndex], phase: lifecyclePhase });
      lifecycleSequence = [];
    }, 20000);
  }
  if (event.type === 'test-fault-armed') {
    if (lifecyclePhase === 'fault-arming') {
      lifecyclePhase = 'detaching';
      lifecycleCloseTimer = setTimeout(() => { lifecycleCloseTimer = null; send('DETACH'); }, 100);
    } else if (lifecyclePhase === 'quit-fault-arming') {
      lifecyclePhase = 'quit-fault-pending';
      setTimeout(() => app.quit(), 100);
    }
  }
  if (event.type === 'start-reused') emit({ type: 'test-start-reused', phase: lifecyclePhase, index: lifecycleIndex, pid: event.pid, hwnd: event.hwnd });
  if (event.type === 'detached' && lifecyclePhase === 'detaching') {
    lifecyclePhase = 'reattaching';
    if (process.env.P15_TEST_REPEAT_DETACH === '1' && !repeatedDetachSteps.has(lifecycleIndex)) {
      repeatedDetachSteps.add(lifecycleIndex);
      send('DETACH');
      emit({ type: 'test-repeat-detach-issued', index: lifecycleIndex });
    }
    clearTimeout(lifecycleCloseTimer);
    lifecycleCloseTimer = setTimeout(() => {
      lifecycleCloseTimer = null;
      send(startCommand(lifecycleSequence[lifecycleIndex])); send('SHOW');
    }, 700);
  }
  if (event.type === 'exited' || event.type === 'window-closed-resident') {
    clearTimeout(lifecycleCloseTimer); lifecycleCloseTimer = null;
    clearTimeout(lifecycleTimeoutTimer); lifecycleTimeoutTimer = null;
    lifecycleIndex += 1;
    lifecyclePhase = 'initial';
    setTimeout(startLifecycleStep, 700);
  }
}

function startLifecycleStep() {
  if (!lifecycleSequence.length || !helperReady || !win || win.isDestroyed()) return;
  if (lifecycleIndex >= lifecycleSequence.length) {
    clearTimeout(lifecycleCloseTimer); lifecycleCloseTimer = null;
    clearTimeout(lifecycleTimeoutTimer); lifecycleTimeoutTimer = null;
    emit({ type: 'lifecycle-sequence-complete', steps: lifecycleSequence.length });
    lifecycleSequence = [];
    if (process.env.P15_TEST_CLOSE_ON_COMPLETE === '1') setTimeout(() => { if (win && !win.isDestroyed()) win.close(); }, 300);
    return;
  }
  const exePath = path.resolve(lifecycleSequence[lifecycleIndex]);
  lifecyclePhase = 'initial';
  emit({ type: 'lifecycle-step-start', index: lifecycleIndex, exePath });
  send(startCommand(exePath)); send('SHOW');
}

function startCommand(exePath) {
  return `START\t${encode(path.resolve(exePath))}\t\t24\t100\t1000\t600`;
}

function send(command) {
  if (!helper || helper.killed || !helper.stdin || !helper.stdin.writable) return false;
  if (!helperReady && command !== 'QUIT') { pendingCommands.push(command); return true; }
  helper.stdin.write(command + '\n');
  return true;
}

function startHelper() {
  if (helper) return;
  if (!fs.existsSync(helperPath)) throw new Error('C++ native host was not built: ' + helperPath);
  const handle = win.getNativeWindowHandle();
  const hwnd = handle.length >= 8 ? handle.readBigUInt64LE(0).toString(16) : handle.readUInt32LE(0).toString(16);
  helper = spawn(helperPath, [hwnd], { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
  const lines = readline.createInterface({ input: helper.stdout });
  lines.on('line', line => {
    try {
      const event = JSON.parse(line);
      if (event.type === 'ready') {
        helperReady = true;
        const queued = pendingCommands; pendingCommands = [];
        for (const next of queued) send(next);
      }
      emit(event);
    } catch (error) { emit({ type: 'protocol-error', message: String(error), line }); }
  });
  helper.stderr.on('data', bytes => emit({ type: 'helper-stderr', message: bytes.toString('utf8') }));
  helper.on('error', error => emit({ type: 'helper-process-error', message: error.message }));
  helper.on('exit', (code, signal) => {
    emit({ type: 'helper-exit', code, signal });
    helper = null;
    helperReady = false;
  });
}

function encode(value) { return Buffer.from(String(value || ''), 'utf8').toString('base64'); }
function boundsCommand(bounds) {
  const dpr = Math.max(0.5, Math.min(4, Number(bounds && bounds.dpr) || 1));
  const x = Math.round((Number(bounds && bounds.x) || 0) * dpr);
  const y = Math.round((Number(bounds && bounds.y) || 0) * dpr);
  const width = Math.max(1, Math.round((Number(bounds && bounds.width) || 1) * dpr));
  const height = Math.max(1, Math.round((Number(bounds && bounds.height) || 1) * dpr));
  return `BOUNDS\t${x}\t${y}\t${width}\t${height}`;
}

ipcMain.handle('poc:pick-exe', async event => {
  if (!win || event.sender !== win.webContents) throw new Error('Unexpected sender');
  const selected = await dialog.showOpenDialog(win, { properties: ['openFile'], filters: [{ name: 'Windows applications', extensions: ['exe'] }] });
  return selected.canceled ? null : selected.filePaths[0];
});
ipcMain.handle('poc:start', (event, config) => {
  if (!win || event.sender !== win.webContents) throw new Error('Unexpected sender');
  const exePath = path.resolve(String(config && config.exePath || ''));
  if (path.extname(exePath).toLowerCase() !== '.exe' || !fs.existsSync(exePath) || !fs.statSync(exePath).isFile()) throw new Error('Choose a local EXE file first.');
  startHelper();
  const command = `START\t${encode(exePath)}\t${encode(config && config.arguments)}\t${boundsCommand(config && config.bounds).split('\t').slice(1).join('\t')}`;
  send(command);
  send('SHOW');
  return { ok: true, exePath };
});
ipcMain.on('poc:bounds', (event, bounds) => {
  if (!win || event.sender !== win.webContents || !helper) return;
  send(boundsCommand(bounds));
});
ipcMain.on('poc:visible', (event, visible) => {
  if (!win || event.sender !== win.webContents || !helper) return;
  send(visible ? 'SHOW' : 'HIDE');
});
ipcMain.on('poc:action', (event, action) => {
  if (!win || event.sender !== win.webContents) return;
  if (action === 'CLOSE_WINDOW') { win.close(); return; }
  if (!helper) return;
  if (['ATTACH', 'DETACH', 'FOCUS', 'CLOSE_APP', 'QUIT'].includes(action)) send(action);
});
ipcMain.on('poc:test-fault', (event, stage) => {
  if (!win || event.sender !== win.webContents || process.env.VIGIL_P15_TEST_FAULT_INJECTION !== '1' || !helper) return;
  send(`TEST_FAIL_RESTORE_ONCE\t${String(stage || '').slice(0, 32)}`);
});

function createWindow() {
  win = new BrowserWindow({
    width: 1180, height: 820, minWidth: 920, minHeight: 620,
    frame: false, backgroundColor: '#f5f4f0', show: false,
    alwaysOnTop: process.env.P15_TEST_ALWAYS_ON_TOP === '1',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });
  win.loadFile(path.join(__dirname, 'index.html'));
  win.webContents.on('did-fail-load', (_event, code, description, url) => emit({ type: 'renderer-load-error', code, description, url }));
  win.webContents.on('console-message', (_event, details) => emit({ type: 'renderer-console', level: details.level, message: details.message, line: details.lineNumber }));
  win.webContents.once('did-finish-load', () => {
    if (process.env.P15_TEST_EXE) win.webContents.send('poc:auto-start', { exePath: process.env.P15_TEST_EXE, arguments: process.env.P15_TEST_ARGS || '' });
    if (lifecycleSequence.length) startHelper();
  });
  win.once('ready-to-show', () => win.show());
  win.on('closed', () => { win = null; });
}

app.whenReady().then(createWindow);
try {
  const sequence = JSON.parse(process.env.P15_TEST_SEQUENCE || '[]');
  if (Array.isArray(sequence) && sequence.length >= 1 && sequence.every(value => typeof value === 'string')) lifecycleSequence = sequence;
} catch (_) { /* A malformed optional smoke-test sequence must not affect normal PoC use. */ }
app.on('window-all-closed', () => app.quit());
app.on('before-quit', event => {
  if (quitStarted || !helper) return;
  event.preventDefault();
  quitStarted = true;
  const child = helper;
  send('QUIT');
  const done = () => app.quit();
  child.once('exit', done);
});
