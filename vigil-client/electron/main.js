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
const { app, BrowserWindow, ipcMain, shell, Menu } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const https = require('https');

// 这个客户端就是表单+列表+聊天气泡，用不上 GPU 合成。禁掉硬件加速：
// ① 核显/驱动有毛病的机器（GPU 进程反复崩溃会把整个渲染进程拖死）
// ② 无 GPU 的远程/虚拟机环境 —— 都能正常开窗。
app.disableHardwareAcceleration();

const DEFAULT_BASE = 'http://127.0.0.1:18787';   // 桌宠走 SSH 隧道；App 直连换这个地址
// 客户端自己的渠道：独立于手机 App / 微信，聊天、历史、记忆都落在 pc 分区。
// 不是可选项 —— 这个客户端就是一台控制台，不顶替任何别的渠道。
const CLIENT_SRC = 'pc';

let win = null;

// ── 配置：地址、渠道这些非机密放普通文件；token 单独进钥匙串 ──────────────
const cfgPath = () => path.join(app.getPath('userData'), 'config.json');

function readCfg() {
  try {
    const j = JSON.parse(fs.readFileSync(cfgPath(), 'utf8'));
    // src 强制 pc：老配置里存的 app/wechat 是旧版"顶替渠道"的遗留，不回读
    return { base: DEFAULT_BASE, ...(j || {}), src: CLIENT_SRC };
  } catch (e) {
    return { base: DEFAULT_BASE, src: CLIENT_SRC };
  }
}

function writeCfg(patch) {
  const next = { ...readCfg(), ...(patch || {}) };
  const dir = path.dirname(cfgPath());
  try {
    fs.mkdirSync(dir, { recursive: true });
    // 原子写：半截的配置会让客户端下次启动直接读不到地址
    const tmp = cfgPath() + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(next, null, 2), 'utf8');
    fs.renameSync(tmp, cfgPath());
  } catch (e) {
    console.warn('[配置] 写入失败：', e.message);
  }
  return next;
}

// keytar 是原生模块，打包/换机器时可能加载不了。加载不了就退回文件存 ——
// 但**要让人知道**，不能静默把密钥写成明文还以为在钥匙串里。
let keytar = null;
try {
  keytar = require('keytar');
} catch (e) {
  console.warn('[配置] keytar 不可用（' + e.message + '），token 将退回本地明文文件');
}
const KEY_SERVICE = 'vigil-client';
const KEY_ACCOUNT = 'brain-token';
const tokenFile = () => path.join(app.getPath('userData'), 'token.json');

async function getToken() {
  const cfg = readCfg();
  // 设备模式：用设备 token（不用 keytar 里可能还是主人钥匙的旧槽位）。
  // 读取顺序必须和 setToken 的写入顺序**对称**（都是钥匙串优先）：
  // 曾经这边文件优先，结果 setToken 更新了钥匙串、token.json 停在上一把
  // token，一读到旧值就直接用 → 全部 401（2026-10-09 实锤一次：大脑设备表
  // 重建后客户端看起来"注册成功"却永远拿旧 token 被拒）。
  if (cfg.token_kind === 'device' && cfg.device_id) {
    if (keytar) {
      try {
        const t = await keytar.getPassword(KEY_SERVICE, KEY_ACCOUNT);
        if (t) return t;
      } catch (e) { /* 读不到就算了，往下走文件兜底 */ }
    }
    try {
      const j = JSON.parse(fs.readFileSync(tokenFile(), 'utf8'));
      if (j.token) return j.token;
    } catch (e) { /* 文件没有/坏了 */ }
    return '';
  }
  if (keytar) {
    try {
      return (await keytar.getPassword(KEY_SERVICE, KEY_ACCOUNT)) || '';
    } catch (e) {
      console.warn('[配置] 读钥匙串失败：', e.message);
    }
  }
  try {
    return JSON.parse(fs.readFileSync(tokenFile(), 'utf8')).token || '';
  } catch (e) {
    return '';
  }
}

async function setToken(token) {
  const t = String(token || '');
  let store = null;
  if (keytar) {
    try {
      if (t) await keytar.setPassword(KEY_SERVICE, KEY_ACCOUNT, t);
      else await keytar.deletePassword(KEY_SERVICE, KEY_ACCOUNT);
      store = 'keytar';
    } catch (e) {
      console.warn('[配置] 写钥匙串失败：', e.message);
    }
  }
  // 两个存储**都写**（文件永远陪写一份）：只写钥匙串的话 token.json 会停在
  // 上一把 token，哪天一边比另一边新，读哪边都是赌（2026-10-09 的 401 事故
  // 就是这么来的）。
  try {
    fs.writeFileSync(tokenFile(), JSON.stringify({ token: t }), 'utf8');
    store = store ? store + '+file' : 'file';
  } catch (e) {
    if (!store) return { ok: false, err: e.message };
    console.warn('[配置] 写 token 文件失败（钥匙串已更新）：', e.message);
  }
  return {
    ok: true,
    store,
    warn: store === 'file' ? 'keytar 不可用，token 存在本地文件里' : '',
  };
}

// ── HTTP：一个入口，鉴权头在这里统一加 ──────────────────────────────
function request(method, urlStr, body, token) {
  return new Promise((resolve) => {
    let u;
    try {
      u = new URL(urlStr);
    } catch (e) {
      return resolve({ ok: false, status: 0, err: '地址不合法：' + urlStr });
    }
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
        timeout: 120000,           // 对话生成可能跑几十秒，别按默认 2 分钟一刀切
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
          resolve({ ok: res.statusCode >= 200 && res.statusCode < 300, status: res.statusCode, json, text });
        });
      }
    );
    req.on('timeout', () => {
      req.destroy(new Error('请求超时'));
    });
    req.on('error', (e) => resolve({ ok: false, status: 0, err: e.message }));
    if (data) req.write(data);
    req.end();
  });
}

// 流式请求：SSE 逐块推送，onEvent 每收到一个 data: 事件就调用一次。
// 用于 /api/chat/stream 的实时思考过程和正文。
function requestStream(method, urlStr, body, token, onEvent) {
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
                  onEvent && onEvent(evt);
                } catch (e) { /* 解析失败的行跳过 */ }
              }
            }
          }
        });
        res.on('end', () => resolve({ ok: res.statusCode >= 200 && res.statusCode < 300, status: res.statusCode }));
      }
    );
    req.on('timeout', () => req.destroy(new Error('请求超时')));
    req.on('error', (e) => resolve({ ok: false, status: 0, err: e.message }));
    if (data) req.write(data);
    req.end();
  });
}

// ── 窗口 ─────────────────────────────────────────────────────────
function createWindow() {
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
  win.loadFile(path.join(__dirname, '..', 'src', 'index.html'));
  win.once('ready-to-show', () => win.show());
  // 外链走系统浏览器，不要在客户端里开新窗口
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  win.on('closed', () => {
    win = null;
  });
}

// ── 自动找大脑 + 自动注册设备 ─────────────────────────────────
// 两步，都不需要人填任何东西：
//
// 1. brain 有个**免鉴权**的 `/api/panel/connect-config`，只认回环地址，
//    会把 token 和它自己建议的地址回给你（Web 面板靠它自动配置）。
//    只要 brain 从本机可达（桌宠那条 SSH 隧道 18787，或本机跑 brain 的 8788）就通。
// 2. 拿到的这把是"主人钥匙"（brain_token）。**不直接用它聊天**，而是换一把
//    本机专用的设备 token：能单独吊销、设备列表里看得见是哪台，
//    主人钥匙也不再落在客户端上。换不到（老版 brain）才退回用主人钥匙。
const os = require('os');
const localtool = require('./localtool');
const oplog = require('./oplog');
const sidecar = require('./sidecar');
const PROBE = ['http://127.0.0.1:18787', 'http://127.0.0.1:8788'];

// 本地大脑（sidecar）一起步就养上；找不到 exe 时它自己会保持 stopped
sidecar.init(app, readCfg);

async function registerThisDevice(base, brainToken) {
  const cfg = readCfg();
  // 已有设备 token 先验一下，**有效就复用、不再注册**。不然每次启动/
  // 每次重连都发一台"新设备"，设备表一天能攒十几台同名垃圾
  // （2026-10-09 实锤：12 台全叫 adw）。服务端故意不做同名去重
  // （两台真电脑可能同名），所以复用只能靠客户端自觉。
  if (cfg.token_kind === 'device' && cfg.device_id) {
    const old = await getToken();
    if (old) {
      const chk = await request('GET', base + '/api/device/list', null, old);
      if (chk.ok && chk.json && chk.json.ok && chk.json.me === cfg.device_id) {
        return { ok: true, device_id: cfg.device_id, reused: true };
      }
    }
    // 401/不匹配 → 掉到下面重新注册，正好完成自愈
  }
  const r = await request('POST', base + '/api/device/register', {
    device_name: os.hostname() || '这台电脑',
    device_type: 'desktop',
    // 声明这台机器能干什么：服务端只会把能力对得上的活派过来
    capabilities: localtool.capabilities(cfg),
  }, brainToken);
  if (r.ok && r.json && r.json.device_token) {
    await setToken(r.json.device_token);
    writeCfg({ device_id: r.json.device_id, token_kind: 'device' });
    return { ok: true, device_id: r.json.device_id, name: r.json.device_name };
  }
  // 老版 brain 没有这个接口 → 沿用主人钥匙，并在界面上标出来
  await setToken(brainToken);
  writeCfg({ token_kind: 'brain', device_id: '' });
  return { ok: false, err: (r.json && r.json.err) || r.err || ('HTTP ' + r.status) };
}

async function autoConnect() {
  const cfg = readCfg();
  const cands = [];
  // 本地大脑优先：exe 在、没被关掉，就等它就绪（最多 30s，首启要解包/下模型）
  if (cfg.sidecar !== false) {
    const st = sidecar.status();
    if (st.exe) {
      if (!st.enabled) sidecar.maybeStart(cfg);
      const deadline = Date.now() + 30000;
      while (!sidecar.ready() && Date.now() < deadline) {
        const s = sidecar.status();
        if (s.status === 'failed' || s.status === 'stopped') break;
        await new Promise((r) => setTimeout(r, 500));
      }
      if (sidecar.ready()) cands.push(sidecar.base());
    }
  }
  if (cfg.base) cands.push(cfg.base);              // 你填过就先试它
  PROBE.forEach((b) => { if (!cands.includes(b)) cands.push(b); });
  const tried = [];
  for (const raw of cands) {
    const base = String(raw).replace(/\/+$/, '');
    const r = await request('GET', base + '/api/panel/connect-config', null, '');
    if (r.status === 200 && r.json && r.json.ok && r.json.token) {
      const server = String(r.json.server || base).replace(/\/+$/, '');
      writeCfg({ base: server });
      const reg = await registerThisDevice(server, r.json.token);
      return { ok: true, base: server, kind: reg.ok ? 'device' : 'brain',
               device_id: reg.device_id || '', name: reg.name || '',
               regErr: reg.ok ? '' : reg.err, tried };
    }
    if (r.status === 403) {
      // 地址是通的，只是它不认为请求来自本机 → 只能用设备 token，让用户补
      writeCfg({ base: base });
      tried.push(base + ' → 能连上，但服务端不认这是本机（403）');
      return { ok: false, needToken: true, base: base, tried };
    }
    tried.push(base + ' → ' + (r.err || ('HTTP ' + r.status)));
  }
  return { ok: false, tried };
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

async function pollOnce() {
  if (pollBusy || asking) return;
  const cfg = readCfg();
  if (!cfg.base) return;
  const caps = localtool.capabilities(cfg);
  if (!caps.length) return;                       // 全关了就别浪费请求
  pollBusy = true;
  try {
    const token = await getToken();
    const base = String(cfg.base).replace(/\/+$/, '');
    const url = base + '/api/plugin/remote/poll?caps=' + encodeURIComponent(caps.join(',')) +
      '&device=' + encodeURIComponent(cfg.device_id || '');
    const r = await request('GET', url, null, token);
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
      const allow = cfg.localAuto ? true : await askLocal(call);
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

ipcMain.handle('api', async (_e, { method = 'GET', path: p = '/', body = null, base = null }) => {
  if (!p.startsWith('/')) return { ok: false, status: 0, err: '路径要以 / 开头' };
  const cfg = readCfg();
  const b = (base || cfg.base || DEFAULT_BASE).replace(/\/+$/, '');
  const src = cfg.src || CLIENT_SRC;
  // 渠道用 query 带上：服务端按 src 分区（features / history / persona 都吃这个）。
  // URL 里已经写了 src 的（数据页看别的渠道历史）尊重写死的那个。
  const sep = p.includes('?') ? '&' : '?';
  const wantsSrc = ['/api/features', '/api/chatty', '/api/history', '/api/persona', '/api/memory']
    .some((k) => p.startsWith(k));
  const hasSrc = /(?:\?|&)src=/.test(p);
  const withSrc = wantsSrc && !hasSrc ? p + sep + 'src=' + encodeURIComponent(src) : p;
  const token = await getToken();
  const r = await request(method, b + withSrc, body, token);
  if (!r.ok && r.status === 401) return { ...r, err: 'token 不对（设置 → 连接与设备）' };
  return r;
});

// 流式聊天：主进程发 SSE 请求，逐块通过 webContents.send 推给渲染层。
// 渲染层调用 api:stream 开始，然后监听 api:stream:event 接收事件。
ipcMain.handle('api:stream', async (e, { path: p = '/', body = null, base = null }) => {
  if (!p.startsWith('/')) return { ok: false, status: 0, err: '路径要以 / 开头' };
  const cfg = readCfg();
  const b = (base || cfg.base || DEFAULT_BASE).replace(/\/+$/, '');
  const src = cfg.src || CLIENT_SRC;
  const sep = p.includes('?') ? '&' : '?';
  const hasSrc = /(?:\?|&)src=/.test(p);
  const withSrc = !hasSrc ? p + sep + 'src=' + encodeURIComponent(src) : p;
  const token = await getToken();
  const wc = e.sender;
  return requestStream('POST', b + withSrc, body, token, (evt) => {
    wc.send('api:stream:event', evt);
  });
});

ipcMain.handle('cfg:get', () => readCfg());
ipcMain.handle('cfg:set', (_e, patch) => writeCfg(patch));

// ── 本地大脑（sidecar）────────────────────────────────────────────
ipcMain.handle('sidecar:status', () => sidecar.status());
ipcMain.handle('sidecar:restart', () => { sidecar.restart(readCfg()); return { ok: true }; });

// ── M3 转发通道（微信消息 → 云端 → 这台电脑的本地大脑）───────────
const forward = require('./forward');
const LOOPBACK_RE = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?\/?$/i;

/** 条件满足就起转发：本地大脑就绪 + 云地址已知（且不是本地回环）。
 *  cfg.forward === false 是总闸（设置页可关）；云地址取 cloud_base，
 *  没填过就借用 autoConnect 写下的 base —— 但 base 指向本地大脑时
 *  毫无意义（自己连自己），直接不起。 */
async function maybeStartForward() {
  const cfg = readCfg();
  const st = sidecar.status();
  if (cfg.forward === false || !sidecar.ready() || !st.port) {
    forward.stop();
    return;
  }
  let cloud = String(cfg.cloud_base || '').trim();
  if (!cloud && cfg.base && !LOOPBACK_RE.test(cfg.base)) cloud = cfg.base;
  if (!cloud) return;                       // 没有云地址，无从转发
  const tok = await getToken();
  forward.start({
    cloudBase: cloud,
    token: tok,
    brainBase: sidecar.base(),
    brainToken: tok,
  });
}
// sidecar 每次变 ready 都试一把（重启本地大脑后转发自动恢复）
sidecar.onState((s) => { if (s.status === 'ready') maybeStartForward(); });
ipcMain.handle('forward:status', () => forward.status());
ipcMain.handle('forward:restart', () => maybeStartForward());

// 向导的"填 key 即用"：走大脑现成的 /api/models/apply —— 它会落盘 + 热加载，
// 内存/磁盘两边一起到位，不用重启 sidecar
ipcMain.handle('sidecar:apply-model', async (_e, patch) => {
  if (!sidecar.ready()) return { ok: false, err: '本地大脑没在运行' };
  const tok = await getToken();
  const r = await request('POST', sidecar.base() + '/api/models/apply', patch || {}, tok);
  return r.ok ? { ok: true, json: r.json } : { ok: false, err: r.err || ('HTTP ' + r.status), json: r.json };
});
// 向导拿服务商清单：让大脑自己报（PROVIDERS 在它那边，别在客户端抄一份）
ipcMain.handle('sidecar:providers', async () => {
  if (!sidecar.ready()) return { ok: false, err: '本地大脑没在运行' };
  const tok = await getToken();
  const r = await request('GET', sidecar.base() + '/api/models', null, tok);
  return r.ok ? { ok: true, json: r.json } : { ok: false, err: r.err || ('HTTP ' + r.status) };
});

// ── 设备管理 ─────────────────────────────────────────────────────
// 列表/吊销都走 brain 的 /api/device/*：设备 token 只能解绑自己（服务端把关），
// 这里不再做权限判断 —— 权限只在一个地方判，才不会两边不一致。
async function deviceCall(method, path, body) {
  const cfg = readCfg();
  if (!cfg.base) return { ok: false, status: 0, err: '还没连上大脑' };
  return request(method, String(cfg.base).replace(/\/+$/, '') + path, body, await getToken());
}
ipcMain.handle('device:list', () => deviceCall('GET', '/api/device/list'));
ipcMain.handle('device:revoke', (_e, id) => deviceCall('POST', '/api/device/revoke', { device_id: id }));
ipcMain.handle('device:whoami', () => {
  const c = readCfg();
  return { device_id: c.device_id || '', token_kind: c.token_kind || '', base: c.base || '' };
});
// 重新绑定：先把本机这条吊销掉（否则设备列表越点越长），再走一次自动注册
ipcMain.handle('device:rebind', async () => {
  const c = readCfg();
  if (c.device_id && c.token_kind === 'device') {
    await deviceCall('POST', '/api/device/revoke', { device_id: c.device_id });
  }
  return autoConnect();
});

ipcMain.handle('token:get', async () => ((await getToken()) ? '已保存' : ''));
ipcMain.handle('token:set', (_e, t) => setToken(t));
ipcMain.handle('token:test', async () => {
  const cfg = readCfg();
  const r = await request('GET', cfg.base.replace(/\/+$/, '') + '/api/persona', null, await getToken());
  return { ok: r.ok, status: r.status, err: r.err || '', name: (r.json && r.json.name) || '' };
});
ipcMain.handle('shell:open', (_e, url) => shell.openExternal(String(url || '')));

const send = (ch, ...a) => win && !win.isDestroyed() && win.webContents.send(ch, ...a);
ipcMain.on('win:minimize', () => win && win.minimize());
ipcMain.on('win:maximize', () => {
  if (!win) return;
  win.isMaximized() ? win.unmaximize() : win.maximize();
  send('win:state', win.isMaximized());
});
ipcMain.on('win:close', () => win && win.close());

// ── 生命周期 ──────────────────────────────────────────────────────
// 单实例：两个客户端同时跑会互相覆盖配置、也会把聊天历史对账搞乱
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) {
      win.isMinimized() && win.restore();
      win.focus();
    }
  });
  app.whenReady().then(() => {
    Menu.setApplicationMenu(null);   // 自绘标题栏，不要系统菜单
    oplog.init(app.getPath('userData'));
    sidecar.maybeStart(readCfg());   // 本地大脑先跑起来，窗口同时开
    sidecar.onState((s) => send('sidecar:state', s));
    createWindow();
    startPolling();                  // 本地操控的活从这儿进来
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });
}

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
