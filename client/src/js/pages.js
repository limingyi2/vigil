// -*- coding: utf-8 -*-
/**
 * 八个页面。
 *
 * 一条规矩：**后端没有的能力就如实写出来**（例如记忆列表现在没有接口），
 * 不要摆一个永远为空的假列表 —— 那种"看着有、其实不工作"最容易误导人。
 *
 * 每个页面是 { id, name, tip, icon, subs?, render(ctx) }：
 *   ctx = { view, topbar, panel, src, refresh }
 * render 自己负责填 topbar / panel / view 三块。
 */
(function (global) {
  'use strict';
  const { esc, icon, fmtTime, fmtAgo, bytes, toast, empty, fmtNum } = U;
  const { get, post, pick, state } = API;

  function confirmDialog(message) {
    return new Promise((resolve) => {
      const overlay = document.createElement('div');
      overlay.className = 'vigil-confirm-backdrop';
      overlay.innerHTML = '<section class="vigil-confirm" role="dialog" aria-modal="true" aria-labelledby="vigilConfirmTitle">' +
        '<h3 id="vigilConfirmTitle">确认操作</h3><p></p><div class="vigil-confirm-actions">' +
        '<button type="button" class="btn" data-choice="cancel">取消</button>' +
        '<button type="button" class="btn fill" data-choice="ok">继续</button></div></section>';
      overlay.querySelector('p').textContent = String(message || '');
      const finish = (value) => { overlay.remove(); resolve(value); };
      overlay.querySelector('[data-choice="cancel"]').onclick = () => finish(false);
      overlay.querySelector('[data-choice="ok"]').onclick = () => finish(true);
      overlay.addEventListener('click', (e) => { if (e.target === overlay) finish(false); });
      document.addEventListener('keydown', function onKey(e) {
        if (e.key === 'Escape' && overlay.isConnected) { document.removeEventListener('keydown', onKey); finish(false); }
      });
      document.body.appendChild(overlay);
      overlay.querySelector('[data-choice="ok"]').focus();
    });
  }
  function inputDialog(message, initialValue) {
    return new Promise((resolve) => {
      const overlay = document.createElement('div'); overlay.className = 'vigil-confirm-backdrop';
      overlay.innerHTML = '<section class="vigil-confirm" role="dialog" aria-modal="true"><h3>编辑内容</h3><p></p><textarea class="input" rows="5"></textarea><div class="vigil-confirm-actions"><button type="button" class="btn" data-choice="cancel">取消</button><button type="button" class="btn fill" data-choice="ok">保存</button></div></section>';
      overlay.querySelector('p').textContent = String(message || '');
      const input = overlay.querySelector('textarea'); input.value = String(initialValue || '');
      const finish = (value) => { overlay.remove(); resolve(value); };
      overlay.querySelector('[data-choice="cancel"]').onclick = () => finish(null);
      overlay.querySelector('[data-choice="ok"]').onclick = () => finish(input.value);
      overlay.addEventListener('click', (e) => { if (e.target === overlay) finish(null); });
      document.body.appendChild(overlay); input.focus();
    });
  }

  // ── 通用零件 ────────────────────────────────────────────────
  const UI_RECORD_LIMITS = { count: 100, chars: 16384, days: 30 };
  function recordKey() {
    const cfg = state.cfg || {};
    return 'vigil.display.v1:' + JSON.stringify([state.base || cfg.base || '', cfg.device_id || '',
      cfg.token_kind || '', state.src || 'pc', state.chatConversationId || 'welcome']);
  }
  async function loadRecordScope() {
    try { await API.loadCfg(); return true; }
    catch (error) { state.recordScopeError = String(error && error.message || '无法读取连接身份'); toast('无法读取当前连接身份，暂不加载或提交会话记录'); return false; }
  }
  function localRecords(key = recordKey()) {
    try { const value = JSON.parse(localStorage.getItem(key) || '[]'); return Array.isArray(value) ? value : []; }
    catch (_) { return []; }
  }
  function rememberTurn(rid, patch, scope = recordKey()) {
    if (!rid) return;
    try {
      if (JSON.parse(localStorage.getItem(scope + ':cleared') || '[]').includes(rid)) return;
      const records = localRecords(scope);
      let item = records.find(r => r.request_id === rid);
      if (!item) { item = {request_id:rid,created_at:Date.now()/1000,status:'pending',content:''}; records.push(item); }
      // Explicit whitelist: no thought deltas, tokens, provider bodies or config.
      for (const k of ['input','kind','job_id','status','content','error']) if (patch[k] !== undefined) item[k] = patch[k];
      item.content = String(item.content || '').slice(-UI_RECORD_LIMITS.chars);
      localStorage.setItem(scope, JSON.stringify(records.filter(r=>r.created_at > Date.now()/1000-UI_RECORD_LIMITS.days*86400).slice(-UI_RECORD_LIMITS.count)));
    } catch (_) { toast('当前记录未能保存到本机，请先复制内容'); }
  }
  function streamBodyHtml(thinkId, textId) {
    return '<div class="think-block streaming"><button class="think-toggle">' +
      icon('brain', 'sm') + '思考 <span class="think-caret">▴</span></button>' +
      '<div class="think-content" id="' + thinkId + '"><div class="think-text" id="' + thinkId + '_inner"></div></div></div>' +
      '<div class="txt md streaming-text" id="' + textId + '" style="min-height:24px"></div>' +
      '<div class="foot"><span class="stream-status">正在生成…</span></div>';
  }

  function subscribeUntilDetached(root, subscribe, handler) {
    let active = true, unsubscribe = null;
    const observer = new MutationObserver(() => { if (!root.isConnected) close(); });
    function close() {
      if (!active) return;
      active = false;
      observer.disconnect();
      if (typeof unsubscribe === 'function') unsubscribe();
    }
    unsubscribe = subscribe(evt => { if (active && root.isConnected) handler(evt); });
    // The transport may synchronously deliver a terminal snapshot during subscribe.
    if (!active && typeof unsubscribe === 'function') unsubscribe();
    if (active) observer.observe(document.body, {childList:true,subtree:true});
    return close;
  }

  function creatorErrorText(error) {
    const e = error || {};
    const stages = {model_request:'模型请求',network:'网络连接',auth:'认证',parse:'JSON 解析',
      codegen:'产物构造',static_validation:'静态校验',draft_save:'草稿保存'};
    return [e.message || '请求未完成', e.stage ? '失败阶段：' + (stages[e.stage] || e.stage) : '',
      e.error_code || e.code ? '错误码：' + (e.error_code || e.code) : '',
      e.http_status ? 'HTTP：' + e.http_status : '', e.hint ? '建议：' + e.hint : '',
      e.target ? '当前连接：' + e.target : '',
      e.log_id ? '日志 ID：' + e.log_id : ''].filter(Boolean).join('；');
  }

  function creatorRequestFailure(r) {
    const status = Number(r && r._status) || 0;
    const confirmed = status >= 400 && status < 500 && status !== 408;
    return {status: confirmed ? 'failed' : 'pending', error: {
      message: status === 404 ? '当前 Brain 没有 Creator jobs 接口，未开始生成'
        : confirmed ? 'Creator 请求被拒绝，未开始生成' : '请求未确认完成，未自动重发',
      code: status === 404 ? 'creator_api_missing' : confirmed ? 'creator_request_rejected' : 'creator_request_unconfirmed',
      http_status: status || undefined, target: (r && r._target) || '',
      hint: status === 404 ? '检查 Electron 与 Brain 版本、sidecar 启动路径是否匹配'
        : confirmed ? '检查认证和请求参数后手动重试' : '检查连接和已有作业，确认后再手动重试',
    }};
  }

  async function watchCreatorJob(root, initial, scope = recordKey()) {
    const jid = initial.job_id;
    const thinkId = 'creatorThink_' + jid;
    root.innerHTML = '<div class="bdy">' + streamBodyHtml(thinkId, 'creatorText_' + jid) +
      '<button class="btn oline creator-cancel">取消生成</button></div>';
    const text = root.querySelector('.streaming-text');
    const thinking = root.querySelector('.think-text');
    const block = root.querySelector('.think-block');
    const status = root.querySelector('.stream-status');
    const cancel = root.querySelector('.creator-cancel');
    root.querySelector('.think-toggle').onclick = () => {
      const content = root.querySelector('.think-content'); content.hidden = !content.hidden;
    };
    let current = initial, cursor = 0, finished = false;
    text.textContent = initial.content || '';
    block.hidden = true;
    cancel.onclick = async () => {
      const r = pick(await post('/api/creator/jobs/' + jid + '/cancel', {}));
      if (r.ok) { current = r; applySnapshot(r); }
    };
    function applySnapshot(item) {
      current = item;
      cursor = Math.max(cursor, item.event_cursor || 0);
      text.textContent = item.content || '';
      rememberTurn(item.request_id, {input:item.feedback || item.requirement,kind:item.kind,job_id:jid,
        status:item.status,content:item.content || '',error:item.error}, scope);
      status.textContent = item.error ? creatorErrorText(item.error) : item.stage || item.status;
      status.setAttribute('role', item.error ? 'alert' : 'status');
      finished = ['succeeded', 'failed', 'cancelled', 'interrupted'].includes(item.status);
      if (finished) {
        cancel.hidden = true;
        if (item.status !== 'succeeded') status.textContent += '（结果不完整）';
      }
    }
    const streamApi = global.vigil && (global.vigil.onStreamEvent || global.vigil.onCreatorEvent);
    const off = streamApi ? subscribeUntilDetached(root, streamApi, evt => {
      if (evt.job_id !== jid || finished || !root.isConnected || recordKey() !== scope) return;
      if (evt.type === 'snapshot') { applySnapshot(evt.payload); return; }
      if (evt.seq <= cursor) return;
      cursor = evt.seq;
      const p = evt.payload || {};
      if (evt.type === 'error') { applySnapshot({...current,status:'failed',error:p,content:text.textContent,event_cursor:cursor}); return; }
      if (evt.type === 'text_delta') {
        text.textContent += p.text || '';
        rememberTurn(initial.request_id, {content:text.textContent}, scope);
      }
      if (evt.type === 'think_delta') { block.hidden = false; thinking.textContent += p.text || ''; }
      if (evt.type === 'stage') status.textContent = p.stage;
    }) : null;
    applySnapshot(initial);
    try {
      while (!finished && root.isConnected && recordKey() === scope) {
        if (global.vigil && global.vigil.apiStream && (global.vigil.onStreamEvent || global.vigil.onCreatorEvent)) {
          await global.vigil.apiStream('/api/creator/jobs/' + jid + '/events?after=' + cursor, null);
        } else await new Promise(resolve => setTimeout(resolve, 500));
        if (!root.isConnected || recordKey() !== scope) break;
        const r = pick(await get('/api/creator/jobs/' + jid));
        if (!r.ok) { status.textContent = '连接中断，记录已保留，请刷新查询'; break; }
        applySnapshot(r);
      }
      if (current.status === 'succeeded' && current.draft_id && root.isConnected) {
        const r = pick(await get('/api/creator/draft?id=' + encodeURIComponent(current.draft_id)));
        if (r.ok && r.draft) { root.innerHTML = draftCardHtml(r.draft); bindDraftCard(root, r.draft); }
      }
      return current;
    } finally { if (off) off(); }
  }
  const tag = (cls, text) => '<span class="tag ' + cls + '">' + esc(text) + '</span>';
  /** 时间戳归一成毫秒：秒 / 毫秒 / 字符串都能吃（与 ui.js fmtTime 同一套规则） */
  const toMs = (t) => {
    const n = typeof t === 'number' ? (t < 1e12 ? t * 1000 : t) : Date.parse(String(t).replace(/-/g, '/'));
    return n && !isNaN(n) ? n : 0;
  };
  const errBox = (msg, hint) =>
    '<div class="card" style="border-color:var(--line)"><b style="font-size:13px">' + esc(msg) + '</b>' +
    (hint ? '<div class="muted" style="font-size:12px;margin-top:6px">' + esc(hint) + '</div>' : '') + '</div>';

  function topbar(title, sub, right) {
    return '<h1>' + esc(title) + '</h1>' + (sub ? '<span class="sub">' + esc(sub) + '</span>' : '') +
      '<div class="sp"></div>' + (right || '');
  }

  function heroHtml() {
    return '<div class="welcome-brand" aria-hidden="true">V</div>' +
      '<h1>有什么可以帮你？</h1>' +
      '<p>与 Vigil 一起思考、编程和执行任务</p>' +
      '<div class="hero-hints">' +
      ['帮我看看今天的日程', '把刚才那段整理成清单', '看看下载文件夹里有什么']
        .map((x) => '<button class="chip" data-hint="' + x + '">' + esc(x) + '</button>').join('') +
      '</div>';
  }

  /** 左栏：分组标题 + 列表项 */
  function li(opts) {
    const o = opts || {};
    return '<div class="li ' + (o.on ? 'on' : '') + '"' + (o.attrs || '') + '>' +
      (o.icon ? '<span class="ic">' + icon(o.icon, 'sm') + '</span>' : '') +
      '<div class="tx"><b>' + esc(o.title || '') + '</b>' + (o.sub ? '<span>' + esc(o.sub) + '</span>' : '') + '</div>' +
      (o.meta ? '<span class="meta">' + esc(o.meta) + '</span>' : '') + '</div>';
  }

  function bindLi(root, fn) {
    root.querySelectorAll('.li[data-k]').forEach((n) => n.addEventListener('click', () => fn(n.dataset.k, n)));
  }

  // ═══ 模型快速切换（主页 composer 旁的模型名）═══════════════════
  //
  // 产品意图：在设置里选过一次的模型，之后在聊天框旁边就该点得到，
  // 不用每次都跑一趟设置页。
  //
  // 数据与写入都复用现有机制，**没有第二份模型配置**：
  //   - 读：GET /api/models/saved   —— 服务端从 config 的 saved_models 派生的清单，
  //                                    只回名字和服务商，不含任何密钥；
  //   - 写：POST /api/models/quick-switch —— 服务端最终仍走 model_hub.apply_cfg()
  //                                    写回同一份配置（与设置页「保存并生效」同一条路）。
  //
  // 切换失败一律**明说**（缺密钥 / 被服务商拒绝 / 没能验证），
  // 绝不静默换成别的模型 —— 那会让人拿着 B 的回复以为在用 A。
  function modelQuickBind(view, ctx, hooks) {
    const h = hooks || {};
    const pop = view.querySelector('#modelPop');
    const btn = view.querySelector('#modelBtn');
    if (!pop || !btn) return null;
    const listEl = view.querySelector('#mqList');
    const searchEl = view.querySelector('#mqSearch');
    const searchToggle = view.querySelector('#mqSearchToggle');
    const msgEl = view.querySelector('#mqMsg');

    let data = { models: [], current: {} };
    let open = false;
    let switching = false;

    const nameOf = (m) => m.name || String(m.model || '').split('/').pop() || m.model || '（未命名）';
    const provOf = (m) => m.provider_name || m.provider || '';
    // 🔴 顶栏那颗模型名不在 view 里（在 ctx.topbar 里）—— 用 view.querySelector
    // 拿到的永远是 null，表现就是"composer 上写着 A、顶栏还写着 B"这种自相矛盾。
    const topEl = () => ((ctx && ctx.topbar) || view).querySelector('.chat-model');
    function say(text, bad) {
      if (!msgEl) return;
      msgEl.textContent = text || '';
      msgEl.style.color = bad ? 'var(--err)' : '';
      msgEl.title = text || '';
    }
    // 当前模型名称同步到 composer 和顶栏；列表行的勾选由服务端 active 状态绘制。
    function label() {
      const c = data.current || {};
      btn.textContent = (c.name || c.model || '未配置') + ' ▾';
      btn.title = c.model
        ? ('当前模型：' + c.model + '（' + (c.provider_name || '?') + '）· 点一下切换')
        : '还没有可用模型：点开看看，或去设置里配一个';
      const top = topEl();
      if (top && c.model) { top.textContent = c.model; top.title = c.model; }
    }
    function draw() {
      const q = String((searchEl && searchEl.value) || '').trim().toLowerCase();
      const all = data.models || [];
      const rows = all.filter((m) => !q
        || (nameOf(m) + ' ' + provOf(m) + ' ' + m.model).toLowerCase().indexOf(q) >= 0);
      if (!rows.length) {
        listEl.innerHTML = '<div class="muted" style="padding:12px 0;font-size:12px">'
          + (all.length ? '没有匹配的模型' : '还没有保存过模型：在设置里选一次，这里就有了') + '</div>';
        return;
      }
      listEl.innerHTML = rows.map((m) => {
        const i = all.indexOf(m);
        // 连接状态只用服务端给的事实：这家存没存过密钥。
        // 真正能不能连通留到切换时探（探不通会明说，不在这儿编）。
        const status = !m.has_key && m.provider !== 'custom'
          ? '<span class="mq-status">缺少密钥</span>' : '';
        return '<div class="mq-item"><button class="mqrow' + (m.active ? ' on' : '') + '" type="button" data-i="' + i +
          '" aria-pressed="' + String(!!m.active) + '">' +
          '<span class="mq-check" aria-hidden="true">' + (m.active ? icon('check', 'sm') : '') + '</span>' +
          '<span class="lb">' +
          '<b>' + esc(nameOf(m)) + (m.unsaved ? ' <span class="muted">(未保存)</span>' : '') + '</b>' +
          '<span class="muted">' + esc(provOf(m)) + ' · ' + esc(m.model) + '</span>' +
          '</span>' + status +
          '</button>' + (m.unsaved ? '' : '<button class="mq-x" type="button" data-x="' + i +
            '" title="从清单里移除（不影响当前设置）" aria-label="从清单里移除 ' + esc(nameOf(m)) + '">×</button>') + '</div>';
      }).join('');
      listEl.querySelectorAll('.mqrow').forEach((n) => {
        const pickRow = () => {
          const m = all[Number(n.dataset.i)];
          if (m) select(m);
        };
        n.addEventListener('click', (e) => {
          e.stopPropagation();
          if (e.target.closest('.mq-x')) return;
          pickRow();
        });
        n.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pickRow(); }
        });
      });
      listEl.querySelectorAll('.mq-x').forEach((n) => {
        const stop = (e) => { e.stopPropagation(); };
        n.addEventListener('click', (e) => { stop(e); forget(all[Number(n.dataset.x)]); });
      });
    }
    // 从清单移除一条（需求：删掉模型后下拉要跟着变）。只动清单不动当前配置，
    // 所以正在用的那个模型不会因为"从列表里删掉"就悄悄被换走。
    async function forget(m) {
      if (!m) return;
      if (!await confirmDialog('从模型清单里移除「' + nameOf(m) + '」？只影响这个下拉列表，'
        + '当前设置和聊天记录都不动。')) return;
      say('正在移除…');
      try {
        const r = pick(await post('/api/models/forget',
          { provider: m.provider, base: m.base, model: m.model }));
        if (!r || !r.ok) { say('移除失败：' + ((r && (r._err || r.err)) || '未知错误'), true); return; }
        await load();
        say(r.removed ? '已从清单移除' : '清单里本来就没有它');
      } catch (e) {
        say('移除失败：' + (e && e.message ? e.message : e), true);
      }
    }
    async function load() {
      const r = pick(await get('/api/models/saved'));
      if (!r || !r.ok) { say('读不到模型清单：' + ((r && (r._err || r.err)) || '未知错误'), true); return; }
      data = { models: r.models || [], current: r.current || {} };
      label();
      draw();
      if (open) placePop();
    }
    async function select(m) {
      if (switching) return;
      if (h.isBusy && h.isBusy()) { say('正在生成回复：等这一轮结束再切，免得上面显示的模型和实际用的对不上', true); return; }
      if (m.active) { closePop(); return; }
      if (!m.has_key && m.provider !== 'custom') {
        // 这份清单可能是旧的：key 也许刚在别处补上（设置页 / 手机上 / 另一个窗口），
        // 而面板一直开着没重新拉过。先跟服务端对一次再下结论，免得凭过期状态
        // 拒掉一个其实能用的模型。
        await load();
        const fresh = (data.models || []).find((x) =>
          x.provider === m.provider && x.model === m.model);
        if (fresh) m = fresh;
      }
      if (!m.has_key && m.provider !== 'custom') {
        // 真缺密钥就直说，别让服务端回落到上一个能用的模型 —— 那正是要避免的"看着切了"
        say('「' + provOf(m) + '」还没存过密钥：先加一次它的 key（点下面「添加或管理模型」）', true);
        return;
      }
      switching = true;
      say('正在切到 ' + m.model + '…');
      try {
        const r = pick(await post('/api/models/quick-switch',
          { provider: m.provider, base: m.base, model: m.model }));
        if (!r || !r.ok) { say('切换失败：' + ((r && (r._err || r.err)) || '未知错误'), true); return; }
        await load();
        say('');
        if (r.warn) say(r.warn, true);
        h.onSwitched && h.onSwitched(data.current || {});
      } catch (e) {
        say('切换失败：' + (e && e.message ? e.message : e), true);
      } finally {
        switching = false;
      }
    }
    function placePop() {
      if (pop.hidden || !btn.isConnected) return;
      const composer = pop.closest('.composer');
      if (!composer) return;
      const cr = composer.getBoundingClientRect();
      const br = btn.getBoundingClientRect();
      const width = pop.getBoundingClientRect().width;
      const left = Math.max(cr.left + 12, Math.min(window.innerWidth - width - 12, br.right - width));
      const top = Math.max(12, br.top - pop.offsetHeight - 8);
      pop.style.left = (left - cr.left) + 'px';
      pop.style.right = 'auto';
      pop.style.top = (top - cr.top) + 'px';
      pop.style.bottom = 'auto';
    }
    function closePop() { open = false; pop.hidden = true; }
    function openPop() {
      open = true; pop.hidden = false;
      placePop();
      if (searchEl) { searchEl.value = ''; searchEl.hidden = true; }
      say('');
      load();
    }
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (h.isBusy && h.isBusy()) { toast('正在生成回复：这一轮结束再切模型'); return; }
      if (open) closePop(); else openPop();
    });
    view.querySelector('#mqClose').addEventListener('click', closePop);
    if (searchToggle) searchToggle.addEventListener('click', () => {
      if (!searchEl) return;
      searchEl.hidden = !searchEl.hidden;
      if (searchEl.hidden) { searchEl.value = ''; draw(); }
      else searchEl.focus();
      placePop();
    });
    window.addEventListener('resize', placePop);
    view.querySelector('#mqManage').addEventListener('click', (e) => {
      e.stopPropagation();
      closePop();
      if (global.vigil && global.vigil.settings) {
        global.vigil.settings.open({ section: 'model', source: 'local' });
      } else {
        state.settingsSub = 'model';
        state.mSrc = 'local';
        ctx.navigate && ctx.navigate('system');
      }
    });
    searchEl.addEventListener('input', draw);
    searchEl.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); closePop(); }
    });
    // 点面板外面收起。挂在 document 上，视图被换掉后这个闭包里的元素
    // 已脱离文档（contains 恒 false），不会误伤别的页面。
    document.addEventListener('click', (e) => {
      if (!open) return;
      if (pop.contains(e.target) || e.target === btn || btn.contains(e.target)) return;
      closePop();
    });

    label();
    load();
    return { load, label };
  }

  // ═══ 1. 对话 ═══════════════════════════════════════════════
  let chatTimer = null;

  async function renderChat(ctx) {
    const view = ctx.view, panel = ctx.panel, topbar = ctx.topbar;
    // 聊天页不用 .scroll（那是给卡片列表用的），换成撑满整块的 .chatwrap
    view.className = 'chatwrap';
    topbar.className = 'topbar chat-topbar';
    // Render the local shell first. Stats and history continue asynchronously
    // so an unavailable Brain cannot block the first usable frame.
    let st = { ok: false, model: '—', tok_in: 0, tok_out: 0, turns: 0, avg_ms: 0, mem_hit_turns: 0, agenda: {} };
    const statsPromise = get('/api/stats', { timeoutMs: 3000 }).then(pick).catch(() => st);
    const model = (typeof st.model === 'object' && st.model ? st.model.id || st.model.name : st.model) || '—';
    topbar.innerHTML =
      '<button class="ibtn" id="sessionsBtn" title="折叠或展开会话列表" aria-label="会话列表" aria-controls="panel">' + icon('sidebar', 'sm') + '</button>' +
      '<span class="chat-title">工作台</span><span class="chat-model" id="chatModel" title="' + esc(model) + '">' + esc(model) + '</span>' +
      '<div class="sp"></div><span class="chat-connection"><span class="led ' + (state.conn.ok ? 'on' : '') + '"></span>' + (API.runtime.preview ? '浏览器预览 · 未连接 Agent' : state.conn.ok ? 'Agent 已连接' : 'Agent 未连接') + '</span>' +
      '<div class="tr">' +
      '<button class="btn" id="rpBtn" aria-expanded="false" aria-controls="rp">上下文</button>' +
      '<button class="ibtn" id="clearBtn" title="清空本机（pc）会话历史">' + icon('dots', 'sm') + '</button>' +
      '</div>';

    // 会话由 Brain 持久化。localStorage 保留作旧版本兼容，但不会用于冷启动自动选中。
    panel.innerHTML =
      '<div class="phead"><h2>会话</h2><button class="ibtn" id="newTopicBtn" title="新建空白会话" aria-label="新建会话">' + icon('plus', 'sm') + '</button></div>' +
      '<div class="plist" id="sessList"><div class="empty">还没有聊过</div></div>';
    const sessList = panel.querySelector('#sessList');

    const SESS_KEY = 'vigil.chat.active.v2:' + String(state.base || '') + ':' + String((state.cfg || {}).device_id || '');
    const RENDERER_SESSION_KEY = 'vigil.chat.renderer-session.v1';
    let sessionItems = [];
    let activeConversationId = '';
    state.chatConversationId = '';
    let appInstanceId = '';
    let createConversationPromise = null;
    let rendererSelection = null;
    function rememberRendererSelection(id, mode) {
      rendererSelection = { app_instance: appInstanceId, conversation_id: id || '', mode: mode || (id ? 'conversation' : 'welcome') };
      try { localStorage.setItem(RENDERER_SESSION_KEY, JSON.stringify(rendererSelection)); } catch (_) {}
    }
    async function refreshConversations() {
      if (API.runtime.preview) {
        const id = '00000000-0000-4000-8000-000000000001';
        sessionItems = [{id,title:'预览对话',created_at:Date.now()/1000,updated_at:Date.now()/1000,legacy:false}];
        if (activeConversationId && activeConversationId !== id) activeConversationId = '';
        state.chatConversationId = activeConversationId; drawSessions(); return activeConversationId;
      }
      const r = pick(await get('/api/conversations?src=pc'));
      if (!r.ok || !Array.isArray(r.items)) throw new Error(r._err || '会话列表加载失败');
      sessionItems = r.items;
      if (activeConversationId && !sessionItems.some((x) => x.id === activeConversationId)) activeConversationId = '';
      state.chatConversationId = activeConversationId;
      drawSessions();
      return activeConversationId;
    }
    const tsOf = (t) => {
      const n = typeof t === 'number' ? (t < 1e12 ? t * 1000 : t) : Date.parse(String(t).replace(/-/g, '/'));
      return isNaN(n) ? Date.now() : n;
    };
    const relative = (ms) => {
      const m = Math.floor((Date.now() - ms) / 60000);
      if (m < 1) return '刚刚';
      if (m < 60) return m + ' 分钟前';
      const h = Math.floor(m / 60);
      if (h < 24) return h + ' 小时前';
      const d = Math.floor(h / 24);
      if (d < 30) return d + ' 天前';
      return fmtTime(ms, 'day');
    };

    function drawSessions() {
      if (!sessionItems.length) {
        sessList.innerHTML = '<div class="empty" style="text-align:left;padding:18px 12px">还没有会话</div>';
        return;
      }
      sessList.innerHTML = sessionItems.map((s) => {
        const updated = Number(s.updated_at || s.created_at || 0) * 1000;
        return '<div class="li session-row' + (s.id === activeConversationId ? ' on' : '') + '">' +
          '<button class="session-open" data-session="' + esc(s.id) + '" title="切换会话">' +
          '<span class="ic">' + icon('chat', 'sm') + '</span><span class="tx"><b>' + esc(String(s.title || '').slice(0, 7)) + '</b>' +
          '<span>' + esc(relative(updated)) + '</span></span></button>' +
          '<button class="ibtn session-rename" data-rename="' + esc(s.id) + '" aria-label="重命名会话" title="重命名">···</button></div>';
      }).join('');
      sessList.querySelectorAll('[data-session]').forEach((n) => n.addEventListener('click', () => activateConversation(n.dataset.session)));
      sessList.querySelectorAll('[data-rename]').forEach((n) => n.addEventListener('click', () => renameConversation(n.dataset.rename)));
    }
    async function activateConversation(id, options = {}) {
      if (!sessionItems.some((s) => s.id === id) || id === activeConversationId) return;
      activeConversationId = id;
      state.chatConversationId = id;
      if (options.persist !== false) {
        try { localStorage.setItem(SESS_KEY, id); } catch (_) {}
        rememberRendererSelection(id, 'conversation');
      }
      lastCount = -1;
      conversationLoadSeq++;
      renderedDraftConversationId = id;
      // Detach the previous transcript and any live task card immediately.
      // Its persistent request continues in Brain and is restored when this
      // conversation is selected again.
      msgs.innerHTML = '';
      heroEl.hidden = false;
      heroEl.innerHTML = heroHtml();
      chat.classList.add('is-empty');
      view.querySelector('#chatLoadState').hidden = true;
      busy = false;
      busyScope = null;
      taEl.value = '';
      attachment = null;
      imgB64 = '';
      attachBox.hidden = true;
      attachBox.innerHTML = '';
      updateComposer();
      drawSessions();
      await loadMsgs(true);
    }
    async function renameConversation(id) {
      const item = sessionItems.find((s) => s.id === id);
      if (!item) return;
      const row = sessList.querySelector('[data-rename="' + CSS.escape(id) + '"]')?.closest('.session-row');
      if (!row) return;
      row.innerHTML = '<input class="input session-title-edit" maxlength="80" value="' + esc(item.title) + '"><button class="ibtn" aria-label="保存标题">✓</button>';
      const input = row.querySelector('input'); input.focus(); input.select();
      const save = async () => {
        const title = input.value.trim(); if (!title) { drawSessions(); return; }
        const r = pick(await post('/api/conversations/' + encodeURIComponent(id), {src:'pc', title}));
        if (!r.ok) { toast(r._err || '标题保存失败'); drawSessions(); return; }
        sessionItems = sessionItems.map((s) => s.id === id ? r.item : s); drawSessions();
      };
      row.querySelector('button').onclick = save;
      input.onkeydown = (e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') drawSessions(); };
    }
    panel.querySelector('#newTopicBtn').addEventListener('click', () => showWelcomeHome(true));

    view.innerHTML =
      '<div class="split no-right">' +
      '  <div class="chat">' +
      '    <div class="hero" id="hero" hidden></div>' +
      '    <div class="chat-load-state" id="chatLoadState" role="status">正在加载会话…</div>' +
      '    <div class="msgs" id="msgs" role="log" aria-label="聊天记录"></div>' +
      '    <div class="composer">' +
      '      <div class="localpop modelpop" id="modelPop" hidden>' +
      '        <div class="lph"><span>选择模型</span>' +
      '          <button class="ibtn" id="mqSearchToggle" aria-label="搜索模型" title="搜索模型">' + icon('search', 'sm') + '</button>' +
      '          <button class="ibtn" id="mqClose" aria-label="收起模型列表" title="收起">' + icon('x', 'sm') + '</button></div>' +
      '        <div class="lpbody">' +
      '          <input class="input" id="mqSearch" placeholder="搜索模型或服务商…" aria-label="搜索模型" hidden>' +
      '          <div id="mqList"></div>' +
      '        </div>' +
      '        <div class="lpadd mqfoot">' +
      '          <button class="btn ghost md" id="mqManage" type="button">添加或管理模型</button>' +
      '          <span class="muted" id="mqMsg"></span>' +
      '        </div>' +
      '      </div>' +
      '      <div class="localpop" id="localPop" hidden></div>' +
      '      <div class="col">' +
      '      <div class="attach" id="attach" hidden></div>' +
      '      <div class="cbox"><textarea id="ta" rows="2" aria-label="消息内容" placeholder="给 Vigil 发送消息…"></textarea>' +
      '        <div class="creatorbar" id="creatorBar" hidden>' +
      '          <span class="muted" style="font-size:12px">创作模式：直接描述你要的插件/技能，Agent 生成草稿（只保存与审核，不自动安装）</span>' +
      '          <div class="sp"></div>' +
      '          <button class="chip" data-kind="plugin">插件</button>' +
      '          <button class="chip" data-kind="skill">技能</button>' +
      '        </div>' +
      '        <div class="cbar">' +
      '          <button class="ibtn" id="localBtn" title="本地操控：权限模式 · 白名单目录">' + icon('shield', 'sm') + '</button>' +
      '          <button class="ibtn" id="clipBtn" title="添加图片、视频或普通文件">' + icon('clip', 'sm') + '</button>' +
      '          <button class="chip" id="creatorBtn" title="创作模式：用自然语言让 Agent 生成插件/技能草稿">创作：关</button>' +
      '          <button class="btn composer-model" id="modelBtn" title="打开模型列表，点一条即切换">' + esc(model) + ' ▾</button>' +
      '          <div class="sp"></div>' +
      '          <span class="mono hintline">Enter 发送 · Shift+Enter 换行</span>' +
      '          <button class="btn fill" id="sendBtn" disabled aria-label="发送消息" title="发送消息">' + icon('arrow-up', 'sm') + '</button>' +
      '        </div></div>' +
      '    </div><div class="composer-caption"><span>Vigil · AI Agent Workspace</span><span class="ccount" id="ccount">0 字</span></div></div>' +
      '  </div>' +
      '  <aside class="rp no" id="rp" aria-label="上下文与统计"></aside>' +
      '</div>';

    const msgs = view.querySelector('#msgs');
    const heroEl = view.querySelector('#hero');
    const rp = view.querySelector('#rp');
    const chat = view.querySelector('.chat');
    const sessionsBtn = topbar.querySelector('#sessionsBtn');
    let sessionsCollapsed = false;
    let narrowSessionsOpen = false;
    try { sessionsCollapsed = localStorage.getItem('vigil.chat.sessionsCollapsed') === 'true'; } catch (_) {}
    function applySessions() {
      const collapsed = window.matchMedia('(max-width:860px)').matches ? !narrowSessionsOpen : sessionsCollapsed;
      document.body.classList.toggle('chat-sessions-collapsed', collapsed);
      sessionsBtn.setAttribute('aria-expanded', String(!collapsed));
    }
    applySessions();
    sessionsBtn.addEventListener('click', () => {
      if (window.matchMedia('(max-width:860px)').matches) narrowSessionsOpen = !narrowSessionsOpen;
      else {
        sessionsCollapsed = !sessionsCollapsed;
        try { localStorage.setItem('vigil.chat.sessionsCollapsed', String(sessionsCollapsed)); } catch (_) {}
      }
      applySessions();
    });
    // 模型快速切换：点左上角那个模型名弹出已保存的模型清单，点一条即切。
    // 数据来自 /api/models/saved（不带密钥），切换走 /api/models/quick-switch。
    // 生成过程中禁用（busy）：不让"正在跑的这轮"和"刚选的模型"对不上。
    const mq = modelQuickBind(view, ctx, {
      isBusy: () => busy,
      onSwitched: (c) => { if (c && c.model) toast('已切到 ' + c.model); },
    });
    // 创作模式开关：chip 循环 关 → 插件 → 技能 → 关
    // 创作模式 bar 里的类型直选（不用循环也能一步切到目标类型）
    // 🔴 监听注册可以提前，但 applyCreatorMode() 的立即调用必须晚于
    // let creatorMode 声明（TDZ：提前调会 "Cannot access before initialization"，
    // 整页画不出来 —— 2026-10-12 实测踩过）
    view.querySelector('#creatorBtn').addEventListener('click', () => {
      if (busy) return;
      creatorMode = creatorMode === 'off' ? 'plugin' : creatorMode === 'plugin' ? 'skill' : 'off';
      applyCreatorMode();
      toast(creatorMode === 'off' ? '已回到普通对话'
        : '创作模式：生成' + (creatorMode === 'skill' ? '技能' : '插件') + '草稿，发消息即生成');
    });
    view.querySelector('#creatorBar').querySelectorAll('.chip[data-kind]').forEach((c) =>
      c.addEventListener('click', () => {
        creatorMode = c.dataset.kind;
        applyCreatorMode();
        toast('创作模式：生成' + (creatorMode === 'skill' ? '技能' : '插件') + '草稿');
      }));
    // Delegate copy actions so history reloads and completed streams share one binding.
    msgs.addEventListener('click', async (e) => {
      const button = e.target.closest('[data-copy-code]');
      if (!button) return;
      try {
        await navigator.clipboard.writeText(button.closest('.codeblock').querySelector('code').textContent);
        button.textContent = '已复制';
        setTimeout(() => { if (button.isConnected) button.textContent = '复制'; }, 1500);
      } catch (_) { toast('复制失败，请选中代码手动复制'); }
    });

    const cIn = Number(st.tok_in) || 0, cOut = Number(st.tok_out) || 0;
    rp.innerHTML =
      '<div class="card" style="margin-bottom:10px"><div class="gtitle" style="padding:0 0 6px">本轮上下文</div>' +
      '<div class="kv"><span>当前人设</span><b data-chat-stat="name">' + esc(st.name || '—') + '</b></div>' +
      '<div class="kv"><span>模型</span><b data-chat-stat="model">' + esc(model) + '</b></div>' +
      '<div class="kv"><span>今日轮次</span><b data-chat-stat="turns">' + esc(fmtNum(st.turns == null ? '—' : st.turns)) + '</b></div>' +
      '<div class="kv"><span>Token 合计（今日）</span><b data-chat-stat="total">' + esc(fmtNum(cIn + cOut)) + '</b></div>' +
      '<div class="kv sub"><span>├ 入</span><b data-chat-stat="in">' + esc(fmtNum(cIn)) + '</b></div>' +
      '<div class="kv sub"><span>└ 出</span><b data-chat-stat="out">' + esc(fmtNum(cOut)) + '</b></div>' +
      '<div class="kv"><span>平均延迟</span><b data-chat-stat="latency">' + esc(st.avg_ms ? (st.avg_ms / 1000).toFixed(1) + 's' : '—') + '</b></div>' +
      '<div class="kv"><span>记忆命中</span><b data-chat-stat="memory">' + esc((st.mem_hit_turns || 0) + ' / ' + (st.turns || 0)) + '</b></div>' +
      '</div>' +
      '<div class="card" style="margin-bottom:10px"><div class="gtitle" style="padding:0 0 6px">Token 趋势 · 最近 60 轮</div>' +
      '<div class="sparkbox" id="tokSpark"><span class="muted" style="font-size:11.5px">…</span></div>' +
      '<div class="kv sub" id="tokSparkSum" style="display:none"></div>' +
      '</div>' +
      '<div class="card" style="margin-bottom:10px"><div class="gtitle" style="padding:0 0 6px">约定账本</div>' +
       '<div class="kv"><span>共</span><b data-chat-stat="agenda-total">' + esc((st.agenda && st.agenda.total) || 0) + ' 条</b></div>' +
       '<div class="kv"><span>待办</span><b data-chat-stat="agenda-pending">' + esc((st.agenda && st.agenda.pending) || 0) + '</b></div>' +
       '<div class="kv"><span>错过</span><b data-chat-stat="agenda-missed">' + esc((st.agenda && st.agenda.missed) || 0) + '</b></div>' +
      '</div>' +
      (st.last_err ? '<div class="card"><div class="gtitle" style="padding:0 0 6px">最近错误</div>' +
        '<div class="mono" style="font-size:11.5px;color:var(--err)">' + esc(String(st.last_err).slice(0, 120)) + '</div></div>' : '');

    void statsPromise.then((fresh) => {
      if (!fresh || !fresh.ok || state.page !== 'chat') return;
      st = fresh;
      const m = (typeof st.model === 'object' && st.model ? st.model.id || st.model.name : st.model) || '—';
      const modelEl = topbar.querySelector('#chatModel');
      if (modelEl) { modelEl.textContent = m; modelEl.title = m; }
      const put = (key, value) => { const el = rp.querySelector('[data-chat-stat="' + key + '"]'); if (el) el.textContent = value; };
      const inTok = Number(st.tok_in) || 0, outTok = Number(st.tok_out) || 0;
      put('name', st.name || '—'); put('model', m); put('turns', fmtNum(st.turns == null ? '—' : st.turns));
      put('total', fmtNum(inTok + outTok)); put('in', fmtNum(inTok)); put('out', fmtNum(outTok));
      put('latency', st.avg_ms ? (st.avg_ms / 1000).toFixed(1) + 's' : '—');
      put('memory', (st.mem_hit_turns || 0) + ' / ' + (st.turns || 0));
      put('agenda-total', ((st.agenda && st.agenda.total) || 0) + ' 条');
      put('agenda-pending', (st.agenda && st.agenda.pending) || 0); put('agenda-missed', (st.agenda && st.agenda.missed) || 0);
    });

    // 右栏「我的草稿」：创作模式生成的所有草稿，点一条就回到对话流里
    // 继续看/改。没有草稿时整卡隐藏，不占地方。
    const rpDrafts = U.el('<div class="card" id="rpDrafts" style="margin-bottom:10px" hidden>'
      + '<div class="gtitle" style="padding:0 0 6px">我的草稿</div></div>');
    rp.appendChild(rpDrafts);
    async function loadRpDrafts() {
      if (!rpDrafts.isConnected) return;
      const r = pick(await API.get('/api/creator/drafts'));
      const ds = (r && r.ok && r.drafts) || [];
      if (!ds.length) { rpDrafts.hidden = true; return; }
      rpDrafts.hidden = false;
      rpDrafts.innerHTML = '<div class="gtitle" style="padding:0 0 6px">我的草稿 '
        + ds.length + '</div>' + ds.map((d) =>
        '<button class="li" data-did="' + esc(d.id) + '" style="width:100%;text-align:left;background:none;border:none;cursor:pointer">' +
          '<span class="ic">' + icon(d.kind === 'skill' ? 'puzzle' : 'plug', 'sm') + '</span>' +
          '<div class="tx"><b>' + esc((d.summary || d.requirement || '未命名草稿').slice(0, 30)) + '</b>' +
          '<span>' + esc(d.kind === 'skill' ? '技能' : '插件') + ' · v' + esc(String(d.version)) +
          (d.valid ? ' · <span style="color:var(--ok)">验证通过</span>'
            : ' · <span style="color:var(--err)">' + esc(String(d.error_count)) + ' 个问题</span>') +
          '</span></div></button>').join('');
      rpDrafts.querySelectorAll('[data-did]').forEach((b) =>
        b.addEventListener('click', () => openDraftInChat(b.dataset.did)));
    }
    loadRpDrafts();

    // 迷你趋势线：异步拉，拉不到就算了（不挡消息区、不打扰聊天）
    get('/api/trace?last=60').then((r) => {
      const box = view.querySelector('#tokSpark');
      if (!box) return;
      const items = (pick(r).items || [])
        .filter((x) => x && x.t != null && (x.tok_in != null || x.tok_out != null))
        .sort((a, b) => toMs(a.t) - toMs(b.t));
      if (items.length < 2) { box.innerHTML = '<span class="muted" style="font-size:11.5px">轮次还太少，画不出趋势</span>'; return; }
      let sum = 0;
      const pts = items.map((x) => { const v = (Number(x.tok_in) || 0) + (Number(x.tok_out) || 0); sum += v; return { x: toMs(x.t), y: v }; });
      U.sparkline(box, pts);
      const sumRow = view.querySelector('#tokSparkSum');
      if (sumRow) { sumRow.style.display = ''; sumRow.innerHTML = '<span>窗口合计</span><b>' + esc(fmtNum(sum)) + '</b>'; }
    }).catch(() => { /* 离线时留占位文案，不报错 */ });

    let lastCount = -1;
    let busy = false;
    let busyScope = null;
    let conversationLoadSeq = 0;
    let renderedDraftConversationId = '';
    function finishBusy(scope) {
      if (busyScope !== scope) return;
      busy = false;
      busyScope = null;
      updateComposer();
    }
    function showWelcomeHome(persist = false) {
      activeConversationId = '';
      state.chatConversationId = '';
      lastCount = -1;
      conversationLoadSeq++;
      renderedDraftConversationId = '';
      msgs.innerHTML = '';
      heroEl.hidden = false;
      heroEl.innerHTML = heroHtml();
      heroEl.querySelectorAll('[data-hint]').forEach((button) => button.addEventListener('click', () => {
        const input = view.querySelector('#ta');
        if (input) { input.value = button.dataset.hint; input.dispatchEvent(new Event('input')); input.focus(); }
      }));
      chat.classList.add('is-empty');
      view.querySelector('#chatLoadState').hidden = true;
      busy = false;
      busyScope = null;
      if (typeof taEl !== 'undefined') taEl.value = '';
      if (typeof clearAttachment === 'function') clearAttachment();
      if (persist) rememberRendererSelection('', 'welcome');
      drawSessions();
    }
    async function ensureConversation() {
      if (activeConversationId) return activeConversationId;
      if (createConversationPromise) return createConversationPromise;
      createConversationPromise = (async () => {
        if (API.runtime.preview) {
          const item = sessionItems[0];
          if (item) activeConversationId = item.id;
        } else {
          const result = pick(await post('/api/conversations', {src:'pc'}));
          if (!result.ok || !result.item || !result.item.id) return '';
          const existing = sessionItems.findIndex((item) => item.id === result.item.id);
          if (existing >= 0) sessionItems.splice(existing, 1);
          sessionItems.unshift(result.item);
          activeConversationId = result.item.id;
        }
        if (!activeConversationId) return '';
        state.chatConversationId = activeConversationId;
        try { localStorage.setItem(SESS_KEY, activeConversationId); } catch (_) {}
        rememberRendererSelection(activeConversationId, 'conversation');
        drawSessions();
        return activeConversationId;
      })().catch((error) => { toast('新建会话失败：' + String(error.message || error)); return ''; })
        .finally(() => { createConversationPromise = null; });
      return createConversationPromise;
    }
    async function initializeConversationView() {
      try {
        appInstanceId = global.vigil && global.vigil.appInstanceId
          ? String(await global.vigil.appInstanceId() || '') : 'browser-session';
      } catch (_) { appInstanceId = 'browser-session'; }
      try {
        rendererSelection = JSON.parse(localStorage.getItem(RENDERER_SESSION_KEY) || 'null');
      } catch (_) { rendererSelection = null; }
      const restoreId = rendererSelection && rendererSelection.app_instance === appInstanceId
        && rendererSelection.mode === 'conversation' ? rendererSelection.conversation_id : '';
      if (!await loadRecordScope()) { showWelcomeHome(false); return; }
      try { await refreshConversations(); }
      catch (error) {
        showWelcomeHome(false);
        sessList.innerHTML = '<div class="empty" style="text-align:left;padding:18px 12px">会话列表暂不可用</div>';
        return;
      }
      if (!msgs.isConnected) return;
      if (restoreId && sessionItems.some((item) => item.id === restoreId)) {
        await activateConversation(restoreId, {persist:false});
      } else {
        showWelcomeHome(false);
      }
    }
    // ── 创作模式（Creator Mode MVP-1）─────────────────────────
    // 主页对话里的一个模式：切到「创作」后，发给 Agent 的自然语言不再进
    // /api/chat，而是进 /api/creator/draft 生成草稿卡。草稿只存独立工作区、
    // 不安装、不执行 —— 这一层边界在服务端 creator.py 钉死，UI 只负责展示。
    let creatorMode = 'off';
    try { creatorMode = localStorage.getItem('vigil.chat.creatorMode') || 'off'; } catch (_) {}
    if (['off', 'plugin', 'skill'].indexOf(creatorMode) < 0) creatorMode = 'off';
    function applyCreatorMode() {
      const btn = view.querySelector('#creatorBtn');
      const bar = view.querySelector('#creatorBar');
      const ta = view.querySelector('#ta');
      if (!btn || !bar || !ta) return;
      const label = creatorMode === 'off' ? '关' : creatorMode === 'plugin' ? '插件' : '技能';
      btn.textContent = '创作：' + label;
      btn.classList.toggle('on', creatorMode !== 'off');
      bar.hidden = creatorMode === 'off';
      bar.querySelectorAll('.chip[data-kind]').forEach((c) =>
        c.classList.toggle('on', c.dataset.kind === creatorMode));
      ta.placeholder = creatorMode === 'off'
        ? '给 Vigil 发送消息…'
        : '描述你想要的' + (creatorMode === 'skill' ? '技能' : '插件')
          + '。例如：一个每小时整点提醒我喝水的插件；一个把一段文字反转后返回的技能。';
      try { localStorage.setItem('vigil.chat.creatorMode', creatorMode); } catch (_) {}
    }
    // 立即调用必须在这里：let creatorMode 已声明（TDZ 已过）
    applyCreatorMode();
    // 附件：文件只在发送时分块上传；聊天请求只携带服务端返回的附件元数据。
    let imgB64 = '';
    let attachment = null;
    const ATTACH_MAX = 256 * 1024 * 1024;
    const ATTACH_IMAGE_MAX = 8 * 1024 * 1024;
    const ATTACH_ACCEPT = 'image/jpeg,image/png,image/gif,image/webp,video/mp4,video/quicktime,video/webm,.pdf,.txt,.md,.csv,.json,.py,.pyw,.js,.mjs,.cjs,.jsx,.ts,.tsx,.html,.htm,.css,.scss,.xml,.yaml,.yml,.toml,.ini,.conf,.sql,.sh,.bash,.zsh,.bat,.cmd,.ps1,.c,.h,.cc,.cpp,.cxx,.hpp,.java,.go,.rs,.rb,.php,.vue,.svelte,.ipynb';
    const ATTACH_EXT = /\.(jpe?g|png|gif|webp|mp4|mov|webm|pdf|txt|md|csv|json|pyw?|mjs|cjs|jsx|tsx?|html?|css|scss|xml|ya?ml|toml|ini|conf|sql|sh|bash|zsh|bat|cmd|ps1|c|h|cc|cpp|cxx|hpp|java|go|rs|rb|php|vue|svelte|ipynb)$/i;

    function clearAttachment() {
      if (attachment && attachment.previewUrl && global.URL) {
        try { URL.revokeObjectURL(attachment.previewUrl); } catch (_) {}
      }
      attachment = null;
      imgB64 = '';
      attachBox.hidden = true;
      attachBox.innerHTML = '';
      updateComposer();
    }

    function renderAttachment() {
      if (!attachment) { attachBox.hidden = true; attachBox.innerHTML = ''; return; }
      attachBox.hidden = false;
      const f = attachment.file;
      const preview = attachment.preview || attachment.previewUrl || '';
      let media = '<span class="afile-icon">' + icon(attachment.kind === 'image' ? 'img' : 'clip', 'sm') + '</span>';
      if (attachment.kind === 'image' && preview) media = '<img class="athumb" src="' + esc(preview) + '" alt="">';
      if (attachment.kind === 'video' && preview) media = '<video class="avideo" controls preload="metadata" src="' + esc(preview) + '"></video>';
      const pct = Math.max(0, Math.min(100, Number(attachment.progress || 0)));
      let target = '当前 Brain';
      try {
        const host = new URL(state.base || (state.cfg || {}).base || '').hostname;
        if (host) target = ['127.0.0.1','localhost','::1'].includes(host) ? '本机 Brain' : '远端 Brain · ' + host;
      } catch (_) {}
      const videoOptions = attachment.kind === 'video' && attachment.videoUnsupported
        ? '<div class="video-limit">当前模型不支持直接理解完整视频<div class="video-choice">' +
          '<button type="button" class="chip' + (attachment.videoChoice === 'frames' ? ' on' : '') + '" data-video-choice="frames">提取关键帧</button>' +
          '<button type="button" class="chip' + (attachment.videoChoice === 'original' ? ' on' : '') + '" data-video-choice="original">仅发送原视频</button></div></div>' : '';
      attachBox.innerHTML = '<div class="attachment-row">' + media +
        '<div class="attachment-info"><b class="aname" title="' + esc(f.name) + '">' + esc(f.name) + '</b>' +
        '<span class="muted">' + esc(bytes(f.size)) + ' · ' + esc(f.type || '未知类型') + '</span>' +
        '<progress class="aupload" max="100" value="' + pct + '"></progress>' +
        '<span class="muted attachment-status' + (attachment.failed ? ' failed' : '') + '" role="status">' + esc(attachment.status || '待发送') + '</span></div>' +
        '<button class="ibtn" id="atClr" title="移除附件" aria-label="移除附件">' + icon('x', 'sm') + '</button></div>' +
        videoOptions + '<div class="attachment-target">发送目标：' + esc(target) +
        (target.startsWith('远端') ? ' · 文件将上传到该服务端，请确认这是预期目标。' : '') + '</div>';
      attachBox.querySelector('#atClr').addEventListener('click', clearAttachment);
      attachBox.querySelectorAll('[data-video-choice]').forEach((b) => b.addEventListener('click', () => {
        attachment.videoChoice = b.dataset.videoChoice; renderAttachment();
      }));
    }

    function chooseFile(file) {
      if (!file) return;
      clearAttachment();
      const kind = /^video\//i.test(file.type) || /\.(mp4|mov|webm)$/i.test(file.name)
        ? 'video' : /^image\//i.test(file.type) || /\.(jpe?g|png|gif|webp)$/i.test(file.name)
          ? 'image' : 'file';
      attachment = { file, kind, progress: 0, status: '待发送', previewUrl: '' };
      const selected = attachment;
      let validationError = '';
      if (file.size <= 0) validationError = '附件不能为空';
      else if (file.size > ATTACH_MAX) validationError = '附件超过 256 MB 上限';
      else if (kind === 'image' && file.size > ATTACH_IMAGE_MAX) validationError = '图片超过 8 MB 聊天上传上限';
      else if (!ATTACH_EXT.test(file.name)) validationError = '不支持此类型；请选择图片、视频、PDF、TXT 或常见代码文件';
      if (validationError) {
        attachment.failed = true;
        attachment.status = '上传失败：' + validationError;
        renderAttachment();
        updateComposer();
        return;
      }
      if (kind === 'image') {
        const fr = new FileReader();
        fr.onload = () => {
          if (attachment !== selected) return;
          attachment.preview = String(fr.result || '');
          imgB64 = attachment.preview.split(',', 2)[1] || '';
          if (!imgB64) {
            attachment.failed = true;
            attachment.status = '读取失败：无法生成图片数据';
            toast('图片读取失败，请移除后重试');
          } else attachment.status = '图片已就绪';
          renderAttachment();
          updateComposer();
        };
        fr.onerror = () => {
          if (attachment !== selected) return;
          attachment.failed = true;
          attachment.status = '读取失败：图片无法读取';
          renderAttachment();
          updateComposer();
          toast('图片读取失败，请移除后重试');
        };
        fr.readAsDataURL(file);
      } else {
        attachment.previewUrl = URL.createObjectURL(file);
        if (kind === 'video') {
          attachment.status = '检查当前模型视频能力…';
          attachment.capabilityCheck = get('/api/attachments/capabilities').then((raw) => {
            if (attachment !== selected) return;
            const cap = pick(raw);
            attachment.videoCapabilities = cap || {};
            attachment.videoUnsupported = !cap || cap.video !== true;
            attachment.videoChoice = 'frames';
            attachment.status = '待发送'; renderAttachment();
          }).catch(() => {
            if (attachment !== selected) return;
            attachment.videoUnsupported = true; attachment.videoChoice = 'frames';
            attachment.status = '能力检查失败；发送前可选择处理方式'; renderAttachment();
          });
        }
        renderAttachment();
      }
      updateComposer();
    }

    async function uploadAttachment(att, silent) {
      if (!global.vigil || !global.vigil.attachments || !global.vigil.attachments.chunk) {
        throw new Error('当前客户端不支持附件上传，请更新客户端');
      }
      const id = 'att_' + (global.crypto && global.crypto.randomUUID ? global.crypto.randomUUID().replace(/-/g, '') : (Date.now() + '_' + Math.random().toString(36).slice(2)));
      const chunkSize = 1024 * 1024;
      for (let offset = 0; offset < att.file.size; offset += chunkSize) {
        const end = Math.min(att.file.size, offset + chunkSize);
        const buf = await att.file.slice(offset, end).arrayBuffer();
        const r = await global.vigil.attachments.chunk(buf, {
          'X-Attachment-Id': id,
          'X-Attachment-Name': encodeURIComponent(att.file.name),
          'X-Attachment-Mime': att.file.type || 'application/octet-stream',
          'X-Attachment-Total': String(att.file.size),
          'X-Attachment-Offset': String(offset),
          'X-Attachment-Confirmed': '1',
        });
        if (!r || !r.ok) throw new Error((r && (r.err || (r.json && r.json.err))) || '附件上传失败');
        att.progress = Math.round((end / att.file.size) * 100);
        att.status = att.progress >= 100 ? '已上传' : '上传中 ' + att.progress + '%';
        if (!silent) renderAttachment();
      }
      return { id, name: att.file.name, mime: att.file.type || 'application/octet-stream', size: att.file.size, kind: att.kind };
    }

    async function extractVideoFrames(file, count) {
      const url = URL.createObjectURL(file);
      const video = document.createElement('video');
      video.muted = true;
      video.playsInline = true;
      video.preload = 'auto';
      const waitFor = (eventName, timeoutMs, errorText) => new Promise((resolve, reject) => {
        let done = false;
        const finish = (fn, value) => {
          if (done) return;
          done = true;
          clearTimeout(timer);
          video.removeEventListener(eventName, onEvent);
          video.removeEventListener('error', onError);
          fn(value);
        };
        const onEvent = () => finish(resolve);
        const onError = () => finish(reject, new Error(errorText));
        const timer = setTimeout(() => finish(reject, new Error(errorText)), timeoutMs);
        video.addEventListener(eventName, onEvent, { once: true });
        video.addEventListener('error', onError, { once: true });
      });
      try {
        video.src = url;
        video.load();
        await waitFor('loadedmetadata', 15000, '无法读取视频元数据；原视频仍可上传');
        const duration = Number(video.duration) || 0;
        if (!duration || !isFinite(duration)) throw new Error('视频时长不可用；原视频仍可上传');
        const canvas = document.createElement('canvas');
        const scale = Math.min(1, 768 / Math.max(video.videoWidth || 1, video.videoHeight || 1));
        canvas.width = Math.max(1, Math.round((video.videoWidth || 768) * scale));
        canvas.height = Math.max(1, Math.round((video.videoHeight || 432) * scale));
        const ctx = canvas.getContext('2d');
        if (!ctx) throw new Error('当前环境无法创建关键帧画布；原视频仍可上传');
        const total = Math.min(count || 3, 3);
        const times = Array.from({ length: total }, (_, i) =>
          Math.min(Math.max(0.1, duration * ((i + 1) / (total + 1))), Math.max(0.1, duration - 0.1)));
        const files = [];
        for (let i = 0; i < times.length; i++) {
          const target = times[i];
          if (Math.abs(Number(video.currentTime || 0) - target) > 0.01 || video.readyState < 2) {
            await new Promise((resolve, reject) => {
              let done = false;
              const finish = (fn, value) => {
                if (done) return;
                done = true;
                clearTimeout(timer);
                video.removeEventListener('seeked', onSeeked);
                video.removeEventListener('error', onError);
                fn(value);
              };
              const onSeeked = () => finish(resolve);
              const onError = () => finish(reject, new Error('关键帧定位失败；原视频仍可上传'));
              const timer = setTimeout(() => finish(reject, new Error('关键帧定位超时；原视频仍可上传')), 15000);
              video.addEventListener('seeked', onSeeked, { once: true });
              video.addEventListener('error', onError, { once: true });
              video.currentTime = target;
            });
          }
          // 必须先把当前视频帧绘制到画布，再编码；否则 toBlob 可能得到空白帧。
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
          const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.82));
          if (!blob || blob.size <= 0) throw new Error('关键帧编码为空；原视频仍可上传');
          const stem = file.name.replace(/\.[^.]+$/, '').slice(0, 80);
          files.push(new File([blob], stem + '.frame-' + (i + 1) + '.jpg', { type: 'image/jpeg' }));
        }
        return files;
      } finally {
        try { video.pause(); video.removeAttribute('src'); video.load(); } catch (_) {}
        URL.revokeObjectURL(url);
      }
    }

    async function extractVideoFramesViaBrain(id, count) {
      const r = pick(await post('/api/attachments/frames', { id, count: Math.min(count || 3, 3) }));
      if (!r || !r.ok) throw new Error((r && (r.err || r._err)) || '本机 FFmpeg 无法解析视频');
      return Array.isArray(r.attachments) ? r.attachments : [];
    }

    function updateComposer() {
      const ta = view.querySelector('#ta');
      const maxHeight = parseFloat(getComputedStyle(ta).maxHeight) || 180;
      ta.style.height = 'auto';
      ta.style.height = Math.min(maxHeight, Math.max(56, ta.scrollHeight)) + 'px';
      ta.style.overflowY = ta.scrollHeight > maxHeight ? 'auto' : 'hidden';
      view.querySelector('#ccount').textContent = ta.value.length + ' 字';
      const button = view.querySelector('#sendBtn');
      button.disabled = !global.vigil || busy || (!ta.value.trim() && !imgB64 && !attachment) ||
        !!(attachment && (attachment.failed || (attachment.kind === 'image' && !imgB64)));
      button.classList.toggle('is-sending', busy);
      button.setAttribute('aria-label', busy ? '正在发送' : '发送消息');
      button.setAttribute('aria-busy', String(busy));
      button.title = busy ? '正在发送…' : '发送消息';
      // 生成中把模型按钮一起禁掉：不让"正在跑的这轮"和"刚选的模型"对不上。
      // 防一手 null —— updateComposer 在发送主链路上，这里抛错会连累整个 composer。
      const mb = view.querySelector('#modelBtn');
      if (mb) mb.disabled = busy;
    }
    const attachBox = view.querySelector('#attach');
    view.querySelector('#clipBtn').addEventListener('click', () => {
      const inp = document.createElement('input');
      inp.type = 'file';
      inp.accept = ATTACH_ACCEPT;
      inp.onchange = () => {
        chooseFile(inp.files && inp.files[0]);
      };
      inp.click();
    });
    const dropTarget = view.querySelector('.cbox');
    dropTarget.addEventListener('dragover', (e) => { e.preventDefault(); dropTarget.classList.add('drop-active'); });
    dropTarget.addEventListener('dragleave', () => dropTarget.classList.remove('drop-active'));
    dropTarget.addEventListener('drop', (e) => {
      e.preventDefault(); dropTarget.classList.remove('drop-active');
      chooseFile(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]);
    });
    // 原生右键菜单的“粘贴”会触发这里；图片或从资源管理器复制的文件
    // 进入同一套类型和大小校验流程；点击发送即表示发送附件。
    dropTarget.addEventListener('paste', (e) => {
      const files = e.clipboardData && e.clipboardData.files;
      if (files && files.length) {
        e.preventDefault();
        chooseFile(files[0]);
      }
    });

    /**
     * 她干活时的进度：一条一条追加，看得见做到哪了。
     * 多步任务（"整理下载目录"）一跑就是十几步、每步都可能等你点确认，
     * 没有这个你只能对着一个转圈的省略号猜它在干嘛。
     */
    function addStep(name, text, kind) {
      const box = msgs.querySelector('#liveSteps');
      if (!box) return;
      box.hidden = false;
      const cls = kind === 'danger' ? 'warn' : kind === 'err' ? 'err' : 'neutral';
      box.insertAdjacentHTML('beforeend',
        '<div style="display:flex;gap:8px;align-items:baseline;padding:3px 0">' +
        '<span class="mono muted" style="font-size:11px;flex:none">' + esc(name.replace(/^local_/, '')) + '</span>' +
        '<span style="font-size:12.5px;color:var(--text-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
        esc(String(text || '').slice(0, 90)) + '</span>' +
        '<span class="sp"></span><span class="tag ' + cls + '">' + esc(kind === 'danger' ? '等你确认' : kind === 'err' ? '失败' : '完成') + '</span>' +
        '</div>');
      msgs.scrollTop = msgs.scrollHeight;
    }
    async function loadMsgs(force) {
      // The welcome page has no conversation context; never load unscoped
      // history or Creator Jobs while it is active.
      if (!activeConversationId) return;
      const loadSeq = ++conversationLoadSeq;
      let loadConversationId = activeConversationId;
      const loadState = view.querySelector('#chatLoadState');
      const showRetry = (message) => {
        heroEl.hidden = true; chat.classList.remove('is-empty'); loadState.hidden = false;
        loadState.innerHTML = '<b>暂时无法加载会话</b><p>' + esc(message || '检查连接设置') + '</p><button class="btn oline" id="retryHistory">重新加载</button>';
        loadState.querySelector('button').onclick = () => loadMsgs(true);
      };
      if (!await loadRecordScope()) { if (loadSeq === conversationLoadSeq && (force || lastCount < 0)) showRetry(state.recordScopeError || '无法读取当前连接身份'); return; }
      if (loadSeq !== conversationLoadSeq || (loadConversationId && activeConversationId !== loadConversationId)) return;
      loadConversationId = activeConversationId;
      state.chatConversationId = activeConversationId;
      const [historyResponse, creatorResponse] = await Promise.all([
        get('/api/history?limit=300&src=pc&conversation_id=' + encodeURIComponent(loadConversationId)),
        get('/api/creator/jobs?conversation_id=' + encodeURIComponent(loadConversationId)),
      ]);
      const r = pick(historyResponse);
      const creatorHistory = pick(creatorResponse);
      if (!msgs.isConnected || loadSeq !== conversationLoadSeq || activeConversationId !== loadConversationId) return;
      if (!r.ok && !localRecords().length && !(creatorHistory.items || []).length) {
        if (force || lastCount < 0) {
          showRetry(r._err || '检查连接设置');
        }
        return;
      }
      loadState.hidden = true;
      const items = (r.items || []).slice();
      const records = new Map(localRecords().map(item=>[item.request_id,item]));
      for (const item of r.display_turns || []) records.set(item.request_id, {...item,kind:'chat'});
      for (const job of creatorHistory.items || []) {
        if (job.conversation_id === loadConversationId) records.set(job.request_id, {...job,input:job.feedback || job.requirement});
      }
      const jobsToWatch = [];
      for (const item of [...records.values()].sort((a,b)=>a.created_at-b.created_at)) {
        const input = item.input || item.requirement || '';
        const status = item.status || 'pending';
        // Successful legacy chat already has its own durable pair. Reuse it.
        if (item.kind === 'chat' && status === 'succeeded' && items.some((m,i)=>m.role==='user'&&m.text===input&&items[i+1]?.text===item.content)) {
          try { localStorage.setItem(recordKey(), JSON.stringify(localRecords().filter(r=>r.request_id!==item.request_id))); }
          catch (_) { toast('本地记录合并未能保存'); }
          continue;
        }
        const t = item.created_at * 1000;
        items.push({role:'user',text:input,t,request_id:item.request_id});
        const message = (item.error ? creatorErrorText(item.error) : '') || ({pending:'请求尚未确认送达，未自动重发',running:'原请求执行中，未自动重发',
          failed:'请求失败',cancelled:'已取消',interrupted:'请求已中断，未自动重发',succeeded:item.kind === 'chat' ? '任务完成' : '草稿已生成'})[status] || status;
        items.push({role:'assistant',text:(item.content || '') + '\n\n' + message,t,
          request_id:item.request_id,partial:status!=='succeeded',job:item.job_id ? item : null,
          run:item.run_id ? item : null});
        if (item.job_id && !['failed','cancelled','interrupted'].includes(status)) jobsToWatch.push(item);
      }
      items.sort((a,b)=>toMs(a.t)-toMs(b.t));
      const renderKey = JSON.stringify(items);
      if (!force && renderKey === lastCount) return;
      lastCount = renderKey;
      if (!items.length) {
        // Empty history: centered welcome and the existing quick questions.
        heroEl.hidden = false;
        chat.classList.add('is-empty');
        heroEl.innerHTML = heroHtml();
        heroEl.querySelectorAll('[data-hint]').forEach((b) => b.addEventListener('click', () => {
          const ta = view.querySelector('#ta');
          if (ta) { ta.value = b.dataset.hint; ta.dispatchEvent(new Event('input')); ta.focus(); }
        }));
        msgs.innerHTML = '';
        drawSessions();
        return;
      }
      drawSessions();
      heroEl.hidden = true;
      chat.classList.remove('is-empty');
      // Preserve live draft nodes only while refreshing this same conversation.
      // Switching sessions detaches these nodes; scoped Creator jobs restore
      // that conversation's cards from durable Brain state.
      const draftCards = renderedDraftConversationId === loadConversationId
        ? [...msgs.querySelectorAll('.crturn')] : [];
      msgs.innerHTML = items.map((m) => {
        const mine = ['me', 'user', 'self'].includes(String(m.role || '').toLowerCase());
        // 用户消息纯文本（esc），AI 消息用 Markdown 渲染
        const t = mine || m.partial ? esc(m.text || '').replace(/\n/g,'<br>') : U.md(m.text || '', { codeActions: true });
        const atts = mine && Array.isArray(m.attachments) ? m.attachments.map((a) =>
          '<div class="msg-attachment">' + icon(a.kind === 'image' ? 'img' : 'clip', 'sm') + ' ' +
          esc(a.name || '附件') + (a.size ? ' · ' + esc(bytes(a.size)) : '') + '</div>').join('') : '';
        // 不画头像、不写名字：这是工具界面，不是"跟谁对话"。
        // 她说的在左、你说的在右，不用再靠头像区分。
        if (mine) return '<div class="turn me"><div class="bdy"><div class="txt">' + t + atts + '</div></div></div>';
        return '<div class="turn"' + (m.job ? ' data-job="' + esc(m.job.job_id) + '"' : '') + '><div class="bdy">' +
          '<div class="txt md">' + t + '</div>' +
          '<div class="foot"><span>' + esc(fmtTime(m.t, 'time')) + '</span>' +
          (m.run && ['queued','running','paused_budget'].includes(m.run.status) ?
            '<button class="btn oline agent-cancel" data-run="' + esc(m.run.run_id) + '">取消任务</button>' : '') +
          '</div></div></div>';
      }).join('');
      renderedDraftConversationId = loadConversationId;
      for (const button of msgs.querySelectorAll('.agent-cancel')) {
        button.onclick = async () => {
          button.disabled = true;
          const r = pick(await post('/api/agent/runs/' + encodeURIComponent(button.dataset.run) + '/cancel', {}));
          if (!r.ok) { button.disabled = false; toast('取消未确认，请检查任务状态'); }
          else await loadMsgs(true);
        };
      }
      draftCards.forEach((n) => msgs.appendChild(n));
      for (const job of jobsToWatch) {
        const root = msgs.querySelector('[data-job="' + job.job_id + '"]');
        if (root) void watchCreatorJob(root, job);
      }
      msgs.scrollTop = msgs.scrollHeight;
    }

    async function send() {
      const ta = view.querySelector('#ta');
      const text = (ta.value || '').trim();
      if (!global.vigil || (!text && !imgB64 && !attachment) || busy) return;
      if (!await loadRecordScope() || busy) return;
      if (attachment && attachment.failed) return;
      if (attachment && attachment.kind === 'image' && !imgB64) {
        attachment.status = '图片尚未读取完成';
        renderAttachment();
        return;
      }
      if (!activeConversationId) {
        const welcomeScope = recordKey();
        busy = true;
        busyScope = welcomeScope;
        updateComposer();
        const createdId = await ensureConversation();
        if (!createdId) { finishBusy(welcomeScope); return; }
        finishBusy(welcomeScope);
      }
      const sendConversationId = activeConversationId;
      const sendScope = recordKey();
      // 创作模式：同一条输入框，发给 creator 而不是聊天历史
      if (creatorMode !== 'off' && text) { creatorSend(text); return; }
      const pending = attachment;
      const uploadedAttachments = [];
      if (pending && pending.kind !== 'image') {
        busy = true;
        busyScope = sendScope;
        pending.status = '准备上传';
        updateComposer();
        try {
          if (pending.kind === 'video' && pending.capabilityCheck) await pending.capabilityCheck;
          // 原视频先独立保存。后续元数据/关键帧分析失败时，不能丢弃
          // 这份已经上传完成的原文件。
          const original = await uploadAttachment(pending);
          uploadedAttachments.push(original);
          if (activeConversationId !== sendConversationId) { finishBusy(sendScope); return; }
          if (pending.kind === 'video') {
            if (pending.videoUnsupported && pending.videoChoice === 'frames') {
                pending.status = '提取关键帧…';
                renderAttachment();
                try {
                  let frameFiles = [];
                  try {
                    frameFiles = await extractVideoFrames(pending.file, 3);
                  } catch (browserError) {
                    if (!pending.videoCapabilities || pending.videoCapabilities.video_frames !== true) throw browserError;
                    pending.status = '浏览器解析失败，改用本机 FFmpeg…';
                    renderAttachment();
                    uploadedAttachments.push(...await extractVideoFramesViaBrain(original.id, 3));
                  }
                  for (const frame of frameFiles) {
                    const frameAtt = { file: frame, kind: 'image', progress: 0, status: '上传中' };
                    try {
                      uploadedAttachments.push(await uploadAttachment(frameAtt, true));
                    } catch (frameUploadError) {
                      frameUploadError.attachmentUploadFailed = true;
                      throw frameUploadError;
                    }
                  }
                  pending.status = '已上传，关键帧已准备';
                } catch (frameError) {
                  if (frameError.attachmentUploadFailed) throw frameError;
                  pending.status = '已上传；关键帧提取失败';
                  renderAttachment();
                  toast('原视频已保存，但关键帧提取失败：' + String(frameError.message || frameError).slice(0, 100));
                }
            }
          }
        } catch (e) {
          pending.failed = true;
          pending.status = '上传失败：' + String(e.message || e).slice(0, 80);
          renderAttachment();
          finishBusy(sendScope);
          return;
        }
      }
      if (activeConversationId !== sendConversationId) { finishBusy(sendScope); return; }
      const sendingImg = imgB64;
      const requestId = 'req_' + crypto.randomUUID();
      const turnScope = sendScope;
      const remember = patch => rememberTurn(requestId, patch, turnScope);
      remember({input:text,kind:'chat',status:'pending'});
      const sendingAttachments = uploadedAttachments;
      if (attachment && attachment.previewUrl && global.URL) {
        try { URL.revokeObjectURL(attachment.previewUrl); } catch (_) {}
      }
      attachment = null;
      imgB64 = '';
      attachBox.hidden = true;
      attachBox.innerHTML = '';
      ta.value = '';
      busy = true;
      busyScope = turnScope;
      heroEl.hidden = true;
      view.querySelector('#chatLoadState').hidden = true;
      chat.classList.remove('is-empty');
      updateComposer();
      const btn = view.querySelector('#sendBtn');
      btn.disabled = true;

      // 乐观气泡：用户消息先画上去
      msgs.insertAdjacentHTML('beforeend',
        '<div class="turn me"><div class="bdy"><div class="txt">' + esc(text) +
        (sendingImg ? '<img class="msgimg" src="data:image/png;base64,' + sendingImg + '" alt="">' : '') +
        (sendingAttachments.length ? '<div class="msg-attachment">' + sendingAttachments.map((a) => icon(a.kind === 'image' ? 'img' : 'clip', 'sm') + ' ' + esc(a.name) + ' · ' + esc(bytes(a.size))).join('<br>') + '</div>' : '') +
        '</div></div></div>');

      // AI 气泡：思考过程 + 正文，实时渲染
      const turnId = 'turn_' + Date.now();
      const thinkId = 'think_' + turnId;
      const textId = 'text_' + turnId;
      msgs.insertAdjacentHTML('beforeend',
        '<div class="turn" id="' + turnId + '"><div class="bdy">' +
        streamBodyHtml(thinkId, textId) +
        '</div></div>');
      msgs.scrollTop = msgs.scrollHeight;

      // 思考过程折叠/展开
      const thinkToggle = msgs.querySelector('#' + turnId + ' .think-toggle');
      const thinkContent = document.getElementById(thinkId);
      thinkToggle && thinkToggle.addEventListener('click', () => {
        thinkContent.hidden = !thinkContent.hidden;
        thinkToggle.querySelector('.think-caret').textContent = thinkContent.hidden ? '▾' : '▴';
      });

      const thinkInner = document.getElementById(thinkId + '_inner');
      const textEl = document.getElementById(textId);
      const statusEl = msgs.querySelector('#' + turnId + ' .stream-status');
      let thinkBuf = '';
      let textBuf = '';
      let streamDone = false;
      let thinkDirty = false;
      let textDirty = false;
      let rafPending = false;
      let agentRunId = null;
      const agentCancel = document.createElement('button');
      agentCancel.className = 'btn oline agent-cancel';
      agentCancel.textContent = '取消任务';
      agentCancel.onclick = async () => {
        if (!agentRunId) return;
        agentCancel.disabled = true;
        try {
          const r = pick(await post('/api/agent/runs/' + encodeURIComponent(agentRunId) + '/cancel', {}));
          if (!r.ok) throw Error(r._err || '取消未确认');
          statusEl.textContent = '已取消后续调度；在途操作可能仍在执行';
        } catch (_) {
          agentCancel.disabled = false;
          toast('取消未确认，请检查任务状态');
        }
      };

      // 借鉴 DeepSeek Harness：事件逐个进状态，但按动画帧合并绘制，
      // 避免每个 token 都重绘导致主线程卡顿
      function scheduleRender() {
        if (rafPending) return;
        rafPending = true;
        requestAnimationFrame(() => {
          rafPending = false;
          if (activeConversationId !== sendConversationId) return;
          if (thinkDirty) {
            thinkInner.textContent = thinkBuf;
            thinkDirty = false;
          }
          if (textDirty) {
            textEl.textContent = textBuf;
            textDirty = false;
          }
          msgs.scrollTop = msgs.scrollHeight;
        });
      }

      // 监听流式事件
      const onEvent = (evt) => {
        if (activeConversationId !== sendConversationId) return;
        if (evt.job_id) return;
        if (evt.request_id && evt.request_id !== requestId) return;
        if (evt.run_id && agentRunId && evt.run_id !== agentRunId) return;
        if (turnScope !== recordKey()) return;
        if (streamDone) return;
        if (evt.type === 'agent_run') {
          agentRunId = evt.run_id;
          remember({run_id:agentRunId,status:'running'});
          statusEl.textContent = '执行中 ';
          statusEl.appendChild(agentCancel);
        } else if (evt.type === 'stage') {
          statusEl.textContent = ({generating:'模型生成中 ',waiting_tool:'等待工具结果 '})[evt.stage] || '执行中 ';
          if (agentRunId) statusEl.appendChild(agentCancel);
        } else if (evt.type === 'run_snapshot') {
          textBuf = evt.text || '';
          remember({content:textBuf,status:'running'});
          textDirty = true;
          scheduleRender();
        } else if (evt.type === 'think' && evt.text) {
          thinkBuf += evt.text;
          thinkDirty = true;
          scheduleRender();
        } else if (evt.type === 'text' && evt.text) {
          textBuf += evt.text;
          remember({content:textBuf,status:'running'});
          textDirty = true;
          scheduleRender();
        } else if (evt.type === 'done') {
          streamDone = true;
          stopListening();
          textBuf = evt.text || textBuf;
          remember({content:textBuf,status:'succeeded',error:null});
          // 强制刷新最后一帧
          if (thinkDirty) { thinkInner.textContent = thinkBuf; thinkDirty = false; }
          if (textDirty) { textEl.textContent = textBuf; textDirty = false; }
          // 正文渲染成 Markdown
          textEl.innerHTML = U.md(textBuf || evt.text || '', { codeActions: true });
          // 思考过程如果为空，隐藏整个思考块
          if (!thinkBuf.trim()) {
            const tb = msgs.querySelector('#' + turnId + ' .think-block');
            if (tb) tb.remove();
          } else {
            thinkToggle.querySelector('.think-caret').textContent = '▾';
            thinkContent.hidden = true;
          }
          statusEl.textContent = fmtTime(Date.now(), 'time');
          finishBusy(turnScope);
          refreshConversations().catch(() => {});
        } else if (evt.type === 'error') {
          streamDone = true;
          stopListening();
          textBuf = evt.text || textBuf;
          remember({content:textBuf,status:evt.status || 'failed',
            error:{class:evt.class || 'service_error',message:evt.msg || '请求未完成'}});
          textEl.innerHTML = esc(textBuf || evt.text || '') + '<br><span style="color:var(--err)">出错了：' + esc(evt.msg || '未知错误') + '（结果不完整）</span>';
          statusEl.textContent = ({cancelled:'已取消',paused_budget:'预算已用尽',interrupted:'执行中断'})[evt.status] || '失败';
          finishBusy(turnScope);
          refreshConversations().catch(() => {});
        }
      };

      // 注册流式事件监听
      let stopListening = () => {};
      if (!sendingAttachments.length && global.vigil && global.vigil.onStreamEvent) {
        stopListening = subscribeUntilDetached(document.getElementById(turnId), global.vigil.onStreamEvent, onEvent);
        if (streamDone) stopListening();
      } else {
        console.warn('[stream] vigil.onStreamEvent not found');
      }

      try {
      const chatPayload = { text: text, src: state.src || 'pc', request_id:requestId,
        conversation_id: activeConversationId, session_id: activeConversationId };
        if (sendingImg) chatPayload.img = sendingImg;
        if (sendingAttachments.length) chatPayload.attachments = sendingAttachments;
        // 附件走普通 JSON 元数据请求；二进制已经在此前分块上传，
        // 这样聊天请求不会再次携带文件内容。纯文本仍使用原有 SSE 流。
        if (sendingAttachments.length) {
          const r = pick(await post('/api/chat', chatPayload));
          if (!r.ok) throw new Error(r._err || '附件消息发送失败');
          streamDone = true;
          textBuf = r.reply || '';
          remember({content:textBuf,status:r.err?'failed':'succeeded',error:r.err?{message:'请求未完成'}:null});
          textEl.innerHTML = U.md(textBuf, { codeActions: true });
          const tb = msgs.querySelector('#' + turnId + ' .think-block');
          if (tb) tb.remove();
          statusEl.textContent = fmtTime(Date.now(), 'time');
          finishBusy(turnScope);
          refreshConversations().catch(() => {});
        } else if (global.vigil && global.vigil.apiStream) {
          // 开始流式请求
          console.log('[stream] starting apiStream...');
          const r = await global.vigil.apiStream('/api/chat/stream', chatPayload);
          // IPC 会在 HTTP 超时/断连时返回 {ok:false}。不能只等 SSE 事件，
          // 否则历史会一直是 running；主进程也可能因进程退出而无法推送事件。
          if (!streamDone && r && r.ok === false) {
            onEvent({type:'error',class:'connection_lost',status:'interrupted',
              msg:r.err || '连接中断，结果不完整'});
          }
          if (!streamDone) {
            // 如果请求返回了但还没收到 done 事件，等一下
            setTimeout(() => {
              if (!streamDone) {
                console.warn('[stream] no done event received, textBuf length:', textBuf.length);
                remember({content:textBuf,status:'interrupted',
                  error:{class:'connection_lost',message:'未收到完整回复'}});
                textEl.innerHTML = (textBuf ? U.md(textBuf, { codeActions: true }) : '') +
                  '<br><span style="color:var(--err)">连接中断：未收到完整回复（结果不完整）</span>';
                statusEl.textContent = '执行中断';
                finishBusy(turnScope);
                streamDone = true;
                stopListening();
              }
            }, 2000);
          }
        } else {
          console.warn('[stream] vigil.apiStream not found, falling back to non-stream');
          // 降级到非流式
          const r = pick(await post('/api/chat', chatPayload));
          if (!r.ok) throw new Error(r._err || '发送失败');
          textBuf = r.reply || '';
          remember({content:textBuf,status:r.err?'failed':'succeeded',error:r.err?{message:'请求未完成'}:null});
          textEl.innerHTML = U.md(textBuf, { codeActions: true });
          statusEl.textContent = fmtTime(Date.now(), 'time');
          finishBusy(turnScope);
          streamDone = true;
          stopListening();
          refreshConversations().catch(() => {});
        }
      } catch (e) {
        remember({content:textBuf,status:'interrupted',error:{message:'连接中断，结果不完整；未自动重发'}});
        streamDone = true;
        stopListening();
        textEl.innerHTML = '<span style="color:var(--err)">出错了：' + esc(String(e.message || e)) + '</span>';
        statusEl.textContent = '失败';
        finishBusy(turnScope);
        refreshConversations().catch(() => {});
      }
    }

    view.querySelector('#sendBtn').addEventListener('click', send);
    view.querySelector('#ta').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        send();
      }
    });
    const taEl = view.querySelector('#ta');
    // ── 创作模式发送：需求 → /api/creator/draft → 草稿卡气泡 ──
    // 不走 /api/chat/stream：创作不产生对话历史，草稿是独立工作区里的
    // 文件 + 静态验证结果。模型失败时明说，不假装生成了东西。
    async function creatorSend(text) {
      if (busy) return;
      if (!await loadRecordScope() || busy) return;
      const turnScope = recordKey();
      const turnConversationId = activeConversationId;
      busy = true;
      busyScope = turnScope;
      updateComposer();
      taEl.value = '';
      heroEl.hidden = true;
      view.querySelector('#chatLoadState').hidden = true;
      chat.classList.remove('is-empty');
      msgs.insertAdjacentHTML('beforeend',
        '<div class="turn me"><div class="bdy"><div class="txt">' + esc(text) + '</div></div></div>');
      const turnId = 'crturn_' + Date.now();
      const requestId = 'req_' + crypto.randomUUID();
      rememberTurn(requestId, {input:text,kind:creatorMode,status:'pending'}, turnScope);
      msgs.insertAdjacentHTML('beforeend',
        '<div class="turn" id="' + turnId + '"><div class="bdy"><div class="txt md" style="color:var(--text-3)">' +
        '正在调用模型生成' + (creatorMode === 'skill' ? '技能' : '插件') + '草稿，可能要几十秒…</div></div></div>');
      msgs.scrollTop = msgs.scrollHeight;
      let r = null;
      try {
        // 🔴 必须 pick() 拆包：API.post 返回的是 {ok,status,json}，
        // draft 在 r.json 里。直接读 r.draft 会永远拿到失败+未知错误
        // （2026-10-12 实测：HTTP 200 也被显示成"生成失败：未知错误"）。
        r = pick(await API.post('/api/creator/jobs',
          { kind: creatorMode, requirement: text, request_id: requestId,
            src: 'pc', conversation_id: turnConversationId }));
        if (activeConversationId !== turnConversationId) { finishBusy(turnScope); return; }
        if (r.ok && r.job_id) {
          const root = document.getElementById(turnId);
          if (root) await watchCreatorJob(root, r, turnScope);
          finishBusy(turnScope); loadRpDrafts();
          return;
        }
      } catch (e) {
        r = { ok: false, err: String((e && e.message) || e) };
      }
      if (activeConversationId !== turnConversationId) { finishBusy(turnScope); return; }
      const el = document.getElementById(turnId);
      if (!r || !r.ok || !r.draft) {
        const failure = creatorRequestFailure(r);
        rememberTurn(requestId, failure, turnScope);
        if (el) {
          const node = el.querySelector('.txt');
          node.textContent = '生成失败：' + creatorErrorText(failure.error);
          node.style.color = 'var(--err)';
          node.setAttribute('role', 'alert');
        }
      } else {
        const holder = document.createElement('div');
        holder.innerHTML = draftCardHtml(r.draft);
        const card = holder.firstChild;
        if (el) el.replaceWith(card); else msgs.appendChild(card);
        bindDraftCard(card, r.draft, loadRpDrafts);
        toast(r.draft.validation && r.draft.validation.ok
          ? '草稿已生成，静态验证通过' : '草稿已生成，但有验证问题，见卡片');
        loadRpDrafts();
      }
      msgs.scrollTop = msgs.scrollHeight;
      finishBusy(turnScope);
    }
    taEl.addEventListener('input', updateComposer);
    const resizeComposer = () => { if (taEl.isConnected) { updateComposer(); applySessions(); } };
    window.addEventListener('resize', resizeComposer);
    updateComposer();
    // 🔴 这两个按钮在**顶栏**里，不在 view 里 —— 用 view.querySelector 会拿到 null，
    // 报的错又是异步的（不进 app.js 那个 try），表现就是"消息区永远空白"。
    ctx.topbar.querySelector('#rpBtn').addEventListener('click', () => {
      view.querySelector('.rp').classList.toggle('no');
      view.querySelector('.split').classList.toggle('no-right');
      ctx.topbar.querySelector('#rpBtn').setAttribute('aria-expanded', String(!rp.classList.contains('no')));
    });
    ctx.topbar.querySelector('#clearBtn').addEventListener('click', async () => {
      if (await confirmDialog('清空当前会话的聊天历史？此操作不可恢复。')) {
        post('/api/history/clear', { src: state.src || 'pc', conversation_id: activeConversationId }).then(r => {
          if (r.ok) {
            const scope = recordKey();
            const cleared = JSON.parse(localStorage.getItem(scope + ':cleared') || '[]');
            localStorage.setItem(scope + ':cleared', JSON.stringify([...new Set([...cleared,...localRecords().map(x=>x.request_id)])].slice(-200)));
            localStorage.removeItem(scope);
            msgs.innerHTML = '';
          }
          else toast('清除失败，记录仍保留');
          return loadMsgs(true);
        });
      }
    });

    // 她调本地工具时，把每一步打在这轮消息下面。
    // 这个监听挂在 window 上（一次），但只往当前页面的 #liveSteps 里塞东西 ——
    // 换页之后那个节点没了，addStep 会自己跳过。
    if (!global.__localStepBound) {
      global.__localStepBound = true;
      const fanout = (msg) => {
        (global.__stepSinks = global.__stepSinks || []).forEach((fn) => {
          try { fn(msg); } catch (_) {}
        });
      };
      if (global.vigil && global.vigil.local) {
        global.vigil.local.onStep && global.vigil.local.onStep(fanout);
        global.vigil.local.onDone && global.vigil.local.onDone(fanout);
      }
    }
    global.__stepSinks = global.__stepSinks || [];
    const sink = (m) => {
      if (!document.body.contains(msgs)) return;
      const t = msgs.querySelector('#pendingText');
      if (t) t.innerHTML = '正在干活<span class="caret"></span>';
      addStep(m.name || '', m.text || '', m.kind || (m.done ? 'ok' : ''));
      if (m.done) {
        const pt = msgs.querySelector('#pendingText');
        if (pt) pt.innerHTML = '正在想结论<span class="caret"></span>';
      }
    };
    global.__stepSinks.push(sink);
    const dropSink = () => {
      global.__stepSinks = (global.__stepSinks || []).filter((f) => f !== sink);
    };
    // 页面被换掉时把自己摘掉，免得越挂越多
    const t = setInterval(() => {
      if (!document.body.contains(msgs)) { dropSink(); offDoc(); clearInterval(t); }
    }, 3000);

    // ── 危险操作确认：落在聊天气泡里 ─────────────────────────
    // 主进程执行写/删/执行命令前会把问题发过来（app.js 负责收，
    // 不在聊天页时自动切过来并排队）。确认卡就挂在消息流末尾，
    // 和"她为什么要做这件事"出现在同一条对话流里。
    function showConfirm(msg) {
      const a = msg.args || {};
      const rows = Object.keys(a).map((k) =>
        '<div class="kv"><span>' + esc(k) + '</span></div><div class="cfarg">' + esc(a[k]) + '</div>');
      // 三档 risk（M4/M5）：dangerous 强提醒（标题和按钮都更重），
      // confirm 普通确认，risk 没带（老服务端）走原样。
      const risky = msg.risk === 'dangerous';
      const confirmish = msg.risk === 'confirm';
      const headTxt = risky ? '她要在这台电脑上做一个高危操作'
        : confirmish ? '她要在这台电脑上做改动'
        : '她要用这台电脑做一个操作';
      const hint = risky
        ? '这一步可能删东西、杀进程或执行命令，不可自动撤销。确认是你让她做的再放行。'
        : '只有点了「允许」才会执行；60 秒不理它算拒绝。';
      const wrap = document.createElement('div');
      wrap.className = 'turn';
      wrap.innerHTML =
        '<div class="bdy"><div class="cfcard">' +
        '<div class="cfh" style="' + (risky ? 'color:var(--err);' : '') + '">' +
        icon('shield', 'sm') + '<b>' + esc(headTxt) + '</b></div>' +
        '<div class="kv" style="margin-top:8px"><span>操作</span><b class="mono">' + esc(msg.name) + '</b></div>' +
        (rows.length ? rows.join('') : '<div class="muted" style="font-size:12px;margin-top:4px">（没有参数）</div>') +
        '<div class="muted" style="font-size:11.5px;margin-top:8px">' + esc(hint) + '</div>' +
        '<div class="cfbtns"><span class="sp"></span>' +
        '<button class="btn oline" data-allow="0">不允许</button>' +
        '<button class="btn ' + (risky ? 'oline' : 'fill') + '" data-allow="1">' +
        (risky ? '我确认，放行' : '允许这一次') + '</button></div>' +
        '</div></div>';
      msgs.appendChild(wrap);
      msgs.scrollTop = msgs.scrollHeight;
      const pt = msgs.querySelector('#pendingText');
      if (pt) pt.innerHTML = '等你确认<span class="caret"></span>';
      const done = (allow) => {
        window.vigil.local.answer(msg.id, allow);
        wrap.querySelector('.cfbtns').innerHTML =
          '<span class="tag ' + (allow ? 'ok' : 'neutral') + '">' + (allow ? '已允许' : '已拒绝') + '</span>';
      };
      wrap.querySelectorAll('[data-allow]').forEach((b) =>
        b.addEventListener('click', () => done(b.dataset.allow === '1')));
      // 主进程 60 秒后自己会按拒绝算，界面同步把按钮收掉
      setTimeout(() => {
        const btns = wrap.querySelector('.cfbtns');
        if (btns && btns.querySelector('[data-allow]'))
          btns.innerHTML = '<span class="tag neutral">超时，已按拒绝处理</span>';
      }, 60000);
    }
    global.__confirmSink = () => {
      const q = global.__confirmQueue || [];
      global.__confirmQueue = [];
      q.forEach(showConfirm);
    };
    global.__confirmSink();          // 把切页期间排队的确认取走

    // ── 本地操控面板：三档权限模式 + 目录白名单 ──
    // 从输入框旁的盾牌弹出。三档预设（只读模式 / 默认模式 / 全部允许），
    // 不再摆一堆开关。工具随档位走：只读=危险工具全关，其余档=危险工具开。
    const pop = view.querySelector('#localPop');
    const localBtn = view.querySelector('#localBtn');
    const MODES = [
      ['readonly', '只读模式', '仅允许查看系统状态与读取文件，不执行任何修改操作'],
      ['asked', '默认模式', '允许读写白名单目录、执行白名单命令；涉及修改的操作需逐次确认'],
      ['full', '全部允许', '开放全部文件系统与命令执行权限，操作自动执行；系统关键目录仍受保护，修改自动备份可撤销'],
    ];

    function curMode(cfg, danger) {
      const off = cfg.localOff || [];
      danger = danger || [];
      const allDangerOff = danger.every((d) => off.includes(d));
      if (cfg.allowAllPaths && cfg.allowAllCmds && cfg.localAuto) return 'full';
      if (allDangerOff) return 'readonly';
      return 'asked';
    }

    async function drawLocalPop() {
      const d = await (global.vigil ? global.vigil.local.defaults() : Promise.resolve({ roots: [], commands: [], danger: [] }));
      const caps = await (global.vigil ? global.vigil.local.caps() : Promise.resolve([]));
      const roots = state.cfg.roots && state.cfg.roots.length ? state.cfg.roots : d.roots;
      const cur = curMode(state.cfg, d.danger);
      const save = async (patch) => {
        await API.saveCfg(patch);
        await API.loadCfg();
      };
      const applyMode = async (m) => {
        const danger = d.danger || [];
        if (m === 'readonly') {
          await save({ localOff: danger.slice(), localRisky: [], localAuto: false, allowAllPaths: false, allowAllCmds: false });
          toast('已切换至只读模式');
        } else if (m === 'asked') {
          await save({ localOff: [], localRisky: danger.slice(), localAuto: false, allowAllPaths: false, allowAllCmds: false });
          toast('已切换至默认模式');
        } else {
          if (!await confirmDialog('将开放全部文件系统与命令执行权限，操作自动执行无需确认（系统关键目录仍受保护，修改自动备份可撤销）。确定吗？')) return;
          await save({ localOff: [], localRisky: danger.slice(), localAuto: true, allowAllPaths: true, allowAllCmds: true });
          toast('已切换至全部允许模式');
        }
        drawLocalPop();
      };

      pop.innerHTML =
        '<div class="lph"><b>本地操控</b>' +
        '<span class="muted">当前能执行 ' + (caps.length ? caps.length + ' 个工具' : '（全关了）') + '</span>' +
        '<button class="ibtn" id="lpClose" title="收起">' + icon('x', 'sm') + '</button></div>' +
        '<div class="lpbody">' +
        '<div class="lsec">权限模式</div>' +
        MODES.map(([k, n, dsc]) =>
          '<div class="lpmode' + (k === cur ? ' on' : '') + '" data-mode="' + k + '">' +
          '<div class="lb"><b>' + esc(n) + '</b><span>' + esc(dsc) + '</span></div>' +
          (k === cur ? icon('check', 'sm') : '') +
          '</div>').join('') +
        '<div class="lsec">允许访问的目录' + (state.cfg.allowAllPaths ? '（全部允许模式下不生效）' : '') + '</div>' +
        roots.map((r, i) => '<div class="lprow"><span class="mono" title="' + esc(r) + '">' + esc(r) + '</span>' +
          (i === 0 ? '<span class="muted" style="font-size:11px;flex:none">默认</span>'
                   : '<button class="btn" data-rm="' + esc(r) + '">移除</button>') + '</div>').join('') +
        '<div class="lpadd"><input class="input mono" id="lpRoot" placeholder="D:\\某目录（点添加后生效）">' +
        '<button class="btn oline" id="lpAdd">添加</button></div>' +
        '</div>';

      pop.querySelector('#lpClose').addEventListener('click', () => { pop.hidden = true; });
      pop.querySelectorAll('[data-mode]').forEach((n) => n.addEventListener('click', () => applyMode(n.dataset.mode)));
      pop.querySelector('#lpAdd').addEventListener('click', async () => {
        const p = pop.querySelector('#lpRoot').value.trim();
        if (!p) return;
        await save({ roots: roots.includes(p) ? roots : roots.concat([p]) });
        toast('已添加');
        drawLocalPop();
      });
      pop.querySelectorAll('[data-rm]').forEach((b) => b.addEventListener('click', async () => {
        await save({ roots: roots.filter((x) => x !== b.dataset.rm) });
        drawLocalPop();
      }));
    }

    const closePop = (e) => {
      if (pop.hidden) return;
      if (pop.contains(e.target) || localBtn.contains(e.target)) return;
      pop.hidden = true;
    };
    document.addEventListener('pointerdown', closePop);
    const offDoc = () => {
      document.removeEventListener('pointerdown', closePop);
      window.removeEventListener('resize', resizeComposer);
    };
    localBtn.addEventListener('click', async () => {
      if (pop.hidden) { pop.hidden = false; await drawLocalPop(); }
      else pop.hidden = true;
    });

    showWelcomeHome(false);
    void initializeConversationView();
    clearInterval(chatTimer);
    chatTimer = setInterval(() => {
      if (state.page === 'chat' && state.conn.ok) loadMsgs(false);
    }, 8000);
  }

  // ═══ 1.5 工具（本地操作，不经过 LLM） ═══════════════════════
  // 这一页是整件事的重点：客户端现在就是干本地活的，那它自己就该能直接干。
  // 好处很实在 —— 不烧 token、不用等模型判意、断网也能用（纯本地）。
  async function renderTools(ctx) {
    // ⚠️ 这里不能把 ctx.topbar 解构成 `topbar` —— 那样会把上面那个
    // 同名的 topbar() 工具函数遮住，`topbar(...)` 就变成"把 DOM 元素当函数调"，
    // 异步抛错、页面一片空白（这个错我刚犯过一次）。
    const view = ctx.view, panel = ctx.panel, bar = ctx.topbar;
    bar.className = 'topbar';
    const cat = await (global.vigil ? global.vigil.local.tools() : Promise.resolve({ items: [], groups: {} }));
    const items = cat.items || [];
    const groups = cat.groups || {};
    let cur = (items.find((t) => !t.danger) || items[0] || null);
    let sub = 'run';
    let last = null;                 // 最近一次运行结果

    bar.innerHTML = topbar('本地操控', '直接在这台机器上执行，不经过对话管线',
      '<button class="btn oline" id="tRefresh">' + icon('refresh', 'sm') + '刷新</button>');
    panel.innerHTML =
      '<div class="phead"><h2>本地操作</h2></div>' +
      '<div class="pseg"><button data-sub="run" class="on">运行</button>' +
      '<button data-sub="log">操作日志</button></div>' +
      '<div class="plist" id="tList"></div>';
    const plist = panel.querySelector('#tList');

    function drawList() {
      if (sub !== 'run') {
        plist.innerHTML = '<div class="gtitle">' + esc('这台机器上做过的事') + '</div>' +
          '<div class="empty" style="text-align:left;padding:16px 12px">' +
          '你点过的、她调过的，都记在这儿。<br>有备份的能一键撤销。</div>';
        return;
      }
      const by = {};
      items.forEach((t) => { (by[t.group] = by[t.group] || []).push(t); });
      plist.innerHTML = Object.keys(by).map((g) =>
        '<div class="gtitle">' + esc(groups[g] || g) + '</div>' +
        by[g].map((t) => li({
          icon: 'term', title: t.name.replace(/^local_/, ''),
          sub: (t.danger ? '危险 · ' : '') + (t.enabled ? t.desc.slice(0, 14) : '未开启'),
          on: cur && t.name === cur.name, attrs: ' data-k="' + esc(t.name) + '"',
        })).join('')).join('');
      bindLi(plist, (k) => {
        cur = items.find((t) => t.name === k) || cur;
        last = null;
        draw();
      });
    }

    async function drawLog() {
      const rows = await (global.vigil ? global.vigil.local.log(200) : Promise.resolve([]));
      view.innerHTML = '<div class="wrap">' +
        (rows.length
          ? '<div class="card hd"><table class="tbl">' +
            '<tr><th>时间</th><th>工具</th><th>参数</th><th>谁</th><th>结果</th><th></th></tr>' +
            rows.map((r) => '<tr>' +
              '<td class="mono muted" style="white-space:nowrap">' + esc(String(r.t).slice(5, 16)) + '</td>' +
              '<td class="mono" style="font-size:12px">' + esc(String(r.tool).replace(/^local_/, '')) + '</td>' +
              '<td class="mono muted" style="font-size:11.5px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
              esc(Object.keys(r.args || {}).map((k) => k + '=' + String(r.args[k]).slice(0, 24)).join(' ')) + '</td>' +
              '<td>' + (r.source === 'her' ? tag('info', '她') : tag('neutral', '你')) + '</td>' +
              '<td class="muted" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
              esc(String(r.text || r.err || '').slice(0, 60)) + '</td>' +
              '<td>' + (r.undone ? tag('neutral', '已撤销')
                : (r.backup ? '<button class="btn" data-undo="' + esc(r.id) + '">撤销</button>'
                            : '<span class="muted" style="font-size:11.5px">不可撤销</span>')) + '</td>' +
              '</tr>').join('') + '</table></div>'
            + '<div class="muted" style="font-size:11.5px;margin-top:10px">只读操作不留备份，所以没有撤销按钮 —— 没什么可撤的。</div>'
          : empty('还没有操作记录', '左边的「运行」里点一个试试')) +
        '</div>';
      view.querySelectorAll('[data-undo]').forEach((b) => b.addEventListener('click', async () => {
        const r = await global.vigil.local.undo(b.dataset.undo);
        toast((r && (r.text || r.err)) || '撤销失败');
        drawLog();
      }));
    }

    function draw() {
      drawList();
      if (sub === 'log') { drawLog(); return; }
      if (!cur) {
        view.innerHTML = '<div class="wrap">' + errBox('没有可用的本地工具', '需要在 Electron 里跑（工具要在本机执行）') + '</div>';
        return;
      }
      const t = cur;
      const inputs = (t.params || []).map((p) =>
        '<div class="field"><label>' + esc(p.label) + (p.req ? ' <span class="muted">必填</span>' : '') + '</label>' +
        (p.multi
          ? '<textarea class="input" style="height:110px" data-p="' + esc(p.k) + '" placeholder="' + esc(p.ph || '') + '"></textarea>'
          : '<input class="input" data-p="' + esc(p.k) + '" placeholder="' + esc(p.ph || '') + '">') +
        '</div>').join('');

      view.innerHTML = '<div class="wrap">' +
        '<div class="card" style="margin-bottom:14px">' +
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
        '<b class="mono" style="font-size:13.5px">' + esc(t.name) + '</b>' +
        (t.danger ? tag('warn', '危险') : '') + (t.enabled ? '' : tag('neutral', '未开启')) + '</div>' +
        '<div class="muted" style="font-size:12.5px;margin-top:6px">' + esc(t.desc || '') + '</div>' +
        (inputs ? '<div style="margin-top:12px">' + inputs + '</div>' : '') +
        (t.danger ? '<div class="row" style="border:0;padding:10px 0 0"><div class="lb">' +
          '<b style="font-size:12.5px">我确认这会改动这台电脑上的东西</b>' +
          '<span>' + (t.enabled ? '写/删之前会自动备份，之后可以在日志里撤销' : '先在聊天输入框旁的盾牌面板里打开它') + '</span></div>' +
          '<button class="sw" id="tOk"></button></div>' : '') +
        '<div style="display:flex;gap:8px;align-items:center;margin-top:12px">' +
        '<button class="btn fill md" id="tRun"' + (t.enabled ? '' : ' disabled') + '>运行</button>' +
        (t.enabled ? '' : '<button class="btn oline" id="tGo">去打开</button>') +
        '<span class="muted" id="tHint" style="font-size:12px"></span></div>' +
        '</div>' +
        (last ? '<div class="sec">结果</div>' +
          '<div class="card"><div style="display:flex;gap:8px;margin-bottom:8px">' +
          '<button class="btn oline" id="tCopy">复制</button>' +
          (last.backup ? '<button class="btn" id="tUndo">撤销这次操作</button>' : '') +
          '</div>' +
          '<div class="cfarg" style="max-height:44vh;overflow:auto">' + esc(last.text) + '</div></div>' : '') +
        '</div>';

      let ok = !t.danger;
      const sw = view.querySelector('#tOk');
      if (sw) sw.addEventListener('click', () => {
        ok = !ok;
        sw.classList.toggle('on', ok);
      });
      view.querySelector('#tGo') && view.querySelector('#tGo').addEventListener('click', () => {
        state.page = 'settings';
        go('settings');
      });
      const btn = view.querySelector('#tRun');
      btn && btn.addEventListener('click', async () => {
        if (t.danger && !ok) { toast('先勾上"我确认"'); return; }
        const args = {};
        (t.params || []).forEach((p) => {
          const el = view.querySelector('[data-p="' + p.k + '"]');
          if (el) args[p.k] = el.value;
        });
        const miss = (t.params || []).filter((p) => p.req && !String(args[p.k] || '').trim());
        if (miss.length) { toast('还差：' + miss.map((p) => p.label).join('、')); return; }
        btn.disabled = true;
        view.querySelector('#tHint').textContent = '执行中…';
        const r = await global.vigil.local.run(t.name, args);
        last = { text: (r && r.text) || '', backup: r && r.backup, id: r && r.id };
        draw();
        toast('执行完成');
      });
      view.querySelector('#tCopy') && view.querySelector('#tCopy').addEventListener('click', () => {
        navigator.clipboard.writeText(last ? last.text : '');
        toast('已复制');
      });
      view.querySelector('#tUndo') && view.querySelector('#tUndo').addEventListener('click', async () => {
        // 直接用这次运行返回的日志 id 撤，别去日志里按备份路径"猜"是哪一条
        // （新建文件的备份路径是空的，猜会猜错）
        const r = await global.vigil.local.undo(last.id);
        toast((r && (r.text || r.err)) || '撤销失败');
        draw();
      });
    }

    U.bindSeg(panel.querySelector('.pseg'), (k) => {
      sub = k;
      draw();
    });
    bar.querySelector('#tRefresh').addEventListener('click', () => ctx.refresh());
    draw();
  }

  // ═══ 2. 人设 ═══════════════════════════════════════════════
  // 卡片式：选一个渠道（App / 微信私聊 / 微信群），点卡片上的「应用」把她在那边换掉。
  // 本机控制台不参与 —— 控制台是干活的工具，不扮演角色（走全局默认）。
  // 🔴 全部接口直达**云端**：微信真身在云端，以前打到本地 sidecar，
  // 改的是本地的账 —— 云端那只根本不知道，"切了人设没变"就是这么来的。
  async function renderPersona(ctx) {
    ctx.view.classList.add('persona-view');
    const CHANS = [['app', 'App', '全局默认（没单独设置的渠道都跟它）'],
                   ['wechat', '微信私聊', '私聊那一个'],
                   ['wechat_group', '微信群', '群里那一个（@ 她 / 她搭话的）']];
    const CB = { base: 'cloud' };   // 人设账本在云端，这一页全走云端
    let curChan = 'wechat_group';
    const r = pick(await get('/api/persona', CB));
    const list = r.templates || r.personas || r.presets || [];
    const per = {};
    for (const [c] of CHANS) {
      const pr = pick(await get('/api/persona?src=' + c, CB));
      per[c] = { key: pr.src_persona_key || pr.key || '', name: pr.name || '' };
    }

    ctx.topbar.className = 'topbar';
    ctx.topbar.innerHTML = topbar('人格与语气', '微信真身在云端 · 应用/编辑直接写云端，下一句话就是新人设',
      '<button class="btn oline" id="reloadP">' + icon('refresh', 'sm') + '重载人格</button>');
    ctx.panel.innerHTML = '<div class="phead"><h2>人设</h2></div>' +
      '<div class="pseg"><button data-sub="wechat_group"' + (curChan === 'wechat_group' ? ' class="on"' : '') + '>微信群</button>' +
      '<button data-sub="wechat"' + (curChan === 'wechat' ? ' class="on"' : '') + '>微信私聊</button>' +
      '<button data-sub="app"' + (curChan === 'app' ? ' class="on"' : '') + '>App</button></div>' +
      '<div class="plist"><div class="empty" style="text-align:left;padding:14px 10px">' +
      '在左边选渠道，点卡片上的「应用」。<br>本机控制台不设人设。</div></div>';

    const cardOf = (p) => {
      const k = p.key || p;
      const name = p.label || p.name || k;
      const desc = p.desc || '';
      const cur = (per[curChan] || {}).key;
      return '<div class="pcard' + (k === cur ? ' cur' : '') + '" data-k="' + esc(k) + '">' +
        '<div class="pch"><b>' + esc(name) + '</b>' + (k === cur ? tag('ok', '使用中') : '') + '</div>' +
        (desc ? '<div class="pdesc">' + esc(String(desc).slice(0, 90)) + '</div>' : '') +
        '<div class="pft"><button class="btn fill" data-apply="' + esc(k) + '"' +
        (k === cur || p.synthetic ? ' disabled' : '') + '>应用</button>' +
        (p.synthetic ? '' : '<button class="btn oline" data-edit="' + esc(k) + '">编辑</button>') + '</div>' +
        '</div>';
    };

    // ── 编辑器：改这套人设的文件本身（与她是否在用无关），保存后若她正用着就热重载 ──
    let editing = null;          // 正在编辑的人设 key
    const PFIELDS = [
      ['name', '名字', 'input'],
      ['nicknames', '昵称', 'input'],
      ['relation', '和你的关系', 'input'],
      ['call_user', '怎么称呼你', 'input'],
      ['personality', '性格', 'area'],
      ['background', '背景经历', 'area'],
      ['scene', '日常场景', 'area'],
      ['appearance', '外貌', 'area'],
      ['notes', '备注（给她看的内部设定）', 'area'],
    ];

    async function drawEditor(key) {
      const [, n] = CHANS.find(([x]) => x === curChan);
      const f = pick(await get('/api/persona/full?key=' + encodeURIComponent(key), CB));
      const fields = f.fields || {};
      const val = (k) => {
        const v = fields[k];
        return Array.isArray(v) ? v.join('、') : String(v == null ? '' : v);
      };
      ctx.view.innerHTML = '<div class="wrap">' +
        '<div class="sech"><b>编辑人设：' + esc(fields.name || key) + '</b>' +
        '<span class="muted">' + esc(n) + '渠道 · 保存写回这套人设的文件；她正在用的话，保存即生效</span></div>' +
        '<div class="card persona-editor">' +
        '<div class="grid g3">' +
        PFIELDS.filter(([, , t]) => t === 'input').map(([k, label]) =>
          '<div class="field"><label>' + esc(label) + '</label>' +
          '<input class="input" data-pf="' + esc(k) + '" value="' + esc(val(k)) + '"></div>').join('') +
        '</div>' +
        PFIELDS.filter(([, , t]) => t === 'area').map(([k, label]) =>
          '<div class="field"><label>' + esc(label) + '</label>' +
          '<textarea class="input" data-pf="' + esc(k) + '" style="height:96px">' + esc(val(k)) + '</textarea></div>').join('') +
        '<div style="display:flex;gap:8px;align-items:center;margin-top:6px">' +
        '<button class="btn fill md" id="pfSave">保存</button>' +
        '<button class="btn oline md" id="pfBack">返回</button>' +
        '<span class="muted" style="font-size:12px" id="pfMsg"></span></div>' +
        '</div></div>';
      ctx.view.querySelector('#pfBack').addEventListener('click', () => { editing = null; draw(); });
      ctx.view.querySelector('#pfSave').addEventListener('click', async () => {
        const fields2 = {};
        ctx.view.querySelectorAll('[data-pf]').forEach((el) => { fields2[el.dataset.pf] = el.value; });
        if (!String(fields2.name || '').trim()) { toast('总得有个名字'); return; }
        // 卡片标题显示的是 label（列表元数据），编辑器里没有这一栏 ——
        // 不跟着 name 走的话，改了名字卡片还挂旧名（"老吴猫"就是这么赖着不走的）
        fields2.label = String(fields2.name || '').slice(0, 20);
        const btn = ctx.view.querySelector('#pfSave');
        btn.disabled = true;
        const rr = pick(await post('/api/persona/save', { key: key, src: curChan, fields: fields2 }, CB));
        btn.disabled = false;
        ctx.view.querySelector('#pfMsg').textContent = rr.ok ? '已保存' + (rr.reloaded ? '，她已切换到新设定' : '（这套人设她当前没在用，下次应用时生效）') : ('保存失败：' + (rr._err || rr.err || ''));
        toast(rr.ok ? '人设已保存（云端）' : '保存失败');
        if (rr.ok) {
          if ((per[curChan] || {}).key === key) per[curChan].name = fields2.name;
        }
      });
    }

    const draw = () => {
      if (editing) { drawEditor(editing); return; }
      const [c, n, d] = CHANS.find(([x]) => x === curChan);
      // 当前人设可能不在模板清单里（比如默认的 base）：补一张合成卡，标"使用中"
      const cur = (per[curChan] || {}).key;
      let cards = list.slice();
      if (cur && !cards.some((p) => (p.key || p) === cur)) {
        cards = [{ key: cur, label: (per[curChan] || {}).name || cur,
                   desc: '当前使用的人设（不在模板清单里）。选一张卡点「应用」就会换掉它。', synthetic: true }].concat(cards);
      }
      ctx.view.innerHTML = '<div class="wrap">' +
        '<div class="sech"><b>' + esc(n) + '</b><span class="muted">' + esc(d) + '</span></div>' +
        (cards.length ? '<div class="pgrid">' + cards.map(cardOf).join('') + '</div>'
                      : r.ok ? empty('暂无人格配置', '添加人格配置后，可以在这里查看和编辑。') : errBox('读不到人设列表', r._err || '')) +
        '</div>';
      ctx.view.querySelectorAll('[data-edit]').forEach((b) => b.addEventListener('click', () => {
        editing = b.dataset.edit;
        draw();
      }));
      ctx.view.querySelectorAll('[data-apply]').forEach((b) => b.addEventListener('click', async () => {
        b.disabled = true;
        const rr = pick(await post('/api/persona/apply', { key: b.dataset.apply, src: curChan }, CB));
        b.disabled = false;
        toast(rr.ok ? '已切换（' + n + ' → ' + (rr.name || b.dataset.apply) + '），下一句话生效' : '切换失败：' + (rr._err || rr.err || ''));
        if (rr.ok) { per[curChan] = { key: b.dataset.apply, name: rr.name || b.dataset.apply }; draw(); }
      }));
    };
    U.bindSeg(ctx.panel.querySelector('.pseg'), (k) => { curChan = k; draw(); });
    ctx.topbar.querySelector('#reloadP').addEventListener('click', async () => {
      const rr = pick(await post('/api/reload_persona', {}, CB));
      toast(rr.ok ? '云端人设已重载' : '重载失败');
      ctx.refresh();
    });
    draw();
  }

  // ═══ 3. 记忆 ═══════════════════════════════════════════════
  // 当前「人设 × 渠道」分区的记忆条目（云端 GET /api/memory，只读）。
  // 写入仍在对话里由她的抽取管线负责 —— 这页是给人看的账本。
  async function renderMemory(ctx) {
    const st = pick(await get('/api/stats'));
    ctx.topbar.className = 'topbar';
    ctx.topbar.innerHTML = topbar('记忆与约定', '分区：本机控制台（pc）');
    ctx.panel.innerHTML = '<div class="phead"><h2>记忆</h2></div><div class="plist">' +
      '<div class="gtitle">账本</div>' +
      li({ icon: 'brain', title: '记忆条目', on: true }) +
      li({ icon: 'bell', title: '约定账本', sub: '待办 ' + ((st.agenda && st.agenda.pending) || 0) }) +
      '</div>';
    ctx.view.innerHTML = '<div class="wrap">' +
      '<div class="grid g4" style="margin-bottom:14px">' +
      '<div class="card"><div class="muted" style="font-size:12px">记忆条数</div><div style="font-size:22px;font-weight:600" id="memCount">…</div></div>' +
      '<div class="card"><div class="muted" style="font-size:12px">今日轮次</div><div style="font-size:22px;font-weight:600">' + esc(st.turns || 0) + '</div></div>' +
      '<div class="card"><div class="muted" style="font-size:12px">记忆命中</div><div style="font-size:22px;font-weight:600">' + esc(st.mem_hit_turns || 0) + '</div></div>' +
      '<div class="card"><div class="muted" style="font-size:12px">约定待办</div><div style="font-size:22px;font-weight:600">' + esc((st.agenda && st.agenda.pending) || 0) + '</div></div>' +
      '</div>' +
      '<div class="card hd"><div class="h"><h3>她记住了什么</h3><div class="sp"></div>' +
      '<select class="input" id="memStore" style="height:26px;width:170px" title="记忆分区（每个渠道/人设一份）"></select>' +
      '<input class="input" id="memQ" placeholder="搜一条…" style="height:26px;width:140px">' +
      '<select class="input" id="memType" style="height:26px;width:90px">' +
      '<option value="">全部</option><option value="fact">事实</option><option value="event">事件</option></select></div>' +
      '<div class="muted" style="font-size:11px;margin:6px 0">改动（编辑 / 删除）会通过同步层自动上云；云端是权威源，删掉的东西不会被增量拉取复活。</div>' +
      '<div id="memList"></div></div>' +
      '</div>';

    let all = [];
    let curStore = 'memory';
    const storeSel = () => ctx.view.querySelector('#memStore');

    async function loadStores() {
      const r = pick(await get('/api/memory/stores'));
      const names = (r && r.items && r.items.length) ? r.items : ['memory'];
      const sel = storeSel();
      sel.innerHTML = names.map((n) =>
        '<option value="' + esc(n) + '"' + (n === curStore ? ' selected' : '') + '>' +
        esc(n === 'memory' ? 'memory（主库）' : n) + '</option>').join('');
      sel.onchange = () => { curStore = sel.value; load(); };
    }

    const drawList = () => {
      const q = (ctx.view.querySelector('#memQ').value || '').trim().toLowerCase();
      const ty = ctx.view.querySelector('#memType').value;
      const box = ctx.view.querySelector('#memList');
      const items = all.filter((x) =>
        (!ty || (x.type || '') === ty) &&
        (!q || String(x.text || '').toLowerCase().includes(q)));
      box.innerHTML = items.length ? items.map((x) =>
        '<div class="row"><div class="lb"><b>' + esc(x.text || '') + '</b>' +
        '<span>' + esc(x.time || '') + (x.chan ? ' · ' + esc(x.chan) : '') +
        (x.src ? ' · ' + esc(x.src) : '') + '</span></div>' +
        tag((x.type || '') === 'fact' ? 'info' : 'neutral', (x.type || '') === 'fact' ? '事实' : '事件') +
        '<button class="btn oline" style="height:24px;padding:0 8px;font-size:11px" data-edit="' + esc(x.id) + '">改</button>' +
        '<button class="btn oline" style="height:24px;padding:0 8px;font-size:11px;color:var(--err)" data-del="' + esc(x.id) + '">删</button>' +
        '</div>').join('')
        : empty(all.length ? '没有匹配的条目' : '这个分区还没有记忆', all.length ? '换个搜法' : '聊几句她就开始记了');
      box.querySelectorAll('[data-edit]').forEach((b) => b.addEventListener('click', async () => {
        const it = all.find((x) => x.id === b.dataset.edit);
        const nv = await inputDialog('改这条记忆（改动会上云）：', it ? it.text : '');
        if (nv == null) return;                     // 取消
        const r = pick(await post('/api/memory/edit',
          { store: curStore, id: b.dataset.edit, text: nv }));
        if (r.ok) { toast('已改，同步层会推上云'); load(); }
        else toast('没改成：' + (r.err || r._err || ''));
      }));
      box.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
        if (!await confirmDialog('删掉这条记忆？会同步到云端（墓碑账本，不会被增量拉取复活）。')) return;
        const r = pick(await post('/api/memory/delete',
          { store: curStore, id: b.dataset.del }));
        if (r.ok) { toast('已删除'); load(); }
        else toast('没删成：' + (r.err || r._err || ''));
      }));
    };

    async function load() {
      const r = pick(await get('/api/memory/list?store=' +
        encodeURIComponent(curStore) + '&limit=300'));
      all = r.items || [];
      const cnt = ctx.view.querySelector('#memCount');
      if (cnt) cnt.textContent = (r.total != null) ? r.total : (all.length || '—');
      if (r.ok === false && !all.length) {
        ctx.view.querySelector('#memList').innerHTML =
          errBox('读不到记忆', r._err || '大脑需要 M5 的 /api/memory/list（重启大脑后生效）');
        return;
      }
      drawList();
    }
    ctx.view.querySelector('#memQ').addEventListener('input', drawList);
    ctx.view.querySelector('#memType').addEventListener('change', drawList);
    await loadStores();
    await load();

    ctx.panel.querySelectorAll('.li').forEach((n) => n.addEventListener('click', () => {
      ctx.panel.querySelectorAll('.li').forEach((x) => x.classList.toggle('on', x === n));
      toast(n.textContent.includes('约定') ? '约定账本在聊天页右侧的「约定账本」卡里看' : '就是这一页');
    }));
  }

  // ═══ 4. 知识库 ═════════════════════════════════════════════
  async function renderKb(ctx) {
    ctx.topbar.className = 'topbar';
    const subs = [['kbdocs', '文档', 'lib'], ['kbsearch', '检索测试', 'search'], ['kbset', '索引设置', 'set']];
    ctx.panel.innerHTML = '<div class="phead"><h2>知识库</h2></div>' +
      '<div class="plist"><div>' +
      subs.map(([k, n, ic], i) => li({ icon: ic, title: n, on: i === 0, attrs: ' data-k="' + k + '"' })).join('') +
      '<div id="kbPanel"></div></div></div>';

    ctx.topbar.innerHTML = topbar('知识库', '', '<button class="btn oline" id="kbRe">重建索引</button>');
    ctx.view.innerHTML = '<div class="wrap" id="kbView"></div>';
    const v = ctx.view.querySelector('#kbView');
    const pnl = ctx.panel.querySelector('#kbPanel');

    async function docs() {
      const s = pick(await get('/api/plugin/kb/stats'));
      const d = pick(await get('/api/plugin/kb/docs'));
      pnl.innerHTML = (d.items || []).length
        ? d.items.map((x) => li({ icon: 'book', title: x.name, sub: x.chunks + ' 块', meta: bytes(x.size) })).join('')
        : empty('还没有文档', '把 .md/.txt 丢进 brain/data/kb/ 或调 ingest');
      v.innerHTML =
        '<div class="grid g4" style="margin-bottom:14px">' +
        '<div class="card"><div class="muted" style="font-size:12px">文档</div><div style="font-size:22px;font-weight:600">' + esc(s.docs || 0) + '</div></div>' +
        '<div class="card"><div class="muted" style="font-size:12px">切块</div><div style="font-size:22px;font-weight:600">' + esc(s.count || 0) + '</div></div>' +
        '<div class="card"><div class="muted" style="font-size:12px">维度</div><div style="font-size:22px;font-weight:600">' + esc(s.dim || '—') + '</div></div>' +
        '<div class="card"><div class="muted" style="font-size:12px">模型</div><div style="font-size:14px;font-weight:600;margin-top:4px">' + esc(s.model || '—') + '</div></div>' +
        '</div>' +
        (s.ok === false ? errBox('知识库插件没响应', s._err || '确认 brain/plugins/kb.py 已加载') : '') +
        '<div class="card hd"><div class="h"><h3>文档</h3><div class="sp"></div>' +
        '<span class="muted" style="font-size:11.5px">分区：' + esc(s.scope || '') + ' · 目录 ' + esc(s.dir || '') + '</span></div>' +
        '<table class="tbl"><tr><th>文件</th><th>切块</th><th>加入时间</th><th>操作</th></tr>' +
        ((d.items || []).length ? d.items.map((x) =>
          '<tr><td>' + esc(x.name) + '</td><td class="mono">' + esc(x.chunks) + '</td>' +
          '<td class="mono muted">' + esc(x.added_at || '') + '</td>' +
          '<td><button class="btn" data-del="' + esc(x.name) + '">删除</button></td></tr>').join('')
          : '<tr><td colspan="4">' + empty('知识库是空的') + '</td></tr>') +
        '</table></div>';
      v.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
        if (!await confirmDialog('删除「' + b.dataset.del + '」的全部块？')) return;
        await post('/api/plugin/kb/delete', { name: b.dataset.del });
        docs();
      }));
    }

    async function search() {
      v.innerHTML = '<div class="card" style="margin-bottom:14px"><div style="display:flex;gap:8px">' +
        '<input class="input" id="kbQ" placeholder="问一句话，看知识库里能翻出什么…" style="flex:1">' +
        '<button class="btn fill" id="kbGo">检索</button></div>' +
        '<div class="muted" style="font-size:11.5px;margin-top:8px">这里门槛设 0，方便看全貌；对话里注入用的是 0.45。</div></div>' +
        '<div id="kbRes"></div>';
      const go = async () => {
        const q = v.querySelector('#kbQ').value.trim();
        if (!q) return;
        const res = v.querySelector('#kbRes');
        res.innerHTML = '<div class="muted" style="padding:12px">检索中…</div>';
        const r = pick(await get('/api/plugin/kb/search?q=' + encodeURIComponent(q) + '&k=5'));
        if (!r.ok) { res.innerHTML = errBox('检索失败', r._err || ''); return; }
        res.innerHTML = (r.items || []).length
          ? r.items.map((x) =>
            '<div class="card" style="margin-bottom:10px;background:var(--bg-sub)">' +
            '<div style="display:flex;gap:8px;align-items:center">' +
            tag(Number(x.score) >= 0.45 ? 'ok' : 'neutral', Number(x.score).toFixed(3)) +
            '<b style="font-size:12.5px">' + esc(x.source) + '</b>' +
            '<span class="muted" style="font-size:11.5px">第 ' + esc(x.chunk) + ' 块 · ' + esc(x.heading || '') + '</span></div>' +
            '<div style="font-size:12.5px;color:var(--text-2);margin-top:6px;white-space:pre-wrap">' + esc(String(x.text).slice(0, 400)) + '</div>' +
            '</div>').join('')
          : empty('没有命中', '换个说法，或先导入文档');
      };
      v.querySelector('#kbGo').addEventListener('click', go);
      v.querySelector('#kbQ').addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    }

    async function conf() {
      const s = pick(await get('/api/plugin/kb/stats'));
      const c = s.config || {};
      v.innerHTML = '<div class="card"><div class="grid g3">' +
        '<div><div class="field"><label>每块字数</label><input class="input" value="' + esc(c.chunk_size) + '" disabled></div>' +
        '<div class="field"><label>块间重叠</label><input class="input" value="' + esc(c.chunk_overlap) + '" disabled></div></div>' +
        '<div><div class="field"><label>嵌入后端</label><input class="input" value="' + esc(c.backend) + '" disabled></div>' +
        '<div class="field"><label>模型</label><input class="input" value="' + esc(c.model) + '" disabled></div></div>' +
        '<div><div class="row"><div class="lb"><b>每轮自动注入</b><span>关掉只留 kb_search 工具</span></div>' +
        '<button class="sw ' + (c.inject ? 'on' : '') + '" data-cfg="inject"></button></div>' +
        '<div class="row"><div class="lb"><b>注入条数</b></div><input class="input" style="width:70px" value="' + esc(c.top_k) + '" data-num="top_k"></div>' +
        '<div class="row"><div class="lb"><b>门槛</b></div><input class="input" style="width:70px" value="' + esc(c.min_score) + '" data-num="min_score"></div></div>' +
        '</div>' +
        '<div class="muted" style="font-size:11.5px;margin-top:8px">改后端或模型会让旧向量维度对不上，要重建索引。</div></div>';
      v.querySelectorAll('[data-cfg]').forEach((b) => b.addEventListener('click', async () => {
        const on = !b.classList.contains('on');
        b.classList.toggle('on', on);
        await post('/api/plugin/kb/config', { inject: on });
        toast(on ? '已开启每轮注入' : '已关闭每轮注入');
      }));
      v.querySelectorAll('[data-num]').forEach((i) => i.addEventListener('change', async () => {
        const patch = {}; patch[i.dataset.num] = i.value;
        await post('/api/plugin/kb/config', patch);
        toast('已保存');
      }));
    }

    const draw = { kbdocs: docs, kbsearch: search, kbset: conf };
    ctx.panel.querySelectorAll('.li[data-k]').forEach((n) => n.addEventListener('click', () => {
      ctx.panel.querySelectorAll('.li').forEach((x) => x.classList.toggle('on', x === n));
      draw[n.dataset.k] && draw[n.dataset.k]();
    }));
    ctx.topbar.querySelector('#kbRe').addEventListener('click', async () => {
      toast('重建中…');
      const r = pick(await post('/api/plugin/kb/reindex', {}));
      toast(r.ok ? '重建完成：' + (r.chunks || 0) + ' 块' : '重建失败');
      docs();
    });
    docs();
  }

  // ═══ 5. 生活 ═══════════════════════════════════════════════
  async function renderLife(ctx) {
    ctx.topbar.className = 'topbar';
    // 2026-10-08：日记与朋友圈模块已在 brain 侧整体移除，这里只剩快递
    const subs = [['pkgs', '快递', 'bell']];
    ctx.panel.innerHTML = '<div class="phead"><h2>生活</h2></div>' +
      '<div class="plist"><div>' +
      subs.map(([k, n, ic], i) => li({ icon: ic, title: n, on: i === 0, attrs: ' data-k="' + k + '"' })).join('') +
      '<div id="lifePanel"></div></div></div>';
    ctx.topbar.innerHTML = topbar('生活面板', '快递跟踪');
    ctx.view.innerHTML = '<div class="wrap" id="lifeView"></div>';
    const v = ctx.view.querySelector('#lifeView');
    const pnl = ctx.panel.querySelector('#lifePanel');

    async function pkgs() {
      const p = pick(await get('/api/packages'));
      const items = p.items || [];
      pnl.innerHTML = '<div class="gtitle">' + esc(items.length) + ' 个单号</div>';
      v.innerHTML = items.length ? '<div class="card hd"><table class="tbl">' +
        '<tr><th>单号</th><th>承运商</th><th>状态</th><th>最新</th><th>备注</th><th></th></tr>' +
        items.map((x) => '<tr><td class="mono">' + esc(x.number) + '</td><td>' + esc(x.carrier || '') + '</td>' +
          '<td>' + tag(/签收|完成/.test(x.status || '') ? 'ok' : /异常|失败/.test(x.status || '') ? 'err' : 'neutral', x.status || '') + '</td>' +
          '<td class="muted">' + esc(String(x.latest || '').slice(0, 40)) + '</td>' +
          '<td class="muted">' + esc(x.note || '') + '</td>' +
          '<td><button class="btn" data-del="' + esc(x.number) + '">删</button></td></tr>').join('') +
        '</table></div>' : empty('没有在跟踪的快递', '在聊天里发单号会自动加进来');
      v.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
        await post('/api/packages/del', { number: b.dataset.del });
        pkgs();
      }));
    }

    const draw = { pkgs };
    ctx.panel.querySelectorAll('.li[data-k]').forEach((n) => n.addEventListener('click', () => {
      ctx.panel.querySelectorAll('.li').forEach((x) => x.classList.toggle('on', x === n));
      draw[n.dataset.k] && draw[n.dataset.k]();
    }));
    pkgs();
  }

  // ═══ 6. 能力 ═══════════════════════════════════════════════
  // 插件 / 技能管理，一屏一页：运行环境（技能）、添加区（拖 zip）、
  // 已装清单（每行：名字 + 状态 + 说明 + 能力明细 + 渠道开关 + 卸载）。
  // 渠道清单来自服务端（paths.CHANNELS 的真源），不在前端写死。
  async function renderAbility(ctx) {
    ctx.topbar.className = 'topbar';
    const subs = [['pl', '插件', 'plug'], ['sk', '技能', 'puzzle'], ['ft', '开关', 'set']];
    ctx.panel.innerHTML = '<div class="phead"><h2>能力</h2></div><div class="plist">' +
      subs.map(([k, n, ic], i) => li({ icon: ic, title: n, on: i === 0, attrs: ' data-k="' + k + '"' })).join('') +
      '</div>';
    ctx.topbar.innerHTML = topbar('能力与技能', '插件 · 技能 · 功能开关',
      '<button class="btn oline" id="abRe">' + icon('refresh', 'sm') + '刷新</button>');
    ctx.view.innerHTML = '<div class="wrap" id="abView"></div>';
    const v = ctx.view.querySelector('#abView');
    ctx.topbar.querySelector('#abRe').addEventListener('click', () => ctx.refresh());

    // 通用：文件 → base64（不带 data: 前缀，服务端两处都直接收）
    function fileToB64(file) {
      return new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result).split(',', 2)[1] || '');
        r.onerror = () => reject(new Error('读不了这个文件'));
        r.readAsDataURL(file);
      });
    }

    // 通用：拖拽上传区（点选 / 拖文件都行）
    function bindDrop(el, onZip) {
      if (!el) return;
      el.addEventListener('click', () => {
        const inp = document.createElement('input');
        inp.type = 'file';
        inp.accept = '.zip';
        inp.onchange = () => { if (inp.files && inp.files[0]) onZip(inp.files[0]); };
        inp.click();
      });
      el.addEventListener('dragover', (e) => { e.preventDefault(); el.classList.add('over'); });
      el.addEventListener('dragleave', () => el.classList.remove('over'));
      el.addEventListener('drop', (e) => {
        e.preventDefault();
        el.classList.remove('over');
        const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        if (f) onZip(f);
      });
    }
    async function installZip(file, url, body, kind) {
      if (!/\.zip$/i.test(file.name)) { toast('要 .zip 包'); return; }
      toast('正在装 ' + file.name + ' …');
      const b64 = await fileToB64(file);
      const r = pick(await post(url, Object.assign({ zip_b64: b64 }, body || {})));
      if (!r.ok) { toast('装失败：' + (r._err || r.err || '')); return; }
      // C1：插件和技能的重启提示不能混用同一句话 —— 两者的"重启"根本不是
      // 同一个东西。插件要重启**大脑**（start() 起的线程不会自己换掉），
      // 技能要重启**技能宿主**（热重载已让它当下可用，覆盖安装后才需要宿主
      // 进程重启一次才完全干净）。说错对象，用户会去重启错的东西，
      // 然后以为问题已经解决。
      const RN = {
        plugin: '有后台线程的插件要重启大脑才完全生效',
        skill: '覆盖了已有技能，重启技能宿主后才完全干净',
      };
      toast('已装上（共 ' + (r.count || '?') + ' 个）' +
        (r.restart_needed ? ' · ' + (RN[kind] || RN.plugin) : ''));
      ctx.refresh();
    }

    // 渠道小圆片：点亮 = 生效；点一下切换（channelable=false 的插件永远开，置灰）
    // 服务端的渠道状态可能是 {'app':true,...} 也可能是数组，这里都接
    function chanOn(cur, c) {
      if (!cur) return false;
      if (Array.isArray(cur)) return cur.includes(c);
      return !!cur[c];
    }
    function chanList(cur) {
      if (Array.isArray(cur)) return cur.slice();
      return Object.keys(cur || {}).filter((k) => cur[k]);
    }
    function chanChips(chans, cur, name, dis) {
      const CN = { app: 'App', wechat: '微信', wechat_group: '微信群', pc: '本机' };
      return '<div class="pkg-ch"><span class="muted">生效于</span>' +
        (chans || []).map((c) =>
          '<button class="chip' + (chanOn(cur, c) ? ' on' : '') + (dis ? ' dis' : '') +
          '" data-ch="' + esc(c) + '" data-p="' + esc(name) + '"' +
          (dis ? ' disabled title="这个插件不声明 FEATURE，契约上永远开，不能按渠道关"' :
                 ' title="点一下切换在 ' + esc(CN[c] || c) + ' 是否生效"') + '>' +
          esc(CN[c] || c) + '</button>').join('') + '</div>';
    }
    function bindChanChips(url, kind) {
      v.querySelectorAll('.chip[data-ch]:not(.dis)').forEach((b) => b.addEventListener('click', async () => {
        const p = b.dataset.p, c = b.dataset.ch;
        const item = (v.__items || []).find((x) => (x.name === p) || (x.id === p)) || {};
        const chans = chanList(item.channels);
        const i = chans.indexOf(c);
        if (i >= 0) chans.splice(i, 1); else chans.push(c);
        b.disabled = true;
        const body = kind === 'skill' ? { skill: p, channels: chans } : { name: p, channels: chans };
        const r = pick(await post(url, body));
        b.disabled = false;
        if (!r.ok) { toast('没改成功：' + (r._err || r.err || '')); return; }
        item.channels = (r.channels !== undefined) ? r.channels : chans;
        drawCurrent();
      }));
    }

    let drawCurrent = () => {};

    // ── 插件 ──
    async function plugins() {
      const r = pick(await get('/api/plugins'));
      const m = r.manage || {};
      const chans = m.channels || ['app', 'wechat', 'wechat_group', 'pc'];
      const items = m.items || [];
      v.__items = items;
      // C3：持久的"待重启"提示。判据是服务端磁盘上的两个时间戳
      //（改动时刻 vs 本进程启动时刻），不是 toast、也不是前端变量 ——
      // 所以刷新页面、重启客户端之后这条都还在，直到大脑真的重启过。
      // 服务端算不出来时（老大脑没这个字段）就不显示，不猜。
      const pend = r.pending_restart || {};
      const pendOn = !!pend.pending;
      const pendWhy = (pend.reasons || []).filter(Boolean).join('；');
      v.innerHTML =
        (pendOn ? '<div class="card" style="margin-bottom:12px;border-color:var(--warn)">' +
          '<div style="display:flex;align-items:center;gap:8px"><b>有改动还没生效</b>' +
          tag('warn', '待重启大脑') + '</div>' +
          '<div class="muted" style="font-size:12px;margin-top:6px">' +
          esc(pendWhy || '插件目录有改动') +
          '。上面这份清单是磁盘上的样子，内存里跑的可能还是旧的那份 —— ' +
          '重启大脑后两边才一致。</div></div>' : '') +
        '<div class="sech"><b>添加插件</b><div class="sp"></div><span class="muted">zip 上传</span></div>' +
        '<div class="drop" id="plDrop">' + icon('upload', 'md') +
        '<b>把插件 .zip 拖到这里</b><span class="muted">或点击选择文件 · 装上自动热重载，不用重启大脑</span></div>' +
        '<div class="sech"><b>已安装的插件</b><div class="sp"></div>' +
        '<span class="tag neutral">' + items.length + ' 个</span>' +
        (pendOn ? tag('warn', '待重启生效') : '') +
        '<button class="btn oline" id="plReload">重载</button></div>' +
        (r.ok === false && !items.length ? errBox('读不到插件清单', r._err || '') :
         items.length ? items.map((p) =>
          '<div class="pkg">' +
          '<div class="pkg-ic">' + icon('plug', 'sm') + '</div>' +
          '<div class="pkg-main">' +
          '<div class="pkg-h"><b>' + esc(p.name) + '</b>' + (p.err ? tag('err', '装载失败') : tag('ok', '运行中')) + '</div>' +
          '<div class="pkg-desc">' + esc(p.desc || '') + '</div>' +
          '<div class="pkg-meta"><span class="mono">' + esc(p.file || '') + '</span>' +
          ((p.tools || []).length ? '<span>· 工具 ' + esc(p.tools.join('、')) + '</span>' : '') +
          ((p.hooks || []).length ? '<span>· 钩子 ' + esc(p.hooks.join('、')) + '</span>' : '') +
          ((p.routes || []).length ? '<span>· 接口 ' + p.routes.length + ' 个</span>' : '') +
          '</div></div>' +
          '<div class="pkg-side">' +
          chanChips(chans, p.channels, p.name, !p.channelable) +
          '<button class="btn pkg-un" data-un="' + esc(p.name) + '">卸载</button>' +
          '</div></div>').join('')
          : empty('一个插件都没装', '上面拖一个 .zip 试试'));

      bindDrop(v.querySelector('#plDrop'), (f) => installZip(f, '/api/plugins/install', { source: '客户端' }, 'plugin'));
      v.querySelector('#plReload').addEventListener('click', async () => {
        const rr = pick(await post('/api/plugins/reload', {}));
        toast(rr.ok ? '已重载：' + (rr.count || '?') + ' 个插件' : '重载失败');
        plugins();
      });
      v.querySelectorAll('[data-un]').forEach((b) => b.addEventListener('click', async () => {
        if (!await confirmDialog('卸载插件 ' + b.dataset.un + '？')) return;
        // C2：卸载接口会回 restart_needed，必须消费。文件删掉只代表磁盘上
        // 没了，已经 import 的那份（连同它 start() 起的线程）还在跑 ——
        // 不告诉用户"要重启才真正停"，他就会以为线程也跟着没了。
        const rr = pick(await post('/api/plugins/uninstall', { name: b.dataset.un, purge_data: false }));
        if (!rr.ok) { toast('卸载失败：' + (rr._err || rr.err || '')); return; }
        toast(rr.restart_needed
          ? '文件已卸载，但它注册的后台线程还在跑 —— 要重启大脑才真正停止'
          : '已卸载（剩 ' + (rr.count != null ? rr.count : '?') + ' 个）');
        plugins();
      }));
      bindChanChips('/api/plugins/channels', 'plugin');
      drawCurrent = plugins;
    }

    // ── 技能 ──
    async function skills() {
      const r = pick(await post('/api/skill/status', {}));
      const list = r.skills || [];
      v.__items = list;
      // C3（技能侧）：判据同上，但"重启"指的是**技能宿主**（Node 进程），
      // 不是大脑。宿主自己启动时会登记 started_at，所以这条提示同样
      // 会一直显示到宿主真的重启过为止。
      const skPend = r.pending_restart || {};
      const skPendOn = !!skPend.pending;
      const skPendWhy = (skPend.reasons || []).filter(Boolean).join('；');
      v.innerHTML =
        (skPendOn ? '<div class="card" style="margin-bottom:12px;border-color:var(--warn)">' +
          '<div style="display:flex;align-items:center;gap:8px"><b>有改动还没生效</b>' +
          tag('warn', '待重启技能宿主') + '</div>' +
          '<div class="muted" style="font-size:12px;margin-top:6px">' +
          esc(skPendWhy || '技能目录有改动') +
          '。热重载已经让新版本能用，但要宿主进程重启一次才完全干净 —— ' +
          '上面这份清单是磁盘上的样子。</div></div>' : '') +
        '<div class="sech"><b>运行环境</b><div class="sp"></div>' +
        (r.alive ? tag('ok', '可以装') : tag('err', '宿主没起来，装了不生效')) + '</div>' +
        '<div class="env">' +
        '<div class="kv"><span>技能宿主</span><b>' + esc(r.host || '—') + (r.alive ? '' : '（没连上）') + '</b></div>' +
        '<div class="kv"><span>Node</span><b>' + esc(r.node || '未安装') + '</b></div>' +
        '<div class="kv"><span>技能目录</span><b class="mono" style="font-size:11.5px">' + esc(r.dir || '—') + '</b></div>' +
        '</div>' +
        '<div class="sech"><b>添加技能</b><div class="sp"></div><span class="muted">zip 上传</span></div>' +
        '<div class="drop" id="skDrop">' + icon('puzzle', 'md') +
        '<b>把技能 .zip 拖到这里</b><span class="muted">或点击选择文件 · 宿主自动热重载，不用重启大脑</span></div>' +
        '<div class="sech"><b>已安装的技能</b><div class="sp"></div>' +
        '<span class="tag neutral">' + list.length + ' 个</span>' +
        (skPendOn ? tag('warn', '待重启生效') : '') +
        '<button class="btn oline" id="skRe">重载宿主</button></div>' +
        (r.ok === false && !list.length ? errBox('读不到技能清单', r._err || '技能宿主（Node）没起来的话装了也不生效') :
         list.length ? list.map((s) =>
          '<div class="pkg">' +
          '<div class="pkg-ic">' + icon('puzzle', 'sm') + '</div>' +
          '<div class="pkg-main">' +
          '<div class="pkg-h"><b>' + esc(s.name || s.id) + '</b>' + tag('ok', '已装载') + '</div>' +
          '<div class="pkg-desc">' + esc(s.desc || '') + '</div>' +
          '<div class="pkg-meta"><span>' + esc((s.id || '') + (s.version ? ' v' + s.version : '')) + '</span>' +
          '<span>· 工具 ' + ((s.tools || []).length) + ' 个</span></div>' +
          ((s.tools || []).length ? '<div class="pkg-tools">' +
            s.tools.map((t) => '<span class="toolchip mono" title="' + esc(t.name) + '">' +
              esc(t.label || t.name) + '</span>').join('') + '</div>' : '') +
          '</div>' +
          '<div class="pkg-side">' +
          chanChips(r.channels, s.channels, s.id || s.name, false) +
          '<button class="btn pkg-un" data-sun="' + esc(s.id || s.name) + '">卸载</button>' +
          '</div></div>').join('')
          : empty('还没有装技能', '技能是 Node 里的独立程序，跟 .py 插件是两条通道'));

      bindDrop(v.querySelector('#skDrop'), (f) => installZip(f, '/api/skill/install', { note: '客户端' }, 'skill'));
      v.querySelector('#skRe').addEventListener('click', async () => {
        await post('/api/skill/reload', {});
        toast('宿主重载中…');
        setTimeout(skills, 1200);
      });
      v.querySelectorAll('[data-sun]').forEach((b) => b.addEventListener('click', async () => {
        if (!await confirmDialog('卸载技能 ' + b.dataset.sun + '？')) return;
        // C2（技能侧同理）：文件删了不等于宿主里那份也没了。
        const rr = pick(await post('/api/skill/uninstall', { skill: b.dataset.sun }));
        if (!rr.ok) { toast('卸载失败：' + (rr._err || rr.err || '')); return; }
        toast(rr.restart_needed
          ? '文件已卸载，宿主里那份可能还在 —— 重启技能宿主后才完全干净'
          : '已卸载');
        skills();
      }));
      bindChanChips('/api/skill/channels', 'skill');
      drawCurrent = skills;
    }

    // ── 功能开关 ──
    // 「说话多少」滑条按渠道调：App / 微信群 / 微信私聊的真身在云端，
    // 改动直达云端 config（1 档 = 只 @，由 gate 插件硬执行）；
    // 本机走本地 sidecar。以前只有一根滑条管本机，微信的根本调不到。
    async function features() {
      const f = pick(await get('/api/features'));
      const CHATS = [['app', 'App'], ['wechat_group', '微信群'], ['wechat', '微信私聊'], ['pc', '本机']];
      let curCh = 'wechat_group';   // 用户最关心的就是群
      const chName = () => (CHATS.find(([c]) => c === curCh) || ['', '?'])[1];
      const chBase = () => curCh === 'pc' ? null : 'cloud';
      const feats = f.features || {};
      const NAMES = {
        hotboard: '热搜接入', weather: '天气信息', weather_alert: '天气变化提醒',
        package: '快递监控', tools: '工具调用',
      };
      v.innerHTML =
        '<div class="card" style="margin-bottom:12px">' +
        '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:10px">' +
        '<div style="flex:1;min-width:180px"><b style="font-size:13.5px">说话多少</b>' +
        '<div class="muted" style="font-size:12px">1 档 = 只有被 @（或叫名字）才搭话；档位越高越爱插话</div></div>' +
        '<div class="seg" id="chSrc">' + CHATS.map(([c, n]) =>
          '<button data-ch="' + c + '"' + (c === curCh ? ' class="on"' : '') + '>' + n + '</button>').join('') + '</div>' +
        '</div>' +
        '<div style="display:flex;align-items:center;gap:12px">' +
        '<input type="range" min="1" max="10" value="5" id="chattyRange" style="flex:1">' +
        '<b id="chattyVal" style="font-size:20px;width:28px;text-align:right">…</b>' +
        '</div>' +
        '<div class="muted" style="font-size:11.5px;margin-top:6px" id="chattyNote"></div>' +
        '</div>' +
        '<div class="card hd"><table class="tbl"><tr><th>功能</th><th class="c">本机（pc）</th></tr>' +
        Object.keys(feats).map((k) => '<tr><td>' + esc(NAMES[k] || k) + '</td>' +
          '<td class="c"><button class="sw ' + (feats[k] ? 'on' : '') + '" data-f="' + esc(k) + '"></button></td></tr>').join('') +
        '</table></div>' +
        '<div class="muted" style="font-size:11.5px;margin-top:8px">开关按渠道存（features_by_src），这里是本机控制台（pc）渠道的开关，改完即时生效。App / 微信各渠道的在手机端调。</div>';

      const range = v.querySelector('#chattyRange');
      const valEl = v.querySelector('#chattyVal');
      const noteEl = v.querySelector('#chattyNote');

      async function loadLevel() {
        valEl.textContent = '…';
        const c = pick(await get('/api/chatty?src=' + curCh, { base: chBase() }));
        const lvl = c.level || 5;
        range.value = lvl;
        valEl.textContent = lvl;
        noteEl.textContent = curCh === 'pc'
          ? '本机控制台的档位，存在本地。'
          : '「' + chName() + '」的档位，存在云端大脑，改完下一句就生效。';
      }
      loadLevel();

      v.querySelectorAll('#chSrc button').forEach((b) => b.addEventListener('click', () => {
        if (curCh === b.dataset.ch) return;
        curCh = b.dataset.ch;
        v.querySelectorAll('#chSrc button').forEach((x) => x.classList.toggle('on', x === b));
        loadLevel();
      }));
      range?.addEventListener('input', () => { valEl.textContent = range.value; });
      range?.addEventListener('change', async () => {
        const lvl = Number(range.value);
        const r = pick(await post('/api/chatty', { level: lvl, src: curCh }, { base: chBase() }));
        if (!r.ok && r._status === 0) { toast('保存失败：' + (r._err || '数据源不可达')); loadLevel(); return; }
        toast('「' + chName() + '」说话多少：' + lvl + (lvl <= 1 ? '（只 @）' : ''));
      });

      v.querySelectorAll('[data-f]').forEach((b) => b.addEventListener('click', async () => {
        const on = !b.classList.contains('on');
        b.classList.toggle('on', on);
        const patch = {}; patch[b.dataset.f] = on;
        const r = pick(await post('/api/features', patch));
        if (!r.ok) { b.classList.toggle('on', !on); toast('保存失败'); }
      }));
    }

    const draw = { pl: plugins, sk: skills, ft: features };
    window.__vigilRefreshExtensions = () => {
      if (!document.querySelector('#abView')) return;
      const active = ctx.panel.querySelector('.li.on')?.dataset.k || 'pl';
      if (draw[active]) draw[active]();
    };
    ctx.panel.querySelectorAll('.li[data-k]').forEach((n) => n.addEventListener('click', () => {
      ctx.panel.querySelectorAll('.li').forEach((x) => x.classList.toggle('on', x === n));
      draw[n.dataset.k] && draw[n.dataset.k]();
    }));
    plugins();
  }

  // ═══ 7. 数据 ═══════════════════════════════════════════════
  async function renderData(ctx) {
    ctx.topbar.className = 'topbar';
    const subs = [['ov', '概览'], ['tok', 'Token 分析'], ['tr', '对话 Trace'], ['hi', '聊天历史'], ['lg', '日志']];
    ctx.panel.innerHTML = '<div class="phead"><h2>数据</h2></div><div class="plist">' +
      subs.map(([k, n], i) => li({ icon: k === 'ov' ? 'chart' : k === 'tok' ? 'activity' : k === 'lg' ? 'book' : k === 'tr' ? 'term' : 'chat', title: n, on: i === 0, attrs: ' data-k="' + k + '"' })).join('') +
      '</div>';
    // ── 云端 / 本机 双数据源 ──
    // 微信真身的 stats/trace/history/log 账都在云端大脑（8788），本地 sidecar
    // 只有 pc 渠道的账 —— 这就是数据页曾经全 0 的根因。已配云端（设置 →
    // 云端同步）时默认看云端，没配则只有本机、不显示切换。
    // 🔴 state.cfg 启动流程里没人填（loadCfg 只在设置页保存后被调），
    // 直接从主进程拿实时配置 —— 用 state.cfg 的话 hasCloud 恒 false，
    // 切换永远不出现、永远看本机的零（2026-10-09 实锤过）。
    const live = await (global.vigil ? global.vigil.cfg.get() : Promise.resolve({}));
    const sync = (live && live.sync) || {};
    const hasCloud = !!(sync.cloud_url && sync.cloud_token);
    if (!state.dataSrc) state.dataSrc = hasCloud ? 'cloud' : 'local';
    const dget = (p) => get(p, { base: state.dataSrc === 'cloud' ? 'cloud' : null, timeoutMs: 8000 });
    const dpost = (p, b) => post(p, b, { base: state.dataSrc === 'cloud' ? 'cloud' : null, timeoutMs: 8000 });
    const srcSeg = () => !hasCloud ? '' :
      '<div class="seg" id="dSrc"><button data-ds="cloud"' + (state.dataSrc === 'cloud' ? ' class="on"' : '') + '>云端</button>' +
      '<button data-ds="local"' + (state.dataSrc === 'local' ? ' class="on"' : '') + '>本机</button></div>';
    // 数据源够不着（云端没配 / 大脑离线）时不渲染一堆假 0，给一句能看懂的话
    const fail = (r) => {
      if (r && r.ok) return false;
      v.innerHTML = '<div class="wrap">' + empty(state.dataSrc === 'cloud' ? '云端数据源不可达' : '本机数据源不可达',
        (r && (r._err || (r._status ? 'HTTP ' + r._status : ''))) || '') + '</div>';
      return true;
    };
    ctx.topbar.innerHTML = topbar('概览', '', srcSeg() + '<button class="btn oline" id="dRe">' + icon('refresh', 'sm') + '刷新</button>');
    ctx.view.innerHTML = '<div class="wrap" id="dView"></div>';
    const v = ctx.view.querySelector('#dView');
    const cur = () => ctx.topbar.querySelector('h1').textContent;

    let dataRenderId = 0;
    async function ov() {
      const renderId = ++dataRenderId;
      const isCurrent = () => renderId === dataRenderId && v.isConnected;
      ctx.topbar.querySelector('h1').textContent = '概览';
      v.innerHTML = '<div class="muted" role="status" aria-live="polite" style="padding:20px 0">正在读取数据…</div>';
      const s = pick(await dget('/api/stats'));
      if (!isCurrent()) return;
      if (fail(s)) return;
      const tIn = Number(s.tok_in) || 0, tOut = Number(s.tok_out) || 0;
      v.innerHTML = '<div class="grid g4" style="margin-bottom:14px">' +
        '<div class="card kpi"><div class="lb">今日轮次</div><div class="v">' + esc(fmtNum(s.turns || 0)) + '</div></div>' +
        '<div class="card kpi"><div class="lb">今日 Token（入+出）</div><div class="v">' + esc(fmtNum(tIn + tOut)) + '</div>' +
        '<div class="sub">入 ' + esc(fmtNum(tIn)) + ' · 出 ' + esc(fmtNum(tOut)) + '</div></div>' +
        '<div class="card kpi"><div class="lb">平均响应</div><div class="v">' + esc(s.avg_ms ? (s.avg_ms / 1000).toFixed(1) + 's' : '—') + '</div></div>' +
        '<div class="card kpi"><div class="lb">降级</div><div class="v">' + esc(fmtNum(s.fallback || 0)) + '</div></div>' +
        '</div>' +
        '<div class="grid g3"><div class="card" style="grid-column:span 2"><b style="font-size:13.5px">上下文块分布</b>' +
        '<div class="muted" style="font-size:12px;margin-top:8px">每轮的上下文由风格约束 / 历史摘要 / 人设 / 约定账本 / 长期记忆拼成，具体字数看 Trace 里的 blocks。</div></div>' +
        '<div class="card"><b style="font-size:13.5px">系统状态</b>' +
        '<div class="kv" style="margin-top:8px"><span>模型</span><b>' + esc(s.model || '—') + '</b></div>' +
        '<div class="kv"><span>语音</span><b>' + esc(s.voice || '未启用') + '</b></div>' +
        '<div class="kv"><span>约定账本</span><b>' + esc((s.agenda && s.agenda.total) || 0) + ' 条</b></div>' +
        '</div></div>' +
        '<div class="card" style="margin-top:14px;padding:12px 14px;display:flex;align-items:center;gap:10px">' +
        icon('activity', 'sm') +
        '<div style="font-size:12.5px">趋势和逐轮明细在<b>「Token 分析」</b>：折线图（按天 / 按轮次）+ 精确合计。</div>' +
        '</div>';
    }

    // Token 分析：折线趋势（按天 / 按轮次）+ 两个口径的精确合计。
    // 口径说清楚：「今日」来自服务端账本 /api/stats（按自然日）；
    // 「窗口」是 /api/trace 逐轮 tok_in/tok_out 的精确求和 —— 两者不该混着说。
    async function tok() {
      const renderId = ++dataRenderId;
      const isCurrent = () => renderId === dataRenderId && v.isConnected;
      ctx.topbar.querySelector('h1').textContent = 'Token 分析';
      v.innerHTML = '<div class="wrap"><div class="muted" style="padding:20px 0">加载中…</div></div>';
      const [s, r] = await Promise.all([dget('/api/stats'), dget('/api/trace?last=500')]);
      if (!isCurrent()) return;
      const st = pick(s);
      if (fail(st)) return;
      const items = (pick(r).items || []).slice()
        .filter((x) => x && x.t != null)
        .sort((a, b) => toMs(a.t) - toMs(b.t));
      const C_IN = 'var(--accent)', C_OUT = 'var(--ok)';

      // 逐轮精确求和：只数带 token 上报的轮，缺字段的轮跳过（宁缺毋错）
      let nTok = 0, sIn = 0, sOut = 0;
      const turns = [];
      items.forEach((x) => {
        if (x.tok_in == null && x.tok_out == null) return;
        const a = Number(x.tok_in) || 0, b = Number(x.tok_out) || 0;
        nTok++; sIn += a; sOut += b;
        turns.push({ t: toMs(x.t), in: a, out: b });
      });

      // 按天聚合：key 用本地日期，x 落在当天 0 点
      const byDay = new Map();
      turns.forEach((p) => {
        const d = new Date(p.t);
        const key = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
        if (!byDay.has(key)) byDay.set(key, { t: new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime(), in: 0, out: 0 });
        const day = byDay.get(key);
        day.in += p.in; day.out += p.out;
      });
      const dayPts = Array.from(byDay.values()).sort((a, b) => a.t - b.t);
      const spanDays = dayPts.length ? Math.round((dayPts[dayPts.length - 1].t - dayPts[0].t) / 86400000) + 1 : 0;
      const spanMs = turns.length ? turns[turns.length - 1].t - turns[0].t : 0;
      const xf = spanMs > 86400000 ? 'full' : 'time';

      const mkSeries = (mode) => mode === 'day'
        ? [
            { name: 'Token 入', color: C_IN, points: dayPts.map((d) => ({ x: d.t, y: d.in })), area: true },
            { name: 'Token 出', color: C_OUT, points: dayPts.map((d) => ({ x: d.t, y: d.out })) },
          ]
        : [
            { name: 'Token 入', color: C_IN, points: turns.map((p) => ({ x: p.t, y: p.in })) },
            { name: 'Token 出', color: C_OUT, points: turns.map((p) => ({ x: p.t, y: p.out })) },
          ];

      const stIn = Number(st.tok_in) || 0, stOut = Number(st.tok_out) || 0;
      v.innerHTML = '<div class="wrap">' +
        '<div class="grid g4" style="margin-bottom:14px">' +
        '<div class="card kpi"><div class="lb">今日 Token（服务端账本）</div><div class="v">' + esc(fmtNum(stIn + stOut)) + '</div>' +
        '<div class="sub">入 ' + esc(fmtNum(stIn)) + ' · 出 ' + esc(fmtNum(stOut)) + '</div></div>' +
        '<div class="card kpi"><div class="lb">今日轮次</div><div class="v">' + esc(fmtNum(st.turns || 0)) + '</div></div>' +
        '<div class="card kpi"><div class="lb">窗口轮数（带上报）</div><div class="v">' + esc(fmtNum(nTok)) + '</div>' +
        '<div class="sub">最近 500 轮里共 ' + esc(fmtNum(items.length)) + ' 轮</div></div>' +
        '<div class="card kpi"><div class="lb">窗口 Token 合计</div><div class="v">' + esc(fmtNum(sIn + sOut)) + '</div>' +
        '<div class="sub">入 ' + esc(fmtNum(sIn)) + ' · 出 ' + esc(fmtNum(sOut)) + '</div></div>' +
        '</div>' +
        '<div class="card" style="padding:14px">' +
        '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
        '<b style="font-size:13.5px">Token 用量折线</b>' +
        '<span class="muted" style="font-size:11.5px">悬浮看每轮 / 每天明细</span>' +
        '<div style="flex:1"></div>' +
        '<div class="seg" id="tokSeg"><button data-sub="day"' + (spanDays >= 2 ? ' class="on"' : '') + '>按天</button>' +
        '<button data-sub="turn"' + (spanDays >= 2 ? '' : ' class="on"') + '>按轮次</button></div>' +
        '</div>' +
        '<div class="legend" style="margin:8px 0 4px" id="tokLegend"></div>' +
        '<div id="tokChart"></div>' +
        '<div class="muted" style="font-size:11.5px;margin-top:8px" id="tokNote"></div>' +
        '</div></div>';

      const chartEl = v.querySelector('#tokChart');
      const legendEl = v.querySelector('#tokLegend');
      const noteEl = v.querySelector('#tokNote');
      let mode = spanDays >= 2 ? 'day' : 'turn';

      function render() {
        const series = mkSeries(mode);
        U.lineChart(chartEl, {
          series,
          height: 260,
          xFmt: (ms) => (mode === 'day' ? U.fmtTime(ms, 'day') : U.fmtTime(ms, xf)),
          tipTitle: (ms) => U.fmtTime(ms, mode === 'day' ? 'day' : 'full'),
        });
        legendEl.innerHTML =
          '<span><span class="dot" style="background:' + C_IN + '"></span>Token 入 · 窗口 ' + esc(fmtNum(sIn)) + '</span>' +
          '<span><span class="dot" style="background:' + C_OUT + '"></span>Token 出 · 窗口 ' + esc(fmtNum(sOut)) + '</span>';
        noteEl.textContent = '统计窗口：最近 500 轮中带 token 上报的 ' + nTok + ' 轮' +
          (turns.length ? '（' + U.fmtTime(turns[0].t, xf) + ' → ' + U.fmtTime(turns[turns.length - 1].t, xf) + '）' : '') +
          '。窗口合计为逐轮精确求和；「今日」按自然日，来自服务端账本，两者口径不同。' +
          (items.length - nTok > 0 ? '另有 ' + (items.length - nTok) + ' 轮没有 token 上报，未计入。' : '');
      }

      v.querySelectorAll('#tokSeg button').forEach((b) => b.addEventListener('click', () => {
        v.querySelectorAll('#tokSeg button').forEach((x) => x.classList.toggle('on', x === b));
        if (mode !== b.dataset.sub) { mode = b.dataset.sub; render(); }
      }));
      render();
    }

    async function tr() {
      const renderId = ++dataRenderId;
      const isCurrent = () => renderId === dataRenderId && v.isConnected;
      ctx.topbar.querySelector('h1').textContent = '对话 Trace';
      v.innerHTML = '<div class="muted" role="status" aria-live="polite" style="padding:20px 0">正在读取 Trace…</div>';
      const r = pick(await dget('/api/trace?last=60'));
      if (!isCurrent()) return;
      if (fail(r)) return;
      const items = r.items || [];
      if (!items.length) { v.innerHTML = empty('还没有 Trace', '聊几句就有了'); return; }
      // 合计只数带 token 上报的轮次，缺字段的轮不掺进总数里（宁缺毋错）
      let nTok = 0, sIn = 0, sOut = 0, sMs = 0, nMs = 0, sLen = 0;
      items.forEach((x) => {
        if (x.tok_in != null || x.tok_out != null) { nTok++; sIn += Number(x.tok_in) || 0; sOut += Number(x.tok_out) || 0; }
        if (x.in_len != null) sLen += Number(x.in_len) || 0;
        if (x.ms) { nMs++; sMs += Number(x.ms) || 0; }
      });
      v.innerHTML = '<div class="card hd"><table class="tbl">' +
        '<tr><th>时间</th><th>来源</th><th class="num">输入</th><th class="num">Token 入</th><th class="num">Token 出</th><th class="num">耗时</th><th class="num">记忆</th><th>模型</th></tr>' +
        items.map((x) => '<tr><td class="mono muted">' + esc(fmtTime(x.t, 'time')) + '</td>' +
          '<td>' + esc(x.src || '') + '</td>' +
          '<td class="num">' + esc(x.in_len == null ? '—' : fmtNum(x.in_len)) + '</td>' +
          '<td class="num">' + esc(x.tok_in == null ? '—' : fmtNum(x.tok_in)) + '</td>' +
          '<td class="num">' + esc(x.tok_out == null ? '—' : fmtNum(x.tok_out)) + '</td>' +
          '<td class="num mono">' + esc(x.ms ? (x.ms / 1000).toFixed(1) + 's' : '—') + '</td>' +
          '<td class="num">' + esc(x.mem_hits == null ? '—' : x.mem_hits) + '</td>' +
          '<td class="muted">' + esc(x.model || '') + '</td></tr>').join('') +
        '<tr class="tsum"><td>合计</td><td class="muted">' + items.length + ' 轮</td>' +
        '<td class="num">' + esc(fmtNum(sLen)) + '</td>' +
        '<td class="num">' + esc(fmtNum(sIn)) + '</td>' +
        '<td class="num">' + esc(fmtNum(sOut)) + '</td>' +
        '<td class="num mono">' + esc(nMs ? (sMs / nMs / 1000).toFixed(1) + 's' : '—') + '</td>' +
        '<td></td><td class="muted">均耗时</td></tr>' +
        '</table>' +
        '<div class="muted" style="font-size:11.5px;padding:8px 12px">带 token 上报的 ' + nTok + ' 轮已计入合计；「—」表示那一轮服务端没有上报该字段，不掺进总数。</div>' +
        '</div>';
    }

    async function hi() {
      const renderId = ++dataRenderId;
      const isCurrent = () => renderId === dataRenderId && v.isConnected;
      ctx.topbar.querySelector('h1').textContent = '聊天历史';
      // 控制台视角：本机是自己的会话，App / 微信的往来在这儿只读查看
      // （云端视角下微信群聊的账在 wechat_group 分区，也得能看）
      const CHANS = [['pc', '本机'], ['app', 'App'], ['wechat', '微信'], ['wechat_group', '微信群']];
      v.innerHTML = '<div class="seg" style="margin-bottom:12px">' +
        CHANS.map(([c, n], i) => '<button data-hc="' + c + '"' + (i === 0 ? ' class="on"' : '') + '>' + n + '</button>').join('') +
        '</div><div id="hcList"></div>';
      const box = v.querySelector('#hcList');
      let curHC = 'pc';
      let historyRequestId = 0;
      const load = async () => {
        const requestId = ++historyRequestId;
        box.innerHTML = '<div class="muted" role="status" aria-live="polite" style="padding:12px 0">正在读取聊天历史…</div>';
        const r = pick(await dget('/api/history?limit=200&src=' + curHC));
        if (!isCurrent() || requestId !== historyRequestId) return;
        if (fail(r)) return;
        const items = r.items || [];
        box.innerHTML = items.length ? '<div class="card" style="padding:14px">' +
          items.map((m) => {
            const mine = ['me', 'user', 'self'].includes(String(m.role || '').toLowerCase());
            const t = mine ? esc(m.text) : U.md(m.text);
            return '<div class="turn ' + (mine ? 'me' : '') + '" style="margin-bottom:12px">' +
              '<div class="bdy"><div class="txt' + (mine ? '' : ' md') + '">' + t + '</div>' +
              '<div class="foot"><span>' + esc(fmtTime(m.t, 'full')) + '</span></div></div></div>';
          }).join('') + '</div>' : empty('这个渠道还没有历史');
      };
      v.querySelectorAll('[data-hc]').forEach((b) => b.addEventListener('click', () => {
        v.querySelectorAll('[data-hc]').forEach((x) => x.classList.toggle('on', x === b));
        curHC = b.dataset.hc;
        load();
      }));
      load();
    }

    async function lg() {
      const renderId = ++dataRenderId;
      const isCurrent = () => renderId === dataRenderId && v.isConnected;
      ctx.topbar.querySelector('h1').textContent = '日志';
      v.innerHTML = '<div class="muted" role="status" aria-live="polite" style="padding:20px 0">正在读取日志…</div>';
      const r = pick(await dget('/api/logs?src=server&n=200'));
      if (!isCurrent()) return;
      if (fail(r)) return;
      const items = r.items || [];
      v.innerHTML = '<div class="card hd"><div class="h"><h3>服务端日志</h3><div class="sp"></div>' +
        '<button class="btn oline" id="lgClear">清空</button></div>' +
        (items.length ? items.map((x) => '<div class="row" style="padding:8px 14px">' +
          '<span class="muted mono" style="font-size:11.5px">' + esc(fmtTime(x.t, 'time')) + '</span>' +
          tag(x.level === 'ERROR' ? 'err' : x.level === 'WARN' ? 'warn' : 'neutral', x.level || 'INFO') +
          '<div class="lb"><span class="mono" style="font-size:12px">' + esc(String(x.msg || '').slice(0, 160)) + '</span></div>' +
          '</div>').join('') : empty('没有日志')) + '</div>';
      v.querySelector('#lgClear')?.addEventListener('click', async () => {
        if (!await confirmDialog('清空服务端日志？此操作不可恢复。')) return;
        await dpost('/api/logs/clear', { src: 'server' });
        lg();
      });
    }

    const draw = { ov, tok, tr, hi, lg };
    bindLi(ctx.panel, (k, n) => {
      ctx.panel.querySelectorAll('.li').forEach((x) => x.classList.toggle('on', x === n));
      draw[k] && draw[k]();
    });
    const reRender = () => {
      const k = { 概览: 'ov', 'Token 分析': 'tok', '对话 Trace': 'tr', 聊天历史: 'hi', 日志: 'lg' }[cur()] || 'ov';
      draw[k]();
    };
    ctx.topbar.querySelector('#dRe').addEventListener('click', reRender);
    // 云端 ↔ 本机切换：按下后用新数据源重画当前子页
    ctx.topbar.querySelectorAll('#dSrc button').forEach((b) => b.addEventListener('click', () => {
      if (state.dataSrc === b.dataset.ds) return;
      state.dataSrc = b.dataset.ds;
      ctx.topbar.querySelectorAll('#dSrc button').forEach((x) => x.classList.toggle('on', x === b));
      reRender();
    }));
    ov();
  }

  // ═══ 8. 设置 ═══════════════════════════════════════════════
  async function renderSettings(ctx) {
    ctx.topbar.className = 'topbar';
    let modelRenderId = 0;
    // 本地操控不再是设置里的一页：确认走聊天气泡，权限/目录/工具开关
    // 收在聊天输入框旁的盾牌面板里（见 renderChat）。
    // 「已绑定设备」删了：云端 brain 没有 /api/device/*（点了就是 no such api）。
    const subs = [['conn', '连接与设备'], ['model', '模型管理'], ['wechat', '微信聊天集成'], ['sync', '云端同步'], ['danger', '权限与安全'], ['about', '关于']];
    ctx.panel.innerHTML = '<div class="phead"><h2>设置</h2></div><div class="plist">' +
      subs.map(([k, n], i) => li({ icon: k === 'conn' ? 'bell' : k === 'model' ? 'brain' : k === 'wechat' ? 'chat' : k === 'sync' ? 'heart' : k === 'danger' ? 'x' : 'set', title: n, on: i === 0, attrs: ' data-k="' + k + '"' })).join('') +
      '</div>';
    ctx.topbar.innerHTML = topbar('连接与设备', state.base || '');
    ctx.view.innerHTML = '<div class="wrap" id="sView"></div>';
    const v = ctx.view.querySelector('#sView');

    async function conn() {
      ctx.topbar.querySelector('h1').textContent = '连接与设备';
      const has = await (global.vigil ? global.vigil.token.has() : Promise.resolve(''));
      v.innerHTML =
        // 正常情况下这一页根本不该被看到：客户端启动时会自己找大脑并取 token。
        // 这里排第二，只在"自动探测没成功"时才需要人介入。
        '<div class="card" style="margin-bottom:14px" id="autoCard">' +
        '<div id="autoHint"></div>' +
        '<div style="display:flex;gap:8px;align-items:center;margin-top:10px">' +
        '<button class="btn fill" id="sAuto">自动探测</button>' +
        '<span class="muted" id="sAutoRes" style="font-size:12px">' +
        '本地模式只连接随包 Brain；远程模式只连接你明确选择的地址</span>' +
        '</div></div>' +

        '<div class="field"><label for="sMode">Brain 运行模式</label>' +
        '<select class="input" id="sMode" aria-describedby="sModeHelp"><option value="local"' + (state.cfg.connection_mode !== 'remote' ? ' selected' : '') + '>本地随包（推荐）</option><option value="remote"' + (state.cfg.connection_mode === 'remote' ? ' selected' : '') + '>远程（明确指定）</option></select>' +
        '<p class="muted" id="sModeHelp">本地自动启动并认证，不需要 IP、端口或 EXE。云端同步和微信桥可独立保留。</p></div>' +
        '<div class="card" style="margin-bottom:14px">' +
        '<div id="sRemoteFields"' + (state.cfg.connection_mode === 'remote' ? '' : ' hidden') + '><div class="field"><label for="sBase">远程 Brain 地址</label>' +
        '<input class="input mono" id="sBase" value="' + esc(state.cfg.remote_base || '') + '" placeholder="https://brain.example.com">' +
        '<div class="muted" style="font-size:11.5px">仅在远程模式使用，不会在本地启动失败时自动回退</div></div>' +
        '<div class="field"><label>Brain Token</label>' +
        '<input class="input" id="sToken" type="password" placeholder="' + (has ? '已保存（留空 = 不改）' : '还没配') + '">' +
        '<div class="muted" style="font-size:11.5px">存在系统钥匙串（keytar），渲染层拿不到它</div></div>' +
        '</div><div style="display:flex;gap:8px;align-items:center">' +
        '<button class="btn fill" id="sSave">保存并重连</button>' +
        '<button class="btn oline" id="sTest">测试连接</button>' +
        '<span class="muted" id="sRes" style="font-size:12px"></span></div></div>' +

        '<div class="sec">当前状态</div>' +
        '<div class="card">' +
        '<div class="kv"><span>连接</span><b>' + (state.conn.ok ? '正常 ' + state.conn.ms + ' ms' : '不通') + '</b></div>' +
        '<div class="kv"><span>地址</span><b class="mono">' + esc(state.cfg.base || '—') + '</b></div>' +
        '<div class="kv"><span>渠道</span><b>' + esc(state.src) + '（本机控制台，独立于 App / 微信）</b></div>' +
        '<div class="kv"><span>token</span><b>' + (has ? '已保存' : '未配置') + '</b></div>' +
        '<div class="kv"><span>凭据类型</span><b>' + (state.cfg.token_kind === 'device' ? '设备 token' : state.cfg.token_kind === 'brain' ? '主人钥匙' : '—') + '</b></div></div>';

      const res = v.querySelector('#sRes');
      const ares = v.querySelector('#sAutoRes');
      v.querySelector('#sMode').addEventListener('change', e => {v.querySelector('#sRemoteFields').hidden=e.target.value!=='remote';});

      // ── 本地大脑（sidecar）卡片：状态读数 + 重启 ──────────────
      // 只在 Electron 里有（网页 harness 没有 window.vigil.sidecar）
      if (global.vigil && global.vigil.sidecar) {
        const card = document.createElement('div');
        card.className = 'card';
        card.style.marginBottom = '14px';
        v.insertBefore(card, v.querySelector('#autoCard'));
        const drawSc = (s) => {
          const tag = { ready: '✓ 运行中', starting: '… 启动中', failed: '✗ 放弃重启', stopped: '— 未启用' }[s.status] || s.status;
          const tail = (s.logTail || []).slice(-3).map((l) => esc(l.slice(0, 80))).join('<br>');
          card.innerHTML =
            '<div class="sec" style="margin-top:0">本地大脑（sidecar）</div>' +
            '<div class="kv"><span>状态</span><b>' + tag + (s.port ? ' · 端口 ' + s.port : '') + '</b></div>' +
            '<div class="kv"><span>重启</span><b>' + (s.restarts || 0) + ' / 3</b></div>' +
            (s.exe ? '<div class="kv"><span>程序</span><b class="mono" style="font-size:11px">' + esc(s.exe) + '</b></div>' : '') +
            (tail ? '<div class="muted mono" style="font-size:10.5px;margin-top:6px;line-height:1.5">' + tail + '</div>' : '') +
            '<div style="margin-top:8px"><button class="btn oline" id="scRestart">重启大脑</button></div>';
          card.querySelector('#scRestart').addEventListener('click', async () => {
            toast('正在重启本地大脑…');
            await global.vigil.sidecar.restart();
          });
        };
        global.vigil.sidecar.status().then(drawSc);
        global.vigil.sidecar.onState(drawSc);   // 启动/崩溃/重启实时刷新
      }

      v.querySelector('#sAuto').addEventListener('click', async () => {
        ares.textContent = '探测中…';
        const r = await global.vigil.autoConnect();
        if (r && r.ok) {
          ares.textContent = '✓ 已连上 ' + r.base + '，token 已收下';
          await API.loadCfg();
          await API.ping();
          toast('已连上 ' + r.base);
          ctx.refresh();
          return;
        }
        ares.textContent = r && r.needToken ? '地址通了，但要手填 token（服务端不认本机）' : '没找到（' + ((r && r.tried || []).join('；') || '') + '）';
        v.querySelector('#autoHint').innerHTML = U.autoHintHtml(r);
      });
      v.querySelector('#sSave').addEventListener('click', async () => {
        const base = v.querySelector('#sBase').value.trim();
        const tok = v.querySelector('#sToken').value.trim();
        const mode=v.querySelector('#sMode').value;
        if(mode==='remote' && !base){res.textContent='请填写远程 Brain 地址';return;}
        await API.saveCfg({connection_mode:mode,...(mode==='remote'?{remote_base:base}:{})});
        if (tok && mode==='remote') {
          const r = await global.vigil.token.set(tok);
          if (r && r.warn) toast(r.warn);
        }
        res.textContent = '已保存，正在重连…';
        const connected=await global.vigil.autoConnect();
        if(!connected.ok){res.textContent=connected.err || 'Brain 未就绪';return;}
        await API.loadCfg();
        await API.ping();
        res.textContent = state.conn.ok ? '连接正常（' + state.conn.ms + ' ms）' : '连不上：' + state.conn.err;
        ctx.refresh();
      });
      v.querySelector('#sTest').addEventListener('click', async () => {
        res.textContent = '测试中…';
        const r = await global.vigil.token.test();
        res.textContent = r.ok ? '✓ 连接正常' + (r.name ? '（' + r.name + '）' : '') : '✗ 连不上：' + (r.err || 'HTTP ' + r.status);
        API.ping();
      });
      // app.js 启动时如果自动探测失败，会把结果放在 state.autoFail
      if (state.autoFail) v.querySelector('#autoHint').innerHTML = U.autoHintHtml(state.autoFail);
    }

    async function model(pv) {
      const renderId = ++modelRenderId;
      const isCurrent = () => renderId === modelRenderId && v.isConnected;
      // pv（可选）{base, provider}：「重新拉取」后带着表单值重画 —— 不带的话
      // 重画又按**已保存**的配置拉一遍，刚拉到的新家清单当场弹回旧家（实锤过）。
      ctx.topbar.querySelector('h1').textContent = '模型管理';
      v.innerHTML = '<div class="card" role="status" aria-live="polite">正在读取模型配置…</div>';
      // 模型配置跟真身走：微信 / App 的她在云端，默认改云端的账；本机这份
      // 只管 PC 控制台聊天那个大脑。跟数据页同一个套路（没配云端就只有本机）。
      const live = await (global.vigil ? global.vigil.cfg.get() : Promise.resolve({}));
      if (!isCurrent()) return;
      const sync = (live && live.sync) || {};
      const hasCloud = !!(sync.cloud_url && sync.cloud_token);
      if (!state.mSrc) state.mSrc = 'local';
      const mb = () => (hasCloud && state.mSrc === 'cloud') ? 'cloud' : null;
      const mget = (p, timeoutMs) => get(p, { base: mb(), timeoutMs });
      const mpost = (p, b, timeoutMs) => post(p, b, { base: mb(), timeoutMs });
      const sourceSwitch = () => hasCloud
        ? '<div class="seg" id="mSrc" role="group" aria-label="模型配置来源" style="margin-bottom:12px">' +
          '<button type="button" data-ms="cloud" aria-pressed="' + (state.mSrc === 'cloud') + '"' + (state.mSrc === 'cloud' ? ' class="on"' : '') + '>云端（微信 / App 的她）</button>' +
          '<button type="button" data-ms="local" aria-pressed="' + (state.mSrc === 'local') + '"' + (state.mSrc === 'local' ? ' class="on"' : '') + '>本机（这台电脑的她）</button></div>'
        : '';
      const bindSourceSwitch = () => {
        v.querySelectorAll('#mSrc button[data-ms]').forEach((b) => b.addEventListener('click', () => {
          if (state.mSrc === b.dataset.ms) return;
          state.mSrc = b.dataset.ms;
          void model();
        }));
      };
      v.innerHTML = sourceSwitch() + '<div class="card" role="status" aria-live="polite">正在读取' +
        (state.mSrc === 'cloud' ? '云端' : '本机') + '模型配置…</div>';
      bindSourceSwitch();
      const [modelReply, savedReply] = await Promise.all([
        mpost('/api/models/list', {
          base: (pv && pv.base) || '', provider: (pv && pv.provider) || ''
        }, 8000),
        mget('/api/models/saved', 8000),
      ]);
      if (!isCurrent()) return;
      const r = pick(modelReply);
      const savedData = pick(savedReply);
      if (!r.current && !r.providers) {
        const st = Number(r._status || 0);
        const msg = st === 404 ? '当前 Brain 没有模型列表接口，请升级 Brain 或检查客户端版本' :
          (st === 401 || st === 403 ? 'Brain 拒绝了请求，请检查 Token 或设备权限' :
            (r._err || '先在「连接与设备」里把地址和 token 填对'));
        v.innerHTML = sourceSwitch() + errBox('读不到模型配置', msg) +
          '<div style="margin-top:10px"><button class="btn oline md" type="button" id="mRetry">重试</button></div>';
        bindSourceSwitch();
        v.querySelector('#mRetry').addEventListener('click', () => { void model(pv); });
        return;
      }
      const cur = r.current || {};
      const provs = r.providers || [];
      const groups = r.groups || {};
      let savedModels = savedData.ok ? (savedData.models || []).filter((m) => !m.unsaved) : [];
      const hasKey = (p) => !!(cur.keys_by_provider && cur.keys_by_provider[p]);
      const provName = (p) => (provs.find((x) => x.key === p) || {}).name || p;
      // 可输入的组合框：候选来自服务商的模型清单，但本地部署的 Qwen 之类
      // 清单里没有的，直接打字就行（保存时按输入原样提交）。
      const combo = (dlId, arr, sel, ph) =>
        '<input class="input" id="' + dlId + '" list="' + dlId + '_dl" value="' + esc(sel || '') + '" placeholder="' + esc(ph || '留空 = 不启用') + '">' +
        '<datalist id="' + dlId + '_dl">' + (arr || []).map((m) => {
          const id = typeof m === 'string' ? m : (m.id || m.name);
          return '<option value="' + esc(id) + '">' + esc(id) + '</option>';
        }).join('') + '</datalist>';
      const keyHintText = (c) => {
        if (!c.api_key_set) return '还没配密钥。密钥一家一把（服务商后台能拿到），换家要换 key';
        if (c.key_belongs_to && c.key_belongs_to !== c.provider)
          return '⚠ 当前这把是「' + esc(provName(c.key_belongs_to)) + '」的密钥——这样她是调不通的，先填这一家的 key';
        return '这把就是当前服务商的密钥。切回存过密钥的服务商不用重填';
      };
      const modelErr = (rr) => {
        const raw = String(rr && (rr._err || rr.err || '') || '');
        const st = Number(rr && rr._status || 0);
        if (st === 404 || /no such api|不存在|not found/i.test(raw)) return '当前 Brain 没有这个模型接口，请升级 Brain 或检查客户端版本';
        if (/HTTP(?: Error)?\s*404|\/models 接口/i.test(raw)) return '服务商没有兼容的模型列表接口，请检查 Base URL 和 Brain 版本';
        if (st === 401 || st === 403) return 'Brain 拒绝了请求：请检查 Token 或设备权限';
        if (st >= 500) return 'Brain 内部错误：' + raw.slice(0, 100);
        if (/timeout|timed out|连接|connect|network|不可达/i.test(raw)) return '服务商不可达或网络超时：' + raw.slice(0, 100);
        if (/401|403|密钥|key|auth/i.test(raw)) return '服务商拒绝了密钥或模型配置：' + raw.slice(0, 100);
        return raw || '模型列表暂时不可用，请检查 Brain 版本、服务商地址和模型配置';
      };
      const ol = cur.ollama || {};
      const dl = cur.delegation || {};
      const mode = cur.model_mode || 'cloud';
      // 运行方式卡片只在「本机大脑」视图出现 —— Ollama 长在这台 PC 上，
      // 云端那台服务器够不着 127.0.0.1:11434。
      const localCard = state.mSrc === 'local' ? (
        '<div class="card" style="margin-bottom:14px">' +
        '<b style="font-size:13px">运行方式</b>' +
        '<div class="seg" id="mMode" style="margin-top:10px">' +
        '<button data-mm="cloud"' + (mode === 'cloud' ? ' class="on"' : '') + '>云端模型</button>' +
        '<button data-mm="local"' + (mode === 'local' ? ' class="on"' : '') + '>本地 Ollama</button>' +
        '<button data-mm="hybrid"' + (mode === 'hybrid' ? ' class="on"' : '') + '>混合（云端主脑+本地委派）</button>' +
        '</div>' +
        '<div class="muted" style="font-size:11.5px;margin-top:6px">本地模式不调云端 API：同一套大脑用本机 Ollama 跑完整工具循环。混合模式：主模型仍在云端，只把需要翻本地文件的只读子任务委派给本机 Ollama。</div>' +
        '<div id="olBox" style="margin-top:12px' + ((mode === 'local' || mode === 'hybrid') ? '' : ';display:none') + '">' +
        '<div class="field"><label>Ollama 地址</label>' +
        '<input class="input mono" id="olBase" value="' + esc(ol.base || 'http://127.0.0.1:11434') + '" placeholder="http://127.0.0.1:11434">' +
        '<div class="muted" style="font-size:11.5px">默认回环；不用带 /v1，程序走原生 /api/chat（options 才生效）</div></div>' +
        '<div class="field"><label>模型</label><input class="input" id="olModel" list="olModel_dl" value="' + esc(ol.model || '') + '" placeholder="如 qwen27b-efficientthink:latest">' +
        '<datalist id="olModel_dl"></datalist>' +
        '<div class="muted" style="font-size:11.5px">从 /api/tags 拉取已安装模型；点「测试连接」自动填充</div></div>' +
        '<div class="grid g2" style="margin-top:6px">' +
        '<div class="field"><label>上下文 num_ctx</label><input class="input" id="olCtx" type="number" value="' + esc(ol.num_ctx != null ? ol.num_ctx : 16384) + '"></div>' +
        '<div class="field"><label>输出上限 num_predict</label><input class="input" id="olPred" type="number" value="' + esc(ol.num_predict != null ? ol.num_predict : 2048) + '"></div>' +
        '</div>' +
        '<label class="row" style="margin:6px 0"><input type="checkbox" id="olThink"' + (ol.think !== false ? ' checked' : '') + '> 请求 Ollama 返回思考过程（efficientthink 建议开）</label>' +
        '<div style="display:flex;gap:8px;align-items:center;margin-top:6px">' +
        '<button class="btn oline md" id="olTest">测试连接</button>' +
        '<span class="muted" id="olRes" style="font-size:12px"></span></div>' +
        '</div>' +
        // 委派设置：只在混合模式露面（开关门控在大脑侧：hybrid + enabled + 已配本地模型）
        '<div id="dlBox" style="margin-top:12px' + (mode === 'hybrid' ? '' : ';display:none') + '">' +
        '<div style="border-top:1px solid var(--border,#e5e5e5);padding-top:10px">' +
        '<label class="row" style="margin:0 0 6px"><input type="checkbox" id="dlEnabled"' + (dl.enabled ? ' checked' : '') + '> 允许云端主脑把只读子任务委派给本机</label>' +
        '<div class="muted" style="font-size:11.5px">默认关闭。仅支持只读勘察（列目录/查找/读文件/搜内容），不能写文件、跑命令、删除或联网；单次硬顶 6 轮模型请求 / 8 次工具调用 / 180 秒。</div></div>' +
        '<div class="field" style="margin-top:10px"><label>授权工作区（每行一个绝对路径）</label>' +
        '<textarea class="input mono" id="dlRoots" rows="3" style="width:100%;resize:vertical;font-size:12px" placeholder="如 F:\projects\demo">' + esc((dl.allowed_roots || []).join('\n')) + '</textarea>' +
        '<div class="muted" style="font-size:11.5px">只有清单内目录能被委派访问，路径外一律拒绝；留空 = 任何委派都不执行（fail-closed）。</div></div>' +
        '<div style="margin-top:10px"><b style="font-size:12.5px">最近委派</b>' +
        '<div style="display:flex;gap:8px;align-items:center;margin:6px 0">' +
        '<button class="btn oline md" id="dlRefresh" type="button">刷新</button>' +
        '<span class="muted" id="dlRes" style="font-size:11.5px"></span></div>' +
        '<div id="dlList" class="muted" style="font-size:11.5px">加载中…</div></div>' +
        '</div></div>') : '';

      const savedRowsHtml = () => savedModels.length
        ? '<div class="model-table-wrap"><table class="model-table"><thead><tr>' +
          '<th>模型名称</th><th>Provider</th><th>Base URL</th><th>Model ID</th><th>API Key</th><th></th>' +
          '</tr></thead><tbody>' + savedModels.map((m, i) =>
            '<tr><td><strong>' + esc(m.name || m.model) + '</strong>' +
              (m.active ? ' <span class="tag ok">当前</span>' : '') + '</td>' +
              '<td>' + esc(m.provider_name || m.provider || '—') + '</td>' +
              '<td class="mono model-url" title="' + esc(m.base || '') + '">' + esc(m.base || '—') + '</td>' +
              '<td class="mono model-id" title="' + esc(m.model || '') + '">' + esc(m.model || '—') + '</td>' +
              '<td>' + (m.has_key
                ? '<span class="tag ok">已配置</span><span class="mono model-key-mask">' + esc(m.key_mask || '********') + '</span>'
                : '<span class="tag warn">未配置</span>') + '</td>' +
              '<td><button class="btn ghost sm" type="button" data-model-remove="' + i + '">移除</button></td></tr>'
          ).join('') + '</tbody></table></div>'
        : '<div class="model-empty">还没有添加模型。添加后可直接从聊天模型菜单切换。</div>';
      const modelManager =
        '<section class="card hd model-manager" aria-labelledby="modelManagerTitle">' +
        '<div class="h"><div><h3 id="modelManagerTitle">已添加模型</h3>' +
        '<div class="muted model-manager-note">本机清单供此客户端聊天切换；云端清单供 App / 微信。配置会写入 Brain 并在重启后保留。</div></div>' +
        '<button class="btn fill md" type="button" id="mAddToggle">添加模型</button></div>' +
        '<div id="mSavedRows">' + savedRowsHtml() + '</div>' +
        '<div class="model-add-form" id="mAddForm" hidden>' +
        '<div class="model-form-title"><strong>添加聊天模型</strong><span class="muted">密钥留空时沿用该 Provider 和 Base URL 已保存的密钥</span></div>' +
        '<div class="grid g2">' +
        '<div class="field"><label for="mAddName">模型名称</label><input class="input" id="mAddName" maxlength="120" placeholder="例如：工作模型"></div>' +
        '<div class="field"><label for="mAddProvider">Provider</label><select class="input" id="mAddProvider">' +
        provs.map((p) => '<option value="' + esc(p.key) + '"' + (p.key === cur.provider ? ' selected' : '') + '>' + esc(p.name || p.key) + '</option>').join('') +
        '</select></div>' +
        '<div class="field"><label for="mAddBase">Base URL</label><input class="input mono" id="mAddBase" value="' + esc(cur.api_base || '') + '" placeholder="https://api.example.com/v1"></div>' +
        '<div class="field"><label for="mAddModel">Model ID</label><input class="input mono" id="mAddModel" maxlength="240" placeholder="例如：vendor/model-name" required></div>' +
        '<div class="field model-key-field"><label for="mAddKey">API Key</label><input class="input" id="mAddKey" type="password" autocomplete="new-password" placeholder="输入新密钥；留空沿用已有密钥"></div>' +
        '</div><div class="model-form-actions"><button class="btn fill md" type="button" id="mAddSave">保存模型</button>' +
        '<button class="btn oline md" type="button" id="mAddCancel">取消</button><span class="muted" id="mAddResult" role="status" aria-live="polite"></span></div></div></section>';

      v.innerHTML =
        sourceSwitch() + modelManager +
        localCard +
        '<div class="card" style="margin-bottom:14px">' +
        '<div class="field"><label>服务商</label><select class="input" id="mProv">' +
        provs.map((p) => '<option value="' + esc(p.key) + '"' + (p.key === cur.provider ? ' selected' : '') + '>' + esc(p.name) + (hasKey(p.key) ? '（已存密钥）' : '') + '</option>').join('') +
        '</select><div class="muted" style="font-size:11.5px">选好服务商：地址自动填、清单自动拉；这家没存过密钥的话先填密钥</div></div>' +
        '<div class="field"><label>接口地址</label><input class="input mono" id="mBase" value="' + esc(cur.api_base || '') + '">' +
        '<div class="muted" style="font-size:11.5px">跟着服务商自动换，只有「自定义」才需要手填</div></div>' +
        '<div class="field"><label>密钥</label><input class="input" id="mKey" type="password" placeholder="' + (cur.api_key_set ? '已有一把，留空 = 不改' : '还没配') + '">' +
        '<div class="muted" style="font-size:11.5px" id="mKeyHint">' + keyHintText(cur) + '</div>' +
        '<div style="font-size:11.5px;margin-top:5px" id="mKeyState">' +
        (cur.api_key_set ? '状态：已保存 · 掩码：<span class="mono">' + esc(cur.api_key || '********') + '</span>' : '状态：未配置') + '</div></div>' +
        '</div>' +
        '<div class="sec">模型分配（下拉选或直接输入；本地部署的 Qwen 填它的模型名）</div>' +
        '<div class="grid g3">' +
        '<div class="card"><b style="font-size:13px">对话</b><div style="margin-top:10px">' + combo('mChat', groups.chat, cur.chat, '必填') + '</div></div>' +
        '<div class="card"><b style="font-size:13px">看图</b><div style="margin-top:10px">' + combo('mVision', groups.vision, cur.vision) + '</div></div>' +
        '<div class="card"><b style="font-size:13px">生图</b><div style="margin-top:10px">' + combo('mImage', groups.image, cur.image) + '</div></div>' +
        '<div class="card"><b style="font-size:13px">语音识别</b><div style="margin-top:10px">' + combo('mAudio', groups.audio, cur.audio) + '</div></div>' +
        '</div>' +
        '<div class="muted" style="font-size:11.5px;margin-top:8px">本地部署（Ollama / LM Studio / vllm）：接口地址填它的地址（如 http://你的IP:11434/v1），模型名手填——注意地址要云端大脑能访问到（走隧道或局域网）。</div>' +
        '<div style="display:flex;gap:8px;margin-top:14px;align-items:center">' +
        '<button class="btn fill md" id="mSave">保存并生效（免重启）</button>' +
        '<button class="btn oline md" id="mPull">重新拉取模型</button>' +
        '<button class="btn oline md" id="mTest">测试连接</button>' +
        '<button class="btn oline md" id="mDeleteKey">删除此家密钥</button>' +
        '<span class="muted" id="mRes" style="font-size:12px">来源：' + esc(r.source || '') + ' · 共 ' + esc(r.count || 0) + ' 个' +
        (r.err ? ' · ' + esc(String(r.err).slice(0, 60)) : '') + '</span></div>';
      const provSel = v.querySelector('#mProv');
      const baseInp = v.querySelector('#mBase');
      const keyInp = v.querySelector('#mKey');
      const keyHint = v.querySelector('#mKeyHint');
      const mRes = v.querySelector('#mRes');
      const mKeyState = v.querySelector('#mKeyState');
      const savedHost = v.querySelector('#mSavedRows');
      const addForm = v.querySelector('#mAddForm');
      const drawSavedModels = () => {
        savedHost.innerHTML = savedRowsHtml();
        savedHost.querySelectorAll('[data-model-remove]').forEach((b) => b.addEventListener('click', async () => {
          const model = savedModels[Number(b.dataset.modelRemove)];
          if (!model) return;
          if (!await confirmDialog('从已添加模型中移除「' + (model.name || model.model) + '」？')) return;
          b.disabled = true;
          const rr = pick(await mpost('/api/models/forget', {
            provider: model.provider, base: model.base, model: model.model,
          }));
          if (!rr.ok) { toast('移除失败：' + (rr._err || rr.err || '未知错误')); b.disabled = false; return; }
          const fresh = pick(await mget('/api/models/saved'));
          savedModels = fresh.ok ? (fresh.models || []).filter((m) => !m.unsaved) : [];
          drawSavedModels();
          toast('已从模型清单移除');
        }));
      };
      const addToggle = v.querySelector('#mAddToggle');
      const addProvider = v.querySelector('#mAddProvider');
      const addBase = v.querySelector('#mAddBase');
      const addModel = v.querySelector('#mAddModel');
      const addKey = v.querySelector('#mAddKey');
      const addResult = v.querySelector('#mAddResult');
      addToggle.addEventListener('click', () => {
        addForm.hidden = !addForm.hidden;
        if (!addForm.hidden) addModel.focus();
      });
      v.querySelector('#mAddCancel').addEventListener('click', () => {
        addForm.hidden = true;
        addResult.textContent = '';
      });
      addProvider.addEventListener('change', () => {
        const selectedProvider = provs.find((p) => p.key === addProvider.value);
        if (selectedProvider && selectedProvider.key !== 'custom' && selectedProvider.base)
          addBase.value = selectedProvider.base;
      });
      v.querySelector('#mAddSave').addEventListener('click', async (e) => {
        const saveButton = e.currentTarget;
        const modelId = addModel.value.trim();
        const base = addBase.value.trim();
        if (!modelId) { addResult.textContent = '请填写 Model ID'; addModel.focus(); return; }
        if (!base) { addResult.textContent = '请填写 Base URL'; addBase.focus(); return; }
        saveButton.disabled = true;
        addResult.textContent = '保存中…';
        const payload = {
          name: v.querySelector('#mAddName').value.trim(),
          provider: addProvider.value,
          base,
          model: modelId,
        };
        if (addKey.value.trim()) payload.api_key = addKey.value.trim();
        const rr = pick(await mpost('/api/models/save', payload));
        saveButton.disabled = false;
        if (!rr.ok) { addResult.textContent = '保存失败：' + (rr._err || rr.err || '未知错误'); return; }
        savedModels = (rr.models || []).filter((m) => !m.unsaved);
        drawSavedModels();
        addKey.value = '';
        addModel.value = '';
        v.querySelector('#mAddName').value = '';
        addResult.textContent = '已保存；聊天窗口重新打开模型菜单即可读取。';
        toast('模型配置已保存');
      });
      drawSavedModels();
      // 清单就地换血：只刷四个下拉的候选和来源行，不整页重画 —— 整页重画
      // 会把用户刚输到一半的密钥洗掉。
      const applyList = (rr) => {
        const K = { mChat: 'chat', mVision: 'vision', mImage: 'image', mAudio: 'audio' };
        Object.keys(K).forEach((id) => {
          const dl = v.querySelector('#' + id + '_dl');
          if (dl) dl.innerHTML = ((rr.groups || {})[K[id]] || []).map((m) => {
            const mid = typeof m === 'string' ? m : (m.id || m.name);
            return '<option value="' + esc(mid) + '">' + esc(mid) + '</option>';
          }).join('');
        });
        mRes.textContent = rr.groups ? ('来源：' + esc(rr.source || '') + ' · 共 ' + esc(rr.count || 0) + ' 个' +
          (rr.err ? ' · ' + esc(String(rr.err).slice(0, 60)) : '')) : ('✗ ' + esc(modelErr(rr)));
      };
      // 云端/本机切换：两边是两份独立配置，整页重画
      bindSourceSwitch();
      // 运行方式：切云端时折叠 Ollama/委派设置（值不清，保存时仍会带上所选模式）
      const olBox = v.querySelector('#olBox');
      const dlBox = v.querySelector('#dlBox');
      v.querySelectorAll('#mMode button').forEach((b) => b.addEventListener('click', () => {
        if (b.disabled) return;
        v.querySelectorAll('#mMode button').forEach((x) => x.classList.toggle('on', x === b));
        if (olBox) olBox.style.display = (b.dataset.mm === 'local' || b.dataset.mm === 'hybrid') ? '' : 'none';
        if (dlBox) dlBox.style.display = b.dataset.mm === 'hybrid' ? '' : 'none';
      }));
      // 连接测试：拿表单里的地址（模型留空也能测服务在不在），成功后回填模型下拉
      const olRes = v.querySelector('#olRes');
      const olTestBtn = v.querySelector('#olTest');
      if (olTestBtn) olTestBtn.addEventListener('click', async () => {
        const base = (v.querySelector('#olBase') || {}).value || '';
        const model = (v.querySelector('#olModel') || {}).value || '';
        olRes.textContent = '测试中…';
        const q = '?base=' + encodeURIComponent(base.trim()) +
          (model ? '&model=' + encodeURIComponent(model.trim()) : '');
        const rr = pick(await mget('/api/ollama/status' + q));
        if (rr.ok) {
          olRes.textContent = '✓ 在线 · 已装 ' + (rr.models || []).length + ' 个模型' +
            (rr.model_installed === false ? '（当前填的模型未安装）' : '');
          const dl = v.querySelector('#olModel_dl');
          if (dl) dl.innerHTML = (rr.models || []).map((m) =>
            '<option value="' + esc(m) + '">').join('');
          if (!model && (rr.models || []).length) {
            v.querySelector('#olModel').value = rr.models[0];
          }
        } else {
          olRes.textContent = '✗ 连不上：' + (rr.err || '检查 Ollama 是否已启动');
        }
      });
      // 最近委派：本机大脑 data/delegations/ 的落盘记录，每次委派的状态/用量
      // 都在这（只在本机视图有 dlBox）。
      if (dlBox) {
        const dlList = dlBox.querySelector('#dlList');
        const dlRes = dlBox.querySelector('#dlRes');
        const KIND_CN = {
          log_root_cause: '日志根因', file_discovery: '文件发现',
          read_only_bug_analysis: '跨文件 Bug', structured_extraction: '结构化抽取',
          plan_draft: '计划草拟',
        };
        const DL_BADGE = {
          succeeded: ['成功', '#1a7f37'], failed: ['失败', '#c00'],
          timed_out: ['超时', '#b25f00'], cancelled: ['取消', '#666'],
          running: ['进行中', '#0969da'], queued: ['排队', '#666'],
        };
        const fmtDLTime = (ts) => {
          const d = new Date((ts || 0) * 1000);
          const p = (n) => String(n).padStart(2, '0');
          return (d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' +
            p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
        };
        const loadDL = async () => {
          dlList.textContent = '加载中…';
          const rr = pick(await mget('/api/delegations/recent?n=20'));
          const items = rr.items || [];
          if (!rr.ok) { dlList.textContent = '读不到：' + esc(rr._err || rr.err || ''); return; }
          if (!items.length) { dlList.textContent = '还没有委派记录。'; return; }
          dlList.innerHTML = items.map((it) => {
            const bg = DL_BADGE[it.status] || [it.status || '?', '#666'];
            const errs = (it.errors || []).map((e) => esc(e.category || '错误')).join('、');
            return '<div style="border-top:1px solid var(--border,#eee);padding:6px 0">' +
              '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
              '<b style="font-size:11.5px">' + esc(KIND_CN[it.kind] || it.kind || '?') + '</b>' +
              '<span style="color:#fff;background:' + bg[1] + ';border-radius:3px;padding:0 5px;font-size:10.5px">' + esc(bg[0]) + '</span>' +
              '<span style="color:#999">' + esc(fmtDLTime(it.created_at)) + '</span>' +
              (it.retry_used ? '<span style="color:#b25f00">重试1次</span>' : '') +
              '</div>' +
              '<div style="margin-top:2px;word-break:break-all">' + esc(it.goal || '') + '</div>' +
              '<div style="color:#999;margin-top:2px">模型请求 ' + esc(it.model_requests) +
              ' · 工具 ' + esc(it.tool_calls) + ' · tokens ' + esc(it.input_tokens) +
              '/' + esc(it.output_tokens) + ' · 用时 ' + esc(it.elapsed_seconds) + 's' +
              (errs ? ' · <span style="color:#c00">' + errs + '</span>' : '') + '</div></div>';
          }).join('');
          dlRes.textContent = '共 ' + items.length + ' 条 · ' + fmtDLTime(items[0].created_at) + ' 更新';
        };
        dlBox.querySelector('#dlRefresh').addEventListener('click', loadDL);
        loadDL();
      }
      // 下拉换服务商：地址自动填 + 清单自动拉。这家没存过密钥、密钥框又空着
      // 就先不拉 —— 服务端只能拿别家的 key 去试，必然 401，白拉一轮还吐出
      // 一份兜底清单冒充这家的模型（"换了家模型却不变"就是它干的）。
      const provPull = async () => {
        const pk = provSel.value;
        const p = provs.find((x) => x.key === pk);
        if (p && p.key !== 'custom') baseInp.value = p.base || '';
        if (pk === 'custom') return;
        if (!hasKey(pk) && !keyInp.value.trim()) {
          keyHint.innerHTML = '「' + esc(provName(pk)) + '」还没存过密钥——先在密钥框填它的 key，再点「重新拉取模型」';
          return;
        }
        const rr = pick(await mpost('/api/models/list', { base: baseInp.value.trim(), provider: pk }));
        if (rr.groups) applyList(rr);
        else mRes.textContent = '✗ ' + modelErr(rr);
      };
      provSel.addEventListener('change', provPull);
      v.querySelector('#mPull').addEventListener('click', async () => {
        const base = baseInp.value.trim();
        const pk = provSel.value;
        const key = keyInp.value.trim();
        const rr = pick(await mpost('/api/models/list', {
          force: true, base: base, provider: pk, api_key: key
        }));
        toast(rr.groups && rr.source !== 'fallback' ? '已重新拉取'
          : '拉取失败：' + modelErr(rr));
        // 带上刚用的 base/provider 重画，清单别弹回已保存的旧家；
        // key 不回传（明文不进 URL），服务端存过这家的会自己认领。
        model({ base: base, provider: pk });
      });
      v.querySelector('#mTest').addEventListener('click', async () => {
        mKeyState.textContent = '状态：测试中…';
        const rr = pick(await mpost('/api/models/test', {
          provider: provSel.value, base: baseInp.value.trim(), api_key: keyInp.value.trim()
        }));
        mRes.textContent = rr.ok ? '✓ 连接正常' : '✗ ' + modelErr(rr);
        mKeyState.textContent = rr.ok ? '状态：测试成功' : '状态：测试失败 · ' + modelErr(rr);
      });
      v.querySelector('#mDeleteKey').addEventListener('click', async () => {
        const pk = provSel.value;
        if (!hasKey(pk)) { mRes.textContent = '当前服务商没有已保存的密钥'; return; }
        if (!await confirmDialog('删除「' + provName(pk) + '」的已保存密钥？')) return;
        const rr = pick(await mpost('/api/models/key/delete', { provider: pk }));
        if (!rr.ok) { mRes.textContent = '✗ ' + modelErr(rr); return; }
        toast('已删除该服务商密钥');
        model({ provider: pk, base: baseInp.value.trim() });
      });
      // 语音合成音色（含训练音色）：/api/voices 是死数据清单，永远打得开。
      // pick() 返回的是普通对象不是 Promise，直接 await 拿结果。
      const vr = pick(await mget('/api/voices'));
      const vItems = vr.items || [];
      if (vr.ok && vItems.length) {
        const card = document.createElement('div');
        card.className = 'sec';
        card.style.marginTop = '18px';
        card.textContent = '语音合成（她的声音）';
        v.appendChild(card);
        const box = document.createElement('div');
        box.className = 'card';
        const vGroups = {};
        vItems.forEach((it) => { (vGroups[it.group || '其他'] = vGroups[it.group || '其他'] || []).push(it); });
        box.innerHTML =
          '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
          '<div style="flex:1;min-width:200px"><b style="font-size:13px">当前音色</b>' +
          '<div class="muted" style="font-size:12px;margin-top:2px">' + esc(vr.current_desc || vr.current || '—') + '</div></div>' +
          '<select class="input" id="voiceSel" style="width:260px">' +
          Object.keys(vGroups).map((g) =>
            '<optgroup label="' + esc(g) + '">' +
            vGroups[g].map((it) => '<option value="' + esc(it.key) + '"' + (it.key === vr.current ? ' selected' : '') + '>' + esc(it.label) + '</option>').join('') +
            '</optgroup>').join('') +
          '</select>' +
          '<button class="btn fill" id="voiceApply">应用音色</button></div>' +
          '<div class="muted" style="font-size:11.5px;margin-top:6px">切换免重启。训练音色在对应分组里。</div>';
        v.appendChild(box);
        box.querySelector('#voiceApply').addEventListener('click', async () => {
          const key = box.querySelector('#voiceSel').value;
          const rr = pick(await mpost('/api/voice/apply', { key: key }));
          toast(rr.ok ? '音色已切换：' + (rr.desc || key) : '切换失败：' + (rr._err || rr.err || ''));
          if (rr.ok) { const d = box.querySelector('.muted'); if (d) d.textContent = rr.desc || key; }
        });
      }

      v.querySelector('#mSave').addEventListener('click', async () => {
        const pk = provSel.value;
        const key = keyInp.value.trim();
        const mmBtn0 = v.querySelector('#mMode button.on');
        const mode0 = mmBtn0 ? mmBtn0.dataset.mm : 'cloud';
        // 护栏：切到一家"没存过密钥也没填新密钥"的，保存完她下一句就 401。
        // 本地 Ollama 模式不调云端，不要云端 key —— 跳过这道护栏。
        if (mode0 !== 'local' && pk !== cur.provider && !hasKey(pk) && !key) {
          keyHint.innerHTML = '「' + esc(provName(pk)) + '」还没密钥——先填它的 key 再保存（或选本地 Ollama 模式）';
          keyInp.focus();
          return;
        }
        const payload = {
          provider: pk,
          api_base: baseInp.value.trim(),
          chat: v.querySelector('#mChat').value,
          vision: v.querySelector('#mVision').value,
          image: v.querySelector('#mImage').value,
          audio: v.querySelector('#mAudio').value,
        };
        if (key) payload.api_key = key;
        // 运行方式 + Ollama 段（仅本机大脑视图有这张卡）
        const mmBtn = v.querySelector('#mMode button.on');
        if (mmBtn) {
          payload.model_mode = mmBtn.dataset.mm;
          if (payload.model_mode === 'local' || payload.model_mode === 'hybrid') {
            const olModel = (v.querySelector('#olModel') || {}).value || '';
            if (!olModel.trim()) {
              toast('本地/混合模式要先选或填一个已安装的 Ollama 模型（点「测试连接」可自动拉取）');
              return;
            }
            payload.ollama = {
              base: (v.querySelector('#olBase') || {}).value || '',
              model: olModel.trim(),
              num_ctx: parseInt((v.querySelector('#olCtx') || {}).value, 10) || 16384,
              num_predict: parseInt((v.querySelector('#olPred') || {}).value, 10) || 2048,
              think: !!(v.querySelector('#olThink') || {}).checked,
            };
          }
          // 委派段只在混合模式提交；云端/本地模式留着配置也无害（门控看 model_mode）
          if (payload.model_mode === 'hybrid') {
            payload.delegation = {
              enabled: !!(v.querySelector('#dlEnabled') || {}).checked,
              allowed_roots: (v.querySelector('#dlRoots') || {}).value || '',
            };
            if (payload.delegation.enabled &&
                !payload.delegation.allowed_roots.trim()) {
              toast('开了委派但没填授权工作区：空名单会拒绝所有委派。先填授权目录。');
              return;
            }
          }
        }
        const rr = pick(await mpost('/api/models/apply', payload));
        // 把服务端认账的改动原样亮出来（"服务商 → 小米 MiMo；已换上小米的密钥"），
        // 切换成没成功一眼看得见，不再是干巴巴的"已生效"。
        mRes.textContent = rr.ok
          ? ((rr.changed && rr.changed.length) ? rr.changed.join('；') : '没有改动')
          : '没保存成：' + (rr._err || rr.err || '');
        toast(rr.ok ? '已生效' : '保存失败');
        if (rr.ok) {
          cur.provider = pk;
          if (key) {
            (cur.keys_by_provider = cur.keys_by_provider || {})[pk] = true;
            cur.key_belongs_to = pk;
            cur.api_key_set = true;
            keyInp.value = '';
          }
          keyHint.innerHTML = keyHintText(cur);
          const fresh = pick(await mget('/api/models/saved'));
          savedModels = fresh.ok ? (fresh.models || []).filter((m) => !m.unsaved) : [];
          drawSavedModels();
        }
      });
    }

    async function danger() {
      ctx.topbar.querySelector('h1').textContent = '危险操作';
      const acts = [
        ['重置对话上下文', '清掉本轮上下文块，聊天记录保留', '/api/reset'],
        ['清空聊天历史', '按当前渠道清，清完不可恢复', '/api/history/clear'],
      ];
      v.innerHTML = '<div class="card">' + acts.map(([t, d, p]) =>
        '<div class="row"><div class="lb"><b>' + esc(t) + '</b><span>' + esc(d) + '</span></div>' +
        '<button class="btn" data-p="' + p + '" data-t="' + esc(t) + '">执行</button></div>').join('') + '</div>';
      v.querySelectorAll('[data-p]').forEach((b) => b.addEventListener('click', async () => {
        if (!await confirmDialog(b.dataset.t + '？')) return;
        const body = b.dataset.p.includes('history') ? { src: state.src } : {};
        const r = pick(await post(b.dataset.p, body));
        toast(r.ok ? b.dataset.t + '：已执行' : '失败：' + (r._err || ''));
      }));
    }

    async function about() {
      ctx.topbar.querySelector('h1').textContent = '关于';
      v.innerHTML = '<div class="card">' +
        '<div class="kv"><span>客户端</span><b>v0.1.0（code <span id="abCode">…</span>）</b></div>' +
        '<div class="kv"><span>桌面框架</span><b>Electron ' + esc(String((global.process && process.versions && process.versions.electron) || '')) + '</b></div>' +
        '<div class="kv"><span>Brain 地址</span><b class="mono">' + esc(state.base || '—') + '</b></div>' +
        '<div class="kv"><span>渠道</span><b>' + esc(state.src) + '（本机控制台，独立于 App / 微信）</b></div>' +
        '<div class="kv"><span>token 存储</span><b>系统钥匙串（keytar）</b></div>' +
        '<div style="display:flex;gap:8px;align-items:center;margin-top:10px">' +
        '<button class="btn oline md" id="abCheck">检查更新</button>' +
        '<span class="muted" style="font-size:12px" id="abMsg">启动时和每小时也会自动检查（走云端）</span>' +
        '</div>' +
        '<div class="muted" style="font-size:11.5px;margin-top:10px">本地操控已并入聊天页：确认在气泡里，权限/目录/工具在输入框旁的盾牌面板里。桌宠 Live2D（阶段5）未开始。</div>' +
        '</div>';

      const show = (u) => {
        v.querySelector('#abCode').textContent = String(u.current != null ? u.current : '0');
        const m = v.querySelector('#abMsg');
        if (u.busy) { m.textContent = '上一次检查还没跑完…'; return; }
        if (u.err) { m.textContent = '检查失败：' + u.err; return; }
        if (!u.update) { m.textContent = '已是最新。'; return; }
        if (u.installing) { m.textContent = '安装器已启动，客户端即将退出。'; return; }
        if (u.downloaded) { m.textContent = '新版本已下载，下次启动会再提醒。'; return; }
        if (u.declined) { m.textContent = '发现新版本 v' + (u.ver || '') + '（你这次先不更）。'; return; }
        m.textContent = '发现新版本 v' + (u.ver || '') + '。';
      };

      v.querySelector('#abCheck').addEventListener('click', async () => {
        const btn = v.querySelector('#abCheck');
        const msg = v.querySelector('#abMsg');
        btn.disabled = true;
        msg.textContent = '检查中…';
        try {
          const u = await global.vigil.updater.check('ask');
          show(u || { err: '无响应' });
        } catch (e) {
          msg.textContent = '检查失败：' + String(e && e.message || e);
        }
        if (btn && !btn.isDestroyed) btn.disabled = false;
      });

      // 进页只查状态（不弹窗不下载）；版本号和"有没有新版"顺便画上
      try {
        const u = await global.vigil.updater.check();
        if (u) show(u);
      } catch (e) { /* 无云端配置等，静默 */ }
    }

    // ── 微信聊天集成：路由、开关、渠道设置和连接诊断 ───────────────
    async function wechat() {
      ctx.topbar.querySelector('h1').textContent = '微信聊天集成';
      const rt = pick(await get('/api/route'));
      const route = rt.wechat_route || 'cloud';
      const enabled = rt.enabled !== false;
      const fwd = rt.forward || {};
      const model = rt.model || '由 Brain 当前模型配置提供';
      const models = rt.models || { chat: model };
      v.innerHTML =
        '<div class="card" style="margin-bottom:14px">' +
        '<div class="kv"><span>集成状态</span><b id="wxState">' + (enabled ? '已启用' : '已停用') + '</b></div>' +
        '<div class="kv"><span>当前路由</span><b>' + (route === 'local' ? '本机大脑优先' : '云端大脑优先') + '</b></div>' +
        '<div class="kv"><span>Brain 全局对话模型</span><b class="mono" style="font-size:11px">' + esc(models.chat || model) + '</b></div>' +
        '<div class="kv"><span>视觉 / 生图</span><b class="mono" style="font-size:11px">' + esc(models.vision || '未配置') + ' / ' + esc(models.image || '未配置') + '</b></div>' +
        '<div class="kv"><span>Bridge 状态</span><b>' + esc(fwd.online ? '在线' : '未连接') + '</b></div>' +
        '<div style="display:flex;gap:8px;margin-top:10px;align-items:center;flex-wrap:wrap">' +
        '<button class="btn ' + (enabled ? 'fill' : 'oline') + ' md" id="wxToggle">' + (enabled ? '停用微信聊天' : '启用微信聊天') + '</button>' +
        '<button class="btn ' + (route === 'cloud' ? 'fill' : 'oline') + ' md" id="wxCloud">云端模型</button>' +
        '<button class="btn ' + (route === 'local' ? 'fill' : 'oline') + ' md" id="wxLocal">本机模型</button>' +
        '<button class="btn oline md" id="wxCheck">连接检测</button><span class="muted" id="wxRes"></span></div></div>' +
        '<div class="sec">私聊与群聊</div><div class="card" style="margin-bottom:14px">' +
        '<div class="muted" style="font-size:11.5px;margin-bottom:8px">当前微信仍使用 Brain 的全局模型配置；本页展示真实来源、路由和 Bridge 状态，不宣称已经完成微信独立模型。私聊和群聊的说话档位独立保存。后续独立模型只需增加渠道 model_ref，不重复保存 API Key。</div>' +
        '<div class="grid g2"><div><b>微信私聊说话档位</b><input class="input" id="wxPrivate" type="number" min="1" max="10" value="5"></div><div><b>微信群聊说话档位</b><input class="input" id="wxGroup" type="number" min="1" max="10" value="5"></div></div>' +
        '<button class="btn oline md" id="wxSaveChatty" style="margin-top:10px">保存渠道设置</button></div>' +
        '<div class="sec">Bridge 配置与说明</div><div class="card">' +
        '<div class="kv"><span>连接配置</span><span class="mono" style="font-size:11px">使用当前 Brain 地址与设备 Token</span></div>' +
        '<div class="muted" style="font-size:11.5px;margin-top:8px">Bridge 进程仍由现有微信桥接服务管理。本页只读连接状态并切换路由，不自动安装、执行或修改 Bridge 命令。微信不支持的版本、扫码限制和外部进程风险请以桥接文档为准。</div>' +
        '<div style="margin-top:10px"><b>最近 Brain 日志</b><div id="wxLogs" class="muted mono" style="font-size:11px;max-height:160px;overflow:auto;margin-top:6px">加载中…</div></div></div>';
      const loadChatty = async () => {
        const a = pick(await get('/api/chatty?src=wechat'));
        const b = pick(await get('/api/chatty?src=wechat_group'));
        v.querySelector('#wxPrivate').value = a.level || 5;
        v.querySelector('#wxGroup').value = b.level || 5;
      };
      loadChatty();
      const logs = pick(await get('/api/logs?src=server&n=20'));
      v.querySelector('#wxLogs').textContent = logs.ok && logs.items?.length
        ? logs.items.map((x) => String(x.msg || x.where || '').slice(0, 180)).join('\n')
        : (logs.err || '暂无日志');
      const sendRoute = async (next) => {
        const r = pick(await post('/api/route', { wechat_route: next, enabled: enabled }));
        v.querySelector('#wxRes').textContent = r.ok ? '已保存' : (r.err || '保存失败');
        if (r.ok) ctx.refresh();
      };
      v.querySelector('#wxCloud').addEventListener('click', () => sendRoute('cloud'));
      v.querySelector('#wxLocal').addEventListener('click', () => sendRoute('local'));
      v.querySelector('#wxToggle').addEventListener('click', async () => {
        const r = pick(await post('/api/route', { wechat_route: route, enabled: !enabled }));
        v.querySelector('#wxRes').textContent = r.ok ? '已保存' : (r.err || '保存失败');
        if (r.ok) ctx.refresh();
      });
      v.querySelector('#wxCheck').addEventListener('click', async () => {
        const r = pick(await get('/api/route'));
        v.querySelector('#wxRes').textContent = r.ok ? ('Brain 可达 · Bridge ' + (r.forward?.online ? '在线' : '未连接')) : (r.err || '连接失败');
      });
      v.querySelector('#wxSaveChatty').addEventListener('click', async () => {
        const a = Number(v.querySelector('#wxPrivate').value);
        const b = Number(v.querySelector('#wxGroup').value);
        await post('/api/chatty', { src: 'wechat', level: a });
        await post('/api/chatty', { src: 'wechat_group', level: b });
        toast('微信私聊和群聊设置已保存');
      });
    }

    // ── 云端同步（M5）：本地大脑 ↔ 云端权威源 ─────────────────
    // 本地优先架构：本地大脑跑着，记忆/人设的改动走 outbox 上云，
    // 云端的增量定时拉下来。这里管"云端在哪 + 主人钥匙 + 同步状态"，
    // 顺带管微信双大脑路由开关（同属"两边大脑怎么分工"这件事）。
    async function sync() {
      ctx.topbar.querySelector('h1').textContent = '云端同步';
      const cfg = await (global.vigil ? global.vigil.cfg.get() : Promise.resolve({}));
      const sc = (cfg && cfg.sync) || {};
      const rt = pick(await get('/api/route'));
      const route = (rt && rt.wechat_route) || 'cloud';
      const st = pick(await get('/api/sync/status'));
      const isLocalBrain = /^https?:\/\/(127\.|localhost)/i.test(state.base || '');

      v.innerHTML =
        '<div class="sec">微信双大脑路由（她说的话在谁的脑子里想）</div>' +
        '<div class="card" style="margin-bottom:14px">' +
        '<div class="kv"><span>当前</span><b>' + (route === 'local' ? '本地大脑（本机这台）' : '云端大脑（云机）') + '</b></div>' +
        '<div class="muted" style="font-size:11.5px;margin:6px 0">只对微信（私聊/群）生效；App 和本机控制台不受影响。本地大脑掉线时云端自动接管直答，消息不会丢。</div>' +
        '<div style="display:flex;gap:8px;align-items:center">' +
        '<button class="btn ' + (route === 'cloud' ? 'fill' : 'oline') + ' md" id="rtCloud">云端优先</button>' +
        '<button class="btn ' + (route === 'local' ? 'fill' : 'oline') + ' md" id="rtLocal" ' + (isLocalBrain ? '' : 'title="当前连的是云端大脑，切本地没有意义"') + '>本地优先</button>' +
        '<span class="muted" id="rtRes" style="font-size:12px"></span></div></div>' +

        '<div class="sec">同步状态（大脑自己报的）</div>' +
        '<div class="card" style="margin-bottom:14px">' +
        '<div class="kv"><span>同步</span><b>' + (st && st.enabled ? '已启用 → ' + esc(st.cloud_url || '') : '未启用（没有云端地址可推）') + '</b></div>' +
        '<div class="kv"><span>outbox 积压</span><b>' + esc((st && st.outbox_pending != null) ? st.outbox_pending : '—') + ' 条</b></div>' +
        '<div class="kv"><span>记忆断点</span><b class="mono" style="font-size:11px">' + esc(JSON.stringify((st && st.mem_seqs) || {})) + '</b></div>' +
        '<div style="display:flex;gap:8px;align-items:center;margin-top:8px">' +
        '<button class="btn oline md" id="syncNow">立即同步</button>' +
        '<span class="muted" id="syncRes" style="font-size:12px"></span></div></div>' +

        '<div class="sec">云端地址与主人钥匙（填一次，重启大脑生效）</div>' +
        '<div class="card">' +
        '<div class="field"><label>云端地址</label>' +
        '<input class="input mono" id="syUrl" value="' + esc(sc.cloud_url || '') + '" placeholder="http://<你的云机 IP>:8788">' +
        '<div class="muted" style="font-size:11.5px">本地大脑把记忆/人设改动推到这儿；留空 = 不同步</div></div>' +
        '<div class="field"><label>主人钥匙（brain token）</label>' +
        '<input class="input" id="syTok" type="password" placeholder="' + (sc.cloud_token ? '已保存（留空 = 不改）' : '还没配') + '">' +
        '<div class="muted" style="font-size:11.5px">同步接口只认主人钥匙；设备 token 拉不到全量</div></div>' +
        '<div style="display:flex;gap:8px;align-items:center">' +
        '<button class="btn fill" id="sySave">保存</button>' +
        '<span class="muted" id="syRes" style="font-size:12px">保存后重启本地大脑（设置 → 连接与设备 → 重启大脑）生效</span></div></div>';

      v.querySelector('#rtCloud').addEventListener('click', async () => {
        const r = pick(await post('/api/route', { route: 'cloud' }));
        v.querySelector('#rtRes').textContent = r.ok ? '已切到云端' : (r.err || '没切成');
        if (r.ok) ctx.refresh();
      });
      v.querySelector('#rtLocal').addEventListener('click', async () => {
        const r = pick(await post('/api/route', { route: 'local' }));
        v.querySelector('#rtRes').textContent = r.ok ? '已切到本地（云端会等本地作答，掉线自动兜底）' : (r.err || '没切成');
        if (r.ok) ctx.refresh();
      });
      v.querySelector('#syncNow').addEventListener('click', async () => {
        v.querySelector('#syncRes').textContent = '同步中…';
        const r = pick(await post('/api/sync/flush', {}));
        v.querySelector('#syncRes').textContent = r.ok
          ? '推 ' + (r.pushed || 0) + ' 条 / 拉 ' + (r.pulled ? ((r.pulled.memories || 0) + ' 记忆 ' + (r.pulled.personas || 0) + ' 人设') : '0')
          : '失败：' + (r.err || r._err || '');
      });
      v.querySelector('#sySave').addEventListener('click', async () => {
        const url = v.querySelector('#syUrl').value.trim().replace(/\/+$/, '');
        const tok = v.querySelector('#syTok').value.trim();
        const next = Object.assign({}, sc, { cloud_url: url });
        if (tok) next.cloud_token = tok;
        await API.saveCfg({ sync: next });
        v.querySelector('#syRes').textContent = '已保存';
        toast('云端同步配置已保存');
      });
    }

    const draw = { conn, model, wechat, sync, danger, about };
    bindLi(ctx.panel, (k, n) => {
      modelRenderId++;
      ctx.panel.querySelectorAll('.li').forEach((x) => x.classList.toggle('on', x === n));
      draw[k] && draw[k]();
    });
    // 首启向导引导：app.js 检测到"本地大脑在跑但还没配模型 key"时，
    // 把 state.settingsSub 指到 'model'，进来就直接是填 key 的表单
    const want = state.settingsSub;
    state.settingsSub = '';
    if (want && draw[want]) {
      modelRenderId++;
      ctx.panel.querySelectorAll('.li').forEach((x) => x.classList.toggle('on', x.dataset.k === want));
      draw[want]();
      return;
    }
    modelRenderId++;
    conn();
  }

  // ═══ 9. 功能分组页（按功能分类的聚合壳）════════════════════
  // 顶部导航五项：01 工作台 02 对话与人格 03 能力与扩展 04 连接与系统 05 数据。
  // 原有九个页面**一个不少**，全部按功能归入三组，作为组内功能项
  // （左栏 § 编号 + 等宽路径尾标）。各渲染器一行不改：给它们一个
  // "假 panel"挂副导航（横排在内容上沿），view 照旧，topbar 由它们自己写。
  const FUNC_GROUPS = [
    { id: 'persona', name: '对话与人格', num: '02', icon: 'user', items: [
      { k: 'persona', num: '021', name: '人格与语气', path: '/persona', icon: 'user', render: renderPersona },
      { k: 'memory', num: '022', name: '记忆与约定', path: '/memory', icon: 'brain', render: renderMemory },
      { k: 'kb', num: '023', name: '知识库', path: '/kb', icon: 'lib', render: renderKb },
    ] },
    { id: 'ability', name: '能力与扩展', num: '03', icon: 'puzzle', items: [
      { k: 'ability', num: '031', name: '能力与技能', path: '/abilities', icon: 'puzzle', render: renderAbility },
      { k: 'tools', num: '032', name: '本地操控', path: '/local', icon: 'term', render: renderTools },
      { k: 'life', num: '033', name: '生活面板', path: '/life', icon: 'heart', render: renderLife },
      { k: 'plugin_market', num: '034', name: '插件市场', path: '/plugins/market', icon: 'puzzle', render: renderPluginMarket },
    ] },
    { id: 'system', name: '连接与系统', num: '04', icon: 'set', items: [
      { k: 'settings', num: '041', name: '连接与设备', path: '/connection', icon: 'set', render: renderSettings },
    ] },
  ];

  function makeGroupPage(group) {
    return async function (ctx) {
      const items = group.items;
      let cur = items[0].k;

      // 左栏：本组功能清单（§ 编号 + 图标 + 等宽路径尾标）
      ctx.panel.innerHTML = '<div class="phead"><h2>' + esc(group.name) + '</h2></div><div class="plist">' +
        items.map((it) =>
          '<div class="li" data-k="' + it.k + '" title="' + esc(it.name + ' ' + it.path) + '">' +
          '<span class="ic">' + icon(it.icon, 'sm') + '</span>' +
          '<div class="tx"><b>' + esc(it.name) + '</b></div>' +
          '<span class="meta mono">' + it.path + '</span></div>').join('') + '</div>';

      const body = U.el('<div class="wrap cfg-wrap"><div class="cfg-subnav" id="cfgSubnav" hidden></div><div id="cfgBody"></div></div>');
      ctx.view.innerHTML = '';
      ctx.view.appendChild(body);
      const cfgBody = body.querySelector('#cfgBody');
      const subnav = body.querySelector('#cfgSubnav');

      async function drawSub(k) {
        const it = items.find((x) => x.k === k) || items[0];
        cur = it.k;
        ctx.panel.querySelectorAll('.li').forEach((n) => n.classList.toggle('on', n.dataset.k === it.k));
        cfgBody.innerHTML = '';
        cfgBody.className = '';
        body.dataset.section = it.k;
        subnav.hidden = true; subnav.innerHTML = '';
        const fakePanel = document.createElement('div');
        try {
          await it.render({ view: cfgBody, panel: fakePanel, topbar: ctx.topbar, src: ctx.src, refresh: () => drawSub(it.k) });
        } catch (e) {
          console.error('功能分区渲染失败:', e);
          cfgBody.innerHTML = '<div class="wrap">' + errBox('这一分区画不出来', e.message || '') + '</div>';
        }
        if (fakePanel.innerHTML.trim()) {          // 功能项自带的副导航（渠道切换 / 插件分类 / 连接分区）
          fakePanel.classList.add('cfg-subnav-inner');
          subnav.hidden = false;
          subnav.appendChild(fakePanel);
        }
        // 渲染器会重写 topbar，所以每次都补：宿主页 § 编号 + 功能项面包屑
        ctx.topbar.classList.add('topbar');
        ctx.topbar.querySelectorAll('.tnum,.crumb').forEach((n) => n.remove());
        ctx.topbar.insertAdjacentHTML('afterbegin',
          '<span class="tnum mono">§ ' + group.num + '</span><span class="crumb mono">§ ' +
          it.num + ' · ' + esc(group.name) + '</span>');
      }

      ctx.panel.querySelectorAll('.li[data-k]').forEach((n) =>
        n.addEventListener('click', () => drawSub(n.dataset.k)));
      await drawSub(cur);
    };
  }

  // ═══ 统一管理 ═══════════════════════════════════════════════
  let mgmtTimer = null;
  async function renderManagement(ctx) {
    ctx.topbar.className = 'topbar';
    ctx.view.className = 'scroll';
    ctx.panel.innerHTML = '<div class="phead"><h2>统一管理</h2></div>' +
      '<div class="plist">' +
      li({ icon: 'mon', title: '节点概览', sub: '所有服务器与本地实例', on: true, attrs: ' data-k="ov"' }) +
      li({ icon: 'book', title: '配置说明', sub: '如何接入新节点', attrs: ' data-k="doc"' }) +
      '</div>';
    ctx.topbar.innerHTML = topbar('节点概览', '',
      '<button class="btn oline" id="mgRe">' + icon('refresh', 'sm') + '刷新</button>');
    ctx.view.innerHTML = '<div class="wrap" id="mgView"><div class="muted" style="padding:20px 0">加载中…</div></div>';
    const v = ctx.view.querySelector('#mgView');
    const CB = { base: 'cloud', timeoutMs: 8000 };   // 统一管理数据在云端大脑；失败时尽快转为明确错误状态

    const statusColor = (s) => s === 'online' ? 'ok' : s === 'delayed' ? 'warn' : 'err';
    const statusText = (s) => s === 'online' ? '在线' : s === 'delayed' ? '延迟' : '离线';
    const bar = (label, val) => {
      const pct = val != null ? Math.max(0, Math.min(100, val)) : 0;
      const color = pct > 85 ? 'var(--err)' : pct > 65 ? 'var(--warn)' : 'var(--ok)';
      return '<div class="kv" style="margin:4px 0"><span>' + esc(label) + '</span>' +
        '<b style="min-width:42px;text-align:right">' + (val != null ? val.toFixed(1) + '%' : '—') + '</b></div>' +
        '<div style="height:4px;background:var(--line);border-radius:2px;overflow:hidden;margin-bottom:6px">' +
        '<div style="height:100%;width:' + pct + '%;background:' + color + ';transition:width .3s"></div></div>';
    };
    const nodeCard = (n) => {
      const m = n.metrics || {};
      const agents = n.agents || [];
      return '<div class="card" style="margin-bottom:12px">' +
        '<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">' +
        icon('mon', 'md') +
        '<div style="flex:1"><b style="font-size:14px">' + esc(n.name || n.id) + '</b>' +
        '<div class="muted" style="font-size:11.5px;margin-top:2px">' +
        esc(n.id) + (n.hostname ? ' · ' + esc(n.hostname) : '') + (n.platform ? ' · ' + esc(n.platform) : '') +
        '</div></div>' +
        tag(statusColor(n.status), statusText(n.status)) +
        '</div>' +
        bar('CPU', m.cpu_percent) + bar('内存', m.memory_percent) + bar('磁盘', m.disk_percent) +
        '<div class="kv" style="margin-top:8px"><span>最后心跳</span>' +
        '<b class="mono">' + (n.last_seen ? fmtAgo(n.last_seen) : '从未') + '</b></div>' +
        (agents.length ? '<div class="kv" style="margin-top:6px"><span>Agent</span><b>' + agents.length + ' 个运行中</b></div>' : '') +
        '</div>';
    };

    async function ov() {
      ctx.topbar.querySelector('h1').textContent = '节点概览';
      v.innerHTML = '<div class="muted" style="padding:20px 0">加载中…</div>';
      const r = pick(await get('/api/management/overview', CB));
      if (!r || !r.ok) {
        v.innerHTML = '<div class="wrap">' + empty('管理接口不可达',
          (r && (r._err || (r._status ? 'HTTP ' + r._status : ''))) || '请确认大脑已启动且管理模块已加载') + '</div>';
        return;
      }
      const nodes = r.nodes || [];
      const online = nodes.filter(n => n.status === 'online').length;
      const delayed = nodes.filter(n => n.status === 'delayed').length;
      const offline = nodes.filter(n => n.status === 'offline').length;
      v.innerHTML = '<div class="grid g4" style="margin-bottom:16px">' +
        '<div class="card kpi"><div class="lb">总节点</div><div class="v">' + nodes.length + '</div></div>' +
        '<div class="card kpi"><div class="lb">在线</div><div class="v" style="color:var(--ok)">' + online + '</div></div>' +
        '<div class="card kpi"><div class="lb">延迟</div><div class="v" style="color:var(--warn)">' + delayed + '</div></div>' +
        '<div class="card kpi"><div class="lb">离线</div><div class="v" style="color:var(--err)">' + offline + '</div></div>' +
        '</div>' +
        (nodes.length ? nodes.map(nodeCard).join('') :
          '<div class="card" style="padding:24px;text-align:center">' +
          icon('mon', 'lg') + '<div style="margin-top:10px;font-size:13.5px">还没有接入任何节点</div>' +
          '<div class="muted" style="font-size:12px;margin-top:6px">在「配置说明」里查看如何接入云端服务器 B 或本地电脑</div>' +
          '</div>');
    }

    function doc() {
      ctx.topbar.querySelector('h1').textContent = '配置说明';
      v.innerHTML = '<div class="card" style="padding:18px 20px">' +
        '<b style="font-size:14px">接入新节点（3 步）</b>' +
        '<div style="margin-top:14px;line-height:1.9;font-size:13px">' +
        '<div><b>1. 管理中心（云端服务器 A）配置令牌</b></div>' +
        '<div class="mono" style="background:var(--bg2);padding:8px 12px;border-radius:6px;margin:6px 0;font-size:12px">set VIGIL_MANAGEMENT_AGENT_TOKENS={"server-b":"随机长令牌","local-pc":"另一个令牌"}<br>set VIGIL_MANAGEMENT_AGENT_NAMES={"server-b":"云端服务器 B","local-pc":"本地电脑"}</div>' +
        '<div><b>2. 被管理节点运行 Agent</b></div>' +
        '<div class="mono" style="background:var(--bg2);padding:8px 12px;border-radius:6px;margin:6px 0;font-size:12px">set VIGIL_MANAGEMENT_URL=http://管理中心地址:8788<br>set VIGIL_MANAGEMENT_NODE_ID=server-b<br>set VIGIL_MANAGEMENT_TOKEN=随机长令牌<br>python brain/management_agent.py</div>' +
        '<div><b>3. 回到节点概览查看节点状态</b>（每 15 秒自动刷新）；Agent 状态与任务统一在「Agent 工作室」查看。</div>' +
        '</div>' +
        '<div class="muted" style="font-size:12px;margin-top:14px">心跳间隔 15 秒 · 45 秒内在线 · 90 秒内延迟 · 超过离线</div>' +
        '</div>';
    }

    const draw = (k) => { if (k === 'ov') ov(); else doc(); };
    ctx.panel.querySelectorAll('.li[data-k]').forEach(n =>
      n.addEventListener('click', () => {
        ctx.panel.querySelectorAll('.li').forEach(x => x.classList.remove('on'));
        n.classList.add('on');
        draw(n.dataset.k);
      }));
    ctx.topbar.querySelector('#mgRe').addEventListener('click', () => draw(
      ctx.panel.querySelector('.li.on')?.dataset.k || 'ov'));

    // 自动刷新：只在概览页每 15 秒刷一次，切走就停
    clearInterval(mgmtTimer);
    mgmtTimer = setInterval(() => {
      const active = ctx.panel.querySelector('.li.on')?.dataset.k;
      if (active === 'ov') ov();
    }, 15000);

    await ov();
  }

  // ── 插件市场 ──────────────────────────────────────────────
  // ═══ Creator Mode（MVP-1）：自然语言草稿 + 静态验证 ═══
  // 边界（与 brain/creator.py 的约定一一对应）：
  // - 生成走 Brain 的真实模型链路（/api/creator/draft），没有假装生成的旁路；
  // - 草稿落 Brain 的独立工作区，不碰正式插件/技能目录；
  // - 静态验证复用 P4 检查器，只查结构不执行代码；
  // - MVP-2 在静态验证通过后展示人工审核与一次性受控安装；生成完成不会自动安装。
  // ═══ Creator Mode：草稿卡（聊天页内创作 + 草稿列表共用）═══════
  // 入口在主页对话：composer 的「创作」chip 切到创作模式后，直接把需求
  // 当消息发给 Agent，回来的就是一个草稿卡。这里只负责"一个草稿怎么画、
  // 卡上几个按钮怎么绑"，不管入口在哪。
  function creatorFenceFor(s) {
    const m = String(s).match(/`{3,}/g);
    let n = 3;
    if (m) n = Math.max.apply(null, m.map((x) => x.length)) + 1;
    return '`'.repeat(n);
  }
  function creatorLangOf(name) {
    return /\.(py)$/.test(name) ? 'python' : /\.(js|mjs|cjs)$/.test(name) ? 'javascript' : 'json';
  }
  function creatorTargetOf(d) {
    const fs = d.files || [];
    const mf = fs.find((f) => f.name === 'skill.json' || f.name.toLowerCase() === 'zxplugin.json');
    if (mf) {
      try {
        const j = JSON.parse(mf.content || '{}');
        if (d.kind === 'skill' && j.id) return String(j.id);
        if (d.kind === 'plugin' && j.name) return String(j.name);
      } catch (_) { /* validation result carries malformed manifest details */ }
    }
    const py = fs.find((f) => /\.py$/i.test(f.name));
    return py ? py.name.replace(/\.py$/i, '') : (d.kind === 'skill' ? '（由 skill.json 决定）' : '（由插件文件名决定）');
  }
  // Keep transport failures visible: API.pick stores non-JSON failures in _err,
  // not err. Include status and the already-sanitized request origin when available.
  function creatorActionError(r, fallback) {
    const detail = String(r && (r.err || (r.install && r.install.err) || r._err || r.text) || '').trim();
    const status = Number(r && (r._status || r.status)) || 0;
    const target = String(r && (r._target || r.target) || '').trim();
    const parts = [];
    if (status > 0) parts.push('HTTP ' + status);
    if (target) parts.push(target);
    if (detail) parts.push(detail);
    return String(fallback || '操作失败') + '：' + (parts.join(' · ') || '未知错误');
  }
  // 一条草稿的完整卡片（聊天气泡形态：外层 .turn/.bdy，内部复用 .card）
  function draftCardHtml(d) {
    const v = d.validation || {};
    const errs = v.errors || [];
    const warns = v.warnings || [];
    const okBadge = v.ok ? tag('ok', '通过') : tag('err', '未通过 ' + errs.length);
    const errHtml = errs.length
      ? '<ul style="margin:8px 0;padding-left:20px">' + errs.map((e) =>
          '<li><b>' + esc(e.code || '') + '</b>：' + esc(e.message || '') + '</li>').join('') + '</ul>'
      : '';
    const warnHtml = warns.length
      ? '<ul style="margin:8px 0;padding-left:20px" class="muted">' + warns.map((w) =>
          '<li>' + esc(w.code || '') + '：' + esc(w.message || '') + '</li>').join('') + '</ul>'
      : '';
    return '<div class="turn crturn" data-crid="' + esc(d.id) + '"><div class="bdy">' +
      '<div class="card">' +
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
          '<span class="tag">' + esc(d.kind === 'skill' ? '技能' : '插件') + '</span>' +
          '<strong>' + esc(d.summary || '未命名草稿') + '</strong>' +
          '<span class="muted mono" style="font-size:12px">' + esc(d.id) + ' · v' + esc(String(d.version)) + '</span>' +
          '<span class="muted" style="font-size:12px">模型：' + esc(d.model || '?') + '</span>' +
          '<div style="flex:1"></div>' +
          '<button class="btn ghost" id="crReval">重新验证</button>' +
          (v.ok ? '<button class="btn" id="crReview" aria-expanded="false">查看安装步骤</button>' : '') +
          '<button class="btn danger" id="crDel">删除草稿</button>' +
        '</div>' +
        '<p class="muted" style="font-size:12px;margin:8px 0 0">需求：' + esc(d.requirement || '') + '</p>' +
        '<p style="margin:8px 0 0"><b>用途：</b>' + esc(d.summary || '（模型没给）') + '</p>' +
        '<p style="margin:4px 0 0"><b>风险与限制：</b>' + esc(d.risks || '无') + '</p>' +
        '<p class="muted" style="font-size:12px;margin:8px 0 0">' +
          '静态验证检查插件结构、Manifest 与聊天工具契约，不执行生成的代码；' +
          '<b>通过 ≠ 沙箱安全</b>。安装前仍需人工阅读上面的代码。</p>' +
      '</div>' +
      '<div class="card" style="margin-top:8px">' +
        '<div class="pkg-h">静态验证 ' + okBadge + '</div>' +
        errHtml + warnHtml +
        (!v.ok ? '<p class="muted" style="font-size:12px;margin-top:8px">验证未通过，安装步骤暂不可用。请先修复上面的错误，再点击“重新验证”。</p>' : '') +
        (!errs.length && !warns.length ? '<p class="muted" style="font-size:12px">没有错误或警告。</p>' : '') +
      '</div>' +
      (v.ok ? '<div class="cr-review-drawer" id="crReviewBox" role="region" aria-label="安装步骤与人工审核" hidden>' +
        '<div class="cr-review-head"><div class="pkg-h">安装前人工审核</div>' +
        '<button class="btn ghost" id="crCancel" type="button" aria-label="关闭安装步骤">关闭</button></div>' +
        '<p style="margin:6px 0"><b>安装目标：</b><span id="crTarget">' + esc(creatorTargetOf(d)) + '</span></p>' +
        '<p class="muted" style="font-size:12px;margin:6px 0">将使用现有 ' + (d.kind === 'skill' ? 'Skill Host' : 'Plugin') +
        ' 事务安装。静态验证不会执行生成代码；技能热重载可能立即加载入口代码，请确认风险说明和完整代码后再批准。</p>' +
        '<div id="crApprovalState" class="muted" style="font-size:12px;margin:6px 0">尚未批准。审批有效期 10 分钟，且只能使用一次。</div>' +
        '<div style="display:flex;gap:8px;align-items:center;margin-top:8px">' +
          '<button class="btn" id="crApproveNow">明确批准</button>' +
          '<button class="btn" id="crInstallNow" style="display:none">仅本次安装</button>' +
        '<button class="btn ghost" id="crCancelAction">取消</button>' +
        '</div></div>' : '') +
      (d.files || []).map((f, i) => {
        const content = String(f.content || '');
        const lines = content.split(/\r?\n/);
        const preview = lines.slice(0, 6).join('\n');
        const omitted = Math.max(0, lines.length - 6);
        return '<div class="card cr-file-card" style="margin-top:8px">' +
          '<div class="cr-file-head">' +
            '<div class="cr-file-title">' +
              icon('clip', 'sm') + ' <strong>' + esc(f.name) + '</strong>' +
              '<span class="muted cr-file-size">' + content.length + ' 字符</span>' +
              '<span class="tag neutral">' + esc(creatorLangOf(f.name)) + '</span>' +
            '</div>' +
            '<div class="cr-file-actions">' +
              '<button class="btn ghost" type="button" data-cr-file-toggle="' + i + '" aria-expanded="false">展开源码</button>' +
              '<button class="btn ghost" type="button" data-copy-file="' + i + '">复制完整源码</button>' +
            '</div>' +
          '</div>' +
          '<div class="cr-file-preview" data-cr-file-preview="' + i + '">' +
            '<pre>' + esc(preview || '（空文件）') + '</pre>' +
            '<div class="cr-file-more">' + (omitted ? '预览前 6 行 · 还有 ' + omitted + ' 行' : '共 ' + lines.length + ' 行') + '</div>' +
          '</div>' +
          '<div class="cr-file-full" data-cr-file-body="' + i + '" hidden></div>' +
        '</div>';
      }).join('') +
      '<div class="card" style="margin-top:8px">' +
        '<div class="pkg-h">修改草稿</div>' +
        '<textarea id="crFb" rows="2" style="width:100%;box-sizing:border-box" ' +
          'placeholder="告诉它要改什么，例如：把提醒文案改成「该喝水了」、间隔改成 30 分钟。"></textarea>' +
        '<div style="display:flex;gap:8px;align-items:center;margin-top:8px">' +
          '<button class="btn" id="crRev">提交修改（生成新版本）</button>' +
          '<span class="muted" style="font-size:12px">旧版本会保存在草稿工作区的 .history/ 里。</span>' +
        '</div>' +
      '</div>' +
      '</div></div>';
  }
  // 绑定一张草稿卡上的交互。root 是 .crturn 元素；任何改动后整卡重绘、
  // 重新绑一次（旧元素已丢弃，监听不会叠加）。
  function bindDraftCard(root, d, onGone) {
    root.addEventListener('click', async (e) => {
      const fileToggle = e.target.closest('[data-cr-file-toggle]');
      if (fileToggle) {
        const index = String(fileToggle.dataset.crFileToggle || '');
        const body = root.querySelector('[data-cr-file-body="' + index + '"]');
        const preview = root.querySelector('[data-cr-file-preview="' + index + '"]');
        const file = (d.files || [])[Number(index)];
        if (!body || !preview || !file) return;
        const opening = body.hidden;
        if (opening && body.dataset.rendered !== 'true') {
          const content = String(file.content || '');
          body.innerHTML = '<div class="cr-code"><div class="md">' +
            U.md(creatorFenceFor(content) + creatorLangOf(file.name) + '\n' + content + '\n' + creatorFenceFor(content), { codeActions: true }) +
            '</div></div>';
          body.dataset.rendered = 'true';
        }
        body.hidden = !opening;
        preview.hidden = opening;
        fileToggle.textContent = opening ? '收起源码' : '展开源码';
        fileToggle.setAttribute('aria-expanded', String(opening));
        return;
      }
      const copyFileButton = e.target.closest('[data-copy-file]');
      if (copyFileButton) {
        const file = (d.files || [])[Number(copyFileButton.dataset.copyFile)];
        if (!file) { toast('找不到要复制的文件'); return; }
        try {
          await navigator.clipboard.writeText(String(file.content || ''));
          copyFileButton.textContent = '已复制';
          setTimeout(() => { if (copyFileButton.isConnected) copyFileButton.textContent = '复制完整源码'; }, 1500);
        } catch (_) { toast('复制失败，请检查剪贴板权限'); }
        return;
      }
      const button = e.target.closest('[data-copy-code]');
      if (button) {
        try {
          await navigator.clipboard.writeText(button.closest('.codeblock').querySelector('code').textContent);
          button.textContent = '已复制';
          setTimeout(() => { if (button.isConnected) button.textContent = '复制'; }, 1500);
        } catch (_) { toast('复制失败，请选中代码手动复制'); }
        return;
      }
      if (e.target.closest('#crReval')) {
        const r = pick(await API.post('/api/creator/validate', { id: d.id }));
        if (!r || !r.ok || !r.validation) { toast('验证失败：' + ((r && r.err) || '未知错误')); return; }
        redrawCard(root, Object.assign({}, d, { validation: r.validation }));
        toast(r.validation.ok ? '静态验证通过' : '有 ' + (r.validation.errors || []).length + ' 个问题');
        return;
      }
      if (e.target.closest('#crReview')) {
        const box = root.querySelector('#crReviewBox');
        if (!box) { toast('当前草稿未通过静态验证，不能安装'); return; }
        const opening = box.hidden;
        box.hidden = !opening;
        const reviewButton = root.querySelector('#crReview');
        if (reviewButton) {
          reviewButton.textContent = opening ? '收起安装步骤' : '查看安装步骤';
          reviewButton.setAttribute('aria-expanded', String(opening));
        }
        if (opening) {
          toast('安装需要两步确认：先点击“明确批准”，再点击“仅本次安装”');
        }
        return;
      }
      if (e.target.closest('#crCancel, #crCancelAction')) {
        const box = root.querySelector('#crReviewBox');
        if (box) box.hidden = true;
        const reviewButton = root.querySelector('#crReview');
        if (reviewButton) {
          reviewButton.textContent = '查看安装步骤';
          reviewButton.setAttribute('aria-expanded', 'false');
        }
        return;
      }
      if (e.target.closest('#crApproveNow')) {
        const approveButton = e.target.closest('#crApproveNow');
        const approvalState = root.querySelector('#crApprovalState');
        approveButton.disabled = true;
        approveButton.textContent = '审批中…';
        if (approvalState) approvalState.textContent = '正在创建一次性审批，请稍候…';
        let r;
        try {
          r = pick(await API.post('/api/creator/approve', { id: d.id }));
        } catch (error) {
          approveButton.disabled = false;
          approveButton.textContent = '明确批准';
          const detail = creatorActionError({err: error && error.message ? error.message : 'Brain 连接失败或请求超时'}, '审批请求失败');
          if (approvalState) approvalState.textContent = detail;
          toast(detail);
          return;
        }
        if (!r || !r.ok || !r.approval || !r.approval.approval_id) {
          approveButton.disabled = false;
          approveButton.textContent = '明确批准';
          const detail = creatorActionError(r, '审批失败');
          if (approvalState) approvalState.textContent = detail;
          toast(detail);
          return;
        }
        if (approvalState) approvalState.textContent = '已批准草稿 v' + r.approval.version + '，审批编号 ' + String(r.approval.approval_id).slice(0, 14) + '…；请点击“仅本次安装”完成一次性消费。';
        approveButton.style.display = 'none';
        const installButton = root.querySelector('#crInstallNow');
        if (installButton) { installButton.style.display = 'inline-block'; installButton.dataset.approval = r.approval.approval_id; }
        toast('已记录人工批准，等待一次性安装');
        return;
      }
      if (e.target.closest('#crInstallNow')) {
        const installButton = e.target.closest('#crInstallNow');
        const aid = installButton.dataset.approval || '';
        if (!aid) { toast('请先点击“明确批准”生成一次性审批'); return; }
        if (!await confirmDialog('确认仅本次安装到“' + creatorTargetOf(d) + '”？安装可能触发 Skill Host 热重载。')) return;
        const approvalState = root.querySelector('#crApprovalState');
        installButton.disabled = true;
        installButton.textContent = '安装中…';
        if (approvalState) approvalState.textContent = '正在安装到“' + creatorTargetOf(d) + '”，请稍候…';
        let r;
        try {
          r = pick(await API.post('/api/creator/install', { approval_id: aid }));
        } catch (error) {
          installButton.disabled = false;
          installButton.textContent = '仅本次安装';
          const detail = creatorActionError({err: error && error.message ? error.message : 'Brain 连接失败或请求超时'}, '安装请求失败');
          if (approvalState) approvalState.textContent = detail;
          toast(detail);
          return;
        }
        if (!r || !r.ok) {
          const detail = creatorActionError(r, '安装失败');
          const status = Number(r && (r._status || r.status)) || 0;
          installButton.dataset.approval = '';
          if (status >= 400 && status < 500) {
            installButton.textContent = '需重新批准';
            if (approvalState) approvalState.textContent = detail + '。请重新验证草稿后，再次明确批准并安装。';
          } else {
            installButton.textContent = '结果待确认';
            if (approvalState) approvalState.textContent = detail + '。安装结果暂不确定，请先刷新插件列表确认，避免重复安装。';
          }
          toast(detail);
          return;
        }
        const installResult = r.install || {};
        const installedName = String(installResult.package || installResult.name ||
          (r.approval && r.approval.target_name) || creatorTargetOf(d));
        const isPlugin = d.kind !== 'skill';
        // The Creator route deliberately commits files without importing newly supplied code.
        // plugin_ops reports restart_needed/dynamic_loaded, so don't claim the Agent can use it yet.
        const restartNeeded = installResult.restart_needed === true ||
          (isPlugin && installResult.dynamic_loaded === false);
        let installStatus;
        if (isPlugin && restartNeeded) {
          installStatus = '安装成功：' + installedName + '.py 已写入 Brain 插件目录。当前运行中的 Brain 尚未加载它；重启 Brain 后，Agent 才能调用插件声明的 TOOLS / TOOL_SPECS。审批已消费，不能重复使用。';
        } else if (isPlugin) {
          installStatus = '安装成功：' + installedName + '.py 已写入 Brain 插件目录。请到插件管理页确认运行状态和工具清单，再在聊天中描述要执行的任务。审批已消费，不能重复使用。';
        } else if (restartNeeded) {
          installStatus = '安装成功：' + installedName + ' 已写入技能目录，但需要重启技能宿主后才能完全生效。审批已消费，不能重复使用。';
        } else {
          installStatus = '安装成功：' + installedName + ' 已写入技能目录。请到技能管理页确认宿主已加载，再在聊天中描述要执行的任务。审批已消费，不能重复使用。';
        }
        if (approvalState) approvalState.textContent = installStatus;
        installButton.textContent = restartNeeded ? '已安装 · 待重启' : '已安装';
        installButton.disabled = true;
        if (typeof window.__vigilRefreshExtensions === 'function') window.__vigilRefreshExtensions();
        toast(installStatus);
        return;
      }
      if (e.target.closest('#crRev')) {
        const fb = (root.querySelector('#crFb').value || '').trim();
        if (!fb) { toast('先写修改意见'); return; }
        const requestId = 'req_' + crypto.randomUUID();
        rememberTurn(requestId, {input:fb,kind:d.kind || 'plugin',status:'pending'});
        const r = pick(await API.post('/api/creator/jobs', { kind: d.kind || 'plugin',
          draft_id: d.id, feedback: fb, request_id: requestId,
          src: 'pc', conversation_id: activeConversationId }));
        if (r.ok && r.job_id) { await watchCreatorJob(root, r); return; }
        if (!r || !r.ok || !r.draft) {
          const failure = creatorRequestFailure(r);
          rememberTurn(requestId, failure);
          toast('修改失败：' + creatorErrorText(failure.error));
          return;
        }
        redrawCard(root, r.draft);
        toast('已生成新版本 v' + r.draft.version);
        return;
      }
      if (e.target.closest('#crDel')) {
        if (!await confirmDialog('删除草稿 ' + d.id + '？此操作不可恢复。')) return;
        const r = pick(await API.post('/api/creator/delete', { id: d.id }));
        if (!r || !r.ok) { toast('删不掉：' + ((r && r.err) || '未知错误')); return; }
        root.remove();
        toast('草稿已删除');
        onGone && onGone();
      }
    });
  }
  function redrawCard(root, d) {
    root.outerHTML = draftCardHtml(d);
    const fresh = document.querySelector('.crturn[data-crid="' + d.id + '"]');
    if (fresh) bindDraftCard(fresh, d);
  }
  // 把一张草稿卡追加到聊天流末尾（草稿列表点条目时用）
  async function openDraftInChat(id) {
    const r = pick(await API.get('/api/creator/draft?id=' + encodeURIComponent(id)));
    if (!r || !r.ok || !r.draft) { toast('打不开草稿：' + ((r && r.err) || '未知错误')); return; }
    const holder = document.createElement('div');
    holder.innerHTML = draftCardHtml(r.draft);
    const node = holder.firstChild;
    const box = document.querySelector('#msgs');
    if (!box) return;
    box.appendChild(node);
    node.scrollIntoView({ block: 'center', behavior: 'smooth' });
    bindDraftCard(node, r.draft);
  }


  async function renderPluginMarket(ctx) {
    ctx.topbar.innerHTML =
      '<span class="tag">§ 插件市场</span>' +
      '<div style="flex:1"></div>' +
      '<button class="btn ghost" id="pmRefresh">刷新市场</button>' +
      '<span class="muted" id="pmRepo" style="margin-left:12px"></span>';

    ctx.view.innerHTML =
      '<div class="wrap">' +
      '<div id="pmGrid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px"></div>' +
      '<div id="pmEmpty" class="muted" style="text-align:center;padding:40px;display:none">市场为空</div>' +
      '</div>';

    let marketData = null;

    async function load() {
      const grid = ctx.view.querySelector('#pmGrid');
      grid.innerHTML = '<div class="muted">加载中...</div>';
      try {
        const r = await API.get('/api/market/list');
        if (!r || !r.ok || !r.json) {
          grid.innerHTML = '<div class="muted">加载失败：' + esc((r && r.err) || '未知错误') + '</div>';
          return;
        }
        marketData = r.json;
        ctx.topbar.querySelector('#pmRepo').textContent =
          '已安装 ' + (r.json.installed_count || 0) + ' 个';
        draw();
      } catch (e) {
        grid.innerHTML = '<div class="muted">加载失败：' + esc(e.message) + '</div>';
      }
    }

    function draw() {
      const grid = ctx.view.querySelector('#pmGrid');
      const empty = ctx.view.querySelector('#pmEmpty');
      const plugins = (marketData && marketData.plugins) || [];
      if (!plugins.length) {
        grid.innerHTML = '';
        empty.style.display = 'block';
        return;
      }
      empty.style.display = 'none';
      grid.innerHTML = plugins.map(function (p) {
        const tags = (p.tags || []).map(function (t) {
          return '<span class="tag" style="margin-right:4px;margin-bottom:4px">' + esc(t) + '</span>';
        }).join('');
        const btn = p.installed
          ? '<button class="btn danger" data-action="uninstall" data-name="' + esc(p.name) + '">卸载</button>'
          : '<button class="btn" data-action="install" data-name="' + esc(p.name) + '">安装</button>';
        const badge = p.installed
          ? '<span class="tag" style="background:var(--ok-bg);color:var(--ok);margin-left:8px">已安装</span>'
          : '';
        return (
          '<div class="card" style="display:flex;flex-direction:column;gap:8px">' +
          '<div style="display:flex;align-items:center;gap:8px">' +
          '<span style="font-size:20px">📦</span>' +
          '<strong>' + esc(p.name) + '</strong>' +
          '<span class="muted" style="font-size:12px">v' + esc(p.version || '?') + '</span>' +
          badge +
          '</div>' +
          '<div class="muted" style="font-size:12px">' + esc(p.author || '未知') + '</div>' +
          '<div style="flex:1;font-size:13px;line-height:1.5">' + esc(p.desc || '') + '</div>' +
          '<div style="margin-top:4px">' + tags + '</div>' +
          '<div style="display:flex;gap:8px;margin-top:8px">' + btn + '</div>' +
          '</div>'
        );
      }).join('');

      // 绑定按钮事件
      grid.querySelectorAll('button[data-action]').forEach(function (btn) {
        btn.addEventListener('click', async function () {
          const action = btn.dataset.action;
          const name = btn.dataset.name;
          btn.disabled = true;
          btn.textContent = action === 'install' ? '安装中...' : '卸载中...';
          try {
            const r = await API.post('/api/market/' + action, { name: name });
            if (r && r.ok && r.json && r.json.ok) {
              await load();
            } else {
              toast((r && r.json && r.json.err) || '操作失败');
              btn.disabled = false;
              btn.textContent = action === 'install' ? '安装' : '卸载';
            }
          } catch (e) {
            toast('操作失败：' + e.message);
            btn.disabled = false;
            btn.textContent = action === 'install' ? '安装' : '卸载';
          }
        });
      });
    }

    ctx.topbar.querySelector('#pmRefresh').addEventListener('click', async function () {
      const btn = ctx.topbar.querySelector('#pmRefresh');
      btn.disabled = true;
      btn.textContent = '刷新中...';
      try {
        await API.post('/api/market/refresh', {});
      } catch (_) { /* 刷新失败不阻塞 */ }
      await load();
      btn.disabled = false;
      btn.textContent = '刷新市场';
    });

    await load();
  }

  global.PAGES = [
    { id: 'chat', name: '工作台', icon: 'chat', render: renderChat },
    ...FUNC_GROUPS.map((g) => ({ id: g.id, name: g.name, icon: g.icon, render: makeGroupPage(g) })),
    { id: 'management', name: '统一管理', icon: 'mon', render: renderManagement },
    { id: 'data', name: '数据', icon: 'chart', render: renderData },
  ];
})(window);
