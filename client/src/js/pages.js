// -*- coding: utf-8 -*-
/**
 * å…«ä¸ªé¡µé¢ã€‚
 *
 * ä¸€æ¡è§„çŸ©ï¼š**åç«¯æ²¡æœ‰çš„èƒ½åŠ›å°±å¦‚å®å†™å‡ºæ¥**ï¼ˆä¾‹å¦‚è®°å¿†åˆ—è¡¨ç°åœ¨æ²¡æœ‰æ¥å£ï¼‰ï¼Œ
 * ä¸è¦æ‘†ä¸€ä¸ªæ°¸è¿œä¸ºç©ºçš„å‡åˆ—è¡¨ â€”â€” é‚£ç§"çœ‹ç€æœ‰ã€å…¶å®ä¸å·¥ä½œ"æœ€å®¹æ˜“è¯¯å¯¼äººã€‚
 *
 * æ¯ä¸ªé¡µé¢æ˜¯ { id, name, tip, icon, subs?, render(ctx) }ï¼š
 *   ctx = { view, topbar, panel, src, refresh }
 * render è‡ªå·±è´Ÿè´£å¡« topbar / panel / view ä¸‰å—ã€‚
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
        '<h3 id="vigilConfirmTitle">ç¡®è®¤æ“ä½œ</h3><p></p><div class="vigil-confirm-actions">' +
        '<button type="button" class="btn" data-choice="cancel">å–æ¶ˆ</button>' +
        '<button type="button" class="btn fill" data-choice="ok">ç»§ç»­</button></div></section>';
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
      overlay.innerHTML = '<section class="vigil-confirm" role="dialog" aria-modal="true"><h3>ç¼–è¾‘å†…å®¹</h3><p></p><textarea class="input" rows="5"></textarea><div class="vigil-confirm-actions"><button type="button" class="btn" data-choice="cancel">å–æ¶ˆ</button><button type="button" class="btn fill" data-choice="ok">ä¿å­˜</button></div></section>';
      overlay.querySelector('p').textContent = String(message || '');
      const input = overlay.querySelector('textarea'); input.value = String(initialValue || '');
      const finish = (value) => { overlay.remove(); resolve(value); };
      overlay.querySelector('[data-choice="cancel"]').onclick = () => finish(null);
      overlay.querySelector('[data-choice="ok"]').onclick = () => finish(input.value);
      overlay.addEventListener('click', (e) => { if (e.target === overlay) finish(null); });
      document.body.appendChild(overlay); input.focus();
    });
  }

  // â”€â”€ é€šç”¨é›¶ä»¶ â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const UI_RECORD_LIMITS = { count: 100, chars: 16384, days: 30 };
  function recordKey() {
    const cfg = state.cfg || {};
    return 'vigil.display.v1:' + JSON.stringify([state.base || cfg.base || '', cfg.device_id || '',
      cfg.token_kind || '', state.src || 'pc', state.chatConversationId || 'welcome']);
  }
  async function loadRecordScope() {
    try { await API.loadCfg(); return true; }
    catch (error) { state.recordScopeError = String(error && error.message || 'æ— æ³•è¯»å–è¿æ¥èº«ä»½'); toast('æ— æ³•è¯»å–å½“å‰è¿æ¥èº«ä»½ï¼Œæš‚ä¸åŠ è½½æˆ–æäº¤ä¼šè¯è®°å½•'); return false; }
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
    } catch (_) { toast('å½“å‰è®°å½•æœªèƒ½ä¿å­˜åˆ°æœ¬æœºï¼Œè¯·å…ˆå¤åˆ¶å†…å®¹'); }
  }
  function streamBodyHtml(thinkId, textId) {
    return '<div class="think-block streaming"><button class="think-toggle">' +
      icon('brain', 'sm') + 'æ€è€ƒ <span class="think-caret">â–´</span></button>' +
      '<div class="think-content" id="' + thinkId + '"><div class="think-text" id="' + thinkId + '_inner"></div></div></div>' +
      '<div class="txt md streaming-text" id="' + textId + '" style="min-height:24px"></div>' +
      '<div class="foot"><span class="stream-status">æ­£åœ¨ç”Ÿæˆâ€¦</span></div>';
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
    const stages = {model_request:'æ¨¡å‹è¯·æ±‚',network:'ç½‘ç»œè¿æ¥',auth:'è®¤è¯',parse:'JSON è§£æ',
      codegen:'äº§ç‰©æ„é€ ',static_validation:'é™æ€æ ¡éªŒ',draft_save:'è‰ç¨¿ä¿å­˜'};
    return [e.message || 'è¯·æ±‚æœªå®Œæˆ', e.stage ? 'å¤±è´¥é˜¶æ®µï¼š' + (stages[e.stage] || e.stage) : '',
      e.error_code || e.code ? 'é”™è¯¯ç ï¼š' + (e.error_code || e.code) : '',
      e.http_status ? 'HTTPï¼š' + e.http_status : '', e.hint ? 'å»ºè®®ï¼š' + e.hint : '',
      e.target ? 'å½“å‰è¿æ¥ï¼š' + e.target : '',
      e.log_id ? 'æ—¥å¿— IDï¼š' + e.log_id : ''].filter(Boolean).join('ï¼›');
  }

  function creatorRequestFailure(r) {
    const status = Number(r && r._status) || 0;
    const confirmed = status >= 400 && status < 500 && status !== 408;
    return {status: confirmed ? 'failed' : 'pending', error: {
      message: status === 404 ? 'å½“å‰ Brain æ²¡æœ‰ Creator jobs æ¥å£ï¼Œæœªå¼€å§‹ç”Ÿæˆ'
        : confirmed ? 'Creator è¯·æ±‚è¢«æ‹’ç»ï¼Œæœªå¼€å§‹ç”Ÿæˆ' : 'è¯·æ±‚æœªç¡®è®¤å®Œæˆï¼Œæœªè‡ªåŠ¨é‡å‘',
      code: status === 404 ? 'creator_api_missing' : confirmed ? 'creator_request_rejected' : 'creator_request_unconfirmed',
      http_status: status || undefined, target: (r && r._target) || '',
      hint: status === 404 ? 'æ£€æŸ¥ Electron ä¸ Brain ç‰ˆæœ¬ã€sidecar å¯åŠ¨è·¯å¾„æ˜¯å¦åŒ¹é…'
        : confirmed ? 'æ£€æŸ¥è®¤è¯å’Œè¯·æ±‚å‚æ•°åæ‰‹åŠ¨é‡è¯•' : 'æ£€æŸ¥è¿æ¥å’Œå·²æœ‰ä½œä¸šï¼Œç¡®è®¤åå†æ‰‹åŠ¨é‡è¯•',
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
        card.setCancel({label: 'æ­£åœ¨å–æ¶ˆâ€¦', disabled: true});
      } else if (cancelState === 'failed') {
        card.setCancel({label: 'é‡è¯•å–æ¶ˆ', disabled: false});
      }
    }
    async function requestCancel() {
      if (cancelState === 'sending' || cancelState === 'waiting' || finished) return;
      cancelState = 'sending';
      card.setCancel({label: 'æ­£åœ¨å–æ¶ˆâ€¦', disabled: true});
      card.note('å·²å‘é€å–æ¶ˆè¯·æ±‚ï¼Œç­‰å¾…æ¨¡å‹è¿æ¥å…³é—­â€¦', 'warn');
      card.log('è¯·æ±‚å–æ¶ˆï¼ˆåç«¯ç¡®è®¤å‰ä¸ä¼šæ˜¾ç¤ºå·²å–æ¶ˆï¼‰');
      let r;
      try { r = pick(await post('/api/creator/jobs/' + jid + '/cancel', {}, {timeoutMs: 8000})); }
      catch (e) { r = {ok:false, _err:String((e && e.message) || e)}; }
      if (r && (r._status === 409 || (r.cancel_state === 'too_late'))) {
        cancelState = 'idle';
        card.setCancel({hidden: true});
        card.note('ä»»åŠ¡å·²ç»ç»“æŸï¼Œæ— æ³•å–æ¶ˆï¼š' + (r.err || 'ç»“æœä»¥ä¸‹æ–¹çŠ¶æ€ä¸ºå‡†'), 'err');
        toast('ä»»åŠ¡å·²ç»“æŸï¼Œæ— æ³•å–æ¶ˆ');
        applySnapshot(Object.assign({}, current, r.job_id ? r : {}));
        return;
      }
      if (!r || !r.ok) {
        cancelState = 'failed';
        const reason = (r && (r._err || r.err)) || 'åç«¯æœªç¡®è®¤';
        card.note('å–æ¶ˆå¤±è´¥ï¼š' + reason + 'ã€‚ä»»åŠ¡å¯èƒ½ä»åœ¨è¿è¡Œï¼Œå¯é‡è¯•å–æ¶ˆã€‚', 'err');
        card.setCancel({label: 'é‡è¯•å–æ¶ˆ', disabled: false});
        toast('å–æ¶ˆå¤±è´¥ï¼š' + reason);
        card.log('å–æ¶ˆå¤±è´¥ï¼š' + reason);
        return;
      }
      card.log('åç«¯å·²å—ç†ï¼š' + (r.status || ''));
      applySnapshot(r);
      if (r.status !== 'cancelled') {
        cancelState = 'waiting';
        card.setCancel({label: 'æ­£åœ¨å–æ¶ˆâ€¦', disabled: true});
        card.note('å–æ¶ˆè¯·æ±‚å·²å—ç†ï¼Œæ­£åœ¨å…³é—­æ¨¡å‹è¿æ¥â€¦', 'warn');
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
        card.log('å¤±è´¥ï¼š' + creatorErrorText(p));
        applySnapshot(Object.assign({}, current, {status:'failed', error:p, event_cursor:cursor}));
        return;
      }
      if (evt.type === 'stage') { card.update(current, p.stage); card.log('é˜¶æ®µï¼š' + creatorStageLabel(p.stage)); }
      if (evt.type === 'text_delta') card.bytes(current);
      if (evt.type === 'usage' && p && typeof p === 'object') card.log('ç”¨é‡ï¼š' + JSON.stringify(p).slice(0, 120));
    }) : null;
    applySnapshot(initial);
    try {
      while (!finished && root.isConnected && recordKey() === scope) {
        const hasStream = global.vigil && global.vigil.apiStream && (global.vigil.onStreamEvent || global.vigil.onCreatorEvent);
        if (hasStream) {
          const sr = await global.vigil.apiStream('/api/creator/jobs/' + jid + '/events?after=' + cursor, null);
          if (sr && sr.ok === false) { card.note('ä¸åç«¯çš„å®æ—¶è¿æ¥ä¸­æ–­ï¼ŒçŠ¶æ€æœªç¡®è®¤ï¼›å¯é‡è¯•è¿æ¥', 'err'); }
        } else await new Promise(resolve => setTimeout(resolve, 500));
        if (!root.isConnected || recordKey() !== scope) break;
        const r = pick(await get('/api/creator/jobs/' + jid));
        if (!r.ok) {
          lost = true;
          card.note('è¿æ¥ä¸­æ–­ï¼Œè®°å½•å·²ä¿ç•™åœ¨åå°ï¼›å¯é‡è¯•è¿æ¥æŸ¥è¯¢çœŸå®çŠ¶æ€', 'err');
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
      card.note('è‰ç¨¿å·²ç”Ÿæˆï¼Œä½†å½“å‰è¯»ä¸åˆ°è‰ç¨¿æ–‡ä»¶ï¼ˆå¯èƒ½å·²è¢«åˆ é™¤ï¼‰ï¼š' + (r._err || r.err || ''), 'err');
      return;
    }
    root.innerHTML = '<div class="bdy">' + creatorTaskCardHtml(job) + '</div>';
    const card = creatorCard(root, job);
    card.update(job);
    card.log('å·²ä»åç«¯æ¢å¤è¯¥ä»»åŠ¡çš„çŠ¶æ€');
  }

  const CREATOR_PHASES = [
    ['queued', 'ç­‰å¾…æ¨¡å‹'], ['generating', 'ç”Ÿæˆä¸­'], ['validating', 'éªŒè¯ä¸­'], ['done', 'å®Œæˆ'],
  ];
  function creatorStageLabel(stage) {
    return ({queued:'ç­‰å¾…æ¨¡å‹', generating:'ç”Ÿæˆä¸­', validating:'éªŒè¯ä¸­', assembling:'æ•´ç†äº§ç‰©',
      cancelling:'æ­£åœ¨å–æ¶ˆ', cancelled:'å·²å–æ¶ˆ', failed:'å¤±è´¥', succeeded:'å·²å®Œæˆ'})[stage] || String(stage || '');
  }
  function creatorPhase(job) {
    const s = String((job && job.status) || 'queued');
    const stage = String((job && job.stage) || '');
    if (s === 'queued') return {key:'queued', step:0, label:'ç­‰å¾…æ¨¡å‹', tone:'idle'};
    if (s === 'cancelling') return {key:'cancelling', step:1, label:'æ­£åœ¨å–æ¶ˆ', tone:'warn'};
    if (s === 'cancelled') return {key:'cancelled', step:1, label:'å·²å–æ¶ˆ', tone:'muted'};
    if (s === 'interrupted') return {key:'interrupted', step:1, label:'å·²ä¸­æ–­', tone:'muted'};
    if (s === 'failed') return {key:'failed', step:2, label:'å¤±è´¥', tone:'err'};
    if (s === 'succeeded') return {key:'succeeded', step:3, label:'å®Œæˆ', tone:'ok'};
    if (stage === 'validating' || stage === 'assembling') return {key:'validating', step:2, label:'éªŒè¯ä¸­', tone:'run'};
    return {key:'generating', step:1, label:'ç”Ÿæˆä¸­', tone:'run'};
  }
  function creatorNeed(job) {
    const text = String((job && (job.requirement || job.feedback)) || '').replace(/\s+/g, ' ').trim();
    return text.length > 72 ? text.slice(0, 72) + 'â€¦' : (text || 'æœªå¡«å†™éœ€æ±‚');
  }
  function creatorTaskCardHtml(job) {
    const phase = creatorPhase(job);
    return '<div class="card cr-task" data-cr-task="' + esc(job.job_id || '') + '">' +
      '<div class="cr-task-top">' +
        '<span class="tag neutral">' + esc(job.kind === 'skill' ? 'æŠ€èƒ½' : 'æ’ä»¶') + '</span>' +
        '<div class="cr-task-title"><b>' + esc(creatorNeed(job)) + '</b>' +
          '<span class="muted mono">' + esc(String(job.job_id || '').slice(0, 18)) + '</span></div>' +
        '<span class="cr-phase ' + phase.tone + '" data-cr-phase><i></i><span></span></span>' +
      '</div>' +
      '<div class="cr-rail">' + CREATOR_PHASES.map(([key, label], i) =>
        '<span class="cr-step" data-step="' + i + '">' + esc(label) + '</span>').join('') + '</div>' +
      '<div class="cr-task-note" data-cr-note role="status"></div>' +
      '<div class="cr-task-foot">' +
        '<span class="muted cr-meter" data-cr-meter>å·²ç”¨æ—¶ 0 ç§’</span>' +
        '<span class="sp"></span>' +
        '<button class="btn ghost cr-retry" type="button" data-cr-retry hidden>é‡è¯•è¿æ¥</button>' +
        '<button class="btn ghost cr-log-toggle" type="button" data-cr-log-toggle aria-expanded="false">çŠ¶æ€æ—¥å¿—</button>' +
        '<button class="btn oline cr-cancel" type="button" data-cr-cancel>å–æ¶ˆç”Ÿæˆ</button>' +
      '</div>' +
      '<div class="cr-log" data-cr-log hidden><div class="cr-log-scroll"><ol class="cr-log-list"></ol></div></div>' +
      '</div>';
  }
  /** Bind the live task card. Only progress facts are rendered â€” never the model text. */
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
      if (meterEl) meterEl.textContent = 'å·²ç”¨æ—¶ ' + seconds + ' ç§’ Â· æ¨¡å‹è¿”å› ' + kb + ' KBï¼ˆä¸åœ¨æ­¤å¤„å±•ç¤ºæºç ï¼‰' +
        (stopped ? '' : ' Â· å®æ—¶æ›´æ–°');
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
      // or a restart can never offer "å–æ¶ˆ" on a job that already ended.
      if (['succeeded', 'failed', 'cancelled', 'interrupted'].includes(status)) {
        setCancel({hidden: true});
      } else if (status === 'cancelling') {
        setCancel({label: 'æ­£åœ¨å–æ¶ˆâ€¦', disabled: true});
      }
      if (item && item.error) note(creatorErrorText(item.error), 'err');
      else if (item && item.status === 'cancelled') note(item.cancel_note || 'å·²å–æ¶ˆï¼Œæœªç”Ÿæˆä»»ä½•è‰ç¨¿', 'muted');
      else if (item && item.status === 'interrupted') note('æ‰§è¡Œå·²ä¸­æ–­ï¼Œæœªè‡ªåŠ¨é‡æ–°ç”Ÿæˆ', 'muted');
      else if (item && item.status === 'cancelling') note('æ­£åœ¨å…³é—­æ¨¡å‹è¿æ¥â€¦', 'warn');
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
      cancelEl.textContent = o.label || 'å–æ¶ˆç”Ÿæˆ';
    }
    function setRetry(show) { if (retryEl) retryEl.hidden = !show; }
    if (logToggle && logEl) logToggle.onclick = () => {
      const open = logEl.hidden;
      logEl.hidden = !open;
      logToggle.setAttribute('aria-expanded', String(open));
      logToggle.textContent = open ? 'æ”¶èµ·æ—¥å¿—' : 'çŠ¶æ€æ—¥å¿—';
    };
    if (cancelEl) cancelEl.onclick = (e) => { e.preventDefault(); if (handler) handler(); };
    if (retryEl) retryEl.onclick = () => {
      if (!root.isConnected) return;
      setRetry(false);
      void watchCreatorJob(root, currentJob, root.dataset.scope || recordKey());
    };
    let currentJob = initial;
    log('ä»»åŠ¡å·²æäº¤ï¼Œç­‰å¾…æ¨¡å‹');
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
  /** æ—¶é—´æˆ³å½’ä¸€æˆæ¯«ç§’ï¼šç§’ / æ¯«ç§’ / å­—ç¬¦ä¸²éƒ½èƒ½åƒï¼ˆä¸ ui.js fmtTime åŒä¸€å¥—è§„åˆ™ï¼‰ */
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
      '<p>ä¸æ›´å¼ºå¤§çš„ AIï¼Œä¸€èµ·æ¢ç´¢æ— é™å¯èƒ½</p>';
  }

  /** å·¦æ ï¼šåˆ†ç»„æ ‡é¢˜ + åˆ—è¡¨é¡¹ */
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

  // â•â•â• æ¨¡å‹å¿«é€Ÿåˆ‡æ¢ï¼ˆä¸»é¡µ composer æ—çš„æ¨¡å‹åï¼‰â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
  //
  // äº§å“æ„å›¾ï¼šåœ¨è®¾ç½®é‡Œé€‰è¿‡ä¸€æ¬¡çš„æ¨¡å‹ï¼Œä¹‹ååœ¨èŠå¤©æ¡†æ—è¾¹å°±è¯¥ç‚¹å¾—åˆ°ï¼Œ
  // ä¸ç”¨æ¯æ¬¡éƒ½è·‘ä¸€è¶Ÿè®¾ç½®é¡µã€‚
  //
  // æ•°æ®ä¸å†™å…¥éƒ½å¤ç”¨ç°æœ‰æœºåˆ¶ï¼Œ**æ²¡æœ‰ç¬¬äºŒä»½æ¨¡å‹é…ç½®**ï¼š
  //   - è¯»ï¼šGET /api/models/saved   â€”â€” æœåŠ¡ç«¯ä» config çš„ saved_models æ´¾ç”Ÿçš„æ¸…å•ï¼Œ
  //                                    åªå›åå­—å’ŒæœåŠ¡å•†ï¼Œä¸å«ä»»ä½•å¯†é’¥ï¼›
  //   - å†™ï¼šPOST /api/models/quick-switch â€”â€” æœåŠ¡ç«¯æœ€ç»ˆä»èµ° model_hub.apply_cfg()
  //                                    å†™å›åŒä¸€ä»½é…ç½®ï¼ˆä¸è®¾ç½®é¡µã€Œä¿å­˜å¹¶ç”Ÿæ•ˆã€åŒä¸€æ¡è·¯ï¼‰ã€‚
  //
  // åˆ‡æ¢å¤±è´¥ä¸€å¾‹**æ˜è¯´**ï¼ˆç¼ºå¯†é’¥ / è¢«æœåŠ¡å•†æ‹’ç» / æ²¡èƒ½éªŒè¯ï¼‰ï¼Œ
  // ç»ä¸é™é»˜æ¢æˆåˆ«çš„æ¨¡å‹ â€”â€” é‚£ä¼šè®©äººæ‹¿ç€ B çš„å›å¤ä»¥ä¸ºåœ¨ç”¨ Aã€‚
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

    const nameOf = (m) => m.name || String(m.model || '').split('/').pop() || m.model || 'ï¼ˆæœªå‘½åï¼‰';
    const provOf = (m) => m.provider_name || m.provider || '';
    // ğŸ”´ é¡¶æ é‚£é¢—æ¨¡å‹åä¸åœ¨ view é‡Œï¼ˆåœ¨ ctx.topbar é‡Œï¼‰â€”â€” ç”¨ view.querySelector
    // æ‹¿åˆ°çš„æ°¸è¿œæ˜¯ nullï¼Œè¡¨ç°å°±æ˜¯"composer ä¸Šå†™ç€ Aã€é¡¶æ è¿˜å†™ç€ B"è¿™ç§è‡ªç›¸çŸ›ç›¾ã€‚
    const topEl = () => ((ctx && ctx.topbar) || view).querySelector('.chat-model');
    function say(text, bad) {
      if (!msgEl) return;
      msgEl.textContent = text || '';
      msgEl.style.color = bad ? 'var(--err)' : '';
      msgEl.title = text || '';
    }
    // å½“å‰æ¨¡å‹åç§°åŒæ­¥åˆ° composer å’Œé¡¶æ ï¼›åˆ—è¡¨è¡Œçš„å‹¾é€‰ç”±æœåŠ¡ç«¯ active çŠ¶æ€ç»˜åˆ¶ã€‚
    function label() {
      const c = data.current || {};
      btn.textContent = (c.name || c.model || 'æœªé…ç½®') + ' â–¾';
      btn.title = c.model
        ? ('å½“å‰æ¨¡å‹ï¼š' + c.model + 'ï¼ˆ' + (c.provider_name || '?') + 'ï¼‰Â· ç‚¹ä¸€ä¸‹åˆ‡æ¢')
        : 'è¿˜æ²¡æœ‰å¯ç”¨æ¨¡å‹ï¼šç‚¹å¼€çœ‹çœ‹ï¼Œæˆ–å»è®¾ç½®é‡Œé…ä¸€ä¸ª';
      const top = topEl();
      if (top && c.model) { top.textContent = c.model; top.title = c.model; }
    }
    function drawDetails() {
      const policy = data.context_window;
      const value = view.querySelector('#mqContextValue');
      const note = view.querySelector('#mqContextNote');
      if (value) value.textContent = policy ? policy.label : 'è¯»å–ä¸­â€¦';
      if (contextToggle) contextToggle.disabled = !policy || switching;
      if (note) note.textContent = !policy ? 'å½“å‰æ— æ³•è¯»å–ä¸Šä¸‹æ–‡çª—å£è®¾ç½®' : policy.limited
        ? 'å·²æŒ‰æ¨¡å‹å®¹é‡è°ƒæ•´ä¸º ' + policy.label + 'ã€‚'
        : 'Vigil æŒ‰æ­¤ç›®æ ‡ç®¡ç†å½“å‰è¯·æ±‚ï¼›æœåŠ¡å•†å¯èƒ½å¦æœ‰é™åˆ¶ã€‚';
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
      if (switching || (h.isBusy && h.isBusy())) { say('è¯·ç­‰å½“å‰å›å¤å®Œæˆå†è°ƒæ•´çª—å£', true); return; }
      switching = true; drawDetails(); say('æ­£åœ¨ä¿å­˜ä¸Šä¸‹æ–‡çª—å£â€¦');
      try {
        const r = pick(await post('/api/models/context', { tier }));
        if (!r || !r.ok) { say('ä¿å­˜å¤±è´¥ï¼š' + ((r && (r.err || r._err)) || 'æœªçŸ¥é”™è¯¯'), true); return; }
        data.context_window = r.context_window;
        say('å·²ä¿å­˜ Â· å®é™…ç”Ÿæ•ˆï¼š' + r.context_window.label);
      } catch (_) { say('ä¸Šä¸‹æ–‡çª—å£ä¿å­˜å¤±è´¥ï¼Œè¯·é‡è¯•', true); }
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
          + (all.length ? 'æ²¡æœ‰åŒ¹é…çš„æ¨¡å‹' : 'è¿˜æ²¡æœ‰ä¿å­˜è¿‡æ¨¡å‹ï¼šåœ¨è®¾ç½®é‡Œé€‰ä¸€æ¬¡ï¼Œè¿™é‡Œå°±æœ‰äº†') + '</div>';
        return;
      }
      listEl.innerHTML = rows.map((m) => {
        const i = all.indexOf(m);
        // è¿æ¥çŠ¶æ€åªç”¨æœåŠ¡ç«¯ç»™çš„äº‹å®ï¼šè¿™å®¶å­˜æ²¡å­˜è¿‡å¯†é’¥ã€‚
        // çœŸæ­£èƒ½ä¸èƒ½è¿é€šç•™åˆ°åˆ‡æ¢æ—¶æ¢ï¼ˆæ¢ä¸é€šä¼šæ˜è¯´ï¼Œä¸åœ¨è¿™å„¿ç¼–ï¼‰ã€‚
        const status = !m.has_key && m.provider !== 'custom'
          ? '<span class="mq-status">ç¼ºå°‘å¯†é’¥</span>' : '';
        return '<div class="mq-item"><button class="mqrow' + (m.active ? ' on' : '') + '" type="button" data-i="' + i +
          '" aria-pressed="' + String(!!m.active) + '">' +
          '<span class="mq-check" aria-hidden="true">' + (m.active ? icon('check', 'sm') : '') + '</span>' +
          '<span class="lb">' +
          '<b>' + esc(nameOf(m)) + (m.unsaved ? ' <span class="muted">(æœªä¿å­˜)</span>' : '') + '</b>' +
          '<span class="muted">' + esc(provOf(m)) + ' Â· ' + esc(m.model) + '</span>' +
          '</span>' + status +
          '</button>' + (m.unsaved ? '' : '<button class="mq-x" type="button" data-x="' + i +
            '" title="ä»æ¸…å•é‡Œç§»é™¤ï¼ˆä¸å½±å“å½“å‰è®¾ç½®ï¼‰" aria-label="ä»æ¸…å•é‡Œç§»é™¤ ' + esc(nameOf(m)) + '">Ã—</button>') + '</div>';
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
    // ä»æ¸…å•ç§»é™¤ä¸€æ¡ï¼ˆéœ€æ±‚ï¼šåˆ æ‰æ¨¡å‹åä¸‹æ‹‰è¦è·Ÿç€å˜ï¼‰ã€‚åªåŠ¨æ¸…å•ä¸åŠ¨å½“å‰é…ç½®ï¼Œ
    // æ‰€ä»¥æ­£åœ¨ç”¨çš„é‚£ä¸ªæ¨¡å‹ä¸ä¼šå› ä¸º"ä»åˆ—è¡¨é‡Œåˆ æ‰"å°±æ‚„æ‚„è¢«æ¢èµ°ã€‚
    async function forget(m) {
      if (!m) return;
      if (!await confirmDialog('ä»æ¨¡å‹æ¸…å•é‡Œç§»é™¤ã€Œ' + nameOf(m) + 'ã€ï¼Ÿåªå½±å“è¿™ä¸ªä¸‹æ‹‰åˆ—è¡¨ï¼Œ'
        + 'å½“å‰è®¾ç½®å’ŒèŠå¤©è®°å½•éƒ½ä¸åŠ¨ã€‚')) return;
      say('æ­£åœ¨ç§»é™¤â€¦');
      try {
        const r = pick(await post('/api/models/forget',
          { provider: m.provider, base: m.base, model: m.model }));
        if (!r || !r.ok) { say('ç§»é™¤å¤±è´¥ï¼š' + ((r && (r._err || r.err)) || 'æœªçŸ¥é”™è¯¯'), true); return; }
        await load();
        say(r.removed ? 'å·²ä»æ¸…å•ç§»é™¤' : 'æ¸…å•é‡Œæœ¬æ¥å°±æ²¡æœ‰å®ƒ');
      } catch (e) {
        say('ç§»é™¤å¤±è´¥ï¼š' + (e && e.message ? e.message : e), true);
      }
    }
    async function load() {
      const r = pick(await get('/api/models/saved'));
      if (!r || !r.ok) { say('è¯»ä¸åˆ°æ¨¡å‹æ¸…å•ï¼š' + ((r && (r._err || r.err)) || 'æœªçŸ¥é”™è¯¯'), true); return; }
      data = { models: r.models || [], current: r.current || {}, context_window: r.context_window };
      label();
      draw();
      if (open) placePop();
      if (h.afterLoad) { try { h.afterLoad(); } catch (_) {} }
    }
    async function select(m) {
      if (switching) return;
      if (h.isBusy && h.isBusy()) { say('æ­£åœ¨ç”Ÿæˆå›å¤ï¼šç­‰è¿™ä¸€è½®ç»“æŸå†åˆ‡ï¼Œå…å¾—ä¸Šé¢æ˜¾ç¤ºçš„æ¨¡å‹å’Œå®é™…ç”¨çš„å¯¹ä¸ä¸Š', true); return; }
      // æœ¬åœ°/æ··åˆæ¨¡å¼ä¸‹æ¨¡å‹ç”±æ‰€é€‰æ¨¡å‹å†³å®šï¼Œé»˜è®¤æ¨¡å‹æ¸…å•åœ¨è¿™é‡Œåˆ‡äº†ä¹Ÿä¸ä¼šç”Ÿæ•ˆ
      if (h.blockOpen && h.blockOpen()) {
        say('ç°åœ¨ç”¨çš„æ˜¯æœ¬åœ°æ¨¡å‹ï¼šå…ˆåœ¨è¾“å…¥æ¡†ä¸‹æ–¹çš„æ¨¡å‹èœå•é‡Œåˆ‡å›é»˜è®¤æ¨¡å‹', true);
        return;
      }
      if (m.active) { closePop(); return; }
      if (!m.has_key && m.provider !== 'custom') {
        // è¿™ä»½æ¸…å•å¯èƒ½æ˜¯æ—§çš„ï¼škey ä¹Ÿè®¸åˆšåœ¨åˆ«å¤„è¡¥ä¸Šï¼ˆè®¾ç½®é¡µ / æ‰‹æœºä¸Š / å¦ä¸€ä¸ªçª—å£ï¼‰ï¼Œ
        // è€Œé¢æ¿ä¸€ç›´å¼€ç€æ²¡é‡æ–°æ‹‰è¿‡ã€‚å…ˆè·ŸæœåŠ¡ç«¯å¯¹ä¸€æ¬¡å†ä¸‹ç»“è®ºï¼Œå…å¾—å‡­è¿‡æœŸçŠ¶æ€
        // æ‹’æ‰ä¸€ä¸ªå…¶å®èƒ½ç”¨çš„æ¨¡å‹ã€‚
        await load();
        const fresh = (data.models || []).find((x) =>
          x.provider === m.provider && x.model === m.model);
        if (fresh) m = fresh;
      }
      if (!m.has_key && m.provider !== 'custom') {
        // çœŸç¼ºå¯†é’¥å°±ç›´è¯´ï¼Œåˆ«è®©æœåŠ¡ç«¯å›è½åˆ°ä¸Šä¸€ä¸ªèƒ½ç”¨çš„æ¨¡å‹ â€”â€” é‚£æ­£æ˜¯è¦é¿å…çš„"çœ‹ç€åˆ‡äº†"
        say('ã€Œ' + provOf(m) + 'ã€è¿˜æ²¡å­˜è¿‡å¯†é’¥ï¼šå…ˆåŠ ä¸€æ¬¡å®ƒçš„ keyï¼ˆç‚¹ä¸‹é¢ã€Œæ·»åŠ æˆ–ç®¡ç†æ¨¡å‹ã€ï¼‰', true);
        return;
      }
      switching = true;
      say('æ­£åœ¨åˆ‡åˆ° ' + m.model + 'â€¦');
      try {
        const r = pick(await post('/api/models/quick-switch',
          { provider: m.provider, base: m.base, model: m.model }));
        if (!r || !r.ok) { say('åˆ‡æ¢å¤±è´¥ï¼š' + ((r && (r._err || r.err)) || 'æœªçŸ¥é”™è¯¯'), true); return; }
        await load();
        say('');
        if (r.warn) say(r.warn, true);
        h.onSwitched && h.onSwitched(data.current || {});
      } catch (e) {
        say('åˆ‡æ¢å¤±è´¥ï¼š' + (e && e.message ? e.message : e), true);
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
      if (h.isBusy && h.isBusy()) { toast('æ­£åœ¨ç”Ÿæˆå›å¤ï¼šè¿™ä¸€è½®ç»“æŸå†åˆ‡æ¨¡å‹'); return; }
      // æœ¬åœ°/æ··åˆæ¨¡å¼ï¼šè¿™ä¸ªæŒ‰é’®æ”¹å¼€æ¨¡å¼èœå•ï¼Œå…å¾—åœ¨è¿™é‡Œåˆ‡äº†äº‘ç«¯æ¨¡å‹å´æ²¡ç”Ÿæ•ˆ
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
    // ç‚¹é¢æ¿å¤–é¢æ”¶èµ·ã€‚æŒ‚åœ¨ document ä¸Šï¼Œè§†å›¾è¢«æ¢æ‰åè¿™ä¸ªé—­åŒ…é‡Œçš„å…ƒç´ 
    // å·²è„±ç¦»æ–‡æ¡£ï¼ˆcontains æ’ falseï¼‰ï¼Œä¸ä¼šè¯¯ä¼¤åˆ«çš„é¡µé¢ã€‚
    document.addEventListener('click', (e) => {
      if (!open) return;
      if (pop.contains(e.target) || e.target === btn || btn.contains(e.target)) return;
      closePop();
    });

    label();
    load();
    return { load, label };
  }

  // â•â•â• 1. å¯¹è¯ â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
  let chatTimer = null;

  async function renderChat(ctx) {
    const view = ctx.view, panel = ctx.panel, topbar = ctx.topbar;
    // èŠå¤©é¡µä¸ç”¨ .scrollï¼ˆé‚£æ˜¯ç»™å¡ç‰‡åˆ—è¡¨ç”¨çš„ï¼‰ï¼Œæ¢æˆæ’‘æ»¡æ•´å—çš„ .chatwrap
    view.className = 'chatwrap';
    topbar.className = 'topbar chat-topbar';
    // Render the local shell first. Stats and history continue asynchronously
    // so an unavailable Brain cannot block the first usable frame.
    let st = { ok: false, model: 'â€”', tok_in: 0, tok_out: 0, turns: 0, avg_ms: 0, mem_hit_turns: 0, agenda: {} };
    const statsPromise = get('/api/stats', { timeoutMs: 3000 }).then(pick).catch(() => st);
    const model = (typeof st.model === 'object' && st.model ? st.model.id || st.model.name : st.model) || 'â€”';
    topbar.innerHTML = '';

    // ä¼šè¯ç”± Brain æŒä¹…åŒ–ã€‚localStorage ä¿ç•™ä½œæ—§ç‰ˆæœ¬å…¼å®¹ï¼Œä½†ä¸ä¼šç”¨äºå†·å¯åŠ¨è‡ªåŠ¨é€‰ä¸­ã€‚
    // å·¥ä½œåŒº = ä¸€ä¸ªçœŸå®çš„æœ¬åœ°æ–‡ä»¶å¤¹ï¼›ä¼šè¯åªæ˜¯å®ƒå†…éƒ¨çš„å¯¹è¯ï¼Œä¸æ˜¯å®ƒæœ¬èº«ã€‚
    panel.innerHTML =
      '<div class="phead"><div class="session-brand">Vigil</div></div>' +
      // ä¾§æ åªç•™â€œæ–°å¯¹è¯â€ï¼šæ‰“å¼€æ–‡ä»¶å¤¹ç»Ÿä¸€æ”¾åœ¨è¾“å…¥åŒºçš„â€œé€‰æ‹©å·¥ä½œç©ºé—´â€é‡Œï¼Œ
      // åŒä¸€ä¸ªåŠ¨ä½œä¸åœ¨ä¸¤å¤„å‡ºç°ï¼Œç”¨æˆ·ä¹Ÿä¸ç”¨å…ˆåœ¨å·¦æ æ‰¾å…¥å£ã€‚
      '<div class="session-new"><button class="session-new-button" id="newTopicBtn" title="æ–°å»ºç©ºç™½ä¼šè¯ï¼ˆä¸å±äºä»»ä½•å·¥ä½œåŒºï¼‰" aria-label="æ–°å¯¹è¯">' + icon('chat', 'sm') + '<span>æ–°å¯¹è¯</span></button></div>' +
      '<div class="plist" id="sessList"><div class="empty">è¿˜æ²¡æœ‰èŠè¿‡</div></div>' +
      '<div class="panel-footer"><button class="panel-settings" id="settingsOpen" title="è®¾ç½®" aria-label="æ‰“å¼€è®¾ç½®">' + icon('set', 'sm') + '<span>è®¾ç½®</span></button></div>';
    const sessList = panel.querySelector('#sessList');

    const SESS_KEY = 'vigil.chat.active.v2:' + String(state.base || '') + ':' + String((state.cfg || {}).device_id || '');
    const RENDERER_SESSION_KEY = 'vigil.chat.renderer-session.v1';
    const WORKSPACE_OPEN_KEY = 'vigil.workspaces.open.v1:' + String(state.base || '') + ':' + String((state.cfg || {}).device_id || '');
    let sessionItems = [];
    let workspaceItems = [];
    let workspaceError = '';
    const workspaceProbe = {};          // wid â†’ {state:'ok'|'missing'|'permission'|'unknown'|'checking', detail}
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
        workspaceError = Number(r._status) === 404 ? 'å½“å‰ Brain ç‰ˆæœ¬æ²¡æœ‰å·¥ä½œåŒºæ¥å£' : (r._err || 'å·¥ä½œåŒºåˆ—è¡¨åŠ è½½å¤±è´¥');
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
        if (!api || !api.probe) { workspaceProbe[ws.id] = {state:'unknown', detail:'å½“å‰ç¯å¢ƒæ— æ³•æ£€æŸ¥ç›®å½•'}; continue; }
        workspaceProbe[ws.id] = {state:'checking'};
        try {
          const p = await api.probe(ws.path);
          if (!p || !p.ok) workspaceProbe[ws.id] = {state:'missing', detail:(p && p.err) || 'æ— æ³•æ£€æŸ¥è¯¥ç›®å½•'};
          else if (!p.exists) workspaceProbe[ws.id] = {state:'missing', detail: p.permission ? 'æ— è®¿é—®æƒé™ï¼ˆå¯èƒ½å·²è¢«ç§»åŠ¨ï¼‰' : 'ç›®å½•ä¸å­˜åœ¨æˆ–å·²ç§»åŠ¨'};
          else if (p.directory === false) workspaceProbe[ws.id] = {state:'missing', detail:'è¿™ä¸ªè·¯å¾„ä¸æ˜¯æ–‡ä»¶å¤¹'};
          else if (p.readable === false) workspaceProbe[ws.id] = {state:'permission', detail:'ç›®å½•å­˜åœ¨ä½†å½“å‰ç”¨æˆ·æ— è®¿é—®æƒé™'};
          else workspaceProbe[ws.id] = {state:'ok', detail:''};
        } catch (e) {
          workspaceProbe[ws.id] = {state:'missing', detail:String((e && e.message) || e)};
        }
        drawSessions();
      }
    }
    async function refreshConversations() {
      if (API.runtime.preview) {
        const id = '00000000-0000-4000-8000-000000000001';
        sessionItems = [{id,title:'é¢„è§ˆå¯¹è¯',created_at:Date.now()/1000,updated_at:Date.now()/1000,legacy:false}];
        if (activeConversationId && activeConversationId !== id) activeConversationId = '';
        state.chatConversationId = activeConversationId; drawSessions(); return activeConversationId;
      }
      const r = pick(await get('/api/conversations?src=pc'));
      if (!r.ok || !Array.isArray(r.items)) throw new Error(r._err || 'ä¼šè¯åˆ—è¡¨åŠ è½½å¤±è´¥');
      sessionItems = r.items.filter((item) => item.legacy || item.has_content !== false);
      if (activeConversationId && !sessionItems.some((x) => x.id === activeConversationId)) activeConversationId = '';
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
      if (m < 1) return 'åˆšåˆš';
      if (m < 60) return m + ' åˆ†é’Ÿå‰';
      const h = Math.floor(m / 60);
      if (h < 24) return h + ' å°æ—¶å‰';
      const d = Math.floor(h / 24);
      if (d < 30) return d + ' å¤©å‰';
      return fmtTime(ms, 'day');
    };

    /** å½“å‰ä¼šè¯æ‰€å±æ–‡ä»¶å¤¹ï¼ˆæ²¡æœ‰å°±ä»€ä¹ˆéƒ½ä¸æ˜¾ç¤ºï¼‰â€”â€”åªåšå±•ç¤ºï¼Œä¸æ”¹å½’å±ã€‚ */
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
        '<button class="chat-ws-path" type="button" data-ws-path="' + esc(ws.path) + '" title="ç‚¹å‡»å¤åˆ¶è·¯å¾„">' + esc(ws.path) + '</button>' +
        '<span class="chat-ws-note">ä»…æœ‰ä½ç½®ï¼Œä¸ç­‰äºå·²æˆæƒè¯»å†™</span>';
      const pathButton = bar.querySelector('[data-ws-path]');
      if (pathButton) pathButton.onclick = async () => {
        try { await navigator.clipboard.writeText(ws.path); toast('å·²å¤åˆ¶è·¯å¾„ï¼š' + ws.path); }
        catch (_) { toast('å¤åˆ¶å¤±è´¥ï¼Œè·¯å¾„ï¼š' + ws.path); }
      };
      drawComposerFoot();
    }
    function workspaceRowHtml(ws) {
      const convs = sessionItems.filter((s) => s.workspace_id === ws.id);
      const probe = workspaceProbe[ws.id] || {state:'checking'};
      const hasActive = convs.some((s) => s.id === activeConversationId);
      const open = workspaceOpen[ws.id] !== undefined ? !!workspaceOpen[ws.id] : hasActive;
      const tone = probe.state === 'ok' ? '' : probe.state === 'checking' ? 'checking' : 'bad';
      const statusText = probe.state === 'ok' ? '' : probe.state === 'checking' ? 'æ£€æŸ¥ä¸­â€¦' : (probe.detail || 'ç›®å½•çŠ¶æ€æœªçŸ¥');
      return '<div class="ws-row' + (hasActive ? ' on' : '') + '" data-workspace="' + esc(ws.id) + '">' +
        '<button class="ws-open" data-ws-toggle="' + esc(ws.id) + '" aria-expanded="' + String(open) + '" title="' + esc(ws.path) + '">' +
          '<span class="ic">' + icon('folder', 'sm') + '</span>' +
          '<span class="tx"><b>' + esc(ws.name) + '</b>' +
            '<span class="ws-path" data-ws-copy="' + esc(ws.path) + '" title="ç‚¹å‡»å¤åˆ¶çœŸå®è·¯å¾„ï¼š' + esc(ws.path) + '">' + esc(ws.path) + '</span>' +
          '</span>' +
          '<span class="ws-count">' + convs.length + '</span>' +
        '</button>' +
        '<button class="ibtn ws-new" data-ws-new="' + esc(ws.id) + '" aria-label="åœ¨æ­¤å·¥ä½œåŒºæ–°å»ºä¼šè¯" title="åœ¨æ­¤å·¥ä½œåŒºæ–°å»ºä¼šè¯">' + icon('plus', 'sm') + '</button>' +
        '<button class="ibtn ws-remove" data-ws-remove="' + esc(ws.id) + '" aria-label="ç§»é™¤å·¥ä½œåŒº" title="ç§»é™¤å·¥ä½œåŒºï¼ˆä¸åˆ é™¤æ–‡ä»¶ï¼‰">' + icon('x', 'sm') + '</button>' +
        (tone ? '<div class="ws-status ' + tone + '" role="status">' + esc(statusText) + '</div>' : '') +
        '</div>' +
        (open ? '<div class="ws-chats">' + (convs.length ? convs.map((s) => renderSessionRow(s)).join('')
          : '<div class="empty ws-empty">è¿™ä¸ªå·¥ä½œåŒºè¿˜æ²¡æœ‰ä¼šè¯</div>') + '</div>' : '');
    }
    function renderSessionRow(s) {
      const s_updated = Number(s.updated_at || s.created_at || 0) * 1000;
      const inWorkspace = !!s.workspace_id;
      return '<div class="li session-row' + (s.id === activeConversationId ? ' on' : '') + (inWorkspace ? ' in-workspace' : '') + '">' +
        '<button class="session-open" data-session="' + esc(s.id) + '" title="åˆ‡æ¢ä¼šè¯">' +
        '<span class="ic">' + icon('chat', 'sm') + '</span><span class="tx"><b>' + esc(String(s.title || '').slice(0, 7)) + '</b>' +
        '<span>' + esc(relative(s_updated)) + '</span></span></button>' +
        '<button class="ibtn session-rename" data-rename="' + esc(s.id) + '" aria-label="é‡å‘½åä¼šè¯" title="é‡å‘½å">Â·Â·Â·</button>' +
        '<button class="ibtn session-delete" data-delete="' + esc(s.id) + '" aria-label="åˆ é™¤ä¼šè¯" title="åˆ é™¤ä¼šè¯">' + icon('x', 'sm') + '</button></div>';
    }
    function drawSessions() {
      // Workspaces first: each one owns the chats that point at its folder.
      // Everything else stays an ordinary chat â€” switching never moves anything.
      const loose = sessionItems.filter((s) => !s.workspace_id);
      const known = new Set(workspaceItems.map((w) => w.id));
      // A chat whose workspace vanished must remain reachable as a normal chat.
      const orphaned = sessionItems.filter((s) => s.workspace_id && !known.has(s.workspace_id));
      const wsHtml = workspaceItems.map(workspaceRowHtml).join('');
      const head = '<div class="session-group-title">å·¥ä½œåŒº</div>';
      // The sidebar is a list, not a launcher: the only "æ‰“å¼€æ–‡ä»¶å¤¹" entry
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
        ? '<div class="li session-row on session-draft" aria-current="page"><span class="ic">' + icon('chat', 'sm') + '</span><span class="tx"><b>æ–°å¯¹è¯</b></span></div>'
        : '';
      // No empty "èŠå¤©" heading: with workspaces present, that section only
      // exists when it actually holds something.
      const showChats = looseRows.length || !!newChatRow || !grouped;
      const chatsSection = showChats ? '<section class="session-group session-chats">' +
        (grouped ? '<div class="session-group-title">èŠå¤©</div>' : '') +
        (looseRows.length ? looseRows.map((s) => renderSessionRow(s)).join('')
          : (newChatRow ? '' : '<div class="empty" style="text-align:left;padding:10px 8px">è¿˜æ²¡æœ‰ä¼šè¯</div>')) +
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
        try { await navigator.clipboard.writeText(n.dataset.wsCopy); toast('å·²å¤åˆ¶è·¯å¾„ï¼š' + n.dataset.wsCopy); }
        catch (_) { toast('å¤åˆ¶å¤±è´¥ï¼Œè·¯å¾„ï¼š' + n.dataset.wsCopy); }
      }));
    }
    async function activateConversation(id, options = {}) {
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
        toast('å½“å‰ç¯å¢ƒæ²¡æœ‰åŸç”Ÿæ–‡ä»¶å¤¹é€‰æ‹©å™¨ï¼ˆæµè§ˆå™¨é¢„è§ˆä¸å¯ç”¨ï¼‰');
        return;
      }
      let picked;
      try {
        picked = picker.pick ? await picker.pick() : await picker.pickDirectory();
      } catch (e) { toast('æ— æ³•æ‰“å¼€æ–‡ä»¶å¤¹é€‰æ‹©å™¨ï¼š' + String((e && e.message) || e)); return; }
      if (!picked || !picked.ok) { toast('æ— æ³•æ‰“å¼€æ–‡ä»¶å¤¹ï¼š' + ((picked && picked.err) || 'æœªçŸ¥é”™è¯¯')); return; }
      if (picked.canceled || !picked.path) return;
      const r = pick(await post('/api/workspaces', { src:'pc', path: picked.path }));
      if (!r.ok || !r.item) { toast(r._err || r.err || 'å·¥ä½œåŒºåˆ›å»ºå¤±è´¥'); return; }
      addWorkspaceItem(r.item);
      workspaceOpen[r.item.id] = true;
      saveWorkspaceOpen();
      workspaceProbe[r.item.id] = {state:'checking'};
      drawSessions();
      toast(r.item.existing ? 'è¿™ä¸ªæ–‡ä»¶å¤¹å·²ç»æ˜¯å·¥ä½œåŒº' : 'å·²æ·»åŠ å·¥ä½œåŒºï¼š' + r.item.name);
      void probeWorkspaces();
      if (assign && activeConversationId) await chooseWorkspace(r.item.id);
    }
    /** New chat inside a workspace. The link is explicit â€” never inferred. */
    async function newConversationIn(wid) {
      const ws = workspaceItems.find((w) => w.id === wid);
      if (!ws || busy) return;
      const r = pick(await post('/api/conversations', { src:'pc', workspace_id: wid }));
      if (!r.ok || !r.item) { toast(r._err || r.err || 'æ— æ³•åœ¨è¿™ä¸ªå·¥ä½œåŒºé‡Œæ–°å»ºä¼šè¯'); return; }
      sessionItems.unshift(r.item);
      workspaceOpen[wid] = true;
      saveWorkspaceOpen();
      drawSessions();
      await activateConversation(r.item.id);
    }
    async function removeWorkspace(wid) {
      const ws = workspaceItems.find((w) => w.id === wid);
      if (!ws) return;
      if (!await confirmDialog('ç§»é™¤å·¥ä½œåŒºã€Œ' + ws.name + 'ã€ï¼Ÿé‡Œé¢çš„ä¼šè¯ä¼šä¿ç•™ä¸ºæ™®é€šèŠå¤©ï¼Œä¸ä¼šåˆ é™¤æˆ–ç§»åŠ¨ä»»ä½•æ–‡ä»¶ã€‚')) return;
      const r = pick(await post('/api/workspaces/' + encodeURIComponent(wid) + '/delete', { src:'pc' }));
      if (!r.ok) { toast(r._err || r.err || 'ç§»é™¤å·¥ä½œåŒºå¤±è´¥'); return; }
      workspaceItems = workspaceItems.filter((w) => w.id !== wid);
      sessionItems = sessionItems.map((s) => s.workspace_id === wid ? Object.assign({}, s, {workspace_id:null}) : s);
      delete workspaceProbe[wid];
      drawSessions();
      drawWorkspaceBar();
      toast('å·²ç§»é™¤å·¥ä½œåŒºï¼Œä¼šè¯ä¿ç•™ä¸ºæ™®é€šèŠå¤©');
    }
    // â”€â”€ è¾“å…¥åŒºä¸‹æ–¹ï¼šå·¥ä½œç©ºé—´ + æˆæƒèŒƒå›´ï¼ˆåªè¯´æ˜ç°çŠ¶ï¼Œä¸ä»£æ›¿è®¾ç½®é¡µï¼‰â”€â”€
    let pendingWorkspaceId = null;
    function drawComposerFoot() {
      const label = view.querySelector('#wsPickBtn [data-ws-label]');
      if (label) {
        const conv = sessionItems.find((s) => s.id === activeConversationId);
        const ws = conv && conv.workspace_id ? workspaceItems.find((w) => w.id === conv.workspace_id) : null;
        const pending = !ws && pendingWorkspaceId ? workspaceItems.find((w) => w.id === pendingWorkspaceId) : null;
        label.textContent = ws ? ws.name : pending ? 'ä¸‹ä¸€ä¸ªä¼šè¯ï¼š' + pending.name : 'é€‰æ‹©å·¥ä½œç©ºé—´';
        const chip = view.querySelector('#wsPickBtn');
        if (chip) chip.title = ws ? ws.path : 'é€‰æ‹©è¿™ä¸ªå¯¹è¯æ‰€å±çš„æœ¬åœ°æ–‡ä»¶å¤¹';
      }
      const perm = view.querySelector('#permChip');
      if (perm) {
        const wide = !!(state.cfg && state.cfg.allowAllPaths);
        const text = perm.querySelector('[data-perm-label]');
        if (text) text.textContent = wide ? 'å…è®¸å®Œå…¨è®¿é—®' : 'ä»…æˆæƒç›®å½•';
        perm.classList.toggle('wide', wide);
        perm.title = wide ? 'å½“å‰å…è®¸è®¿é—®æ•´å°æœºå™¨ï¼›ç‚¹è¿™é‡Œè°ƒæ•´æˆæƒèŒƒå›´' : 'å½“å‰åªå…è®¸ç™½åå•ç›®å½•ï¼›ç‚¹è¿™é‡Œè°ƒæ•´æˆæƒèŒƒå›´';
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
        '<div class="wsp-head"><input class="input" id="wsSearch" type="search" placeholder="æœç´¢å·¥ä½œç©ºé—´" aria-label="æœç´¢å·¥ä½œç©ºé—´"></div>' +
        '<div class="wsp-list">' +
          (workspaceItems.length ? rows : '<div class="wsp-empty">è¿˜æ²¡æœ‰å·¥ä½œç©ºé—´</div>') +
          (conv && conv.workspace_id
            ? '<button class="wsp-item" type="button" data-ws-pick="" data-search=""><span class="ic">' + icon('x', 'sm') + '</span>' +
              '<span class="tx"><b>ç§»å‡ºå·¥ä½œåŒº</b><span>ä¼šè¯ä¿ç•™ä¸ºæ™®é€šèŠå¤©ï¼Œä¸åŠ¨ä»»ä½•æ–‡ä»¶</span></span></button>'
            : '') +
        '</div>' +
        '<div class="wsp-foot">' +
          '<button class="wsp-action" type="button" id="wspOpenFolder">' + icon('plus', 'sm') + '<span>æ‰“å¼€æœ¬åœ°æ–‡ä»¶å¤¹</span></button>' +
          '<span class="wsp-note">åªè®°å½•ä½ç½®ï¼Œä¸æˆäºˆè¯»å†™æƒé™</span>' +
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
    /** å½’å±åªç”±ç”¨æˆ·è¿™ä¸€ä¸‹ç‚¹å‡»å†³å®šï¼Œé€‰æ‹©å·¥ä½œåŒºä¸ä¼šè‡ªåŠ¨è·å¾—æ–‡ä»¶æƒé™ã€‚ */
    async function chooseWorkspace(wid) {
      const name = pickWorkspaceTitle(wid);
      if (!activeConversationId) {
        pendingWorkspaceId = wid || null;
        closeWsPop();
        drawComposerFoot();
        toast(wid ? 'ä¸‹ä¸€ä¸ªæ–°ä¼šè¯ä¼šæ”¾è¿›ï¼š' + name : 'ä¸‹ä¸€ä¸ªæ–°ä¼šè¯ä¸å½’å±å·¥ä½œåŒº');
        return;
      }
      const r = pick(await post('/api/conversations/' + encodeURIComponent(activeConversationId) + '/workspace',
        { src:'pc', workspace_id: wid || null }));
      if (!r.ok || !r.item) { toast(r._err || r.err || 'å·¥ä½œåŒºå½’å±ä¿å­˜å¤±è´¥'); return; }
      sessionItems = sessionItems.map((s) => s.id === r.item.id ? r.item : s);
      if (wid) { workspaceOpen[wid] = true; saveWorkspaceOpen(); }
      pendingWorkspaceId = null;
      closeWsPop();
      drawSessions();
      drawWorkspaceBar();
      toast(wid ? 'å·²æŠŠè¿™ä¸ªä¼šè¯æ”¾è¿›ï¼š' + name : 'å·²ç§»å‡ºå·¥ä½œåŒºï¼Œä¼šè¯ä¿ç•™ä¸ºæ™®é€šèŠå¤©');
    }
    /** è¾“å…¥åŒºä¸‹æ–¹é‚£ä¸¤ä¸ªæ ‡ç­¾çš„ç»‘å®šï¼Œå¿…é¡»åœ¨ view.innerHTML ä¹‹åè°ƒç”¨ã€‚ */
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
        // æˆæƒèŒƒå›´å°±åœ¨èŠå¤©æ¡†é‡Œæ”¹ï¼šç‚¹æ ‡ç­¾å¼¹å‡ºå’Œç›¾ç‰ŒåŒä¸€ä¸ªé¢æ¿ï¼Œä¸è·³è®¾ç½®é¡µã€‚
        // æƒé™ä»ç„¶åªç”±é¢æ¿é‡Œçš„æ˜¾å¼é€‰æ‹©æ”¹å˜ï¼Œç‚¹æ ‡ç­¾æœ¬èº«ä¸æˆäºˆä»»ä½•æƒé™ã€‚
        toggleLocalPop(perm);
      });
    }
    async function renameConversation(id) {
      const item = sessionItems.find((s) => s.id === id);
      if (!item) return;
      const row = sessList.querySelector('[data-rename="' + CSS.escape(id) + '"]')?.closest('.session-row');
      if (!row) return;
      row.innerHTML = '<input class="input session-title-edit" maxlength="80" value="' + esc(item.title) + '"><button class="ibtn" aria-label="ä¿å­˜æ ‡é¢˜">âœ“</button>';
      const input = row.querySelector('input'); input.focus(); input.select();
      const save = async () => {
        const title = input.value.trim(); if (!title) { drawSessions(); return; }
        const r = pick(await post('/api/conversations/' + encodeURIComponent(id), {src:'pc', title}));
        if (!r.ok) { toast(r._err || 'æ ‡é¢˜ä¿å­˜å¤±è´¥'); drawSessions(); return; }
        sessionItems = sessionItems.map((s) => s.id === id ? r.item : s); drawSessions();
      };
      row.querySelector('button').onclick = save;
      input.onkeydown = (e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') drawSessions(); };
    }
    async function deleteConversation(id) {
      const item = sessionItems.find((s) => s.id === id);
      if (!item || (busy && id === activeConversationId)) return;
      if (!await confirmDialog('åˆ é™¤ã€Œ' + String(item.title || 'è¿™æ®µèŠå¤©').slice(0, 40) + 'ã€ï¼Ÿè¯¥ä¼šè¯çš„èŠå¤©è®°å½•å°†è¢«åˆ é™¤ï¼Œæ’ä»¶è‰ç¨¿ä»ä¼šä¿ç•™ã€‚')) return;
      const r = pick(await post('/api/conversations/' + encodeURIComponent(id) + '/delete', {src:'pc'}));
      if (!r.ok) { toast(r._err || 'åˆ é™¤å¤±è´¥'); return; }
      sessionItems = sessionItems.filter((s) => s.id !== id);
      if (activeConversationId === id) {
        const next = sessionItems[0];
        if (next) await activateConversation(next.id); else showWelcomeHome(true);
      } else drawSessions();
      toast('ä¼šè¯å·²åˆ é™¤');
    }
    panel.querySelector('#newTopicBtn').addEventListener('click', () => { if (!busy) showWelcomeHome(true, true); });
    panel.querySelector('#settingsOpen').addEventListener('click', () => ctx.navigate('settings'));

    view.innerHTML =
      '<div class="split no-right">' +
      '  <div class="chat">' +
      '    <div class="chat-ws-bar" id="chatWsBar" hidden></div>' +
      '    <div class="hero" id="hero" hidden></div>' +
      '    <div class="chat-load-state" id="chatLoadState" role="status">æ­£åœ¨åŠ è½½ä¼šè¯â€¦</div>' +
      '    <div class="msgs" id="msgs" role="log" aria-label="èŠå¤©è®°å½•"></div>' +
      '    <div class="composer">' +
      '      <div class="localpop modelpop" id="modelPop" hidden>' +
      '        <div class="lph"><span>é€‰æ‹©æ¨¡å‹</span>' +
      '          <button class="ibtn" id="mqSearchToggle" aria-label="æœç´¢æ¨¡å‹" title="æœç´¢æ¨¡å‹">' + icon('search', 'sm') + '</button>' +
      '          <button class="ibtn" id="mqClose" aria-label="æ”¶èµ·æ¨¡å‹åˆ—è¡¨" title="æ”¶èµ·">' + icon('x', 'sm') + '</button></div>' +
      '        <div class="lpbody">' +
      '          <input class="input" id="mqSearch" placeholder="æœç´¢æ¨¡å‹æˆ–æœåŠ¡å•†â€¦" aria-label="æœç´¢æ¨¡å‹" hidden>' +
      '          <div id="mqList"></div>' +
      '          <div class="mq-details">' +
      '            <button class="mq-detail-row" id="mqContextToggle" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="mqContextMenu">' +
      '              <span>ä¸Šä¸‹æ–‡çª—å£</span><b id="mqContextValue">è¯»å–ä¸­â€¦</b><span aria-hidden="true">â€º</span></button>' +
      '            <div id="mqContextMenu" class="mq-context-menu" role="menu" aria-label="äº‘ç«¯ä¸Šä¸‹æ–‡çª—å£" hidden>' +
                     [96000,128000,300000].map((tier) => '<button type="button" role="menuitemradio" aria-checked="false" data-context-tier="' + tier + '"><span class="mq-check" aria-hidden="true"></span>' + (tier / 1000) + 'K</button>').join('') +
      '            </div><p id="mqContextNote" class="mq-detail-note" role="status"></p>' +
      '          </div>' +
      '        </div>' +
      '        <div class="lpadd mqfoot">' +
      '          <button class="btn ghost md" id="mqManage" type="button">æ·»åŠ æˆ–ç®¡ç†æ¨¡å‹</button>' +
      '          <span class="muted" id="mqMsg"></span>' +
      '        </div>' +
      '      </div>' +
      '      <div class="localpop" id="localPop" hidden></div>' +
      '      <div class="localpop pluspop" id="plusPop" hidden role="dialog" aria-label="æ·»åŠ å†…å®¹ä¸è¿è¡Œæ¨¡å¼"></div>' +
      '      <div class="wsPop localpop" id="wsPop" hidden role="dialog" aria-label="é€‰æ‹©å·¥ä½œç©ºé—´"></div>' +
      '      <div class="col">' +
      '      <div class="attach" id="attach" hidden></div>' +
      '      <div class="cbox"><textarea id="ta" rows="2" aria-label="æ¶ˆæ¯å†…å®¹" placeholder="ä»Šå¤©å¸®ä½ åšäº›ä»€ä¹ˆï¼Ÿ@ æ·»åŠ ä¸Šä¸‹æ–‡ï¼Œ/ è°ƒç”¨æŠ€èƒ½ä¸æŒ‡ä»¤"></textarea>' +
      '        <div class="creatorbar" id="creatorBar" hidden>' +
      '          <span class="muted" style="font-size:12px">åˆ›ä½œæ¨¡å¼ï¼šç›´æ¥æè¿°ä½ è¦çš„æ’ä»¶/æŠ€èƒ½ï¼ŒAgent ç”Ÿæˆè‰ç¨¿ï¼ˆåªä¿å­˜ä¸å®¡æ ¸ï¼Œä¸è‡ªåŠ¨å®‰è£…ï¼‰</span>' +
      '          <div class="sp"></div>' +
      '          <button class="chip" data-kind="plugin">æ’ä»¶</button>' +
      '          <button class="chip" data-kind="skill">æŠ€èƒ½</button>' +
      '        </div>' +
      '        <div class="cbar">' +
      '          <button class="ibtn cbar-add" id="clipBtn" title="æ·»åŠ å›¾ç‰‡ã€è§†é¢‘æˆ–æ™®é€šæ–‡ä»¶" aria-label="æ·»åŠ é™„ä»¶">' + icon('plus', 'sm') + '</button>' +
      '          <button class="chip" id="creatorBtn" title="åˆ›ä½œæ¨¡å¼ï¼šç”¨è‡ªç„¶è¯­è¨€è®© Agent ç”Ÿæˆæ’ä»¶/æŠ€èƒ½è‰ç¨¿">åˆ›ä½œï¼šå…³</button>' +
      '          <button class="ibtn" id="localBtn" title="æœ¬åœ°æ“æ§ï¼šæƒé™æ¨¡å¼ Â· ç™½åå•ç›®å½•">' + icon('shield', 'sm') + '</button>' +
      '          <div class="sp"></div>' +
      '          <button class="btn composer-model" id="modelBtn" title="æ‰“å¼€æ¨¡å‹åˆ—è¡¨ï¼Œç‚¹ä¸€æ¡å³åˆ‡æ¢">' + esc(model) + ' â–¾</button>' +
      '          <button class="btn fill" id="sendBtn" disabled aria-label="å‘é€æ¶ˆæ¯" title="å‘é€æ¶ˆæ¯ï¼ˆEnterï¼‰">' + icon('arrow-up', 'sm') + '</button>' +
      '        </div></div>' +
      '      <div class="composer-caption composer-foot">' +
      '        <button class="foot-chip mode-chip" id="modeChip" type="button" data-mode="cloud" title="è¿™ä¸€è½®ä½¿ç”¨çš„æ¨¡å‹"><span class="mode-dot"></span><span data-mode-model>â€”</span></button>' +
      '        <button class="foot-chip" id="wsPickBtn" type="button" title="é€‰æ‹©è¿™ä¸ªå¯¹è¯æ‰€å±çš„æœ¬åœ°æ–‡ä»¶å¤¹">' + icon('folder', 'sm') + '<span data-ws-label>é€‰æ‹©å·¥ä½œç©ºé—´</span></button>' +
      '        <button class="foot-chip" id="permChip" type="button" title="æœ¬åœ°æ–‡ä»¶ä¸å‘½ä»¤çš„æˆæƒèŒƒå›´"><span class="perm-dot"></span><span data-perm-label>ä»…æˆæƒç›®å½•</span></button>' +
      '        <div class="sp"></div>' +
      '        <span class="foot-brand">Vigil Â· AI Agent Workspace</span>' +
      '        <span class="ccount" id="ccount">0 å­—</span>' +
      '      </div>' +
      '      </div>' +
      '    </div>' +
      '  </div>' +
      '  <aside class="rp no" id="rp" aria-label="ä¸Šä¸‹æ–‡ä¸ç»Ÿè®¡"></aside>' +
      '</div>';

    const msgs = view.querySelector('#msgs');
    const heroEl = view.querySelector('#hero');
    const rp = view.querySelector('#rp');
    const chat = view.querySelector('.chat');
    const applySessions = () => {};
    // æ¨¡å‹å¿«é€Ÿåˆ‡æ¢ï¼šç‚¹å·¦ä¸Šè§’é‚£ä¸ªæ¨¡å‹åå¼¹å‡ºå·²ä¿å­˜çš„æ¨¡å‹æ¸…å•ï¼Œç‚¹ä¸€æ¡å³åˆ‡ã€‚
    // æ•°æ®æ¥è‡ª /api/models/savedï¼ˆä¸å¸¦å¯†é’¥ï¼‰ï¼Œåˆ‡æ¢èµ° /api/models/quick-switchã€‚
    // ç”Ÿæˆè¿‡ç¨‹ä¸­ç¦ç”¨ï¼ˆbusyï¼‰ï¼šä¸è®©"æ­£åœ¨è·‘çš„è¿™è½®"å’Œ"åˆšé€‰çš„æ¨¡å‹"å¯¹ä¸ä¸Šã€‚
    const mq = modelQuickBind(view, ctx, {
      isBusy: () => busy,
      // æœ¬åœ°/æ··åˆæ¨¡å¼ä¸‹è¿™ä¸ªæŒ‰é’®æ”¹å¼€æ¨¡å¼èœå•ï¼šäº‘ç«¯æ¸…å•é‡Œçš„é€‰æ‹©åœ¨é‚£ä¸¤ç§æ¨¡å¼é‡Œä¸ç”Ÿæ•ˆ
      blockOpen: () => !!(runStatus && runStatus.mode === 'local'),
      onBlocked: () => { openPlusPop(); },
      afterLoad: () => syncComposerModelLabel(),
      onSwitched: (c) => {
        if (c && c.model) toast('å·²åˆ‡åˆ° ' + c.model);
        void loadRunMode(false);
      },
    });
    // åˆ›ä½œæ¨¡å¼å¼€å…³ï¼šchip å¾ªç¯ å…³ â†’ æ’ä»¶ â†’ æŠ€èƒ½ â†’ å…³
    // åˆ›ä½œæ¨¡å¼ bar é‡Œçš„ç±»å‹ç›´é€‰ï¼ˆä¸ç”¨å¾ªç¯ä¹Ÿèƒ½ä¸€æ­¥åˆ‡åˆ°ç›®æ ‡ç±»å‹ï¼‰
    // ğŸ”´ ç›‘å¬æ³¨å†Œå¯ä»¥æå‰ï¼Œä½† applyCreatorMode() çš„ç«‹å³è°ƒç”¨å¿…é¡»æ™šäº
    // let creatorMode å£°æ˜ï¼ˆTDZï¼šæå‰è°ƒä¼š "Cannot access before initialization"ï¼Œ
    // æ•´é¡µç”»ä¸å‡ºæ¥ â€”â€” 2026-10-12 å®æµ‹è¸©è¿‡ï¼‰
    view.querySelector('#creatorBtn').addEventListener('click', () => {
      if (busy) return;
      creatorMode = creatorMode === 'off' ? 'plugin' : creatorMode === 'plugin' ? 'skill' : 'off';
      applyCreatorMode();
      toast(creatorMode === 'off' ? 'å·²å›åˆ°æ™®é€šå¯¹è¯'
        : 'åˆ›ä½œæ¨¡å¼ï¼šç”Ÿæˆ' + (creatorMode === 'skill' ? 'æŠ€èƒ½' : 'æ’ä»¶') + 'è‰ç¨¿ï¼Œå‘æ¶ˆæ¯å³ç”Ÿæˆ');
    });
    view.querySelector('#creatorBar').querySelectorAll('.chip[data-kind]').forEach((c) =>
      c.addEventListener('click', () => {
        creatorMode = c.dataset.kind;
        applyCreatorMode();
        toast('åˆ›ä½œæ¨¡å¼ï¼šç”Ÿæˆ' + (creatorMode === 'skill' ? 'æŠ€èƒ½' : 'æ’ä»¶') + 'è‰ç¨¿');
      }));
    // Delegate copy actions so history reloads and completed streams share one binding.
    msgs.addEventListener('click', async (e) => {
      const button = e.target.closest('[data-copy-code]');
      if (!button) return;
      try {
        await navigator.clipboard.writeText(button.closest('.codeblock').querySelector('code').textContent);
        button.textContent = 'å·²å¤åˆ¶';
        setTimeout(() => { if (button.isConnected) button.textContent = 'å¤åˆ¶'; }, 1500);
      } catch (_) { toast('å¤åˆ¶å¤±è´¥ï¼Œè¯·é€‰ä¸­ä»£ç æ‰‹åŠ¨å¤åˆ¶'); }
    });

    const cIn = Number(st.tok_in) || 0, cOut = Number(st.tok_out) || 0;
    rp.innerHTML =
      '<div class="card" style="margin-bottom:10px"><div class="gtitle" style="padding:0 0 6px">æœ¬è½®ä¸Šä¸‹æ–‡</div>' +
      '<div class="kv"><span>å½“å‰äººè®¾</span><b data-chat-stat="name">' + esc(st.name || 'â€”') + '</b></div>' +
      '<div class="kv"><span>æ¨¡å‹</span><b data-chat-stat="model">' + esc(model) + '</b></div>' +
      '<div class="kv"><span>ä»Šæ—¥è½®æ¬¡</span><b data-chat-stat="turnç®÷êÚ$z{-®éÜj×ˆŞiK’r¢~‹ùk*˜XÒr’²r#âr°¢sÆF—b6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£ãW‚"–CÒ&Ô¶W”†–çB#âr²¶W”†–çEFW‡B†7W"’²sÂöF—câr°¢sÆF—b7G–ÆSÒ&föçB×6—¦S£ãWƒ¶Ö&v–â×F÷£W‚"–CÒ&Ô¶W•7FFR#âr°¢†7W"æ•ö¶W•÷6WBò~x«nhûÉ®[{.KùŞZÙ‚+rhêzûÉ£Ç7â6Æ73Ò&Ööæò#âr²W62†7W"æ•ö¶W’ÇÂr¢¢¢¢¢¢¢¢r’²sÂ÷7ãâr¢~x«nhûÉ®iÊ®˜XŞ{Úâr’²sÂöF—cãÂöF—câr°¢sÂöF—câr°¢sÆF—b6Æ73Ò'6V2#îjŠYè¾Xˆn˜XŞûÈKˆ¾h¸˜h‰ny»Nhê^‹é>XZ^ûÉ¾iÊÎYË˜:{Û.y¨BvVâZ¾Zè>y¨NjŠYè¾YŞûÈ“ÂöF—câr°¢sÆF—b6Æ73Ò&w&–Bs2#âr°¢sÆF—b6Æ73Ò&6&B#ãÆ"7G–ÆSÒ&föçB×6—¦S£7‚#îZûŠùÓÂö#ãÆF—b7G–ÆSÒ&Ö&v–â×F÷£‚#âr²6öÖ&ò‚vÔ6†BrÂw&÷W2æ6†BÂ7W"æ6†BÂ~[ø^Z²r’²sÂöF—cãÂöF—câr°¢sÆF—b6Æ73Ò&6&B#ãÆ"7G–ÆSÒ&föçB×6—¦S£7‚#îyÈ¾Y»ãÂö#ãÆF—b7G–ÆSÒ&Ö&v–â×F÷£‚#âr²6öÖ&ò‚vÕf—6–öârÂw&÷W2çf—6–öâÂ7W"çf—6–öâ’²sÂöF—cãÂöF—câr°¢sÆF—b6Æ73Ò&6&B#ãÆ"7G–ÆSÒ&föçB×6—¦S£7‚#îyIşY»ãÂö#ãÆF—b7G–ÆSÒ&Ö&v–â×F÷£‚#âr²6öÖ&ò‚vÔ–ÖvRrÂw&÷W2æ–ÖvRÂ7W"æ–ÖvR’²sÂöF—cãÂöF—câr°¢sÆF—b6Æ73Ò&6&B#ãÆ"7G–ÆSÒ&föçB×6—¦S£7‚#îŠúŞ™û>ŠønXŠ³Âö#ãÆF—b7G–ÆSÒ&Ö&v–â×F÷£‚#âr²6öÖ&ò‚vÔVF–òrÂw&÷W2æVF–òÂ7W"æVF–ò’²sÂöF—cãÂöF—câr°¢sÂöF—câr°¢sÆF—b6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£ãWƒ¶Ö&v–â×F÷£‡‚#îiÊÎYË˜:{Û.ûÈ„öÆÆÖòÄÒ7GVF–òòfÆÆŞûÈûÉ®hê^Xú>YËYØZ¾Zè>y¨NYËYØûÈZh"‡GG¢òşKÚy¨D•£C3B÷cûÈûÈÎjŠYè¾YŞh˜¾Z¾(	N(	Nk:hHş‹ùKŠ®YËYØŠhiÊÎiË®Šëş™zî[é~X‹ûÈ›¹ŠêN[iŠò#rãããûÈ8#ÂöF—câr°¢sÆF—b7G–ÆSÒ&F—7Æ“¦fÆWƒ¶v£‡ƒ¶Ö&v–â×F÷£Gƒ¶Æ–vâÖ—FV×3¦6VçFW"#âr°¢sÆ'WGFöâ6Æ73Ò&'Fâf–ÆÂÖB"–CÒ&Õ6fR#îKùŞZÙ[›nyIşiXûÈXXŞ˜xŞY
şûÈ“Âö'WGFöãâr°¢sÆ'WGFöâ6Æ73Ò&'FâöÆ–æRÖB"–CÒ&ÕVÆÂ#î˜xŞikh¸XùnjŠYè³Âö'WGFöãâr°¢sÆ'WGFöâ6Æ73Ò&'FâöÆ–æRÖB"–CÒ&ÕFW7B#îkX¾Šù^‹ùîhêSÂö'WGFöãâr°¢sÆ'WGFöâ6Æ73Ò&'FâöÆ–æRÖB"–CÒ&ÔFVÆWFT¶W’#îXŠ™šNjÚNZënZøn™*SÂö'WGFöãâr°¢sÇ7â6Æ73Ò&×WFVB"–CÒ&Õ&W2"7G–ÆSÒ&föçB×6—¦S£'‚#îiÚ^k©ûÉ¢r²W62‡"ç6÷W&6RÇÂrr’²r+rX[r²W62‡"æ6÷VçBÇÂ’²rKŠ¢r°¢‡"æW'"òr+rr²W62…7G&–ær‡"æW'"’ç6Æ–6RƒÂc’’¢rr’²sÂ÷7ããÂöF—câs°¢6öç7B&÷e6VÂÒbçVW'•6VÆV7F÷"‚r6Õ&÷br“°¢6öç7B&6T–çÒbçVW'•6VÆV7F÷"‚r6Ô&6Rr“°¢6öç7B¶W”–çÒbçVW'•6VÆV7F÷"‚r6Ô¶W’r“°¢6öç7B¶W”†–çBÒbçVW'•6VÆV7F÷"‚r6Ô¶W”†–çBr“°¢6öç7BÕ&W2ÒbçVW'•6VÆV7F÷"‚r6Õ&W2r“°¢6öç7BÔ¶W•7FFRÒbçVW'•6VÆV7F÷"‚r6Ô¶W•7FFRr“°¢6öç7B6fVD†÷7BÒbçVW'•6VÆV7F÷"‚r6Õ6fVE&÷w2r“°¢6öç7BFDf÷&ÒÒbçVW'•6VÆV7F÷"‚r6ÔFDf÷&Òr“°¢6öç7BG&u6fVDÖöFVÇ2Ò‚’Óâ°¢6fVD†÷7Bæ–ææW$…DÔÂÒ6fVE&÷w4‡FÖÂ‚“°¢6fVD†÷7BçVW'•6VÆV7F÷$ÆÂ‚u¶FFÖÖöFVÂ×&VÖ÷fUÒr’æf÷$V6‚‚†"’Óâ"æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ7–æ2‚’Óâ°¢6öç7BÖöFVÂÒ6fVDÖöFVÇ5´çVÖ&W"†"æFF6WBæÖöFVÅ&VÖ÷fR•Ó°¢–b‚ÖöFVÂ’&WGW&ã°¢–b‚v—B6öæf—&ÔF–Æör‚~K¸î[{.k{¾XªjŠYè¾KŠŞz{¾™šN8Âr²†ÖöFVÂææÖRÇÂÖöFVÂæÖöFVÂ’²~8ŞûÉòr’’&WGW&ã°¢"æF—6&ÆVBÒG'VS°¢6öç7B'"Ò–6²†v—B×÷7B‚rö’öÖöFVÇ2öf÷&vWBrÂ°¢&÷f–FW#¢ÖöFVÂç&÷f–FW"Â&6S¢ÖöFVÂæ&6RÂÖöFVÃ¢ÖöFVÂæÖöFVÂÀ¢Ò’“°¢–b‚'"æö²’²Fö7B‚~z{¾™šNZK‹J^ûÉ¢r²‡'"åöW'"ÇÂ'"æW'"ÇÂ~iÊ®yú^™IŠúòr’“²"æF—6&ÆVBÒfÇ6S²&WGW&ã²Ğ¢6öç7Bg&W6‚Ò–6²†v—BÖvWB‚rö’öÖöFVÇ2÷6fVBr’“°¢6fVDÖöFVÇ2Òg&W6‚æö²ò†g&W6‚æÖöFVÇ2ÇÂµÒ’æf–ÇFW"‚†Ò’ÓâÒçVç6fVB’¢µÓ°¢G&u6fVDÖöFVÇ2‚“°¢Fö7B‚~[{.K¸îjŠYè¾kˆ^XÙ^z{¾™šBr“°¢Ò’“°¢Ó°¢6öç7BFEFövvÆRÒbçVW'•6VÆV7F÷"‚r6ÔFEFövvÆRr“°¢6öç7BFE&÷f–FW"ÒbçVW'•6VÆV7F÷"‚r6ÔFE&÷f–FW"r“°¢6öç7BFD&6RÒbçVW'•6VÆV7F÷"‚r6ÔFD&6Rr“°¢6öç7BFDÖöFVÂÒbçVW'•6VÆV7F÷"‚r6ÔFDÖöFVÂr“°¢6öç7BFD¶W’ÒbçVW'•6VÆV7F÷"‚r6ÔFD¶W’r“°¢6öç7BFE&W7VÇBÒbçVW'•6VÆV7F÷"‚r6ÔFE&W7VÇBr“°¢FEFövvÆRæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚’Óâ°¢FDf÷&Òæ†–FFVâÒFDf÷&Òæ†–FFVã°¢–b‚FDf÷&Òæ†–FFVâ’FDÖöFVÂæfö7W2‚“°¢Ò“°¢bçVW'•6VÆV7F÷"‚r6ÔFD6æ6VÂr’æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚’Óâ°¢FDf÷&Òæ†–FFVâÒG'VS°¢FE&W7VÇBçFW‡D6öçFVçBÒrs°¢Ò“°¢FE&÷f–FW"æFDWfVçDÆ—7FVæW"‚v6†ævRrÂ‚’Óâ°¢6öç7B6VÆV7FVE&÷f–FW"Ò&÷g2æf–æB‚‡’Óâæ¶W’ÓÓÒFE&÷f–FW"çfÇVR“°¢–b‡6VÆV7FVE&÷f–FW"bb6VÆV7FVE&÷f–FW"æ¶W’ÓÒv7W7FöÒrbb6VÆV7FVE&÷f–FW"æ&6R¢FD&6RçfÇVRÒ6VÆV7FVE&÷f–FW"æ&6S°¢Ò“°¢bçVW'•6VÆV7F÷"‚r6ÔFE6fRr’æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ7–æ2†R’Óâ°¢6öç7B6fT'WGFöâÒRæ7W'&VçEF&vWC°¢6öç7BÖöFVÄ–BÒFDÖöFVÂçfÇVRçG&–Ò‚“°¢6öç7B&6RÒFD&6RçfÇVRçG&–Ò‚“°¢–b‚ÖöFVÄ–B’²FE&W7VÇBçFW‡D6öçFVçBÒ~Šû~Z¾Xi’ÖöFVÂ”Bs²FDÖöFVÂæfö7W2‚“²&WGW&ã²Ğ¢–b‚&6R’²FE&W7VÇBçFW‡D6öçFVçBÒ~Šû~Z¾Xi’&6RU$Âs²FD&6Ræfö7W2‚“²&WGW&ã²Ğ¢6fT'WGFöâæF—6&ÆVBÒG'VS°¢FE&W7VÇBçFW‡D6öçFVçBÒ~KùŞZÙKŠŞ(
bs°¢6öç7B–ÆöBÒ°¢æÖS¢bçVW'•6VÆV7F÷"‚r6ÔFDæÖRr’çfÇVRçG&–Ò‚’À¢&÷f–FW#¢FE&÷f–FW"çfÇVRÀ¢&6RÀ¢ÖöFVÃ¢ÖöFVÄ–BÀ¢Ó°¢–b†FD¶W’çfÇVRçG&–Ò‚’’–ÆöBæ•ö¶W’ÒFD¶W’çfÇVRçG&–Ò‚“°¢6öç7B'"Ò–6²†v—B×÷7B‚rö’öÖöFVÇ2÷6fRrÂ–ÆöB’“°¢6fT'WGFöâæF—6&ÆVBÒfÇ6S°¢–b‚'"æö²’²FE&W7VÇBçFW‡D6öçFVçBÒ~KùŞZÙZK‹J^ûÉ¢r²‡'"åöW'"ÇÂ'"æW'"ÇÂ~iÊ®yú^™IŠúòr“²&WGW&ã²Ğ¢6fVDÖöFVÇ2Ò‡'"æÖöFVÇ2ÇÂµÒ’æf–ÇFW"‚†Ò’ÓâÒçVç6fVB“°¢G&u6fVDÖöFVÇ2‚“°¢FD¶W’çfÇVRÒrs°¢FDÖöFVÂçfÇVRÒrs°¢bçVW'•6VÆV7F÷"‚r6ÔFDæÖRr’çfÇVRÒrs°¢FE&W7VÇBçFW‡D6öçFVçBÒ~[{.KùŞZÙûÉ¾ˆ®ZJz©~Xú>˜xŞikh™>[ÈjŠYè¾ˆùÎXÙ^XÛ>XúşŠû¾Xùn8"s°¢Fö7B‚~jŠYè¾˜XŞ{Úî[{.KùŞZÙ‚r“°¢Ò“°¢G&u6fVDÖöFVÇ2‚“°¢òòkˆ^XÙ^[YËhÚ.ŠûÉ®Xú®X‹~Y¹¾KŠ®Kˆ¾h¸y¨NX	˜Y(ÎiÚ^k©ŠÎûÈÎKˆŞi[Nš^˜xŞyK²(	N(	Bi[Nš^˜xŞyK°¢òòKÉ®h¨®yJh‹~X‰®‹é>X‹KˆXØ®y¨NZøn™*^kI~hè8 ¢6öç7BÇ”Æ—7BÒ‡'"’Óâ°¢6öç7B²Ò²Ô6†C¢v6†BrÂÕf—6–öã¢wf—6–öârÂÔ–ÖvS¢v–ÖvRrÂÔVF–ó¢vVF–òrÓ°¢ö&¦V7Bæ¶W—2„²’æf÷$V6‚‚†–B’Óâ°¢6öç7BFÂÒbçVW'•6VÆV7F÷"‚r2r²–B²uöFÂr“°¢–b†FÂ’FÂæ–ææW$…DÔÂÒ‚‡'"æw&÷W2ÇÂ·Ò•´µ¶–EÕÒÇÂµÒ’æÖ‚†Ò’Óâ°¢6öç7BÖ–BÒG—VöbÒÓÓÒw7G&–ærròÒ¢†Òæ–BÇÂÒææÖR“°¢&WGW&âsÆ÷F–öâfÇVSÒ"r²W62†Ö–B’²r#âr²W62†Ö–B’²sÂö÷F–öãâs°¢Ò’æ¦ö–â‚rr“°¢Ò“°¢Õ&W2çFW‡D6öçFVçBÒ'"æw&÷W2ò‚~iÚ^k©ûÉ¢r²W62‡'"ç6÷W&6RÇÂrr’²r+rX[r²W62‡'"æ6÷VçBÇÂ’²rKŠ¢r°¢‡'"æW'"òr+rr²W62…7G&–ær‡'"æW'"’ç6Æ–6RƒÂc’’¢rr’’¢‚~)Érr²W62†ÖöFVÄW'"‡'"’’“°¢Ó°¢òòK©zºòşiÊÎiË®Xˆ~hÚ.ûÉ®KŠN‹ëiŠşKŠNK»ŞxºÎz¸¾˜XŞ{ÚîûÈÎi[Nš^˜xŞyK°¢&–æE6÷W&6U7v—F6‚‚“°¢òò‹ùŠÎik[ÈşûÉ®Xˆ~K©zºşi{nh©XúöÆÆÖşZyNkKîŠëî{ÚîûÈXÎKˆŞkˆ^ûÈÎKùŞZÙi{nK¸ŞKÉ®[ŠnKˆ®h˜˜jŠ[ÈşûÈ¢6öç7BöÄ&÷‚ÒbçVW'•6VÆV7F÷"‚r6öÄ&÷‚r“°¢6öç7BFÄ&÷‚ÒbçVW'•6VÆV7F÷"‚r6FÄ&÷‚r“°¢bçVW'•6VÆV7F÷$ÆÂ‚r6ÔÖöFR'WGFöâr’æf÷$V6‚‚†"’Óâ"æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚’Óâ°¢–b†"æF—6&ÆVB’&WGW&ã°¢bçVW'•6VÆV7F÷$ÆÂ‚r6ÔÖöFR'WGFöâr’æf÷$V6‚‚‡‚’Óâ‚æ6Æ74Æ—7BçFövvÆR‚vöârÂ‚ÓÓÒ"’“°¢–b†öÄ&÷‚’öÄ&÷‚ç7G–ÆRæF—7Æ’Ò†"æFF6WBæÖÒÓÓÒvÆö6ÂrÇÂ"æFF6WBæÖÒÓÓÒv‡–'&–Br’òrr¢væöæRs°¢–b†FÄ&÷‚’FÄ&÷‚ç7G–ÆRæF—7Æ’Ò"æFF6WBæÖÒÓÓÒv‡–'&–Bròrr¢væöæRs°¢Ò’“°¢òò‹ùîhê^kX¾Šù^ûÉ®h»şŠXÙ^˜xÎy¨NYËYØûÈjŠYè¾yYz›®K™şˆ;ŞkX¾iÈŞXªYÊKˆŞYÊûÈûÈÎh‰X©şYîY¹îZ¾jŠYè¾Kˆ¾h¸¢6öç7BöÅ&W2ÒbçVW'•6VÆV7F÷"‚r6öÅ&W2r“°¢6öç7BöÅFW7D'FâÒbçVW'•6VÆV7F÷"‚r6öÅFW7Br“°¢–b†öÅFW7D'Fâ’öÅFW7D'FâæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ7–æ2‚’Óâ°¢6öç7B&6RÒ‡bçVW'•6VÆV7F÷"‚r6öÄ&6Rr’ÇÂ·Ò’çfÇVRÇÂrs°¢6öç7BÖöFVÂÒ‡bçVW'•6VÆV7F÷"‚r6öÄÖöFVÂr’ÇÂ·Ò’çfÇVRÇÂrs°¢öÅ&W2çFW‡D6öçFVçBÒ~kX¾Šù^KŠŞ(
bs°¢6öç7BÒsö&6SÒr²Væ6öFUU$”6ö×öæVçB†&6RçG&–Ò‚’’°¢†ÖöFVÂòrfÖöFVÃÒr²Væ6öFUU$”6ö×öæVçB†ÖöFVÂçG&–Ò‚’’¢rr“°¢6öç7B'"Ò–6²†v—BÖvWB‚rö’ööÆÆÖ÷7FGW2r²’“°¢–b‡'"æö²’°¢öÅ&W2çFW‡D6öçFVçBÒ~)É2YÊ{«ò+r[{.Š8Rr²‡'"æÖöFVÇ2ÇÂµÒ’æÆVæwF‚²rKŠ®jŠYè²r°¢‡'"æÖöFVÅö–ç7FÆÆVBÓÓÒfÇ6Rò~ûÈ[Ù>X˜ŞZ¾y¨NjŠYè¾iÊ®ZèŠ8^ûÈ’r¢rr“°¢6öç7BFÂÒbçVW'•6VÆV7F÷"‚r6öÄÖöFVÅöFÂr“°¢–b†FÂ’FÂæ–ææW$…DÔÂÒ‡'"æÖöFVÇ2ÇÂµÒ’æÖ‚†Ò’Óà¢sÆ÷F–öâfÇVSÒ"r²W62†Ò’²r#âr’æ¦ö–â‚rr“°¢–b‚ÖöFVÂbb‡'"æÖöFVÇ2ÇÂµÒ’æÆVæwF‚’°¢bçVW'•6VÆV7F÷"‚r6öÄÖöFVÂr’çfÇVRÒ'"æÖöFVÇ5³Ó°¢Ğ¢ÒVÇ6R°¢öÅ&W2çFW‡D6öçFVçBÒ~)Ér‹ùîKˆŞKˆ®ûÉ¢r²‡'"æW'"ÇÂ~j8iúRöÆÆÖiŠşY
n[{.Y
şXª‚r“°¢Ğ¢Ò“°¢òòiÈ‹ùZyNkKîûÉ®iÊÎiË®ZJ~ˆIFFöFVÆVvF–öç2òy¨N‰Şy¹Šë[Ù^ûÈÎjøşjÊZyNkKîy¨Nx«nhşyJ˜xğ¢òò˜;ŞYÊ‹ùûÈXú®YÊiÊÎiË®ŠxnY»îiÈ’FÄ&÷ûÈ8 ¢–b†FÄ&÷‚’°¢6öç7BFÄÆ—7BÒFÄ&÷‚çVW'•6VÆV7F÷"‚r6FÄÆ—7Br“°¢6öç7BFÅ&W2ÒFÄ&÷‚çVW'•6VÆV7F÷"‚r6FÅ&W2r“°¢6öç7B´”äEô4âÒ°¢Æöu÷&ö÷Eö6W6S¢~iz^[ù~jYºrÂf–ÆUöF—66÷fW'“¢~ih~K»nXùxërÀ¢&VEööæÇ•ö'VuöæÇ—6—3¢~‹zih~K»b'VrrÂ7G'V7GW&VEöW‡G&7F–öã¢~{¹>ièNXÉnh«ŞXùbrÀ¢ÆåöG&gC¢~ŠêX‰.ˆØh¹òrÀ¢Ó°¢6öç7BDÅô$DtRÒ°¢7V66VVFVC¢²~h‰X©òrÂr3vc3ruÒÂf–ÆVC¢²~ZK‹JRrÂr63uÒÀ¢F–ÖVEö÷WC¢²~‹h^i{brÂr6##VcuÒÂ6æ6VÆÆVC¢²~Xùnkh‚rÂr3ccbuÒÀ¢'Vææ–æs¢²~‹ù¾ŠÎKŠÒrÂr3“c–FuÒÂVWVVC¢²~hé.™‰òrÂr3ccbuÒÀ¢Ó°¢6öç7Bf×DDÅF–ÖRÒ‡G2’Óâ°¢6öç7BBÒæWrFFR‚‡G2ÇÂ’¢“°¢6öç7BÒ†â’Óâ7G&–ær†â’çE7F'Bƒ"Âsr“°¢&WGW&â†BævWDÖöçF‚‚’²’²rÒr²†BævWDFFR‚’’²rr°¢†BævWD†÷W'2‚’’²s¢r²†BævWDÖ–çWFW2‚’’²s¢r²†BævWE6V6öæG2‚’“°¢Ó°¢6öç7BÆöDDÂÒ7–æ2‚’Óâ°¢FÄÆ—7BçFW‡D6öçFVçBÒ~Xª‹ÛŞKŠŞ(
bs°¢6öç7B'"Ò–6²†v—BÖvWB‚rö’öFVÆVvF–öç2÷&V6VçCöãÓ#r’“°¢6öç7B—FV×2Ò'"æ—FV×2ÇÂµÓ°¢–b‚'"æö²’²FÄÆ—7BçFW‡D6öçFVçBÒ~Šû¾KˆŞX‹ûÉ¢r²W62‡'"åöW'"ÇÂ'"æW'"ÇÂrr“²&WGW&ã²Ğ¢–b‚—FV×2æÆVæwF‚’²FÄÆ—7BçFW‡D6öçFVçBÒ~‹ùk*iÈZyNkKîŠë[Ù^8"s²&WGW&ã²Ğ¢FÄÆ—7Bæ–ææW$…DÔÂÒ—FV×2æÖ‚†—B’Óâ°¢6öç7B&rÒDÅô$DtU¶—Bç7FGW5ÒÇÂ¶—Bç7FGW2ÇÂsòrÂr3ccbuÓ°¢6öç7BW''2Ò†—BæW'&÷'2ÇÂµÒ’æÖ‚†R’ÓâW62†Ræ6FVv÷'’ÇÂ~™IŠúòr’’æ¦ö–â‚~8r“°¢&WGW&âsÆF—b7G–ÆSÒ&&÷&FW"×F÷£‚6öÆ–Bf"‚ÒÖ&÷&FW"Â6VVR“·FF–æs£g‚#âr°¢sÆF—b7G–ÆSÒ&F—7Æ“¦fÆWƒ¶v£‡ƒ¶Æ–vâÖ—FV×3¦6VçFW#¶fÆW‚×w&§w&#âr°¢sÆ"7G–ÆSÒ&föçB×6—¦S£ãW‚#âr²W62„´”äEô4å¶—Bæ¶–æEÒÇÂ—Bæ¶–æBÇÂsòr’²sÂö#âr°¢sÇ7â7G–ÆSÒ&6öÆ÷#¢6ffc¶&6¶w&÷VæC¢r²&u³Ò²s¶&÷&FW"×&F—W3£7ƒ·FF–æs£Wƒ¶föçB×6—¦S£ãW‚#âr²W62†&u³Ò’²sÂ÷7ãâr°¢sÇ7â7G–ÆSÒ&6öÆ÷#¢3““’#âr²W62†f×DDÅF–ÖR†—Bæ7&VFVEöB’’²sÂ÷7ãâr°¢†—Bç&WG'•÷W6VBòsÇ7â7G–ÆSÒ&6öÆ÷#¢6##Vc#î˜xŞŠùSjÊÂ÷7ãâr¢rr’°¢sÂöF—câr°¢sÆF—b7G–ÆSÒ&Ö&v–â×F÷£'ƒ·v÷&BÖ'&V³¦'&V²ÖÆÂ#âr²W62†—BævöÂÇÂrr’²sÂöF—câr°¢sÆF—b7G–ÆSÒ&6öÆ÷#¢3“““¶Ö&v–â×F÷£'‚#îjŠYè¾Šû~k"r²W62†—BæÖöFVÅ÷&WVW7G2’°¢r+r[z^X[rr²W62†—BçFööÅö6ÆÇ2’²r+rFö¶Vç2r²W62†—Bæ–çWE÷Fö¶Vç2’°¢ròr²W62†—Bæ÷WGWE÷Fö¶Vç2’²r+ryJi{br²W62†—BæVÆ6VE÷6V6öæG2’²w2r°¢†W''2òr+rÇ7â7G–ÆSÒ&6öÆ÷#¢63#âr²W''2²sÂ÷7ãâr¢rr’²sÂöF—cãÂöF—câs°¢Ò’æ¦ö–â‚rr“°¢FÅ&W2çFW‡D6öçFVçBÒ~X[r²—FV×2æÆVæwF‚²riÚ+rr²f×DDÅF–ÖR†—FV×5³Òæ7&VFVEöB’²ri»Niks°¢Ó°¢FÄ&÷‚çVW'•6VÆV7F÷"‚r6FÅ&Vg&W6‚r’æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂÆöDDÂ“°¢ÆöDDÂ‚“°¢Ğ¢òòKˆ¾h¸hÚ.iÈŞXªYXnûÉ®YËYØˆz®XªZ²²kˆ^XÙ^ˆz®Xªh¸8.‹ùZënk*ZÙ‹ø~Zøn™*^8Zøn™*^jnXøz›®yØ ¢òò[XXKˆŞh¸’(	N(	BiÈŞXªzºşXú®ˆ;Şh»şXŠ¾Zëny¨B¶W’Xë¾Šù^ûÈÎ[ø^xKbCûÈÎy›Şh¸Kˆ‹Úî‹ùY	X{ ¢òòKˆK»ŞXYÎ[©^kˆ^XÙ^Xi.XX^‹ùZëny¨NjŠYè¾ûÈ‚.hÚ.K¨nZënjŠYè¾XÛNKˆŞXù‚.[iŠşZè>[›.y¨NûÈ8 ¢6öç7B&÷eVÆÂÒ7–æ2‚’Óâ°¢6öç7B²Ò&÷e6VÂçfÇVS°¢6öç7BÒ&÷g2æf–æB‚‡‚’Óâ‚æ¶W’ÓÓÒ²“°¢–b‡bbæ¶W’ÓÒv7W7FöÒr’&6T–ççfÇVRÒæ&6RÇÂrs°¢–b‡²ÓÓÒv7W7FöÒr’&WGW&ã°¢–b‚†4¶W’‡²’bb¶W”–ççfÇVRçG&–Ò‚’’°¢¶W”†–çBæ–ææW$…DÔÂÒ~8Âr²W62‡&÷dæÖR‡²’’²~8Ş‹ùk*ZÙ‹ø~Zøn™*^(	N(	NXXYÊZøn™*^jnZ¾Zè>y¨B¶WûÈÎXhŞx+8Î˜xŞikh¸XùnjŠYè¾8Òs°¢&WGW&ã°¢Ğ¢6öç7B'"Ò–6²†v—B×÷7B‚rö’öÖöFVÇ2öÆ—7BrÂ²&6S¢&6T–ççfÇVRçG&–Ò‚’Â&÷f–FW#¢²Ò’“°¢–b‡'"æw&÷W2’Ç”Æ—7B‡'"“°¢VÇ6RÕ&W2çFW‡D6öçFVçBÒ~)Érr²ÖöFVÄW'"‡'"“°¢Ó°¢&÷e6VÂæFDWfVçDÆ—7FVæW"‚v6†ævRrÂ&÷eVÆÂ“°¢bçVW'•6VÆV7F÷"‚r6ÕVÆÂr’æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ7–æ2‚’Óâ°¢6öç7B&6RÒ&6T–ççfÇVRçG&–Ò‚“°¢6öç7B²Ò&÷e6VÂçfÇVS°¢6öç7B¶W’Ò¶W”–ççfÇVRçG&–Ò‚“°¢6öç7B'"Ò–6²†v—B×÷7B‚rö’öÖöFVÇ2öÆ—7BrÂ°¢f÷&6S¢G'VRÂ&6S¢&6RÂ&÷f–FW#¢²Â•ö¶W“¢¶W¢Ò’“°¢Fö7B‡'"æw&÷W2bb'"ç6÷W&6RÓÒvfÆÆ&6²rò~[{.˜xŞikh¸Xùbp¢¢~h¸XùnZK‹J^ûÉ¢r²ÖöFVÄW'"‡'"’“°¢òò[ŠnKˆ®X‰®yJy¨B&6R÷&÷f–FW"˜xŞyK¾ûÈÎkˆ^XÙ^XŠ¾[ËY¹î[{.KùŞZÙy¨Niz~ZënûÉ°¢òò¶W’KˆŞY¹îKÊûÈiˆîih~KˆŞ‹ù²U$ÎûÈûÈÎiÈŞXªzºşZÙ‹ø~‹ùZëny¨NKÉ®ˆz®[{ŠêNš(n8 ¢ÖöFVÂ‡²&6S¢&6RÂ&÷f–FW#¢²Ò“°¢Ò“°¢bçVW'•6VÆV7F÷"‚r6ÕFW7Br’æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ7–æ2‚’Óâ°¢Ô¶W•7FFRçFW‡D6öçFVçBÒ~x«nhûÉ®kX¾Šù^KŠŞ(
bs°¢6öç7B'"Ò–6²†v—B×÷7B‚rö’öÖöFVÇ2÷FW7BrÂ°¢&÷f–FW#¢&÷e6VÂçfÇVRÂ&6S¢&6T–ççfÇVRçG&–Ò‚’Â•ö¶W“¢¶W”–ççfÇVRçG&–Ò‚¢Ò’“°¢Õ&W2çFW‡D6öçFVçBÒ'"æö²ò~)É2‹ùîhê^jÚ>[‹‚r¢~)Érr²ÖöFVÄW'"‡'"“°¢Ô¶W•7FFRçFW‡D6öçFVçBÒ'"æö²ò~x«nhûÉ®kX¾Šù^h‰X©òr¢~x«nhûÉ®kX¾Šù^ZK‹JR+rr²ÖöFVÄW'"‡'"“°¢Ò“°¢bçVW'•6VÆV7F÷"‚r6ÔFVÆWFT¶W’r’æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ7–æ2‚’Óâ°¢6öç7B²Ò&÷e6VÂçfÇVS°¢–b‚†4¶W’‡²’’²Õ&W2çFW‡D6öçFVçBÒ~[Ù>X˜ŞiÈŞXªYXnk*iÈ[{.KùŞZÙy¨NZøn™*Rs²&WGW&ã²Ğ¢–b‚v—B6öæf—&ÔF–Æör‚~XŠ™šN8Âr²&÷dæÖR‡²’²~8Şy¨N[{.KùŞZÙZøn™*^ûÉòr’’&WGW&ã°¢6öç7B'"Ò–6²†v—B×÷7B‚rö’öÖöFVÇ2ö¶W’öFVÆWFRrÂ²&÷f–FW#¢²Ò’“°¢–b‚'"æö²’²Õ&W2çFW‡D6öçFVçBÒ~)Érr²ÖöFVÄW'"‡'"“²&WGW&ã²Ğ¢Fö7B‚~[{.XŠ™šNŠú^iÈŞXªYXnZøn™*Rr“°¢ÖöFVÂ‡²&÷f–FW#¢²Â&6S¢&6T–ççfÇVRçG&–Ò‚’Ò“°¢Ò“°¢òòŠúŞ™û>Yh‰™û>ˆ›.ûÈY
¾ŠêŞ{¸>™û>ˆ›.ûÈûÉ¢ö’÷fö–6W2iŠşjÛ¾i[hÚîkˆ^XÙ^ûÈÎk‹ùÎh™>[é~[È8 ¢òò–6²‚’‹ùNY¹îy¨NiŠşišî˜	®Zû‹KˆŞiŠò&öÖ—6^ûÈÎy»NhêRv—Bh»ş{¹>iéÎ8 ¢6öç7Bg"Ò–6²†v—BÖvWB‚rö’÷fö–6W2r’“°¢6öç7Bd—FV×2Òg"æ—FV×2ÇÂµÓ°¢–b‡g"æö²bbd—FV×2æÆVæwF‚’°¢6öç7B6&BÒFö7VÖVçBæ7&VFTVÆVÖVçB‚vF—br“°¢6&Bæ6Æ74æÖRÒw6V2s°¢6&Bç7G–ÆRæÖ&v–åF÷Òs‡‚s°¢6&BçFW‡D6öçFVçBÒ~ŠúŞ™û>Yh‰ûÈZ[y¨NZ;™û>ûÈ’s°¢bæVæD6†–ÆB†6&B“°¢6öç7B&÷‚ÒFö7VÖVçBæ7&VFTVÆVÖVçB‚vF—br“°¢&÷‚æ6Æ74æÖRÒv6&Bs°¢6öç7Bdw&÷W2Ò·Ó°¢d—FV×2æf÷$V6‚‚†—B’Óâ²‡dw&÷W5¶—Bæw&÷WÇÂ~X[nK¹buÒÒdw&÷W5¶—Bæw&÷WÇÂ~X[nK¹buÒÇÂµÒ’çW6‚†—B“²Ò“°¢&÷‚æ–ææW$…DÔÂĞ¢sÆF—b7G–ÆSÒ&F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£ƒ¶fÆW‚×w&§w&#âr°¢sÆF—b7G–ÆSÒ&fÆWƒ£¶Ö–â×v–GFƒ£#‚#ãÆ"7G–ÆSÒ&föçB×6—¦S£7‚#î[Ù>X˜Ş™û>ˆ›#Âö#âr°¢sÆF—b6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£'ƒ¶Ö&v–â×F÷£'‚#âr²W62‡g"æ7W'&VçEöFW62ÇÂg"æ7W'&VçBÇÂ~(	Br’²sÂöF—cãÂöF—câr°¢sÇ6VÆV7B6Æ73Ò&–çWB"–CÒ'fö–6U6VÂ"7G–ÆSÒ'v–GFƒ£#c‚#âr°¢ö&¦V7Bæ¶W—2‡dw&÷W2’æÖ‚†r’Óà¢sÆ÷Fw&÷WÆ&VÃÒ"r²W62†r’²r#âr°¢dw&÷W5¶uÒæÖ‚†—B’ÓâsÆ÷F–öâfÇVSÒ"r²W62†—Bæ¶W’’²r"r²†—Bæ¶W’ÓÓÒg"æ7W'&VçBòr6VÆV7FVBr¢rr’²sâr²W62†—BæÆ&VÂ’²sÂö÷F–öãâr’æ¦ö–â‚rr’°¢sÂö÷Fw&÷Wâr’æ¦ö–â‚rr’°¢sÂ÷6VÆV7Câr°¢sÆ'WGFöâ6Æ73Ò&'Fâf–ÆÂ"–CÒ'fö–6TÇ’#î[©NyJ™û>ˆ›#Âö'WGFöããÂöF—câr°¢sÆF—b6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£ãWƒ¶Ö&v–â×F÷£g‚#îXˆ~hÚ.XXŞ˜xŞY
ş8.ŠêŞ{¸>™û>ˆ›.YÊZû[©NXˆn{¸N˜xÎ8#ÂöF—câs°¢bæVæD6†–ÆB†&÷‚“°¢&÷‚çVW'•6VÆV7F÷"‚r7fö–6TÇ’r’æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ7–æ2‚’Óâ°¢6öç7B¶W’Ò&÷‚çVW'•6VÆV7F÷"‚r7fö–6U6VÂr’çfÇVS°¢6öç7B'"Ò–6²†v—B×÷7B‚rö’÷fö–6RöÇ’rÂ²¶W“¢¶W’Ò’“°¢Fö7B‡'"æö²ò~™û>ˆ›.[{.Xˆ~hÚ.ûÉ¢r²‡'"æFW62ÇÂ¶W’’¢~Xˆ~hÚ.ZK‹J^ûÉ¢r²‡'"åöW'"ÇÂ'"æW'"ÇÂrr’“°¢–b‡'"æö²’²6öç7BBÒ&÷‚çVW'•6VÆV7F÷"‚ræ×WFVBr“²–b†B’BçFW‡D6öçFVçBÒ'"æFW62ÇÂ¶W“²Ğ¢Ò“°¢Ğ ¢bçVW'•6VÆV7F÷"‚r6Õ6fRr’æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ7–æ2‚’Óâ°¢6öç7B²Ò&÷e6VÂçfÇVS°¢6öç7B¶W’Ò¶W”–ççfÇVRçG&–Ò‚“°¢6öç7BÖÔ'FãÒbçVW'•6VÆV7F÷"‚r6ÔÖöFR'WGFöâæöâr“°¢6öç7BÖöFSÒÖÔ'FãòÖÔ'FãæFF6WBæÖÒ¢v6Æ÷VBs°¢òòhªNjşûÉ®Xˆ~X‹KˆZëb.k*ZÙ‹ø~Zøn™*^K™şk*Z¾ikZøn™*R.y¨NûÈÎKùŞZÙZèÎZ[Kˆ¾KˆXú^[C8 ¢òòiÊÎYËöÆÆÖjŠ[ÈşKˆŞ‹>K©zºşûÈÎKˆŞŠhK©zºò¶W’(	N(	B‹{>‹ø~‹ù˜>hªNjş8 ¢–b†ÖöFSÓÒvÆö6Ârbb²ÓÒ7W"ç&÷f–FW"bb†4¶W’‡²’bb¶W’’°¢¶W”†–çBæ–ææW$…DÔÂÒ~8Âr²W62‡&÷dæÖR‡²’’²~8Ş‹ùk*Zøn™*^(	N(	NXXZ¾Zè>y¨B¶W’XhŞKùŞZÙûÈh‰n˜iÊÎYËöÆÆÖjŠ[ÈşûÈ’s°¢¶W”–çæfö7W2‚“°¢&WGW&ã°¢Ğ¢6öç7B–ÆöBÒ°¢&÷f–FW#¢²À¢•ö&6S¢&6T–ççfÇVRçG&–Ò‚’À¢6†C¢bçVW'•6VÆV7F÷"‚r6Ô6†Br’çfÇVRÀ¢f—6–öã¢bçVW'•6VÆV7F÷"‚r6Õf—6–öâr’çfÇVRÀ¢–ÖvS¢bçVW'•6VÆV7F÷"‚r6Ô–ÖvRr’çfÇVRÀ¢VF–ó¢bçVW'•6VÆV7F÷"‚r6ÔVF–òr’çfÇVRÀ¢Ó°¢–b†¶W’’–ÆöBæ•ö¶W’Ò¶W“°¢òò‹ùŠÎik[Èò²öÆÆÖjë^ûÈK¸^iÊÎiË®ZJ~ˆIŠxnY»îiÈ‹ù[ÊXÚûÈ¢6öç7BÖÔ'FâÒbçVW'•6VÆV7F÷"‚r6ÔÖöFR'WGFöâæöâr“°¢–b†ÖÔ'Fâ’°¢–ÆöBæÖöFVÅöÖöFRÒÖÔ'FâæFF6WBæÖÓ°¢–b‡–ÆöBæÖöFVÅöÖöFRÓÓÒvÆö6ÂrÇÂ–ÆöBæÖöFVÅöÖöFRÓÓÒv‡–'&–Br’°¢6öç7BöÄÖöFVÂÒ‡bçVW'•6VÆV7F÷"‚r6öÄÖöFVÂr’ÇÂ·Ò’çfÇVRÇÂrs°¢–b‚öÄÖöFVÂçG&–Ò‚’’°¢Fö7B‚~iÊÎYËşk{~YjŠ[ÈşŠhXX˜h‰nZ¾KˆKŠ®[{.ZèŠ8^y¨BöÆÆÖjŠYè¾ûÈx+8ÎkX¾Šù^‹ùîhê^8ŞXúşˆz®Xªh¸XùnûÈ’r“°¢&WGW&ã°¢Ğ¢–ÆöBæöÆÆÖÒ°¢&6S¢‡bçVW'•6VÆV7F÷"‚r6öÄ&6Rr’ÇÂ·Ò’çfÇVRÇÂrrÀ¢ÖöFVÃ¢öÄÖöFVÂçG&–Ò‚’À¢çVÕö7Gƒ¢'6T–çB‚‡bçVW'•6VÆV7F÷"‚r6öÄ7G‚r’ÇÂ·Ò’çfÇVRÂ’ÇÂc3ƒBÀ¢çVÕ÷&VF–7C¢'6T–çB‚‡bçVW'•6VÆV7F÷"‚r6öÅ&VBr’ÇÂ·Ò’çfÇVRÂ’ÇÂ#C‚À¢F†–æ³¢‡bçVW'•6VÆV7F÷"‚r6öÅF†–æ²r’ÇÂ·Ò’æ6†V6¶VBÀ¢Ó°¢Ğ¢òòZyNkKîjë^Xú®YÊk{~YjŠ[ÈşhùKªNûÉ¾K©zºòşiÊÎYËjŠ[ÈşyYyØ˜XŞ{ÚîK™şizZë>ûÈ™zhê~yÈ²ÖöFVÅöÖöF^ûÈ¢–b‡–ÆöBæÖöFVÅöÖöFRÓÓÒv‡–'&–Br’°¢–ÆöBæFVÆVvF–öâÒ°¢Væ&ÆVC¢‡bçVW'•6VÆV7F÷"‚r6FÄVæ&ÆVBr’ÇÂ·Ò’æ6†V6¶VBÀ¢ÆÆ÷vVE÷&ö÷G3¢‡bçVW'•6VÆV7F÷"‚r6FÅ&ö÷G2r’ÇÂ·Ò’çfÇVRÇÂrrÀ¢Ó°¢–b‡–ÆöBæFVÆVvF–öâæVæ&ÆVBb`¢–ÆöBæFVÆVvF–öâæÆÆ÷vVE÷&ö÷G2çG&–Ò‚’’°¢Fö7B‚~[ÈK¨nZyNkKîKØnk*Z¾hèiØ>[z^KÙÎXË®ûÉ®z›®YŞXÙ^KÉ®h¹.{¹Şh˜iÈZyNkKî8.XXZ¾hèiØ>yºî[Ù^8"r“°¢&WGW&ã°¢Ğ¢Ğ¢Ğ¢òò‹ùŠÎik[ÈşXX‹ør.[ŠnZéîkX¾y¨NXˆ~hÚ.™z‚.ûÉ®iÊÎYËjŠYè¾{Ë®ZKòi‹îZÙKˆŞ‹k2òXª‹ÛŞ‹h^i{`¢òò˜;ŞYÊ‹ù˜xÎhÊKØşûÈÎKˆŞKÉ®XXXi‹ù¾˜XŞ{ÚîXhŞŠ8^KÙÎXˆ~Z[ŞK¨n8.X[nKÙZÙ~jë^K¸Ş‹[Ç8 ¢6öç7BvçDÖöFRÒ–ÆöBæÖöFVÅöÖöFS°¢–b‡vçDÖöFRbbvçDÖöFRÓÒ†7W"æÖöFVÅöÖöFRÇÂv6Æ÷VBr’’°¢6öç7B7rÒ–6²†v—B×÷7B‚rö’÷'VâÖÖöFRrÂ°¢ÖöFS¢vçDÖöFRÂÖöFVÃ¢‡–ÆöBæöÆÆÖÇÂ·Ò’æÖöFVÂÇÂrrÀ¢Ò’“°¢–b‡7ræö²’°¢7W"æÖöFVÅöÖöFRÒvçDÖöFS°¢FVÆWFR–ÆöBæÖöFVÅöÖöFS²òò[{.{¸şyIşiXûÈÎKˆŞXhŞ˜xŞZHŞhùKª@¢–b‡7rçv&æ–æw2bb7rçv&æ–æw2æÆVæwF‚’Õ&W2çFW‡D6öçFVçBÒ7rçv&æ–æw2æ¦ö–â‚~ûÉ²r“°¢ÒVÇ6R–b‡7rå÷7FGW2ÓÓÒCB’°¢òòiz~x˜K‹¾ˆIk*iÈXˆ~hÚ.™zûÉ®iÊÎYËşk{~YYÊ˜*>‹ëjiÊÎKˆŞZÙYÊûÈÎKˆŞˆ;ŞX~Š8^KùŞZÙh‰X©şûÉ°¢òòY¹î›¹ŠêNX‰hÈˆhê^Xú>xZ~[‹KùŞZÙ‚(	N(	B{Ë®KˆKŠ®hê^Xú>KˆŞŠú^h¨®K«®hÊYÊ™zZIn8 ¢FVÆWFR–ÆöBæÖöFVÅöÖöFS°¢–b‡vçDÖöFRÓÒv6Æ÷VBr’°¢Õ&W2çFW‡D6öçFVçBÒ~k*KùŞZÙh‰ûÉ®‹ùXûK‹¾ˆI‹ùk*iÈiÊÎYËjŠYè¾ˆ;ŞX©¾ûÈx˜iÊÎ‹è>iz~ûÈûÈÎi»NikK‹¾ˆIYîXhŞXˆrs°¢Fö7B‚~K‹¾ˆIKˆŞiJşhÈiÊÎYËjŠYè²r“°¢&WGW&ã°¢Ğ¢Õ&W2çFW‡D6öçFVçBÒ~K‹¾ˆIx˜iÊÎ‹è>iz~ûÉ®[{.hÈ›¹ŠêNjŠYè¾KùŞZÙ‚s°¢ÒVÇ6R°¢Õ&W2çFW‡D6öçFVçBÒ~k*KùŞZÙh‰ûÉ¢r²‡7ræW'"ÇÂ7råöW'"ÇÂ~‹ùŠÎjŠ[ÈşXˆ~hÚ.ZK‹JRr“°¢Fö7B‚~‹ùŠÎjŠ[Èşk*Xˆ~h‰ûÉ¢r²‡7rç&V6öâÇÂrr’“°¢&WGW&ã°¢Ğ¢Ğ¢6öç7B'"Ò–6²†v—B×÷7B‚rö’öÖöFVÇ2öÇ’rÂ–ÆöB’“°¢òòh¨®iÈŞXªzºşŠêN‹Jny¨NiKXªXéşj~KªîX{®iÚ^ûÈ‚.iÈŞXªYXb(i"[ş{2Ö”ÖşûÉ¾[{.hÚ.Kˆ®[ş{>y¨NZøn™*R.ûÈûÈÀ¢òòXˆ~hÚ.h‰k*h‰X©şKˆyËÎyÈ¾[é~ŠxûÈÎKˆŞXhŞiŠş[›.[{N[{Ny¨B.[{.yIşiX‚.8 ¢6öç7BÖöFTæ÷FRÒõîk*KùŞZÙh‰òçFW7B†Õ&W2çFW‡D6öçFVçBÇÂrr’òrr¢†Õ&W2çFW‡D6öçFVçBÇÂrr“°¢Õ&W2çFW‡D6öçFVçBÒ'"æö°¢ò‚‡'"æ6†ævVBbb'"æ6†ævVBæÆVæwF‚’ò'"æ6†ævVBæ¦ö–â‚~ûÉ²r’¢†ÖöFTæ÷FRÇÂ~k*iÈiKXª‚r’¢¢~k*KùŞZÙh‰ûÉ¢r²‡'"åöW'"ÇÂ'"æW'"ÇÂrr“°¢Fö7B‡'"æö²ò~[{.yIşiX‚r¢~KùŞZÙZK‹JRr“°¢–b‡'"æö²’°¢7W"ç&÷f–FW"Ò³°¢–b†¶W’’°¢†7W"æ¶W—5ö'•÷&÷f–FW"Ò7W"æ¶W—5ö'•÷&÷f–FW"ÇÂ·Ò•·µÒÒG'VS°¢7W"æ¶W•ö&VÆöæw5÷FòÒ³°¢7W"æ•ö¶W•÷6WBÒG'VS°¢¶W”–ççfÇVRÒrs°¢Ğ¢¶W”†–çBæ–ææW$…DÔÂÒ¶W”†–çEFW‡B†7W"“°¢6öç7Bg&W6‚Ò–6²†v—BÖvWB‚rö’öÖöFVÇ2÷6fVBr’“°¢6fVDÖöFVÇ2Òg&W6‚æö²ò†g&W6‚æÖöFVÇ2ÇÂµÒ’æf–ÇFW"‚†Ò’ÓâÒçVç6fVB’¢µÓ°¢G&u6fVDÖöFVÇ2‚“°¢Ğ¢Ò“°¢Ğ ¢7–æ2gVæ7F–öâFævW"‚’°¢7G‚çF÷&"çVW'•6VÆV7F÷"‚vƒr’çFW‡D6öçFVçBÒ~XÛ™ši8ŞKÙÂs°¢6öç7B7G2Ò°¢²~˜xŞ{ÚîZûŠùŞKˆ®Kˆ¾ihrrÂ~kˆ^hèiÊÎ‹ÚîKˆ®Kˆ¾ih~YÙ~ûÈÎˆ®ZJŠë[Ù^KùŞyY’rÂrö’÷&W6WBuÒÀ¢²~kˆ^z›®ˆ®ZJXènXû"rÂ~hÈ[Ù>X˜ŞkŠ˜>kˆ^ûÈÎkˆ^ZèÎKˆŞXúşh.ZHÒrÂrö’ö†—7F÷'’ö6ÆV"uÒÀ¢Ó°¢bæ–ææW$…DÔÂÒsÆF—b6Æ73Ò&6&B#âr²7G2æÖ‚…·BÂBÂÒ’Óà¢sÆF—b6Æ73Ò'&÷r#ãÆF—b6Æ73Ò&Æ"#ãÆ#âr²W62‡B’²sÂö#ãÇ7ãâr²W62†B’²sÂ÷7ããÂöF—câr°¢sÆ'WGFöâ6Æ73Ò&'Fâ"FF×Ò"r²²r"FF×CÒ"r²W62‡B’²r#îhš~ŠÃÂö'WGFöããÂöF—câr’æ¦ö–â‚rr’²sÂöF—câs°¢bçVW'•6VÆV7F÷$ÆÂ‚u¶FF×Òr’æf÷$V6‚‚†"’Óâ"æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ7–æ2‚’Óâ°¢–b‚v—B6öæf—&ÔF–Æör†"æFF6WBçB²~ûÉòr’’&WGW&ã°¢6öç7B&öG’Ò"æFF6WBçæ–æ6ÇVFW2‚v†—7F÷'’r’ò²7&3¢7FFRç7&2Ò¢·Ó°¢6öç7B"Ò–6²†v—B÷7B†"æFF6WBçÂ&öG’’“°¢–b‡"æö²bb"æFF6WBçÓÓÒrö’ö†—7F÷'’ö6ÆV"r’6ÆV$Æö6ÄF—7Æ•&V6÷&G4f÷$6†ææVÂ‡7FFRç7&2Â"æ6ÆV&VE÷6W76–öç2“°¢Fö7B‡"æö²ò"æFF6WBçB²~ûÉ®[{.hš~ŠÂr¢~ZK‹J^ûÉ¢r²‡"åöW'"ÇÂrr’“°¢Ò’“°¢Ğ ¢7–æ2gVæ7F–öâ&÷WB‚’°¢7G‚çF÷&"çVW'•6VÆV7F÷"‚vƒr’çFW‡D6öçFVçBÒ~X[>K¨âs°¢bæ–ææW$…DÔÂÒsÆF—b6Æ73Ò&6&B#âr°¢sÆF—b6Æ73Ò&·b#ãÇ7ãîZê.h‹~zºóÂ÷7ããÆ#çcããûÈ†6öFRÇ7â–CÒ&$6öFR#î(
cÂ÷7ãîûÈ“Âö#ãÂöF—câr°¢sÆF—b6Æ73Ò&·b#ãÇ7ãîjÎ™Ú.jniëcÂ÷7ããÆ#äVÆV7G&öâr²W62…7G&–ær‚†vÆö&Âç&ö6W72bb&ö6W72çfW'6–öç2bb&ö6W72çfW'6–öç2æVÆV7G&öâ’ÇÂrr’’²sÂö#ãÂöF—câr°¢sÆF—b6Æ73Ò&·b#ãÇ7ãä'&–âYËYØÂ÷7ããÆ"6Æ73Ò&Ööæò#âr²W62‡7FFRæ&6RÇÂ~(	Br’²sÂö#ãÂöF—câr°¢sÆF—b6Æ73Ò&·b#ãÇ7ãîkŠ˜3Â÷7ããÆ#âr²W62‡7FFRç7&2’²~ûÈiÊÎiË®hê~X‹nXûûÈÎxºÎz¸¾K¨âò[êîKúûÈ“Âö#ãÂöF—câr°¢sÆF—b6Æ73Ò&·b#ãÇ7ãîXzŞhÚãÂ÷7ããÆ#îiÊÎiË¢'&–âˆz®[{y¨BFö¶VîûÈKˆŞX{®iË®YšûÈ“Âö#ãÂöF—câr°¢sÆF—b6Æ73Ò&·b#ãÇ7ãîi»NikiÚ^k©Â÷7ããÆ#äv—D‡V"&VÆV6W3Âö#ãÂöF—câr°¢sÆF—b7G–ÆSÒ&F—7Æ“¦fÆWƒ¶v£‡ƒ¶Æ–vâÖ—FV×3¦6VçFW#¶Ö&v–â×F÷£‚#âr°¢sÆ'WGFöâ6Æ73Ò&'FâöÆ–æRÖB"–CÒ&$6†V6²#îj8iú^i»NikÂö'WGFöãâr°¢sÇ7â6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£'‚"–CÒ&$×6r#îY
şXªi{nY(Îjøş[şi{nK™şKÉ®ˆz®Xªj8iú^ûÈ„v—D‡V.ûÈ“Â÷7ãâr°¢sÂöF—câr°¢sÆF—b6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£ãWƒ¶Ö&v–â×F÷£‚#îiÊÎYËi8Şhê~[{.[›nXZ^ˆ®ZJš^ûÉ®zîŠêNYÊk	Nk:˜xÎûÈÎiØ>™™şyºî[ÙRş[z^X[~YÊ‹é>XZ^jnixy¨Ny»îx˜Î™Ú.iÛş˜xÎûÈÎ‹é>XZ^jnKˆ¾iky¨N(	ÎK¸^hèiØ>yºî[Ù^(	Şj~zÛîh™>[ÈYÎKˆKŠ®™Ú.iÛş8.jÎZêÆ—fS$NûÈ™‹njëS^ûÈiÊ®[ÈZx¾8#ÂöF—câr°¢sÂöF—câs° ¢6öç7B6†÷rÒ‡R’Óâ°¢bçVW'•6VÆV7F÷"‚r6$6öFRr’çFW‡D6öçFVçBÒ7G&–ær‡Ræ7W'&VçBÒçVÆÂòRæ7W'&VçB¢sr“°¢6öç7BÒÒbçVW'•6VÆV7F÷"‚r6$×6rr“°¢–b‡Ræ'W7’’²ÒçFW‡D6öçFVçBÒ~Kˆ®KˆjÊj8iú^‹ùk*‹yZèÎ(
bs²&WGW&ã²Ğ¢–b‡RæW'"’²ÒçFW‡D6öçFVçBÒ~j8iú^ZK‹J^ûÉ¢r²RæW'#²&WGW&ã²Ğ¢–b‚RçWFFR’²ÒçFW‡D6öçFVçBÒ~[{.iŠşiÈik8"s²&WGW&ã²Ğ¢–b‡Ræ–ç7FÆÆ–ær’²ÒçFW‡D6öçFVçBÒ~ZèŠ8^Yš[{.Y
şXªûÈÎZê.h‹~zºşXÛ>[n˜X{®8"s²&WGW&ã²Ğ¢–b‡RæF÷væÆöFVB’²ÒçFW‡D6öçFVçBÒ~ikx˜iÊÎ[{.Kˆ¾‹ÛŞûÈÎKˆ¾jÊY
şXªKÉ®XhŞhù˜i.8"s²&WGW&ã²Ğ¢–b‡RæFV6Æ–æVB’²ÒçFW‡D6öçFVçBÒ~Xùxëikx˜iÊÂbr²‡RçfW"ÇÂrr’²~ûÈKÚ‹ùjÊXXKˆŞi»NûÈ8"s²&WGW&ã²Ğ¢ÒçFW‡D6öçFVçBÒ~Xùxëikx˜iÊÂbr²‡RçfW"ÇÂrr’²~8"s°¢Ó° ¢bçVW'•6VÆV7F÷"‚r6$6†V6²r’æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ7–æ2‚’Óâ°¢6öç7B'FâÒbçVW'•6VÆV7F÷"‚r6$6†V6²r“°¢6öç7B×6rÒbçVW'•6VÆV7F÷"‚r6$×6rr“°¢'FâæF—6&ÆVBÒG'VS°¢×6rçFW‡D6öçFVçBÒ~j8iú^KŠŞ(
bs°¢G'’°¢6öç7BRÒv—BvÆö&Âçf–v–ÂçWFFW"æ6†V6²‚v6²r“°¢6†÷r‡RÇÂ²W'#¢~izY8Ş[©BrÒ“°¢Ò6F6‚†R’°¢×6rçFW‡D6öçFVçBÒ~j8iú^ZK‹J^ûÉ¢r²7G&–ær†RbbRæÖW76vRÇÂR“°¢Ğ¢–b†'Fâbb'Fâæ—4FW7G&÷–VB’'FâæF—6&ÆVBÒfÇ6S°¢Ò“° ¢òò‹ù¾š^Xú®iú^x«nhûÈKˆŞ[Ëz©~KˆŞKˆ¾‹ÛŞûÈûÉ¾x˜iÊÎXû~Y(Â.iÈk*iÈikx˜‚.š®KëşyK¾Kˆ ¢G'’°¢6öç7BRÒv—BvÆö&Âçf–v–ÂçWFFW"æ6†V6²‚“°¢–b‡R’6†÷r‡R“°¢Ò6F6‚†R’²ò¢izK©zºş˜XŞ{ÚîzØûÈÎ™Ù›¹‚¢òĞ¢Ğ ¢òò)H)H[êîKúˆ®ZJ™¸nh‰ûÉ®‹zşyK8[ÈX[>8kŠ˜>Šëî{ÚîY(Î‹ùîhê^Šø®ijÒ)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H ¢òò)H)H[êîKúj^hê^ûÉ®‹ùKˆ‹ÚîXú®Šû¾ûÈÎKˆŞX®K»¾KÙR.˜XŞ{ÚîK©zºò.y¨NX~XªKÙÂ)H)H)H)H)H)H)H)H)H ¢òò[êîKúkhhşyKxºÎz¸¾y¨Nj^hê^iÈŞXªZHNynûÈÎZè>‹ùîy¨NiŠşY:®Xû'&–âKˆŞ[Ù.‹ùKŠ®Zê.h‹~zºşzêûÉ°¢òò‚ã"KÉ®h¨®Zè>X®h‰XúşZèŠ8RşXúşXÎyJy¨Nhù.K»nûÈ…vT6†B'&–Fv^ûÈûÈÎ[®i{nXzŞhÚîKˆî[ÈX[0¢òò˜;ŞiKnYÊhù.K»nˆz®[{y¨N˜XŞ{Úî˜xÎ8.xëYÊh¨®‹ëyXÎŠûNkˆ^jY®ûÈÎXŠ¾ŠêhÈ™*îš©~K«®8 ¢7–æ2gVæ7F–öâvV6†B‚’°¢7G‚çF÷&"çVW'•6VÆV7F÷"‚vƒr’çFW‡D6öçFVçBÒ~[êîKúj^hêRs°¢6öç7B'BÒ–6²†v—BvWB‚rö’÷&÷WFRr’“°¢6öç7BÆö6Äö²Ò'Bæö³°¢bæ–ææW$…DÔÂĞ¢sÆF—b6Æ73Ò&6&B"7G–ÆSÒ&Ö&v–âÖ&÷GFöÓ£G‚#âr°¢sÆF—b6Æ73Ò&·b#ãÇ7ãîiÊÎiË¢'&–ãÂ÷7ããÆ#âr²†Æö6Äö²ò~Xúş‹ëâr¢~KˆŞXúş‹ëâr’²sÂö#ãÂöF—câr°¢sÆF—b6Æ73Ò&·b#ãÇ7ãî[êîKúj^hê^‹ù¾zˆ³Â÷7ããÆ#îxºÎz¸¾iÈŞXªûÈKˆŞYÊiÊÎZê.h‹~zºş˜xÎûÈ“Âö#ãÂöF—câr°¢sÆF—b6Æ73Ò&·b#ãÇ7ãîiÊÎ‹ÚîiKXªƒÂ÷7ããÆ#îiÊ®Šznz+K»¾KÙ^[êîKúKº>zKˆî˜XŞ{ÚãÂö#ãÂöF—câr°¢sÆF—b7G–ÆSÒ&F—7Æ“¦fÆWƒ¶v£‡ƒ¶Ö&v–â×F÷£ƒ¶Æ–vâÖ—FV×3¦6VçFW"#âr°¢sÆ'WGFöâ6Æ73Ò&'FâöÆ–æRÖB"–CÒ'w„6†V6²#î‹ùîhê^j8kX³Âö'WGFöããÇ7â6Æ73Ò&×WFVB"–CÒ'w…&W2#ãÂ÷7ããÂöF—cãÂöF—câr°¢sÆF—b6Æ73Ò'6V2#îK‹®K¸K˜‹ù˜xÎk*iÈ[ÈX[3ÂöF—cãÆF—b6Æ73Ò&6&B"7G–ÆSÒ&Ö&v–âÖ&÷GFöÓ£G‚#âr°¢sÆF—b6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£ãWƒ¶Æ–æRÖ†V–v‡C£ãr#âr°¢~jÎ™Ú.Zê.h‹~zºş[{.{¸şXú®‹ùîiÊÎiË¢'&–îûÈÎˆÎ[êîKúj^hê^yJy¨NiŠşZè>ˆz®[{˜*>ZY~‹ùîhê^Y(ÎXzŞhÚî8"r°¢~YÊ‹ù˜xÎiK’.yJY:®XûZJ~ˆIY¹îzÙB.Xú®KÉ®iKX‹iÊÎiË®‹ùK»Ş˜XŞ{ÚîûÈÎZû[êîKújú¾iz[ÛY8Ò(	N(	Bh˜Kº^KˆŞX®8#Æ'#âr°¢~Kˆ¾Kˆ‹ÚîûÈ…‚ã.ûÈKÉ®h¨¢vT6†B'&–FvRX®h‰hù.K»nûÉ®xºÎz¸¾Y
şyJ‚şXÎyJ8XzŞhÚî™©Nzk¾8Xúş˜‹ùîhê^j^hê^iÈŞXªûÈÂr°¢~ZHŞyJxëYÊy¨Nhù.K»nzêynyXÎ™Ú.ûÈÎKˆŞŠ8^hù.K»ni{nKˆŞKÉ®Y
şXªK»¾KÙ^[êîKúK»¾Xª8#ÂöF—cãÂöF—câr°¢sÆF—b6Æ73Ò'6V2#îK¸ŞxKn[îK¨î[êîKúy¨N‹ùÎzˆ¾KéŞ‹YnûÈ…‚ã"‹øz{¾kˆ^XÙ^ûÈ“ÂöF—câr°¢sÆF—b6Æ73Ò&6&B#ãÇF&ÆR6Æ73Ò'F&Â#ãÇG#ãÇFƒîKéŞ‹YcÂ÷FƒãÇFƒîxëYÊ[Ù.‹Â÷FƒãÂ÷G#âr°¢°¢²~[êîKúy›¾[ÙRòiKnXùkhhòrÂwvV6†BÖ'&–FvRòxºÎz¸¾‹ù¾zˆ²uÒÀ¢²~{êNˆ¢™zjy¾ûÈ†vFRhù.K»nûÈ’rÂwvV6†BÖ'&–FvRö'&–â×ÇVv–âövFRç’uÒÀ¢²~[êîKúkŠ˜>K«®ŠëâòŠë[ønXˆnXË¢rÂ~j^hê^h˜‹ùîy¨B'&–âuÒÀ¢²~[êîKúkŠ˜>ŠûNŠùŞj>KØÒrÂ~j^hê^h˜‹ùîy¨B'&–îûÈ†fVGW&W5ö'•÷7&>ûÈ’uÒÀ¢²~K©zºò(i"iÊÎiË®y¨NZJ~ˆI‹ÚÎXùûÈ‡w5öf÷'v&Bòf÷'v&Bæ§>ûÈ’rÂ~j^hê^KˆîX[b'&–îûÈÎjÎ™Ú.zºşiÊÎ‹Úî[{.KˆŞXhŞY
şXª‚uÒÀ¢²~[êîKúkŠ˜>y¨N{¹şŠêKˆîXènXû"rÂ~j^hê^h˜‹ùîy¨B'&–âuÒÀ¢ÒæÖ‚…¶Â%Ò’ÓâsÇG#ãÇFCâr²W62†’²sÂ÷FCãÇFB6Æ73Ò&×WFVB#âr²W62†"’²sÂ÷FCãÂ÷G#âr’æ¦ö–â‚rr’°¢sÂ÷F&ÆSâr°¢sÆF—b6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£ãWƒ¶Ö&v–â×F÷£‡‚#î‹ùK©¾ˆ;ŞX©¾iÊÎ‹ÚîKˆŠÎk*XŠûÉ¾‹øz{¾X‹hù.K»ni{nhÈKˆ®Š˜	ši
ÎûÈÎjÎ™Ú.zºşKˆŞXø.Kˆî8#ÂöF—cãÂöF—câs° ¢bçVW'•6VÆV7F÷"‚r7w„6†V6²r’æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ7–æ2‚’Óâ°¢6öç7B"Ò–6²†v—BvWB‚rö’÷&÷WFRr’“°¢bçVW'•6VÆV7F÷"‚r7w…&W2r’çFW‡D6öçFVçBÒ"æö°¢ò~iÊÎiË¢'&–âXúş‹ëîûÈ[êîKúj^hê^iŠşY
nYÊ{«şŠh™zîZè>ˆz®[{ûÈ’p¢¢‡"æW'"ÇÂ"åöW'"ÇÂ~iÊÎiË¢'&–âKˆŞXúş‹ëâr“°¢Ò“°¢Ğ ¢6öç7BG&rÒ²6öæâÂÖöFVÂÂvV6†BÂFævW"Â&÷WBÓ°¢&–æDÆ’†7G‚çæVÂÂ†²Ââ’Óâ°¢ÖöFVÅ&VæFW$–B²³°¢7G‚çæVÂçVW'•6VÆV7F÷$ÆÂ‚ræÆ’r’æf÷$V6‚‚‡‚’Óâ‚æ6Æ74Æ—7BçFövvÆR‚vöârÂ‚ÓÓÒâ’“°¢G&u¶µÒbbG&u¶µÒ‚“°¢Ò“°¢òòšinY
şY	ZûÎ[É^ZûÎûÉ¦æ§2j8kX¾X‹.iÊÎYËZJ~ˆIYÊ‹yKØn‹ùk*˜XŞjŠYè²¶W’.i{nûÈÀ¢òòh¨¢7FFRç6WGF–æw57V"hÈ~X‹vÖöFVÂ~ûÈÎ‹ù¾iÚ^[y»Nhê^iŠşZ²¶W’y¨NŠXÙP¢6öç7BvçBÒ7FFRç6WGF–æw57V#°¢7FFRç6WGF–æw57V"Òrs°¢–b‡vçBbbG&u·vçEÒ’°¢ÖöFVÅ&VæFW$–B²³°¢7G‚çæVÂçVW'•6VÆV7F÷$ÆÂ‚ræÆ’r’æf÷$V6‚‚‡‚’Óâ‚æ6Æ74Æ—7BçFövvÆR‚vöârÂ‚æFF6WBæ²ÓÓÒvçB’“°¢G&u·vçEÒ‚“°¢&WGW&ã°¢Ğ¢ÖöFVÅ&VæFW$–B²³°¢6öæâ‚“°¢Ğ ¢7–æ2gVæ7F–öâ&VæFW%vÆÇW$W‡FVç6–öâ†7G‚’°¢7G‚çF÷&"æ6Æ74æÖRÒwF÷&"s°¢7G‚çF÷&"æ–ææW$…DÔÂÒsÆƒåvÆÇW"hš[SÂöƒâs°¢7G‚çf–Wræ–ææW$…DÔÂÒsÆF—b6Æ73Ò'w&#îjÚ>YÊŠû¾Xùnhš[^x«nh(
cÂöF—câs°¢6öç7B’Òv–æF÷rçf–v–ÃòçvÆÇW$W‡FVç6–öã°¢–b‚’’²7G‚çf–Wræ–ææW$…DÔÂÒsÆF—b6Æ73Ò'w&#îjÎ™Ú.hš[^zêynK¸^YÊ‚VÆV7G&öâZê.h‹~zºşXúşyJ8#ÂöF—câs²&WGW&ã²Ğ¢6öç7BG&rÒ7–æ2†ÖW76vRÒrr’Óâ°¢6öç7B2Òv—B’ç7FFR‚“°¢7G‚çf–Wræ–ææW$…DÔÂÒsÆF—b6Æ73Ò'w&6fr×w&#ãÇ6V7F–öâ6Æ73Ò'æVÂÖ6&B#ãÆƒ#åvÆÇW"Væv–æSÂöƒ#âr°¢sÇâr²W62†ÖW76vRÇÂ‡2æW'&÷"ÇÂ‡2æVæ&ÆVBò~[{.Y
şyJ‚r¢2æ–ç7FÆÆVBò~[{.ZèŠ8^ûÈÎ[Ù>X˜ŞXÎyJ‚r¢~iÊ®ZèŠ8Rr’’’²sÂ÷âr°¢sÇ6Æ73Ò&×WFVB#îhš[^zˆ¾[¨şKˆîyJh‹~i[hÚîXˆn[ÈKùŞZÙ8.zhyJh‰nXÛ‹ÛŞKˆŞKÉ®XŠ™šBW6W$FF÷vÆÇW"ÖVæv–æRKŠŞy¨N˜XŞ{ÚîY(Î{JiÙ8#Â÷âr°¢sÆF—b6Æ73Ò'&÷rÖ7F–öç2#ãÆ'WGFöâ6Æ73Ò&'Fâ&–Ö'’"–CÒ'vÆÇW$–ç7FÆÂ#âr²‡2æ–ç7FÆÆVBò~ZèŠ8^i»Nikr¢~˜hºhš[^XÈ^[›nZèŠ8Rr’²sÂö'WGFöãâr°¢‡2æVæ&ÆVBòsÆ'WGFöâ6Æ73Ò&'Fâ"–CÒ'vÆÇW$F—6&ÆR#îzhyJƒÂö'WGFöãâr¢sÆ'WGFöâ6Æ73Ò&'Fâ"–CÒ'vÆÇW$Væ&ÆR"r²‚2æ–ç7FÆÆVBòvF—6&ÆVBr¢rr’²sîY
şyJƒÂö'WGFöãâr’°¢‡2æ–ç7FÆÆVBòsÆ'WGFöâ6Æ73Ò&'FâöÆ–æR"–CÒ'vÆÇW%Væ–ç7FÆÂ#îXÛ‹ÛÓÂö'WGFöãâr¢rr’°¢sÂöF—cãÂ÷6V7F–öããÇ6Æ73Ò&×WFVB#îhš[^XÈ^™ÈyKf–v–ÂièN[»®[z^X[~yIşh‰ûÉ¾ZèŠ8^i{nKÉ®š¨ÎŠøÖæ–fW7N8XØşŠêî8iØ>™™Y(ÎjøşKŠ®ih~K»ny¨B4„Ó#Sn8#Â÷ãÂöF—câs°¢6öç7B–ç7FÆÂÒ7G‚çf–WrçVW'•6VÆV7F÷"‚r7vÆÇW$–ç7FÆÂr“°¢–b†–ç7FÆÂ’–ç7FÆÂæöæ6Æ–6²Ò7–æ2‚’Óâ°¢–ç7FÆÂæF—6&ÆVBÒG'VS°¢6öç7B&W7VÇBÒv—B’æ–ç7FÆÂ‚“°¢–b‡&W7VÇBæö²bb&W7VÇBæ6æ6VÆVB’²v—BG&r‡&W7VÇBæVæ&ÆVBò~hš[^[{.i»Nik[›n˜	®‹ø~X^[«~j8iú^8"r¢~hš[^[{.ZèŠ8^ûÉ¾˜hº(	ÎY
şyJ(	ŞYîh˜ŞKÉ®Y
şXªZ8{«iÈŞXª8"r“²–b‡&W7VÇBæVæ&ÆVB’Æö6F–öâç&VÆöB‚“²Ğ¢VÇ6R²–ç7FÆÂæF—6&ÆVBÒfÇ6S²–b‚&W7VÇBæ6æ6VÆVB’v—BG&r‡&W7VÇBæW'"ÇÂ~ZèŠ8^ZK‹JRr“²Ğ¢Ó°¢6öç7BVæ&ÆRÒ7G‚çf–WrçVW'•6VÆV7F÷"‚r7vÆÇW$Væ&ÆRr“°¢–b†Væ&ÆR’Væ&ÆRæöæ6Æ–6²Ò7–æ2‚’Óâ°¢Væ&ÆRæF—6&ÆVBÒG'VS°¢6öç7B&W7VÇBÒv—B’æVæ&ÆR‚“°¢–b‡&W7VÇBæö²’Æö6F–öâç&VÆöB‚“²VÇ6R²v—BG&r‡&W7VÇBæW'"ÇÂ~Y
şyJZK‹J^ûÈÎhš[^KùŞhÈXÎyJ‚r“²Ğ¢Ó°¢6öç7BF—6&ÆRÒ7G‚çf–WrçVW'•6VÆV7F÷"‚r7vÆÇW$F—6&ÆRr“°¢–b†F—6&ÆR’F—6&ÆRæöæ6Æ–6²Ò7–æ2‚’Óâ²6öç7B&W7VÇBÒv—B’æF—6&ÆR‚“²–b‡&W7VÇBæö²’Æö6F–öâç&VÆöB‚“²VÇ6Rv—BG&r‡&W7VÇBæW'"ÇÂ~zhyJZK‹JRr“²Ó°¢6öç7BVæ–ç7FÆÂÒ7G‚çf–WrçVW'•6VÆV7F÷"‚r7vÆÇW%Væ–ç7FÆÂr“°¢–b‡Væ–ç7FÆÂ’Væ–ç7FÆÂæöæ6Æ–6²Ò7–æ2‚’Óâ°¢–b‚v—B6öæf—&ÔF–Æör‚~XÛ‹ÛÒvÆÇW"hš[^ûÉşyJh‹~˜XŞ{ÚîY(Î{JiÙKÉ®KùŞyY8"r’’&WGW&ã°¢6öç7B&W7VÇBÒv—B’çVæ–ç7FÆÂ‚“²–b‡&W7VÇBæö²’Æö6F–öâç&VÆöB‚“²VÇ6Rv—BG&r‡&W7VÇBæW'"ÇÂ~XÛ‹ÛŞZK‹JRr“°¢Ó°¢Ó°¢v—BG&r‚“°¢Ğ ¢òò)Y)Y)Y’âX©şˆ;ŞXˆn{¸Nš^ûÈhÈX©şˆ;ŞXˆn{¾y¨Nˆ®YZ;>ûÈ)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y ¢òòšn˜:ZûÎˆŠ®K©NšûÉ£[z^KÙÎXû"ZûŠùŞKˆîK«®jÂ2ˆ;ŞX©¾Kˆîhš[RB‹ùîhê^Kˆî{;¾{¹òRi[hÚî8 ¢òòXéşiÈK™ŞKŠ®š^™Ú"¢®KˆKŠ®KˆŞ[	¢®ûÈÎXZ˜:hÈX©şˆ;Ş[Ù.XZ^Kˆ{¸NûÈÎKÙÎK‹®{¸NXh^X©şˆ;Şš¢òòûÈ[znjò*r{ÉnXûr²zØZëŞ‹zş[èN[îj~ûÈ8.YNk‹.iù>YšKˆŠÎKˆŞiKûÉ®{¹Zè>KºÎKˆKŠ ¢òò.XræVÂ.hÈ.XšşZûÎˆŠ®ûÈjŠ®hé.YÊXh^ZëKˆ®k+şûÈûÈÇf–WrxZ~iz~ûÈÇF÷&"yKZè>KºÎˆz®[{Xi8 ¢6öç7BeTä5ôu$õU2Ò°¢²–C¢wW'6öærÂæÖS¢~ZûŠùŞKˆîK«®jÂrÂçVÓ¢s"rÂ–6öã¢wW6W"rÂ—FV×3¢°¢²³¢wW'6öærÂçVÓ¢s#rÂæÖS¢~K«®jÎKˆîŠúŞk	BrÂFƒ¢r÷W'6öærÂ–6öã¢wW6W"rÂ&VæFW#¢&VæFW%W'6öæÒÀ¢²³¢vÖVÖ÷'’rÂçVÓ¢s#"rÂæÖS¢~Šë[ønKˆî{ªnZé¢rÂFƒ¢röÖVÖ÷'’rÂ–6öã¢v'&–ârÂ&VæFW#¢&VæFW$ÖVÖ÷'’ÒÀ¢²³¢v¶"rÂçVÓ¢s#2rÂæÖS¢~yú^Šøn[©2rÂFƒ¢rö¶"rÂ–6öã¢vÆ–"rÂ&VæFW#¢&VæFW$¶"ÒÀ¢ÒÒÀ¢²–C¢v&–Æ—G’rÂæÖS¢~ˆ;ŞX©¾Kˆîhš[RrÂçVÓ¢s2rÂ–6öã¢wW§¦ÆRrÂ—FV×3¢°¢²³¢v&–Æ—G’rÂçVÓ¢s3rÂæÖS¢~ˆ;ŞX©¾Kˆîh¨ˆ;ÒrÂFƒ¢rö&–Æ—F–W2rÂ–6öã¢wW§¦ÆRrÂ&VæFW#¢&VæFW$&–Æ—G’ÒÀ¢²³¢wFööÇ2rÂçVÓ¢s3"rÂæÖS¢~iÊÎYËi8ŞhêrrÂFƒ¢röÆö6ÂrÂ–6öã¢wFW&ÒrÂ&VæFW#¢&VæFW%FööÇ2ÒÀ¢²³¢vÆ–fRrÂçVÓ¢s32rÂæÖS¢~yIşkK¾™Ú.iÛòrÂFƒ¢röÆ–fRrÂ–6öã¢v†V'BrÂ&VæFW#¢&VæFW$Æ–fRÒÀ¢²³¢wÇVv–åöÖ&¶WBrÂçVÓ¢s3BrÂæÖS¢~hù.K»n[ˆ.YË¢rÂFƒ¢r÷ÇVv–ç2öÖ&¶WBrÂ–6öã¢wW§¦ÆRrÂ&VæFW#¢&VæFW%ÇVv–äÖ&¶WBÒÀ¢ÒÒÀ¢²–C¢w7—7FVÒrÂæÖS¢~‹ùîhê^Kˆî{;¾{¹òrÂçVÓ¢sBrÂ–6öã¢w6WBrÂ—FV×3¢°¢²³¢w6WGF–æw2rÂçVÓ¢sCrÂæÖS¢~‹ùîhê^KˆîŠëîZHrrÂFƒ¢rö6öææV7F–öârÂ–6öã¢w6WBrÂ&VæFW#¢&VæFW%6WGF–æw2ÒÀ¢²³¢vFW6·F÷öW‡FVç6–öç2rÂçVÓ¢sC"rÂæÖS¢~jÎ™Ú.hš[RrÂFƒ¢röW‡FVç6–öç2öFW6·F÷rÂ–6öã¢wW§¦ÆRrÂ&VæFW#¢&VæFW%vÆÇW$W‡FVç6–öâÒÀ¢ÒÒÀ¢Ó° ¢gVæ7F–öâÖ¶Tw&÷WvR†w&÷W’°¢&WGW&â7–æ2gVæ7F–öâ†7G‚’°¢6öç7B—FV×2Òw&÷Wæ—FV×3°¢ÆWB7W"Ò—FV×2æf–æB‚†—B’Óâ—Bæ²ÓÓÒ7FFRç6WGF–æw4w&÷W’ò7FFRç6WGF–æw4w&÷W¢—FV×5³Òæ³°¢7FFRç6WGF–æw4w&÷WÒrs° ¢òò[znjşûÉ®iÊÎ{¸NX©şˆ;Şkˆ^XÙ^ûÈŒ*r{ÉnXûr²Y»îjr²zØZëŞ‹zş[èN[îj~ûÈ¢7G‚çæVÂæ–ææW$…DÔÂÒsÆF—b6Æ73Ò'†VB#ãÆƒ#âr²W62†w&÷WææÖR’²sÂöƒ#ãÂöF—cãÆF—b6Æ73Ò'Æ—7B#âr°¢—FV×2æÖ‚†—B’Óà¢sÆF—b6Æ73Ò&Æ’"FFÖ³Ò"r²—Bæ²²r"F—FÆSÒ"r²W62†—BææÖR²rr²—BçF‚’²r#âr°¢sÇ7â6Æ73Ò&–2#âr²–6öâ†—Bæ–6öâÂw6Òr’²sÂ÷7ãâr°¢sÆF—b6Æ73Ò'G‚#ãÆ#âr²W62†—BææÖR’²sÂö#ãÂöF—câr°¢sÇ7â6Æ73Ò&ÖWFÖöæò#âr²—BçF‚²sÂ÷7ããÂöF—câr’æ¦ö–â‚rr’²sÂöF—câs° ¢6öç7B&öG’ÒRæVÂ‚sÆF—b6Æ73Ò'w&6fr×w&#ãÆF—b6Æ73Ò&6fr×7V&æb"–CÒ&6fu7V&æb"†–FFVããÂöF—cãÆF—b–CÒ&6ft&öG’#ãÂöF—cãÂöF—câr“°¢7G‚çf–Wræ–ææW$…DÔÂÒrs°¢7G‚çf–WræVæD6†–ÆB†&öG’“°¢6öç7B6ft&öG’Ò&öG’çVW'•6VÆV7F÷"‚r66ft&öG’r“°¢6öç7B7V&æbÒ&öG’çVW'•6VÆV7F÷"‚r66fu7V&æbr“° ¢7–æ2gVæ7F–öâG&u7V"†²’°¢6öç7B—BÒ—FV×2æf–æB‚‡‚’Óâ‚æ²ÓÓÒ²’ÇÂ—FV×5³Ó°¢7W"Ò—Bæ³°¢7G‚çæVÂçVW'•6VÆV7F÷$ÆÂ‚ræÆ’r’æf÷$V6‚‚†â’Óââæ6Æ74Æ—7BçFövvÆR‚vöârÂâæFF6WBæ²ÓÓÒ—Bæ²’“°¢6ft&öG’æ–ææW$…DÔÂÒrs°¢6ft&öG’æ6Æ74æÖRÒrs°¢&öG’æFF6WBç6V7F–öâÒ—Bæ³°¢7V&æbæ†–FFVâÒG'VS²7V&æbæ–ææW$…DÔÂÒrs°¢6öç7Bf¶UæVÂÒFö7VÖVçBæ7&VFTVÆVÖVçB‚vF—br“°¢G'’°¢v—B—Bç&VæFW"‡²f–Ws¢6ft&öG’ÂæVÃ¢f¶UæVÂÂF÷&#¢7G‚çF÷&"Â7&3¢7G‚ç7&2Â&Vg&W6ƒ¢‚’ÓâG&u7V"†—Bæ²’Ò“°¢Ò6F6‚†R’°¢6öç6öÆRæW'&÷"‚~X©şˆ;ŞXˆnXË®k‹.iù>ZK‹JS¢rÂR“°¢6ft&öG’æ–ææW$…DÔÂÒsÆF—b6Æ73Ò'w&#âr²W'$&÷‚‚~‹ùKˆXˆnXË®yK¾KˆŞX{®iÚRrÂRæÖW76vRÇÂrr’²sÂöF—câs°¢Ğ¢–b†f¶UæVÂæ–ææW$…DÔÂçG&–Ò‚’’²òòX©şˆ;Şšˆz®[Šny¨NXšşZûÎˆŠ®ûÈkŠ˜>Xˆ~hÚ"òhù.K»nXˆn{²ò‹ùîhê^XˆnXË®ûÈ¢f¶UæVÂæ6Æ74Æ—7BæFB‚v6fr×7V&æbÖ–ææW"r“°¢7V&æbæ†–FFVâÒfÇ6S°¢7V&æbæVæD6†–ÆB†f¶UæVÂ“°¢Ğ¢òòk‹.iù>YšKÉ®˜xŞXi’F÷&.ûÈÎh˜Kº^jøşjÊ˜;ŞŠ^ûÉ®ZëşK‹¾šR*r{ÉnXûr²X©şˆ;Şš™Ú.XÈ^[¢7G‚çF÷&"æ6Æ74Æ—7BæFB‚wF÷&"r“°¢7G‚çF÷&"çVW'•6VÆV7F÷$ÆÂ‚rçFçVÒÂæ7'VÖ"r’æf÷$V6‚‚†â’Óââç&VÖ÷fR‚’“°¢7G‚çF÷&"æ–ç6W'DF¦6VçD…DÔÂ‚vgFW&&Vv–ârÀ¢sÇ7â6Æ73Ò'FçVÒÖöæò#ì*rr²w&÷WæçVÒ²sÂ÷7ããÇ7â6Æ73Ò&7'VÖ"Ööæò#ì*rr°¢—BæçVÒ²r+rr²W62†w&÷WææÖR’²sÂ÷7ãâr“°¢Ğ ¢7G‚çæVÂçVW'•6VÆV7F÷$ÆÂ‚ræÆ•¶FFÖµÒr’æf÷$V6‚‚†â’Óà¢âæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚’ÓâG&u7V"†âæFF6WBæ²’’“°¢v—BG&u7V"†7W"“°¢Ó°¢Ğ ¢7–æ2gVæ7F–öâ&VæFW%6WGF–æw4†öÖR†7G‚’°¢7G‚çæVÂæ–ææW$…DÔÂÒrs°¢7G‚çF÷&"æ6Æ74æÖRÒwF÷&"6WGF–æw2Ö†öÖR×F÷&"s°¢7G‚çF÷&"æ–ææW$…DÔÂÒsÆƒîŠëî{ÚãÂöƒãÆF—b6Æ73Ò'7#ãÂöF—cãÆ'WGFöâ6Æ73Ò&'FâöÆ–æR"–CÒ'6WGF–æw4&6´6†B#î‹ùNY¹îˆ®ZJ“Âö'WGFöãâs°¢7G‚çF÷&"çVW'•6VÆV7F÷"‚r76WGF–æw4&6´6†Br’æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚’Óâ°¢7FFRç6WGF–æw4FWF–ÂÒfÇ6S°¢7G‚ææf–vFR‚v6†Br“°¢Ò“°¢6öç7B6&G2Ò°¢·F—FÆS¢~hù.K»nKˆîh¨ˆ;ÒrÂFW63¢~iú^yÈ¾[{.Y
şyJˆ;ŞX©¾Kˆîhù.K»n[ˆ.YË¢rÂ–6öã¢wW§¦ÆRrÂvS¢v&–Æ—G’rÂw&÷W¢wÇVv–åöÖ&¶WBwÒÀ¢·F—FÆS¢~ˆz®XªXÉnK»¾XªrÂFW63¢~h™>[ÈyIşkK¾™Ú.iÛşKˆîK»¾XªXZ^Xú2rÂ–6öã¢v7F—f—G’rÂvS¢v&–Æ—G’rÂw&÷W¢vÆ–fRwÒÀ¢·F—FÆS¢~[z^KÙÎXË¢rÂFW63¢~ˆ¨.x+8‹ùŠÎh8^Xk^Kˆîzêyn[z^X[rrÂ–6öã¢vÖöârÂvS¢vÖævVÖVçBwÒÀ¢·F—FÆS¢~iÊÎYËZJ~ˆIrÂFW63¢~™¨şXÈR'&–âx«nh8‹ùîhê^KˆîiØ>™™rÂ–6öã¢w6WBrÂvS¢w7—7FVÒrÂ7V#¢v6öæâwÒÀ¢·F—FÆS¢~Z8{«KˆîZInŠx"rÂFW63¢~Z8{«hš[^Kˆîi‹îzK®iXiéÂrÂ–6öã¢v–ÖrrÂvS¢w7—7FVÒrÂ7V#¢vFW6·F÷öW‡FVç6–öç2wÒÀ¢·F—FÆS¢~jÎ™Ú.hš[RrÂFW63¢~xºÎz¸¾ZèŠ8^8Y
şXÎY(Îi»NikjÎ™Ú.hš[RrÂ–6öã¢wW§¦ÆRrÂvS¢w7—7FVÒrÂ7V#¢vFW6·F÷öW‡FVç6–öç2wÒÀ¢·F—FÆS¢~jŠYè¾KˆîhêybrÂFW63¢~jŠYè¾iÈŞXªYXn8hêynKˆî[ú¾˜	şXˆ~hÚ"rÂ–6öã¢v'&–ârÂvS¢w7—7FVÒrÂ7V#¢vÖöFVÂwÒÀ¢·F—FÆS¢~iz^[ùrò‹>ŠùRrÂFW63¢~iú^yÈ¾‹ùŠÎi[hÚîY(ÎŠø®ijŞKúhòrÂ–6öã¢v6†'BrÂvS¢vFFwÒÀ¢·F—FÆS¢~X[>K¨ârÂFW63¢~x˜iÊÎ8i»NikKˆîšyºîKúhòrÂ–6öã¢w6WBrÂvS¢w7—7FVÒrÂ7V#¢v&÷WBwÒÀ¢Ó°¢7G‚çf–Wræ6Æ74æÖRÒw67&öÆÂ6WGF–æw2Ö†öÖR×f–Wrs°¢7G‚çf–Wræ–ææW$…DÔÂÒsÆF—b6Æ73Ò'6WGF–æw2Ö†öÖR#ãÆF—b6Æ73Ò'6WGF–æw2Ö–çG&ò#ãÇ7â6Æ73Ò&W–V'&÷r#åd”t”ÃÂ÷7ããÆƒ#îŠëî{ÚãÂöƒ#ãÇî[‹yJX©şˆ;ŞKˆî[©NyJ˜XŞ{ÚãÂ÷ãÂöF—cãÆF—b6Æ73Ò'6WGF–æw2Öw&–B#âr°¢6&G2æÖ‚†2Æ’’ÓâsÆ'WGFöâ6Æ73Ò'6WGF–æw2×F–ÆR"FF×6WGF–ærÖ–æFWƒÒ"r²’²r#ãÇ7â6Æ73Ò'6WGF–æw2×F–ÆRÖ–6öâ#âr²–6öâ†2æ–6öâÂvÖBr’²sÂ÷7ããÇ7ããÆ#âr²W62†2çF—FÆR’²sÂö#ãÇ6ÖÆÃâr²W62†2æFW62’²sÂ÷6ÖÆÃãÂ÷7ããÇ7â6Æ73Ò'6WGF–æw2×F–ÆRÖ'&÷r#î(£Â÷7ããÂö'WGFöãâr’æ¦ö–â‚rr’°¢sÂöF—cãÂöF—câs°¢7G‚çf–WrçVW'•6VÆV7F÷$ÆÂ‚u¶FF×6WGF–ærÖ–æFW…Òr’æf÷$V6‚‚†"’Óâ"æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚’Óâ°¢6öç7B—FVÒÒ6&G5´çVÖ&W"†"æFF6WBç6WGF–æt–æFW‚•Ó°¢7FFRç6WGF–æw4FWF–ÂÒG'VS°¢7FFRç6WGF–æw4w&÷WÒ—FVÒæw&÷WÇÂrs°¢7FFRç6WGF–æw57V"Ò—FVÒç7V"ÇÂrs°¢7G‚ææf–vFR†—FVÒçvR“°¢Ò’“°¢Ğ ¢òò)Y)Y)Y{¹şKˆzêyb)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y)Y ¢ÆWBÖv×EF–ÖW"ÒçVÆÃ°¢7–æ2gVæ7F–öâ&VæFW$ÖævVÖVçB†7G‚’°¢7G‚çF÷&"æ6Æ74æÖRÒwF÷&"s°¢7G‚çf–Wræ6Æ74æÖRÒw67&öÆÂs°¢7G‚çæVÂæ–ææW$…DÔÂÒsÆF—b6Æ73Ò'†VB#ãÆƒ#î{¹şKˆzêycÂöƒ#ãÂöF—câr°¢sÆF—b6Æ73Ò'Æ—7B#âr°¢Æ’‡²–6öã¢vÖöârÂF—FÆS¢~ˆ¨.x+jh.Šx‚rÂ7V#¢~h˜iÈiÈŞXªYšKˆîiÊÎYËZéîKè²rÂöã¢G'VRÂGG'3¢rFFÖ³Ò&÷b"rÒ’°¢Æ’‡²–6öã¢v&öö²rÂF—FÆS¢~˜XŞ{ÚîŠûNiˆârÂ7V#¢~Zh.KÙ^hê^XZ^ikˆ¨.x+’rÂGG'3¢rFFÖ³Ò&Fö2"rÒ’°¢sÂöF—câs°¢7G‚çF÷&"æ–ææW$…DÔÂÒF÷&"‚~ˆ¨.x+jh.Šx‚rÂrrÀ¢sÆ'WGFöâ6Æ73Ò&'FâöÆ–æR"–CÒ&Öu&R#âr²–6öâ‚w&Vg&W6‚rÂw6Òr’²~X‹~ikÂö'WGFöãâr“°¢7G‚çf–Wræ–ææW$…DÔÂÒsÆF—b6Æ73Ò'w&"–CÒ&Öuf–Wr#ãÆF—b6Æ73Ò&×WFVB"7G–ÆSÒ'FF–æs£#‚#îXª‹ÛŞKŠŞ(
cÂöF—cãÂöF—câs°¢6öç7BbÒ7G‚çf–WrçVW'•6VÆV7F÷"‚r6Öuf–Wrr“°¢6öç7B4"Ò²F–ÖV÷WD×3¢ƒÓ²òò{¹şKˆzêyni[hÚîYÊiÊÎiË¢'&–îûÉ¾ZK‹J^i{n[Ş[ú¾‹ÚÎK‹®iˆîzî™IŠúşx«nh ¢6öç7B7FGW46öÆ÷"Ò‡2’Óâ2ÓÓÒvöæÆ–æRròvö²r¢2ÓÓÒvFVÆ–VBròwv&âr¢vW'"s°¢6öç7B7FGW5FW‡BÒ‡2’Óâ2ÓÓÒvöæÆ–æRrò~YÊ{«òr¢2ÓÓÒvFVÆ–VBrò~[»n‹ùòr¢~zk¾{«òs°¢6öç7B&"Ò†Æ&VÂÂfÂ’Óâ°¢6öç7B7BÒfÂÒçVÆÂòÖF‚æÖ‚ƒÂÖF‚æÖ–âƒÂfÂ’’¢°¢6öç7B6öÆ÷"Ò7BâƒRòwf"‚ÒÖW'"’r¢7BâcRòwf"‚Ò×v&â’r¢wf"‚ÒÖö²’s°¢&WGW&âsÆF—b6Æ73Ò&·b"7G–ÆSÒ&Ö&v–ã£G‚#ãÇ7ãâr²W62†Æ&VÂ’²sÂ÷7ãâr°¢sÆ"7G–ÆSÒ&Ö–â×v–GFƒ£C'ƒ·FW‡BÖÆ–vã§&–v‡B#âr²‡fÂÒçVÆÂòfÂçFôf—†VBƒ’²rRr¢~(	Br’²sÂö#ãÂöF—câr°¢sÆF—b7G–ÆSÒ&†V–v‡C£Gƒ¶&6¶w&÷VæC§f"‚ÒÖÆ–æR“¶&÷&FW"×&F—W3£'ƒ¶÷fW&fÆ÷s¦†–FFVã¶Ö&v–âÖ&÷GFöÓ£g‚#âr°¢sÆF—b7G–ÆSÒ&†V–v‡C£S·v–GFƒ¢r²7B²rS¶&6¶w&÷VæC¢r²6öÆ÷"²s·G&ç6—F–öã§v–GF‚ã72#ãÂöF—cãÂöF—câs°¢Ó°¢6öç7BæöFT6&BÒ†â’Óâ°¢6öç7BÒÒâæÖWG&–72ÇÂ·Ó°¢6öç7BvVçG2ÒâævVçG2ÇÂµÓ°¢&WGW&âsÆF—b6Æ73Ò&6&B"7G–ÆSÒ&Ö&v–âÖ&÷GFöÓ£'‚#âr°¢sÆF—b7G–ÆSÒ&F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£ƒ¶Ö&v–âÖ&÷GFöÓ£‚#âr°¢–6öâ‚vÖöârÂvÖBr’°¢sÆF—b7G–ÆSÒ&fÆWƒ£#ãÆ"7G–ÆSÒ&föçB×6—¦S£G‚#âr²W62†âææÖRÇÂâæ–B’²sÂö#âr°¢sÆF—b6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£ãWƒ¶Ö&v–â×F÷£'‚#âr°¢W62†âæ–B’²†âæ†÷7FæÖRòr+rr²W62†âæ†÷7FæÖR’¢rr’²†âçÆFf÷&Òòr+rr²W62†âçÆFf÷&Ò’¢rr’°¢sÂöF—cãÂöF—câr°¢Fr‡7FGW46öÆ÷"†âç7FGW2’Â7FGW5FW‡B†âç7FGW2’’°¢sÂöF—câr°¢&"‚t5RrÂÒæ7U÷W&6VçB’²&"‚~Xh^ZÙ‚rÂÒæÖVÖ÷'•÷W&6VçB’²&"‚~z8y¹‚rÂÒæF—6µ÷W&6VçB’°¢sÆF—b6Æ73Ò&·b"7G–ÆSÒ&Ö&v–â×F÷£‡‚#ãÇ7ãîiÈYî[ø>‹{3Â÷7ãâr°¢sÆ"6Æ73Ò&Ööæò#âr²†âæÆ7E÷6VVâòf×Dvò†âæÆ7E÷6VVâ’¢~K¸îiÊ¢r’²sÂö#ãÂöF—câr°¢†vVçG2æÆVæwF‚òsÆF—b6Æ73Ò&·b"7G–ÆSÒ&Ö&v–â×F÷£g‚#ãÇ7ãävVçCÂ÷7ããÆ#âr²vVçG2æÆVæwF‚²rKŠ®‹ùŠÎKŠÓÂö#ãÂöF—câr¢rr’°¢sÂöF—câs°¢Ó° ¢7–æ2gVæ7F–öâ÷b‚’°¢7G‚çF÷&"çVW'•6VÆV7F÷"‚vƒr’çFW‡D6öçFVçBÒ~ˆ¨.x+jh.Šx‚s°¢bæ–ææW$…DÔÂÒsÆF—b6Æ73Ò&×WFVB"7G–ÆSÒ'FF–æs£#‚#îXª‹ÛŞKŠŞ(
cÂöF—câs°¢6öç7B"Ò–6²†v—BvWB‚rö’öÖævVÖVçBö÷fW'f–WrrÂ4"’“°¢–b‚"ÇÂ"æö²’°¢bæ–ææW$…DÔÂÒsÆF—b6Æ73Ò'w&#âr²V×G’‚~zêynhê^Xú>KˆŞXúş‹ëârÀ¢‡"bb‡"åöW'"ÇÂ‡"å÷7FGW2òt…EEr²"å÷7FGW2¢rr’’’ÇÂ~Šû~zîŠêNZJ~ˆI[{.Y
şXªK‰NzêynjŠYÙ~[{.Xª‹ÛÒr’²sÂöF—câs°¢&WGW&ã°¢Ğ¢6öç7BæöFW2Ò"ææöFW2ÇÂµÓ°¢6öç7BöæÆ–æRÒæöFW2æf–ÇFW"†âÓââç7FGW2ÓÓÒvöæÆ–æRr’æÆVæwFƒ°¢6öç7BFVÆ–VBÒæöFW2æf–ÇFW"†âÓââç7FGW2ÓÓÒvFVÆ–VBr’æÆVæwFƒ°¢6öç7BöffÆ–æRÒæöFW2æf–ÇFW"†âÓââç7FGW2ÓÓÒvöffÆ–æRr’æÆVæwFƒ°¢bæ–ææW$…DÔÂÒsÆF—b6Æ73Ò&w&–BsB"7G–ÆSÒ&Ö&v–âÖ&÷GFöÓ£g‚#âr°¢sÆF—b6Æ73Ò&6&B·’#ãÆF—b6Æ73Ò&Æ"#îh¾ˆ¨.x+“ÂöF—cãÆF—b6Æ73Ò'b#âr²æöFW2æÆVæwF‚²sÂöF—cãÂöF—câr°¢sÆF—b6Æ73Ò&6&B·’#ãÆF—b6Æ73Ò&Æ"#îYÊ{«óÂöF—cãÆF—b6Æ73Ò'b"7G–ÆSÒ&6öÆ÷#§f"‚ÒÖö²’#âr²öæÆ–æR²sÂöF—cãÂöF—câr°¢sÆF—b6Æ73Ò&6&B·’#ãÆF—b6Æ73Ò&Æ"#î[»n‹ùóÂöF—cãÆF—b6Æ73Ò'b"7G–ÆSÒ&6öÆ÷#§f"‚Ò×v&â’#âr²FVÆ–VB²sÂöF—cãÂöF—câr°¢sÆF—b6Æ73Ò&6&B·’#ãÆF—b6Æ73Ò&Æ"#îzk¾{«óÂöF—cãÆF—b6Æ73Ò'b"7G–ÆSÒ&6öÆ÷#§f"‚ÒÖW'"’#âr²öffÆ–æR²sÂöF—cãÂöF—câr°¢sÂöF—câr°¢†æöFW2æÆVæwF‚òæöFW2æÖ†æöFT6&B’æ¦ö–â‚rr’ ¢sÆF—b6Æ73Ò&6&B"7G–ÆSÒ'FF–æs£#Gƒ·FW‡BÖÆ–vã¦6VçFW"#âr°¢–6öâ‚vÖöârÂvÆrr’²sÆF—b7G–ÆSÒ&Ö&v–â×F÷£ƒ¶föçB×6—¦S£2ãW‚#î‹ùk*iÈhê^XZ^K»¾KÙ^ˆ¨.x+“ÂöF—câr°¢sÆF—b6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£'ƒ¶Ö&v–â×F÷£g‚#îYÊ8Î˜XŞ{ÚîŠûNiˆî8Ş˜xÎiú^yÈ¾Zh.KÙ^hê^XZ^K©zºşiÈŞXªYš‚"h‰niÊÎYËyK^ˆIÂöF—câr°¢sÂöF—câr“°¢Ğ ¢gVæ7F–öâFö2‚’°¢7G‚çF÷&"çVW'•6VÆV7F÷"‚vƒr’çFW‡D6öçFVçBÒ~˜XŞ{ÚîŠûNiˆâs°¢bæ–ææW$…DÔÂÒsÆF—b6Æ73Ò&6&B"7G–ÆSÒ'FF–æs£‡‚#‚#âr°¢sÆ"7G–ÆSÒ&föçB×6—¦S£G‚#îhê^XZ^ikˆ¨.x+ûÈƒ2jÚ^ûÈ“Âö#âr°¢sÆF—b7G–ÆSÒ&Ö&v–â×F÷£Gƒ¶Æ–æRÖ†V–v‡C£ã“¶föçB×6—¦S£7‚#âr°¢sÆF—cãÆ#ãâzêynKŠŞ[ø>ûÈK©zºşiÈŞXªYš‚ûÈ˜XŞ{ÚîKºNx˜ÃÂö#ãÂöF—câr°¢sÆF—b6Æ73Ò&Ööæò"7G–ÆSÒ&&6¶w&÷VæC§f"‚ÒÖ&s"“·FF–æs£‡‚'ƒ¶&÷&FW"×&F—W3£gƒ¶Ö&v–ã£g‚¶föçB×6—¦S£'‚#ç6WBd”t”ÅôÔätTÔTåEôtTåEõDô´Tå3×²'6W'fW"Ö"#¢.™¨şiË®™[şKºNx˜Â"Â&Æö6Â×2#¢.XúnKˆKŠ®KºNx˜Â'ÓÆ'#ç6WBd”t”ÅôÔätTÔTåEôtTåEôäÔU3×²'6W'fW"Ö"#¢.K©zºşiÈŞXªYš‚""Â&Æö6Â×2#¢.iÊÎYËyK^ˆI'ÓÂöF—câr°¢sÆF—cãÆ#ã"âŠ*¾zêynˆ¨.x+‹ùŠÂvVçCÂö#ãÂöF—câr°¢sÆF—b6Æ73Ò&Ööæò"7G–ÆSÒ&&6¶w&÷VæC§f"‚ÒÖ&s"“·FF–æs£‡‚'ƒ¶&÷&FW"×&F—W3£gƒ¶Ö&v–ã£g‚¶föçB×6—¦S£'‚#ç6WBd”t”ÅôÔätTÔTåEõU$ÃÖ‡GG¢òşzêynKŠŞ[ø>YËYØ£ƒsƒƒÆ'#ç6WBd”t”ÅôÔätTÔTåEôäôDUô”C×6W'fW"Ö#Æ'#ç6WBd”t”ÅôÔätTÔTåEõDô´TãŞ™¨şiË®™[şKºNx˜ÃÆ'#ç—F†öâ'&–âöÖævVÖVçEövVçBç“ÂöF—câr°¢sÆF—cãÆ#ã2âY¹îX‹ˆ¨.x+jh.Šxiú^yÈ¾ˆ¨.x+x«nhÂö#îûÈjøòRzy.ˆz®XªX‹~ikûÈûÉ´vVçBx«nhKˆîK»¾Xª{¹şKˆYÊ8ÄvVçB[z^KÙÎZêN8Şiú^yÈ¾8#ÂöF—câr°¢sÂöF—câr°¢sÆF—b6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£'ƒ¶Ö&v–â×F÷£G‚#î[ø>‹{>™{N™©BRzy"+rCRzy.Xh^YÊ{«ò+r“zy.Xh^[»n‹ùò+r‹h^‹ø~zk¾{«óÂöF—câr°¢sÂöF—câs°¢Ğ ¢6öç7BG&rÒ†²’Óâ²–b†²ÓÓÒv÷br’÷b‚“²VÇ6RFö2‚“²Ó°¢7G‚çæVÂçVW'•6VÆV7F÷$ÆÂ‚ræÆ•¶FFÖµÒr’æf÷$V6‚†âÓà¢âæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚’Óâ°¢7G‚çæVÂçVW'•6VÆV7F÷$ÆÂ‚ræÆ’r’æf÷$V6‚‡‚Óâ‚æ6Æ74Æ—7Bç&VÖ÷fR‚vöâr’“°¢âæ6Æ74Æ—7BæFB‚vöâr“°¢G&r†âæFF6WBæ²“°¢Ò’“°¢7G‚çF÷&"çVW'•6VÆV7F÷"‚r6Öu&Rr’æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ‚’ÓâG&r€¢7G‚çæVÂçVW'•6VÆV7F÷"‚ræÆ’æöâr“òæFF6WBæ²ÇÂv÷br’“° ¢òòˆz®XªX‹~ikûÉ®Xú®YÊjh.Šxš^jøòRzy.X‹~KˆjÊûÈÎXˆ~‹[[XÀ¢6ÆV$–çFW'fÂ†Öv×EF–ÖW"“°¢Öv×EF–ÖW"Ò6WD–çFW'fÂ‚‚’Óâ°¢6öç7B7F—fRÒ7G‚çæVÂçVW'•6VÆV7F÷"‚ræÆ’æöâr“òæFF6WBæ³°¢–b†7F—fRÓÓÒv÷br’÷b‚“°¢ÒÂS“° ¢v—B÷b‚“°¢Ğ ¢òò)H)Hhù.K»n[ˆ.YË¢)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H)H ¢òò)Y)Y)Y7&VF÷"ÖöF^ûÈ„ÕeÓûÈûÉ®ˆz®xKnŠúŞŠˆˆØz‹ò²™Ùhš¨ÎŠø)Y)Y)Y ¢òò‹ëyXÎûÈKˆâ'&–âö7&VF÷"ç’y¨N{ªnZé®KˆKˆZû[©NûÈûÉ ¢òòÒyIşh‰‹['&–ây¨NyÉşZéîjŠYè¾™;î‹zşûÈ‚ö’ö7&VF÷"öG&gNûÈûÈÎk*iÈX~Š8^yIşh‰y¨Nix‹zşûÉ°¢òòÒˆØz‹ş‰Ò'&–ây¨NxºÎz¸¾[z^KÙÎXË®ûÈÎKˆŞz+jÚ>[Èşhù.K»bşh¨ˆ;Şyºî[Ù^ûÉ°¢òòÒ™Ùhš¨ÎŠøZHŞyJ‚Bj8iú^YšûÈÎXú®iú^{¹>ièNKˆŞhš~ŠÎKº>zûÉ°¢òòÒÕeÓ"YÊ™Ùhš¨ÎŠø˜	®‹ø~Yî[^zK®K«®[z^ZêjKˆîKˆjÊh
~Xù~hê~ZèŠ8^ûÉ¾yIşh‰ZèÎh‰KˆŞKÉ®ˆz®XªZèŠ8^8 ¢òò)Y)Y)Y7&VF÷"ÖöF^ûÉ®ˆØz‹şXÚûÈˆ®ZJš^Xh^X‰¾KÙÂ²ˆØz‹şX‰~ŠX[yJûÈ)Y)Y)Y)Y)Y)Y)Y ¢òòXZ^Xú>YÊK‹¾š^ZûŠùŞûÉ¦6ö×÷6W"y¨N8ÎX‰¾KÙÎ8Ö6†—Xˆ~X‹X‰¾KÙÎjŠ[ÈşYîûÈÎy»Nhê^h¨®™Èk ¢òò[Ù>khhşXù{¹’vVçNûÈÎY¹îiÚ^y¨N[iŠşKˆKŠ®ˆØz‹şXÚ8.‹ù˜xÎXú®‹Iş‹J2.KˆKŠ®ˆØz‹şhîK˜yK¾8¢òòXÚKˆ®XzKŠ®hÈ™*îhîK˜{¹.ûÈÎKˆŞzêXZ^Xú>YÊY:®8 ¢gVæ7F–öâ7&VF÷$fVæ6Tf÷"‡2’°¢6öç7BÒÒ7G&–ær‡2’æÖF6‚‚ö³2ÇÒör“°¢ÆWBâÒ3°¢–b†Ò’âÒÖF‚æÖ‚æÇ’†çVÆÂÂÒæÖ‚‡‚’Óâ‚æÆVæwF‚’’²°¢&WGW&âvrç&WVB†â“°¢Ğ¢gVæ7F–öâ7&VF÷$Æætöb†æÖR’°¢&WGW&âõÂâ‡’’BòçFW7B†æÖR’òw—F†öâr¢õÂâ†§7ÆÖ§7Æ6§2’BòçFW7B†æÖR’òv¦f67&—Br¢v§6öâs°¢Ğ¢gVæ7F–öâ7&VF÷%F&vWDöb†B’°¢6öç7Bg2ÒBæf–ÆW2ÇÂµÓ°¢6öç7BÖbÒg2æf–æB‚†b’ÓâbææÖRçFôÆ÷vW$66R‚’ÓÓÒw6¶–ÆÂæ§6öârÇÂbææÖRçFôÆ÷vW$66R‚’æVæG5v—F‚‚r÷§‡ÇVv–âæ§6öâr’ÇÂbææÖRçFôÆ÷vW$66R‚’ÓÓÒw§‡ÇVv–âæ§6öâr“°¢–b†Öb’°¢G'’°¢6öç7B¢Ò¥4ôâç'6R†Öbæ6öçFVçBÇÂw·Òr“°¢–b†Bæ¶–æBÓÓÒw6¶–ÆÂrbb¢æ–B’&WGW&â7G&–ær†¢æ–B“°¢–b†Bæ¶–æBÓÓÒwÇVv–ârbb¢ææÖR’&WGW&â7G&–ær†¢ææÖR“°¢Ò6F6‚…ò’²ò¢fÆ–FF–öâ&W7VÇB6'&–W2ÖÆf÷&ÖVBÖæ–fW7BFWF–Ç2¢òĞ¢Ğ¢6öç7B’Òg2æf–æB‚†b’ÓâõÂç’Bö’çFW7B†bææÖR’“°¢–b‡’bbõÂõõö–æ—EõõÂç’Bö’çFW7B‡’ææÖR’’&WGW&â’ææÖRç7Æ—B‚ròr•³Ó°¢&WGW&â’ò’ææÖRç&WÆ6R‚õÂç’Bö’Ârr’¢†Bæ¶–æBÓÓÒw6¶–ÆÂrò~ûÈyK6¶–ÆÂæ§6öâXk>Zé®ûÈ’r¢~ûÈyKhù.K»nih~K»nYŞXk>Zé®ûÈ’r“°¢Ğ¢òò¶VWG&ç7÷'Bf–ÇW&W2f—6–&ÆS¢’ç–6²7F÷&W2æöâÔ¥4ôâf–ÇW&W2–âöW'"À¢òòæ÷BW'"â–æ6ÇVFR7FGW2æBF†RÇ&VG’×6æ—F—¦VB&WVW7B÷&–v–âv†Vâf–Æ&ÆRà¢gVæ7F–öâ7&VF÷$7F–öäW'&÷"‡"ÂfÆÆ&6²’°¢6öç7BFWF–ÂÒ7G&–ær‡"bb‡"æW'"ÇÂ‡"æ–ç7FÆÂbb"æ–ç7FÆÂæW'"’ÇÂ"åöW'"ÇÂ"çFW‡B’ÇÂrr’çG&–Ò‚“°¢6öç7B7FGW2ÒçVÖ&W"‡"bb‡"å÷7FGW2ÇÂ"ç7FGW2’’ÇÂ°¢6öç7BF&vWBÒ7G&–ær‡"bb‡"å÷F&vWBÇÂ"çF&vWB’ÇÂrr’çG&–Ò‚“°¢6öç7B'G2ÒµÓ°¢–b‡7FGW2â’'G2çW6‚‚t…EEr²7FGW2“°¢–b‡F&vWB’'G2çW6‚‡F&vWB“°¢–b†FWF–Â’'G2çW6‚†FWF–Â“°¢&WGW&â7G&–ær†fÆÆ&6²ÇÂ~i8ŞKÙÎZK‹JRr’²~ûÉ¢r²‡'G2æ¦ö–â‚r+rr’ÇÂ~iÊ®yú^™IŠúòr“°¢Ğ¢òòKˆiÚˆØz‹şy¨NZèÎi[NXÚx˜~ûÈˆ®ZJk	Nk:[Ú.hûÉ®ZIn["çGW&âòæ&GûÈÎXh^˜:ZHŞyJ‚æ6&NûÈ¢gVæ7F–öâG&gD6&D‡FÖÂ†B’°¢6öç7BbÒBçfÆ–FF–öâÇÂ·Ó°¢6öç7BW''2ÒbæW'&÷'2ÇÂµÓ°¢6öç7Bv&ç2Òbçv&æ–æw2ÇÂµÓ°¢6öç7Bö´&FvRÒbæö²òFr‚vö²rÂ~˜	®‹ørr’¢Fr‚vW'"rÂ~iÊ®˜	®‹ørr²W''2æÆVæwF‚“°¢6öç7BW'$‡FÖÂÒW''2æÆVæwF€¢òsÇVÂ7G–ÆSÒ&Ö&v–ã£‡‚·FF–ærÖÆVgC£#‚#âr²W''2æÖ‚†R’Óà¢sÆÆ“ãÆ#âr²W62†Ræ6öFRÇÂrr’²sÂö#îûÉ¢r²W62†RæÖW76vRÇÂrr’²sÂöÆ“âr’æ¦ö–â‚rr’²sÂ÷VÃâp¢¢rs°¢6öç7Bv&ä‡FÖÂÒv&ç2æÆVæwF€¢òsÇVÂ7G–ÆSÒ&Ö&v–ã£‡‚·FF–ærÖÆVgC£#‚"6Æ73Ò&×WFVB#âr²v&ç2æÖ‚‡r’Óà¢sÆÆ“âr²W62‡ræ6öFRÇÂrr’²~ûÉ¢r²W62‡ræÖW76vRÇÂrr’²sÂöÆ“âr’æ¦ö–â‚rr’²sÂ÷VÃâp¢¢rs°¢&WGW&âsÆF—b6Æ73Ò'GW&â7'GW&â"FFÖ7&–CÒ"r²W62†Bæ–B’²r#ãÆF—b6Æ73Ò&&G’#âr°¢sÆF—b6Æ73Ò&6&B#âr°¢sÆF—b7G–ÆSÒ&F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£‡ƒ¶fÆW‚×w&§w&#âr°¢sÇ7â6Æ73Ò'Fr#âr²W62†Bæ¶–æBÓÓÒw6¶–ÆÂrò~h¨ˆ;Òr¢~hù.K»br’²sÂ÷7ãâr°¢sÇ7G&öæsâr²W62†Bç7VÖÖ'’ÇÂ~iÊ®YŞYŞˆØz‹òr’²sÂ÷7G&öæsâr°¢sÇ7â6Æ73Ò&×WFVBÖöæò"7G–ÆSÒ&föçB×6—¦S£'‚#âr²W62†Bæ–B’²r+rbr²W62…7G&–ær†BçfW'6–öâ’’²sÂ÷7ãâr°¢sÇ7â6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£'‚#îjŠYè¾ûÉ¢r²W62†BæÖöFVÂÇÂsòr’²sÂ÷7ãâr°¢sÆF—b7G–ÆSÒ&fÆWƒ£#ãÂöF—câr°¢sÆ'WGFöâ6Æ73Ò&'Fâv†÷7B"–CÒ&7%&WfÂ#î˜xŞikš¨ÎŠøÂö'WGFöãâr°¢‡bæö²òsÆ'WGFöâ6Æ73Ò&'Fâ"–CÒ&7%&Wf–Wr"&–ÖW‡æFVCÒ&fÇ6R#îiú^yÈ¾ZèŠ8^jÚ^šªCÂö'WGFöãâr¢rr’°¢sÆ'WGFöâ6Æ73Ò&'FâFævW""–CÒ&7$FVÂ#îXŠ™šNˆØz‹óÂö'WGFöãâr°¢sÂöF—câr°¢sÇ6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£'ƒ¶Ö&v–ã£‡‚#î™Èk.ûÉ¢r²W62†Bç&WV—&VÖVçBÇÂrr’²sÂ÷âr°¢sÇ7G–ÆSÒ&Ö&v–ã£‡‚#ãÆ#îyJ˜	NûÉ£Âö#âr²W62†Bç7VÖÖ'’ÇÂ~ûÈjŠYè¾k*{¹ûÈ’r’²sÂ÷âr°¢sÇ7G–ÆSÒ&Ö&v–ã£G‚#ãÆ#îš8î™šKˆî™™X‹nûÉ£Âö#âr²W62†Bç&—6·2ÇÂ~izr’²sÂ÷âr°¢sÇ6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£'ƒ¶Ö&v–ã£‡‚#âr°¢~™Ùhš¨ÎŠøj8iú^hù.K»n{¹>ièN8Öæ–fW7BKˆîˆ®ZJ[z^X[~ZY{ªnûÈÎKˆŞhš~ŠÎyIşh‰y¨NKº>zûÉ²r°¢sÆ#î˜	®‹ør(šk)zëZèXZƒÂö#î8.ZèŠ8^X˜ŞK¸Ş™ÈK«®[z^™ˆ^Šû¾Kˆ®™Ú.y¨NKº>z8#Â÷âr°¢sÂöF—câr°¢sÆF—b6Æ73Ò&6&B"7G–ÆSÒ&Ö&v–â×F÷£‡‚#âr°¢sÆF—b6Æ73Ò'¶rÖ‚#î™Ùhš¨ÎŠør²ö´&FvR²sÂöF—câr°¢W'$‡FÖÂ²v&ä‡FÖÂ°¢‚bæö²òsÇ6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£'ƒ¶Ö&v–â×F÷£‡‚#îš¨ÎŠøiÊ®˜	®‹ø~ûÈÎZèŠ8^jÚ^šªNi¨.KˆŞXúşyJ8.Šû~XXKúîZHŞKˆ®™Ú.y¨N™IŠúşûÈÎXhŞx+X{¾(	Î˜xŞikš¨ÎŠø(	Ş8#Â÷âr¢rr’°¢‚W''2æÆVæwF‚bbv&ç2æÆVæwF‚òsÇ6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£'‚#îk*iÈ™IŠúşh‰nŠÚnY®8#Â÷âr¢rr’°¢sÂöF—câr°¢‡bæö²òsÆF—b6Æ73Ò&7"×&Wf–WrÖG&vW""–CÒ&7%&Wf–Wt&÷‚"&öÆSÒ'&Vv–öâ"&–ÖÆ&VÃÒ.ZèŠ8^jÚ^šªNKˆîK«®[z^Zêj‚"†–FFVãâr°¢sÆF—b6Æ73Ò&7"×&Wf–WrÖ†VB#ãÆF—b6Æ73Ò'¶rÖ‚#îZèŠ8^X˜ŞK«®[z^ZêjƒÂöF—câr°¢sÆ'WGFöâ6Æ73Ò&'Fâv†÷7B"–CÒ&7$6æ6VÂ"G—SÒ&'WGFöâ"&–ÖÆ&VÃÒ.X[>™zŞZèŠ8^jÚ^šªB#îX[>™zÓÂö'WGFöããÂöF—câr°¢sÇ7G–ÆSÒ&Ö&v–ã£g‚#ãÆ#îZèŠ8^yºîj~ûÉ£Âö#ãÇ7â–CÒ&7%F&vWB#âr²W62†7&VF÷%F&vWDöb†B’’²sÂ÷7ããÂ÷âr°¢sÇ6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£'ƒ¶Ö&v–ã£g‚#î[nKÛşyJxëiÈ’r²†Bæ¶–æBÓÓÒw6¶–ÆÂròu6¶–ÆÂ†÷7Br¢uÇVv–âr’°¢rK¨¾XªZèŠ8^8.™Ùhš¨ÎŠøKˆŞKÉ®hš~ŠÎyIşh‰Kº>zûÉ¾h¨ˆ;Şx:Ş˜xŞ‹ÛŞXúşˆ;Şz¸¾XÛ>Xª‹ÛŞXZ^Xú>Kº>zûÈÎŠû~zîŠêNš8î™šŠûNiˆîY(ÎZèÎi[NKº>zYîXhŞh›Xxn8#Â÷âr°¢sÆF—b–CÒ&7$&÷fÅ7FFR"6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£'ƒ¶Ö&v–ã£g‚#î[	®iÊ®h›Xxn8.Zêh›iÈiXiÉòXˆn™)şûÈÎK‰NXú®ˆ;ŞKÛşyJKˆjÊ8#ÂöF—câr°¢sÆF—b7G–ÆSÒ&F—7Æ“¦fÆWƒ¶v£‡ƒ¶Æ–vâÖ—FV×3¦6VçFW#¶Ö&v–â×F÷£‡‚#âr°¢sÆ'WGFöâ6Æ73Ò&'Fâ"–CÒ&7$&÷fTæ÷r#îiˆîzîh›XxcÂö'WGFöãâr°¢sÆ'WGFöâ6Æ73Ò&'Fâ"–CÒ&7$–ç7FÆÄæ÷r"7G–ÆSÒ&F—7Æ“¦æöæR#îK¸^iÊÎjÊZèŠ8SÂö'WGFöãâr°¢sÆ'WGFöâ6Æ73Ò&'Fâv†÷7B"–CÒ&7$6æ6VÄ7F–öâ#îXùnkhƒÂö'WGFöãâr°¢sÂöF—cãÂöF—câr¢rr’°¢†Bæf–ÆW2ÇÂµÒ’æÖ‚†bÂ’’Óâ°¢6öç7B6öçFVçBÒ7G&–ær†bæ6öçFVçBÇÂrr“°¢6öç7BÆ–æW2Ò6öçFVçBç7Æ—B‚õÇ#õÆâò“°¢6öç7B&Wf–WrÒÆ–æW2ç6Æ–6RƒÂb’æ¦ö–â‚uÆâr“°¢6öç7BöÖ—GFVBÒÖF‚æÖ‚ƒÂÆ–æW2æÆVæwF‚Òb“°¢6öç7B'—FW2Ò‚‚’Óâ²G'’²&WGW&âæWrFW‡DVæ6öFW"‚’æVæ6öFR†6öçFVçB’æÆVæwFƒ²Ò6F6‚…ò’²&WGW&â6öçFVçBæÆVæwFƒ²ÒÒ’‚“°¢6öç7B6—¦UFW‡BÒ†'—FW2ò#B’çFôf—†VBƒ’²r´"+rr²Æ–æW2æÆVæwF‚²rŠÂs°¢&WGW&âsÆF—b6Æ73Ò&6&B7"Öf–ÆRÖ6&B"7G–ÆSÒ&Ö&v–â×F÷£‡‚#âr°¢sÆF—b6Æ73Ò&7"Öf–ÆRÖ†VB#âr°¢sÆF—b6Æ73Ò&7"Öf–ÆR×F—FÆR#âr°¢–6öâ‚v6Æ—rÂw6Òr’²rÇ7G&öæsâr²W62†bææÖR’²sÂ÷7G&öæsâr°¢sÇ7â6Æ73Ò&×WFVB7"Öf–ÆR×6—¦R#âr²W62‡6—¦UFW‡B’²sÂ÷7ãâr°¢sÇ7â6Æ73Ò'FræWWG&Â#âr²W62†7&VF÷$Æætöb†bææÖR’’²sÂ÷7ãâr°¢sÂöF—câr°¢sÆF—b6Æ73Ò&7"Öf–ÆRÖ7F–öç2#âr°¢sÆ'WGFöâ6Æ73Ò&'Fâv†÷7B"G—SÒ&'WGFöâ"FFÖ7"Öf–ÆR×FövvÆSÒ"r²’²r"&–ÖW‡æFVCÒ&fÇ6R#î[^[Èk©zÂö'WGFöãâr°¢sÆ'WGFöâ6Æ73Ò&'Fâv†÷7B"G—SÒ&'WGFöâ"FFÖ6÷’Öf–ÆSÒ"r²’²r#îZHŞX‹nZèÎi[Nk©zÂö'WGFöãâr°¢sÂöF—câr°¢sÂöF—câr°¢sÆF—b6Æ73Ò&7"Öf–ÆR×&Wf–Wr"FFÖ7"Öf–ÆR×&Wf–WsÒ"r²’²r#âr°¢sÇ&Sâr²W62‡&Wf–WrÇÂ~ûÈz›®ih~K»nûÈ’r’²sÂ÷&Sâr°¢sÆF—b6Æ73Ò&7"Öf–ÆRÖÖ÷&R#âr²†öÖ—GFVBò~š(NŠxX˜ÒbŠÂ+r‹ùiÈ’r²öÖ—GFVB²rŠÂr¢~X[r²Æ–æW2æÆVæwF‚²rŠÂr’²sÂöF—câr°¢sÂöF—câr°¢sÆF—b6Æ73Ò&7"Öf–ÆRÖgVÆÂ"FFÖ7"Öf–ÆRÖ&öG“Ò"r²’²r"†–FFVããÂöF—câr°¢sÂöF—câs°¢Ò’æ¦ö–â‚rr’°¢sÆF—b6Æ73Ò&6&B"7G–ÆSÒ&Ö&v–â×F÷£‡‚#âr°¢sÆF—b6Æ73Ò'¶rÖ‚#îKúîiKˆØz‹óÂöF—câr°¢sÇFW‡F&V–CÒ&7$f""&÷w3Ò#""7G–ÆSÒ'v–GFƒ£S¶&÷‚×6—¦–æs¦&÷&FW"Ö&÷‚"r°¢wÆ6V†öÆFW#Ò.Y®ŠøZè>ŠhiKK¸K˜ûÈÎKè¾Zh.ûÉ®h¨®hù˜i.ih~jiKh‰8ÎŠú^YiŞkNK¨n8Ş8™{N™©NiKh‰3Xˆn™)ş8"#ãÂ÷FW‡F&Vâr°¢sÆF—b7G–ÆSÒ&F—7Æ“¦fÆWƒ¶v£‡ƒ¶Æ–vâÖ—FV×3¦6VçFW#¶Ö&v–â×F÷£‡‚#âr°¢sÆ'WGFöâ6Æ73Ò&'Fâ"–CÒ&7%&Wb#îhùKªNKúîiKûÈyIşh‰ikx˜iÊÎûÈ“Âö'WGFöãâr°¢sÇ7â6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£'‚#îiz~x˜iÊÎKÉ®KùŞZÙYÊˆØz‹ş[z^KÙÎXË®y¨Bæ†—7F÷'’ò˜xÎ8#Â÷7ãâr°¢sÂöF—câr°¢sÂöF—câr°¢sÂöF—cãÂöF—câs°¢Ğ¢òò{¹Zé®Kˆ[ÊˆØz‹şXÚKˆ®y¨NKªNK©.8'&ö÷BiŠòæ7'GW&âXX>{JûÉ¾K»¾KÙ^iKXªYîi[NXÚ˜xŞ{¹8¢òò˜xŞik{¹KˆjÊûÈiz~XX>{J[{.KŠ.[È>ûÈÎy¹Y
ÎKˆŞKÉ®XúXªûÈ8 ¢gVæ7F–öâ&–æDG&gD6&B‡&ö÷BÂBÂöävöæR’°¢&ö÷BæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ7–æ2†R’Óâ°¢6öç7Bf–ÆUFövvÆRÒRçF&vWBæ6Æ÷6W7B‚u¶FFÖ7"Öf–ÆR×FövvÆUÒr“°¢–b†f–ÆUFövvÆR’°¢6öç7B–æFW‚Ò7G&–ær†f–ÆUFövvÆRæFF6WBæ7$f–ÆUFövvÆRÇÂrr“°¢6öç7B&öG’Ò&ö÷BçVW'•6VÆV7F÷"‚u¶FFÖ7"Öf–ÆRÖ&öG“Ò"r²–æFW‚²r%Òr“°¢6öç7B&Wf–WrÒ&ö÷BçVW'•6VÆV7F÷"‚u¶FFÖ7"Öf–ÆR×&Wf–WsÒ"r²–æFW‚²r%Òr“°¢6öç7Bf–ÆRÒ†Bæf–ÆW2ÇÂµÒ•´çVÖ&W"†–æFW‚•Ó°¢–b‚&öG’ÇÂ&Wf–WrÇÂf–ÆR’&WGW&ã°¢6öç7B÷Væ–ærÒ&öG’æ†–FFVã°¢–b†÷Væ–ærbb&öG’æFF6WBç&VæFW&VBÓÒwG'VRr’°¢6öç7B6öçFVçBÒ7G&–ær†f–ÆRæ6öçFVçBÇÂrr“°¢&öG’æ–ææW$…DÔÂÒsÆF—b6Æ73Ò&7"Ö6öFR#ãÆF—b6Æ73Ò&ÖB#âr°¢RæÖB†7&VF÷$fVæ6Tf÷"†6öçFVçB’²7&VF÷$Æætöb†f–ÆRææÖR’²uÆâr²6öçFVçB²uÆâr²7&VF÷$fVæ6Tf÷"†6öçFVçB’Â²6öFT7F–öç3¢G'VRÒ’°¢sÂöF—cãÂöF—câs°¢&öG’æFF6WBç&VæFW&VBÒwG'VRs°¢Ğ¢&öG’æ†–FFVâÒ÷Væ–æs°¢&Wf–Wræ†–FFVâÒ÷Væ–æs°¢f–ÆUFövvÆRçFW‡D6öçFVçBÒ÷Væ–ærò~iKn‹[~k©zr¢~[^[Èk©zs°¢f–ÆUFövvÆRç6WDGG&–'WFR‚v&–ÖW‡æFVBrÂ7G&–ær†÷Væ–ær’“°¢&WGW&ã°¢Ğ¢6öç7B6÷”f–ÆT'WGFöâÒRçF&vWBæ6Æ÷6W7B‚u¶FFÖ6÷’Öf–ÆUÒr“°¢–b†6÷”f–ÆT'WGFöâ’°¢6öç7Bf–ÆRÒ†Bæf–ÆW2ÇÂµÒ•´çVÖ&W"†6÷”f–ÆT'WGFöâæFF6WBæ6÷”f–ÆR•Ó°¢–b‚f–ÆR’²Fö7B‚~h›îKˆŞX‹ŠhZHŞX‹ny¨Nih~K»br“²&WGW&ã²Ğ¢G'’°¢v—Bæf–vF÷"æ6Æ—&ö&Bçw&—FUFW‡B…7G&–ær†f–ÆRæ6öçFVçBÇÂrr’“°¢6÷”f–ÆT'WGFöâçFW‡D6öçFVçBÒ~[{.ZHŞX‹bs°¢6WEF–ÖV÷WB‚‚’Óâ²–b†6÷”f–ÆT'WGFöâæ—46öææV7FVB’6÷”f–ÆT'WGFöâçFW‡D6öçFVçBÒ~ZHŞX‹nZèÎi[Nk©zs²ÒÂS“°¢Ò6F6‚…ò’²Fö7B‚~ZHŞX‹nZK‹J^ûÈÎŠû~j8iú^Xš®‹KNiÛşiØ>™™r“²Ğ¢&WGW&ã°¢Ğ¢6öç7B'WGFöâÒRçF&vWBæ6Æ÷6W7B‚u¶FFÖ6÷’Ö6öFUÒr“°¢–b†'WGFöâ’°¢G'’°¢v—Bæf–vF÷"æ6Æ—&ö&Bçw&—FUFW‡B†'WGFöâæ6Æ÷6W7B‚ræ6öFV&Æö6²r’çVW'•6VÆV7F÷"‚v6öFRr’çFW‡D6öçFVçB“°¢'WGFöâçFW‡D6öçFVçBÒ~[{.ZHŞX‹bs°¢6WEF–ÖV÷WB‚‚’Óâ²–b†'WGFöâæ—46öææV7FVB’'WGFöâçFW‡D6öçFVçBÒ~ZHŞX‹bs²ÒÂS“°¢Ò6F6‚…ò’²Fö7B‚~ZHŞX‹nZK‹J^ûÈÎŠû~˜KŠŞKº>zh˜¾XªZHŞX‹br“²Ğ¢&WGW&ã°¢Ğ¢–b†RçF&vWBæ6Æ÷6W7B‚r67%&WfÂr’’°¢6öç7B"Ò–6²†v—B’ç÷7B‚rö’ö7&VF÷"÷fÆ–FFRrÂ²–C¢Bæ–BÒ’“°¢–b‚"ÇÂ"æö²ÇÂ"çfÆ–FF–öâ’²Fö7B‚~š¨ÎŠøZK‹J^ûÉ¢r²‚‡"bb"æW'"’ÇÂ~iÊ®yú^™IŠúòr’“²&WGW&ã²Ğ¢&VG&t6&B‡&ö÷BÂö&¦V7Bæ76–vâ‡·ÒÂBÂ²fÆ–FF–öã¢"çfÆ–FF–öâÒ’“°¢Fö7B‡"çfÆ–FF–öâæö²ò~™Ùhš¨ÎŠø˜	®‹ørr¢~iÈ’r²‡"çfÆ–FF–öâæW'&÷'2ÇÂµÒ’æÆVæwF‚²rKŠ®™zîš)‚r“°¢&WGW&ã°¢Ğ¢–b†RçF&vWBæ6Æ÷6W7B‚r67%&Wf–Wrr’’°¢6öç7B&÷‚Ò&ö÷BçVW'•6VÆV7F÷"‚r67%&Wf–Wt&÷‚r“°¢–b‚&÷‚’²Fö7B‚~[Ù>X˜ŞˆØz‹şiÊ®˜	®‹ø~™Ùhš¨ÎŠøûÈÎKˆŞˆ;ŞZèŠ8Rr“²&WGW&ã²Ğ¢6öç7B÷Væ–ærÒ&÷‚æ†–FFVã°¢&÷‚æ†–FFVâÒ÷Væ–æs°¢6öç7B&Wf–Wt'WGFöâÒ&ö÷BçVW'•6VÆV7F÷"‚r67%&Wf–Wrr“°¢–b‡&Wf–Wt'WGFöâ’°¢&Wf–Wt'WGFöâçFW‡D6öçFVçBÒ÷Væ–ærò~iKn‹[~ZèŠ8^jÚ^šªBr¢~iú^yÈ¾ZèŠ8^jÚ^šªBs°¢&Wf–Wt'WGFöâç6WDGG&–'WFR‚v&–ÖW‡æFVBrÂ7G&–ær†÷Væ–ær’“°¢Ğ¢–b†÷Væ–ær’°¢Fö7B‚~ZèŠ8^™ÈŠhKŠNjÚ^zîŠêNûÉ®XXx+X{¾(	Îiˆîzîh›Xxn(	ŞûÈÎXhŞx+X{¾(	ÎK¸^iÊÎjÊZèŠ8^(	Òr“°¢Ğ¢&WGW&ã°¢Ğ¢–b†RçF&vWBæ6Æ÷6W7B‚r67$6æ6VÂÂ67$6æ6VÄ7F–öâr’’°¢6öç7B&÷‚Ò&ö÷BçVW'•6VÆV7F÷"‚r67%&Wf–Wt&÷‚r“°¢–b†&÷‚’&÷‚æ†–FFVâÒG'VS°¢6öç7B&Wf–Wt'WGFöâÒ&ö÷BçVW'•6VÆV7F÷"‚r67%&Wf–Wrr“°¢–b‡&Wf–Wt'WGFöâ’°¢&Wf–Wt'WGFöâçFW‡D6öçFVçBÒ~iú^yÈ¾ZèŠ8^jÚ^šªBs°¢&Wf–Wt'WGFöâç6WDGG&–'WFR‚v&–ÖW‡æFVBrÂvfÇ6Rr“°¢Ğ¢&WGW&ã°¢Ğ¢–b†RçF&vWBæ6Æ÷6W7B‚r67$&÷fTæ÷rr’’°¢6öç7B&÷fT'WGFöâÒRçF&vWBæ6Æ÷6W7B‚r67$&÷fTæ÷rr“°¢6öç7B&÷fÅ7FFRÒ&ö÷BçVW'•6VÆV7F÷"‚r67$&÷fÅ7FFRr“°¢&÷fT'WGFöâæF—6&ÆVBÒG'VS°¢&÷fT'WGFöâçFW‡D6öçFVçBÒ~Zêh›KŠŞ(
bs°¢–b†&÷fÅ7FFR’&÷fÅ7FFRçFW‡D6öçFVçBÒ~jÚ>YÊX‰¾[»®KˆjÊh
~Zêh›ûÈÎŠû~zˆŞX	(
bs°¢ÆWB#°¢G'’°¢"Ò–6²†v—B’ç÷7B‚rö’ö7&VF÷"ö&÷fRrÂ²–C¢Bæ–BÒ’“°¢Ò6F6‚†W'&÷"’°¢&÷fT'WGFöâæF—6&ÆVBÒfÇ6S°¢&÷fT'WGFöâçFW‡D6öçFVçBÒ~iˆîzîh›Xxbs°¢6öç7BFWF–ÂÒ7&VF÷$7F–öäW'&÷"‡¶W'#¢W'&÷"bbW'&÷"æÖW76vRòW'&÷"æÖW76vR¢t'&–â‹ùîhê^ZK‹J^h‰nŠû~k.‹h^i{bwÒÂ~Zêh›Šû~k.ZK‹JRr“°¢–b†&÷fÅ7FFR’&÷fÅ7FFRçFW‡D6öçFVçBÒFWF–Ã°¢Fö7B†FWF–Â“°¢&WGW&ã°¢Ğ¢–b‚"ÇÂ"æö²ÇÂ"æ&÷fÂÇÂ"æ&÷fÂæ&÷fÅö–B’°¢&÷fT'WGFöâæF—6&ÆVBÒfÇ6S°¢&÷fT'WGFöâçFW‡D6öçFVçBÒ~iˆîzîh›Xxbs°¢6öç7BFWF–ÂÒ7&VF÷$7F–öäW'&÷"‡"Â~Zêh›ZK‹JRr“°¢–b†&÷fÅ7FFR’&÷fÅ7FFRçFW‡D6öçFVçBÒFWF–Ã°¢Fö7B†FWF–Â“°¢&WGW&ã°¢Ğ¢–b†&÷fÅ7FFR’&÷fÅ7FFRçFW‡D6öçFVçBÒ~[{.h›XxnˆØz‹òbr²"æ&÷fÂçfW'6–öâ²~ûÈÎZêh›{ÉnXûrr²7G&–ær‡"æ&÷fÂæ&÷fÅö–B’ç6Æ–6RƒÂB’²~(
nûÉ¾Šû~x+X{¾(	ÎK¸^iÊÎjÊZèŠ8^(	ŞZèÎh‰KˆjÊh
~kh‹K8"s°¢&÷fT'WGFöâç7G–ÆRæF—7Æ’ÒvæöæRs°¢6öç7B–ç7FÆÄ'WGFöâÒ&ö÷BçVW'•6VÆV7F÷"‚r67$–ç7FÆÄæ÷rr“°¢–b†–ç7FÆÄ'WGFöâ’²–ç7FÆÄ'WGFöâç7G–ÆRæF—7Æ’Òv–æÆ–æRÖ&Æö6²s²–ç7FÆÄ'WGFöâæFF6WBæ&÷fÂÒ"æ&÷fÂæ&÷fÅö–C²Ğ¢Fö7B‚~[{.Šë[Ù^K«®[z^h›XxnûÈÎzØ[è^KˆjÊh
~ZèŠ8Rr“°¢&WGW&ã°¢Ğ¢–b†RçF&vWBæ6Æ÷6W7B‚r67$–ç7FÆÄæ÷rr’’°¢6öç7B–ç7FÆÄ'WGFöâÒRçF&vWBæ6Æ÷6W7B‚r67$–ç7FÆÄæ÷rr“°¢6öç7B–BÒ–ç7FÆÄ'WGFöâæFF6WBæ&÷fÂÇÂrs°¢–b‚–B’²Fö7B‚~Šû~XXx+X{¾(	Îiˆîzîh›Xxn(	ŞyIşh‰KˆjÊh
~Zêh›’r“²&WGW&ã²Ğ¢–b‚v—B6öæf—&ÔF–Æör‚~zîŠêNK¸^iÊÎjÊZèŠ8^X‹(	Âr²7&VF÷%F&vWDöb†B’²~(	ŞûÉşZèŠ8^Xúşˆ;ŞŠznXù6¶–ÆÂ†÷7Bx:Ş˜xŞ‹ÛŞ8"r’’&WGW&ã°¢6öç7B&÷fÅ7FFRÒ&ö÷BçVW'•6VÆV7F÷"‚r67$&÷fÅ7FFRr“°¢–ç7FÆÄ'WGFöâæF—6&ÆVBÒG'VS°¢–ç7FÆÄ'WGFöâçFW‡D6öçFVçBÒ~ZèŠ8^KŠŞ(
bs°¢–b†&÷fÅ7FFR’&÷fÅ7FFRçFW‡D6öçFVçBÒ~jÚ>YÊZèŠ8^X‹(	Âr²7&VF÷%F&vWDöb†B’²~(	ŞûÈÎŠû~zˆŞX	(
bs°¢ÆWB#°¢G'’°¢"Ò–6²†v—B’ç÷7B‚rö’ö7&VF÷"ö–ç7FÆÂrÂ²&÷fÅö–C¢–BÒ’“°¢Ò6F6‚†W'&÷"’°¢–ç7FÆÄ'WGFöâæF—6&ÆVBÒfÇ6S°¢–ç7FÆÄ'WGFöâçFW‡D6öçFVçBÒ~K¸^iÊÎjÊZèŠ8Rs°¢6öç7BFWF–ÂÒ7&VF÷$7F–öäW'&÷"‡¶W'#¢W'&÷"bbW'&÷"æÖW76vRòW'&÷"æÖW76vR¢t'&–â‹ùîhê^ZK‹J^h‰nŠû~k.‹h^i{bwÒÂ~ZèŠ8^Šû~k.ZK‹JRr“°¢–b†&÷fÅ7FFR’&÷fÅ7FFRçFW‡D6öçFVçBÒFWF–Ã°¢Fö7B†FWF–Â“°¢&WGW&ã°¢Ğ¢–b‚"ÇÂ"æö²’°¢6öç7BFWF–ÂÒ7&VF÷$7F–öäW'&÷"‡"Â~ZèŠ8^ZK‹JRr“°¢6öç7B7FGW2ÒçVÖ&W"‡"bb‡"å÷7FGW2ÇÂ"ç7FGW2’’ÇÂ°¢–ç7FÆÄ'WGFöâæFF6WBæ&÷fÂÒrs°¢–b‡7FGW2ãÒCbb7FGW2ÂS’°¢–ç7FÆÄ'WGFöâçFW‡D6öçFVçBÒ~™È˜xŞikh›Xxbs°¢–b†&÷fÅ7FFR’&÷fÅ7FFRçFW‡D6öçFVçBÒFWF–Â²~8.Šû~˜xŞikš¨ÎŠøˆØz‹şYîûÈÎXhŞjÊiˆîzîh›Xxn[›nZèŠ8^8"s°¢ÒVÇ6R°¢–ç7FÆÄ'WGFöâçFW‡D6öçFVçBÒ~{¹>iéÎ[è^zîŠêBs°¢–b†&÷fÅ7FFR’&÷fÅ7FFRçFW‡D6öçFVçBÒFWF–Â²~8.ZèŠ8^{¹>iéÎi¨.KˆŞzîZé®ûÈÎŠû~XXX‹~ikhù.K»nX‰~ŠzîŠêNûÈÎ˜şXXŞ˜xŞZHŞZèŠ8^8"s°¢Ğ¢Fö7B†FWF–Â“°¢&WGW&ã°¢Ğ¢6öç7B–ç7FÆÅ&W7VÇBÒ"æ–ç7FÆÂÇÂ·Ó°¢6öç7B–ç7FÆÆVDæÖRÒ7G&–ær†–ç7FÆÅ&W7VÇBç6¶vRÇÂ–ç7FÆÅ&W7VÇBææÖRÇÀ¢‡"æ&÷fÂbb"æ&÷fÂçF&vWEöæÖR’ÇÂ7&VF÷%F&vWDöb†B’“°¢6öç7B—5ÇVv–âÒBæ¶–æBÓÒw6¶–ÆÂs°¢òòF†R7&VF÷"&÷WFRFVÆ–&W&FVÇ’6öÖÖ—G2f–ÆW2v—F†÷WB–×÷'F–æræWvÇ’7WÆ–VB6öFRà¢òòÇVv–åö÷2&W÷'G2&W7F'EöæVVFVBöG–æÖ–5öÆöFVBÂ6òFöâwB6Æ–ÒF†RvVçB6âW6R—B–WBà¢6öç7B&W7F'DæVVFVBÒ–ç7FÆÅ&W7VÇBç&W7F'EöæVVFVBÓÓÒG'VRÇÀ¢†—5ÇVv–âbb–ç7FÆÅ&W7VÇBæG–æÖ–5öÆöFVBÓÓÒfÇ6R“°¢ÆWB–ç7FÆÅ7FGW3°¢6öç7BÆ–÷WBÒ7G&–ær†–ç7FÆÅ&W7VÇBæÆ–÷WBÇÂ‚†Bæf–ÆW2ÇÂµÒ’ç6öÖR‚†b’ÓâõÂõõö–æ—EõõÂç’Bö’çFW7B†bææÖR’’òvF—&V7F÷'’r¢w6–ævÆRr’“°¢6öç7B6¶vTÆ&VÂÒÆ–÷WBÓÓÒvF—&V7F÷'’rò~yºî[Ù^hù.K»nXÈRr¢~XÙ^ih~K»nhù.K»bs°¢6öç7Bg&öçFVæD76WG2Ò–ç7FÆÅ&W7VÇBæg&öçFVæEö76WG2ÓÓÒG'VRÇÂ†Bæf–ÆW2ÇÂµÒ’ç6öÖR‚†b’ÓâõÂög&öçFVæEÂâ†§7Æ772’Bö’çFW7B†bææÖR’“°¢–b†—5ÇVv–âbb&W7F'DæVVFVB’°¢–ç7FÆÅ7FGW2Ò~ZèŠ8^h‰X©şûÉ¢r²–ç7FÆÆVDæÖR²~ûÈ‚r²6¶vTÆ&VÂ²~ûÈ[{.XiXZR'&–âhù.K»nyºî[Ù^8.[Ù>X˜Ò'&–â[	®iÊ®Xª‹ÛŞikKº>zûÉ¾˜xŞY
ò'&–âYîûÈÄvVçBh˜Şˆ;Ş‹>yJ‚DôôÅ2òDôôÅõ5T5>8"r²†g&öçFVæD76WG2ò~hù.K»nyXÎ™Ú.‹XNk©K™ş[{.ZèŠ8^ûÈÎZê.h‹~zºşKÉ®YÊ‚'&–â˜xŞY
şYîj8kX¾[›nXª‹ÛŞ8"r¢rr’²~Zêh›[{.kh‹KûÈÎKˆŞˆ;Ş˜xŞZHŞKÛşyJ8"s°¢ÒVÇ6R–b†—5ÇVv–â’°¢–ç7FÆÅ7FGW2Ò~ZèŠ8^h‰X©şûÉ¢r²–ç7FÆÆVDæÖR²~ûÈ‚r²6¶vTÆ&VÂ²~ûÈ[{.XiXZR'&–âhù.K»nyºî[Ù^8.Šû~X‹hù.K»nzêynš^zîŠêN‹ùŠÎx«nhY(Î[z^X[~kˆ^XÙ^ûÈÎXhŞYÊˆ®ZJKŠŞhøş‹ûŠhhš~ŠÎy¨NK»¾Xª8.Zêh›[{.kh‹KûÈÎKˆŞˆ;Ş˜xŞZHŞKÛşyJ8"s°¢ÒVÇ6R–b‡&W7F'DæVVFVB’°¢–ç7FÆÅ7FGW2Ò~ZèŠ8^h‰X©şûÉ¢r²–ç7FÆÆVDæÖR²r[{.XiXZ^h¨ˆ;Şyºî[Ù^ûÈÎKØn™ÈŠh˜xŞY
şh¨ˆ;ŞZëşK‹¾Yîh˜Şˆ;ŞZèÎXZyIşiX8.Zêh›[{.kh‹KûÈÎKˆŞˆ;Ş˜xŞZHŞKÛşyJ8"s°¢ÒVÇ6R°¢–ç7FÆÅ7FGW2Ò~ZèŠ8^h‰X©şûÉ¢r²–ç7FÆÆVDæÖR²r[{.XiXZ^h¨ˆ;Şyºî[Ù^8.Šû~X‹h¨ˆ;Şzêynš^zîŠêNZëşK‹¾[{.Xª‹ÛŞûÈÎXhŞYÊˆ®ZJKŠŞhøş‹ûŠhhš~ŠÎy¨NK»¾Xª8.Zêh›[{.kh‹KûÈÎKˆŞˆ;Ş˜xŞZHŞKÛşyJ8"s°¢Ğ¢–b†&÷fÅ7FFR’&÷fÅ7FFRçFW‡D6öçFVçBÒ–ç7FÆÅ7FGW3°¢–ç7FÆÄ'WGFöâçFW‡D6öçFVçBÒ&W7F'DæVVFVBò~[{.ZèŠ8R+r[è^˜xŞY
òr¢~[{.ZèŠ8Rs°¢–ç7FÆÄ'WGFöâæF—6&ÆVBÒG'VS°¢òò&Vg&W6‚F†RW‡FVç6–öâf–WræB6†V6²F†RW'6—7FVB6W'fW"7FFRWfVà¢òòv†Vâ–ç7FÆÆF–öâv27F'FVBg&öÒ6†Bâf–ÆVB&Vg&W6‚×W7Bæ÷@¢òò†–FRF†RÇ&VG’6öÖÖ—GFVB–ç7FÆÆF–öâ&W7VÇBà¢–b‡G—Vöbv–æF÷råõ÷f–v–Å&Vg&W6„W‡FVç6–öç2ÓÓÒvgVæ7F–öâr’v–æF÷råõ÷f–v–Å&Vg&W6„W‡FVç6–öç2‚“°¢–b†—5ÇVv–â’°¢G'’°¢6öç7B7FFRÒ–6²†v—B’ævWB‚rö’÷ÇVv–ç2r’“°¢–b‚7FFRæö²’F‡&÷ræWrW'&÷"†7&VF÷$7F–öäW'&÷"‡7FFRÂ~hù.K»nx«nhX‹~ikZK‹JRr’“°¢6öç7BÆöFVBÒ‡7FFRçÇVv–ç2ÇÂµÒ’ç6öÖR‚‡’ÓâææÖRÓÓÒ–ç7FÆÆVDæÖR“°¢–b‡7FFRçVæF–æu÷&W7F'Bbb7FFRçVæF–æu÷&W7F'BçVæF–ær’°¢–ç7FÆÅ7FGW2³Òr[{.zîŠêNz8y¹ZèŠ8^ZèÎh‰ûÈÎhù.K»nx«nhi‹îzK®[è^˜xŞY
ò'&–î8"s°¢ÒVÇ6R–b‚ÆöFVB’°¢–ç7FÆÅ7FGW2³ÒrZèŠ8^K¨¾Xª[{.ZèÎh‰ûÈÎKØn[Ù>X˜Şhù.K»nkˆ^XÙ^[	®iÊ®zîŠêNXª‹ÛŞûÉ¾Šû~j8iú^hù.K»nzêynš^Y(Â'&–âiz^[ù~8"s°¢ÒVÇ6R°¢–ç7FÆÅ7FGW2³Òr[{.K¸î[Ù>X˜Şhù.K»nkˆ^XÙ^zîŠêN8"s°¢Ğ¢Ò6F6‚†W'&÷"’°¢–ç7FÆÅ7FGW2³Òrhù.K»nx«nhi¨.i{nizk9^X‹~ikûÉ¢r²7G&–ær†W'&÷"bbW'&÷"æÖW76vRÇÂW'&÷"“°¢Ğ¢–b†&÷fÅ7FFR’&÷fÅ7FFRçFW‡D6öçFVçBÒ–ç7FÆÅ7FGW3°¢Ğ¢Fö7B†–ç7FÆÅ7FGW2“°¢&WGW&ã°¢Ğ¢–b†RçF&vWBæ6Æ÷6W7B‚r67%&Wbr’’°¢6öç7Bf"Ò‡&ö÷BçVW'•6VÆV7F÷"‚r67$f"r’çfÇVRÇÂrr’çG&–Ò‚“°¢–b‚f"’²Fö7B‚~XXXiKúîiKhHşŠxr“²&WGW&ã²Ğ¢6öç7B&WVW7D–BÒw&Wòr²7'—Fòç&æFöÕUT”B‚“°¢&VÖVÖ&W%GW&â‡&WVW7D–BÂ¶–çWC¦f"Æ¶–æC¦Bæ¶–æBÇÂwÇVv–ârÇ7FGW3¢wVæF–ærwÒ“°¢6öç7B"Ò–6²†v—B’ç÷7B‚rö’ö7&VF÷"ö¦ö'2rÂ²¶–æC¢Bæ¶–æBÇÂwÇVv–ârÀ¢G&gEö–C¢Bæ–BÂfVVF&6³¢f"Â&WVW7Eö–C¢&WVW7D–BÀ¢7&3¢w2rÂ6öçfW'6F–öåö–C¢7F—fT6öçfW'6F–öä–BÒ’“°¢–b‡"æö²bb"æ¦ö%ö–B’²v—BvF6„7&VF÷$¦ö"‡&ö÷BÂ"“²&WGW&ã²Ğ¢–b‚"ÇÂ"æö²ÇÂ"æG&gB’°¢6öç7Bf–ÇW&RÒ7&VF÷%&WVW7Df–ÇW&R‡"“°¢&VÖVÖ&W%GW&â‡&WVW7D–BÂf–ÇW&R“°¢Fö7B‚~KúîiKZK‹J^ûÉ¢r²7&VF÷$W'&÷%FW‡B†f–ÇW&RæW'&÷"’“°¢&WGW&ã°¢Ğ¢&VG&t6&B‡&ö÷BÂ"æG&gB“°¢Fö7B‚~[{.yIşh‰ikx˜iÊÂbr²"æG&gBçfW'6–öâ“°¢&WGW&ã°¢Ğ¢–b†RçF&vWBæ6Æ÷6W7B‚r67$FVÂr’’°¢–b‚v—B6öæf—&ÔF–Æör‚~XŠ™šNˆØz‹òr²Bæ–B²~ûÉşjÚNi8ŞKÙÎKˆŞXúşh.ZHŞ8"r’’&WGW&ã°¢6öç7B"Ò–6²†v—B’ç÷7B‚rö’ö7&VF÷"öFVÆWFRrÂ²–C¢Bæ–BÒ’“°¢–b‚"ÇÂ"æö²’²Fö7B‚~XŠKˆŞhèûÉ¢r²‚‡"bb"æW'"’ÇÂ~iÊ®yú^™IŠúòr’“²&WGW&ã²Ğ¢&ö÷Bç&VÖ÷fR‚“°¢Fö7B‚~ˆØz‹ş[{.XŠ™šBr“°¢öävöæRbböävöæR‚“°¢Ğ¢Ò“°¢Ğ¢gVæ7F–öâ&VG&t6&B‡&ö÷BÂB’°¢&ö÷Bæ÷WFW$…DÔÂÒG&gD6&D‡FÖÂ†B“°¢6öç7Bg&W6‚ÒFö7VÖVçBçVW'•6VÆV7F÷"‚ræ7'GW&å¶FFÖ7&–CÒ"r²Bæ–B²r%Òr“°¢–b†g&W6‚’&–æDG&gD6&B†g&W6‚ÂB“°¢Ğ¢òòh¨®Kˆ[ÊˆØz‹şXÚ‹ûŞXªX‹ˆ®ZJkXiÊ¾[îûÈˆØz‹şX‰~Šx+iÚyºîi{nyJûÈ¢7–æ2gVæ7F–öâ÷VäG&gD–ä6†B†–B’°¢6öç7B"Ò–6²†v—B’ævWB‚rö’ö7&VF÷"öG&gCö–CÒr²Væ6öFUU$”6ö×öæVçB†–B’’“°¢–b‚"ÇÂ"æö²ÇÂ"æG&gB’²Fö7B‚~h™>KˆŞ[ÈˆØz‹şûÉ¢r²‚‡"bb"æW'"’ÇÂ~iÊ®yú^™IŠúòr’“²&WGW&ã²Ğ¢6öç7B†öÆFW"ÒFö7VÖVçBæ7&VFTVÆVÖVçB‚vF—br“°¢†öÆFW"æ–ææW$…DÔÂÒG&gD6&D‡FÖÂ‡"æG&gB“°¢6öç7BæöFRÒ†öÆFW"æf—'7D6†–ÆC°¢6öç7B&÷‚ÒFö7VÖVçBçVW'•6VÆV7F÷"‚r6×6w2r“°¢–b‚&÷‚’&WGW&ã°¢&÷‚æVæD6†–ÆB†æöFR“°¢æöFRç67&öÆÄ–çFõf–Wr‡²&Æö6³¢v6VçFW"rÂ&V†f–÷#¢w6Öö÷F‚rÒ“°¢&–æDG&gD6&B†æöFRÂ"æG&gB“°¢Ğ  ¢7–æ2gVæ7F–öâ&VæFW%ÇVv–äÖ&¶WB†7G‚’°¢7G‚çF÷&"æ–ææW$…DÔÂĞ¢sÇ7â6Æ73Ò'Fr#ì*rhù.K»n[ˆ.YË£Â÷7ãâr°¢sÆF—b7G–ÆSÒ&fÆWƒ£#ãÂöF—câr°¢sÆ'WGFöâ6Æ73Ò&'Fâv†÷7B"–CÒ'Õ&Vg&W6‚#îX‹~ik[ˆ.YË£Âö'WGFöãâr°¢sÇ7â6Æ73Ò&×WFVB"–CÒ'Õ&Wò"7G–ÆSÒ&Ö&v–âÖÆVgC£'‚#ãÂ÷7ãâs° ¢7G‚çf–Wræ–ææW$…DÔÂĞ¢sÆF—b6Æ73Ò'w&#âr°¢sÆF—b–CÒ'Ôw&–B"7G–ÆSÒ&F—7Æ“¦w&–C¶w&–B×FV×ÆFRÖ6öÇVÖç3§&WVB†WFòÖf–ÆÂÆÖ–æÖ‚ƒ3#‚Ãg"’“¶v£g‚#ãÂöF—câr°¢sÆF—b–CÒ'ÔV×G’"6Æ73Ò&×WFVB"7G–ÆSÒ'FW‡BÖÆ–vã¦6VçFW#·FF–æs£Cƒ¶F—7Æ“¦æöæR#î[ˆ.YË®K‹®z›£ÂöF—câr°¢sÂöF—câs° ¢ÆWBÖ&¶WDFFÒçVÆÃ° ¢7–æ2gVæ7F–öâÆöB‚’°¢6öç7Bw&–BÒ7G‚çf–WrçVW'•6VÆV7F÷"‚r7Ôw&–Br“°¢w&–Bæ–ææW$…DÔÂÒsÆF—b6Æ73Ò&×WFVB#îXª‹ÛŞKŠÒââãÂöF—câs°¢G'’°¢6öç7B"Òv—B’ævWB‚rö’öÖ&¶WBöÆ—7Br“°¢–b‚"ÇÂ"æö²ÇÂ"æ§6öâ’°¢w&–Bæ–ææW$…DÔÂÒsÆF—b6Æ73Ò&×WFVB#îXª‹ÛŞZK‹J^ûÉ¢r²W62‚‡"bb"æW'"’ÇÂ~iÊ®yú^™IŠúòr’²sÂöF—câs°¢&WGW&ã°¢Ğ¢Ö&¶WDFFÒ"æ§6öã°¢7G‚çF÷&"çVW'•6VÆV7F÷"‚r7Õ&Wòr’çFW‡D6öçFVçBĞ¢~[{.ZèŠ8Rr²‡"æ§6öâæ–ç7FÆÆVEö6÷VçBÇÂ’²rKŠ¢s°¢G&r‚“°¢Ò6F6‚†R’°¢w&–Bæ–ææW$…DÔÂÒsÆF—b6Æ73Ò&×WFVB#îXª‹ÛŞZK‹J^ûÉ¢r²W62†RæÖW76vR’²sÂöF—câs°¢Ğ¢Ğ ¢gVæ7F–öâG&r‚’°¢6öç7Bw&–BÒ7G‚çf–WrçVW'•6VÆV7F÷"‚r7Ôw&–Br“°¢6öç7BV×G’Ò7G‚çf–WrçVW'•6VÆV7F÷"‚r7ÔV×G’r“°¢6öç7BÇVv–ç2Ò†Ö&¶WDFFbbÖ&¶WDFFçÇVv–ç2’ÇÂµÓ°¢–b‚ÇVv–ç2æÆVæwF‚’°¢w&–Bæ–ææW$…DÔÂÒrs°¢V×G’ç7G–ÆRæF—7Æ’Òv&Æö6²s°¢&WGW&ã°¢Ğ¢V×G’ç7G–ÆRæF—7Æ’ÒvæöæRs°¢w&–Bæ–ææW$…DÔÂÒÇVv–ç2æÖ†gVæ7F–öâ‡’°¢6öç7BFw2Ò‡çFw2ÇÂµÒ’æÖ†gVæ7F–öâ‡B’°¢&WGW&âsÇ7â6Æ73Ò'Fr"7G–ÆSÒ&Ö&v–â×&–v‡C£Gƒ¶Ö&v–âÖ&÷GFöÓ£G‚#âr²W62‡B’²sÂ÷7ãâs°¢Ò’æ¦ö–â‚rr“°¢6öç7B'FâÒæ–ç7FÆÆV@¢òsÆ'WGFöâ6Æ73Ò&'FâFævW""FFÖ7F–öãÒ'Væ–ç7FÆÂ"FFÖæÖSÒ"r²W62‡ææÖR’²r#îXÛ‹ÛÓÂö'WGFöãâp¢¢sÆ'WGFöâ6Æ73Ò&'Fâ"FFÖ7F–öãÒ&–ç7FÆÂ"FFÖæÖSÒ"r²W62‡ææÖR’²r#îZèŠ8SÂö'WGFöãâs°¢6öç7B&FvRÒæ–ç7FÆÆV@¢òsÇ7â6Æ73Ò'Fr"7G–ÆSÒ&&6¶w&÷VæC§f"‚ÒÖö²Ö&r“¶6öÆ÷#§f"‚ÒÖö²“¶Ö&v–âÖÆVgC£‡‚#î[{.ZèŠ8SÂ÷7ãâp¢¢rs°¢&WGW&â€¢sÆF—b6Æ73Ò&6&B"7G–ÆSÒ&F—7Æ“¦fÆWƒ¶fÆW‚ÖF—&V7F–öã¦6öÇVÖã¶v£‡‚#âr°¢sÆF—b7G–ÆSÒ&F—7Æ“¦fÆWƒ¶Æ–vâÖ—FV×3¦6VçFW#¶v£‡‚#âr°¢sÇ7â7G–ÆSÒ&föçB×6—¦S£#‚#ï	ù:cÂ÷7ãâr°¢sÇ7G&öæsâr²W62‡ææÖR’²sÂ÷7G&öæsâr°¢sÇ7â6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£'‚#çbr²W62‡çfW'6–öâÇÂsòr’²sÂ÷7ãâr°¢&FvR°¢sÂöF—câr°¢sÆF—b6Æ73Ò&×WFVB"7G–ÆSÒ&föçB×6—¦S£'‚#âr²W62‡æWF†÷"ÇÂ~iÊ®yúRr’²sÂöF—câr°¢sÆF—b7G–ÆSÒ&fÆWƒ£¶föçB×6—¦S£7ƒ¶Æ–æRÖ†V–v‡C£ãR#âr²W62‡æFW62ÇÂrr’²sÂöF—câr°¢sÆF—b7G–ÆSÒ&Ö&v–â×F÷£G‚#âr²Fw2²sÂöF—câr°¢sÆF—b7G–ÆSÒ&F—7Æ“¦fÆWƒ¶v£‡ƒ¶Ö&v–â×F÷£‡‚#âr²'Fâ²sÂöF—câr°¢sÂöF—câp¢“°¢Ò’æ¦ö–â‚rr“° ¢òò{¹Zé®hÈ™*îK¨¾K»`¢w&–BçVW'•6VÆV7F÷$ÆÂ‚v'WGFöå¶FFÖ7F–öåÒr’æf÷$V6‚†gVæ7F–öâ†'Fâ’°¢'FâæFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ7–æ2gVæ7F–öâ‚’°¢6öç7B7F–öâÒ'FâæFF6WBæ7F–öã°¢6öç7BæÖRÒ'FâæFF6WBææÖS°¢'FâæF—6&ÆVBÒG'VS°¢'FâçFW‡D6öçFVçBÒ7F–öâÓÓÒv–ç7FÆÂrò~ZèŠ8^KŠÒâââr¢~XÛ‹ÛŞKŠÒâââs°¢G'’°¢6öç7B"Òv—B’ç÷7B‚rö’öÖ&¶WBòr²7F–öâÂ²æÖS¢æÖRÒ“°¢–b‡"bb"æö²bb"æ§6öâbb"æ§6öâæö²’°¢v—BÆöB‚“°¢ÒVÇ6R°¢Fö7B‚‡"bb"æ§6öâbb"æ§6öâæW'"’ÇÂ~i8ŞKÙÎZK‹JRr“°¢'FâæF—6&ÆVBÒfÇ6S°¢'FâçFW‡D6öçFVçBÒ7F–öâÓÓÒv–ç7FÆÂrò~ZèŠ8Rr¢~XÛ‹ÛÒs°¢Ğ¢Ò6F6‚†R’°¢Fö7B‚~i8ŞKÙÎZK‹J^ûÉ¢r²RæÖW76vR“°¢'FâæF—6&ÆVBÒfÇ6S°¢'FâçFW‡D6öçFVçBÒ7F–öâÓÓÒv–ç7FÆÂrò~ZèŠ8Rr¢~XÛ‹ÛÒs°¢Ğ¢Ò“°¢Ò“°¢Ğ ¢7G‚çF÷&"çVW'•6VÆV7F÷"‚r7Õ&Vg&W6‚r’æFDWfVçDÆ—7FVæW"‚v6Æ–6²rÂ7–æ2gVæ7F–öâ‚’°¢6öç7B'FâÒ7G‚çF÷&"çVW'•6VÆV7F÷"‚r7Õ&Vg&W6‚r“°¢'FâæF—6&ÆVBÒG'VS°¢'FâçFW‡D6öçFVçBÒ~X‹~ikKŠÒâââs°¢G'’°¢v—B’ç÷7B‚rö’öÖ&¶WB÷&Vg&W6‚rÂ·Ò“°¢Ò6F6‚…ò’²ò¢X‹~ikZK‹J^KˆŞ™‹¾Zâ¢òĞ¢v—BÆöB‚“°¢'FâæF—6&ÆVBÒfÇ6S°¢'FâçFW‡D6öçFVçBÒ~X‹~ik[ˆ.YË¢s°¢Ò“° ¢v—BÆöB‚“°¢Ğ ¢vÆö&ÂåtU2Ò°¢²–C¢v6†BrÂæÖS¢~[z^KÙÎXûrÂ–6öã¢v6†BrÂ&VæFW#¢&VæFW$6†BÒÀ¢²–C¢w6WGF–æw2rÂæÖS¢~Šëî{ÚârÂ–6öã¢w6WBrÂ&VæFW#¢&VæFW%6WGF–æw4†öÖRÒÀ¢ââäeTä5ôu$õU2æÖ‚†r’Óâ‡²–C¢ræ–BÂæÖS¢rææÖRÂ–6öã¢ræ–6öâÂ&VæFW#¢Ö¶Tw&÷WvR†r’Ò’’À¢²–C¢vÖævVÖVçBrÂæÖS¢~{¹şKˆzêybrÂ–6öã¢vÖöârÂ&VæFW#¢&VæFW$ÖævVÖVçBÒÀ¢²–C¢vFFrÂæÖS¢~i[hÚârÂ–6öã¢v6†'BrÂ&VæFW#¢&VæFW$FFÒÀ¢Ó°§Ò’‡v–æF÷r“°