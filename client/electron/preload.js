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
// 单一流式订阅总线：主对话与创作模式共用同一套事件注册，不再各自一套流式系统。
// 每个处理器都自带"完成/作用域"守卫（streamDone / turnScope / job_id），
// 因此用集合注册是安全的；某个处理器抛错也不影响其它订阅。
const _streamHandlers = new Set();
let _streamBound = false;
function bindStream() {
  if (_streamBound) return;
  _streamBound = true;
  ipcRenderer.on('api:stream:event', dispatchStream);
}
function dispatchStream(_e, evt) {
  for (const fn of Array.from(_streamHandlers)) {
    if (!_streamHandlers.has(fn)) continue;
    try { fn(evt); } catch (_) { /* One subscriber cannot break other streams. */ }
  }
}
function subscribeStream(fn) {
  if (typeof fn !== 'function') throw new TypeError('Stream subscriber must be a function');
  // Separate registrations of the same callback have independent lifetimes.
  const handler = evt => fn(evt);
  bindStream();
  _streamHandlers.add(handler);
  return () => {
    _streamHandlers.delete(handler);
    if (!_streamHandlers.size && _streamBound) {
      ipcRenderer.removeListener('api:stream:event', dispatchStream);
      _streamBound = false;
    }
  };
}
let _settingsNavigateHandler = null;
let _pendingSettingsNavigation = null;
ipcRenderer.on('settings:navigate', (_e, target) => {
  if (_settingsNavigateHandler) _settingsNavigateHandler(target || {});
  else _pendingSettingsNavigation = target || {};
});

contextBridge.exposeInMainWorld('vigil', {
  appVersion: () => ipcRenderer.invoke('app:version'),
  appInstanceId: () => ipcRenderer.invoke('app:instance-id'),
  // 所有网络请求走主进程：token 在那边加，渲染层拿不到凭据
  api: (method, path, body, opts) => ipcRenderer.invoke('api', {
    method, path, body,
    base: (opts && opts.base) || null,
    timeoutMs: opts && Number.isFinite(opts.timeoutMs) ? opts.timeoutMs : null,
  }),

  // 流式聊天：开始 SSE 请求 + 监听逐块事件
  apiStream: (path, body) => ipcRenderer.invoke('api:stream', { path, body }),
  attachments: {
    chunk: (bytes, headers) => ipcRenderer.invoke('attachment:chunk', { bytes, headers }),
  },
  // 主对话与创作模式共用同一条订阅总线（onCreatorEvent 为兼容别名）。
  onStreamEvent: subscribeStream,
  onCreatorEvent: subscribeStream,

  cfg: {
    get: () => ipcRenderer.invoke('cfg:get'),
    set: (patch) => ipcRenderer.invoke('cfg:set', patch),
  },

  token: {
    has: () => ipcRenderer.invoke('token:get'),
    // 只有本机 Brain 的 token，没有"手填远程 token"这回事；test 用来探活。
    test: () => ipcRenderer.invoke('token:test'),
  },

  // 启动本地内核 / 查看它为什么没起来（见 main.js 的 sidecar 通道）
  autoConnect: () => ipcRenderer.invoke('auto:connect'),

  // 推送更新：mode 省略=只查状态；'ask'=发现新版就弹窗引导下载安装
  updater: { check: (mode) => ipcRenderer.invoke('updater:check', mode || 'status') },

  // 本地内核（sidecar）：状态 / 重启 / 向导的模型配置
  sidecar: {
    status: () => ipcRenderer.invoke('sidecar:status'),
    restart: () => ipcRenderer.invoke('sidecar:restart'),
    applyModel: (patch) => ipcRenderer.invoke('sidecar:apply-model', patch),
    providers: () => ipcRenderer.invoke('sidecar:providers'),
    onState: (fn) => ipcRenderer.on('sidecar:state', (_e, s) => fn(s)),
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

  settings: {
    open: (options) => ipcRenderer.invoke('settings:open', options || {}),
    onNavigate: (fn) => {
      _settingsNavigateHandler = fn;
      if (_pendingSettingsNavigation) {
        const target = _pendingSettingsNavigation;
        _pendingSettingsNavigation = null;
        fn(target);
      }
    },
  },

  openExternal: (url) => ipcRenderer.invoke('shell:open', url),

  // 插件前端注入：渲染层只能"请主进程加载一次"。插件代码由主进程自己从
  // 服务端拉取（token 不经过渲染层）并注入，不再有"任意代码 → executeJavaScript"
  // 的通道（旧 runJS 已移除：插件 JS/XSS 能借它执行任意脚本）。
  plugins: {
    load: () => ipcRenderer.invoke('plugins:load'),
    resourceUrl: (path) => ipcRenderer.invoke('plugins:resource-url', path),
  },
  // Narrow native capability for first-party local project tools; returns a selected folder only.
  projectPicker: {
    pickDirectory: () => ipcRenderer.invoke('project-picker:directory'),
  },
  // 工作区：选一个真实文件夹，并查询它现在是否还在。
  // 两个方法都不读文件内容，也不授予任何读写权限。
  workspace: {
    pick: () => ipcRenderer.invoke('workspace:pick'),
    probe: (p) => ipcRenderer.invoke('workspace:probe', p),
  },

  platform: process.platform,
  agentCenter: {
    notify: (event) => ipcRenderer.invoke('agent-center:notify', event),
    onOpen: (fn) => ipcRenderer.on('agent-center:open', () => fn()),
  },
});
