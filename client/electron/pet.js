// Vigil 桌宠 sidecar：把移植自 codex-bailong-desktop-pet 的 pet/app 作为
// 独立 PySide6 进程拉起。进程有单实例锁，重复 start 会让新实例自行退出。
const fs = require('fs');
const path = require('path');
const { app } = require('electron');
const { spawn } = require('child_process');

// pet/ 在仓库根（client/electron 的上两级）；打包后随 extraResources 落在 resources/pet。
function petRoot() {
  const packaged = path.join(process.resourcesPath || '', 'pet');
  if (app.isPackaged !== undefined && app.isPackaged && fs.existsSync(path.join(packaged, 'app', 'enhanced.py'))) return packaged;
  return path.resolve(__dirname, '..', '..', 'pet');
}

function resolvePython() {
  if (process.env.VIGIL_PET_PYTHON && fs.existsSync(process.env.VIGIL_PET_PYTHON)) return process.env.VIGIL_PET_PYTHON;
  const venv = path.resolve(__dirname, '..', '..', 'venv', 'Scripts', 'python.exe');
  if (fs.existsSync(venv)) return venv;
  return 'python';
}

const state = { proc: null, status: 'stopped', lastError: '', pid: null, startedAt: 0 };

function snapshot() {
  return { status: state.status, pid: state.pid, lastError: state.lastError,
    startedAt: state.startedAt || null, running: state.status === 'running' && !!state.proc };
}

function checkDeps(python) {
  return new Promise(resolve => {
    const probe = spawn(python, ['-c', 'import PySide6'], { windowsHide: true });
    let out = '';
    probe.stderr.on('data', d => { out += d; });
    probe.on('error', () => resolve(`无法启动 Python（${python}），请安装或设置 VIGIL_PET_PYTHON`));
    probe.on('close', code => resolve(code === 0 ? '' : `桌宠依赖未安装：请在 venv 执行 pip install -r pet/requirements.txt${out ? '\n' + out.trim().split('\n').slice(-1)[0] : ''}`));
  });
}

function start() {
  return new Promise(async resolve => {
    if (state.proc && state.status !== 'stopped') return resolve({ ok: true, ...snapshot() });
    const entry = path.join(petRoot(), 'app', 'enhanced.py');
    if (!fs.existsSync(entry)) {
      state.status = 'unavailable'; state.lastError = '未找到 pet/app/enhanced.py，桌宠未随本构建分发';
      return resolve({ ok: false, error: state.lastError, ...snapshot() });
    }
    const python = resolvePython();
    const depError = await checkDeps(python);
    if (depError) { state.status = 'unavailable'; state.lastError = depError; return resolve({ ok: false, error: depError, ...snapshot() }); }
    state.status = 'starting'; state.lastError = '';
    let stderr = '';
    const proc = spawn(python, [entry], { cwd: path.dirname(entry), windowsHide: true,
      stdio: ['ignore', 'ignore', 'pipe'], env: { ...process.env } });
    state.proc = proc; state.pid = proc.pid; state.startedAt = Date.now();
    proc.stderr.on('data', d => { stderr = (stderr + d).slice(-4000); });
    proc.on('error', err => {
      state.status = 'unavailable'; state.lastError = String(err); state.proc = null; state.pid = null;
      if (pending) { pending({ ok: false, error: state.lastError }); pending = null; }
    });
    // 单实例锁：已有桌宠在跑时新进程立即退出，视为已启动。
    let pending = result => { resolve(result); };
    proc.on('exit', code => {
      const wasStarting = state.status === 'starting';
      state.proc = null; state.pid = null;
      if (state.status !== 'unavailable') state.status = 'stopped';
      if (code && code !== 0) state.lastError = (stderr.trim().split('\n').slice(-1)[0] || `进程退出码 ${code}`);
      if (pending && wasStarting && code === 0) pending({ ok: true, ...snapshot() });
      if (pending && code !== 0) pending({ ok: false, error: state.lastError, ...snapshot() });
      pending = null;
    });
    // 进程稳活 1.5s 认为启动成功（真正的窗口由桌宠自己展示）。
    setTimeout(() => {
      if (state.proc === proc && state.status === 'starting') {
        state.status = 'running';
        if (pending) { pending({ ok: true, ...snapshot() }); pending = null; }
      }
    }, 1500);
  });
}

function stop() {
  return new Promise(resolve => {
    if (!state.proc) return resolve({ ok: true, ...snapshot() });
    const proc = state.proc;
    proc.once('exit', () => resolve({ ok: true, ...snapshot() }));
    proc.kill();
    setTimeout(() => { if (state.proc === proc) { try { proc.kill('SIGKILL'); } catch {} } resolve({ ok: true, ...snapshot() }); }, 3000).unref();
  });
}

module.exports = { start, stop, status: snapshot };
