// -*- coding: utf-8 -*-
/**
 * M3 转发通道客户端：本地 Electron 出站长连云端 /ws/brain。
 *
 * 职责就一件：云端来 msg → 调本地大脑 /api/chat → 把 answer 送回去。
 * 附带：ack（云端日志用）、指数退避重连、30s 心跳探测。
 *
 * 为什么出站连接：NAT/防火墙后面只有出站是天然通的；云端不用开新端口
 * （8791 实测被安全组挡），WS 就挂在 8788 上（bind_host 0.0.0.0 + token）。
 *
 * 协议见 brain/ws_forward.py 顶部。心跳用 WS 协议层 ping/pong：
 * ws 包收到服务端 ping 会自动回 pong，我们这边 30s 没收到**任何**帧
 * 就主动 ws.ping()，连续两次（60s）没回应就当半开连接，terminate 重连。
 */
const WebSocket = require('ws');
const http = require('http');

const BACKOFF_MAX = 60 * 1000;    // 重连封顶 1 分钟
const BACKOFF_BASE = 1000;
const IDLE_PING_MS = 30 * 1000;   // 30s 没任何帧 → 主动 ping
const BRAIN_TIMEOUT_MS = 22 * 1000; // 调本地大脑的超时（云端 25s 总闸，留余量）

let state = {
  running: false,
  cloudBase: '',
  wsState: 'idle',       // idle | connecting | open | closed
  attempts: 0,           // 当前退避轮数（重连次数）
  lastOpenAt: 0,
  lastMsgAt: 0,          // 最近一次收到云端消息
  forwarded: 0,
  lastErr: '',
};
let ws = null;
let stopFlag = false;
let reconnectTimer = null;
let idleTimer = null;
const listeners = [];

function emit() {
  const snap = { ...state };
  listeners.forEach((fn) => { try { fn(snap); } catch (e) { /* 界面回调别连累 */ } });
}

function log(line) {
  console.log('[forward] ' + line);
}

/** http://host:port -> ws://host:port/ws/brain?token=... */
function wsUrl(cloudBase, token) {
  const u = String(cloudBase || '').replace(/\/+$/, '');
  const wsBase = u.replace(/^http/, 'ws');
  return wsBase + '/ws/brain?token=' + encodeURIComponent(token || '');
}

/** POST 本地大脑 /api/chat，拿 { reply, messages, ... } */
function askLocalBrain(brainBase, token, payload) {
  return new Promise((resolve) => {
    const body = JSON.stringify({
      text: payload.text || '',
      src: payload.src || 'wechat',
      dry_run: !!payload.dry_run,
      // img：云端原样转来的 base64（微信发图场景），本地大脑自己走视觉链
      img: payload.img || undefined,
    });
    const u = new URL(brainBase + '/api/chat');
    const req = http.request(u, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
        ...(token ? { Authorization: 'Bearer ' + token } : {}),
      },
      timeout: BRAIN_TIMEOUT_MS,
    }, (res) => {
      let data = '';
      res.on('data', (c) => { data += c; });
      res.on('end', () => {
        try { resolve(JSON.parse(data)); } catch (e) { resolve({ err: 'bad json' }); }
      });
    });
    req.on('timeout', () => { req.destroy(new Error('本地大脑超时')); });
    req.on('error', (e) => resolve({ err: String(e.message || e) }));
    req.end(body);
  });
}

function sendJson(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
    return true;
  }
  return false;
}

/** 处理云端转发来的一条消息 */
async function handleMsg(payload, brainBase, brainToken) {
  const mid = payload.id || '';
  sendJson({ t: 'ack', id: mid });
  state.lastMsgAt = Date.now();
  const r = await askLocalBrain(brainBase, brainToken, payload);
  state.forwarded += 1;
  emit();
  sendJson({
    t: 'result',
    id: mid,
    reply: r.reply || '',
    err: r.err || '',
  });
}

function startIdleWatch() {
  stopIdleWatch();
  idleTimer = setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (Date.now() - (state.lastMsgAt || state.lastOpenAt) > IDLE_PING_MS) {
      log('idle ping');
      try { ws.ping(); } catch (e) { /* 死连接交给 close 流程 */ }
    }
  }, IDLE_PING_MS);
}

function stopIdleWatch() {
  if (idleTimer) { clearInterval(idleTimer); idleTimer = null; }
}

function scheduleReconnect(opts) {
  if (stopFlag) return;
  state.attempts += 1;
  const wait = Math.min(BACKOFF_BASE * Math.pow(2, state.attempts - 1),
                        BACKOFF_MAX) + Math.floor(Math.random() * 1000);
  log('reconnect in ' + Math.round(wait / 1000) + 's (attempt ' +
      state.attempts + ')');
  reconnectTimer = setTimeout(() => connect(opts), wait);
}

function connect(opts) {
  if (stopFlag) return;
  const { cloudBase, token, brainBase, brainToken } = opts;
  state.wsState = 'connecting';
  emit();
  let sock;
  try {
    sock = new WebSocket(wsUrl(cloudBase, token));
  } catch (e) {
    state.lastErr = String(e.message || e);
    state.wsState = 'closed';
    emit();
    scheduleReconnect(opts);
    return;
  }
  ws = sock;

  sock.on('open', () => {
    log('connected -> ' + cloudBase);
    state.wsState = 'open';
    state.lastOpenAt = Date.now();
    state.lastMsgAt = Date.now();
    state.attempts = 0;         // 连上了，退避清零
    state.lastErr = '';
    emit();
    startIdleWatch();
  });

  sock.on('message', (data) => {
    state.lastMsgAt = Date.now();
    let d = null;
    try { d = JSON.parse(String(data)); } catch (e) { return; }
    if (d && d.t === 'msg') {
      // 串行处理：她本地大脑一次答一句（/api/chat 自带排队语义）
      handleMsg(d, brainBase, brainToken).catch((e) => {
        log('handle msg failed: ' + (e.message || e));
        sendJson({ t: 'result', id: d.id || '', reply: '',
                   err: String(e.message || e) });
      });
    }
  });

  sock.on('pong', () => { state.lastMsgAt = Date.now(); });

  sock.on('error', (e) => {
    state.lastErr = String(e.message || e);
  });

  sock.on('close', () => {
    stopIdleWatch();
    state.wsState = 'closed';
    emit();
    if (!stopFlag) scheduleReconnect(opts);
  });
}

/** 启动转发客户端。opts: { cloudBase, token, brainBase, brainToken } */
function start(opts) {
  if (state.running) return status();
  if (!opts || !opts.cloudBase || !opts.brainBase) {
    return { ...status(), err: '缺 cloudBase 或 brainBase' };
  }
  state.running = true;
  state.cloudBase = opts.cloudBase;
  state.forwarded = 0;
  state.attempts = 0;
  stopFlag = false;
  emit();
  connect(opts);
  return status();
}

function stop() {
  stopFlag = true;
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  stopIdleWatch();
  if (ws) { try { ws.close(); } catch (e) { /* 尽力而为 */ } ws = null; }
  state.running = false;
  state.wsState = 'idle';
  emit();
}

function status() { return { ...state }; }

function onState(fn) { listeners.push(fn); }

module.exports = { start, stop, status, onState, wsUrl };
