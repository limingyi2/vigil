// -*- coding: utf-8 -*-
/**
 * 壳：左侧图标导航、§ 页号注入、状态栏、主题、分栏拖拽、启动顺序。
 *
 * 页面本身在 pages.js 里（PAGES: {id,name,icon,render(ctx)}），这里只负责
 * "把舞台搭好、把当前页叫上来"。渲染层不持有任何密钥，网络全走主进程。
 *
 * 对齐 index.html 的 ID：gnav / panel / topbar / view / status / gPanel /
 * winMin / winMax / winClose。
 * 全部带存在性守卫 —— 网页 harness 里没有 window.vigil 也要能跑 UI；
 * 启动失败必须看得见：异常打进状态栏和 __bootErr，不许无声黑屏。
 */
(function () {
  'use strict';
  const { esc, icon, fmtNum, toast } = U;
  const { state, ping, get } = API;
  const windowQuery = new URLSearchParams(location.search);
  const settingsWindow = windowQuery.get('window') === 'settings';
  if (settingsWindow) document.body.classList.add('settings-window');

  const gnavEl = document.getElementById('gnav');
  let panelEl = document.getElementById('panel');
  let viewEl = document.getElementById('view');
  let topbarEl = document.getElementById('topbar');
  const statusEl = document.getElementById('status');

  // ── 左侧主导航：图标栏，页名由原生 tooltip 和 aria-label 提供 ──
  function buildNav() {
    if (!gnavEl) return;
    const navPages = PAGES.filter(p => p.id !== 'system' && p.id !== 'settings' && p.id !== 'market');
    const marketPage = PAGES.find(p => p.id === 'market');
    if (marketPage) navPages.splice(navPages.findIndex(p => p.id === 'chat') + 1, 0, marketPage);
    navPages.push({id:'settings',name:'设置',icon:'set'});
    let saved=[];try{saved=JSON.parse(localStorage.getItem('vigil.primary-nav.order.v1')||'[]');}catch(_){}
    if(!Array.isArray(saved))saved=[];
    const order=[...new Set([...saved,...navPages.map(p=>p.id)])];
    navPages.sort((a,b)=>order.indexOf(a.id)-order.indexOf(b.id));
    gnavEl.innerHTML = '<div class="gnav-brand" aria-label="Vigil">Vigil</div>' +
      navPages.map((p) => {
      // 插件可能注册 'i-img' 这种带前缀的名字，这里归一化，别让按钮变成空白图标
      const ic = String(p.icon === 'image' ? 'img' : p.icon || '').replace(/^i-/, '') || 'puzzle';
      return (p.id==='settings' && navPages.at(-1).id==='settings' ? '<div class="nav-spacer" aria-hidden="true"></div>' : '') + '<button draggable="true" '+(p.id==='settings'?'id="settingsBtn" ':'')+'class="gbtn' + (p.id === state.page ? ' on' : '') + '" data-page="' + esc(p.id) + '" data-tip="' + esc(p.name) + '" title="' + esc(p.name) + '" aria-label="' + esc(p.name) + '">' +
      U.icon(ic, 'md') +
      '<span class="gnav-label">' + esc(p.id === 'chat' ? '会话' : p.name) + '</span></button>'; }).join('') +
      '';
    let dragged=null;
    gnavEl.querySelectorAll('.gbtn').forEach(b=>{
      b.addEventListener('dragstart',e=>{dragged=b.dataset.page;e.dataTransfer.setData('text/plain',dragged);e.dataTransfer.effectAllowed='move';b.classList.add('dragging');});
      b.addEventListener('dragend',()=>{dragged=null;b.classList.remove('dragging');});
      b.addEventListener('dragover',e=>{if(dragged)e.preventDefault();});
      b.addEventListener('drop',e=>{e.preventDefault();if(!dragged || dragged===b.dataset.page)return;
        const ids=navPages.map(p=>p.id).filter(id=>id!==dragged);ids.splice(ids.indexOf(b.dataset.page),0,dragged);
        try{localStorage.setItem('vigil.primary-nav.order.v1',JSON.stringify(ids));}catch(_){}buildNav();});
    });
    // 折叠按钮（‹/›）已下线：一级栏宽度改成直接拖 #gRail 的分隔条，
    // 想收窄就拖窄，不需要一个"藏起来"的中间态。
    gnavEl.querySelectorAll('.gbtn').forEach((b) =>
      b.addEventListener('click', () => {
        if (b.id === 'settingsBtn') {
          if (window.vigil && window.vigil.settings) window.vigil.settings.open({ section: 'model' });
          else { state.settingsSub = 'model'; go('system'); }
        } else if(b.dataset.page !== state.page) go(b.dataset.page);
      }));
  }

  // ── 状态栏 ────────────────────────────────────────────────
  // 等宽字分段读数：● 状态 │ 模型 │ 今日 TOKENS │ 版本，像终端。
  // 数字来自 /api/stats，随 ping 一起刷；拿不到就不显示，不摆假数。
  let lastStats = null;
  function setStatus(extra) {
    if (!statusEl) return;
    const led = document.getElementById('led');
    if (led) led.classList.toggle('on', !!state.conn.ok);
    const seg = ['<span class="led ' + (state.conn.ok ? 'on' : '') + '"></span>' +
      '<b>' + (API.runtime.preview ? '浏览器预览 · 未连接 Agent' : state.conn.ok ? '已连接' : '未连接') + '</b>'];
    if (state.conn.ok && state.conn.ms != null) seg.push(esc(String(state.conn.ms)) + ' ms');
    seg.push('<i class="ssep">│</i>渠道 <b>本机控制台（pc）</b>');
    if (lastStats && lastStats.model) seg.push('<i class="ssep">│</i>模型 <b>' + esc(lastStats.model) + '</b>');
    if (lastStats && lastStats.tok) seg.push('<i class="ssep">│</i>今日 TOKENS <b>' + fmtNum(lastStats.tok) + '</b>');
    if (extra) seg.push('<i class="ssep">│</i>' + esc(extra));
    seg.push('<span class="sp"></span><span class="mono muted">' + esc(state.base || '') + '</span><span class="mono muted">v' + esc(state.version || '…') + '</span>');
    statusEl.innerHTML = seg.join('');
  }
  async function refreshStats() {
    try {
      const r = pick(await get('/api/stats'));
      if (!r || !r.ok) return;
      lastStats = {
        model: (r.model && (r.model.id || r.model.name)) || (typeof r.model === 'string' ? r.model : ''),
        tok: Number(r.tok_today || r.tokens_today || r.tokens || 0),
      };
      setStatus();
    } catch (_) { /* 静默：状态栏少一段总比假数字好 */ }
  }

  // ── 路由 ──────────────────────────────────────────────────
  let disposePage = () => {};
  let navigationId = 0;
  let settingsBackObserver = null;
  let parkedChat = null;
  const parking = document.createElement('div');
  parking.hidden = true;
  parking.id = 'parkedChat';
  document.body.appendChild(parking);
  async function go(id) {
    const navigation = ++navigationId;
    // Keep the live chat connected while the market is open: stream listeners,
    // input draft, conversation and workspace selection belong to this view.
    if (state.page === 'chat' && id === 'market' && !parkedChat) {
      parkedChat = { dispose: disposePage, areas: [],
        classes: [...document.body.classList].filter(c => c.startsWith('welcome-') || c === 'chat-sessions-collapsed') };
      for (const el of [viewEl, panelEl, topbarEl]) {
        const replacement = el.cloneNode(false);
        const id = el.id;
        parkedChat.areas.push({el, id, scroll: el.scrollTop});
        el.replaceWith(replacement);
        el.id = 'parked-' + id;
        parking.appendChild(el);
        if (el === viewEl) viewEl = replacement;
        else if (el === panelEl) panelEl = replacement;
        else topbarEl = replacement;
      }
      disposePage = () => {};
      watchSettingsBack();
    }
    disposePage();
    if (id === 'chat' && parkedChat) {
      const saved = parkedChat;
      parkedChat = null;
      state.page = 'chat';
      state.settingsDetail = false;
      document.body.dataset.vigilPage = 'chat';
      document.body.classList.remove('settings-detail');
      document.body.classList.add(...saved.classes);
      for (const {el, id, scroll} of saved.areas) {
        document.getElementById(id).replaceWith(el);
        el.id = id;
        el.scrollTop = scroll;
      }
      viewEl = document.getElementById('view');
      panelEl = document.getElementById('panel');
      topbarEl = document.getElementById('topbar');
      watchSettingsBack();
      disposePage = saved.dispose;
      gnavEl.querySelectorAll('.gbtn').forEach(b => b.classList.toggle('on', b.dataset.page === 'chat'));
      return;
    }
    const cleanups = [];
    disposePage = () => cleanups.splice(0).forEach(fn => fn());
    const page = PAGES.find((p) => p.id === id) || PAGES[0];
    state.page = page.id;
    if (page.id === 'market') state.settingsDetail = false;
    document.body.dataset.vigilPage = page.id;
    document.body.classList.toggle('settings-detail', !!state.settingsDetail && page.id !== 'chat' && page.id !== 'settings');
    if (page.id !== 'chat') document.body.classList.remove('welcome-home', 'welcome-has-sessions');
    if (gnavEl) gnavEl.querySelectorAll('.gbtn').forEach((b) =>
      b.classList.toggle('on', b.dataset.page === page.id));
    // 每次切页把三块都清干净，页面自己负责填
    if (viewEl) {
      viewEl.className = 'scroll';
      viewEl.style.padding = '';
      viewEl.innerHTML = '';
    }
    if (panelEl) panelEl.innerHTML = '';
    if (topbarEl) {
      topbarEl.className = 'topbar';
      topbarEl.innerHTML = '';
    }
    const ctx = {
      view: viewEl, topbar: topbarEl, panel: panelEl,
      src: state.src, refresh: () => go(page.id),
      navigate: go,
      onDispose: fn => cleanups.push(fn),
    };
    try {
      await page.render(ctx);
    } catch (e) {
      if (navigation !== navigationId) return;
      console.error('渲染失败:', e);
      if (viewEl) viewEl.innerHTML = '<div class="wrap"><div class="card err"><b>这一页画不出来</b>' +
        '<div class="mono" style="font-size:11.5px;color:var(--err);margin-top:6px">' + esc(e.message) + '</div></div></div>';
    }
    if (navigation !== navigationId) return;
    if (state.settingsDetail && page.id !== 'chat' && page.id !== 'settings' && topbarEl) {
      ensureSettingsBack();
    }
    // § 章节编号：页号 = 导航序号（01–08）。子页换名不动编号，
    // 所以渲染完再补；子页只改 h1 文本，编号插在最前不受影响。
    if (page.id !== 'chat' && topbarEl && !topbarEl.querySelector('.tnum')) {
      topbarEl.insertAdjacentHTML('afterbegin',
        '<span class="tnum mono">§ ' + String(PAGES.indexOf(page) + 1).padStart(2, '0') + '</span>');
    }
  }

  // ── 「返回设置」必须一直在 ──────────────────────────────────
  // 设置子页的功能分区会 `topbar.innerHTML = ...` 重写整条顶栏，之前把按钮
  // 追加在 topbar 里，用户一点左栏换分区按钮就没了 —— 而 .settings-detail
  // 又隐藏了左侧导航，等于走进死胡同。现在按钮由 go() 之外的一个观察器守着，
  // 顶栏被谁重写都会补回来。
  let backGuardBusy = false;
  function ensureSettingsBack() {
    if (!topbarEl) return;
    const want = !!state.settingsDetail
      && state.page !== 'chat' && state.page !== 'settings';
    const old = topbarEl.querySelector('.settings-detail-back');
    if (!want) { if (old) old.remove(); return; }
    if (old) return;
    const back = document.createElement('button');
    back.className = 'btn oline settings-detail-back';
    back.type = 'button';
    back.textContent = '返回设置';
    back.addEventListener('click', () => { state.settingsDetail = false; go('settings'); });
    topbarEl.appendChild(back);
  }
  function watchSettingsBack() {
    if (!topbarEl || typeof MutationObserver !== 'function') return;
    if (settingsBackObserver) settingsBackObserver.disconnect();
    settingsBackObserver = new MutationObserver(() => {
      if (backGuardBusy) return;
      backGuardBusy = true;
      // 让当前这次重写先完成，再决定要不要补按钮
      Promise.resolve().then(() => {
        backGuardBusy = false;
        ensureSettingsBack();
      });
    });
    settingsBackObserver.observe(topbarEl, { childList: true });
  }

  // ── 主题 ──────────────────────────────────────────────────
  function applyTheme(t) {
    state.theme = t === 'dark' ? 'dark' : 'light';
    document.documentElement.dataset.theme = state.theme;
    const b = document.getElementById('themeBtn');
    if (b) b.innerHTML = icon('moon', 'xs') + (state.theme === 'dark' ? '浅色' : '深色');
    try { localStorage.setItem('vigil_theme', state.theme); } catch (_) {}
  }

  // ── 分栏拖拽（双击复位，方向键微调，宽度记在 localStorage）────
  // 一级栏 --rail（图标栏，拖宽后自动显示文字标签）、二级栏 --panel（列表栏）。
  function bindGutter(el, o) {
    if (!el) return;
    let on = false, x0 = 0, w0 = 0;
    const KEY = 'vigil.gutter.' + o.var;
    const cur = () => parseInt(getComputedStyle(document.documentElement).getPropertyValue(o.var), 10) || o.def;
    const put = (v) => {
      v = Math.max(o.min, Math.min(o.max, v));
      document.documentElement.style.setProperty(o.var, v + 'px');
      if (o.onChange) o.onChange(v);
    };
    const save = () => { try { localStorage.setItem(KEY, String(cur())); } catch (_) {} };
    try {
      const saved = parseInt(localStorage.getItem(KEY) || '', 10);
      if (saved) put(saved);
    } catch (_) {}
    el.addEventListener('pointerdown', (e) => {
      on = true; x0 = e.clientX; w0 = cur(); el.classList.add('drag');
      el.setPointerCapture(e.pointerId); e.preventDefault();
    });
    el.addEventListener('pointermove', (e) => { if (on) put(w0 + (e.clientX - x0) * o.dir); });
    const stop = (e) => {
      if (!on) return;
      on = false; el.classList.remove('drag');
      save();
      try { el.releasePointerCapture(e.pointerId); } catch (_) {}
    };
    el.addEventListener('pointerup', stop);
    el.addEventListener('pointercancel', stop);
    el.addEventListener('dblclick', () => { put(o.def); save(); });
    el.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      put(cur() + (e.key === 'ArrowRight' ? 12 : -12) * o.dir);
      save();
    });
    if (o.onChange) o.onChange(cur());
  }

  // ── 启动 ──────────────────────────────────────────────────
  // ── 插件前端注入：CSS 在渲染层注入；JS 由主进程加载并注入 ──
  // 安全收紧（2026-10）：渲染层不再有 window.vigil.runJS 这种"任意代码 →
  // executeJavaScript"通道。插件 JS 由主进程凭服务端 token 自己拉取后注入，
  // 每个插件独立 try/catch，一个炸了不影响其他。CSS 无执行权，仍在渲染层注入。
  let lastPluginVersion = 0;
  // Older brains return a timestamp version. Compare actual assets so they cannot
  // reload the window every ten seconds and interrupt wallpaper playback.
  function pluginFingerprint(plugins) {
    const content = JSON.stringify(plugins
      .map(p => [p.name, p.enabled, p.js, p.css]).sort((a, b) => a[0].localeCompare(b[0])));
    let h = 2166136261;
    for (let i = 0; i < content.length; i++) h = Math.imul(h ^ content.charCodeAt(i), 16777619);
    return (h >>> 0).toString(16);
  }

  let pluginLoadInFlight = null;
  let loadedPluginNames = new Set();
  function loadPluginFrontends() {
    if (pluginLoadInFlight) return pluginLoadInFlight;
    pluginLoadInFlight = (async () => { try {
      const r = await get('/api/plugins/frontend');
      if (!r || !r.ok || !r.json) return;
      const { plugins } = r.json;
      if (!Array.isArray(plugins)) return;

      const enabled = plugins.filter(p => p && p.enabled && (p.js || p.css));
      const fingerprint = pluginFingerprint(plugins);
      if (lastPluginVersion && fingerprint === lastPluginVersion) return;

      // Plugin contributions belong to their package. Remove old registrations and
      // run their disposers before the main process injects the new enabled set.
      for (const name of loadedPluginNames) {
        if (window.VigilPlugin && window.VigilPlugin.unregisterPlugin) {
          window.VigilPlugin.unregisterPlugin(name);
        }
      }
      document.querySelectorAll('style[id^="plugin-css-"],style[id^="plugin-dyn-css-"]').forEach(el => el.remove());
      loadedPluginNames.clear();

      // 注入 CSS
      for (const p of enabled) {
        if (p.css) {
          const style = document.getElementById('plugin-css-' + p.name) || document.createElement('style');
          style.id = 'plugin-css-' + p.name;
          style.textContent = p.css;
          document.head.appendChild(style);
        }
      }

      // 注入 JS：交给主进程（它会重新拉一次资源并逐个执行）。
      if (window.vigil && window.vigil.plugins && window.vigil.plugins.load) {
        const lr = await window.vigil.plugins.load();
        if (!lr || !lr.ok) {
          toast('插件界面加载失败：' + ((lr && lr.err) || '未知原因'));
          return;
        }
        // 插件 JS 在主进程里执行，出错只有 console 一条 —— 用户看不见就以为
        // 功能坏了。这里把每个失败都念出来，别再静默。
        if (Array.isArray(lr.errors) && lr.errors.length) {
          toast('插件界面加载失败：' + lr.errors.join('；'));
        }
      }

      loadedPluginNames = new Set(enabled.map(p => p.name));
      lastPluginVersion = fingerprint;
    } catch (e) {
      console.warn('插件前端加载失败:', e);
    } finally {
      pluginLoadInFlight = null;
    } })();
    return pluginLoadInFlight;
  }

  async function boot() {
    const bootAt = performance.now();
    const perf = (name, extra) => {
      try { console.info('[perf] ' + name + ' +' + Math.round(performance.now() - bootAt) + 'ms' + (extra ? ' ' + extra : '')); } catch (_) {}
    };
    perf('renderer-dom-ready');
    if (window.vigil?.appVersion) state.version = await window.vigil.appVersion();
    if (settingsWindow) {
      const badge = document.querySelector('.chanbadge');
      if (badge) badge.textContent = '设置';
    }
    let t = 'light';
    try { t = localStorage.getItem('vigil_theme') || 'light'; } catch (_) {}
    applyTheme(t);

    // ── 插件前端注入：暴露全局 API 给插件用 ──
    window.VigilAPI = API;
    window.VigilUI = U;
    window.VigilPlugin = {
      refresh: loadPluginFrontends,
      // 注册新页面：进 PAGES 并重建导航。page.after 指定插在哪个页面后面
      // （导航栏按钮多、高度有限，插件页一律 push 到末尾会被滚动区吞掉看不见）
      registerPage(page) {
        if (!page || !page.id || !page.render) return;
        if (PAGES.find(p => p.id === page.id)) {
          const existing = PAGES.find(p => p.id === page.id);
          if (existing.__pluginOwner === page.__pluginOwner) return;
          return;
        }
        page.__pluginOwner = page.__pluginOwner || this._currentOwner || 'legacy';
        const at = page.after ? PAGES.findIndex(p => p.id === page.after) : -1;
        if (at >= 0) PAGES.splice(at + 1, 0, page);
        else PAGES.push(page);
        buildNav();
      },
      registerCleanup(owner, cleanup) {
        if (!owner || typeof cleanup !== 'function') return () => {};
        const entry = { owner: String(owner), cleanup };
        this._cleanups.push(entry);
        return () => { this._cleanups = this._cleanups.filter(x => x !== entry); };
      },
      unregisterPlugin(owner) {
        owner = String(owner || '');
        if (!owner) return;
        for (const entry of this._cleanups.filter(x => x.owner === owner)) {
          try { entry.cleanup(); } catch (e) { console.warn('[plugin dispose]', owner, e); }
        }
        this._cleanups = this._cleanups.filter(x => x.owner !== owner);
        const removed = PAGES.filter(p => p.__pluginOwner === owner);
        for (const page of removed) {
          try { if (typeof page.dispose === 'function') page.dispose(); } catch (e) { console.warn('[plugin page dispose]', owner, e); }
          PAGES.splice(PAGES.indexOf(page), 1);
        }
        (this._listeners[owner] || []).forEach(({ event, fn }) => {
          this._listeners[event] = (this._listeners[event] || []).filter(x => x.fn !== fn);
        });
        delete this._listeners[owner];
        document.getElementById('plugin-css-' + owner)?.remove();
        document.getElementById('plugin-dyn-css-' + owner)?.remove();
        buildNav();
        if (removed.some(p => p.id === state.page)) go('chat');
      },
      // 动态注入 CSS
      injectCSS(name, css) {
        const id = 'plugin-dyn-css-' + name;
        let el = document.getElementById(id);
        if (!el) {
          el = document.createElement('style');
          el.id = id;
          document.head.appendChild(el);
        }
        el.textContent = css;
      },
      // 简单事件总线
      _listeners: {},
      _cleanups: [],
      on(event, fn) {
        const owner = this._currentOwner || 'legacy';
        (this._listeners[event] = this._listeners[event] || []).push({ owner, fn });
        (this._listeners[owner] = this._listeners[owner] || []).push({ event, fn });
      },
      emit(event, data) {
        (this._listeners[event] || []).forEach(entry => {
          try { entry.fn(data); } catch (e) { console.error('[插件事件]', e); }
        });
      },
    };

    buildNav();
    watchSettingsBack();
    perf('nav-built');
    if (!settingsWindow && window.VigilAgentCenter) {
      window.VigilAgentCenter.start();
      window.vigil && window.vigil.agentCenter && window.vigil.agentCenter.onOpen(() => go('agent-center'));
    }

    bindGutter(document.getElementById('gPanel'), { var: '--panel', min: 180, max: 460, def: 240, dir: 1 });
    // 一级栏：拖窄是纯图标，拖过 112px 自动带上文字标签（替代原来的折叠按钮）
    bindGutter(document.getElementById('gRail'), {
      var: '--rail', min: 52, max: 220, def: 62, dir: 1,
      onChange: (v) => document.body.classList.toggle('rail-wide', v >= 112),
    });

    if (settingsWindow && window.vigil && window.vigil.settings) {
      window.vigil.settings.onNavigate((target) => {
        state.settingsSub = target.section || 'model';
        go('system');
      });
    }

    // 窗口控制：只在 Electron 里存在（harness 没有 window.vigil，点了也没事）
    const q = (id, fn) => {
      const b = document.getElementById(id);
      if (b) b.addEventListener('click', fn);
    };
    q('winMin', () => window.vigil && window.vigil.win && window.vigil.win.minimize());
    q('winMax', () => window.vigil && window.vigil.win && window.vigil.win.maximize());
    q('winClose', () => window.vigil && window.vigil.win && window.vigil.win.close());
    q('settingsReturn', () => window.vigil && window.vigil.win && window.vigil.win.close());
    window.vigil && window.vigil.win && window.vigil.win.onState(() => {});

    // 主进程的本地操控确认：不在聊天页就先切过去（pages.js 的 confirm 气泡接住）
    window.vigil && window.vigil.local && window.vigil.local.onConfirm(() => {
      if (state.page !== 'chat') go('chat');
    });

    // 首屏先使用本地 shell；壁纸、插件和本机 Brain 连接都异步进行。
    if (settingsWindow) {
      state.settingsSub = windowQuery.get('section') || 'model';
      void go('system').then(() => {
        perf('first-usable-settings');
        // 等两帧再回报：go() 只保证 DOM 建好，样式与首帧还要一次合成。
        // 隐藏窗口的 rAF 可能被节流，另挂一个定时器兜底，谁先到算谁。
        let reported = false;
        const report = () => {
          if (reported) return;
          reported = true;
          if (window.vigil && window.vigil.settings) window.vigil.settings.ready();
        };
        requestAnimationFrame(() => requestAnimationFrame(report));
        setTimeout(report, 400);
      });
    } else {
      void go(state.page).then(() => perf('first-usable'));
    }
    // 设置窗口也要加载插件前端：壁纸/玻璃主题需要在设置窗口里同样生效
    //（此前只有主窗口注入，设置窗口永远是白底硬框）。
    void loadPluginFrontends();

    // 先按存下来的地址连一次；连不上且在 Electron 里就自己再找一遍。
    // 没有这段：换过地址（隧道 ↔ 本机）后会一直停在"未连接"，
    // 因为"有 token"就以为配好了，永远不会重新探测。
    perf('brain-connect-start');
    setStatus('连接中');
    ping().then(async () => {
      if (!state.conn.ok && window.vigil) {
        setStatus('本地 Brain 未就绪，正在重试');
        const again = await window.vigil.autoConnect().catch(() => null);
        if (again && again.ok) {
          await ping();
          toast('已连上本机 Brain ' + again.base);
        } else {
          // 起不来就把原因说清楚：本地 Brain 是唯一后端，静默失败等于"她不见了"
          state.autoFail = again || { err: state.conn.err || '未知原因' };
          toast('本地 Brain 没起来：' + (state.autoFail.err || '请到设置 → 本地内核 查看'));
        }
      }
      if (state.conn.ok && lastPluginVersion === 0) void loadPluginFrontends();
      setStatus();
      refreshStats();
      perf('brain-connect-end', state.conn.ok ? 'ok=' + state.conn.ms + 'ms' : 'failed');
      if(state.page==='chat')window.dispatchEvent(new Event('vigil-brain-connected'));
      if (!settingsWindow) maybeWizard();
    });

    // 心跳：30 秒一轮 ping + 状态读数刷新。断线时顺手让主进程拉一次本地
    // Brain（它可能刚好起完了），连上就继续用，不用重启客户端。
    setInterval(async () => {
      await ping();
      if (!state.conn.ok && window.vigil && window.vigil.autoConnect) {
        const again = await window.vigil.autoConnect().catch(() => null);
        if (again && again.ok) await ping();
      }
      setStatus();
      refreshStats();
    }, 30000);

    // 插件热重载：10 秒查一次版本，变了就刷新页面（本地应用刷新零点几秒）
    setInterval(async () => {
      try {
        const r = await get('/api/plugins/frontend');
        if (r && r.ok && r.json && r.json.version) {
          if (lastPluginVersion === 0) await loadPluginFrontends();
          else if (Array.isArray(r.json.plugins) && pluginFingerprint(r.json.plugins) !== lastPluginVersion) await loadPluginFrontends();
        }
      } catch (_) { /* 静默 */ }
    }, 10000);

    refreshStats();
  }

  // ── 首启向导（填 key 即用）─────────────────────────────────
  // 本地内核跑着、连接正常、但一个模型 key 都没配 → 直接把人领到
  // "模型与服务商"表单前。配过一次（wizard_done）就再也不弹。
  async function maybeWizard() {
    try {
      if (!window.vigil || !window.vigil.sidecar || !state.conn.ok) return;
      const cfg = await window.vigil.cfg.get();
      if (cfg && cfg.wizard_done) return;
      const sc = await window.vigil.sidecar.status();
      if (!sc || sc.status !== 'ready' || !sc.port) return;
      const r = await get('/api/models');
      if (r && r.ok && r.json && r.json.current && !r.json.current.api_key_set) {
        if (window.vigil.settings) {
          window.vigil.settings.open({ section: 'model' });
        } else {
          state.settingsSub = 'model';
          go('system');
        }
        toast('填好模型 API key 就能聊了');
      } else {
        window.vigil.cfg.set({ wizard_done: true });
      }
    } catch (e) { /* 向导是锦上添花，任何异常都不该挡住启动 */ }
  }

  // boot 失败必须看得见：异常打进状态栏和 __bootErr，不许无声黑屏
  document.addEventListener('DOMContentLoaded', () => {
    const reportBootError = (e) => {
      window.__bootErr = e.message;
      console.error('启动失败:', e);
      if (statusEl) statusEl.innerHTML = '<span style="color:var(--err)">启动失败: ' + esc(e.message) + '</span>';
      if (viewEl) viewEl.innerHTML = '<div class="wrap"><div class="card err"><b>客户端启动失败</b><p>' + esc(e.message) + '</p></div></div>';
    };
    try { boot().catch(reportBootError); }
    catch (e) { reportBootError(e); }
  });
})();
