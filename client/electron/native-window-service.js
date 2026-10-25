const { app, dialog, screen } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const readline = require('readline');
const { normalizeDipBounds } = require('./native-window-bounds');

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
  let hosted = null;                 // {profileName, exePath, pid} —— 托管的到底是哪个软件
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
          hosted = null;
        } else if (event.type === 'started') {
          nativeState = 'starting';
          helperExitSafe = false;
          if (event.exePath || event.profileName) {
            hosted = { profileName: String(event.profileName || hosted?.profileName || ''),
                       exePath: String(event.exePath || hosted?.exePath || ''),
                       pid: event.pid || hosted?.pid || null };
          }
        } else if (event.type === 'attached' || event.type === 'start-reused' || event.type === 'recreated') {
          if (event.exePath || event.profileName || event.pid) {
            hosted = { profileName: String(event.profileName || hosted?.profileName || ''),
                       exePath: String(event.exePath || hosted?.exePath || ''),
                       pid: event.pid || hosted?.pid || null };
          }
        } else if (event.type === 'detached' || event.type === 'fallback') {
          hosted = null;
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
  // 目标窗口是独立顶层窗口，跟着绑定区域走，所以要的是屏幕物理像素：
  // 渲染层给的是 CSS 坐标（相对内容区），这里加上内容区原点再按显示器缩放换算。
  let lastBounds = null;
  let boundsTimer = null;
  function boundsCommand(bounds) {
    const target = getWindow();
    if (!target || target.isDestroyed()) return null;
    const content = target.getContentBounds();
    const dip = normalizeDipBounds(bounds, content);
    if (!dip) {
      console.warn('[video-window] Ignoring non-finite bounds update');
      return null;
    }
    let rect;
    try { rect = screen.dipToScreenRect(target, dip); }
    catch (error) {
      console.warn('[video-window] Could not convert bounds:', String(error && error.message || error).slice(0, 160));
      return null;
    }
    if (![rect && rect.x, rect && rect.y, rect && rect.width, rect && rect.height].every(Number.isFinite)) {
      console.warn('[video-window] Ignoring invalid screen bounds result');
      return null;
    }
    return `BOUNDS\t${Math.round(rect.x)}\t${Math.round(rect.y)}\t${Math.max(1, Math.round(rect.width))}\t${Math.max(1, Math.round(rect.height))}`;
  }
  function pushBounds() {
    if (!helper || !lastBounds) return;
    try {
      const command = boundsCommand(lastBounds);
      if (command) send(command);
    } catch (error) {
      console.warn('[video-window] Bounds update failed:', String(error && error.message || error).slice(0, 160));
    }
  }
  function scheduleBoundsPush() {
    if (boundsTimer) return;
    boundsTimer = setTimeout(() => { boundsTimer = null; pushBounds(); }, 60);
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
    lastBounds = bounds || lastBounds;
    const rectFields = (boundsCommand(bounds) || 'BOUNDS\t0\t0\t1\t1').split('\t').slice(1);
    send(`START\t${encode(exePath)}\t${encode(profile && profile.args)}\t${rectFields.join('\t')}`);
    send('SHOW');
    return { ok: true, exePath, state: nativeState };
  });
  const onBounds = (event, bounds) => {
    if (!trusted(event)) return;
    lastBounds = bounds;
    if (!helper) return;
    try {
      const command = boundsCommand(bounds);
      if (command) send(command);
    } catch (error) {
      console.warn('[video-window] Bounds update failed:', String(error && error.message || error).slice(0, 160));
    }
  };
  const onVisible = (event, visible) => { if (trusted(event) && helper) send(visible ? 'SHOW' : 'HIDE'); };
  const onAction = (event, action) => {
    if (!trusted(event) || !helper) return;
    if (['ATTACH', 'DETACH', 'FOCUS', 'CLOSE_APP', 'STATUS'].includes(action)) send(action);
  };
  ipcMain.on('video-window:bounds', onBounds);
  ipcMain.on('video-window:visible', onVisible);
  ipcMain.on('video-window:action', onAction);
  // 窗口被拖动/改大小时 CSS 坐标没变但屏幕位置变了，必须由主进程重算后补发。
  const hostWindow = getWindow();
  const onHostGeometry = () => scheduleBoundsPush();
  if (hostWindow) {
    hostWindow.on('move', onHostGeometry);
    hostWindow.on('resize', onHostGeometry);
  }

  // The Brain drives window control through the client's normal tool
  // dispatch channel, so the service has to answer three questions without
  // guessing: what is hosted right now, and did the action actually happen.
  // `action` reports the helper's real answer — never an assumed success.
  const ACTIONS = { focus: 'FOCUS', show: 'SHOW', hide: 'HIDE', attach: 'ATTACH',
                    detach: 'DETACH', close: 'CLOSE_APP' };

  function status() {
    return { state: nativeState, hosted_software: hosted ? hosted.profileName || hosted.exePath : null,
             exe_path: hosted ? hosted.exePath : null, pid: hosted ? hosted.pid : null,
             helper_running: !!helper, helper_ready: helperReady };
  }

  function action(name) {
    const key = String(name || '').trim().toLowerCase();
    const command = ACTIONS[key];
    if (!command) return { ok: false, error: 'unsupported_action', detail: '不支持的窗口操作：' + name };
    if (!helper || helper.killed) {
      return { ok: false, error: 'no_hosted_window', detail: '当前没有托管任何软件窗口', state: nativeState };
    }
    const sent = send(command);
    if (!sent) {
      return { ok: false, error: 'helper_unavailable', detail: '原生窗口宿主没有响应', state: nativeState };
    }
    if (command === 'HIDE') nativeState = 'window-hidden';
    if (command === 'SHOW') nativeState = 'resident';
    return { ok: true, action: key, state: nativeState, hosted_software: hosted ? hosted.profileName : null };
  }

  return {
    hide: () => send('HIDE'),
    action,
    status,
    profiles,
    startProfile: (profileId, bounds) => {
      const item = profiles().find(p => p.id === String(profileId || '') || p.name === String(profileId || ''));
      if (!item) return { ok: false, error: 'profile_not_found', detail: '没有这个软件配置：' + profileId };
      if (process.platform !== 'win32') return { ok: false, error: 'unsupported_platform', detail: '原生窗口托管目前仅支持 Windows' };
      const target = getWindow();
      try {
        startHelper(target);
      } catch (error) {
        return { ok: false, error: 'helper_start_failed', detail: String(error && error.message || error) };
      }
      lastBounds = bounds || lastBounds;
      const rectFields = (boundsCommand(bounds) || 'BOUNDS\t0\t0\t1\t1').split('\t').slice(1);
      send(`START\t${encode(item.exePath)}\t${encode(item.args)}\t${rectFields.join('\t')}`);
      send('SHOW');
      hosted = { profileName: item.name, exePath: item.exePath, pid: null };
      nativeState = 'starting';
      emit({ type: 'start-requested', exePath: item.exePath, profileName: item.name });
      return { ok: true, profile: item.name, exePath: item.exePath, state: nativeState };
    },
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
      if (hostWindow && !hostWindow.isDestroyed()) {
        hostWindow.removeListener('move', onHostGeometry);
        hostWindow.removeListener('resize', onHostGeometry);
      }
      if (boundsTimer) { clearTimeout(boundsTimer); boundsTimer = null; }
    },
  };
}

module.exports = { createNativeWindowService };

