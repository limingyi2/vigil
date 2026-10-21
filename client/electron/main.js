// -*- coding: utf-8 -*-
/**
 * 主进程：窗口、配置、系统钥匙串、HTTP 代理。
 *
 * 三条边界，别越：
 * 1. **渲染进程不碰 Node、不碰 token**。token 存在系统钥匙串里，由这里读出来
 *    加在请求头上。渲染层只知道"调用失败"或"成功"，拿不到凭据。
 * 2. **所有网络请求走这里**。渲染层用 file:// 加载，直接 fetch 会撞 CORS；
 *    而且鉴权头统一在主进程加，能保证没有一处漏加。
 * 3. **窗口无边框**（frame:false），标题栏由页面自己画 —— 排版稿里那套
 *    Vigil / 渠道切换 / 窗口按钮要能落地，就不能用系统标题栏。
 *    代价是拖拽、最小化、关闭都要自己实现（见下面的 win:* 通道）。
 */
const { app, BrowserWindow, ipcMain, shell, Menu, Tray, dialog, Notification } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const https = require('https');
const os = require('os');
const crypto = require('crypto');
const { spawn } = require('child_process');
if (process.env.VIGIL_USER_DATA) app.setPath('userData', path.resolve(process.env.VIGIL_USER_DATA));
if (process.platform === 'win32') app.setAppUserModelId('com.vigil.client');
// Stable only for this Electron process: renderer reloads can restore the
// selected chat, while a normal app launch starts at the welcome page.
const APP_PROCESS_INSTANCE_ID = crypto.randomUUID();

const PERF_START_AT = Date.now();
function perfLog(stage, detail = '') {
  const suffix = detail ? ' ' + detail : '';
  console.log('[perf] ' + stage + ' +' + (Date.now() - PERF_START_AT) + 'ms' + suffix);
}
// 客户端自己的渠道：独立于手机 App / 微信，聊天、历史、记忆都落在 pc 分区。
// 不是可选项 —— 这个客户端就是一台控制台，不顶替任何别的渠道。
const CLIENT_SRC = 'pc';

let win = null;
let settingsWin = null;
let settingsWinReady = false;
let pendingSettingsTarget = null;
require('./agent-center-notifications').register({ipcMain, Notification, getWindow:() => win,
  indexPath:path.join(__dirname, '..', 'src', 'index.html'), onClick:() => showMainWindow()});

// ── 托盘：点关闭 = 隐藏，不退出（2026-10-09 用户要求）──────────────
// 她是常驻的：微信消息、本地操控轮询、转发通道都在这个进程里跑着。
// 点 ✕ 把整个客户端杀掉 = 微信渠道的本地工具全断。所以：
//   · 点关闭 → 窗口隐藏，进程照常活着（托盘图标还在）
//   · 托盘单击 → 窗口回来；右键菜单 → 显示 / 退出
//   · 只有托盘菜单里的"退出"才真正结束进程（quitting 放行 close）
let tray = null;
let quitting = false;

function showMainWindow() {
  if (!win || win.isDestroyed()) { createWindow(); return; }
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

function createTray() {
  if (tray || process.platform === 'linux') return;   // linux 没测过，不硬上
  const icon = path.join(__dirname, '..', 'assets', 'icon.ico');
  // 2026-10-09：assets 曾经不在 build.files 里 → 打包后这个路径不存在，
  // 托盘画出来是一块空白 —— 用户找不到入口也点不准。至少留句话在日志里。
  if (!fs.existsSync(icon)) {
    console.warn('[托盘] 图标不存在，会是空白图标：', icon);
  }
  tray = new Tray(icon);
  tray.setToolTip('Vigil（点关闭只是躲到这里）');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '显示主界面', click: () => showMainWindow() },
    { type: 'separator' },
    { label: '退出', click: () => {
        // 唯一的真退出入口：先放行 close，再走正常生命周期
        quitting = true;
        if (win && !win.isDestroyed()) win.close();
        else app.quit();
      } },
  ]));
  // 单击托盘图标 = 收起/唤回（Windows 习惯）
  tray.on('click', () => {
    if (!win || win.isDestroyed()) { createWindow(); return; }
    if (win.isVisible() && !win.isMinimized()) win.hide();
    else showMainWindow();
  });
}

// ── 推送更新（2026-10-09）：跟手机 App 一套思路 ─────────────────────
// 云端 C:\Vigil\app\client\ 放安装包 + client_version.json（code 递增），
// 内核加两条只读路由 /api/client/version、/api/client/setup。
// 客户端启动 15s 后查一次、之后每小时查一次；有新版先问、同意了后台
// 下载（sha256 校验），完成后弹窗 —— 走安装向导覆盖装，配置不会丢。
// 本地包里的 version.json（打进 asar）是"我是谁"：code 就是版本号。
const UPD_DL = path.join(os.tmpdir(), 'vigil-setup-download.exe');
let updBusy = false;
let updDeclinedCode = 0;   // 用户这轮会话里拒绝过的版本，别每小时弹一次

function readLocalCode() {
  try {
    const j = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'version.json'), 'utf8'));
    return Number(j.code) || 0;
  } catch (e) {
    return 0;   // 老包没有 version.json，永远是 0 → 第一次有版就能收到推送
  }
}

function sha256File(p) {
  return new Promise((resolve) => {
    try {
      const h = crypto.createHash('sha256');
      fs.createReadStream(p).on('data', (c) => h.update(c))
        .on('end', () => resolve(h.digest('hex')))
        .on('error', () => resolve(''));
    } catch (e) { resolve(''); }
  });
}

async function checkUpdate(mode) {
  // mode='status' → 只查版本状态，绝不弹窗不下载（关于页进页展示用）
  // mode='ask'    → 关于页按钮：查 → 有新版先问 → 下载 → 问安装
  // mode=false    → 启动/定时自动查：全静默，有新版也先问（拒绝过这版就不再烦）
  const interactive = mode === 'ask';
  const cur = readLocalCode();
  const base = { current: cur, ver: app.getVersion() };
  if (process.env.VIGIL_TEST_NO_UPDATES === '1') return Object.assign(base, { ok: true, update: false, test: true });
  if (updBusy) return Object.assign(base, { ok: true, update: false, busy: true });
  // 只认公开发布源（GitHub Releases）。跟着阿里云 Brain 走的那条更新通道
  // 在 P8.1 去掉了：Brain 不再托管安装包，更新也不该依赖它。
  const v = await require('./public-updates').latest();
  const j = (v && v.json) || {};
  if (!v.ok || !j.code) {
    return Object.assign(base, { ok: false, update: false, err: (j && j.err) || (v ? v.err || ('HTTP ' + v.status) : '无响应') });
  }
  const latest = Number(j.code) || 0;
  if (latest <= cur) {
    return Object.assign(base, { ok: true, update: false, latest, ver: j.ver || '' });
  }
  const have = { ok: true, update: true, latest, ver: j.ver || '', notes: j.notes || '' };
  if (mode === 'status') return have;                       // 只报状态
  if (!interactive && updDeclinedCode === latest) {
    return Object.assign(have, { declined: true });         // 这版已经拒绝过，别再弹
  }
  // 有新版：先问一次（自动检查也问 —— 装不装什么时候装是用户的事）
  updBusy = true;
  try {
    const ask = await dialog.showMessageBox(win || undefined, {
      type: 'info', title: '发现新版本',
      message: '客户端有更新：v' + (j.ver || ('code ' + latest)) + '（当前 code ' + cur + '）',
      detail: String(j.notes || '修复与改进。') + '\n\n现在开始下载吗？下载完成后可以再选安装时间。',
      buttons: ['下载', '下次再说'], defaultId: 0, cancelId: 1, noLink: true,
    });
    if (ask.response !== 0) {
      updDeclinedCode = latest;
      return Object.assign(base, { ok: true, update: true, latest, ver: j.ver || '', declined: true });
    }
    // 已下载过且校验通过的就不重下（sha 一致 = 就是这个版本）
    const wantSha = String(j.sha256 || '').toLowerCase();
    if (!/^[a-f0-9]{64}$/.test(wantSha) || !Number.isSafeInteger(j.size) || j.size <= 0) {
      return {...base,ok:false,err:'更新清单缺少有效的 SHA-256 或文件大小，已拒绝安装'};
    }
    let dl = { ok: false };
    if (fs.existsSync(UPD_DL) && wantSha && (await sha256File(UPD_DL)) === wantSha) {
      dl = { ok: true, cached: true };
    } else {
      const progress = (got, total) => {
          // 进度写进窗口标题（没有专门的进度页，title 不打扰界面）
          if (win && !win.isDestroyed() && total) {
            win.setTitle('正在下载更新 ' + Math.round((got / total) * 100) + '%');
          }
        };
      dl = await require('./public-updates').download(j.setup_url, UPD_DL, progress);
      if (win && !win.isDestroyed()) win.setTitle('');
    }
    if (!dl.ok) {
      dialog.showErrorBox('更新下载失败', String(dl.err || '网络错误'));
      return Object.assign(base, { ok: false, update: true, latest, err: dl.err });
    }
    if (wantSha) {
      const sha = await sha256File(UPD_DL);
      if (sha !== wantSha || fs.statSync(UPD_DL).size !== j.size) {
        fs.unlinkSync(UPD_DL);
        dialog.showErrorBox('更新失败', '安装包校验不一致，已删除重试下次。');
        return Object.assign(base, { ok: false, update: true, latest, err: 'sha256 不匹配' });
      }
    }
    const go = await dialog.showMessageBox(win || undefined, {
      type: 'question', title: '更新就绪',
      message: '新版本已下载，现在安装吗？',
      detail: '安装会退出客户端（配置和数据都在，不会丢）。装完从开始菜单或桌面重新打开。',
      buttons: ['立即安装', '下次启动再说'], defaultId: 0, cancelId: 1, noLink: true,
    });
    if (go.response === 0) {
      updDeclinedCode = 0;
      // 不带参数跑安装向导（assisted NSIS）：静默覆盖运行中的程序有锁文件风险，
      // 向导点两下但一定成功。退出客户端前把安装器先拉起来。
      spawn(UPD_DL, [], { detached: true, stdio: 'ignore' }).unref();
      quitting = true;
      setTimeout(() => app.quit(), 300);
      return Object.assign(base, { ok: true, update: true, latest, installing: true });
    }
    updDeclinedCode = latest;   // 装包留在临时目录，下次弹窗直接可装
    return Object.assign(base, { ok: true, update: true, latest, ver: j.ver || '', downloaded: true });
  } finally {
    updBusy = false;
  }
}


// ── 配置：只留本机这一份 ─────────────────────────────────────────
// P8.1 起客户端只连随包 Brain。旧版配置里可能写着远程地址、设备 token、
// 云端同步凭据 —— 读的时候一律不采用；迁移时把这些键挪进 legacy_remote
// 归档（值原样保留，一条都不删），并把 sync.enabled 显式关掉。
const LEGACY_REMOTE_KEYS = ['connection_mode', 'remote_base', 'cloud_base',
  'device_id', 'token_kind', 'update_source'];
const cfgPath = () => path.join(app.getPath('userData'), 'config.json');

function readRawCfg() {
  try { return JSON.parse(fs.readFileSync(cfgPath(), 'utf8')) || {}; } catch (e) { return {}; }
}

/** 一次性把旧远程配置归档。幂等：第二次运行时已经没有可搬的键。 */
function migrateCfg() {
  const raw = readRawCfg();
  const legacy = Object.assign({}, raw.legacy_remote || {});
  let changed = false;
  for (const key of LEGACY_REMOTE_KEYS.concat(['base'])) {
    if (raw[key] === undefined) continue;
    legacy[key] = raw[key];
    delete raw[key];
    changed = true;
  }
  // 同步凭据是用户的东西，留着；但桌面端已经没有同步入口，永远不自动开。
  if (raw.sync && typeof raw.sync === 'object' && raw.sync.enabled !== false) {
    raw.sync = { ...raw.sync, enabled: false };
    changed = true;
  }
  if (Object.keys(legacy).length && JSON.stringify(raw.legacy_remote || {}) !== JSON.stringify(legacy)) {
    raw.legacy_remote = legacy;
    changed = true;
  }
  if (!changed) return false;
  try {
    const tmp = cfgPath() + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(raw, null, 2), 'utf8');
    fs.renameSync(tmp, cfgPath());
    console.log('[配置] 已归档旧远程 Brain 配置到 legacy_remote');
  } catch (e) {
    console.warn('[配置] 迁移写入失败：', e.message);
  }
  return true;
}

function writeCfg(patch) {
  const next = { ...readRawCfg(), src: CLIENT_SRC, ...(patch || {}) };
  // 归档过的键不允许被旧代码路径再写回来
  for (const key of LEGACY_REMOTE_KEYS) delete next[key];
  const dir = path.dirname(cfgPath());
  try {
    fs.mkdirSync(dir, { recursive: true });
    // 原子写：半截的配置会让客户端下次启动直接读不到配置
    const tmp = cfgPath() + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(next, null, 2), 'utf8');
    fs.renameSync(tmp, cfgPath());
  } catch (e) {
    console.warn('[配置] 写入失败：', e.message);
  }
  return next;
}

function readCfg() {
  // src 强制 pc：老配置里存的 app/wechat 是旧版"顶替渠道"的遗留，不回读
  return { ...readRawCfg(), src: CLIENT_SRC };
}

// ── 凭据：只有本机 Brain 的 token ────────────────────────────────
// token 由随包 Brain 自己在 <userData>/brain/config/config.json 里生成，
// 每次调用现读 —— Brain 重启换了 token 也不会拿旧的去打 401。
async function getToken() {
  const fixed = testBrain();
  if (fixed) return fixed.token;
  return sidecar.ready() ? sidecar.token() : '';
}


// ── HTTP：一个入口，鉴权头在这里统一加 ──────────────────────────────
function request(method, urlStr, body, token, timeoutMs, perfLabel = '') {
  return new Promise((resolve) => {
    let u;
    try {
      u = new URL(urlStr);
    } catch (e) {
      return resolve({ ok: false, status: 0, err: '地址不合法：' + urlStr });
    }
    const route = u.pathname;
    const startedAt = Date.now();
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      if (perfLabel) perfLog(perfLabel + ':end', 'route=' + route + ' status=' + (result.status || 0) + ' duration=' + (Date.now() - startedAt) + 'ms');
      resolve(result);
    };
    if (perfLabel) perfLog(perfLabel + ':start', 'route=' + route);
    const mod = u.protocol === 'https:' ? https : http;
    const data = body === undefined || body === null ? null : Buffer.from(JSON.stringify(body), 'utf8');
    const headers = { Accept: 'application/json' };
    if (data) {
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = data.length;
    }
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const req = mod.request(
      {
        method,
        hostname: u.hostname,
        port: u.port || (u.protocol === 'https:' ? 443 : 80),
        path: u.pathname + (u.search || ''),
        headers,
        // 对话生成可能跑几十秒（默认 120s）；轮询这类心跳请求应该传短的，
        // 不然目标一掉线每次都要等满超时才回来
        timeout: timeoutMs || 120000,
      },
      (res) => {
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          let json = null;
          try {
            json = JSON.parse(text);
          } catch (e) {
            /* 非 JSON（比如 APK、图片）就把原文带回去 */
          }
          finish({ ok: res.statusCode >= 200 && res.statusCode < 300, status: res.statusCode, json, text });
        });
      }
    );
    req.on('socket', (socket) => {
      if (!perfLabel) return;
      socket.once('lookup', () => perfLog(perfLabel + ':dns'));
      socket.once('connect', () => perfLog(perfLabel + ':tcp'));
      socket.once('secureConnect', () => perfLog(perfLabel + ':tls'));
    });
    req.on('timeout', () => {
      req.destroy(new Error('请求超时'));
    });
    req.on('error', (e) => finish({ ok: false, status: 0, err: e.message,
      networkCode: String(e && e.code || '') }));
    if (data) req.write(data);
    req.end();
  });
}

function apiDiagnostic(result, method, route) {
  const status = Number(result && result.status) || 0;
  const code = String(result && result.networkCode || '');
  let kind = 'brain_http_error';
  if (!status) {
    if (/TIMEOUT/i.test(code) || /timeout/i.test(String(result && result.err || ''))) kind = 'timeout';
    else if (/ENOTFOUND|EAI_AGAIN|EAI_FAIL/i.test(code)) kind = 'dns';
    else if (/CERT|TLS|SSL/i.test(code)) kind = 'tls';
    else if (/PROXY|TUNNEL/i.test(code + ' ' + String(result && result.err || ''))) kind = 'proxy';
    else kind = 'brain_unavailable';
  } else if (status === 401 || status === 403) kind = 'authentication';
  else if (status >= 400 && status < 500) kind = 'brain_http_error';
  else if (status >= 500) kind = 'brain_server_error';
  return { stage: 'electron_to_brain', kind, method, route, status,
    networkCode: code || undefined,
    message: status ? ('Brain HTTP ' + status) : String(result && result.err || 'Brain unavailable').slice(0, 180),
    retryable: !status || status >= 500 };
}

// 二进制附件分块请求：与 JSON API 共用同一套目标限制和 token，
// 但正文直接以 Buffer 发送，避免把视频/文档编码进聊天 JSON。
function requestBinary(method, urlStr, bytes, token, headers, timeoutMs) {
  return new Promise((resolve) => {
    let u;
    try { u = new URL(urlStr); } catch (e) {
      return resolve({ ok: false, status: 0, err: '地址不合法：' + urlStr });
    }
    const mod = u.protocol === 'https:' ? https : http;
    const data = Buffer.isBuffer(bytes) ? bytes : Buffer.from(bytes || []);
    const reqHeaders = { Accept: 'application/json', ...(headers || {}),
      'Content-Length': data.length };
    if (token) reqHeaders.Authorization = 'Bearer ' + token;
    const req = mod.request({ method, hostname: u.hostname,
      port: u.port || (u.protocol === 'https:' ? 443 : 80),
      path: u.pathname + (u.search || ''), headers: reqHeaders,
      timeout: timeoutMs || 120000 }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let json = null;
        try { json = JSON.parse(text); } catch (_) {}
        resolve({ ok: res.statusCode >= 200 && res.statusCode < 300,
          status: res.statusCode, json, text });
      });
    });
    req.on('timeout', () => req.destroy(new Error('请求超时')));
    req.on('error', (e) => resolve({ ok: false, status: 0, err: e.message }));
    req.end(data);
  });
}

// 流式请求：SSE 逐块推送，onEvent 每收到一个 data: 事件就调用一次。
// 用于 /api/chat/stream 的实时思考过程和正文。
function requestStream(method, urlStr, body, token, onEvent, isTerminalEvent) {
  return new Promise((resolve) => {
    let u;
    try {
      u = new URL(urlStr);
    } catch (e) {
      return resolve({ ok: false, status: 0, err: '地址不合法：' + urlStr });
    }
    const mod = u.protocol === 'https:' ? https : http;
    const data = body === undefined || body === null ? null : Buffer.from(JSON.stringify(body), 'utf8');
    const headers = { Accept: 'text/event-stream' };
    if (data) {
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = data.length;
    }
    if (token) headers['Authorization'] = 'Bearer ' + token;
    let terminalEvent = false;
    let transportFailed = false;
    const failStream = (msg) => {
      if (terminalEvent || transportFailed) return;
      transportFailed = true;
      onEvent && onEvent({ type: 'error', class: 'connection_lost',
        status: 'interrupted', msg });
    };
    const req = mod.request(
      {
        method,
        hostname: u.hostname,
        port: u.port || (u.protocol === 'https:' ? 443 : 80),
        path: u.pathname + (u.search || ''),
        headers,
        timeout: 300000,  // 流式对话可能跑几分钟
      },
      (res) => {
        res.setEncoding('utf8');
        if (res.statusCode !== 200) {
          res.resume();
          onEvent({ type: 'error', msg: '请求失败（HTTP ' + res.statusCode + '）' });
          return resolve({ ok: false, status: res.statusCode });
        }
        let buf = '';
        res.on('data', (chunk) => {
          buf += chunk.toString('utf8');
          // SSE 事件以 \n\n 分隔
          let idx;
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            const raw = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            for (const line of raw.split('\n')) {
              if (line.startsWith('data: ')) {
                try {
                  const evt = JSON.parse(line.slice(6));
                  if (evt && (isTerminalEvent
                    ? isTerminalEvent(evt)
                    : (evt.type === 'done' || evt.type === 'error'))) terminalEvent = true;
                  onEvent && onEvent(evt);
                } catch (e) { /* 解析失败的行跳过 */ }
              }
            }
          }
        });
        res.on('aborted', () => {
          failStream('连接中断，结果不完整');
          // IncomingMessage emits `aborted` rather than `end` when the peer
          // drops an SSE response. Settle the stream promise so its bounded
          // Agent-run resume policy can take over; otherwise the caller hangs
          // until the renderer's unrelated UI fallback fires.
          resolve({ ok: false, status: res.statusCode || 0, err: '连接中断，结果不完整' });
        });
        res.on('end', () => {
          if (!terminalEvent) failStream('服务端提前关闭连接，结果不完整');
          resolve({ ok: res.statusCode >= 200 && res.statusCode < 300 && terminalEvent, status: res.statusCode });
        });
      }
    );
    req.on('timeout', () => { failStream('请求超时，结果不完整'); req.destroy(); });
    req.on('error', (e) => {
      failStream(e && e.message === 'socket hang up'
        ? '连接中断，结果不完整' : '连接失败，结果不完整');
      resolve({ ok: false, status: 0, err: '连接失败，结果不完整' });
    });
    if (data) req.write(data);
    req.end();
  });
}

// ── 窗口 ─────────────────────────────────────────────────────────
function createWindow() {
  perfLog('window-create');
  win = new BrowserWindow({
    width: 1380,
    height: 880,
    minWidth: 900,           // 再窄三栏就挤成一团了（排版稿的断点在 860）
    minHeight: 600,
    frame: false,
    backgroundColor: '#faf7f2',   // 与主题底色一致，避免启动闪白
    show: false,
    // 任务栏和 Alt+Tab 用这个图标（assets/icon.ico，打包时也用它当应用图标）
    icon: path.join(__dirname, '..', 'assets', 'icon.ico'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      // 方案里写死的三条安全配置，别动
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      spellcheck: false,
    },
  });
  // The renderer needs a dynamic port for WebWallGL's authenticated media bridge.
  // All other renderer XHR/fetch traffic to localhost must be blocked here.
  const wallpaperRendererId = win.webContents.id;
  win.webContents.session.webRequest.onBeforeRequest(
    { urls: ['http://127.0.0.1/*'] },
    (details, callback) => {
      if (details.webContentsId === wallpaperRendererId
          && (details.resourceType === 'xhr' || details.resourceType === 'fetch')
          && !pluginMedia.isApprovedUrl(details.url)) {
        callback({ cancel: true });
        return;
      }
      callback({});
    });
  win.loadFile(path.join(__dirname, '..', 'src', 'index.html'));
  // Electron 不会自动提供浏览器右键菜单。给聊天输入框补上最小原生菜单，
  // 其中“粘贴”仍由渲染层的 paste 事件读取文本、图片或文件，不绕过附件校验。
  win.webContents.on('context-menu', (_event, params) => {
    if (!win || win.isDestroyed()) return;
    const items = [];
    if (params.isEditable) {
      if (params.editFlags && params.editFlags.canUndo) items.push({ role: 'undo' });
      if (params.editFlags && params.editFlags.canRedo) items.push({ role: 'redo' });
      if (items.length) items.push({ type: 'separator' });
      if (params.editFlags && params.editFlags.canCut) items.push({ role: 'cut' });
      if (params.editFlags && params.editFlags.canCopy) items.push({ role: 'copy' });
      if (params.editFlags && params.editFlags.canPaste) {
        items.push({ label: '粘贴', click: () => win.webContents.paste() });
      }
      if (params.editFlags && params.editFlags.canSelectAll) items.push({ role: 'selectAll' });
    } else if (String(params.selectionText || '').trim()) {
      items.push({ role: 'copy' });
    }
    if (items.length) Menu.buildFromTemplate(items).popup({ window: win });
  });
  win.webContents.once('did-finish-load', () => perfLog('renderer-load-finished'));
  win.once('ready-to-show', () => { perfLog('ready-to-show'); win.show(); perfLog('window-shown'); });
  // 2026-10-09 兜底：GPU 进程连环崩的机器上 renderer 可能永远不发
  // ready-to-show，show:false 的窗口就永远不出来 = "打不开"。
  // 3.5 秒还没显示就强制 show —— 白屏也总比没有窗口强，后续
  // renderer 恢复后 UI 自然就画出来了。
  setTimeout(() => {
    try {
      if (win && !win.isDestroyed() && !win.isVisible()) {
        console.log('[window] ready-to-show 超时，强制显示');
        perfLog('window-shown-fallback');
        win.show();
      }
    } catch (e) { /* 窗口已销毁就算了 */ }
  }, 3500);
  // renderer 死了要留痕：不然"窗口白屏/打不开"这种事查无可查
  win.webContents.on('render-process-gone', (_e, d) => {
    console.log('[window] renderer gone: reason=' + d.reason + ' exitCode=' + d.exitCode);
  });
  // 外链走系统浏览器，不要在客户端里开新窗口
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  // 🔴 点关闭 = 隐藏（2026-10-09）：拦截 close，窗口藏起来、进程活着。
  // 真退出只走托盘菜单的"退出"（它先把 quitting 置真再 close）。
  // 不拦的话 window-all-closed → app.quit()，微信渠道的本地工具
  // 轮询、转发通道全部跟着一起死。
  win.on('close', (e) => {
    if (!quitting) {
      e.preventDefault();
      win.hide();
    }
  });
  win.on('closed', () => {
    win = null;
  });
}

function openSettingsWindow(options) {
  const opts = options || {};
  const section = String(opts.section || 'model');
  // 只有一份模型配置（本机内核），不再有 cloud/local 两个来源的参数
  const target = { section };
  if (settingsWin && !settingsWin.isDestroyed()) {
    if (settingsWin.isMinimized()) settingsWin.restore();
    settingsWin.show();
    settingsWin.focus();
    if (settingsWinReady) settingsWin.webContents.send('settings:navigate', target);
    else pendingSettingsTarget = target;
    return { ok: true, existing: true };
  }
  settingsWinReady = false;
  pendingSettingsTarget = null;
  settingsWin = new BrowserWindow({
    width: 1120,
    height: 790,
    minWidth: 820,
    minHeight: 600,
    parent: win && !win.isDestroyed() ? win : undefined,
    frame: false,
    backgroundColor: '#faf7f2',
    show: false,
    icon: path.join(__dirname, '..', 'assets', 'icon.ico'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      spellcheck: false,
    },
  });
  const created = settingsWin;
  created.loadFile(path.join(__dirname, '..', 'src', 'index.html'), {
    query: { window: 'settings', section },
  });
  created.once('ready-to-show', () => { if (!created.isDestroyed()) created.show(); });
  setTimeout(() => {
    try { if (!created.isDestroyed() && !created.isVisible()) created.show(); }
    catch (_) { /* window was closed before the fallback */ }
  }, 3500);
  created.webContents.once('did-finish-load', () => {
    settingsWinReady = true;
    if (pendingSettingsTarget && !created.isDestroyed()) {
      created.webContents.send('settings:navigate', pendingSettingsTarget);
      pendingSettingsTarget = null;
    }
  });
  created.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  created.on('closed', () => {
    if (settingsWin === created) settingsWin = null;
    settingsWinReady = false;
    pendingSettingsTarget = null;
  });
  return { ok: true, existing: false };
}

// ── 只伺候随包 Brain ─────────────────────────────────────────
// 地址与凭据都长在本机：sidecar 拉起 <userData>/brain 里的 exe，token 是它
// 自己生成在 config.json 里的那把。不需要人填地址、端口、token，也没有
// "连不上就换一台"的逻辑 —— 起不来就把原因报出来并允许重试。
const localtool = require('./localtool');
const oplog = require('./oplog');
const sidecar = require('./sidecar');
const pluginMedia = require('./plugin-media').createPluginMedia({
  getBase: () => connectionBase(),
  getToken,
});

// 本地内核（sidecar）一起步就养上；找不到 exe 时它自己会保持 failed 并给出原因
sidecar.init(app, readCfg);

// 随包 Brain 的地址就是唯一地址。没有"本地失败就退回某台服务器"这回事：
// 起不来就把原因摊在界面上（sidecar failed + 重试按钮），而不是偷偷连别处。
function connectionBase() {
  const fixed = testBrain();
  return fixed ? fixed.base : sidecar.base();
}

// 解包运行的端到端测试可以指向一个已经在跑的真实 Brain —— 否则每次跑
// 渲染层用例都得先打一个 160MB 的包。只认回环地址，打包后的发行版忽略它。
function testBrain() {
  if (app.isPackaged) return null;
  const raw = String(process.env.VIGIL_TEST_BRAIN || '').trim();
  if (!raw) return null;
  try {
    const u = new URL(raw);
    if (!['http:', 'https:'].includes(u.protocol)) return null;
    if (u.username || u.password || u.search || u.hash || u.pathname !== '/') return null;
    if (!/^(127\.0\.0\.1|localhost|\[::1\])$/.test(u.hostname)) return null;
    return { base: u.origin, token: String(process.env.VIGIL_TEST_TOKEN || '') };
  } catch (_) { return null; }
}

async function autoConnect() {
  const fixed = testBrain();
  if (fixed) {
    const r = await request('GET', fixed.base + '/api/runtime', null, fixed.token, 5000);
    return r.ok ? { ok: true, base: fixed.base, kind: 'test' }
      : { ok: false, err: '测试 Brain 认证失败（HTTP ' + r.status + '）', tried: [] };
  }
  sidecar.maybeStart(readCfg());
  const until = Date.now() + 120000;
  while (!sidecar.ready() && Date.now() < until) {
    const s = sidecar.status();
    if (s.status === 'failed' || s.status === 'stopped') {
      return { ok: false, err: sidecar.diagnosis(), tried: [] };
    }
    await new Promise(r => setTimeout(r, 250));
  }
  return sidecar.ready() ? { ok: true, base: sidecar.base(), kind: 'local' }
    : { ok: false, err: '本地 Brain 启动超时；不会连接其他地址。' + sidecar.diagnosis(), tried: [] };
}


// ── 本地操控：轮询 → 执行 → 送回结果 ───────────────────────────
//
// 为什么是轮询而不是 WebSocket：见 brain/localtools.py 顶部那段说明。
// 一句话——服务端零依赖、隧道/反代不用改、出问题两个 HTTP 请求就能看清。
//
// 危险操作**必须**先问人：这里发一条 IPC 给界面，等它回话。
// 60 秒没人理就当拒绝（人不在电脑前，不该替他把文件删了）。
let pollTimer = null;
let pollBusy = false;
let asking = null;

/** 一句话说清这一步要干什么（进度条上显示的） */
function describeCall(name, args) {
  const a = args || {};
  const p = a.path || a.from || a.cmd || a.name_or_pid || a.url || a.action || '';
  const MAP = {
    local_file_write: '写文件', local_file_append: '追加内容', local_mkdir: '建目录',
    local_move: '移动', local_copy: '复制', local_file_delete: '删除',
    local_process_kill: '结束进程', local_clipboard_write: '写剪贴板',
    local_exec: '执行命令',
  };
  const verb = MAP[name] || name.replace(/^local_/, '');
  let tail = String(p).slice(0, 70);
  if (name === 'local_move' || name === 'local_copy') {
    tail = String(a.from || '').slice(0, 40) + ' → ' + String(a.to || '').slice(0, 40);
  }
  return verb + (tail ? '：' + tail : '');
}

function askLocal(call) {
  return new Promise((resolve) => {
    if (!win || win.isDestroyed()) return resolve(false);
    const id = 'cf_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
    asking = { id, resolve };
    // risk 三档（M4 服务端 public() 透出）：safe 不该走到这、confirm 普通
    // 确认、dangerous 强提醒。老服务端没有 risk 字段 → 退回 danger 布尔。
    win.webContents.send('local:confirm', {
      id, name: call.name, args: call.args || {}, danger: !!call.danger,
      risk: call.risk || (call.danger ? 'confirm' : ''),
    });
    setTimeout(() => {
      if (asking && asking.id === id) { asking = null; resolve(false); }
    }, 60000);
  });
}

ipcMain.on('local:answer', (_e, msg) => {
  if (asking && asking.id === (msg && msg.id)) {
    const a = asking;
    asking = null;
    a.resolve(!!(msg && msg.allow));
  }
});

/** 向本机内核要活：有活就执行，结果送回同一个内核。
 *  以前还要再轮一遍云端队列（微信消息由云上那只答，工具调用入的是云端
 *  队列）。P8.1 起桌面端只连本机，那条双轮询随远程 Brain 一起去掉了。 */
async function pollBrain(base, token, device, caps) {
  const url = base + '/api/plugin/remote/poll?caps=' + encodeURIComponent(caps.join(',')) +
    '&device=' + encodeURIComponent(device || '');
  const r = await request('GET', url, null, token, 8000);
  const call = r.json && r.json.call;
  if (!call) return;                            // 没活是常态
  let result;
  let backup = null;
  // 三档 risk（M4）：safe 直接干；confirm 普通确认；dangerous 强确认。
  // 老服务端只有 danger 布尔 → risk 缺失时按老逻辑（danger 才问）。
  const risk = String(call.risk || (call.danger ? 'confirm' : 'safe'));
  const needsAsk = risk !== 'safe' || call.danger ||
    localtool.DANGER.has(call.name);
  // 让她那边/你这边都能看见"正在做第几步"：先报"要做什么"，
  // 危险操作在等确认时也报一条（否则界面上会静默几十秒）。
  if (needsAsk) {
    send('local:step', { name: call.name, args: call.args, kind: 'danger',
                         risk,
                         text: describeCall(call.name, call.args) });
    // 自动模式（权限菜单里选了"白名单内自动执行/完全访问"）不再逐条问：
    // 拦截靠系统目录硬拦 + 白名单，出事靠备份撤销 + 操作日志兜底。
    const allow = readCfg().localAuto ? true : await askLocal(call);
    if (!allow) {
      result = '__refused__';
      oplog.append({ tool: call.name, args: call.args, source: 'her',
                     ok: false, text: '你拒绝了这次操作', backup: null });
      send('local:step', { name: call.name, kind: 'err', text: '你拒绝了' });
    } else {
      const r = await localtool.run(call.name, call.args, readCfg());
      result = r.text;
      backup = r.backup;
      send('local:step', { name: call.name, kind: /出错|失败|拒绝/.test(r.text) ? 'err' : 'ok',
                           text: r.text.split('\n')[0] });
    }
  } else {
    const r = await localtool.run(call.name, call.args, readCfg());
    result = r.text;
    backup = r.backup;
    send('local:step', { name: call.name, kind: 'ok', text: r.text.split('\n')[0] });
  }
  if (result !== '__refused__') {
    // 她做的每一件事都留痕：不然你根本不知道她动过什么
    oplog.append({ tool: call.name, args: call.args, source: 'her',
                   ok: true, text: result, backup: backup });
  }
  await request('POST', base + '/api/plugin/remote/result',
    { call_id: call.call_id, result: result }, token);
  send('local:done', { name: call.name });
}

async function pollOnce() {
  if (pollBusy || asking) return;
  const cfg = readCfg();
  // 与 api IPC 保持一致：本地 Brain 启动后会选择动态端口，配置里没有地址
  // 可存。若轮询另找地址，聊天正常她却收不到 local_file_* 的活，Brain 还会
  // 误报"本地客户端没连上"。
  const base = connectionBase();
  if (!base) return;
  const caps = localtool.capabilities(cfg);
  if (!caps.length) return;                       // 全关了就别浪费请求
  pollBusy = true;
  try {
    const token = await getToken();
    await pollBrain(base, token, cfg.device_id, caps);
  } catch (e) {
    // 轮询失败不打扰用户：网络抖一下很正常，下一轮接着来
  } finally {
    pollBusy = false;
  }
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(pollOnce, 1500);
  pollOnce();
}

// ── IPC ──────────────────────────────────────────────────────────
// 插件前端注入（收紧版）：渲染层**不能**再把任意代码塞给主进程执行
// （旧的 run-js 通道等于 renderer → executeJavaScript 的任意代码桥，
//  插件 JS 或 XSS 都能借它执行任意脚本）。现在由主进程自己拉
// /api/plugins/frontend（带服务端 token，渲染层碰不到），只注入
// 管理员已审核安装的插件代码；渲染层能做的只是"请加载一次"。
ipcMain.handle('plugins:load', async () => {
  try {
    if (!win || win.isDestroyed()) return { ok: false, loaded: 0, err: '窗口没准备好' };
    const cfg0 = readCfg();
    const sidecarOk = sidecar.ready && sidecar.ready() && sidecar.base();
    const b = connectionBase(cfg0);
    if (!b) return {ok:false,status:0,err:sidecar.status().lastErr || '本地 Brain 未就绪'};
    const token = await getToken();
    const r = await request('GET', b + '/api/plugins/frontend', null, token, 8000);
    if (!r.ok || !r.json) return { ok: false, loaded: 0, err: '拉取插件资源失败' };
    const plugins = Array.isArray(r.json.plugins) ? r.json.plugins : [];
    let loaded = 0;
    const errors = [];
    for (const p of plugins) {
      if (!p.enabled || !p.js) continue;
      const name = String(p.name || '').slice(0, 40).replace(/[^a-zA-Z0-9_-]/g, '');
      const wrapped =
        '(function(){\nconst vp=window.VigilPlugin;const prev=vp&&vp._currentOwner;if(vp)vp._currentOwner=' + JSON.stringify(name) + ';\ntry{\n' + String(p.js) +
        '\n}catch(e){console.error("[插件 ' + name + ' 前端出错]",e);}\nfinally{if(vp)vp._currentOwner=prev;}\n})();';
      try {
        await win.webContents.executeJavaScript(wrapped);
        loaded += 1;
      } catch (err) {
        console.error('[plugins:load] 插件', name, '执行失败:', err);
        errors.push(name + '（' + String(err && err.message || err).slice(0, 80) + '）');
      }
    }
    return { ok: true, loaded, version: r.json.version || 0, errors };
  } catch (err) {
    return { ok: false, loaded: 0, err: String(err && err.message || err) };
  }
});
ipcMain.handle('plugins:resource-url', async (_e, resource) => {
  try { return { ok: true, url: await pluginMedia.url(resource) }; }
  catch (e) { return { ok: false, err: e.message }; }
});
ipcMain.handle('plugins:release-resources', () => {
  pluginMedia.releaseResources();
  return { ok: true };
});
ipcMain.handle('app:version', () => app.getVersion());
ipcMain.handle('app:instance-id', () => APP_PROCESS_INSTANCE_ID);
ipcMain.handle('project-picker:directory', async () => {
  if (!win || win.isDestroyed()) return { ok: false, err: '窗口尚未准备好' };
  try {
    const result = await dialog.showOpenDialog(win, {
      title: '选择 Git 仓库根目录（包含 .git 的文件夹）',
      properties: ['openDirectory'],
    });
    if (result.canceled || !result.filePaths || !result.filePaths[0]) {
      return { ok: true, canceled: true };
    }
    return { ok: true, path: result.filePaths[0] };
  } catch (e) {
    return { ok: false, err: String(e && e.message || e).slice(0, 160) };
  }
});

// ── 工作区（一个真实文件夹）─────────────────────────────────────
// 只做两件事：让用户用原生选择器指定一个目录；回答"这个目录现在还在不在"。
// 两者都不读文件内容，也不改变 localtool 的授权 —— 选中文件夹不等于拿到
// 读写/执行权限，那边该弹的确认框一个都不会少。
function workspacePath(value) {
  const text = String(value == null ? '' : value).trim();
  if (!text || text.length > 1024) return '';
  if (/[\u0000-\u001f]/.test(text)) return '';
  if (!path.isAbsolute(text)) return '';
  try { return path.normalize(text); } catch (_) { return ''; }
}
ipcMain.handle('workspace:pick', async () => {
  if (!win || win.isDestroyed()) return { ok: false, err: '窗口尚未准备好' };
  try {
    const result = await dialog.showOpenDialog(win, {
      title: '选择一个本地文件夹作为工作区',
      buttonLabel: '作为工作区打开',
      properties: ['openDirectory'],
    });
    if (result.canceled || !result.filePaths || !result.filePaths[0]) {
      return { ok: true, canceled: true };
    }
    return { ok: true, path: workspacePath(result.filePaths[0]) || result.filePaths[0] };
  } catch (e) {
    return { ok: false, err: String(e && e.message || e).slice(0, 160) };
  }
});
ipcMain.handle('workspace:probe', async (_e, raw) => {
  const target = workspacePath(raw);
  if (!target) return { ok: false, err: '路径不是有效的绝对路径' };
  try {
    const st = await fs.promises.stat(target);
    if (!st.isDirectory()) return { ok: true, path: target, exists: true, directory: false, readable: false };
    try {
      await fs.promises.access(target, fs.constants.R_OK);
      return { ok: true, path: target, exists: true, directory: true, readable: true };
    } catch (_) {
      return { ok: true, path: target, exists: true, directory: true, readable: false };
    }
  } catch (e) {
    const code = String((e && e.code) || '');
    // ENOENT / ENOTDIR 说明目录被删了或移走了；EPERM / EACCES 是权限。
    if (code === 'EPERM' || code === 'EACCES') {
      return { ok: true, path: target, exists: false, directory: false, readable: false, permission: true };
    }
    return { ok: true, path: target, exists: false, directory: false, readable: false, code: code || 'unknown' };
  }
});
ipcMain.handle('auto:connect', () => autoConnect());
ipcMain.handle('local:defaults', () => ({
  roots: localtool.defaultRoots(),
  commands: localtool.CMD_ALLOW.slice(),
  danger: [...localtool.DANGER],
}));
ipcMain.handle('local:caps', () => localtool.capabilities(readCfg()));

// 工具页：目录、手动运行、操作日志、撤销
ipcMain.handle('local:tools', () => ({
  groups: localtool.GROUP_NAME,
  items: localtool.CATALOG.map((t) => ({
    ...t,
    enabled: localtool.capabilities(readCfg()).includes(t.name),
  })),
}));

ipcMain.handle('local:run', async (_e, { name, args }) => {
  // 🔴 主进程是最后一道：危险工具不管调用方是工具页还是插件 JS，
  // 都必须走同一个确认气泡（auto 模式除外，那是用户显式选的全自动）。
  // 不能只靠渲染层的"我确认"勾选 —— 注入的插件代码能直接调 local.run，
  // 但弹不弹、用户点不点，在这个主进程分支里说了算。
  if (localtool.DANGER.has(name)) {
    const allow = readCfg().localAuto
      ? true
      : await askLocal({ name, args: args || {}, danger: true, risk: 'confirm' });
    if (!allow) {
      const rec = oplog.append({ tool: name, args: args || {}, source: 'me',
                                 ok: false, text: '你拒绝了这次操作', backup: null });
      return { ok: true, refused: true, text: '你拒绝了这次操作',
               backup: null, id: rec && rec.id };
    }
  }
  const r = await localtool.run(name, args, readCfg());
  const rec = oplog.append({
    tool: name, args: args || {}, source: 'me',
    ok: !/出错|拒绝|不在白名单|读不到|不让|撤不回来/.test(r.text),
    text: r.text, backup: r.backup,
  });
  return { ok: true, text: r.text, backup: r.backup, id: rec && rec.id };
});

ipcMain.handle('local:log', (_e, { limit } = {}) => oplog.list(limit));

ipcMain.handle('local:undo', async (_e, { id }) => {
  const rec = oplog.get(id);
  if (!rec) return { ok: false, err: '没有这条记录' };
  if (rec.undone) return { ok: false, err: '已经撤过了' };
  if (!rec.backup) return { ok: false, err: '这条没有备份，撤不回来' };
  const text = await localtool.restore(rec.backup);
  const ok = /已还原|已撤销/.test(text);
  if (ok) oplog.markUndone(id);
  oplog.append({ tool: 'undo:' + rec.tool, args: rec.args, source: 'me',
                 ok, text: text, backup: null });
  return { ok, text };
});

ipcMain.handle('local:reveal', (_e, p) => {
  // 打开备份/结果所在的文件夹（截图、备份文件要能直接看到）
  try { shell.showItemInFolder(String(p || '')); return { ok: true }; }
  catch (e) { return { ok: false, err: String(e && e.message || e) }; }
});

// 手动检查更新（关于页按钮）。结果回给渲染层画状态，弹窗逻辑在 checkUpdate 里。
// 渲染层只能给 'ask' 或默认 'status'——不会绕过弹窗直接下载。
ipcMain.handle('updater:check', (_e, mode) => checkUpdate(mode === 'ask' ? 'ask' : 'status'));

ipcMain.handle('api', async (_e, { method = 'GET', path: p = '/', body = null, base = null, timeoutMs = null }) => {
  if (!p.startsWith('/')) return { ok: false, status: 0, err: '路径要以 / 开头' };
  // 只有本机 Brain 一个目标。渲染层（含被注入的插件 JS）传任何 base 都拒绝 ——
  // 以前可以指任意 URL，主进程会把 token 带过去，等于凭据外传口。
  if (base != null) {
    return { ok: false, status: 0, err: '不允许的请求目标：桌面客户端只连接本机 Brain' };
  }
  const cfg = readCfg();
  // sidecar 动态端口优先（重启后端口会变）
  const b = connectionBase();
  if (!b) {
    const result = {ok:false,status:0,err:sidecar.diagnosis() || '本地 Brain 未就绪'};
    return { ...result, diagnostic: apiDiagnostic(result, method, p.split('?')[0]) };
  }
  const src = cfg.src || CLIENT_SRC;
  // 渠道用 query 带上：服务端按 src 分区（features / history / persona 都吃这个）。
  // URL 里已经写了 src 的（数据页看别的渠道历史）尊重写死的那个。
  const sep = p.includes('?') ? '&' : '?';
  const wantsSrc = ['/api/features', '/api/chatty', '/api/history', '/api/persona', '/api/memory']
    .some((k) => p.startsWith(k));
  const hasSrc = /(?:\?|&)src=/.test(p);
  const withSrc = wantsSrc && !hasSrc ? p + sep + 'src=' + encodeURIComponent(src) : p;
  const token = await getToken();
  const centerTimeout = p.startsWith('/api/agent-center/') ? (method === 'GET' ? 5000 : 45000) : undefined;
  const requestedTimeout = Number.isFinite(timeoutMs) ? Math.max(1000, Math.min(30000, timeoutMs)) : centerTimeout;
  const perfLabel = p === '/api/persona' ? 'connect.persona' : '';
  const r = await request(method, b + withSrc, body, token, requestedTimeout, perfLabel);
  // Report the actual destination, without credentials, query strings or paths.
  const target = new URL(b).origin;
  const diagnostic = r.ok ? undefined : apiDiagnostic(r, method, p.split('?')[0]);
  if (!r.ok && r.status === 401) return { ...r, target,
    err: 'token 不对（本机 Brain 的凭据被换过？设置 → 本地内核 可重启它）', diagnostic };
  return { ...r, target, diagnostic };
});

ipcMain.handle('attachment:chunk', async (_e, { bytes, headers = {} } = {}) => {
  const b = connectionBase();
  if (!b) return {ok:false,status:0,err:sidecar.diagnosis() || '本地 Brain 未就绪'};
  const safeHeaders = {};
  for (const key of ['X-Attachment-Id', 'X-Attachment-Name', 'X-Attachment-Mime',
    'X-Attachment-Total', 'X-Attachment-Offset', 'X-Attachment-Confirmed']) {
    if (headers[key] != null) safeHeaders[key] = String(headers[key]);
  }
  const token = await getToken();
  return requestBinary('POST', String(b).replace(/\/+$/, '') + '/api/attachments/upload',
    bytes, token, safeHeaders, 120000);
});

// 流式聊天：主进程发 SSE 请求，逐块通过 webContents.send 推给渲染层。
// 渲染层调用 api:stream 开始，然后监听 api:stream:event 接收事件。
ipcMain.handle('api:stream', async (e, { path: p = '/', body = null, base = null }) => {
  if (!p.startsWith('/')) return { ok: false, status: 0, err: '路径要以 / 开头' };
  // 同 'api'：流式通道也只认本机 Brain（防 token 外传）。
  if (base != null) {
    return { ok: false, status: 0, err: '不允许的请求目标：桌面客户端只连接本机 Brain' };
  }
  const cfg = readCfg();
  const b = connectionBase();
  if (!b) return {ok:false,status:0,err:sidecar.diagnosis() || '本地 Brain 未就绪'};
  const src = cfg.src || CLIENT_SRC;
  const sep = p.includes('?') ? '&' : '?';
  const hasSrc = /(?:\?|&)src=/.test(p);
  const withSrc = !hasSrc ? p + sep + 'src=' + encodeURIComponent(src) : p;
  const token = await getToken();
  const wc = e.sender;
  const subscription = /^\/api\/creator\/jobs\/[A-Za-z0-9_-]+\/events(?:\?|$)/.test(p);
  const isTerminalEvent = subscription
    ? (evt) => evt.type === 'snapshot' &&
      ['succeeded', 'failed', 'cancelled', 'interrupted'].includes(evt.payload?.status)
    : null;
  const resumableAgentChat = p === '/api/chat/stream' && body &&
    typeof body.request_id === 'string' && body.request_id.length > 0;
  let agentRunId = null;
  let agentCursor = 0;
  let resumeCount = 0;
  let lastConnectionError = null;
  const onEvent = (event) => {
    if (resumableAgentChat && event.request_id && event.request_id !== body.request_id) return;
    // Every IPC subscriber shares one bus. Bind even legacy terminal/tool
    // projections to the originating request before publishing them.
    let evt = resumableAgentChat ? {...event,request_id:body.request_id,
      conversation_id:body.conversation_id || body.session_id || ''} : event;
    if (resumableAgentChat && agentRunId && evt.type==='error' && !evt.run_id)
      evt={...evt,run_id:agentRunId};
    if (resumableAgentChat && evt.type === 'agent_run' && evt.run_id &&
        (!evt.request_id || evt.request_id === body.request_id)) agentRunId = evt.run_id;
    if (resumableAgentChat && agentRunId && evt.run_id === agentRunId &&
        Number.isSafeInteger(evt.seq) && evt.seq >= agentCursor) agentCursor = evt.seq;
    if (resumableAgentChat && agentRunId && evt.type === 'error' &&
        evt.class === 'connection_lost') {
      lastConnectionError = evt;
      return; // Keep the renderer run open while the bounded read-only resume runs.
    }
    if (subscription && !evt.job_id) evt = { ...evt, job_id: p.split('/')[4] };
    if (!wc.isDestroyed()) wc.send('api:stream:event', evt);
  };
  let requestUrl = b + withSrc;
  for (;;) {
    const result = await requestStream(subscription ? 'GET' : 'POST', requestUrl,
      subscription ? null : body, token, onEvent, isTerminalEvent);
    if (!resumableAgentChat || !agentRunId || result.ok || !lastConnectionError || resumeCount >= 3) {
      if (resumableAgentChat && agentRunId && !result.ok && lastConnectionError && !wc.isDestroyed())
        wc.send('api:stream:event', lastConnectionError);
      return result;
    }
    // Agent runs are durable and request_id is idempotent. Resume only after
    // the stream has confirmed the run identity; never replay a legacy chat or
    // an unconfirmed request whose side effects may be uncertain.
    resumeCount += 1;
    await new Promise(resolve => setTimeout(resolve, Math.min(200 * (2 ** (resumeCount - 1)), 800)));
    const resumeUrl = new URL(b + withSrc);
    resumeUrl.searchParams.set('after', String(agentCursor));
    requestUrl = resumeUrl.toString();
  }
});

// 渲染层只看到"本机配置 + 当前地址"。没有连接模式可选：地址永远来自 sidecar。
ipcMain.handle('cfg:get', () => ({ ...readCfg(), base: connectionBase() }));
ipcMain.handle('cfg:set', (_e, patch) => ({ ...writeCfg(patch), base: connectionBase() }));

// ── 本地内核（sidecar）────────────────────────────────────────────
ipcMain.handle('sidecar:status', () => sidecar.status());
ipcMain.handle('sidecar:restart', () => {
  if (testBrain()) return { ok: true, external: true };
  sidecar.restart(readCfg());
  return { ok: true };
});

// 向导的"填 key 即用"：走内核现成的 /api/models/apply —— 它会落盘 + 热加载，
// 内存/磁盘两边一起到位，不用重启 sidecar
ipcMain.handle('sidecar:apply-model', async (_e, patch) => {
  if (!sidecar.ready()) return { ok: false, err: '本地内核没在运行' };
  const tok = await getToken();
  const r = await request('POST', sidecar.base() + '/api/models/apply', patch || {}, tok);
  return r.ok ? { ok: true, json: r.json } : { ok: false, err: r.err || ('HTTP ' + r.status), json: r.json };
});
// 向导拿服务商清单：让内核自己报（PROVIDERS 在它那边，别在客户端抄一份）
ipcMain.handle('sidecar:providers', async () => {
  if (!sidecar.ready()) return { ok: false, err: '本地内核没在运行' };
  const tok = await getToken();
  const r = await request('GET', sidecar.base() + '/api/models', null, tok);
  return r.ok ? { ok: true, json: r.json } : { ok: false, err: r.err || ('HTTP ' + r.status) };
});

// ── 本机凭据：只用来告诉自己"Brain 是不是活的" ──────────────────
// P8.1 起不再向任何服务器注册设备、也不再有主人钥匙/设备 token 两套凭据：
// 本机 Brain 的 token 就在它自己的配置文件里，由 sidecar 直接读。
ipcMain.handle('token:get', () => (sidecar.ready() ? '本地 Brain' : ''));
ipcMain.handle('token:test', async () => {
  const b = connectionBase();
  if (!b) return { ok: false, status: 0, err: sidecar.diagnosis() || 'Brain 未就绪' };
  const r = await request('GET', b + '/api/persona', null, await getToken());
  return { ok: r.ok, status: r.status, err: r.err || '', name: (r.json && r.json.name) || '' };
});
ipcMain.handle('shell:open', (_e, url) => shell.openExternal(String(url || '')));
ipcMain.handle('settings:open', (_e, options) => openSettingsWindow(options));

const send = (ch, ...a) => win && !win.isDestroyed() && win.webContents.send(ch, ...a);
const senderWindow = (event) => BrowserWindow.fromWebContents(event.sender);
ipcMain.on('win:minimize', (event) => {
  const target = senderWindow(event);
  if (target && !target.isDestroyed()) target.minimize();
});
ipcMain.on('win:maximize', (event) => {
  const target = senderWindow(event);
  if (!target || target.isDestroyed()) return;
  target.isMaximized() ? target.unmaximize() : target.maximize();
  target.webContents.send('win:state', target.isMaximized());
});
// 自绘标题栏的 ✕：走 win.close() → 被 close 拦截器接住 → 隐藏进托盘
// （真退出在托盘右键菜单，见 createTray。2026-10-09 起"点关闭=隐藏"）
ipcMain.on('win:close', (event) => {
  const target = senderWindow(event);
  if (target && !target.isDestroyed()) target.close();
});

// ── 生命周期 ──────────────────────────────────────────────────────
// 单实例：两个客户端同时跑会互相覆盖配置、也会把聊天历史对账搞乱
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) {
      win.isMinimized() && win.restore();
      showMainWindow();
    }
  });
  app.whenReady().then(() => {
    perfLog('electron-ready');
    Menu.setApplicationMenu(null);   // 自绘标题栏，不要系统菜单
    oplog.init(app.getPath('userData'));
    // 旧版配置里可能写着远程地址 / 设备 token / 云端同步：先归档，再启动内核。
    migrateCfg();
    const updates=require('./update-transaction');
    let recoveryShown=false;
    sidecar.onState(async s=>{
      send('sidecar:state',s);
      if(s.status==='ready') updates.markHealthy(app.getPath('userData'),s.build);
      if(s.status==='failed' && !recoveryShown && updates.pending(app.getPath('userData'))){
        recoveryShown=true;
        const choice=await dialog.showMessageBox({type:'error',title:'Vigil 更新未就绪',message:s.lastErr,
          detail:'本地 Brain 没能起来。可恢复备份中的 Electron 与 Brain 整套程序；用户数据保留。',buttons:['保留现场','恢复上一版'],defaultId:0,cancelId:0});
        if(choice.response===1){updates.launchRollback(app.getPath('userData'));quitting=true;app.quit();}
      }
    });
    if (testBrain()) {
      console.log('[测试] 使用外部 Brain：' + testBrain().base + '（不启动随包 sidecar）');
    } else {
      sidecar.maybeStart(readCfg());   // 启动即拉起随包 Brain，不等第一次聊天
    }
    createWindow();
    createTray();                    // 常驻托盘：点关闭只是隐藏（2026-10-09）
    startPolling();                  // 本地操控的活从这儿进来
    // 推送更新：启动 15s 后查一次（错开启动时的连接风暴），之后每小时
    setTimeout(() => { checkUpdate(false); }, 15000);
    setInterval(() => { checkUpdate(false); }, 3600000);
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });
}

// 真退出前的放行标记（托盘"退出"已把 quitting 置真；系统关机/注销
// 也会走 before-quit —— 那时必须放行 close，不然关不了机）
app.on('before-quit', () => { quitting = true; pluginMedia.close(); });

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
