// -*- coding: utf-8 -*-
/**
 * 与 brain 的接口层。
 *
 * 所有请求经 `window.vigil.api` 走主进程（渲染层自己发不出去 —— CSP 里
 * connect-src 是 'none'，token 也只在主进程手上）。
 *
 * 错误一律**返回结果对象**，不抛：后台的离线/401/500 应该在页面上显示成
 * 一句能看懂的话，而不是把整个页面渲染失败。
 */
(function (global) {
  'use strict';

  const state = {
    page: 'chat',
    src: 'pc',           // 客户端自己的渠道（独立于 app / wechat，见 electron/main.js）
    theme: 'light',
    conn: { ok: false, ms: 0, err: '' },
    base: '',
    personas: [],
    persona: null,       // 当前人设 {key,name,...}
    cfg: {},             // 主进程里的配置（地址等）
    autoFail: null,      // 启动时自动探测失败的结果（设置页拿它显示原因）
    // 聊天页的内存状态：会话 id 由聊天页自己维护，但别的页面要知道"现在这个
    // 对话是谁"，才能把专家标在正确的那一条上、并在召唤后指定"打开哪个对话"。
    // 只活在内存里（刷新页面靠 localStorage 的 SESS_KEY 恢复），不参与持久化。
    chatConversationId: '',
    chatPendingConversation: '',  // 召唤专家后要跳过去的新会话 id，聊天页取走即清空
    chatPendingPrompt: '',        // 顺带填进输入框的开场白
  };

  // A missing bridge in Electron is a preload failure, not a browser preview.
  const runtime = {
    kind: global.vigil ? 'electron' : (/\bElectron\//i.test(navigator.userAgent) ? 'electron-missing-preload' : 'browser-preview'),
    get preview() { return this.kind === 'browser-preview'; },
  };

  async function api(method, path, body, opts) {
    if (!global.vigil) {
      // Only an explicit preview gets empty fixture history. Never turn a failed
      // production history request into an empty conversation.
      if (runtime.preview && method === 'GET' && path.startsWith('/api/history?')) {
        return { ok:true, status:200, preview:true, json:{ items:[] } };
      }
      return { ok:false, status:0, err: runtime.preview
        ? '浏览器预览：未连接 Agent，数据与操作仅在 Electron 客户端可用'
        : 'Electron preload 未加载：请检查启动日志与 preload 路径，IPC 当前不可用' };
    }
    try {
      const r = await global.vigil.api(method, path, body, opts);
      return r || { ok: false, status: 0, err: '空响应' };
    } catch (e) {
      return { ok: false, status: 0, err: String(e && e.message || e) };
    }
  }

  // 目标只有一个：随包本地 Brain（主进程按 sidecar 动态端口拼地址）。
  // 传 opts.base 会被主进程直接拒绝 —— 那是防止凭据外传的边界，不是可选项。
  const get = (p, opts) => api('GET', p, null, opts);
  const post = (p, b, opts) => api('POST', p, b || {}, opts);

  /** 拿 JSON；失败时把 status/err 一起带出来，页面好显示 */
  function pick(r) {
    if (!r) return { ok: false, err: '无响应' };
    if (r.json && typeof r.json === 'object') return { ...r.json, ok: !!r.ok && r.json.ok !== false, _status: r.status, _target: r.target || '', _err: r.err || (r.json && r.json.err) || '' };
    return { ok: !!r.ok, _status: r.status, _target: r.target || '', _err: r.err || (r.text || '').slice(0, 200) };
  }

  /** 探活 + 记录延迟：只用 /api/persona，它轻且一定在 */
  async function ping() {
    const t0 = Date.now();
    const r = await get('/api/persona', { timeoutMs: 5000 });
    const ms = Date.now() - t0;
    state.conn = r.ok
      ? { ok: true, ms, err: '' }
      : { ok: false, ms, err: r.err || ('HTTP ' + r.status) };
    if (r.ok && r.json) {
      state.persona = r.json.current || r.json.key ? r.json : state.persona;
    }
    return state.conn;
  }

  async function loadCfg() {
    if (!global.vigil) return state.cfg;
    state.cfg = (await global.vigil.cfg.get()) || {};
    state.src = state.cfg.src || 'pc';
    state.base = state.cfg.base || '';
    return state.cfg;
  }

  async function saveCfg(patch) {
    if (!global.vigil) return null;
    state.cfg = (await global.vigil.cfg.set(patch)) || state.cfg;
    state.src = state.cfg.src || state.src;
    state.base = state.cfg.base || state.base;
    return state.cfg;
  }

  global.API = { state, runtime, api, get, post, pick, ping, loadCfg, saveCfg };
})(window);
