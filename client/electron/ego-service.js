'use strict';
// EgoAgent 视频问答服务的本地进程管理：只负责探测、拉起与回收。
// 问答流量本身由渲染层直连 127.0.0.1（CSP 的 connect-src 已允许回环），
// 不经过主进程转发 —— SSE 走 IPC 反而要自己重造一套分块协议。
//
// 服务不在随包范围内：它跑在用户自己的 EgoAgent 仓库里（默认
// F:\EgoAgent-Eval，可用 VIGIL_EGO_ROOT 覆盖）。找不到就如实报错，
// 未找到服务环境时返回不可用状态，不尝试其他目录。

const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');

const PORT = Number(process.env.VIGIL_EGO_PORT || 8799);
const ROOT = process.env.VIGIL_EGO_ROOT || 'F:\\EgoAgent-Eval';
// 默认走真实视觉模型；想看脚本演示就把 VIGIL_EGO_PROVIDER 设成 fake。
const PROVIDER = process.env.VIGIL_EGO_PROVIDER || 'mimo';
const WAIT_MS = 30000;
const LOG = path.join(os.tmpdir(), 'vigil-ego-service.log');

let child = null;
let starting = null;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// 只允许主窗口顶层页面访问问答 API，插件 iframe 和其他本机端口仍走票据闸门。
function isApprovedRequest(details, rendererId) {
  if (details.webContentsId !== rendererId || details.resourceType !== 'xhr'
      || !details.frame || details.frame.parent) return false;
  try {
    const url = new URL(details.url);
    if (url.origin !== 'http://127.0.0.1:' + PORT || url.username || url.password
        || url.search || url.hash) return false;
    const method = details.method;
    if (url.pathname === '/health') return method === 'GET' || method === 'OPTIONS';
    return ['/session', '/ask', '/cancel', '/close'].includes(url.pathname)
      && (method === 'POST' || method === 'OPTIONS');
  } catch { return false; }
}

// 探活：能连上并且确实是 EgoAgent 的 VR 服务才算数，别的进程占着这个端口不算。
function health() {
  return new Promise((resolve) => {
    const req = http.get(
      { host: '127.0.0.1', port: PORT, path: '/health', timeout: 1500 },
      (res) => {
        let body = '';
        res.setEncoding('utf8');
        res.on('data', (chunk) => { body += chunk; });
        res.on('end', () => {
          try {
            const value = JSON.parse(body);
            resolve(value && value.service === 'egoagent-vr' ? value : null);
          } catch { resolve(null); }
        });
      },
    );
    req.on('timeout', () => { req.destroy(); resolve(null); });
    req.on('error', () => resolve(null));
  });
}

function describe(value) {
  return {
    ok: true, port: PORT, base: 'http://127.0.0.1:' + PORT,
    provider: value.provider || 'unknown', isFake: !!value.is_fake,
    hasSession: !!value.session,
  };
}

function spawnService() {
  const exe = path.join(ROOT, '.venv', 'Scripts', 'python.exe');
  if (!fs.existsSync(exe)) throw new Error('找不到 EgoAgent 的解释器：' + exe);
  const out = fs.openSync(LOG, 'a');
  let proc;
  try {
    proc = spawn(exe, ['-m', 'egoagent_eval.vr.serve', '--port', String(PORT),
      '--provider', PROVIDER], {
      cwd: ROOT, stdio: ['ignore', out, out], windowsHide: true,
    });
  } finally {
    fs.closeSync(out);
  }
  proc.on('exit', () => { if (child === proc) child = null; });
  proc.on('error', () => { if (child === proc) child = null; });
  child = proc;
  return proc;
}

async function ensure() {
  const existing = await health();
  if (existing) return describe(existing);
  if (starting) return starting;
  starting = (async () => {
    try {
      spawnService();
    } catch (error) {
      return { ok: false, error: error.message, log: LOG };
    }
    const deadline = Date.now() + WAIT_MS;
    while (Date.now() < deadline) {
      await sleep(400);
      const value = await health();
      if (value) return describe(value);
      if (!child) return { ok: false, error: '分析服务启动后立即退出', log: LOG };
    }
    return { ok: false, error: '分析服务启动超时（30 秒）', log: LOG };
  })();
  try {
    return await starting;
  } finally {
    starting = null;
  }
}

async function status() {
  const value = await health();
  return value ? describe(value) : { ok: false, port: PORT, log: LOG };
}

function dispose() {
  if (!child) return;
  try { child.kill(); } catch { /* 进程可能已自行退出 */ }
  child = null;
}

module.exports = { ensure, status, dispose, isApprovedRequest, LOG, PORT, ROOT };
