// -*- coding: utf-8 -*-
/**
 * sidecar 生命周期：把 vigil-brain.exe（PyInstaller 打包的本地大脑）当子进程养。
 *
 * 四件事，别的不碰：
 * 1. 找 exe（配置 > 环境变量 > 打包资源 > 开发仓 dist/）
 * 2. spawn 并从 stdout 抓就绪行（VIGIL_BRAIN_READY port=N，见 brain/server_launch.py）
 * 3. 崩了自动重启，最多 3 次；稳定跑 10 分钟后计数清零
 * 4. 应用退出时把子进程带走（Windows 用 taskkill /T，防止孤儿进程占着端口）
 *
 * 大脑的端口是它自己挑的（没传 --port 就让系统给个空闲的），
 * 所以"端口发现"只能靠读 stdout —— 这就是就绪行存在的意义。
 */
const { spawn, execFile } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');

const READY_RE = /VIGIL_BRAIN_READY\s+port=(\d+)/;
const MAX_RESTARTS = 3;
const STABLE_MS = 10 * 60 * 1000;   // 跑满 10 分钟算稳定，重启计数清零

let app = null;          // electron app（init 注入，为了拿 resourcesPath）
let child = null;
let state = {
  enabled: false,        // 找到了 exe 且允许启动
  exe: '',
  home: '',
  status: 'stopped',     // stopped | starting | ready | failed
  port: 0,
  pid: 0,
  restarts: 0,
  lastErr: '',
  startedAt: 0,
  logTail: [],           // 最近几十行 stdout/stderr，给诊断面板用
};
const listeners = [];

function emit() {
  const snap = { ...state, base: base() };
  listeners.forEach((fn) => { try { fn(snap); } catch (e) { /* 界面回调别连累主进程 */ } });
}

function log(line) {
  const s = String(line || '').replace(/\s+$/, '');
  if (!s) return;
  state.logTail.push(s);
  if (state.logTail.length > 60) state.logTail.shift();
  console.log('[sidecar] ' + s);
}

function base() {
  return state.port ? 'http://127.0.0.1:' + state.port : '';
}

/** exe 定位：一个入口，别处不许自己拼路径 */
function locateExe(cfg) {
  const cands = [];
  if (cfg && cfg.sidecar_exe) cands.push(cfg.sidecar_exe);
  if (process.env.VIGIL_BRAIN_EXE) cands.push(process.env.VIGIL_BRAIN_EXE);
  if (app) {
    if (app.isPackaged) {
      cands.push(path.join(process.resourcesPath, 'vigil-brain', 'vigil-brain.exe'));
    } else {
      // 开发仓：vigil-client/electron/ 上一级是仓库根
      cands.push(path.join(__dirname, '..', '..', 'dist', 'vigil-brain', 'vigil-brain.exe'));
    }
  }
  for (const p of cands) {
    try {
      if (p && fs.existsSync(p)) return path.resolve(p);
    } catch (e) { /* 排下一个 */ }
  }
  return '';
}

function probeHealth(port, timeoutMs) {
  return new Promise((resolve) => {
    const req = http.get('http://127.0.0.1:' + port + '/api/health',
      { timeout: timeoutMs || 3000 }, (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      });
    req.on('timeout', () => { req.destroy(); resolve(false); });
    req.on('error', () => resolve(false));
  });
}

/** 云端同步（M5）：把云端地址 + 主人钥匙递给本地大脑。
 *  大脑的 sync_client.cloud() 认 VIGIL_CLOUD_URL / VIGIL_CLOUD_TOKEN，
 *  config 的 sync 段优先（设置页直接写大脑 config 也行），这里给没写
 *  config 的场景兜底 —— 客户端是唯一知道"云端在哪"的地方。 */
function cloudEnv() {
  let cfg = {};
  try { cfg = readCfgSafe() || {}; } catch (e) { /* 没有配置就当没有 */ }
  const s = cfg.sync || {};
  const env = {};
  if (s.cloud_url) env.VIGIL_CLOUD_URL = String(s.cloud_url);
  if (s.cloud_token) env.VIGIL_CLOUD_TOKEN = String(s.cloud_token);
  return env;
}

function spawnBrain() {
  state.status = 'starting';
  state.startedAt = Date.now();
  emit();
  try {
    child = spawn(state.exe, [], {
      cwd: state.home,             // 大脑的家：config/ data/ 都落这儿
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: Object.assign({}, process.env, cloudEnv()),
    });
  } catch (e) {
    state.status = 'failed';
    state.lastErr = 'spawn 失败：' + e.message;
    emit();
    return;
  }
  state.pid = child.pid || 0;
  log('spawn pid=' + state.pid);

  let buf = '';
  const onData = (chunk) => {
    buf += chunk.toString('utf8');
    let idx;
    while ((idx = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, idx);
      buf = buf.slice(idx + 1);
      log(line);
      const m = line.match(READY_RE);
      if (m && state.status !== 'ready') {
        state.port = parseInt(m[1], 10);
        state.status = 'ready';
        emit();
        // 就绪行只是"大脑说它好了"，再摸一次 /api/health 才算真的通
        probeHealth(state.port).then((ok) => {
          if (!ok) log('就绪行已收到但健康检查没过，等下一轮探测');
        });
      }
    }
  };
  child.stdout.on('data', onData);
  child.stderr.on('data', onData);

  child.on('exit', (code, signal) => {
    log('exit code=' + code + ' signal=' + signal);
    child = null;
    state.pid = 0;
    state.port = 0;
    if (state.status === 'stopped') { emit(); return; }   // 人为关停，别拉起
    // 崩溃重启：跑满 STABLE_MS 后计数清零；超 3 次放弃并亮红灯
    if (Date.now() - state.startedAt > STABLE_MS) state.restarts = 0;
    if (state.restarts < MAX_RESTARTS) {
      state.restarts += 1;
      state.status = 'starting';
      state.lastErr = '进程退出(code=' + code + ')，第 ' + state.restarts + ' 次重启';
      emit();
      log(state.lastErr);
      setTimeout(() => {
        if (state.status !== 'stopped') spawnBrain();
      }, 2000);
    } else {
      state.status = 'failed';
      state.lastErr = '连续退出 ' + MAX_RESTARTS + ' 次，放弃重启（看日志尾部）';
      emit();
    }
  });
  child.on('error', (e) => log('进程错误：' + e.message));
}

/** 找到 exe 就起；cfg.sidecar === false 或 VIGIL_SIDECAR_DISABLE=1 明确说"别用本地大脑" */
function maybeStart(cfg) {
  if (child) return;
  const exe = locateExe(cfg);
  state.enabled = !!exe && cfg.sidecar !== false &&
    process.env.VIGIL_SIDECAR_DISABLE !== '1';
  state.exe = exe;
  state.home = exe ? path.dirname(exe) : '';
  if (!state.enabled) {
    state.status = 'stopped';
    emit();
    return;
  }
  spawnBrain();
}

function stop() {
  if (!child) { state.status = 'stopped'; emit(); return; }
  state.status = 'stopped';        // 先定性，exit 回调看到它就不重启了
  const pid = state.pid;
  const c = child;
  child = null;
  state.pid = 0;
  state.port = 0;
  if (process.platform === 'win32' && pid) {
    // /T 连子进程一起带走，不留孤儿占端口
    execFile('taskkill', ['/pid', String(pid), '/T', '/F'], () => {
      try { c.kill(); } catch (e) { /* 已经死了 */ }
    });
  } else {
    try { c.kill(); } catch (e) { /* 同上 */ }
  }
  emit();
}

function restart(cfg) {
  stop();
  // 给 taskkill 一点时间，否则新进程可能撞上还没释放的端口
  setTimeout(() => maybeStart(cfg || readCfgSafe()), 1500);
}

let _cfgGetter = null;
function readCfgSafe() {
  try { return _cfgGetter ? _cfgGetter() : {}; } catch (e) { return {}; }
}

function init(electronApp, cfgGetter) {
  app = electronApp;
  _cfgGetter = cfgGetter;
  app.on('before-quit', stop);
  app.on('will-quit', stop);
}

module.exports = {
  init, maybeStart, stop, restart, base, probeHealth,
  status: () => ({ ...state, base: base() }),
  ready: () => state.status === 'ready' && !!state.port,
  onState: (fn) => { listeners.push(fn); return () => {
    const i = listeners.indexOf(fn); if (i >= 0) listeners.splice(i, 1);
  }; },
};
