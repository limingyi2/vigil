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
  function clearLocalDisplayRecordsForChannel(src = state.src || 'pc', sessionIds = []) {
    const cfg = state.cfg || {};
    const identity = [state.base || cfg.base || '', cfg.device_id || '', cfg.token_kind || '', src || 'pc'];
    const sessions = new Set((Array.isArray(sessionIds) ? sessionIds : []).map(String));
    const prefix = 'vigil.display.v1:';
    const keys = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key || !key.startsWith(prefix)) continue;
      const scopeKey = key.endsWith(':cleared') ? key.slice(0, -8) : key;
      try {
        const scope = JSON.parse(scopeKey.slice(prefix.length));
        if (Array.isArray(scope) && identity.every((part, index) => scope[index] === part) && sessions.has(String(scope[4] || ''))) keys.push(key);
      } catch (_) { /* Ignore malformed or unrelated local records. */ }
    }
    keys.forEach((key) => localStorage.removeItem(key));
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
    return '<div class="agent-progress"><div class="agent-elapsed" role="status"></div><div class="live-steps" id="liveSteps" hidden></div></div>' +
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
    // One structured task card, never the raw model response. The full text is
    // still stored by the backend; the chat surface only shows progress facts.
    root.dataset.scope = scope;
    root.innerHTML = '<div class="bdy">' + creatorTaskCardHtml(initial) + '</div>';
    const card = creatorCard(root, initial);
    let current = initial, cursor = 0, finished = false, cancelState = 'idle', lost = false;
    const streamApi = global.vigil && (global.vigil.onStreamEvent || global.vigil.onCreatorEvent);
    function applySnapshot(item) {
      current = item;
      cursor = Math.max(cursor, item.event_cursor || 0);
      card.update(item);
      rememberTurn(item.request_id, {input:item.feedback || item.requirement,kind:item.kind,
        job_id:jid,status:item.status,error:item.error}, scope);
      finished = ['succeeded', 'failed', 'cancelled', 'interrupted'].includes(item.status);
      if (finished) {
        cancelState = item.status === 'cancelled' ? 'done' : 'idle';
        card.setCancel({hidden: true});
      }
      if (item.status === 'cancelling' && cancelState === 'idle') cancelState = 'waiting';
      if (cancelState === 'sending' || cancelState === 'waiting') {
        card.setCancel({label: '正在取消…', disabled: true});
      } else if (cancelState === 'failed') {
        card.setCancel({label: '重试取消', disabled: false});
      }
    }
    async function requestCancel() {
      if (cancelState === 'sending' || cancelState === 'waiting' || finished) return;
      cancelState = 'sending';
      card.setCancel({label: '正在取消…', disabled: true});
      card.note('已发送取消请求，等待模型连接关闭…', 'warn');
      card.log('请求取消（后端确认前不会显示已取消）');
      let r;
      try { r = pick(await post('/api/creator/jobs/' + jid + '/cancel', {}, {timeoutMs: 8000})); }
      catch (e) { r = {ok:false, _err:String((e && e.message) || e)}; }
      if (r && (r._status === 409 || (r.cancel_state === 'too_late'))) {
        cancelState = 'idle';
        card.setCancel({hidden: true});
        card.note('任务已经结束，无法取消：' + (r.err || '结果以下方状态为准'), 'err');
        toast('任务已结束，无法取消');
        applySnapshot(Object.assign({}, current, r.job_id ? r : {}));
        return;
      }
      if (!r || !r.ok) {
        cancelState = 'failed';
        const reason = (r && (r._err || r.err)) || '后端未确认';
        card.note('取消失败：' + reason + '。任务可能仍在运行，可重试取消。', 'err');
        card.setCancel({label: '重试取消', disabled: false});
        toast('取消失败：' + reason);
        card.log('取消失败：' + reason);
        return;
      }
      card.log('后端已受理：' + (r.status || ''));
      applySnapshot(r);
      if (r.status !== 'cancelled') {
        cancelState = 'waiting';
        card.setCancel({label: '正在取消…', disabled: true});
        card.note('取消请求已受理，正在关闭模型连接…', 'warn');
      }
    }
    card.onCancel(requestCancel);
    const off = streamApi ? subscribeUntilDetached(root, streamApi, evt => {
      if (evt.job_id !== jid || finished || !root.isConnected || recordKey() !== scope) return;
      if (evt.type === 'snapshot') { applySnapshot(evt.payload); return; }
      if (evt.seq <= cursor) return;
      cursor = evt.seq;
      const p = evt.payload || {};
      if (evt.type === 'error') {
        card.log('失败：' + creatorErrorText(p));
        applySnapshot(Object.assign({}, current, {status:'failed', error:p, event_cursor:cursor}));
        return;
      }
      if (evt.type === 'stage') { card.update(current, p.stage); card.log('阶段：' + creatorStageLabel(p.stage)); }
      if (evt.type === 'text_delta') card.bytes(current);
      if (evt.type === 'usage' && p && typeof p === 'object') card.log('用量：' + JSON.stringify(p).slice(0, 120));
    }) : null;
    applySnapshot(initial);
    try {
      while (!finished && root.isConnected && recordKey() === scope) {
        const hasStream = global.vigil && global.vigil.apiStream && (global.vigil.onStreamEvent || global.vigil.onCreatorEvent);
        if (hasStream) {
          const sr = await global.vigil.apiStream('/api/creator/jobs/' + jid + '/events?after=' + cursor, null);
          if (sr && sr.ok === false) { card.note('与后端的实时连接中断，状态未确认；可重试连接', 'err'); }
        } else await new Promise(resolve => setTimeout(resolve, 500));
        if (!root.isConnected || recordKey() !== scope) break;
        const r = pick(await get('/api/creator/jobs/' + jid));
        if (!r.ok) {
          lost = true;
          card.note('连接中断，记录已保留在后台；可重试连接查询真实状态', 'err');
          card.setRetry(true);
          break;
        }
        applySnapshot(r);
      }
      if (current.status === 'succeeded' && current.draft_id && root.isConnected) {
        const r = pick(await get('/api/creator/draft?id=' + encodeURIComponent(current.draft_id)));
        if (r.ok && r.draft) { root.innerHTML = draftCardHtml(r.draft); bindDraftCard(root, r.draft); }
      }
      return current;
    } finally { if (off) off(); if (lost) card.setRetry(true); }
  }

  /** Rebuild a finished Creator job from durable state (after reload/restart). */
  async function restoreCreatorJob(root, job) {
    if (!root || !root.isConnected) return;
    if (job.status === 'succeeded' && job.draft_id) {
      const r = pick(await get('/api/creator/draft?id=' + encodeURIComponent(job.draft_id)));
      if (!root.isConnected) return;
      if (r.ok && r.draft) { root.innerHTML = draftCardHtml(r.draft); bindDraftCard(root, r.draft); return; }
      root.innerHTML = '<div class="bdy">' + creatorTaskCardHtml(job) + '</div>';
      const card = creatorCard(root, job);
      card.update(job);
      card.note('草稿已生成，但当前读不到草稿文件（可能已被删除）：' + (r._err || r.err || ''), 'err');
      return;
    }
    root.innerHTML = '<div class="bdy">' + creatorTaskCardHtml(job) + '</div>';
    const card = creatorCard(root, job);
    card.update(job);
    card.log('已从后端恢复该任务的状态');
  }

  const CREATOR_PHASES = [
    ['queued', '等待模型'], ['generating', '生成中'], ['validating', '验证中'], ['done', '完成'],
  ];
  function creatorStageLabel(stage) {
    return ({queued:'等待模型', generating:'生成中', validating:'验证中', assembling:'整理产物',
      cancelling:'正在取消', cancelled:'已取消', failed:'失败', succeeded:'已完成'})[stage] || String(stage || '');
  }
  function creatorPhase(job) {
    const s = String((job && job.status) || 'queued');
    const stage = String((job && job.stage) || '');
    if (s === 'queued') return {key:'queued', step:0, label:'等待模型', tone:'idle'};
    if (s === 'cancelling') return {key:'cancelling', step:1, label:'正在取消', tone:'warn'};
    if (s === 'cancelled') return {key:'cancelled', step:1, label:'已取消', tone:'muted'};
    if (s === 'interrupted') return {key:'interrupted', step:1, label:'已中断', tone:'muted'};
    if (s === 'failed') return {key:'failed', step:2, label:'失败', tone:'err'};
    if (s === 'succeeded') return {key:'succeeded', step:3, label:'完成', tone:'ok'};
    if (stage === 'validating' || stage === 'assembling') return {key:'validating', step:2, label:'验证中', tone:'run'};
    return {key:'generating', step:1, label:'生成中', tone:'run'};
  }
  function creatorNeed(job) {
    const text = String((job && (job.requirement || job.feedback)) || '').replace(/\s+/g, ' ').trim();
    return text.length > 72 ? text.slice(0, 72) + '…' : (text || '未填写需求');
  }
  function creatorTaskCardHtml(job) {
    const phase = creatorPhase(job);
    return '<div class="card cr-task" data-cr-task="' + esc(job.job_id || '') + '">' +
      '<div class="cr-task-top">' +
        '<span class="tag neutral">' + esc(job.kind === 'skill' ? '技能' : '插件') + '</span>' +
        '<div class="cr-task-title"><b>' + esc(creatorNeed(job)) + '</b>' +
          '<span class="muted mono">' + esc(String(job.job_id || '').slice(0, 18)) + '</span></div>' +
        '<span class="cr-phase ' + phase.tone + '" data-cr-phase><i></i><span></span></span>' +
      '</div>' +
      '<div class="cr-rail">' + CREATOR_PHASES.map(([key, label], i) =>
        '<span class="cr-step" data-step="' + i + '">' + esc(label) + '</span>').join('') + '</div>' +
      '<div class="cr-task-note" data-cr-note role="status"></div>' +
      '<div class="cr-task-foot">' +
        '<span class="muted cr-meter" data-cr-meter>已用时 0 秒</span>' +
        '<span class="sp"></span>' +
        '<button class="btn ghost cr-retry" type="button" data-cr-retry hidden>重试连接</button>' +
        '<button class="btn ghost cr-log-toggle" type="button" data-cr-log-toggle aria-expanded="false">状态日志</button>' +
        '<button class="btn oline cr-cancel" type="button" data-cr-cancel>取消生成</button>' +
      '</div>' +
      '<div class="cr-log" data-cr-log hidden><div class="cr-log-scroll"><ol class="cr-log-list"></ol></div></div>' +
      '</div>';
  }
  /** Bind the live task card. Only progress facts are rendered — never the model text. */
  function creatorCard(root, initial) {
    const q = (s) => root.querySelector(s);
    const phaseEl = q('[data-cr-phase]'), noteEl = q('[data-cr-note]'), meterEl = q('[data-cr-meter]');
    const cancelEl = q('[data-cr-cancel]'), logEl = q('[data-cr-log]'), logList = q('.cr-log-list');
    const logToggle = q('[data-cr-log-toggle]'), retryEl = q('[data-cr-retry]');
    const startedAt = (Number(initial.created_at) || Date.now() / 1000) * 1000;
    const byteLen = (text) => {
      try { return new TextEncoder().encode(String(text || '')).length; } catch (_) { return String(text || '').length; }
    };
    let handler = null, sizes = byteLen(initial.content);
    const written = [];
    function log(line) {
      if (!logList) return;
      const at = new Date().toLocaleTimeString('zh-CN', {hour12:false});
      written.push(at + '  ' + String(line || '').slice(0, 160));
      while (written.length > 60) written.shift();
      logList.innerHTML = written.map((row) => '<li>' + esc(row) + '</li>').join('');
      const scroll = logEl && logEl.querySelector('.cr-log-scroll');
      if (scroll) scroll.scrollTop = scroll.scrollHeight;
    }
    function meter(item) {
      const seconds = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
      const kb = (sizes / 1024).toFixed(1);
      const stopped = ['succeeded', 'failed', 'cancelled', 'interrupted'].includes(String(item && item.status));
      if (meterEl) meterEl.textContent = '已用时 ' + seconds + ' 秒 · 模型返回 ' + kb + ' KB（不在此处展示源码）' +
        (stopped ? '' : ' · 实时更新');
    }
    function update(item, stageOverride) {
      const phase = creatorPhase(item);
      const status = String((item && item.status) || '');
      if (phaseEl) {
        phaseEl.className = 'cr-phase ' + phase.tone;
        const label = phaseEl.querySelector('span');
        if (label) label.textContent = stageOverride ? creatorStageLabel(stageOverride) : phase.label;
      }
      root.querySelectorAll('.cr-step').forEach((node) => {
        const index = Number(node.dataset.step);
        const reached = phase.step;
        node.classList.toggle('done', index < reached || phase.key === 'succeeded');
        node.classList.toggle('on', index === reached && !['succeeded','failed','cancelled','interrupted'].includes(phase.key));
        node.classList.toggle('bad', index === reached && phase.key === 'failed');
      });
      // The button follows the durable status, so a card rebuilt after a reload
      // or a restart can never offer "取消" on a job that already ended.
      if (['succeeded', 'failed', 'cancelled', 'interrupted'].includes(status)) {
        setCancel({hidden: true});
      } else if (status === 'cancelling') {
        setCancel({label: '正在取消…', disabled: true});
      }
      if (item && item.error) note(creatorErrorText(item.error), 'err');
      else if (item && item.status === 'cancelled') note(item.cancel_note || '已取消，未生成任何草稿', 'muted');
      else if (item && item.status === 'interrupted') note('执行已中断，未自动重新生成', 'muted');
      else if (item && item.status === 'cancelling') note('正在关闭模型连接…', 'warn');
      meter(item);
    }
    function note(text, tone) {
      if (!noteEl) return;
      noteEl.textContent = String(text || '');
      noteEl.dataset.tone = tone || '';
      noteEl.setAttribute('role', tone === 'err' ? 'alert' : 'status');
    }
    function setCancel(opts) {
      const o = opts || {};
      if (!cancelEl) return;
      cancelEl.hidden = !!o.hidden;
      cancelEl.disabled = !!o.disabled;
      cancelEl.textContent = o.label || '取消生成';
    }
    function setRetry(show) { if (retryEl) retryEl.hidden = !show; }
    if (logToggle && logEl) logToggle.onclick = () => {
      const open = logEl.hidden;
      logEl.hidden = !open;
      logToggle.setAttribute('aria-expanded', String(open));
      logToggle.textContent = open ? '收起日志' : '状态日志';
    };
    if (cancelEl) cancelEl.onclick = (e) => { e.preventDefault(); if (handler) handler(); };
    if (retryEl) retryEl.onclick = () => {
      if (!root.isConnected) return;
      setRetry(false);
      void watchCreatorJob(root, currentJob, root.dataset.scope || recordKey());
    };
    let currentJob = initial;
    log('任务已提交，等待模型');
    const api = {
      update(item, stageOverride) {
        currentJob = item || currentJob;
        update(currentJob, stageOverride);
        return api;
      },
      note, log, setCancel, setRetry,
      bytes(item) {
        sizes = Math.max(sizes, byteLen(item && item.content));
        meter(item);
      },
      onCancel(fn) { handler = fn; },
    };
    return api;
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
    return '<h1 class="welcome-brand">Vigil</h1>' +
      '<p>与更强大的 AI，一起探索无限可能</p>';
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
    const contextToggle = view.querySelector('#mqContextToggle');
    const contextMenu = view.querySelector('#mqContextMenu');

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
    function drawDetails() {
      const policy = data.context_window;
      const value = view.querySelector('#mqContextValue');
      const note = view.querySelector('#mqContextNote');
      if (value) value.textContent = policy ? policy.label : '读取中…';
      if (contextToggle) contextToggle.disabled = !policy || switching;
      if (note) note.textContent = '';
      contextMenu.querySelectorAll('[data-context-tier]').forEach((button) => {
        const checked = !!policy && Number(button.dataset.contextTier) === policy.selected;
        button.setAttribute('aria-checked', String(checked));
        button.querySelector('.mq-check').innerHTML = checked ? icon('check', 'sm') : '';
        button.disabled = switching;
      });
    }
    function toggleContext(show, focus) {
      contextMenu.hidden = !show;
      contextToggle.setAttribute('aria-expanded', String(show));
      placePop();
      if (show && focus) (contextMenu.querySelector('[aria-checked="true"]') || contextMenu.querySelector('button')).focus();
    }
    async function selectContext(tier) {
      if (switching || (h.isBusy && h.isBusy())) { say('请等当前回复完成再调整窗口', true); return; }
      switching = true; drawDetails(); say('正在保存上下文窗口…');
      try {
        const r = pick(await post('/api/models/context', { tier }));
        if (!r || !r.ok) { say('保存失败：' + ((r && (r.err || r._err)) || '未知错误'), true); return; }
        data.context_window = r.context_window;
        say('已保存 · 实际生效：' + r.context_window.label);
      } catch (_) { say('上下文窗口保存失败，请重试', true); }
      finally { switching = false; drawDetails(); placePop(); }
    }
    if (contextToggle) {
      contextToggle.addEventListener('click', () => toggleContext(contextMenu.hidden, false));
      contextToggle.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); toggleContext(true, true); }
      });
      contextMenu.querySelectorAll('[data-context-tier]').forEach((button) => {
        button.addEventListener('click', () => selectContext(Number(button.dataset.contextTier)));
      });
      contextMenu.addEventListener('keydown', (e) => {
        const buttons = Array.from(contextMenu.querySelectorAll('button'));
        const i = buttons.indexOf(document.activeElement);
        if (e.key === 'Escape' || e.key === 'ArrowLeft') {
          e.preventDefault(); e.stopPropagation(); toggleContext(false); contextToggle.focus();
        } else if (['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) {
          e.preventDefault();
          buttons[e.key === 'Home' ? 0 : e.key === 'End' ? buttons.length - 1
            : (i + (e.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length].focus();
        }
      });
    }
    function draw() {
      drawDetails();
      const q = String((searchEl && searchEl.value) || '').trim().toLowerCase();
      const all = data.models || [];
      const rows = all.filter((m) => !q
        || (nameOf(m) + ' ' + provOf(m) + ' ' + m.model).toLowerCase().indexOf(q) >= 0);
      if (!rows.length) {
        listEl.innerHTML = '<div class="muted" style="padding:12px 0;font-size:12px">'
          + (all.length ? '没有匹配的模型' : '暂无模型') + '</div>';
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
          '<b>' + esc(nameOf(m)) + '</b>' +
          '</span>' +
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
      data = { models: r.models || [], current: r.current || {}, context_window: r.context_window };
      label();
      draw();
      if (open) placePop();
      if (h.afterLoad) { try { h.afterLoad(); } catch (_) {} }
    }
    async function select(m) {
      if (switching) return;
      if (h.isBusy && h.isBusy()) { say('正在生成回复：等这一轮结束再切，免得上面显示的模型和实际用的对不上', true); return; }
      // 本地/混合模式下模型由所选模型决定，默认模型清单在这里切了也不会生效
      if (h.blockOpen && h.blockOpen()) {
        say('现在用的是本地模型：先在输入框下方的模型菜单里切回默认模型', true);
        return;
      }
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
        drawDetails();
        if (open) placePop();
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
    function closePop() { open = false; pop.hidden = true; toggleContext(false); }
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
      // 本地/混合模式：这个按钮改开模式菜单，免得在这里切了云端模型却没生效
      if (h.blockOpen && h.blockOpen()) { if (h.onBlocked) h.onBlocked(); return; }
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
        global.vigil.settings.open({ section: 'model' });
      } else {
        state.settingsSub = 'model';
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
    topbar.innerHTML = '';

    // 会话由 Brain 持久化。localStorage 保留作旧版本兼容，但不会用于冷启动自动选中。
    // 工作区 = 一个真实的本地文件夹；会话只是它内部的对话，不是它本身。
    panel.innerHTML =
      '<div class="phead"><div class="session-brand">Vigil</div></div>' +
      // 侧栏只留“新对话”：打开文件夹统一放在输入区的“选择工作空间”里，
      // 同一个动作不在两处出现，用户也不用先在左栏找入口。
      '<div class="session-new"><button class="session-new-button" id="newTopicBtn" title="新建空白会话（不属于任何工作区）" aria-label="新对话">' + icon('chat', 'sm') + '<span>新对话</span></button></div>' +
      '<div class="plist" id="sessList"><div class="empty">还没有聊过</div></div>' +
      '';
    const sessList = panel.querySelector('#sessList');

    const SESS_KEY = 'vigil.chat.active.v2:' + String(state.base || '') + ':' + String((state.cfg || {}).device_id || '');
    const RENDERER_SESSION_KEY = 'vigil.chat.renderer-session.v1';
    const WORKSPACE_OPEN_KEY = 'vigil.workspaces.open.v1:' + String(state.base || '') + ':' + String((state.cfg || {}).device_id || '');
    let sessionItems = [];
    let workspaceItems = [];
    let workspaceError = '';
    const workspaceProbe = {};          // wid → {state:'ok'|'missing'|'permission'|'unknown'|'checking', detail}
    let workspaceOpen = {};
    try { workspaceOpen = JSON.parse(localStorage.getItem(WORKSPACE_OPEN_KEY) || '{}') || {}; } catch (_) { workspaceOpen = {}; }
    function saveWorkspaceOpen() {
      try { localStorage.setItem(WORKSPACE_OPEN_KEY, JSON.stringify(workspaceOpen)); } catch (_) {}
    }
    let activeConversationId = '';
    let draftActive = false;
    state.chatConversationId = '';
    let appInstanceId = '';
    let createConversationPromise = null;
    let rendererSelection = null;
    function rememberRendererSelection(id, mode) {
      rendererSelection = { app_instance: appInstanceId, conversation_id: id || '', mode: mode || (id ? 'conversation' : 'welcome') };
      try { localStorage.setItem(RENDERER_SESSION_KEY, JSON.stringify(rendererSelection)); } catch (_) {}
    }
    async function refreshWorkspaces() {
      workspaceError = '';
      if (API.runtime.preview) { workspaceItems = []; drawSessions(); return; }
      const r = pick(await get('/api/workspaces?src=pc'));
      if (!r.ok) {
        // An older Brain has no workspace API. Say so instead of drawing a fake list.
        workspaceItems = [];
        workspaceError = Number(r._status) === 404 ? '当前 Brain 版本没有工作区接口' : (r._err || '工作区列表加载失败');
        drawSessions();
        return;
      }
      workspaceItems = Array.isArray(r.items) ? r.items : [];
      drawSessions();
      void probeWorkspaces();
    }
    async function probeWorkspaces() {
      const api = global.vigil && global.vigil.workspace;
      for (const ws of workspaceItems.slice()) {
        if (!api || !api.probe) { workspaceProbe[ws.id] = {state:'unknown', detail:'当前环境无法检查目录'}; continue; }
        workspaceProbe[ws.id] = {state:'checking'};
        try {
          const p = await api.probe(ws.path);
          if (!p || !p.ok) workspaceProbe[ws.id] = {state:'missing', detail:(p && p.err) || '无法检查该目录'};
          else if (!p.exists) workspaceProbe[ws.id] = {state:'missing', detail: p.permission ? '无访问权限（可能已被移动）' : '目录不存在或已移动'};
          else if (p.directory === false) workspaceProbe[ws.id] = {state:'missing', detail:'这个路径不是文件夹'};
          else if (p.readable === false) workspaceProbe[ws.id] = {state:'permission', detail:'目录存在但当前用户无访问权限'};
          else workspaceProbe[ws.id] = {state:'ok', detail:''};
        } catch (e) {
          workspaceProbe[ws.id] = {state:'missing', detail:String((e && e.message) || e)};
        }
        drawSessions();
      }
    }
    async function refreshConversations() {
      const selectedAtRead=activeConversationId;
      if (API.runtime.preview) {
        const id = '00000000-0000-4000-8000-000000000001';
        sessionItems = [{id,title:'预览对话',created_at:Date.now()/1000,updated_at:Date.now()/1000,legacy:false}];
        if (activeConversationId && activeConversationId !== id) activeConversationId = '';
        state.chatConversationId = activeConversationId; drawSessions(); return activeConversationId;
      }
      const r = pick(await get('/api/conversations?src=pc'));
      if (!msgs.isConnected) return activeConversationId;
      if (!r.ok || !Array.isArray(r.items)) throw new Error(r._err || '会话列表加载失败');
      sessionItems = r.items.filter((item) => item.legacy || item.has_content !== false);
      if (!busy && !submitPending && activeConversationId===selectedAtRead && activeConversationId && !sessionItems.some((x) => x.id === activeConversationId)) activeConversationId = '';
      state.chatConversationId = activeConversationId;
      drawSessions();
      drawWorkspaceBar();
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

    /** 当前会话所属文件夹（没有就什么都不显示）——只做展示，不改归属。 */
    function drawWorkspaceBar() {
      const bar = view.querySelector('#chatWsBar');
      if (!bar) return;
      const conv = sessionItems.find((s) => s.id === activeConversationId);
      const ws = conv && conv.workspace_id ? workspaceItems.find((w) => w.id === conv.workspace_id) : null;
      if (!ws) { bar.hidden = true; bar.innerHTML = ''; drawComposerFoot(); return; }
      bar.hidden = false;
      bar.title = ws.path;
      bar.innerHTML = '<span class="ic">' + icon('folder', 'sm') + '</span>' +
        '<b>' + esc(ws.name) + '</b>' +
        '<button class="chat-ws-path" type="button" data-ws-path="' + esc(ws.path) + '" title="点击复制路径">' + esc(ws.path) + '</button>' +
        '<span class="chat-ws-note">仅有位置，不等于已授权读写</span>';
      const pathButton = bar.querySelector('[data-ws-path]');
      if (pathButton) pathButton.onclick = async () => {
        try { await navigator.clipboard.writeText(ws.path); toast('已复制路径：' + ws.path); }
        catch (_) { toast('复制失败，路径：' + ws.path); }
      };
      drawComposerFoot();
    }
    function workspaceRowHtml(ws) {
      const convs = sessionItems.filter((s) => s.workspace_id === ws.id);
      const probe = workspaceProbe[ws.id] || {state:'checking'};
      const hasActive = convs.some((s) => s.id === activeConversationId);
      const open = workspaceOpen[ws.id] !== undefined ? !!workspaceOpen[ws.id] : hasActive;
      const tone = probe.state === 'ok' ? '' : probe.state === 'checking' ? 'checking' : 'bad';
      const statusText = probe.state === 'ok' ? '' : probe.state === 'checking' ? '检查中…' : (probe.detail || '目录状态未知');
      return '<div class="ws-row' + (hasActive ? ' on' : '') + '" data-workspace="' + esc(ws.id) + '">' +
        '<button class="ws-open" data-ws-toggle="' + esc(ws.id) + '" aria-expanded="' + String(open) + '" title="' + esc(ws.path) + '">' +
          '<span class="ic">' + icon('folder', 'sm') + '</span>' +
          '<span class="tx"><b>' + esc(ws.name) + '</b>' +
            '<span class="ws-path" data-ws-copy="' + esc(ws.path) + '" title="点击复制真实路径：' + esc(ws.path) + '">' + esc(ws.path) + '</span>' +
          '</span>' +
          '<span class="ws-count">' + convs.length + '</span>' +
        '</button>' +
        '<button class="ibtn ws-new" data-ws-new="' + esc(ws.id) + '" aria-label="在此工作区新建会话" title="在此工作区新建会话">' + icon('plus', 'sm') + '</button>' +
        '<button class="ibtn ws-remove" data-ws-remove="' + esc(ws.id) + '" aria-label="移除工作区" title="移除工作区（不删除文件）">' + icon('x', 'sm') + '</button>' +
        (tone ? '<div class="ws-status ' + tone + '" role="status">' + esc(statusText) + '</div>' : '') +
        '</div>' +
        (open ? '<div class="ws-chats">' + (convs.length ? convs.map((s) => renderSessionRow(s)).join('')
          : '<div class="empty ws-empty">这个工作区还没有会话</div>') + '</div>' : '');
    }
    function renderSessionRow(s) {
      const s_updated = Number(s.updated_at || s.created_at || 0) * 1000;
      const inWorkspace = !!s.workspace_id;
      return '<div class="li session-row' + (s.id === activeConversationId ? ' on' : '') + (inWorkspace ? ' in-workspace' : '') + '">' +
        '<button class="session-open" data-session="' + esc(s.id) + '" title="切换会话">' +
        '<span class="ic">' + icon('chat', 'sm') + '</span><span class="tx"><b>' + esc(String(s.title || '').slice(0, 7)) + '</b>' +
        '<span>' + esc(relative(s_updated)) + '</span></span></button>' +
        '<button class="ibtn session-rename" data-rename="' + esc(s.id) + '" aria-label="重命名会话" title="重命名">···</button>' +
        '<button class="ibtn session-delete" data-delete="' + esc(s.id) + '" aria-label="删除会话" title="删除会话">' + icon('x', 'sm') + '</button></div>';
    }
    function drawSessions() {
      // Workspaces first: each one owns the chats that point at its folder.
      // Everything else stays an ordinary chat — switching never moves anything.
      const loose = sessionItems.filter((s) => !s.workspace_id);
      const known = new Set(workspaceItems.map((w) => w.id));
      // A chat whose workspace vanished must remain reachable as a normal chat.
      const orphaned = sessionItems.filter((s) => s.workspace_id && !known.has(s.workspace_id));
      const wsHtml = workspaceItems.map(workspaceRowHtml).join('');
      const head = '<div class="session-group-title">工作区</div>';
      // The sidebar is a list, not a launcher: the only "打开文件夹" entry
      // lives in the composer picker, so nothing here creates workspaces.
      const workspaceSection = (workspaceItems.length || workspaceError)
        ? '<section class="session-group session-workspaces">' + head +
            (workspaceError
              ? '<div class="empty ws-empty">' + esc(workspaceError) + '</div>'
              : wsHtml)
          + '</section>'
        : '';
      const looseRows = loose.concat(orphaned).sort((a, b) =>
        Number(b.updated_at || 0) - Number(a.updated_at || 0));
      const grouped = !!(workspaceItems.length || workspaceError);
      // The row for an unsaved chat only exists while no conversation is
      // selected; it never points at a workspace.
      const newChatRow = !activeConversationId
        ? '<div class="li session-row on session-draft" aria-current="page"><span class="ic">' + icon('chat', 'sm') + '</span><span class="tx"><b>新对话</b></span></div>'
        : '';
      // No empty "聊天" heading: with workspaces present, that section only
      // exists when it actually holds something.
      const showChats = looseRows.length || !!newChatRow || !grouped;
      const chatsSection = showChats ? '<section class="session-group session-chats">' +
        (grouped ? '<div class="session-group-title">聊天</div>' : '') +
        (looseRows.length ? looseRows.map((s) => renderSessionRow(s)).join('')
          : (newChatRow ? '' : '<div class="empty" style="text-align:left;padding:10px 8px">还没有会话</div>')) +
        newChatRow +
        '</section>' : '';
      sessList.innerHTML = workspaceSection + chatsSection;
      sessList.querySelectorAll('[data-session]').forEach((n) => n.addEventListener('click', () => activateConversation(n.dataset.session)));
      sessList.querySelectorAll('[data-rename]').forEach((n) => n.addEventListener('click', () => renameConversation(n.dataset.rename)));
      sessList.querySelectorAll('[data-delete]').forEach((n) => n.addEventListener('click', (e) => { e.stopPropagation(); deleteConversation(n.dataset.delete); }));
      sessList.querySelectorAll('[data-ws-toggle]').forEach((n) => n.addEventListener('click', () => {
        const wid = n.dataset.wsToggle;
        const hasActive = sessionItems.some((s) => s.workspace_id === wid && s.id === activeConversationId);
        const current = workspaceOpen[wid] !== undefined ? !!workspaceOpen[wid] : hasActive;
        workspaceOpen[wid] = !current;
        saveWorkspaceOpen();
        drawSessions();
      }));
      sessList.querySelectorAll('[data-ws-new]').forEach((n) => n.addEventListener('click', (e) => { e.stopPropagation(); newConversationIn(n.dataset.wsNew); }));
      sessList.querySelectorAll('[data-ws-remove]').forEach((n) => n.addEventListener('click', (e) => { e.stopPropagation(); removeWorkspace(n.dataset.wsRemove); }));
      sessList.querySelectorAll('[data-ws-copy]').forEach((n) => n.addEventListener('click', async (e) => {
        e.stopPropagation();
        try { await navigator.clipboard.writeText(n.dataset.wsCopy); toast('已复制路径：' + n.dataset.wsCopy); }
        catch (_) { toast('复制失败，路径：' + n.dataset.wsCopy); }
      }));
    }
    async function activateConversation(id, options = {}) {
      clearTokenRate();
      if (!sessionItems.some((s) => s.id === id) || id === activeConversationId) return;
      activeConversationId = id;
      draftActive = false;
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
      heroEl.hidden = true;
      heroEl.innerHTML = '';
      chat.classList.remove('is-empty');
      chat.classList.add('is-draft');
      view.querySelector('#chatLoadState').hidden = true;
      busy = false;
      busyScope = null;
      currentRequestId = null; liveReconcile = null; uncertainRunId = null;
      taEl.value = '';
      attachment = null;
      imgB64 = '';
      attachBox.hidden = true;
      attachBox.innerHTML = '';
      updateComposer();
      drawSessions();
      drawWorkspaceBar();
      await loadMsgs(true);
    }
    function addWorkspaceItem(item) {
      if (!item || !item.id) return null;
      const existing = workspaceItems.findIndex((w) => w.id === item.id);
      if (existing >= 0) workspaceItems.splice(existing, 1);
      workspaceItems.unshift(item);
      return item;
    }
    /** Pick a real local folder through the native dialog, then register it.
     *  ``assign`` is only passed by the composer picker: there the click is
     *  explicitly about this conversation, so the new folder becomes its
     *  workspace. The sidebar list action never reassigns anything. */
    async function openFolderWorkspace(assign) {
      const bridge = global.vigil || {};
      const picker = bridge.workspace && bridge.workspace.pick
        ? bridge.workspace : bridge.projectPicker;
      if (!bridge.workspace && !(bridge.projectPicker && bridge.projectPicker.pickDirectory)) {
        toast('当前环境没有原生文件夹选择器（浏览器预览不可用）');
        return;
      }
      let picked;
      try {
        picked = picker.pick ? await picker.pick() : await picker.pickDirectory();
      } catch (e) { toast('无法打开文件夹选择器：' + String((e && e.message) || e)); return; }
      if (!picked || !picked.ok) { toast('无法打开文件夹：' + ((picked && picked.err) || '未知错误')); return; }
      if (picked.canceled || !picked.path) return;
      const r = pick(await post('/api/workspaces', { src:'pc', path: picked.path }));
      if (!r.ok || !r.item) { toast(r._err || r.err || '工作区创建失败'); return; }
      addWorkspaceItem(r.item);
      workspaceOpen[r.item.id] = true;
      saveWorkspaceOpen();
      workspaceProbe[r.item.id] = {state:'checking'};
      drawSessions();
      toast(r.item.existing ? '这个文件夹已经是工作区' : '已添加工作区：' + r.item.name);
      void probeWorkspaces();
      if (assign && activeConversationId) await chooseWorkspace(r.item.id);
    }
    /** New chat inside a workspace. The link is explicit — never inferred. */
    async function newConversationIn(wid) {
      const ws = workspaceItems.find((w) => w.id === wid);
      if (!ws || busy) return;
      const r = pick(await post('/api/conversations', { src:'pc', workspace_id: wid }));
      if (!r.ok || !r.item) { toast(r._err || r.err || '无法在这个工作区里新建会话'); return; }
      sessionItems.unshift(r.item);
      workspaceOpen[wid] = true;
      saveWorkspaceOpen();
      drawSessions();
      await activateConversation(r.item.id);
    }
    async function removeWorkspace(wid) {
      const ws = workspaceItems.find((w) => w.id === wid);
      if (!ws) return;
      if (!await confirmDialog('移除工作区「' + ws.name + '」？里面的会话会保留为普通聊天，不会删除或移动任何文件。')) return;
      const r = pick(await post('/api/workspaces/' + encodeURIComponent(wid) + '/delete', { src:'pc' }));
      if (!r.ok) { toast(r._err || r.err || '移除工作区失败'); return; }
      workspaceItems = workspaceItems.filter((w) => w.id !== wid);
      sessionItems = sessionItems.map((s) => s.workspace_id === wid ? Object.assign({}, s, {workspace_id:null}) : s);
      delete workspaceProbe[wid];
      drawSessions();
      drawWorkspaceBar();
      toast('已移除工作区，会话保留为普通聊天');
    }
    // ── 输入区下方：工作空间 + 授权范围（只说明现状，不代替设置页）──
    let pendingWorkspaceId = null;
    function drawComposerFoot() {
      const label = view.querySelector('#wsPickBtn [data-ws-label]');
      if (label) {
        const conv = sessionItems.find((s) => s.id === activeConversationId);
        const ws = conv && conv.workspace_id ? workspaceItems.find((w) => w.id === conv.workspace_id) : null;
        const pending = !ws && pendingWorkspaceId ? workspaceItems.find((w) => w.id === pendingWorkspaceId) : null;
        label.textContent = ws ? ws.name : pending ? '下一个会话：' + pending.name : '选择工作空间';
        const chip = view.querySelector('#wsPickBtn');
        if (chip) chip.title = ws ? ws.path : '选择这个对话所属的本地文件夹';
      }
      const perm = view.querySelector('#permChip');
      if (perm) {
        const wide = !!(state.cfg && state.cfg.allowAllPaths);
        const text = perm.querySelector('[data-perm-label]');
        if (text) text.textContent = wide ? '允许完全访问' : '仅授权目录';
        perm.classList.toggle('wide', wide);
        perm.title = wide ? '当前允许访问整台机器；点这里调整授权范围' : '当前只允许白名单目录；点这里调整授权范围';
      }
    }
    let wsPop = null;
    function closeWsPop() {
      if (!wsPop) return;
      wsPop.hidden = true;
      const b = view.querySelector('#wsPickBtn');
      if (b) b.setAttribute('aria-expanded', 'false');
    }
    function pickWorkspaceTitle(wid) {
      const w = wid ? workspaceItems.find((x) => x.id === wid) : null;
      return w ? w.name : '';
    }
    function drawWsPop() {
      if (!wsPop) return;
      const conv = sessionItems.find((s) => s.id === activeConversationId);
      const currentId = (conv && conv.workspace_id) || pendingWorkspaceId || '';
      const rows = workspaceItems.map((w) => {
        const search = (w.name + ' ' + w.path).toLowerCase();
        return '<button class="wsp-item' + (w.id === currentId ? ' on' : '') + '" type="button" data-ws-pick="' + esc(w.id) + '"' +
          ' data-search="' + esc(search) + '" title="' + esc(w.path) + '">' + icon('folder', 'sm') +
          '<span class="tx"><b>' + esc(w.name) + '</b><span>' + esc(w.path) + '</span></span></button>';
      }).join('');
      wsPop.innerHTML =
        '<div class="wsp-head"><input class="input" id="wsSearch" type="search" placeholder="搜索工作空间" aria-label="搜索工作空间"></div>' +
        '<div class="wsp-list">' +
          (workspaceItems.length ? rows : '<div class="wsp-empty">还没有工作空间</div>') +
          (conv && conv.workspace_id
            ? '<button class="wsp-item" type="button" data-ws-pick="" data-search=""><span class="ic">' + icon('x', 'sm') + '</span>' +
              '<span class="tx"><b>移出工作区</b><span>会话保留为普通聊天，不动任何文件</span></span></button>'
            : '') +
        '</div>' +
        '<div class="wsp-foot">' +
          '<button class="wsp-action" type="button" id="wspOpenFolder">' + icon('plus', 'sm') + '<span>打开本地文件夹</span></button>' +
          '<span class="wsp-note">只记录位置，不授予读写权限</span>' +
        '</div>';
      const search = wsPop.querySelector('#wsSearch');
      if (search) search.oninput = () => {
        const q = String(search.value || '').trim().toLowerCase();
        wsPop.querySelectorAll('[data-ws-pick]').forEach((n) => {
          const text = n.dataset.search || '';
          n.hidden = !!q && !text.includes(q);
        });
      };
      wsPop.querySelectorAll('[data-ws-pick]').forEach((n) => {
        n.onclick = () => chooseWorkspace(n.dataset.wsPick || '');
      });
      const openBtn = wsPop.querySelector('#wspOpenFolder');
      if (openBtn) openBtn.onclick = async () => { closeWsPop(); await openFolderWorkspace(true); };
    }
    /** 归属只由用户这一下点击决定，选择工作区不会自动获得文件权限。 */
    async function chooseWorkspace(wid) {
      const name = pickWorkspaceTitle(wid);
      if (!activeConversationId) {
        pendingWorkspaceId = wid || null;
        closeWsPop();
        drawComposerFoot();
        toast(wid ? '下一个新会话会放进：' + name : '下一个新会话不归属工作区');
        return;
      }
      const r = pick(await post('/api/conversations/' + encodeURIComponent(activeConversationId) + '/workspace',
        { src:'pc', workspace_id: wid || null }));
      if (!r.ok || !r.item) { toast(r._err || r.err || '工作区归属保存失败'); return; }
      sessionItems = sessionItems.map((s) => s.id === r.item.id ? r.item : s);
      if (wid) { workspaceOpen[wid] = true; saveWorkspaceOpen(); }
      pendingWorkspaceId = null;
      closeWsPop();
      drawSessions();
      drawWorkspaceBar();
      toast(wid ? '已把这个会话放进：' + name : '已移出工作区，会话保留为普通聊天');
    }
    /** 输入区下方那两个标签的绑定，必须在 view.innerHTML 之后调用。 */
    function bindComposerFoot() {
      wsPop = view.querySelector('#wsPop');
      const pick = view.querySelector('#wsPickBtn');
      if (pick) pick.addEventListener('click', () => {
        if (!wsPop) return;
        if (!wsPop.hidden) { closeWsPop(); return; }
        const lp = view.querySelector('#localPop');
        if (lp) lp.hidden = true;
        drawWsPop();
        // Anchor the panel over the chip that opened it, not the page corner.
        const shell = view.querySelector('.composer');
        if (shell) {
          const box = shell.getBoundingClientRect(), chip = pick.getBoundingClientRect();
          const width = Math.min(340, box.width - 24);
          wsPop.style.left = Math.max(8, Math.min(chip.left - box.left, box.width - width - 8)) + 'px';
        }
        wsPop.hidden = false;
        pick.setAttribute('aria-expanded', 'true');
        const search = wsPop.querySelector('#wsSearch');
        if (search) requestAnimationFrame(() => search.focus());
      });
      const perm = view.querySelector('#permChip');
      if (perm) perm.addEventListener('click', () => {
        // 授权范围就在聊天框里改：点标签弹出和盾牌同一个面板，不跳设置页。
        // 权限仍然只由面板里的显式选择改变，点标签本身不授予任何权限。
        toggleLocalPop(perm);
      });
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
    async function deleteConversation(id) {
      const item = sessionItems.find((s) => s.id === id);
      if (!item || (busy && id === activeConversationId)) return;
      if (!await confirmDialog('删除「' + String(item.title || '这段聊天').slice(0, 40) + '」？该会话的聊天记录将被删除，插件草稿仍会保留。')) return;
      const r = pick(await post('/api/conversations/' + encodeURIComponent(id) + '/delete', {src:'pc'}));
      if (!r.ok) { toast(r._err || '删除失败'); return; }
      sessionItems = sessionItems.filter((s) => s.id !== id);
      if (activeConversationId === id) {
        const next = sessionItems[0];
        if (next) await activateConversation(next.id); else showWelcomeHome(true);
      } else drawSessions();
      toast('会话已删除');
    }
    panel.querySelector('#newTopicBtn').addEventListener('click', () => { if (!busy) showWelcomeHome(true, true); });

    view.innerHTML =
      '<div class="split no-right">' +
      '  <div class="chat">' +
      '    <div class="chat-ws-bar" id="chatWsBar" hidden></div>' +
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
      '          <div class="mq-details">' +
      '            <button class="mq-detail-row" id="mqContextToggle" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="mqContextMenu">' +
      '              <span>上下文窗口</span><b id="mqContextValue">读取中…</b><span aria-hidden="true">›</span></button>' +
      '            <div id="mqContextMenu" class="mq-context-menu" role="menu" aria-label="云端上下文窗口" hidden>' +
                     [96000,128000,300000].map((tier) => '<button type="button" role="menuitemradio" aria-checked="false" data-context-tier="' + tier + '"><span class="mq-check" aria-hidden="true"></span>' + (tier / 1000) + 'K</button>').join('') +
      '            </div>' +
      '          </div>' +
      '        </div>' +
      '        <div class="lpadd mqfoot">' +
      '          <button class="btn ghost md" id="mqManage" type="button">添加或管理模型</button>' +
      '          <span class="muted" id="mqMsg"></span>' +
      '        </div>' +
      '      </div>' +
      '      <div class="localpop" id="localPop" hidden></div>' +
      '      <div class="localpop pluspop" id="plusPop" hidden role="dialog" aria-label="添加内容与运行模式"></div>' +
      '      <div class="wsPop localpop" id="wsPop" hidden role="dialog" aria-label="选择工作空间"></div>' +
      '      <div class="col">' +
      '      <div class="attach" id="attach" hidden></div>' +
      '      <div class="cbox"><textarea id="ta" rows="2" aria-label="消息内容" placeholder="今天帮你做些什么？@ 添加上下文，/ 调用技能与指令"></textarea>' +
      '        <div class="cbar">' +
      '          <button class="ibtn cbar-add" id="clipBtn" title="添加图片、视频或普通文件" aria-label="添加附件">' + icon('plus', 'sm') + '</button>' +
      '          <button class="chip" id="creatorBtn" title="创作模式：用自然语言让 Agent 生成插件/技能草稿">创作：关</button>' +
      '          <button class="ibtn" id="localBtn" title="本地操控：权限模式 · 白名单目录">' + icon('shield', 'sm') + '</button>' +
      '          <div class="sp"></div>' +
      '          <button class="btn composer-model" id="modelBtn" title="打开模型列表，点一条即切换">' + esc(model) + ' ▾</button>' +
      '          <button class="btn fill" id="sendBtn" disabled aria-label="发送消息" title="发送消息（Enter）">' + icon('arrow-up', 'sm') + '</button>' +
      '        </div></div>' +
      '      <div class="composer-caption composer-foot">' +
      '        <button class="foot-chip mode-chip" id="modeChip" type="button" data-mode="cloud" title="这一轮使用的模型"><span class="mode-dot"></span><span data-mode-model>—</span></button>' +
      '        <span id="tokenRate" class="token-rate" hidden title="模型输出吞吐率；Provider usage 可能包含推理 Token">—</span>' +
      '        <button class="foot-chip" id="wsPickBtn" type="button" title="选择这个对话所属的本地文件夹">' + icon('folder', 'sm') + '<span data-ws-label>选择工作空间</span></button>' +
      '        <button class="foot-chip" id="permChip" type="button" title="本地文件与命令的授权范围"><span class="perm-dot"></span><span data-perm-label>仅授权目录</span></button>' +
      '        <div class="sp"></div>' +
      '        <span class="foot-brand">Vigil · AI Agent Workspace</span>' +
      '        <span class="ccount" id="ccount">0 字</span>' +
      '      </div>' +
      '      </div>' +
      '    </div>' +
      '  </div>' +
      '  <aside class="rp no" id="rp" aria-label="上下文与统计"></aside>' +
      '</div>';

    const msgs = view.querySelector('#msgs');
    const heroEl = view.querySelector('#hero');
    const rp = view.querySelector('#rp');
    const chat = view.querySelector('.chat');
    const tokenRateEl = view.querySelector('#tokenRate');
    let metricStartedAt = 0, metricVisibleTokens = 0, metricLastPaint = 0;
    function clearTokenRate() {
      metricStartedAt = 0; metricVisibleTokens = 0; metricLastPaint = 0;
      if (tokenRateEl) { tokenRateEl.hidden = true; tokenRateEl.textContent = '—'; }
    }
    function startTokenRate() {
      metricStartedAt = performance.now(); metricVisibleTokens = 0; metricLastPaint = 0;
      if (tokenRateEl) tokenRateEl.hidden = true;
    }
    function addVisibleTokenEstimate(text) {
      const value = String(text || '');
      let cjk = 0;
      for (let i = 0; i < value.length; i++) if (value.charCodeAt(i) >= 0x4e00 && value.charCodeAt(i) <= 0x9fff) cjk++;
      metricVisibleTokens += cjk * 0.9 + (value.length - cjk) * 0.3;
      const now = performance.now();
      if (!metricStartedAt || !metricVisibleTokens || now - metricLastPaint < 1000) return;
      const rate = metricVisibleTokens / Math.max(0.001, (now - metricStartedAt) / 1000);
      paintTokenRate(rate, true); metricLastPaint = now;
    }
    function paintTokenRate(rate, estimated, reasoningTokens) {
      if (!tokenRateEl || !Number.isFinite(Number(rate)) || Number(rate) <= 0) return;
      tokenRateEl.textContent = (estimated ? '≈ ' : '') + Number(rate).toFixed(1) + ' tok/s';
      tokenRateEl.title = '模型输出吞吐率；' + (estimated ? '按屏幕可见文本估算' : '按 Provider 输出 Token 统计') +
        (reasoningTokens ? '；包含 ' + reasoningTokens + ' 个 Provider 报告的推理 Token' : '；Provider usage 可能包含推理 Token');
      tokenRateEl.hidden = false;
    }
    function finishTokenRate(data) {
      metricStartedAt = 0;
      if (!data) return;
      const elapsed = Number(data.request_elapsed_seconds || 0);
      const count = Number(data.output_tokens);
      const rate = Number(data.tokens_per_second || (elapsed > 0 ? count / elapsed : 0));
      paintTokenRate(rate, !!(data.rate_estimated ?? data.estimated), data.reasoning_tokens);
    }
    const applySessions = () => {};
    // 模型快速切换：点左上角那个模型名弹出已保存的模型清单，点一条即切。
    // 数据来自 /api/models/saved（不带密钥），切换走 /api/models/quick-switch。
    // 生成过程中禁用（busy）：不让"正在跑的这轮"和"刚选的模型"对不上。
    const mq = modelQuickBind(view, ctx, {
      isBusy: () => busy,
      // 本地/混合模式下这个按钮改开模式菜单：云端清单里的选择在那两种模式里不生效
      blockOpen: () => !!(runStatus && runStatus.mode === 'local'),
      onBlocked: () => { openPlusPop(); },
      afterLoad: () => syncComposerModelLabel(),
      onSwitched: (c) => {
        clearTokenRate();
        if (c && c.model) toast('已切到 ' + c.model);
        void loadRunMode(false);
      },
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

    // ---- P20.2 能力审批卡片 ----
    // 确认/拒绝按钮是受控操作唯一的执行入口；回执来自 Brain 的 approve
    // 响应（含执行后的真实状态复核），不在前端伪造成功。
    function renderCapabilityOutcome(card, action, r) {
      let html;
      if (action === 'deny') {
        html = '<div class="cap-approval-state cap-denied">已拒绝，未执行</div>';
      } else {
        const st = String((r && r.status) || '');
        if (st === 'unknown') {
          // 结果未知时绝不能显示成成功：副作用可能已经生效。
          html = '<div class="cap-approval-state cap-unknown">已执行，但结果未知；'
            + '在确认实际生效情况前不会自动重试'
            + ((r && r.detail) ? '<pre>' + esc(String(r.detail).slice(0, 300)) + '</pre>' : '')
            + '</div>';
        } else {
          const ok = !!(r && r.ok);
          const facts = [];
          if (r && r.after && typeof r.after === 'object') {
            facts.push(Object.entries(r.after).filter(([, v]) => v !== undefined && v !== '')
              .map(([k, v]) => k + '=' + v).join(' · '));
          }
          if (Array.isArray(r && r.receipts) && r.receipts.length) {
            facts.push(r.receipts.map((x) => [x.state, x.reason, x.detail].filter(Boolean).join(' ')).join(' | '));
          }
          html = '<div class="cap-approval-state ' + (ok ? 'cap-ok' : 'cap-fail') + '">'
            + (ok ? '已确认并执行成功' : '已确认，但执行未成功：' + esc(String((r && (r.error || r.detail)) || '原因未知').slice(0, 160)))
            + (facts.length ? '<pre>' + esc(facts.join('\n').slice(0, 400)) + '</pre>' : '')
            + '</div>';
        }
      }
      const actions = card.querySelector('.cap-approval-actions');
      if (actions) actions.outerHTML = html;
      else card.insertAdjacentHTML('beforeend', html);
      card.dataset.capDecorated = '1';
    }
    msgs.addEventListener('click', async (e) => {
      const button = e.target.closest('[data-cap-action]');
      if (!button || button.disabled) return;
      e.preventDefault();
      const action = button.dataset.capAction;
      const card = button.closest('.cap-approval');
      const rid = button.dataset.capRequest;
      if (!card || !rid) return;
      const actions = card.querySelector('.cap-approval-actions');
      if (actions) actions.innerHTML = '<div class="cap-approval-state cap-running">正在'
        + (action === 'approve' ? '执行' : '记录拒绝') + '…</div>';
      try {
        const r = pick(await post('/api/capabilities/' + (action === 'approve' ? 'approve' : 'deny'),
          { request_id: rid }));
        if (action === 'deny' && r && r.ok === false) {
          // 拒绝本身没成功（执行中/已完成）：如实说明，不宣称"已拒绝未执行"。
          const actions2 = card.querySelector('.cap-approval-actions');
          if (actions2) actions2.innerHTML = '<div class="cap-approval-state cap-fail">拒绝未生效：'
            + esc(String(r.error || '原因未知').slice(0, 120)) + '</div>';
          return;
        }
        if (action === 'approve' && r && r.ok === false && r.error === 'request_in_flight') {
          // 另一个审批动作正在执行：不是失败，是进行中。
          const actions2 = card.querySelector('.cap-approval-actions');
          if (actions2) actions2.innerHTML = '<div class="cap-approval-state cap-running">正在执行…（另一个确认已受理）</div>';
          card.dataset.capDecorated = '1';
          return;
        }
        if (action === 'approve' && r && r.ok === false && r.error === 'request_not_found') {
          // Brain 重启导致内存请求丢失：失效卡片不能保留可点的按钮。
          const actions2 = card.querySelector('.cap-approval-actions');
          if (actions2) actions2.outerHTML = '<div class="cap-approval-state cap-stale">审批已失效，请重新发起</div>';
          card.dataset.capDecorated = '1';
          return;
        }
        renderCapabilityOutcome(card, action, r);
      } catch (err) {
        if (actions) actions.innerHTML = '<div class="cap-approval-state cap-fail">请求失败：'
          + esc(String((err && err.message) || err).slice(0, 120)) + '</div>';
      }
    });
    // 历史会话回填：卡片状态以服务端为准 —— pending 才有按钮；
    // executing/approved 显示执行中（不可重复操作）；completed 显示真实
    // 结果；denied 显示已拒绝；服务端查不到 request_id（Brain 重启后内存
    // 请求丢失）显示"审批已失效"。查询带当前会话过滤，无匹配时回退一次
    // 全量（管理员可见全部）。两次查询都成功才允许判定失效；查询本身
    // 失败时保持原样，绝不把未知状态误判成已拒绝或已成功。
    async function decorateCapabilityApprovals(sessionId) {
      const cards = [...msgs.querySelectorAll('.cap-approval[data-cap-request-id]')];
      if (!cards.length) return;
      const fetchMap = async (withSession) => pick(await get('/api/capabilities/requests'
        + (withSession && sessionId
          ? '?session=' + encodeURIComponent(sessionId) : '')));
      // 失败语义收口在 approvalHistory:{ok:false}({ok:false} 即 HTTP
      // 401/403/500)、断网、格式异常都返回 null —— 只有两次必要查询都
      // 成功拿到有效列表,缺失的 request_id 才允许被判成失效。
      let byId = null;
      try {
        byId = await global.VigilAgentView.approvalHistory(cards, fetchMap);
      } catch (_) { byId = null; }
      if (!byId) return;
      for (const { card, action, request } of global.VigilAgentView.approvalResolutions(cards, byId)) {
        if (action === 'skip') continue;
        card.dataset.capDecorated = '1';
        if (action === 'stale') {
          const actions = card.querySelector('.cap-approval-actions');
          if (actions) actions.outerHTML = '<div class="cap-approval-state cap-stale">审批已失效，请重新发起</div>';
          continue;
        }
        if (action === 'pending') continue;
        if (action === 'executing' || action === 'approved') {
          const actions = card.querySelector('.cap-approval-actions');
          if (actions) actions.innerHTML = '<div class="cap-approval-state cap-running">正在执行…</div>';
          continue;
        }
        if (action === 'denied') {
          renderCapabilityOutcome(card, 'deny', request.result || {});
          continue;
        }
        if (action === 'completed') {
          renderCapabilityOutcome(card, 'approve', request.result || {});
        }
      }
    }

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
      const put = (key, value) => { const el = rp.querySelector('[data-chat-stat="' + key + '"]'); if (el) el.textContent = value; };
      const inTok = Number(st.tok_in) || 0, outTok = Number(st.tok_out) || 0;
      put('name', st.name || '—'); put('model', m); put('turns', fmtNum(st.turns == null ? '—' : st.turns));
      put('total', fmtNum(inTok + outTok)); put('in', fmtNum(inTok)); put('out', fmtNum(outTok));
      put('latency', st.avg_ms ? (st.avg_ms / 1000).toFixed(1) + 's' : '—');
      put('memory', (st.mem_hit_turns || 0) + ' / ' + (st.turns || 0));
      put('retrieval', (st.history_retrieval || {}).label || '状态暂不可用');
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
    let submitPending = false;
    let busyScope = null;
    // 发送键三态（发送/暂停/继续）：
    // - pause：本会话有 agent run 在跑，点它 = 暂停任务
    // - resume：最近一次 run 停在可续跑状态且输入框为空，点它 = 续跑
    // 输入框一旦有文字，优先级永远是"发新消息"。
    let activeRunId = null;
    let resumableRunId = null;
    let composerMode = 'send';
    let pendingResumeOf = null;
    // 执行中插话：把目标 run 放在这里，send() 会带上 interject_of。
    let pendingInterjectOf = null;
    let pausePending = false;
    let liveReconcile = null;
    let currentRequestId = null;
    let uncertainRunId = null;
    let conversationLoadSeq = 0;
    let renderedDraftConversationId = '';
    function finishBusy(scope) {
      if (busyScope !== scope) return;
      busy = false;
      busyScope = null;
      updateComposer();
    }
    function showWelcomeHome(persist = false, focusComposer = false) {
      activeConversationId = '';
      draftActive = false;
      state.chatConversationId = '';
      lastCount = -1;
      conversationLoadSeq++;
      renderedDraftConversationId = '';
      msgs.innerHTML = '';
      heroEl.hidden = false;
      heroEl.innerHTML = heroHtml();
      chat.classList.add('is-empty');
      chat.classList.remove('is-draft');
      view.querySelector('#chatLoadState').hidden = true;
      busy = false;
      busyScope = null;
      activeRunId = null; resumableRunId = null; pendingResumeOf = null;
      currentRequestId = null; liveReconcile = null; uncertainRunId = null;
      setComposerMode('send');
      if (typeof taEl !== 'undefined') taEl.value = '';
      if (typeof clearAttachment === 'function') clearAttachment();
      if (persist) rememberRendererSelection('', 'welcome');
      drawSessions();
      drawWorkspaceBar();
      if (focusComposer) requestAnimationFrame(() => { if (taEl.isConnected) taEl.focus(); });
    }
    function showDraftConversation(persist = false) {
      activeConversationId = '';
      draftActive = true;
      state.chatConversationId = '';
      lastCount = -1;
      conversationLoadSeq++;
      renderedDraftConversationId = '';
      msgs.innerHTML = '';
      heroEl.hidden = true;
      heroEl.innerHTML = '';
      chat.classList.remove('is-empty');
      chat.classList.add('is-draft');
      view.querySelector('#chatLoadState').hidden = true;
      busy = false;
      busyScope = null;
      activeRunId = null; resumableRunId = null; pendingResumeOf = null;
      currentRequestId = null; liveReconcile = null; uncertainRunId = null;
      setComposerMode('send');
      taEl.value = '';
      if (typeof clearAttachment === 'function') clearAttachment();
      if (persist) rememberRendererSelection('', 'draft');
      drawSessions();
      drawWorkspaceBar();
      requestAnimationFrame(() => { if (taEl.isConnected) taEl.focus(); });
    }
    async function ensureConversation() {
      if (activeConversationId) return activeConversationId;
      if (createConversationPromise) return createConversationPromise;
      createConversationPromise = (async () => {
        if (API.runtime.preview) {
          const item = sessionItems[0];
          if (item) activeConversationId = item.id;
        } else {
          // 用户在下方的“选择工作空间”里点过某个文件夹，这一条新会话才归属它。
          const created = pick(await post('/api/conversations',
            Object.assign({src:'pc'}, pendingWorkspaceId ? {workspace_id: pendingWorkspaceId} : {})));
          if (!created.ok || !created.item || !created.item.id) return '';
          const result = created;
          pendingWorkspaceId = null;
          const existing = sessionItems.findIndex((item) => item.id === result.item.id);
          if (existing >= 0) sessionItems.splice(existing, 1);
          sessionItems.unshift(result.item);
          activeConversationId = result.item.id;
        }
        if (!activeConversationId) return '';
        draftActive = false;
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
      const initialSelectionSeq=conversationLoadSeq;
      try {
        appInstanceId = global.vigil && global.vigil.appInstanceId
          ? String(await global.vigil.appInstanceId() || '') : 'browser-session';
      } catch (_) { appInstanceId = 'browser-session'; }
      try {
        rendererSelection = JSON.parse(localStorage.getItem(RENDERER_SESSION_KEY) || 'null');
      } catch (_) { rendererSelection = null; }
      const restoreId = rendererSelection && rendererSelection.app_instance === appInstanceId
        && rendererSelection.mode === 'conversation' ? rendererSelection.conversation_id : '';
      const restoreDraft = rendererSelection && rendererSelection.app_instance === appInstanceId
        && rendererSelection.mode === 'draft';
      if (!await loadRecordScope()) { if(msgs.isConnected)showWelcomeHome(false); return; }
      if(!msgs.isConnected)return;
      // Workspaces are a separate index; a failed read must not block the chat
      // list, so it runs alongside and degrades to the "not available" note.
      refreshWorkspaces().catch(() => {});
      try { await refreshConversations(); }
      catch (error) {
        if(!msgs.isConnected)return;
        showWelcomeHome(false);
        sessList.innerHTML = '<div class="empty" style="text-align:left;padding:18px 12px">会话列表暂不可用</div>';
        return;
      }
      if (!msgs.isConnected || initialSelectionSeq!==conversationLoadSeq || busy || submitPending || activeConversationId) return;
      if (restoreId && sessionItems.some((item) => item.id === restoreId)) {
        await activateConversation(restoreId, {persist:false});
      } else if (restoreDraft) {
        showDraftConversation(false);
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
      const ta = view.querySelector('#ta');
      if (!btn || !ta) return;
      const label = creatorMode === 'off' ? '关' : creatorMode === 'plugin' ? '插件' : '技能';
      btn.textContent = '创作：' + label;
      btn.classList.toggle('on', creatorMode !== 'off');
      ta.placeholder = creatorMode === 'off'
        ? '今天帮你做些什么？@ 添加上下文，/ 调用技能与指令'
        : '描述你想要的' + (creatorMode === 'skill' ? '技能' : '插件')
          + '。例如：一个每小时整点提醒我喝水的插件；一个把一段文字反转后返回的技能。';
      try { localStorage.setItem('vigil.chat.creatorMode', creatorMode); } catch (_) {}
    }
    // 立即调用必须在这里：let creatorMode 已声明（TDZ 已过）
    applyCreatorMode();
    bindComposerFoot();
    drawComposerFoot();

    // ── 运行模式（云端 / 本地 / 混合）与加号菜单 ─────────────────
    // 运行模式 = "这一轮由谁执行"，创作模式 = "要不要产出草稿"，两者独立。
    // 切模式只改服务端配置与输入区标记：不跳页、不新建会话、不清历史、
    // 不动工作区归属，conversation_id 和滚动位置都保持原样。
    let runStatus = null;
    let modeSwitching = false;
    let plusPop = null;
    let plusAnchor = null;   // 弹层当前挂靠的按钮，窗口尺寸变化时按它重新定位

    function effectiveModel(st) {
      const s = st || runStatus || {};
      if (s.mode === 'local') return (s.local && s.local.model) || '';
      return (s.cloud && s.cloud.model) || '';
    }
    // 界面上不暴露"云端"这类内部术语：只在工具提示里区分默认 / 本地 / 混合
    function modeLabelOf(st) {
      const s = st || runStatus || {};
      return s.mode === 'local' ? '本地' : s.mode === 'hybrid' ? '混合' : '默认';
    }
    // 本地/混合模式下这个按钮显示真实生效的本地模型；默认模式交回快速切换自己维护
    function syncComposerModelLabel() {
      const btn = view.querySelector('#modelBtn');
      if (!btn || !runStatus || runStatus.mode === 'cloud') return;
      btn.textContent = (effectiveModel() || '未配置') + ' ▾';
      btn.title = '当前由' + modeLabelOf() + '模型执行：点开修改';
    }
    function drawModeChip() {
      const chip = view.querySelector('#modeChip');
      if (!chip) return;
      const st = runStatus || { mode: 'cloud', cloud: {} };
      // 旧版 Brain 没有本地模型可选：这个标记没有信息量，藏起来，别占位显示 "—"
      if (st.unsupported) { chip.hidden = true; return; }
      chip.hidden = false;
      const mode = st.mode || 'cloud';
      const local = st.local || {};
      chip.dataset.mode = mode;
      const m = chip.querySelector('[data-mode-model]');
      if (m) m.textContent = effectiveModel(st) || '未配置';
      chip.title = mode === 'local'
        ? '本地模型：' + (local.model || '未选模型')
          + (local.reachable === false ? '（Ollama 未运行）' : '')
        : mode === 'hybrid'
          ? '混合：' + ((st.cloud || {}).model || '未配置') + ' 作答，本地模型辅助'
          : '模型：' + ((st.cloud || {}).model || '未配置');
      syncComposerModelLabel();
    }
    async function loadRunMode(probe) {
      const r = pick(await get('/api/run-mode' + (probe ? '?probe=1' : '')));
      if (r && r._status === 404 && !r.ok) {
        // Brain 是旧版：没有本地模型这回事。别把入口画成"能点但一定失败"，
        // 保留聊天的默认模型，模型名交给快速切换按钮显示。
        runStatus = { mode: 'cloud', unsupported: true, cloud: {}, local: {} };
        drawModeChip();
        if (plusPop && !plusPop.hidden && !modeSwitching) drawPlusPop();
        return;
      }
      if (!r || !r.ok) return;
      runStatus = r;
      drawModeChip();
      if (plusPop && !plusPop.hidden && !modeSwitching) drawPlusPop();
    }
    function plusSay(text, bad) {
      if (!plusPop) return;
      const el = plusPop.querySelector('#plusMsg');
      if (!el) return;
      el.textContent = text || '';
      el.style.color = bad ? 'var(--err)' : '';
    }
    function drawPlusPop() {
      if (!plusPop) return;
      const st = runStatus || { mode: 'cloud', cloud: {}, local: {} };
      const cur = st.mode || 'cloud';
      const local = st.local || {};
      const installed = local.installed || [];
      const modeRow = (mode, name, model, note) =>
        '<button class="plus-row' + (mode === cur ? ' on' : '') + '" type="button" data-mode="' + mode + '">' +
        '<span class="plus-lb"><b>' + esc(name) + '</b></span>' +
        (mode === 'local' && local.reachable === false ? '<span class="plus-note">未启动</span>' : '') +
        (mode === cur ? '<span class="plus-check">' + icon('check', 'sm') + '</span>' : '') +
        '</button>';
      const localNote = local.reachable === false
        ? 'Ollama 未运行（切换时自动启动）'
        : local.reachable
          ? 'Ollama 就绪 · ' + installed.length + ' 个模型'
          : '切换时检查';
      // 旧版 Brain 没有 /api/run-mode：只留一句说明，不摆一排点了必然失败的选项
      const modeSection = st.unsupported
        ? '<div class="plus-sec">模型<span class="plus-hint">这台 Brain 还不支持本地模型</span></div>' +
          '<div class="plus-row muted-row">' +
          '<span class="plus-lb"><b>默认模型</b><span>' + esc((st.cloud || {}).model || '由 Brain 配置决定') + '</span></span>' +
          '<span class="plus-note">Brain 版本较旧</span></div>'
        : '<div class="plus-sec">模型</div>' +
          modeRow('cloud', '默认模型', (st.cloud || {}).model,
            (st.cloud || {}).ready ? '随时可用' : '缺密钥或模型') +
          modeRow('local', '本地模型', local.model, localNote) +
          modeRow('hybrid', '混合', (st.cloud || {}).model, '默认作答 · 本地辅助') +
          (cur === 'local' && installed.length
            ? '<div class="plus-sub"><label for="plusLocalModel">本地模型</label>' +
              '<select class="input" id="plusLocalModel">' +
              installed.map((n) => '<option' + (n === local.model ? ' selected' : '') + '>' + esc(n) + '</option>').join('') +
              '</select></div>'
            : '');
      plusPop.innerHTML =
        '<div class="lph"><span>添加与模型</span>' +
        '<button class="ibtn" id="plusClose" type="button" aria-label="收起菜单" title="收起">' + icon('x', 'sm') + '</button></div>' +
        '<div class="lpbody plusbody">' +
        '<div class="plus-sec">添加</div>' +
        '<button class="plus-row" type="button" data-plus="file">' +
        '<span class="plus-rail plus-ic">' + icon('clip', 'sm') + '</span>' +
        '<span class="plus-lb"><b>图片、视频或文件</b></span>' +
        '<span class="plus-note">' + (attachment ? '已附加 1 个' : '') + '</span>' +
        '</button>' +
        modeSection +
        '<div class="plus-sec">创作模式</div>' +
        '<div class="plus-seg">' +
        ['off', 'plugin', 'skill'].map((k) =>
          '<button type="button" data-cr="' + k + '"' + (creatorMode === k ? ' class="on"' : '') + '>' +
          (k === 'off' ? '关' : k === 'plugin' ? '插件' : '技能') + '</button>').join('') +
        '</div>' +
        '</div>' +
        '<div class="plusfoot"><span id="plusMsg" role="status"></span></div>';
      const closeBtn = plusPop.querySelector('#plusClose');
      if (closeBtn) closeBtn.onclick = closePlusPop;
      plusPop.querySelectorAll('[data-mode]').forEach((b) => {
        b.onclick = () => {
          const sel = plusPop.querySelector('#plusLocalModel');
          setRunMode(b.dataset.mode, b.dataset.mode === 'local' && sel ? sel.value : '');
        };
      });
      const sel = plusPop.querySelector('#plusLocalModel');
      if (sel) sel.onchange = () => setRunMode('local', sel.value);
      plusPop.querySelectorAll('[data-cr]').forEach((b) => {
        b.onclick = () => {
          creatorMode = b.dataset.cr;
          applyCreatorMode();
          drawPlusPop();
          toast(creatorMode === 'off' ? '已回到普通对话'
            : '创作模式：生成' + (creatorMode === 'skill' ? '技能' : '插件') + '草稿');
        };
      });
      const fileRow = plusPop.querySelector('[data-plus="file"]');
      if (fileRow) fileRow.onclick = () => { closePlusPop(); pickAttachmentFile(); };
    }
    // 纵向交给 CSS（bottom:100%+8px，和其他输入框弹层同一套）；
    // 这里只管水平对齐，并按按钮上方的真实空间压高度，免得顶破窗口。
    function placePlusPop(anchor) {
      if (!plusPop || !anchor) return;
      const shell = view.querySelector('.composer');
      if (!shell) return;
      const parent = plusPop.offsetParent || shell;
      const pb = parent.getBoundingClientRect();
      const sb = shell.getBoundingClientRect();
      const a = anchor.getBoundingClientRect();
      const width = Math.min(380, Math.max(280, sb.width - 24));
      plusPop.classList.add('anchored');
      plusPop.style.width = width + 'px';
      plusPop.style.left = Math.max(8, Math.min(a.left - pb.left, sb.width - width - 8)) + 'px';
      const room = Math.round(a.top - (sb.top - pb.top) - 16);   // 按钮到窗口顶的空隙
      plusPop.style.maxHeight = Math.max(180, Math.min(520, room)) + 'px';
      plusAnchor = anchor;
    }
    function closePlusPop() {
      if (!plusPop) return;
      plusPop.hidden = true;
      plusAnchor = null;
      ['#clipBtn', '#modeChip'].forEach((sel) => {
        const n = view.querySelector(sel);
        if (n) n.setAttribute('aria-expanded', 'false');
      });
    }
    function openPlusPop(anchor) {
      if (!plusPop) return;
      if (wsPop && !wsPop.hidden) closeWsPop();
      const lp = view.querySelector('#localPop');
      if (lp) lp.hidden = true;
      const a = anchor || view.querySelector('#clipBtn') || view.querySelector('#modeChip');
      plusPop.hidden = false;
      drawPlusPop();
      placePlusPop(a);
      if (a) a.setAttribute('aria-expanded', 'true');
      // 先按缓存画，再探一次真实状态（探测要走 Ollama，慢，不挡打开）
      void loadRunMode(true);
    }
    function togglePlusPop(anchor) {
      if (!plusPop) return;
      if (!plusPop.hidden) { closePlusPop(); return; }
      openPlusPop(anchor);
    }
    /** 切模型：失败就保持原样并说明原因，绝不"显示已切换"。 */
    async function setRunMode(mode, model) {
      if (modeSwitching) return;
      if (runStatus && runStatus.unsupported) {
        plusSay('这台 Brain 是旧版，没有本地模型能力：先更新 Vigil', true);
        return;
      }
      if (busy) { plusSay('正在生成回复：等这一轮结束再切模型', true); return; }
      const already = runStatus && runStatus.mode === mode
        && (!model || model === (runStatus.local || {}).model);
      if (already) { plusSay('已经在用这个模型了'); return; }
      modeSwitching = true;
      plusPop.querySelectorAll('button,select').forEach((n) => { n.disabled = true; });
      plusSay(mode === 'local'
        ? '正在检查 Ollama 并加载本地模型…（首次可能要几十秒）'
        : '正在切换模型…');
      let r;
      try { r = pick(await post('/api/run-mode', model ? { mode, model } : { mode })); }
      catch (e) { r = { ok: false, _err: String((e && e.message) || e) }; }
      modeSwitching = false;
      const bad = r.err || r._err || '';
      if (!r.ok) {
        drawPlusPop();
        plusSay(bad || '切换失败', true);
        toast('没有切换：' + (bad || '未知错误'));
        return;
      }
      if (r.status) runStatus = Object.assign({}, runStatus, r.status);
      drawModeChip();
      // 切回默认模型时把这个按钮的文案交回模型清单（本地模式期间它显示的是本地模型）
      if (runStatus && runStatus.mode === 'cloud' && mq && mq.load) void mq.load();
      drawPlusPop();
      const extra = (r.warnings || []).filter(Boolean);
      if (r.unchanged) plusSay('已经在用这个模型了');
      else plusSay('生效中：' + (effectiveModel() || '未配置')
        + (extra.length ? '（' + extra.join('；') + '）' : ''));
      if (!r.unchanged) {
        toast('已切换模型：' + (effectiveModel() || '未配置'));
      }
      // 以服务端状态为准再核一次（不拿请求参数冒充结果）
      void loadRunMode(true);
    }
    const closePlusOnDoc = (e) => {
      if (!plusPop || plusPop.hidden) return;
      const add = view.querySelector('#clipBtn');
      const chip = view.querySelector('#modeChip');
      if (plusPop.contains(e.target)
        || (add && add.contains(e.target)) || (chip && chip.contains(e.target))) return;
      closePlusPop();
    };
    document.addEventListener('pointerdown', closePlusOnDoc);
    ctx.onDispose(() => document.removeEventListener('pointerdown', closePlusOnDoc));
    const replacePlus = () => {
      if (!plusPop || plusPop.hidden) return;
      placePlusPop(plusAnchor || view.querySelector('#clipBtn') || view.querySelector('#modeChip'));
    };
    window.addEventListener('resize', replacePlus);
    ctx.onDispose(() => window.removeEventListener('resize', replacePlus));
    plusPop = view.querySelector('#plusPop');
    const addBtnEl = view.querySelector('#clipBtn');
    if (addBtnEl) addBtnEl.addEventListener('click', (e) => { e.stopPropagation(); togglePlusPop(addBtnEl); });
    const modeChipEl = view.querySelector('#modeChip');
    if (modeChipEl) modeChipEl.addEventListener('click', (e) => { e.stopPropagation(); togglePlusPop(modeChipEl); });
    drawModeChip();
    void loadRunMode(false);
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
      // resume 模式下一旦开始打字，立即回到发送模式 —— 新消息优先于续跑。
      if (composerMode === 'resume' && (ta.value.trim() || imgB64 || attachment)) {
        composerMode = 'send';
        drawComposerIcon();
      }
      if (composerMode === 'uncertain') {
        button.disabled = !global.vigil;
      } else if (composerMode === 'pause') {
        // 有字就是插话（发得出去），没字才是暂停。
        button.disabled = !global.vigil || !activeRunId ||
          !!(attachment && attachment.failed);
      } else if (composerMode === 'resume') {
        button.disabled = !global.vigil || !resumableRunId;
      } else {
        button.disabled = !global.vigil || busy || (!ta.value.trim() && !imgB64 && !attachment) ||
          !!(attachment && (attachment.failed || (attachment.kind === 'image' && !imgB64)));
      }
      button.classList.toggle('is-sending', busy && composerMode === 'send');
      button.disabled = button.disabled || submitPending;
      button.setAttribute('aria-busy', String(busy || submitPending));
      const label = composerMode === 'uncertain' ? '确认任务状态（连接异常或状态待确认）'
        : composerMode === 'pause' ? ((ta.value.trim() || imgB64)
          ? '插话并重新规划（会停下当前任务，保留已完成的步骤）' : '暂停任务')
        : composerMode === 'resume' ? '继续上次任务（输入框有字则发送新消息）'
        : (busy ? '正在发送…' : '发送消息');
      button.setAttribute('aria-label', label);
      button.title = label;
      // 生成中把模型按钮一起禁掉：不让"正在跑的这轮"和"刚选的模型"对不上。
      // 防一手 null —— updateComposer 在发送主链路上，这里抛错会连累整个 composer。
      const mb = view.querySelector('#modelBtn');
      if (mb) mb.disabled = busy && composerMode === 'send';
    }
    function drawComposerIcon() {
      const button = view.querySelector('#sendBtn');
      button.innerHTML = icon(composerMode === 'uncertain' ? 'refresh' : composerMode === 'pause' ? 'pause'
        : composerMode === 'resume' ? 'play' : 'arrow-up', 'sm');
    }
    function setComposerMode(mode) {
      composerMode = ['pause','resume','uncertain'].includes(mode) ? mode : 'send';
      drawComposerIcon();
      updateComposer();
    }
    const attachBox = view.querySelector('#attach');
    /** 加号菜单里的「图片、视频或文件」走这里：原生文件对话框 → 同一套类型/大小校验。 */
    function pickAttachmentFile() {
      const inp = document.createElement('input');
      inp.type = 'file';
      inp.accept = ATTACH_ACCEPT;
      inp.onchange = () => {
        chooseFile(inp.files && inp.files[0]);
      };
      inp.click();
    }
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
    function addStep(name, text, kind, meta = {}) {
      const turns = msgs.querySelectorAll('.turn');
      const target = meta.turn_id ? msgs.querySelector('#'+meta.turn_id) : turns[turns.length-1];
      const box = target && target.querySelector('#liveSteps');
      if (!box) return;
      box.hidden = false;
      const event = {...meta,tool:name,state:meta.state || (kind==='ok'?'returned':kind==='err'?'failed':'started')};
      const key=encodeURIComponent(String(event.run_id || '')+':'+String(event.call_id || event.step_id || name));
      const row=[...box.querySelectorAll('[data-step-key]')].find(n=>n.dataset.stepKey===key);
      const html=global.VigilAgentView.step(event,!!row?.open);
      if(row)row.outerHTML=html;else box.insertAdjacentHTML('beforeend',html);
      msgs.scrollTop = msgs.scrollHeight;
    }
    async function loadMsgs(force) {
      // History is a recovery view, not the owner of an in-flight bubble.
      // Replacing it here detaches its stream subscriber during long requests.
      if (busy && busyScope === recordKey()) return;
      // The welcome page has no conversation context; never load unscoped
      // history or Creator Jobs while it is active.
      if (!activeConversationId) return;
      const loadSeq = ++conversationLoadSeq;
      let loadConversationId = activeConversationId;
      const loadState = view.querySelector('#chatLoadState');
      const showRetry = (message) => {
        heroEl.hidden = true; chat.classList.remove('is-empty', 'is-draft'); loadState.hidden = false;
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
      if (busy && busyScope === recordKey()) return;
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
        if (job.conversation_id === loadConversationId || (r.conversation && r.conversation.legacy && job.legacy_unassigned))
          records.set(job.request_id, {...job,input:job.feedback || job.requirement});
      }
      // Local display rows are hints; only a scoped Brain snapshot owns the composer.
      let unknownRun = null;
      if (!busy) {
        const latest = [...records.values()].filter(i=>i.kind==='chat' && i.run_id)
          .sort((a,b)=>(b.created_at || 0)-(a.created_at || 0))[0];
        if (latest) {
          const authority=await global.VigilAgentState.lookup(get,{runId:latest.run_id,
            conversationId:loadConversationId,requestId:latest.request_id,src:state.src || 'pc'});
          if (loadSeq !== conversationLoadSeq || activeConversationId !== loadConversationId) return;
          if (authority.kind==='snapshot') records.set(latest.request_id,{...latest,...authority.item,kind:'chat'});
          else if (['missing','replaced'].includes(authority.kind)) {
            records.set(latest.request_id,{...latest,status:'interrupted',can_resume:false,
              error:{message:'任务记录已不存在或已变化，未重放工具'}});
            if (authority.item) records.set(authority.item.request_id,{...authority.item,kind:'chat'});
          } else if (['queued','running','pending','state_unknown'].includes(latest.status)) {
            unknownRun=latest.run_id;
            records.set(latest.request_id,{...latest,status:'state_unknown',can_resume:false,
              error:{message:authority.kind==='forbidden'?'身份无权限，任务状态待确认':'连接异常，任务状态待确认'}});
          }
        }
      }
      const jobsToWatch = [];
      // 断点续跑恢复：最近一条 chat 记录若停在可续跑状态，发送键恢复成"继续"。
      let lastResumable = null;
      let lastActive = null;
      let newestChatTs = 0;
      for (const item of [...records.values()].sort((a,b)=>a.created_at-b.created_at)) {
        const input = item.input || item.requirement || '';
        const status = item.status || 'pending';
        if (['chat', 'coding'].includes(item.kind) && item.run_id) {
          if ((item.created_at || 0) >= newestChatTs) { newestChatTs = item.created_at || 0; lastResumable = null; lastActive = null; }
          if (['queued', 'running'].includes(status)) lastActive = item;
          if (item.can_resume !== false && ['paused','paused_budget','interrupted'].includes(status)) lastResumable = item;
        }
        // Successful legacy chat already has its own durable pair. Reuse it.
        if (item.kind === 'chat' && status === 'succeeded' && items.some((m,i)=>m.role==='user'&&m.text===input&&items[i+1]?.text===item.content)) {
          try { localStorage.setItem(recordKey(), JSON.stringify(localRecords().filter(r=>r.request_id!==item.request_id))); }
          catch (_) { toast('本地记录合并未能保存'); }
          continue;
        }
        const t = item.created_at * 1000;
        items.push({role:'user',text:input,t,request_id:item.request_id});
        if (item.job_id) {
          // Creator jobs restore as a task card. Their stored ``content`` is the
          // raw model response and must never be rendered as a chat message.
          items.push({role:'creator',t,request_id:item.request_id,job:item});
          jobsToWatch.push(item);
          continue;
        }
        const message = (item.error ? creatorErrorText(item.error) : '') || ({pending:'排队中…',running:'执行中…',
          failed:'请求失败',cancelled:'已取消',interrupted:'已中断',succeeded:'任务完成'})[status] || status;
        items.push({role:'assistant',text:item.status==='succeeded' ? (item.result ?? item.content ?? '') : (item.content || ''),statusNote:status === 'succeeded' ? '' : message,t,
          request_id:item.request_id,partial:status!=='succeeded',
          run:item.run_id ? item : null});
      }
      items.sort((a,b)=>toMs(a.t)-toMs(b.t));
      // 恢复判断放渲染后：只信"最近一条"chat 记录，旧的可续跑任务不抢按钮。
      if (!busy) {
        if (unknownRun) {
          activeRunId = null; resumableRunId = null; uncertainRunId = unknownRun;
          setComposerMode('uncertain');
        } else if (lastActive && lastActive.run_id) {
          // 页面刷新/切换会丢掉 renderer 内存，但不会停止 Brain 的任务。
          // 先恢复实际运行中的 run，避免把发送键误画成继续或发送。
          activeRunId = lastActive.run_id;
          resumableRunId = null;
          setComposerMode('pause');
        } else if (lastResumable && lastResumable.run_id) {
          activeRunId = null;
          resumableRunId = lastResumable.run_id;
          setComposerMode('resume');
        } else {
          activeRunId = null;
          resumableRunId = null;
          uncertainRunId = null;
          liveReconcile = null;
          setComposerMode('send');
        }
      }
      const renderKey = JSON.stringify(items);
      if (!force && renderKey === lastCount) return;
      lastCount = renderKey;
      if (!items.length) {
        // A selected session is an empty chat; welcome is only for no active session.
        heroEl.hidden = true;
        chat.classList.remove('is-empty');
        chat.classList.add('is-draft');
        heroEl.innerHTML = '';
        msgs.innerHTML = '';
        drawSessions();
        return;
      }
      drawSessions();
      heroEl.hidden = true;
      chat.classList.remove('is-empty');
      chat.classList.remove('is-draft');
      // Preserve live draft nodes only while refreshing this same conversation.
      // Switching sessions detaches these nodes; scoped Creator jobs restore
      // that conversation's cards from durable Brain state.
      const draftCards = renderedDraftConversationId === loadConversationId
        ? [...msgs.querySelectorAll('.crturn')] : [];
      msgs.innerHTML = items.map((m) => {
        const mine = ['me', 'user', 'self'].includes(String(m.role || '').toLowerCase());
        if (m.role === 'creator') {
          // Filled by restoreCreatorJob/watchCreatorJob below.
          return '<div class="turn cr-live-turn" data-job="' + esc(m.job.job_id) + '"></div>';
        }
        // 用户消息纯文本（esc），AI 消息用 Markdown 渲染
        const t = mine || m.partial ? esc(m.text || '').replace(/\n/g,'<br>') : U.md(m.text || '', { codeActions: true });
        const atts = mine && Array.isArray(m.attachments) ? m.attachments.map((a) =>
          '<div class="msg-attachment">' + icon(a.kind === 'image' ? 'img' : 'clip', 'sm') + ' ' +
          esc(a.name || '附件') + (a.size ? ' · ' + esc(bytes(a.size)) : '') + '</div>').join('') : '';
        // 不画头像、不写名字：这是工具界面，不是"跟谁对话"。
        // 她说的在左、你说的在右，不用再靠头像区分。
        if (mine) return '<div class="turn me"><div class="bdy"><div class="txt">' + t + atts + '</div></div></div>';
        // 状态说明（执行中/已取消…）不再塞进正文气泡：小字一行跟在下面，
        // 取消也只是一条文字链，不是按钮气泡。
        const runable = m.run && ['queued','running'].includes(m.run.status);
        const note = (m.statusNote ? '<div class="run-note"><span>' + esc(m.statusNote) + '</span>' +
          (runable ? '<button class="agent-cancel" data-run="' + esc(m.run.run_id) + '">取消</button>' : '') + '</div>' : '');
        const evidence = (m.run?.steps || []).map((step,index,steps)=>step.kind==='tool' ? global.VigilAgentView.step({...step,run_id:m.run.run_id,state:step.result_state || step.status,output:step.result}) : (index < steps.length-1 && step.content ? '<div class="agent-commentary">'+U.md(step.content)+'</div>' : '')).join('');
        const coding = m.run?.kind === 'coding' ? global.VigilAgentView.codingSummary(m.run.coding) : '';
        const body = m.text
          ? '<div class="txt md">' + t + '</div>' + note
          : (note || '<div class="txt md"></div>');
        return '<div class="turn"' + (m.job ? ' data-job="' + esc(m.job.job_id) + '"' : '') + '><div class="bdy">' +
          (coding || evidence ? '<div class="agent-progress live-steps">'+coding+evidence+'</div>' : '') + body +
          '<div class="foot"><span>' + esc(fmtTime(m.t, 'time')) + '</span>' +
          '</div></div></div>';
      }).join('');
      renderedDraftConversationId = loadConversationId;
      void decorateCapabilityApprovals(loadConversationId);
      for (const button of msgs.querySelectorAll('.agent-cancel')) {
        button.onclick = async () => {
          button.disabled = true;
          const r = pick(await post('/api/agent/runs/' + encodeURIComponent(button.dataset.run) + '/cancel',
            {src:state.src || 'pc', conversation_id:loadConversationId}));
          if (!r.ok) { button.disabled = false; toast('取消未确认，请检查任务状态'); }
          else await loadMsgs(true);
        };
      }
      draftCards.forEach((n) => msgs.appendChild(n));
      for (const job of jobsToWatch) {
        const root = msgs.querySelector('[data-job="' + job.job_id + '"]');
        if (!root) continue;
        if (['succeeded', 'failed', 'cancelled', 'interrupted'].includes(job.status)) void restoreCreatorJob(root, job);
        else void watchCreatorJob(root, job, recordKey());
      }
      msgs.scrollTop = msgs.scrollHeight;
    }

    async function send() {
      // 续跑也走这条主链路：resumeTurn 把 pendingResumeOf 置位后调进来，
      // 带上 resume_of 发给同一条聊天 SSE 通道。
      const resumeOf = pendingResumeOf; pendingResumeOf = null;
      // 插话同样复用这条链路：带 interject_of 发给同一个 SSE 通道，
      // 服务端把旧 run 停成 paused/interjected 并另起一个带检查点的新 run。
      const interjectOf = pendingInterjectOf; pendingInterjectOf = null;
      const ta = view.querySelector('#ta');
      const text = (ta.value || '').trim();
      if (!global.vigil || (!text && !imgB64 && !attachment && !resumeOf) || busy || submitPending) {
        if (!interjectOf) return;
      }
      if (interjectOf && !text && !imgB64) return;
      clearTokenRate();
      submitPending = true;
      updateComposer();
      let scopeReady;
      try { scopeReady = await loadRecordScope(); }
      finally { submitPending = false; updateComposer(); }
      // 插话时 busy 是真的（旧任务还在收尾），但它不是"不能发"的理由。
      if (!scopeReady || (busy && !interjectOf)) return;
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
      currentRequestId = requestId;
      liveReconcile = null;
      uncertainRunId = null;
      const turnScope = sendScope;
      const remember = patch => rememberTurn(requestId, patch, turnScope);
      remember({input:resumeOf ? '继续上次任务' : text,kind:'chat',status:'pending'});
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
      composerMode = 'send';
      drawComposerIcon();
      if (!resumeOf) resumableRunId = null;
      heroEl.hidden = true;
      view.querySelector('#chatLoadState').hidden = true;
      chat.classList.remove('is-empty');
      updateComposer();
      const btn = view.querySelector('#sendBtn');
      btn.disabled = true;

      // 乐观气泡：用户消息先画上去（续跑时也画一条，让会话留痕）
      const bubbleText = resumeOf ? '▶ 继续上次任务' : text;
      msgs.insertAdjacentHTML('beforeend',
        '<div class="turn me"><div class="bdy"><div class="txt">' + esc(bubbleText) +
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

      const turnStart = Date.now();
      const elapsedEl = msgs.querySelector('#'+turnId+' .agent-elapsed');
      const updateElapsed=()=>{elapsedEl.textContent=global.VigilAgentView.elapsed(Date.now()-turnStart);};
      updateElapsed();
      const elapsedTimer=setInterval(()=>{if(!elapsedEl.isConnected || streamDone)clearInterval(elapsedTimer);else updateElapsed();},1000);
      const textEl = document.getElementById(textId);
      const statusEl = msgs.querySelector('#' + turnId + ' .stream-status');
      let textBuf = '';
      let streamDone = false;
      let textDirty = false;
      let renderTimer = null;
      let renderFrame = null;
      let agentRunId = null;
      let lastAgentSeq = 0;
      let reconcileTimer = null;
      let needsSnapshotText = false;
      const agentCancel = document.createElement('button');
      agentCancel.className = 'agent-cancel';
      agentCancel.textContent = '取消';
      agentCancel.type = 'button';
      agentCancel.onclick = async () => {
        if (!agentRunId) return;
        agentCancel.disabled = true;
        try {
          const r = pick(await post('/api/agent/runs/' + encodeURIComponent(agentRunId) + '/cancel',
            {src:state.src || 'pc', conversation_id:sendConversationId}));
          if (!r.ok) throw Error(r._err || '取消未确认');
          statusEl.textContent = '已取消后续调度；在途操作可能仍在执行';
        } catch (_) {
          agentCancel.disabled = false;
          toast('取消未确认，请检查任务状态');
        }
      };

      // Parse incomplete Markdown into a safe live preview. Token bursts are
      // coalesced to one DOM update per 100ms, then painted on one animation frame.
      function renderStreamText(finalFrame = false) {
        if (!textEl || activeConversationId !== sendConversationId) return;
        if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; }
        if (renderFrame !== null) { cancelAnimationFrame(renderFrame); renderFrame = null; }
        const paint = () => {
          renderFrame = null;
          if (!textEl.isConnected || activeConversationId !== sendConversationId) return;
          textDirty = false;
          textEl.innerHTML = U.md(textBuf, { codeActions: true });
          msgs.scrollTop = msgs.scrollHeight;
        };
        if (finalFrame) paint();
        else renderFrame = requestAnimationFrame(paint);
      }

      function scheduleRender() {
        if (renderTimer || renderFrame !== null) return;
        renderTimer = setTimeout(() => {
          renderTimer = null;
          renderStreamText(false);
        }, 100);
      }

      // A closed SSE socket says nothing about the durable run.  Ask Brain for
      // the run snapshot and keep polling while it is still queued/running;
      // only a terminal snapshot is allowed to release the composer.
      async function reconcileRun() {
        if (streamDone || !agentRunId || activeConversationId !== sendConversationId ||
            turnScope !== recordKey() || currentRequestId !== requestId) return;
        if (reconcileTimer) { clearTimeout(reconcileTimer); reconcileTimer = null; }
        const authority = await global.VigilAgentState.lookup(get, {runId:agentRunId,
          conversationId:sendConversationId,requestId,src:state.src || 'pc'});
        if (streamDone || activeConversationId !== sendConversationId || currentRequestId !== requestId) return;
        if (authority.kind === 'snapshot') {
          const snap = authority.item, status = snap.status;
          if (status === 'succeeded') {
            onEvent({type:'done',text:snap.content || snap.result || '',run_id:agentRunId});
          } else if (global.VigilAgentState.stopped.has(status)) {
            onEvent({type:'error',text:snap.content || '',run_id:agentRunId,status,
              class:snap.error?.reason || 'service_error',msg:snap.error?.message || '任务已停止',
              detail:snap.error?.detail || ''});
          } else {
            if (needsSnapshotText) {
              textBuf = snap.content || textBuf;
              textEl.innerHTML = U.md(textBuf, {codeActions:true});
              needsSnapshotText = false;
            }
            busy = true; busyScope = turnScope;
            activeRunId = agentRunId; uncertainRunId = null;
            statusEl.textContent = '任务仍在执行 ';
            statusEl.appendChild(agentCancel);
            setComposerMode('pause');
            reconcileTimer = setTimeout(reconcileRun, 2000);
          }
        } else if (authority.kind === 'missing' || authority.kind === 'replaced') {
          onEvent({type:'error',run_id:agentRunId,status:'interrupted',class:'run_missing',
            can_resume:false,msg:authority.kind === 'missing' ? '任务已不存在；执行状态已同步，未重放工具' : '任务记录已变化；请查看当前会话任务'});
          if (authority.kind === 'replaced') void loadMsgs(true);
        } else {
          // Unknown is not success or cancellation. Release the stale Pause
          // control, keep its request identity, and allow a read-only recheck.
          activeRunId = null; resumableRunId = null; uncertainRunId = agentRunId;
          busy = false; busyScope = null;
          statusEl.textContent = authority.kind === 'forbidden' ? '身份无权限，任务状态待确认' : '连接异常，任务状态待确认';
          setComposerMode('uncertain');
          reconcileTimer = setTimeout(reconcileRun, 2000);
        }
      }
      liveReconcile = reconcileRun;

      // This is a user-visible phase summary derived only from received events;
      // provider reasoning deltas are intentionally never rendered here.
      let thinkingRow=null;
      function setProgress(summary,state='running'){
        if(!summary)return;
        const steps=msgs.querySelector('#'+turnId+' #liveSteps');steps.hidden=false;
        if(!thinkingRow){
          thinkingRow=document.createElement('div');thinkingRow.className='agent-thinking';
          thinkingRow.innerHTML=icon('brain','sm')+'<span></span>';steps.appendChild(thinkingRow);
        }
        thinkingRow.dataset.state=state;
        thinkingRow.querySelector('span:last-child').textContent=String(summary);
      }
      // 监听流式事件
      const onEvent = (evt) => {
        if (activeConversationId !== sendConversationId || currentRequestId !== requestId) return;
        if (evt.conversation_id && evt.conversation_id !== sendConversationId) return;
        if (evt.job_id) return;
        if (evt.request_id && evt.request_id !== requestId) return;
        if (evt.run_id && !agentRunId && evt.type !== 'agent_run') return;
        // Agent events without an id are legacy chat events.  Once an Agent
        // run is bound, accepting such an event would let a late stream from a
        // previous turn overwrite this turn's text or terminal state.
        if (evt.run_id && agentRunId && evt.run_id !== agentRunId) return;
        if (agentRunId && !evt.run_id &&
            ['stage','tool','tool_reused','run_snapshot','think','text','progress','done','error',
             'model_request_started','token_rate'].includes(evt.type)) return;
        if (evt.type === 'agent_run') {
          if (agentRunId && evt.run_id !== agentRunId) return;
          if (!agentRunId) agentRunId = evt.run_id;
        }
        if (Number.isSafeInteger(evt.seq)) {
          if (evt.seq <= lastAgentSeq) return;
          lastAgentSeq = evt.seq;
        }
        if (turnScope !== recordKey()) return;
        if (streamDone) return;
        if (evt.type === 'model_request_started') {
          startTokenRate();
          setProgress(agentRunId && !evt.step_id ? '正在准备模型请求' : '正在请求模型生成回复');
        } else if (evt.type === 'image') {
          // 图片四段状态各有各的话：收到 / 分析中 / 看不了 / 已看到。
          // 「看不了」必须说出来 —— 否则她答的是猜的，用户却以为她看过了。
          if (evt.stage === 'describe') {
            // Kept only for old bridge events.  Native chat requests do not
            // emit this stage; a provider refusal is shown as its real error.
            statusEl.textContent = evt.ok ? '图片已收到，正在回复 '
              : '图片没有送进当前对话模型（' + String(evt.msg || '未知错误') + '）';
            if (!evt.ok) toast(String(evt.msg || '图片没有送进当前对话模型'));
          } else {
            statusEl.textContent = evt.ok ? '图片已收到'
              : '图片没收到：' + String(evt.msg || evt.status || '未知原因');
            if (!evt.ok) toast(String(evt.msg || '图片没有送进模型'));
          }
          if (agentRunId) statusEl.appendChild(agentCancel);
        } else if (evt.type === 'token_rate') {
          finishTokenRate(evt);
        } else if (evt.type === 'agent_run') {
          activeRunId = evt.run_id;
          remember({run_id:agentRunId,status:'running'});
          statusEl.textContent = '执行中 ';
          statusEl.appendChild(agentCancel);
          setComposerMode('pause');
          if (!reconcileTimer) reconcileTimer = setTimeout(reconcileRun, 2000);
        } else if (evt.type && evt.type.startsWith('coding_')) {
          const payload = evt.payload || evt;
          const phase = payload.phase || evt.phase || '';
          if (phase) setProgress('Coding Agent：' + String(phase));
          if (evt.type === 'coding_phase') {
            statusEl.textContent = 'Coding Agent · ' + phase + ' ';
            if (agentRunId) statusEl.appendChild(agentCancel);
          } else if (evt.type === 'coding_plan') {
            const count = Array.isArray(payload.steps) ? payload.steps.length : 0;
            addStep('coding-plan', '已生成计划（' + count + ' 步）', 'neutral');
          } else if (evt.type === 'coding_file') {
            addStep('coding-file', '修改 ' + String(payload.path || ''), 'ok');
          } else if (evt.type === 'coding_test') {
            addStep('coding-test', (payload.passed ? '验证通过：' : '验证失败：') + String(payload.command || ''), payload.passed ? 'ok' : 'err');
          } else if (evt.type === 'coding_diagnosis') {
            addStep('coding-diagnosis', '已定位失败原因：' + String(payload.summary || payload.root_cause || ''), 'warn');
          } else if (evt.type === 'coding_repair') {
            addStep('coding-repair', '自动修复第 ' + String(payload.repair_count || 0) + ' 轮', 'ok');
          } else if (evt.type === 'coding_workflow') {
            const progress = payload.progress || {};
            const done = Number(progress.completed || 0), total = Number(progress.total || 0);
            const current = (payload.steps || []).find(s => s.status === 'running');
            let label = '已更新';
            if (payload.requires_review) label = '需要人工核对';
            else if (payload.status === 'failed') label = '任务失败';
            else if (payload.status === 'cancelled') label = '已取消';
            else if (payload.status === 'paused') label = '已暂停';
            else if (current) label = '正在执行：' + String(current.title || current.step_id || '');
            else if (total) label = '已完成 ' + done + ' / ' + total + ' 步';
            addStep('coding-workflow', '复杂任务 · ' + label, payload.requires_review || payload.status === 'failed' ? 'warn' : 'neutral');
          }
        } else if (evt.type === 'stage') {
          if(evt.stage==='generating')setProgress(evt.step_id
            ? '正在处理任务的第 ' + String(evt.step_id) + ' 步'
            : '正在处理当前任务的下一步');
          statusEl.textContent = ({generating:'模型生成中 ',waiting_tool:'等待工具结果 '})[evt.stage] || '执行中 ';
          if (agentRunId) statusEl.appendChild(agentCancel);
        } else if (evt.type === 'tool') {
          const toolProgress = global.VigilAgentView.toolProgress(evt);
          setProgress(toolProgress.summary, toolProgress.state);
          // 每次工具调用落一行小字：她在干什么、成没成，全程可见。
          // 'denied' 是被明确拒绝，不算执行失败；'returned' 带 error
          // （执行完但没有任何输出）也不能显示成完成。
          const denied = evt.state === 'denied';
          const ok = evt.state === 'returned' && !evt.error;
          const failed = ['not_dispatched', 'unknown', 'failed', 'denied'].includes(evt.state)
            || (evt.state === 'returned' && !!evt.error);
          if (textBuf.trim() && !evt.historical) {
            const steps=msgs.querySelector('#'+turnId+' #liveSteps');
            steps.insertAdjacentHTML('beforeend','<div class="agent-commentary">'+U.md(textBuf)+'</div>');
            textBuf='';textDirty=false;textEl.innerHTML='';
          }
          addStep(evt.tool || 'tool', ok ? '完成' : denied ? '已拒绝' : failed ? '失败' : '执行中',
            ok ? 'ok' : denied ? 'warn' : failed ? 'err' : 'neutral', {...evt,turn_id:turnId});
          statusEl.textContent = (evt.state === 'started' ? '正在调用 ' : '调用 ') + (evt.tool || '工具') + ' ';
          if (agentRunId) statusEl.appendChild(agentCancel);
        } else if (evt.type === 'tool_reused') {
          // 看门狗发现她在原地打转（同样的调用拿到同样的结果）。
          addStep('watchdog', '检测到重复调用，提醒她换个方法', 'danger');
        } else if (evt.type === 'run_snapshot') {
          textBuf = evt.text || '';
          remember({content:textBuf,status:'running'});
          textDirty = true;
          scheduleRender();
        } else if (evt.type === 'think' && evt.text) {
          // 思考流单独一块、默认折叠：推理内容不该混进正文气泡，也不该
          // 被静默丢掉 —— 以前这条分支不存在，服务端推了 think 事件，
          // 客户端收下就再也没人画，于是"思考过程"永远不显示。
          const steps=msgs.querySelector('#'+turnId+' #liveSteps');
          let thinkBox=steps.querySelector('.agent-think');
          if(!thinkBox){
            thinkBox=document.createElement('details');
            thinkBox.className='agent-think';
            thinkBox.innerHTML='<summary>'+icon('brain','sm')
              +'<span class="agent-think-label">思考中</span></summary>'
              +'<div class="agent-think-body"></div>';
            steps.insertBefore(thinkBox, steps.firstChild);
          }
          const thinkBody=thinkBox.querySelector('.agent-think-body');
          thinkBody.textContent=(thinkBody.textContent||'')+evt.text;
          const thinkLabel=thinkBox.querySelector('.agent-think-label');
          if(thinkLabel) thinkLabel.textContent='思考中 · '+thinkBody.textContent.length+' 字';
        } else if (evt.type === 'progress' && evt.summary) {
          setProgress(evt.summary, evt.state || 'running');
        } else if (evt.type === 'text' && evt.text) {
          setProgress('正在接收模型回复');
          addVisibleTokenEstimate(evt.text);
          textBuf += evt.text;
          remember({content:textBuf,status:'running'});
          textDirty = true;
          scheduleRender();
        } else if (evt.type === 'done') {
          setProgress('任务已完成','succeeded');
          finishTokenRate(evt.token_rate);
          streamDone = true;
          finishBusy(turnScope);
          if (reconcileTimer) { clearTimeout(reconcileTimer); reconcileTimer = null; }
          stopListening();
          activeRunId = null;
          resumableRunId = null;
          setComposerMode('send');
          textBuf = evt.result ?? evt.text ?? textBuf;
          remember({content:textBuf,status:'succeeded',error:null});
          // 强制刷新最后一帧 Markdown，并取消排队中的预览绘制。
          renderStreamText(true);
          statusEl.textContent = fmtTime(Date.now(), 'time');
          finishBusy(turnScope);
          refreshConversations().catch(() => {});
        } else if (evt.type === 'error') {
          metricStartedAt = 0;
          if (evt.class === 'connection_lost' && agentRunId) {
            needsSnapshotText = true;
            reconcileRun();
            return;
          }
          setProgress(({cancelled:'任务已取消',paused:'任务已暂停',paused_budget:'任务预算已用尽',
            interrupted:'任务已中断'})[evt.status] || '任务失败',evt.status || 'failed');
          streamDone = true;
          finishBusy(turnScope);
          if (reconcileTimer) { clearTimeout(reconcileTimer); reconcileTimer = null; }
          stopListening();
          activeRunId = null;
          textBuf = evt.text || textBuf;
          remember({content:textBuf,status:evt.status || 'failed',
            error:{class:evt.class || 'service_error',message:evt.msg || '请求未完成',
                   detail:evt.detail || ''},can_resume:evt.can_resume !== false});
          // 暂停/中断不是"出错"：给出续跑指引，发送键随即变成继续键。
          uncertainRunId = null; liveReconcile = null;
          const resumable = evt.can_resume !== false && ['paused','paused_budget','interrupted'].includes(evt.status);
          if (resumable && agentRunId) resumableRunId = agentRunId;
          setComposerMode(resumable && resumableRunId ? 'resume' : 'send');
          // detail 是"哪个工具、哪个参数、哪种错误"的唯一载体：非法工具
          // 调用没进工具执行阶段，没有步骤行可看，只能在这里展开给用户。
          const errDetail = evt.detail
            ? '<br><span style="color:var(--mut);font-size:12px;white-space:pre-wrap">' + esc(evt.detail) + '</span>' : '';
          if (resumable) {
            renderStreamText(true);
            textEl.innerHTML = (U.md(textBuf || '', { codeActions: true }) + '<br><span style="color:var(--mut)">'
              + esc(evt.msg || '已暂停') + ' —— 点发送键继续这个任务</span>');
          } else {
            renderStreamText(true);
            textEl.innerHTML = U.md(textBuf || evt.text || '', { codeActions: true }) + '<br><span style="color:var(--err)">出错了：' + esc(evt.msg || '未知错误') + '（结果不完整）</span>' + errDetail;
          }
          statusEl.textContent = ({cancelled:'已取消',paused:'已暂停',paused_budget:'预算已用尽',
            interrupted:'执行中断'})[evt.status] || '失败';
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
      const activeSession = sessionItems.find((s) => s.id === activeConversationId);
      if (activeSession && activeSession.workspace_id) {
        chatPayload.workspace_id = activeSession.workspace_id;
      }
      if (resumeOf) chatPayload.resume_of = resumeOf;
        if (interjectOf) chatPayload.interject_of = interjectOf;
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
            // 插话请求没落地就要把目标还回去：pendingInterjectOf 在发送前
            // 已清空，不恢复的话用户重发只会变成新消息，旧任务还挂着。
            if (interjectOf && !agentRunId) {
              pendingInterjectOf = interjectOf;
              if (!ta.value) ta.value = text;
              // 旧 run 可能还在跑：重新拉一次会话状态，让发送键回到
              // 暂停/插话态，别让 UI 和一个还在执行的任务脱节。
              setTimeout(() => { void loadMsgs(true); }, 300);
            }
            onEvent({type:'error',class:'connection_lost',status:'interrupted',
              msg:r.err || '连接中断，结果不完整'});
          }
          if (!streamDone) {
            // If this is a durable Agent run, reconcile its real status.  Do
            // not release the input box merely because the socket was quiet.
            if (agentRunId) reconcileTimer = setTimeout(reconcileRun, 2000);
            else setTimeout(() => {
              if (!streamDone) {
                console.warn('[stream] no done event received, textBuf length:', textBuf.length);
                remember({content:textBuf,status:'interrupted',
                  error:{class:'connection_lost',message:'未收到完整回复'}});
                renderStreamText(true);
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
        // 插话在半路失败同样要还目标：见 apiStream 分支的同款处理。
        if (interjectOf && !agentRunId && !streamDone) {
          pendingInterjectOf = interjectOf;
          if (!ta.value) ta.value = text;
          setTimeout(() => { void loadMsgs(true); }, 300);
        }
        remember({content:textBuf,status:'interrupted',error:{message:'连接中断，结果不完整；未自动重发'}});
        streamDone = true;
        stopListening();
        textEl.innerHTML = '<span style="color:var(--err)">出错了：' + esc(String(e.message || e)) + '</span>';
        statusEl.textContent = '失败';
        finishBusy(turnScope);
        refreshConversations().catch(() => {});
      }
    }

    // 发送键/回车的统一入口：按 composer 三态分派。
    function composerAction() {
      const ta = view.querySelector('#ta');
      if (composerMode === 'uncertain' && !ta.value.trim()) { if (liveReconcile) void liveReconcile(); else void loadMsgs(true); return; }
      // 执行中输入了内容 = 插话：停掉后续调度，带着进度重新规划。
      // 输入框空着才是"暂停"。两者都落在同一个按钮上，靠有没有文字区分。
      if (composerMode === 'pause' && activeRunId && (ta.value.trim() || imgB64)) {
        pendingInterjectOf = activeRunId;
        send();
        return;
      }
      if (composerMode === 'pause' && activeRunId) { firePause(); return; }
      if (composerMode === 'resume' && resumableRunId &&
          !ta.value.trim() && !imgB64 && !attachment) { resumeTurn(resumableRunId); return; }
      send();
    }
    async function firePause() {
      if (!activeRunId || pausePending) return;
      const rid=activeRunId, cid=activeConversationId, request=currentRequestId;
      pausePending=true;
      try {
        const r=pick(await post('/api/agent/runs/'+encodeURIComponent(rid)+'/pause',
          {src:state.src || 'pc',conversation_id:cid}));
        if (activeConversationId !== cid || activeRunId !== rid || currentRequestId !== request) return;
        // Apply the actual snapshot even if the terminal SSE event was lost.
        if (liveReconcile) await liveReconcile();
        else await loadMsgs(true);
        if (!r.ok) toast('暂停未确认：'+(r._err || '正在核对任务状态'));
      } finally { pausePending=false; }
    }
    async function resumeTurn(rid) {
      if (busy || !rid) return;
      pendingResumeOf = rid;
      await send();
    }
    view.querySelector('#sendBtn').addEventListener('click', (e) => {
      e.preventDefault();
      composerAction();
    });
    view.querySelector('#ta').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        composerAction();
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
    const permChip = view.querySelector('#permChip');
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
        drawComposerFoot();
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
        '<button class="btn oline" id="lpAdd">添加</button>' +
        '<button class="btn oline" id="lpPick">选择文件夹…</button></div>' +
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
      // 系统目录选择器：不让人手打路径。选完立刻写进白名单并在面板里可见。
      pop.querySelector('#lpPick').addEventListener('click', async () => {
        const api = global.vigil && global.vigil.workspace;
        if (!api || !api.pick) { toast('当前版本没有系统文件夹选择器'); return; }
        const r = await api.pick();
        if (!r || !r.ok || r.canceled || !r.path) return;
        const p = r.path;
        await save({ roots: roots.includes(p) ? roots : roots.concat([p]) });
        toast('已授权目录：' + p);
        drawLocalPop();
      });
      pop.querySelectorAll('[data-rm]').forEach((b) => b.addEventListener('click', async () => {
        await save({ roots: roots.filter((x) => x !== b.dataset.rm) });
        drawLocalPop();
      }));
    }

    // 盾牌和输入框下方那个"仅授权目录"标签共用这一个面板：授权范围留在
    // 聊天框里改，不跳设置页。位置贴着点它的那个按钮，不飘到页面中间。
    function placeLocalPop(anchor) {
      if (!pop) return;
      const shell = view.querySelector('.composer');
      if (!shell) return;
      const box = shell.getBoundingClientRect(), chip = anchor.getBoundingClientRect();
      const width = Math.min(560, Math.max(260, box.width - 24));
      pop.classList.add('anchored');
      pop.style.width = width + 'px';
      pop.style.left = Math.max(8, Math.min(chip.left - box.left, box.width - width - 8)) + 'px';
    }
    function toggleLocalPop(anchor) {
      if (!pop) return;
      if (wsPop && !wsPop.hidden) closeWsPop();
      const open = pop.hidden;
      pop.hidden = !open;
      if (anchor) anchor.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (!open) return;
      placeLocalPop(anchor || localBtn);
      void drawLocalPop();
    }
    const closePop = (e) => {
      if (pop.hidden) return;
      if (pop.contains(e.target) || localBtn.contains(e.target) ||
          (permChip && permChip.contains(e.target))) return;
      pop.hidden = true;
      localBtn.setAttribute('aria-expanded', 'false');
      if (permChip) permChip.setAttribute('aria-expanded', 'false');
    };
    document.addEventListener('pointerdown', closePop);
    const offDoc = () => {
      document.removeEventListener('pointerdown', closePop);
      window.removeEventListener('resize', resizeComposer);
    };
    localBtn.addEventListener('click', () => toggleLocalPop(localBtn));

    showWelcomeHome(false);
    void initializeConversationView();
    const onBrainConnected=()=>{
      if(!msgs.isConnected)return;
      void refreshConversations().catch(()=>{});
      void refreshWorkspaces().catch(()=>{});
      if(activeConversationId)void loadMsgs(false);
    };
    window.addEventListener('vigil-brain-connected',onBrainConnected);
    ctx.onDispose(()=>window.removeEventListener('vigil-brain-connected',onBrainConnected));
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
  // PC persona selection edits the local Core's persona_file.
  async function renderPersona(ctx) {
    ctx.view.classList.add('persona-view');
    const CHANS = [['pc', '本机 PC', '本机默认人设']];
    let curChan = 'pc';
    const r = pick(await get('/api/persona'));
    const list = r.templates || r.personas || r.presets || [];
    const per = {};
    for (const [c] of CHANS) {
      const pr = pick(await get('/api/persona?src=' + c));
      per[c] = { key: pr.src_persona_key || pr.key || '', name: pr.name || '' };
    }

    ctx.topbar.className = 'topbar';
    ctx.topbar.innerHTML = topbar('人格与语气', '存在本机 Brain · 应用/编辑立即生效，下一句话就是新人设',
      '<button class="btn oline" id="reloadP">' + icon('refresh', 'sm') + '重载人格</button>');
    ctx.panel.innerHTML = '<div class="phead"><h2>人设</h2></div>' +
      '<div class="pseg"><button data-sub="pc" class="on">本机 PC</button></div>' +
      '<div class="plist"><div class="empty" style="text-align:left;padding:14px 10px">' +
      '点卡片上的「应用」切换本机人设。历史与记忆按人设隔离。</div></div>';

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
      const f = pick(await get('/api/persona/full?key=' + encodeURIComponent(key)));
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
        const rr = pick(await post('/api/persona/save', { key: key, src: curChan, fields: fields2 }));
        btn.disabled = false;
        ctx.view.querySelector('#pfMsg').textContent = rr.ok ? '已保存' + (rr.reloaded ? '，她已切换到新设定' : '（这套人设她当前没在用，下次应用时生效）') : ('保存失败：' + (rr._err || rr.err || ''));
        toast(rr.ok ? '人设已保存' : '保存失败');
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
        const rr = pick(await post('/api/persona/apply', { key: b.dataset.apply, src: curChan }));
        b.disabled = false;
        toast(rr.ok ? '已切换（' + n + ' → ' + (rr.name || b.dataset.apply) + '），下一句话生效' : '切换失败：' + (rr._err || rr.err || ''));
        if (rr.ok) { per[curChan] = { key: b.dataset.apply, name: rr.name || b.dataset.apply }; draw(); }
      }));
    };
    U.bindSeg(ctx.panel.querySelector('.pseg'), (k) => { curChan = k; draw(); });
    ctx.topbar.querySelector('#reloadP').addEventListener('click', async () => {
      const rr = pick(await post('/api/reload_persona'));
      toast(rr.ok ? '人设已重载' : '重载失败');
      ctx.refresh();
    });
    draw();
  }

  // ═══ 3. 记忆 ═══════════════════════════════════════════════
  // 当前「人设 × 渠道」分区的记忆条目（本机 GET /api/memory）。
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
      '<div class="muted" style="font-size:11px;margin:6px 0">改动（编辑 / 删除）直接写本机 Brain 的记忆库，删了就是删了（墓碑账本，不会被检索复活）。</div>' +
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
        if (r.ok) { toast('已改'); load(); }
        else toast('没改成：' + (r.err || r._err || ''));
      }));
      box.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
        if (!await confirmDialog('删掉这条记忆？删了就删了，检索不会再命中它。')) return;
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
          errBox('读不到记忆', r._err || '内核需要 M5 的 /api/memory/list（重启内核后生效）');
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
      // 同一个东西。插件要重启**内核**（start() 起的线程不会自己换掉），
      // 技能要重启**技能宿主**（热重载已让它当下可用，覆盖安装后才需要宿主
      // 进程重启一次才完全干净）。说错对象，用户会去重启错的东西，
      // 然后以为问题已经解决。
      const RN = {
        plugin: '有后台线程的插件要重启内核才完全生效',
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
      const CN = { pc: '本机' };
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
      const chans = m.channels || ['pc'];
      const items = m.items || [];
      v.__items = items;
      // C3：持久的"待重启"提示。判据是服务端磁盘上的两个时间戳
      //（改动时刻 vs 本进程启动时刻），不是 toast、也不是前端变量 ——
      // 所以刷新页面、重启客户端之后这条都还在，直到内核真的重启过。
      // 服务端算不出来时（老内核没这个字段）就不显示，不猜。
      const pend = r.pending_restart || {};
      const pendOn = !!pend.pending;
      const pendWhy = (pend.reasons || []).filter(Boolean).join('；');
      v.innerHTML =
        (pendOn ? '<div class="card" style="margin-bottom:12px;border-color:var(--warn)">' +
          '<div style="display:flex;align-items:center;gap:8px"><b>有改动还没生效</b>' +
          tag('warn', '待重启内核') + '</div>' +
          '<div class="muted" style="font-size:12px;margin-top:6px">' +
          esc(pendWhy || '插件目录有改动') +
          '。上面这份清单是磁盘上的样子，内存里跑的可能还是旧的那份 —— ' +
          '重启内核后两边才一致。</div></div>' : '') +
        '<div class="sech"><b>添加插件</b><div class="sp"></div><span class="muted">zip 上传</span></div>' +
        '<div class="drop" id="plDrop">' + icon('upload', 'md') +
        '<b>把插件 .zip 拖到这里</b><span class="muted">或点击选择文件 · 安装时不会执行新代码，确认后再重载</span></div>' +
        '<div class="sech"><b>已安装的插件</b><div class="sp"></div>' +
        '<span class="tag neutral">' + items.length + ' 个</span>' +
        (pendOn ? tag('warn', '待重启生效') : '') +
        '<button class="btn oline" id="plReload">重载</button></div>' +
        (r.ok === false && !items.length ? errBox('读不到插件清单', r._err || '') :
         items.length ? items.map((p) =>
          '<div class="pkg">' +
          '<div class="pkg-ic">' + icon('plug', 'sm') + '</div>' +
          '<div class="pkg-main">' +
          '<div class="pkg-h"><b>' + esc(p.name) + '</b>' +
          (p.state === 'restart_required' ? tag('warn', '停用待重启') :
           p.enabled === false ? tag('neutral', '已停用') :
           p.err ? tag('err', '装载失败') : tag('ok', '已启用')) +
          (p.lifecycle_enabled && p.effective_by_src && p.effective_by_src.pc === false
            ? tag('warn', 'PC 渠道关闭') :
             p.lifecycle_enabled && p.effective_by_src && p.effective_by_src.pc === true
            ? tag('ok', 'PC 渠道可用') : '') +
          (p.builtin ? tag('neutral', '内置插件') : '') + '</div>' +
          '<div class="pkg-desc">' + esc(p.desc || '') + '</div>' +
          (p.builtin ? '<div class="pkg-desc">随 Vigil 程序发布，可停用；不能单独卸载，更新随程序版本进行。</div>' : '') +
          (p.diagnostic ? '<div class="pkg-desc" style="color:var(--danger)">' + esc(p.diagnostic) + '</div>' : '') +
          '<div class="pkg-meta"><span class="mono">' + esc(p.file || '') + '</span>' +
          ((p.tools || []).length ? '<span>· 工具 ' + esc(p.tools.join('、')) + '</span>' : '') +
          ((p.hooks || []).length ? '<span>· 钩子 ' + esc(p.hooks.join('、')) + '</span>' : '') +
          ((p.routes || []).length ? '<span>· 接口 ' + p.routes.length + ' 个</span>' : '') +
          '</div></div>' +
          '<div class="pkg-side">' +
          chanChips(chans, p.channels, p.name, !p.channelable) +
          (p.state === 'restart_required' ? '<button class="btn" disabled>待重启</button>' :
           '<button class="btn" data-life="' + (p.enabled === false ? 'enable' : 'disable') + '" data-name="' + esc(p.name) + '">' + (p.enabled === false ? '启用' : '停用') + '</button>') +
          (p.removable ? '<button class="btn pkg-un" data-un="' + esc(p.name) + '">卸载</button>' : '') +
          '</div></div>').join('')
          : empty('一个插件都没装', '上面拖一个 .zip 试试'));

      bindDrop(v.querySelector('#plDrop'), (f) => installZip(f, '/api/plugins/install', { source: '客户端' }, 'plugin'));
      v.querySelector('#plReload').addEventListener('click', async () => {
        const rr = pick(await post('/api/plugins/reload', {}));
        toast(rr.ok ? '插件已重载' : ('重载失败：' + (rr._err || rr.err || '未知错误')));
        plugins();
      });
      v.querySelectorAll('[data-life]').forEach((b) => b.addEventListener('click', async () => {
        const action = b.dataset.life;
        const name = b.dataset.name;
        b.disabled = true;
        const rr = pick(await post('/api/plugins/lifecycle', { name, action }));
        if (!rr.ok) toast((rr.state === 'restart_required' ? '停用请求已记录，待重启内核：' : '操作失败：') + (rr._err || rr.err || '未知错误'));
        else toast(action === 'disable' ? '插件已停用' : '插件已启用');
        window.dispatchEvent(new Event('vigil-market-changed'));
        await plugins();
      }));
      v.querySelectorAll('[data-un]').forEach((b) => b.addEventListener('click', async () => {
        if (!await confirmDialog('卸载插件 ' + b.dataset.un + '？')) return;
        // C2：卸载接口会回 restart_needed，必须消费。文件删掉只代表磁盘上
        // 没了，已经 import 的那份（连同它 start() 起的线程）还在跑 ——
        // 不告诉用户"要重启才真正停"，他就会以为线程也跟着没了。
        const rr = pick(await post('/api/plugins/uninstall', { name: b.dataset.un, purge_data: false }));
        if (!rr.ok) { toast('卸载失败：' + (rr._err || rr.err || '')); await plugins(); return; }
        toast(rr.restart_needed
          ? '文件已卸载，但它注册的后台线程还在跑 —— 要重启内核才真正停止'
          : '已卸载（剩 ' + (rr.count != null ? rr.count : '?') + ' 个）');
        window.dispatchEvent(new Event('vigil-market-changed'));
        await plugins();
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
      // 不是内核。宿主自己启动时会登记 started_at，所以这条提示同样
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
        '<b>把技能 .zip 拖到这里</b><span class="muted">或点击选择文件 · 宿主自动热重载，不用重启内核</span></div>' +
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
    // 这里只管本机控制台（pc）这一路。App / 微信那几路归各自的客户端和微信
    // 桥接，桌面端改不到、也不该假装改得到（P8.1 起桌面端只连本机 Brain）。
    async function features() {
      const f = pick(await get('/api/features'));
      const CHATS = [['pc', '本机']];
      let curCh = 'pc';
      const chName = () => (CHATS.find(([c]) => c === curCh) || ['', '?'])[1];
      const chBase = () => null;
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
        (CHATS.length > 1 ? '<div class="seg" id="chSrc">' + CHATS.map(([c, n]) =>
          '<button data-ch="' + c + '"' + (c === curCh ? ' class="on"' : '') + '>' + n + '</button>').join('') + '</div>' : '') +
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
        '<div class="muted" style="font-size:11.5px;margin-top:8px">这里是本机控制台（pc）的开关，改完即时生效。所有设置仅作用于本机 PC。</div>';

      const range = v.querySelector('#chattyRange');
      const valEl = v.querySelector('#chattyVal');
      const noteEl = v.querySelector('#chattyNote');

      async function loadLevel() {
        valEl.textContent = '…';
        const c = pick(await get('/api/chatty?src=' + curCh, { base: chBase() }));
        const lvl = c.level || 5;
        range.value = lvl;
        valEl.textContent = lvl;
        noteEl.textContent = '本机控制台的档位，存在本机 Brain，改完下一句就生效。';
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
  function renderData(ctx) {
    return global.VigilRenderData(ctx, { li, topbar, tag, toMs, bindLi, confirmDialog });
  }

  // ═══ 8. 设置 ═══════════════════════════════════════════════
  async function renderSettings(ctx) {
    ctx.topbar.className = 'topbar';
    let modelRenderId = 0;
    // 本地操控不再是设置里的一页：确认走聊天气泡，权限/目录/工具开关
    // 收在聊天输入框旁的盾牌面板里（见 renderChat）。
    // 「已绑定设备」删了：连接目标只有本机 Brain，没有设备表要管。
    // 「云端同步」整页删了（P8.1）：桌面端不再把记忆/人设推到任何远端。
    const subs = [['conn', '本地内核'], ['model', '模型管理'], ['retrieval', '检索与知识库'], ['video-apps', '视频软件'], ['danger', '权限与安全'], ['about', '关于']];
    ctx.panel.innerHTML = '<div class="phead"><h2>设置</h2></div><div class="plist">' +
      subs.map(([k, n], i) => li({ icon: k === 'conn' ? 'bell' : k === 'model' ? 'brain' : k === 'retrieval' ? 'search' : k === 'video-apps' ? 'video' : k === 'danger' ? 'x' : 'set', title: n, on: i === 0, attrs: ' data-k="' + k + '"' })).join('') +
      '</div>';
    ctx.topbar.innerHTML = topbar('本地内核', state.base || '');
    ctx.view.innerHTML = '<div class="wrap" id="sView"></div>';
    const v = ctx.view.querySelector('#sView');

    // 连接与设备：现在只说一件事 —— 本机内核活着没有、为什么没活。
    // 没有连接模式、没有远程地址、没有 token 输入框：地址由随包 Brain 决定。
    async function conn() {
      ctx.topbar.querySelector('h1').textContent = '本地内核';
      v.innerHTML =
        '<div id="scCard"></div>' +
        '<div class="sec">当前状态</div>' +
        '<div class="card">' +
        '<div class="kv"><span>连接</span><b id="cConn">' + (state.conn.ok ? '正常 ' + state.conn.ms + ' ms' : '不通') + '</b></div>' +
        '<div class="kv"><span>地址</span><b class="mono" id="cBase">' + esc(state.base || '—') + '</b></div>' +
        '<div class="kv"><span>渠道</span><b>' + esc(state.src) + '（本机 PC Agent）</b></div>' +
        '<div style="display:flex;gap:8px;align-items:center;margin-top:10px">' +
        '<button class="btn fill" id="cRetry">重试连接</button>' +
        '<button class="btn oline" id="cRestart">重启内核</button>' +
        '<span class="muted" id="cRes" style="font-size:12px"></span></div></div>';

      const card = v.querySelector('#scCard');
      const sc = global.vigil && global.vigil.sidecar;

      const drawSc = (s) => {
        s = s || {};
        const label = { ready: '运行中', starting: '启动中', failed: '启动失败', stopped: '未启动' }[s.status] || (s.status || '未知');
        const bad = s.status === 'failed' || s.status === 'stopped';
        // 日志尾里的人设读取行是启动期的常规噪音（微信已分离，人设不再随日志走），
        // 一行不落 —— 用户只关心内核活着没有、为什么没活着。
        const tail = (s.logTail || []).filter((l) => !/人设|persona/i.test(String(l))).slice(-6);
        card.innerHTML =
          '<div class="sec" style="margin-top:0">随包内核</div>' +
          '<div class="card" style="margin-bottom:14px"' + (bad ? ' data-state="failed"' : '') + '>' +
          '<div class="kv"><span>状态</span><b>' + esc(label) + (s.port ? ' · 127.0.0.1:' + s.port : '') + '</b></div>' +
          '<div class="kv"><span>自动重启</span><b>' + (s.restarts || 0) + ' / 3</b></div>' +
          (s.exe ? '<div class="kv"><span>程序</span><b class="mono" style="font-size:11px">' + esc(s.exe) + '</b></div>' : '') +
          (bad && s.diagnosis
            ? '<div class="card err" style="margin-top:10px"><b>没能启动</b><p style="margin:6px 0 0">' + esc(s.diagnosis) + '</p>' +
              '<p class="muted" style="margin:6px 0 0">本地内核是唯一的后端；它起不来时聊天、创作、插件都不可用。点上面「重启内核」重试，装坏了就重装 Vigil。</p></div>'
            : '') +
          (tail.length
            ? '<div class="muted mono" style="font-size:10.5px;margin-top:8px;line-height:1.5">' +
              tail.map((l) => esc(l)).join('<br>') + '</div>'
            : '') +
          '</div>';
      };

      if (sc) {
        sc.status().then(drawSc);
        sc.onState(drawSc);
      } else {
        drawSc({ status: 'stopped', diagnosis: '当前不在 Electron 客户端里运行' });
      }

      const say = (t) => { v.querySelector('#cRes').textContent = t; };
      v.querySelector('#cRestart').addEventListener('click', async () => {
        say('正在重启本地内核…');
        await global.vigil.sidecar.restart();
      });
      v.querySelector('#cRetry').addEventListener('click', async () => {
        say('连接中…');
        const r = await global.vigil.autoConnect();
        if (!r || !r.ok) { say('连不上：' + ((r && r.err) || '未知原因')); return; }
        await API.loadCfg();
        const c = await API.ping();
        const el = v.querySelector('#cConn');
        if (el) el.textContent = c.ok ? '正常 ' + c.ms + ' ms' : '不通';
        const b = v.querySelector('#cBase');
        if (b) b.textContent = state.base || '—';
        say(c.ok ? '已连上' : '探活失败：' + (c.err || ''));
      });
    }

    async function model(pv) {
      const renderId = ++modelRenderId;
      const isCurrent = () => renderId === modelRenderId && v.isConnected;
      // pv（可选）{base, provider}：「重新拉取」后带着表单值重画 —— 不带的话
      // 重画又按**已保存**的配置拉一遍，刚拉到的新家清单当场弹回旧家（实锤过）。
      ctx.topbar.querySelector('h1').textContent = '模型管理';
      v.innerHTML = '<div class="card" role="status" aria-live="polite">正在读取模型配置…</div>';
      // 只有一份模型配置：本机 Brain 的。App / 微信用什么模型不在这里管
      // （那属于微信桥接，P8.2 会作为插件独立配置）。
      const mget = (p, timeoutMs) => get(p, { timeoutMs });
      const mpost = (p, b, timeoutMs) => post(p, b, { timeoutMs });
      const sourceSwitch = () => '';
      const bindSourceSwitch = () => {};
      v.innerHTML = '<div class="card" role="status" aria-live="polite">正在读取模型配置…</div>';
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
      // 模型卡片：Ollama 长在这台 PC 上，所以它只可能跟本机 Brain 有关
      const localCard = (
        '<div class="card" style="margin-bottom:14px">' +
        '<b style="font-size:13px">模型</b>' +
        '<div class="seg" id="mMode" style="margin-top:10px">' +
        '<button data-mm="cloud"' + (mode === 'cloud' ? ' class="on"' : '') + '>默认</button>' +
        '<button data-mm="local"' + (mode === 'local' ? ' class="on"' : '') + '>本地 Ollama</button>' +
        '<button data-mm="hybrid"' + (mode === 'hybrid' ? ' class="on"' : '') + '>混合（默认 + 本地）</button>' +
        '</div>' +
        '<div class="muted" style="font-size:11.5px;margin-top:6px">默认：走服务商接口。本地：同一套内核用本机 Ollama 跑完整工具循环，不经过任何线上接口。混合：主模型仍是默认模型，只把需要翻本地文件的只读子任务交给本机 Ollama。</div>' +
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
        // 委派设置：只在混合模式露面（开关门控在内核侧：hybrid + enabled + 已配本地模型）
        '<div id="dlBox" style="margin-top:12px' + (mode === 'hybrid' ? '' : ';display:none') + '">' +
        '<div style="border-top:1px solid var(--border,#e5e5e5);padding-top:10px">' +
        '<label class="row" style="margin:0 0 6px"><input type="checkbox" id="dlEnabled"' + (dl.enabled ? ' checked' : '') + '> 允许主模型把只读子任务交给本机</label>' +
        '<div class="muted" style="font-size:11.5px">默认关闭。仅支持只读勘察（列目录/查找/读文件/搜内容），不能写文件、跑命令、删除或联网；单次硬顶 6 轮模型请求 / 8 次工具调用 / 180 秒。</div></div>' +
        '<div class="field" style="margin-top:10px"><label>授权工作区（每行一个绝对路径）</label>' +
        '<textarea class="input mono" id="dlRoots" rows="3" style="width:100%;resize:vertical;font-size:12px" placeholder="如 F:\projects\demo">' + esc((dl.allowed_roots || []).join('\n')) + '</textarea>' +
        '<div class="muted" style="font-size:11.5px">只有清单内目录能被委派访问，路径外一律拒绝；留空 = 任何委派都不执行（fail-closed）。</div></div>' +
        '<div style="margin-top:10px"><b style="font-size:12.5px">最近委派</b>' +
        '<div style="display:flex;gap:8px;align-items:center;margin:6px 0">' +
        '<button class="btn oline md" id="dlRefresh" type="button">刷新</button>' +
        '<span class="muted" id="dlRes" style="font-size:11.5px"></span></div>' +
        '<div id="dlList" class="muted" style="font-size:11.5px">加载中…</div></div>' +
        '</div></div>');

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
        '<div class="muted model-manager-note">这份清单由本机 Brain 保存，聊天模型菜单直接读它。配置会写入 Brain 并在重启后保留。</div></div>' +
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
        '<div class="card"><b style="font-size:13px">对话（含原生图片输入）</b><div style="margin-top:10px">' + combo('mChat', groups.chat, cur.chat, '必填') + '</div></div>' +
        '<div class="card"><b style="font-size:13px">生图</b><div style="margin-top:10px">' + combo('mImage', groups.image, cur.image) + '</div></div>' +
        '<div class="card"><b style="font-size:13px">语音识别</b><div style="margin-top:10px">' + combo('mAudio', groups.audio, cur.audio) + '</div></div>' +
        '</div>' +
        '<div class="muted" style="font-size:11.5px;margin-top:8px">本地部署（Ollama / LM Studio / vllm）：接口地址填它的地址（如 http://你的IP:11434/v1），模型名手填——注意这个地址要本机访问得到（默认就是 127.0.0.1）。</div>' +
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
        const K = { mChat: 'chat', mImage: 'image', mAudio: 'audio' };
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
      // 最近委派：本机内核 data/delegations/ 的落盘记录，每次委派的状态/用量
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
          image: v.querySelector('#mImage').value,
          audio: v.querySelector('#mAudio').value,
        };
        if (key) payload.api_key = key;
        // 运行方式 + Ollama 段（仅本机内核视图有这张卡）
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
        // 运行方式先过"带实测的切换门"：本地模型缺失 / 显存不足 / 加载超时
        // 都在这里挡住，不会先写进配置再装作切好了。其余字段仍走 apply。
        const wantMode = payload.model_mode;
        if (wantMode && wantMode !== (cur.model_mode || 'cloud')) {
          const sw = pick(await mpost('/api/run-mode', {
            mode: wantMode, model: (payload.ollama || {}).model || '',
          }));
          if (sw.ok) {
            cur.model_mode = wantMode;
            delete payload.model_mode;      // 已经生效，不再重复提交
            if (sw.warnings && sw.warnings.length) mRes.textContent = sw.warnings.join('；');
          } else if (sw._status === 404) {
            // 旧版主内核没有切换门：本地/混合在那边根本不存在，不能假装保存成功；
            // 回默认则按老接口照常保存 —— 缺一个接口不该把人挡在门外。
            delete payload.model_mode;
            if (wantMode !== 'cloud') {
              mRes.textContent = '没保存成：这台主内核还没有本地模型能力（版本较旧），更新主内核后再切';
              toast('主内核不支持本地模型');
              return;
            }
            mRes.textContent = '主内核版本较旧：已按默认模型保存';
          } else {
            mRes.textContent = '没保存成：' + (sw.err || sw._err || '运行模式切换失败');
            toast('运行模式没切成：' + (sw.reason || ''));
            return;
          }
        }
        const rr = pick(await mpost('/api/models/apply', payload));
        // 把服务端认账的改动原样亮出来（"服务商 → 小米 MiMo；已换上小米的密钥"），
        // 切换成没成功一眼看得见，不再是干巴巴的"已生效"。
        const modeNote = /^没保存成/.test(mRes.textContent || '') ? '' : (mRes.textContent || '');
        mRes.textContent = rr.ok
          ? ((rr.changed && rr.changed.length) ? rr.changed.join('；') : (modeNote || '没有改动'))
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
        if (r.ok && b.dataset.p === '/api/history/clear') clearLocalDisplayRecordsForChannel(state.src, r.cleared_sessions);
        toast(r.ok ? b.dataset.t + '：已执行' : '失败：' + (r._err || ''));
      }));
    }

    async function videoApps() {
      ctx.topbar.querySelector('h1').textContent = '视频软件';
      const api = global.vigil && global.vigil.videoWindow;
      if (!api) {
        v.innerHTML = '<div class="card">视频窗口配置需要在 Windows 桌面客户端中管理。</div>';
        return;
      }
      let rows = [];
      let selectedId = '';
      let creating = false;
      const draw = async () => {
        rows = await api.profiles();
        if (!creating && !rows.some(row => row.id === selectedId)) selectedId = rows[0] ? rows[0].id : '';
        const current = rows.find(row => row.id === selectedId) || { id: '', name: '', exePath: '', args: '' };
        v.innerHTML = '<div class="card video-settings-card"><div class="video-settings-head"><div><b>已保存的软件</b><div class="muted">添加任意本地 Windows EXE，工作台只显示这里保存的软件。</div></div><button class="btn" id="vaNew">新建配置</button></div>' +
          (rows.length ? '<div class="video-settings-list">' + rows.map(row => '<button class="video-settings-row ' + (row.id === selectedId ? 'on' : '') + '" data-profile="' + esc(row.id) + '"><b>' + esc(row.name) + '</b><small>' + esc(row.exePath) + '</small></button>').join('') + '</div>' : '<div class="video-empty">还没有配置。选择“新建配置”添加软件。</div>') +
          '<div class="video-settings-form"><label>配置名称<input id="vaName" maxlength="80" placeholder="例如：剪映" value="' + esc(current.name) + '"></label>' +
          '<label>EXE 路径<div class="video-path-row"><input id="vaExe" readonly placeholder="选择本机 EXE" value="' + esc(current.exePath) + '"><button class="btn" id="vaPick">选择 EXE</button></div></label>' +
          '<details class="video-advanced"><summary>高级启动选项</summary><label>启动参数<input id="vaArgs" maxlength="2048" placeholder="可留空" value="' + esc(current.args || '') + '"></label></details>' +
          '<div class="video-actions"><button class="btn primary" id="vaSave">保存配置</button>' + (current.id ? '<button class="btn" id="vaDelete">删除配置</button>' : '') + '<span class="video-status" id="vaStatus" role="status"></span></div></div></div>';
        v.querySelectorAll('[data-profile]').forEach(button => button.addEventListener('click', () => { creating = false; selectedId = button.dataset.profile; draw(); }));
        const setMessage = message => { const target = v.querySelector('#vaStatus'); if (target) target.textContent = message; };
        v.querySelector('#vaNew').addEventListener('click', () => { creating = true; selectedId = ''; draw(); });
        v.querySelector('#vaPick').addEventListener('click', async () => {
          try {
            const file = await api.pickExe();
            if (file) { v.querySelector('#vaExe').value = file; if (!v.querySelector('#vaName').value) v.querySelector('#vaName').value = file.split(/[\\/]/).pop().replace(/\.exe$/i, ''); }
          } catch (error) { setMessage(error.message); }
        });
        v.querySelector('#vaSave').addEventListener('click', async () => {
          try {
            const exePath = v.querySelector('#vaExe').value;
            if (!exePath) { setMessage('请先选择 EXE 文件。'); return; }
            const saved = await api.saveProfile({ id: selectedId, name: v.querySelector('#vaName').value, exePath, args: v.querySelector('#vaArgs').value });
            const match = saved.find(row => row.exePath.toLowerCase() === exePath.toLowerCase() && row.name === v.querySelector('#vaName').value.trim());
            creating = false; selectedId = match ? match.id : selectedId;
            toast('软件配置已保存'); await draw();
          } catch (error) { setMessage(error.message); }
        });
        const remove = v.querySelector('#vaDelete');
        if (remove) remove.addEventListener('click', async () => {
          if (!await confirmDialog('删除此软件配置？')) return;
          await api.deleteProfile(selectedId); creating = false; selectedId = ''; await draw();
        });
      };
      await draw();
    }

    async function about() {
      ctx.topbar.querySelector('h1').textContent = '关于';
      v.innerHTML = '<div class="card">' +
        '<div class="kv"><span>客户端</span><b>v0.1.0（code <span id="abCode">…</span>）</b></div>' +
        '<div class="kv"><span>桌面框架</span><b>Electron ' + esc(String((global.process && process.versions && process.versions.electron) || '')) + '</b></div>' +
        '<div class="kv"><span>Brain 地址</span><b class="mono">' + esc(state.base || '—') + '</b></div>' +
        '<div class="kv"><span>渠道</span><b>' + esc(state.src) + '（本机 PC Agent）</b></div>' +
        '<div class="kv"><span>凭据</span><b>本机 Brain 自己的 token（不出机器）</b></div>' +
        '<div class="kv"><span>更新来源</span><b>GitHub Releases</b></div>' +
        '<div style="display:flex;gap:8px;align-items:center;margin-top:10px">' +
        '<button class="btn oline md" id="abCheck">检查更新</button>' +
        '<span class="muted" style="font-size:12px" id="abMsg">启动时和每小时也会自动检查（GitHub）</span>' +
        '</div>' +
        '<div class="muted" style="font-size:11.5px;margin-top:10px">本地操控已并入聊天页：确认在气泡里，权限/目录/工具在输入框旁的盾牌面板里，输入框下方的“仅授权目录”标签打开同一个面板。桌宠 Live2D（阶段5）未开始。</div>' +
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

    async function retrieval() {
      ctx.topbar.querySelector('h1').textContent = '检索与知识库';
      v.innerHTML = '<div class="card" role="status">正在读取检索设置…</div>';
      const r = pick(await get('/api/retrieval/settings'));
      if (!v.isConnected) return;
      if (!r.ok) {
        v.innerHTML = errBox('检索设置读取失败', r._err || '请检查本地 Brain 连接与版本') +
          '<button class="btn oline" type="button" id="retrievalRetry">重新加载</button>';
        v.querySelector('#retrievalRetry').addEventListener('click', () => { void retrieval(); });
        return;
      }
      let enabled = r.semantic_enabled !== false;
      v.innerHTML =
        '<div class="sec" style="margin-top:0">检索方式</div>' +
        '<div class="card">' +
        '<div class="row"><div class="lb"><b>语义检索</b>' +
        '<span>开启：BM25 + 向量 + RRF；关闭：只使用 BM25 关键词检索</span></div>' +
        '<button class="sw ' + (enabled ? 'on' : '') + '" id="retrievalSemantic" type="button" role="switch" aria-label="语义检索" aria-checked="' + String(enabled) + '"></button></div>' +
        '<div class="muted" style="font-size:12px;margin-top:10px">关闭后不调用嵌入模型、不执行 FAISS 检索，新导入的知识库内容仍可用 BM25 搜索。已有向量不会删除；重新开启后可按需重建向量索引。</div>' +
        '<div id="retrievalResult" role="status" aria-live="polite" class="muted" style="font-size:12px;margin-top:10px"></div>' +
        '</div>';
      const toggle = v.querySelector('#retrievalSemantic');
      const result = v.querySelector('#retrievalResult');
      toggle.addEventListener('click', async () => {
        if (toggle.disabled) return;
        const proposed = !enabled;
        toggle.disabled = true;
        result.textContent = '正在保存…';
        const next = pick(await post('/api/retrieval/settings', { semantic_enabled: proposed }));
        if (!v.isConnected) return;
        if (!next.ok || typeof next.semantic_enabled !== 'boolean') {
          result.textContent = '保存失败：' + (next._err || next.err || '请检查连接后重试');
          toggle.disabled = false;
          return;
        }
        enabled = next.semantic_enabled;
        toggle.classList.toggle('on', enabled);
        toggle.setAttribute('aria-checked', String(enabled));
        toggle.disabled = false;
        result.textContent = enabled ? '已启用混合检索' : '已切换为纯 BM25，无需重启 Brain';
      });
    }

    const draw = { conn, model, retrieval, 'video-apps': videoApps, danger, about };
    bindLi(ctx.panel, (k, n) => {
      modelRenderId++;
      ctx.panel.querySelectorAll('.li').forEach((x) => x.classList.toggle('on', x === n));
      draw[k] && draw[k]();
    });
    // 首启向导引导：app.js 检测到"本地内核在跑但还没配模型 key"时，
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
  // 顶部导航：01 工作台 · 插件市场 · 设置 · 02 对话与人格 · 03 能力与扩展 · 04 连接与系统 · 05 自动化任务 · 统一管理 · 数据。
  // 原有各页面**一个不少**，按功能归入分组，作为组内功能项
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
    ] },
    { id: 'system', name: '连接与系统', num: '04', icon: 'set', items: [
      { k: 'settings', num: '041', name: '连接与设备', path: '/connection', icon: 'set', render: renderSettings },
    ] },
    { id: 'auto', name: '自动化任务', num: '05', icon: 'activity', items: [
      { k: 'life', num: '051', name: '生活面板', path: '/life', icon: 'heart', render: renderLife },
    ] },
  ];

  function makeGroupPage(group) {
    return async function (ctx) {
      const items = group.items;
      let cur = items.find((it) => it.k === state.settingsGroup) ? state.settingsGroup : items[0].k;
      state.settingsGroup = '';

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

  async function renderSettingsHome(ctx) {
    ctx.panel.innerHTML = '';
    ctx.topbar.className = 'topbar settings-home-topbar';
    ctx.topbar.innerHTML = '<h1>设置</h1><div class="sp"></div><button class="btn oline" id="settingsBackChat">返回聊天</button>';
    ctx.topbar.querySelector('#settingsBackChat').addEventListener('click', () => {
      state.settingsDetail = false;
      ctx.navigate('chat');
    });
    const cards = [
      {title:'自动化任务', desc:'打开生活面板与任务入口', icon:'activity', page:'auto', group:'life'},
      {title:'工作区', desc:'节点、运行情况与管理工具', icon:'mon', page:'management'},
      {title:'本地内核', desc:'随包 Brain 状态、连接与权限', icon:'set', page:'system', sub:'conn'},
      {title:'模型与推理', desc:'模型服务商、推理与快速切换', icon:'brain', page:'system', sub:'model'},
      {title:'检索与知识库', desc:'语义检索开关、BM25 与混合检索', icon:'search', page:'system', sub:'retrieval'},
      {title:'视频软件', desc:'管理视频工作台中的本地软件', icon:'video', page:'system', sub:'video-apps'},
      {title:'日志 / 调试', desc:'查看运行数据和诊断信息', icon:'chart', page:'data'},
      {title:'关于', desc:'版本、更新与项目信息', icon:'set', page:'system', sub:'about'},
    ];
    ctx.view.className = 'scroll settings-home-view';
    ctx.view.innerHTML = '<div class="settings-home"><div class="settings-intro"><span class="eyebrow">VIGIL</span><h2>设置</h2><p>常用功能与应用配置</p></div><div class="settings-grid">' +
      cards.map((c,i) => '<button class="settings-tile" data-setting-index="' + i + '"><span class="settings-tile-icon">' + icon(c.icon,'md') + '</span><span><b>' + esc(c.title) + '</b><small>' + esc(c.desc) + '</small></span><span class="settings-tile-arrow">›</span></button>').join('') +
      '</div></div>';
    ctx.view.querySelectorAll('[data-setting-index]').forEach((b) => b.addEventListener('click', () => {
      const item = cards[Number(b.dataset.settingIndex)];
      state.settingsDetail = true;
      state.settingsGroup = item.group || '';
      state.settingsSub = item.sub || '';
      ctx.navigate(item.page);
    }));
  }

  // ═══ 统一管理 ═══════════════════════════════════════════════
  let mgmtTimer = null;
  async function renderManagement(ctx) {
    ctx.topbar.className = 'topbar';
    ctx.view.className = 'scroll';
    ctx.panel.innerHTML = '<div class="phead"><h2>统一管理</h2></div>' +
      '<div class="plist">' +
      li({ icon: 'mon', title: '节点概览', sub: '所有服务器与本地实例', on: true, attrs: ' data-k="ov"' }) +
      '</div>';
    ctx.topbar.innerHTML = topbar('节点概览', '',
      '<button class="btn oline" id="mgRe">' + icon('refresh', 'sm') + '刷新</button>');
    ctx.view.innerHTML = '<div class="wrap" id="mgView"><div class="muted" style="padding:20px 0">加载中…</div></div>';
    const v = ctx.view.querySelector('#mgView');
    const CB = { timeoutMs: 8000 };   // 统一管理数据在本机 Brain；失败时尽快转为明确错误状态

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
          (r && (r._err || (r._status ? 'HTTP ' + r._status : ''))) || '请确认内核已启动且管理模块已加载') + '</div>';
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
          '<div class="muted" style="font-size:12px;margin-top:6px">被管理节点配好 VIGIL_MANAGEMENT_* 环境变量并启动 management_agent 后会自动出现在这里</div>' +
          '</div>');
    }

    const draw = (k) => { ov(); };
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
    const mf = fs.find((f) => f.name.toLowerCase() === 'skill.json' || f.name.toLowerCase().endsWith('/zxplugin.json') || f.name.toLowerCase() === 'zxplugin.json');
    if (mf) {
      try {
        const j = JSON.parse(mf.content || '{}');
        if (d.kind === 'skill' && j.id) return String(j.id);
        if (d.kind === 'plugin' && j.name) return String(j.name);
      } catch (_) { /* validation result carries malformed manifest details */ }
    }
    const py = fs.find((f) => /\.py$/i.test(f.name));
    if (py && /\/__init__\.py$/i.test(py.name)) return py.name.split('/')[0];
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
        const bytes = (() => { try { return new TextEncoder().encode(content).length; } catch (_) { return content.length; } })();
        const sizeText = (bytes / 1024).toFixed(1) + ' KB · ' + lines.length + ' 行';
        return '<div class="card cr-file-card" style="margin-top:8px">' +
          '<div class="cr-file-head">' +
            '<div class="cr-file-title">' +
              icon('clip', 'sm') + ' <strong>' + esc(f.name) + '</strong>' +
              '<span class="muted cr-file-size">' + esc(sizeText) + '</span>' +
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
        const layout = String(installResult.layout || ((d.files || []).some((f) => /\/__init__\.py$/i.test(f.name)) ? 'directory' : 'single'));
        const packageLabel = layout === 'directory' ? '目录插件包' : '单文件插件';
        const frontendAssets = installResult.frontend_assets === true || (d.files || []).some((f) => /\/frontend\.(js|css)$/i.test(f.name));
        if (isPlugin && restartNeeded) {
          installStatus = '安装成功：' + installedName + '（' + packageLabel + '）已写入 Brain 插件目录。当前 Brain 尚未加载新代码；重启 Brain 后，Agent 才能调用 TOOLS / TOOL_SPECS。' + (frontendAssets ? '插件界面资源也已安装，客户端会在 Brain 重启后检测并加载。' : '') + '审批已消费，不能重复使用。';
        } else if (isPlugin) {
          installStatus = '安装成功：' + installedName + '（' + packageLabel + '）已写入 Brain 插件目录。请到插件管理页确认运行状态和工具清单，再在聊天中描述要执行的任务。审批已消费，不能重复使用。';
        } else if (restartNeeded) {
          installStatus = '安装成功：' + installedName + ' 已写入技能目录，但需要重启技能宿主后才能完全生效。审批已消费，不能重复使用。';
        } else {
          installStatus = '安装成功：' + installedName + ' 已写入技能目录。请到技能管理页确认宿主已加载，再在聊天中描述要执行的任务。审批已消费，不能重复使用。';
        }
        if (approvalState) approvalState.textContent = installStatus;
        installButton.textContent = restartNeeded ? '已安装 · 待重启' : '已安装';
        installButton.disabled = true;
        // Refresh the extension view and check the persisted server state even
        // when installation was started from chat. A failed refresh must not
        // hide the already committed installation result.
        if (typeof window.__vigilRefreshExtensions === 'function') window.__vigilRefreshExtensions();
        if (isPlugin) {
          try {
            const state = pick(await API.get('/api/plugins'));
            if (!state.ok) throw new Error(creatorActionError(state, '插件状态刷新失败'));
            const loaded = (state.plugins || []).some((p) => p.name === installedName);
            if (state.pending_restart && state.pending_restart.pending) {
              installStatus += ' 已确认磁盘安装完成，插件状态显示待重启 Brain。';
            } else if (!loaded) {
              installStatus += ' 安装事务已完成，但当前插件清单尚未确认加载；请检查插件管理页和 Brain 日志。';
            } else {
              installStatus += ' 已从当前插件清单确认。';
            }
          } catch (error) {
            installStatus += ' 插件状态暂时无法刷新：' + String(error && error.message || error);
          }
          if (approvalState) approvalState.textContent = installStatus;
        }
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


  const renderPluginMarket = (ctx) => global.VigilRenderPluginMarket(ctx, confirmDialog);


  global.PAGES = [
    { id: 'chat', name: '工作台', icon: 'chat', render: renderChat },
    { id: 'video-workbench', name: '视频工作台', icon: 'video', render: global.VigilRenderVideoWorkbench },
    // 插件市场升为一级入口：装插件是高频动作，不该藏在能力与扩展的子页里。
    { id: 'market', name: '插件市场', icon: 'puzzle', render: renderPluginMarket },
    { id: 'settings', name: '设置', icon: 'set', render: renderSettingsHome },
    ...FUNC_GROUPS.map((g) => ({ id: g.id, name: g.name, icon: g.icon, render: makeGroupPage(g) })),
    { id: 'management', name: '统一管理', icon: 'mon', render: renderManagement },
    { id: 'data', name: '数据', icon: 'chart', render: renderData },
  ];
})(window);
