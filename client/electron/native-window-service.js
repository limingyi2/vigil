const { app, dialog } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const readline = require('readline');

const CHANNELS = [
  'video-window:pick-exe', 'video-window:profiles', 'video-window:save-profile',
  'video-window:delete-profile', 'video-window:start', 'video-window:bounds',
  'video-window:visible', 'video-window:action',
];

function createNativeWindowService({ ipcMain, getWindow }) {
  let helper = null;
  let helperReady = false;
  let pending = [];
  let nativeState = 'idle';
  let shutdownWaiter = null;
  let helperExitSafe = true;
  const profileFile = path.join(app.getPath('userData'), 'p15-video-apps.json');

  function profiles() {
    try {
      const data = JSON.parse(fs.readFileSync(profileFile, 'utf8'));
      return Array.isArray(data) ? data.filter(p => p && typeof p.id === 'string' && typeof p.exePath === 'string') : [];
    } catch (_) { return []; }
  }
  function saveProfiles(data) {
    fs.mkdirSync(path.dirname(profileFile), { recursive: true });
    fs.writeFileSync(profileFile, JSON.stringify(data, null, 2), 'utf8');
  }
  function trusted(event) {
    const target = getWindow();
    return target && !target.isDestroyed() && event.sender === target.webContents;
  }
  function emit(message) {
    try {
      fs.mkdirSync(path.dirname(profileFile), { recursive: true });
      fs.appendFileSync(path.join(path.dirname(profileFile), 'native-window-host-events.jsonl'), JSON.stringify({ at: new Date().toISOString(), ...message }) + '\n', 'utf8');
    } catch (_) { /* Diagnostics must not break the window host. */ }
    const target = getWindow();
    if (target && !target.isDestroyed()) target.webContents.send('video-window:event', message);
  }
  function send(command) {
    if (!helper || helper.killed || !helper.stdin || !helper.stdin.writable) return false;
    if (!helperReady && command !== 'QUIT') { pending.push(command); return true; }
    helper.stdin.write(command + '\n');
    return true;
  }
  function helperPath() {
    return app.isPackaged
      ? path.join(process.resourcesPath, 'native-window-host', 'native-window-host.exe')
      : path.resolve(__dirname, '..', 'native-window-host', 'build', 'Release', 'native-window-host.exe');
  }
  function startHelper(target) {
    if (helper) return;
    const executable = helperPath();
    if (!fs.existsSync(executable)) throw new Error('原生窗口模块尚未构建：' + executable);
    const bytes = target.getNativeWindowHandle();
    const hwnd = bytes.length >= 8 ? bytes.readBigUInt64LE(0).toString(16) : bytes.readUInt32LE(0).toString(16);
    helper = spawn(executable, [hwnd], { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    readline.createInterface({ input: helper.stdout }).on('line', line => {
      try {
        const event = JSON.parse(line);
        if (event.type === 'state') {
          nativeState = event.state || nativeState;
          helperExitSafe = ['idle', 'detached', 'fallback', 'resident'].includes(event.state) || !event.state;
        } else if (event.type === 'exited' || event.type === 'window-closed-resident' || event.type === 'session-released') {
          nativeState = 'idle';
          helperExitSafe = true;
        } else if (event.type === 'started') {
          nativeState = 'starting';
          helperExitSafe = false;
        } else if (event.type === 'restore-failed' || event.type === 'shutdown-blocked') {
          nativeState = 'restore-failed';
        } else if (event.type === 'shutdown-complete') {
          nativeState = 'idle';
          helperExitSafe = true;
          if (shutdownWaiter) shutdownWaiter(true);
        } else if (event.type === 'start-reused' && nativeState === 'starting') {
          // The following native `state` event is authoritative; do not synthesize a state here.
        }
        if (event.type === 'ready') {
          helperReady = true;
          const queue = pending; pending = [];
          queue.forEach(send);
        }
        emit(event);
      } catch (error) { emit({ type: 'protocol-error', message: String(error), line }); }
    });
    helper.stderr.on('data', bytes => emit({ type: 'helper-stderr', message: bytes.toString('utf8') }));
    helper.on('error', error => emit({ type: 'helper-error', message: error.message }));
    helper.on('exit', (code, signal) => {
      emit({ type: 'helper-exit', code, signal });
      if (shutdownWaiter) shutdownWaiter(helperExitSafe);
      const unsafeExit = !helperExitSafe;
      helper = null; helperReady = false; pending = []; nativeState = unsafeExit ? 'helper-lost' : 'idle';
    });
  }
  function encode(value) { return Buffer.from(String(value || ''), 'utf8').toString('base64'); }
  function boundsCommand(bounds) {
    const dpr = Math.max(0.5, Math.min(4, Number(bounds && bounds.dpr) || 1));
    const n = (key, fallback = 0) => Math.round((Number(bounds && bounds[key]) || fallback) * dpr);
    return `BOUNDS\t${n('x')}\t${n('y')}\t${Math.max(1, n('width', 1))}\t${Math.max(1, n('height', 1))}`;
  }
  function selectedExe(value) {
    const exePath = path.resolve(String(value || ''));
    if (path.extname(exePath).toLowerCase() !== '.exe' || !fs.existsSync(exePath) || !fs.statSync(exePath).isFile()) {
      throw new Error('请选择有效的本地 EXE 文件。');
    }
    return exePath;
  }
  function handle(channel, callback) {
    ipcMain.handle(channel, (event, ...args) => {
      if (!trusted(event)) throw new Error('Unexpected video window IPC sender');
      return callback(...args);
    });
  }

  handle('video-window:pick-exe', async () => {
    const target = getWindow();
    const result = await dialog.showOpenDialog(target, { properties: ['openFile'], filters: [{ name: 'Windows 应用程序', extensions: ['exe'] }] });
    return result.canceled ? null : selectedExe(result.filePaths[0]);
  });
  handle('video-window:profiles', () => profiles());
  handle('video-window:save-profile', profile => {
    const exePath = selectedExe(profile && profile.exePath);
    const data = profiles();
    const id = String(profile && profile.id || crypto.randomUUID());
    const item = { id, name: String(profile && profile.name || path.basename(exePath, '.exe')).trim().slice(0, 80), exePath, args: String(profile && profile.args || '').slice(0, 2048) };
    const index = data.findIndex(p => p.id === id);
    if (index < 0) data.push(item); else data[index] = item;
    saveProfiles(data);
    return data;
  });
  handle('video-window:delete-profile', id => {
    const data = profiles().filter(p => p.id !== String(id || ''));
    saveProfiles(data); return data;
  });
  handle('video-window:start', (profile, bounds) => {
    if (process.platform !== 'win32') throw new Error('原生窗口托管目前仅支持 Windows。');
    const exePath = selectedExe(profile && profile.exePath);
    const target = getWindow();
    startHelper(target);
    emit({ type: 'start-requested', exePath, profileName: String(profile && profile.name || '') });
    if (target && !target.isDestroyed()) target.focus();
    send(`START\t${encode(exePath)}\t${encode(profile && profile.args)}\t${boundsCommand(bounds).split('\t').slice(1).join('\t')}`);
    send('SHOW');
    return { ok: true, exePath, state: nativeState };
  });
  const onBounds = (event, bounds) => { if (trusted(event) && helper) send(boundsCommand(bounds)); };
  const onVisible = (event, visible) => { if (trusted(event) && helper) send(visible ? 'SHOW' : 'HIDE'); };
  const onAction = (event, action) => {
    if (!trusted(event) || !helper) return;
    if (['ATTACH', 'DETACH', 'FOCUS', 'CLOSE_APP', 'STATUS'].includes(action)) send(action);
  };
  ipcMain.on('video-window:bounds', onBounds);
  ipcMain.on('video-window:visible', onVisible);
  ipcMain.on('video-window:action', onAction);

  return {
    hide: () => send('HIDE'),
    shutdown: () => {
      if (!helper) return Promise.resolve(helperExitSafe);
      const child = helper;
      return new Promise(resolve => {
        let settled = false;
        let timeout = null;
        const finish = safe => {
          if (settled) return;
          settled = true;
          if (timeout) clearTimeout(timeout);
          if (shutdownWaiter === finish) shutdownWaiter = null;
          resolve(!!safe);
        };
        shutdownWaiter = finish;
        child.once('exit', () => finish(helperExitSafe));
        timeout = setTimeout(() => finish(false), 10000);
        if (!send('QUIT')) finish(helperExitSafe);
      });
    },
    dispose: () => {
      CHANNELS.forEach(channel => { try { ipcMain.removeHandler(channel); } catch (_) {} });
      ipcMain.removeListener('video-window:bounds', onBounds);
      ipcMain.removeListener('video-window:visible', onVisible);
      ipcMain.removeListener('video-window:action', onAction);
    },
  };
}

module.exports = { createNativeWindowService };
