// -*- coding: utf-8 -*-
/**
 * 壳：顶部编号导航、§ 页号注入、状态栏、主题、分栏拖拽、启动顺序。
 *
 * 页面本身在 pages.js 里（PAGES: {id,name,icon,render(ctx)}），这里只负责
 * "把舞台搭好、把当前页叫上来"。渲染层不持有任何密钥，网络全走主进程。
 *
 * 对齐 index.html 的 ID：gnav / panel / topbar / view / status / gPanel /
 * themeBtn / reloadBtn / winMin / winMax / winClose。
 * 全部带存在性守卫 —— 网页 harness 里没有 window.vigil 也要能跑 UI；
 * 启动失败必须看得见：异常打进状态栏和 __bootErr，不许无声黑屏。
 */
(function () {
  'use strict';
  const { esc, icon, fmtNum, toast } = U;
  const { state, ping, get } = API;

  const gnavEl = document.getElementById('gnav');
  const panelEl = document.getElementById('panel');
  const viewEl = document.getElementById('view');
  const topbarEl = document.getElementById('topbar');
  const statusEl = document.getElementById('status');

  // ── 顶部编号导航：01 对话 02 人设 …（活动项黑底白字）────────
  // 与 Hermes 编辑部版一致：序号 + 页名，活动项实底。图标栏废弃。
  function buildNav() {
    if (!gnavEl) return;
    gnavEl.innerHTML = PAGES.map((p, i) =>
      '<button class="gbtn' + (p.id === state.page ? ' on' : '') + '" data-page="' + p.id + '" data-tip="' + esc(p.name) + '">' +
      '<i>' + String(i + 1).padStart(2, '0') + '</i>' + esc(p.name) + '</button>').join('');
    gnavEl.querySelectorAll('.gbtn').forEach((b) =>
      b.addEventListener('click', () => go(b.dataset.page)));
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
      '<b>' + (state.conn.ok ? '已连接' : '未连接') + '</b>'];
    if (state.conn.ok && state.conn.ms != null) seg.push(esc(String(state.conn.ms)) + ' ms');
    seg.push('<i class="ssep">│</i>渠道 <b>本机控制台（pc）</b>');
    if (lastStats && lastStats.model) seg.push('<i class="ssep">│</i>模型 <b>' + esc(lastStats.model) + '</b>');
    if (lastStats && lastStats.tok) seg.push('<i class="ssep">│</i>今日 TOKENS <b>' + fmtNum(lastStats.tok) + '</b>');
    if (extra) seg.push('<i class="ssep">│</i>' + esc(extra));
    seg.push('<span class="sp"></span><span class="mono muted">' + esc(state.base || '') + '</span><span class="mono muted">v0.1.0</span>');
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
  async function go(id) {
    const page = PAGES.find((p) => p.id === id) || PAGES[0];
    state.page = page.id;
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
    };
    try {
      await page.render(ctx);
    } catch (e) {
      console.error('渲染失败:', e);
      if (viewEl) viewEl.innerHTML = '<div class="wrap"><div class="card err"><b>这一页画不出来</b>' +
        '<div class="mono" style="font-size:11.5px;color:var(--err);margin-top:6px">' + esc(e.message) + '</div></div></div>';
    }
    // § 章节编号：页号 = 导航序号（01–08）。子页换名不动编号，
    // 所以渲染完再补；子页只改 h1 文本，编号插在最前不受影响。
    if (topbarEl && !topbarEl.querySelector('.tnum')) {
      topbarEl.insertAdjacentHTML('afterbegin',
        '<span class="tnum mono">§ ' + String(PAGES.indexOf(page) + 1).padStart(2, '0') + '</span>');
    }
  }

  // ── 主题 ──────────────────────────────────────────────────
  function applyTheme(t) {
    state.theme = t === 'dark' ? 'dark' : 'light';
    document.documentElement.dataset.theme = state.theme;
    const b = document.getElementById('themeBtn');
    if (b) b.innerHTML = icon('moon', 'xs') + (state.theme === 'dark' ? '浅色' : '深色');
    try { localStorage.setItem('vigil_theme', state.theme); } catch (_) {}
  }

  // ── 分栏拖拽（左栏宽度，180–460，双击复位，方向键微调）────
  function bindGutter(el, o) {
    if (!el) return;
    let on = false, x0 = 0, w0 = 0;
    const cur = () => parseInt(getComputedStyle(document.documentElement).getPropertyValue(o.var), 10) || o.def;
    const put = (v) => {
      v = Math.max(o.min, Math.min(o.max, v));
      document.documentElement.style.setProperty(o.var, v + 'px');
    };
    el.addEventListener('pointerdown', (e) => {
      on = true; x0 = e.clientX; w0 = cur(); el.classList.add('drag');
      el.setPointerCapture(e.pointerId); e.preventDefault();
    });
    el.addEventListener('pointermove', (e) => { if (on) put(w0 + (e.clientX - x0) * o.dir); });
    const stop = (e) => {
      if (!on) return;
      on = false; el.classList.remove('drag');
      try { el.releasePointerCapture(e.pointerId); } catch (_) {}
    };
    el.addEventListener('pointerup', stop);
    el.addEventListener('pointercancel', stop);
    el.addEventListener('dblclick', () => put(o.def));
    el.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      put(cur() + (e.key === 'ArrowRight' ? 12 : -12) * o.dir);
    });
  }

  // ── 启动 ──────────────────────────────────────────────────
  async function boot() {
    let t = 'light';
    try { t = localStorage.getItem('vigil_theme') || 'light'; } catch (_) {}
    applyTheme(t);

    buildNav();

    bindGutter(document.getElementById('gPanel'), { var: '--panel', min: 180, max: 460, def: 240, dir: 1 });

    const tb = document.getElementById('themeBtn');
    if (tb) tb.addEventListener('click', () => applyTheme(state.theme === 'dark' ? 'light' : 'dark'));
    const rb = document.getElementById('reloadBtn');
    if (rb) rb.addEventListener('click', () => go(state.page));

    // 窗口控制：只在 Electron 里存在（harness 没有 window.vigil，点了也没事）
    const q = (id, fn) => {
      const b = document.getElementById(id);
      if (b) b.addEventListener('click', fn);
    };
    q('winMin', () => window.vigil && window.vigil.win && window.vigil.win.minimize());
    q('winMax', () => window.vigil && window.vigil.win && window.vigil.win.maximize());
    q('winClose', () => window.vigil && window.vigil.win && window.vigil.win.close());
    window.vigil && window.vigil.win && window.vigil.win.onState(() => {});

    // 主进程的本地操控确认：不在聊天页就先切过去（pages.js 的 confirm 气泡接住）
    window.vigil && window.vigil.local && window.vigil.local.onConfirm(() => {
      if (state.page !== 'chat') go('chat');
    });

    // 先按存下来的地址连一次；连不上且在 Electron 里就自己再找一遍。
    // 没有这段：换过地址（隧道 ↔ 本机）后会一直停在"未连接"，
    // 因为"有 token"就以为配好了，永远不会重新探测。
    ping().then(async () => {
      if (!state.conn.ok && window.vigil && state.conn.err) {
        const again = await window.vigil.autoConnect().catch(() => null);
        if (again && again.ok) {
          await ping();
          toast('已重新连上 ' + again.base);
        }
      }
      setStatus();
      refreshStats();
      if (state.page === 'chat') go('chat');
      maybeWizard();
    });

    // 心跳：30 秒一轮 ping + 状态读数刷新
    setInterval(async () => {
      await ping();
      setStatus();
      refreshStats();
    }, 30000);
    refreshStats();
  }

  // ── 首启向导（填 key 即用）─────────────────────────────────
  // 本地大脑跑着、连接正常、但一个模型 key 都没配 → 直接把人领到
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
        // 顶级页是分组（04 连接与系统 = 'system'），具体表单在组内的
        // 「模型与服务商」子页 —— settingsSub 让 renderSettings 直接切过去
        state.settingsSub = 'model';
        go('system');
        toast('填好模型 API key 就能聊了');
      } else {
        window.vigil.cfg.set({ wizard_done: true });
      }
    } catch (e) { /* 向导是锦上添花，任何异常都不该挡住启动 */ }
  }

  // boot 失败必须看得见：异常打进状态栏和 __bootErr，不许无声黑屏
  document.addEventListener('DOMContentLoaded', () => {
    try { boot(); }
    catch (e) {
      window.__bootErr = e.message;
      console.error('启动失败:', e);
      if (statusEl) statusEl.innerHTML = '<span style="color:var(--err)">启动失败: ' + esc(e.message) + '</span>';
    }
  });
})();
