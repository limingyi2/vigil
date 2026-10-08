// -*- coding: utf-8 -*-
/**
 * 预加载：渲染层唯一能碰到的桥。
 *
 * 🔴 只暴露**函数**，不暴露 ipcRenderer 本身 ——
 * 暴露了它，页面就能往任意通道发消息，contextIsolation 等于白开。
 * 这里每一个方法在主进程那边都有对应的 handler，能力面就是这几行。
 *
 * sandbox: true 下预加载里能用的只有 electron 的这几个模块（不能 require fs）。
 */
const { contextBridge, ipcRenderer } = require('electron');

// 用闭包变量存储流式事件处理函数，避免依赖 global 对象（sandbox 下可能受限）
let _streamHandler = null;
let _streamBound = false;

contextBridge.exposeInMainWorld('vigil', {
  // 所有网络请求走主进程：token 在那边加，渲染层拿不到凭据
  api: (method, path, body) => ipcRenderer.invoke('api', { method, path, body }),

  // 流式聊天：开始 SSE 请求 + 监听逐块事件
  apiStream: (path, body) => ipcRenderer.invoke('api:stream', { path, body }),
  onStreamEvent: (fn) => {
    if (!_streamBound) {
      _streamBound = true;
      ipcRenderer.on('api:stream:event', (_e, evt) => {
        if (typeof _streamHandler === 'function') {
          _streamHandler(evt);
        }
      });
    }
    _streamHandler = fn;
  },

  cfg: {
    get: () => ipcRenderer.invoke('cfg:get'),
    set: (patch) => ipcRenderer.invoke('cfg:set', patch),
  },

  token: {
    has: () => ipcRenderer.invoke('token:get'),
    set: (t) => ipcRenderer.invoke('token:set', t),
    test: () => ipcRenderer.invoke('token:test'),
  },

  // 自动找大脑：探本机隧道/本机服务，命中就注册本机并收下设备 token（见 main.js）
  autoConnect: () => ipcRenderer.invoke('auto:connect'),

  // 本地大脑（sidecar）：状态 / 重启 / 向导的模型配置
  sidecar: {
    status: () => ipcRenderer.invoke('sidecar:status'),
    restart: () => ipcRenderer.invoke('sidecar:restart'),
    applyModel: (patch) => ipcRenderer.invoke('sidecar:apply-model', patch),
    providers: () => ipcRenderer.invoke('sidecar:providers'),
    onState: (fn) => ipcRenderer.on('sidecar:state', (_e, s) => fn(s)),
  },

  // 设备管理
  device: {
    list: () => ipcRenderer.invoke('device:list'),
    revoke: (id) => ipcRenderer.invoke('device:revoke', id),
    whoami: () => ipcRenderer.invoke('device:whoami'),
    rebind: () => ipcRenderer.invoke('device:rebind'),
  },

  // 本地操控
  local: {
    defaults: () => ipcRenderer.invoke('local:defaults'),
    caps: () => ipcRenderer.invoke('local:caps'),
    // 工具页：目录 / 手动运行 / 操作日志 / 撤销
    tools: () => ipcRenderer.invoke('local:tools'),
    run: (name, args) => ipcRenderer.invoke('local:run', { name, args }),
    log: (limit) => ipcRenderer.invoke('local:log', { limit }),
    undo: (id) => ipcRenderer.invoke('local:undo', { id }),
    reveal: (p) => ipcRenderer.invoke('local:reveal', p),
    // 危险操作要弹确认：主进程把问题发过来，界面把答复发回去
    onConfirm: (fn) => ipcRenderer.on('local:confirm', (_e, msg) => fn(msg)),
    answer: (id, allow) => ipcRenderer.send('local:answer', { id, allow }),
    // 她在干活时的进度（聊天页把它逐条打出来）
    onStep: (fn) => ipcRenderer.on('local:step', (_e, msg) => fn(msg)),
    onDone: (fn) => ipcRenderer.on('local:done', (_e, msg) => fn(msg)),
  },

  win: {
    minimize: () => ipcRenderer.send('win:minimize'),
    maximize: () => ipcRenderer.send('win:maximize'),
    close: () => ipcRenderer.send('win:close'),
    onState: (fn) => ipcRenderer.on('win:state', (_e, maximized) => fn(maximized)),
  },

  openExternal: (url) => ipcRenderer.invoke('shell:open', url),

  platform: process.platform,
});
