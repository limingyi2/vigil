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

  // ── 通用零件 ────────────────────────────────────────────────
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

  // ── 工作台问好：按时段变化 ──────────────────────────────────
  // 5-11 早上好 · 11-13 中午好 · 13-18 下午好 · 18-23 晚上好 · 其余 夜深了
  // big=空会话（Hermes 式大字头版）；否则压缩成消息流上方的一条细横幅。
  function heroHtml(big) {
    const now = new Date();
    const h = now.getHours();
    const hi = h < 5 ? '夜深了。' : h < 11 ? '早上好。' : h < 13 ? '中午好。' : h < 18 ? '下午好。' : '晚上好。';
    const wd = '日一二三四五六'[now.getDay()];
    const pad = (n) => String(n).padStart(2, '0');
    const ds = now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate());
    const ts = pad(now.getHours()) + ':' + pad(now.getMinutes());
    const sub = '<span class="mono">' + ds + '</span><span>·</span><span>周' + wd + '</span><span>·</span><span class="mono">' + ts + '</span>';
    if (!big) return '<div class="hero slim"><div class="display slim">' + hi + '</div>' +
      '<div class="hero-sub">' + sub + '</div></div>';
    return '<div class="hero">' +
      '<div class="hero-meta mono"><div class="no">NO. 001</div>工作台</div>' +
      '<div class="hero-main"><div class="display">' + hi + '<small>接下来做什么？</small></div>' +
      '<div class="hero-sub">' + sub + '</div>' +
      '<div class="hero-hints">' +
      ['帮我看看今天的日程', '把刚才那段整理成清单', '看看下载文件夹里有什么']
        .map((x) => '<button class="chip" data-hint="' + x + '">' + esc(x) + '</button>').join('') +
      '</div></div></div>';
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

  // ═══ 1. 对话 ═══════════════════════════════════════════════
  let chatTimer = null;

  async function renderChat(ctx) {
    const view = ctx.view, panel = ctx.panel, topbar = ctx.topbar;
    // 聊天页不用 .scroll（那是给卡片列表用的），换成撑满整块的 .chatwrap
    view.className = 'chatwrap';
    topbar.className = 'topbar center';
    const st = pick(await get('/api/stats'));
    const model = st.model || '—';
    topbar.innerHTML =
      // 左上是空的，中间也不摆人设 —— 控制台是干活的工具，不扮演角色
      '<div></div>' +
      '<div class="tc">' + tag(state.conn.ok ? 'ok' : 'err', state.conn.ok ? '在线' : '离线') +
      tag('neutral', model) + '</div>' +
      '<div class="tr">' +
      '<button class="btn" id="rpBtn">上下文</button>' +
      '<button class="ibtn" id="clearBtn" title="清空本机（pc）会话历史">' + icon('dots', 'sm') + '</button>' +
      '</div>';

    // 左栏照会话列表的样子来：顶部一个动作项，下面按天分组的真实记录，
    // 点一条跳到消息流里那句。不放说明文字 —— 面板里摆的都是能点的东西。
    panel.innerHTML =
      '<div class="phead"><h2>工作台</h2></div>' +
      '<div class="plist" id="sessList"><div class="empty">还没有聊过</div></div>';
    const sessList = panel.querySelector('#sessList');

    // ── 会话分段 ─────────────────────────────────────────────
    // 后端历史是按渠道的扁平消息流（没有会话边界），边界只能在客户端定。
    // 规则：你点一次「新话题」→ 之后算新的一段；跨天也算新的一段。
    // 这样左栏是「一段对话一行」，而不是每一条消息都占一行。
    const SESS_KEY = 'vigil.sessions.pc.v1';
    function loadMarkers() {
      try {
        const a = JSON.parse(localStorage.getItem(SESS_KEY) || '[]');
        return Array.isArray(a) ? a.filter((x) => typeof x === 'number').sort((x, y) => x - y) : [];
      } catch (e) { return []; }
    }
    function saveMarkers(a) { try { localStorage.setItem(SESS_KEY, JSON.stringify(a.slice(-200))); } catch (e) {} }
    const tsOf = (t) => {
      const n = typeof t === 'number' ? (t < 1e12 ? t * 1000 : t) : Date.parse(String(t).replace(/-/g, '/'));
      return isNaN(n) ? Date.now() : n;
    };
    const minuteOf = (ms) => Math.floor(ms / 60000) * 60000;
    const dayStrOf = (ms) => { const d = new Date(ms); return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate(); };
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

    function drawSessions(items) {
      if (!items.length) {
        sessList.innerHTML = '<div class="empty" style="text-align:left;padding:18px 12px">还没有聊过。说点什么试试。</div>';
        return;
      }
      const rows = items.map((m, i) => ({ m, i, ts: tsOf(m.t) }));
      const markers = loadMarkers().map(minuteOf);
      const order = [];
      const byKey = new Map();
      rows.forEach(({ m, i, ts }) => {
        const mmin = minuteOf(ts);
        let mk = 0;
        for (const x of markers) { if (x <= mmin) mk = x; else break; }
        const key = mk + '@' + dayStrOf(ts);
        let s = byKey.get(key);
        if (!s) { s = { key, startIdx: i, endIdx: i, firstTs: ts, lastTs: ts, n: 0 }; byKey.set(key, s); order.push(s); }
        s.endIdx = i; s.lastTs = ts; s.n += 1;
      });
      const titleOf = (s) => {
        for (let i = s.startIdx; i <= s.endIdx; i++) {
          const mm = rows[i];
          const mine = ['me', 'user', 'self'].includes(String(mm.m.role || '').toLowerCase());
          const tx = String(mm.m.text || '').replace(/\s+/g, ' ').trim();
          if (mine && tx) return tx;
        }
        return String(rows[s.startIdx].m.text || '').replace(/\s+/g, ' ').trim();
      };
      const parts = [
        '<div class="li" data-newtopic>' +
        '<span class="ic">' + icon('plus', 'sm') + '</span>' +
        '<div class="tx"><b>新话题</b><span>清掉当前上下文 · 聊天记录保留</span></div></div>',
      ];
      order.slice().reverse().forEach((s) => {
        const title = titleOf(s) || '（空）';
        parts.push(
          '<div class="li" data-goto="' + s.startIdx + '"' +
          ' title="' + esc(s.n + ' 条 · ' + fmtTime(s.firstTs, 'time') + ' – ' + fmtTime(s.lastTs, 'time')) + '">' +
          '<span class="ic">' + icon('chat', 'sm') + '</span>' +
          '<div class="tx"><b>' + esc(title.slice(0, 26)) + '</b>' +
          '<span>' + esc(relative(s.lastTs)) + ' · ' + s.n + ' 条</span></div>' +
          '<span class="meta">' + esc(fmtTime(s.lastTs, 'time')) + '</span></div>');
      });
      sessList.innerHTML = parts.join('');

      sessList.querySelectorAll('[data-goto]').forEach((n) => n.addEventListener('click', () => {
        const node = msgs.children[Number(n.dataset.goto)];
        if (!node) return;
        node.scrollIntoView({ block: 'center', behavior: 'smooth' });
        node.classList.add('flash');
        setTimeout(() => node.classList.remove('flash'), 1400);
      }));
      sessList.querySelector('[data-newtopic]').addEventListener('click', async () => {
        if (!confirm('重开一段话题？当前上下文清掉，聊天记录保留。')) return;
        const r = pick(await post('/api/reset', {}));
        if (r.ok) { const a = loadMarkers(); a.push(Date.now()); saveMarkers(a); }
        toast(r.ok ? '新话题开始了' : '重置失败：' + (r._err || ''));
        if (r.ok) setTimeout(() => loadMsgs(true), 200);
      });
    }

    view.innerHTML =
      '<div class="split">' +
      '  <div class="chat">' +
      '    <div class="hero" id="hero" hidden></div>' +
      '    <div class="msgs" id="msgs"></div>' +
      '    <div class="composer">' +
      '      <div class="localpop" id="localPop" hidden></div>' +
      '      <div class="col">' +
      '      <div class="attach" id="attach" hidden></div>' +
      '      <div class="chead"><span class="cdot"></span><b>新消息</b><span class="sp"></span><span class="ccount mono" id="ccount">0 字</span></div>' +
      '      <div class="cbox"><textarea id="ta" placeholder="说点什么，或交代一件要做的事…"></textarea>' +
      '        <div class="cbar">' +
      '          <button class="ibtn" id="localBtn" title="本地操控：权限模式 · 白名单目录">' + icon('shield', 'sm') + '</button>' +
      '          <button class="ibtn" id="clipBtn" title="附一张图（她能看见）">' + icon('clip', 'sm') + '</button>' +
      '          <div class="sp"></div>' +
      '          <span class="mono hintline">Enter 发送 · Shift+Enter 换行</span>' +
      '          <button class="btn fill" id="sendBtn">' + icon('send', 'sm') + '发送</button>' +
      '        </div></div>' +
      '    </div></div>' +
      '  </div>' +
      '  <aside class="rp" id="rp"></aside>' +
      '</div>';

    const msgs = view.querySelector('#msgs');
    const heroEl = view.querySelector('#hero');
    const rp = view.querySelector('#rp');

    const cIn = Number(st.tok_in) || 0, cOut = Number(st.tok_out) || 0;
    rp.innerHTML =
      '<div class="card" style="margin-bottom:10px"><div class="gtitle" style="padding:0 0 6px">本轮上下文</div>' +
      '<div class="kv"><span>当前人设</span><b>' + esc(st.name || '—') + '</b></div>' +
      '<div class="kv"><span>模型</span><b>' + esc(model) + '</b></div>' +
      '<div class="kv"><span>今日轮次</span><b>' + esc(fmtNum(st.turns == null ? '—' : st.turns)) + '</b></div>' +
      '<div class="kv"><span>Token 合计（今日）</span><b>' + esc(fmtNum(cIn + cOut)) + '</b></div>' +
      '<div class="kv sub"><span>├ 入</span><b>' + esc(fmtNum(cIn)) + '</b></div>' +
      '<div class="kv sub"><span>└ 出</span><b>' + esc(fmtNum(cOut)) + '</b></div>' +
      '<div class="kv"><span>平均延迟</span><b>' + esc(st.avg_ms ? (st.avg_ms / 1000).toFixed(1) + 's' : '—') + '</b></div>' +
      '<div class="kv"><span>记忆命中</span><b>' + esc((st.mem_hit_turns || 0) + ' / ' + (st.turns || 0)) + '</b></div>' +
      '</div>' +
      '<div class="card" style="margin-bottom:10px"><div class="gtitle" style="padding:0 0 6px">Token 趋势 · 最近 60 轮</div>' +
      '<div class="sparkbox" id="tokSpark"><span class="muted" style="font-size:11.5px">…</span></div>' +
      '<div class="kv sub" id="tokSparkSum" style="display:none"></div>' +
      '</div>' +
      '<div class="card" style="margin-bottom:10px"><div class="gtitle" style="padding:0 0 6px">约定账本</div>' +
      '<div class="kv"><span>共</span><b>' + esc((st.agenda && st.agenda.total) || 0) + ' 条</b></div>' +
      '<div class="kv"><span>待办</span><b>' + esc((st.agenda && st.agenda.pending) || 0) + '</b></div>' +
      '<div class="kv"><span>错过</span><b>' + esc((st.agenda && st.agenda.missed) || 0) + '</b></div>' +
      '</div>' +
      (st.last_err ? '<div class="card"><div class="gtitle" style="padding:0 0 6px">最近错误</div>' +
        '<div class="mono" style="font-size:11.5px;color:var(--err)">' + esc(String(st.last_err).slice(0, 120)) + '</div></div>' : '');

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
    // 附件：一张图，随下一条消息发出去（她能"看见"——视觉模型先描述成文字再进上下文）
    let imgB64 = '';
    const attachBox = view.querySelector('#attach');
    view.querySelector('#clipBtn').addEventListener('click', () => {
      const inp = document.createElement('input');
      inp.type = 'file';
      inp.accept = 'image/*';
      inp.onchange = () => {
        const f = inp.files && inp.files[0];
        if (!f) return;
        const r = new FileReader();
        r.onload = () => {
          imgB64 = String(r.result).split(',', 2)[1] || '';
          attachBox.hidden = false;
          attachBox.innerHTML =
            '<img class="athumb" src="' + r.result + '" alt="">' +
            '<span class="aname">' + esc(f.name) + '</span>' +
            '<button class="ibtn" id="atClr" title="不发了">' + icon('x', 'sm') + '</button>';
          attachBox.querySelector('#atClr').addEventListener('click', () => {
            imgB64 = '';
            attachBox.hidden = true;
            attachBox.innerHTML = '';
          });
        };
        r.readAsDataURL(f);
      };
      inp.click();
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
      const r = pick(await get('/api/history?limit=300'));
      if (!r.ok) {
        if (force) msgs.innerHTML = errBox('读不到聊天记录', r._err || '检查设置 → 连接与设备');
        return;
      }
      const items = r.items || [];
      if (!force && items.length === lastCount) return;
      lastCount = items.length;
      if (!items.length) {
        // 空会话：Hermes 式大字问好 + 快捷开场，点一句填进输入框
        heroEl.hidden = false;
        heroEl.innerHTML = heroHtml(true);
        heroEl.querySelectorAll('[data-hint]').forEach((b) => b.addEventListener('click', () => {
          const ta = view.querySelector('#ta');
          if (ta) { ta.value = b.dataset.hint; ta.dispatchEvent(new Event('input')); ta.focus(); }
        }));
        msgs.innerHTML = '';
        drawSessions(items);
        return;
      }
      drawSessions(items);
      heroEl.hidden = false;
      heroEl.innerHTML = heroHtml(false);   // 有历史：压缩成细横幅，时段问好仍然可见
      msgs.innerHTML = items.map((m) => {
        const mine = ['me', 'user', 'self'].includes(String(m.role || '').toLowerCase());
        // 用户消息纯文本（esc），AI 消息用 Markdown 渲染
        const t = mine ? esc(m.text || '') : U.md(m.text || '');
        // 不画头像、不写名字：这是工具界面，不是"跟谁对话"。
        // 她说的在左、你说的在右，不用再靠头像区分。
        if (mine) return '<div class="turn me"><div class="bdy"><div class="txt">' + t + '</div></div></div>';
        return '<div class="turn"><div class="bdy">' +
          '<div class="txt md">' + t + '</div>' +
          '<div class="foot"><span>' + esc(fmtTime(m.t, 'time')) + '</span></div></div></div>';
      }).join('');
      msgs.scrollTop = msgs.scrollHeight;
    }

    async function send() {
      const ta = view.querySelector('#ta');
      const text = (ta.value || '').trim();
      if ((!text && !imgB64) || busy) return;
      const sendingImg = imgB64;
      imgB64 = '';
      attachBox.hidden = true;
      attachBox.innerHTML = '';
      ta.value = '';
      busy = true;
      const btn = view.querySelector('#sendBtn');
      btn.disabled = true;

      // 乐观气泡：用户消息先画上去
      msgs.insertAdjacentHTML('beforeend',
        '<div class="turn me"><div class="bdy"><div class="txt">' + esc(text) +
        (sendingImg ? '<img class="msgimg" src="data:image/png;base64,' + sendingImg + '" alt="">' : '') +
        '</div></div></div>');

      // AI 气泡：思考过程 + 正文，实时渲染
      const turnId = 'turn_' + Date.now();
      const thinkId = 'think_' + turnId;
      const textId = 'text_' + turnId;
      msgs.insertAdjacentHTML('beforeend',
        '<div class="turn" id="' + turnId + '"><div class="bdy">' +
        // 思考过程区域（默认展开）
        '<div class="think-block streaming">' +
        '<button class="think-toggle" data-target="' + thinkId + '">' +
        icon('brain', 'sm') + '思考中 <span class="think-caret">▴</span></button>' +
        '<div class="think-content" id="' + thinkId + '">' +
        '<div class="think-text" id="' + thinkId + '_inner"></div></div></div>' +
        // 正文区域（纯文本实时追加，完成后渲染 Markdown）
        '<div class="txt md streaming-text" id="' + textId + '" style="min-height:24px"></div>' +
        '<div class="foot"><span class="stream-status">正在生成…</span></div>' +
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

      // 借鉴 DeepSeek Harness：事件逐个进状态，但按动画帧合并绘制，
      // 避免每个 token 都重绘导致主线程卡顿
      function scheduleRender() {
        if (rafPending) return;
        rafPending = true;
        requestAnimationFrame(() => {
          rafPending = false;
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
        if (streamDone) return;
        console.log('[stream]', evt.type, evt.text ? evt.text.slice(0, 30) : '');
        if (evt.type === 'think' && evt.text) {
          thinkBuf += evt.text;
          thinkDirty = true;
          scheduleRender();
        } else if (evt.type === 'text' && evt.text) {
          textBuf += evt.text;
          textDirty = true;
          scheduleRender();
        } else if (evt.type === 'done') {
          streamDone = true;
          // 强制刷新最后一帧
          if (thinkDirty) { thinkInner.textContent = thinkBuf; thinkDirty = false; }
          if (textDirty) { textEl.textContent = textBuf; textDirty = false; }
          // 正文渲染成 Markdown
          textEl.innerHTML = U.md(textBuf || evt.text || '');
          // 思考过程如果为空，隐藏整个思考块
          if (!thinkBuf.trim()) {
            const tb = msgs.querySelector('#' + turnId + ' .think-block');
            if (tb) tb.remove();
          } else {
            thinkToggle.querySelector('.think-caret').textContent = '▾';
            thinkContent.hidden = true;
          }
          statusEl.textContent = fmtTime(Date.now(), 'time');
          busy = false;
          btn.disabled = false;
        } else if (evt.type === 'error') {
          streamDone = true;
          textEl.innerHTML = '<span style="color:var(--danger)">出错了：' + esc(evt.msg || '未知错误') + '</span>';
          statusEl.textContent = '失败';
          busy = false;
          btn.disabled = false;
        }
      };

      // 注册流式事件监听
      if (global.vigil && global.vigil.onStreamEvent) {
        global.vigil.onStreamEvent(onEvent);
      } else {
        console.warn('[stream] vigil.onStreamEvent not found');
      }

      try {
        // 开始流式请求
        if (global.vigil && global.vigil.apiStream) {
          console.log('[stream] starting apiStream...');
          const r = await global.vigil.apiStream('/api/chat/stream',
            sendingImg ? { text: text, img: sendingImg, src: 'pc' } : { text: text, src: 'pc' });
          console.log('[stream] apiStream returned:', r);
          if (!streamDone) {
            // 如果请求返回了但还没收到 done 事件，等一下
            setTimeout(() => {
              if (!streamDone) {
                console.warn('[stream] no done event received, textBuf length:', textBuf.length);
                if (textBuf) {
                  textEl.innerHTML = U.md(textBuf);
                  statusEl.textContent = fmtTime(Date.now(), 'time');
                } else {
                  statusEl.textContent = '无响应';
                }
                busy = false;
                btn.disabled = false;
                streamDone = true;
              }
            }, 2000);
          }
        } else {
          console.warn('[stream] vigil.apiStream not found, falling back to non-stream');
          // 降级到非流式
          const r = pick(await post('/api/chat', sendingImg
            ? { text: text, img: sendingImg, src: 'pc' }
            : { text: text, src: 'pc' }));
          if (!r.ok) throw new Error(r._err || '发送失败');
          textBuf = r.reply || '';
          textEl.innerHTML = U.md(textBuf);
          statusEl.textContent = fmtTime(Date.now(), 'time');
          busy = false;
          btn.disabled = false;
        }
      } catch (e) {
        console.error('[stream] error:', e);
        streamDone = true;
        textEl.innerHTML = '<span style="color:var(--danger)">出错了：' + esc(String(e.message || e)) + '</span>';
        statusEl.textContent = '失败';
        busy = false;
        btn.disabled = false;
      }
    }

    view.querySelector('#sendBtn').addEventListener('click', send);
    view.querySelector('#ta').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    });
    // 任务卡头部的实时字数（Hermes 式 "0 字"）
    const taEl = view.querySelector('#ta');
    const ccount = view.querySelector('#ccount');
    if (taEl && ccount) {
      const updCount = () => { ccount.textContent = (taEl.value || '').length + ' 字'; };
      taEl.addEventListener('input', updCount);
      updCount();
    }
    // 🔴 这两个按钮在**顶栏**里，不在 view 里 —— 用 view.querySelector 会拿到 null，
    // 报的错又是异步的（不进 app.js 那个 try），表现就是"消息区永远空白"。
    ctx.topbar.querySelector('#rpBtn').addEventListener('click', () => {
      view.querySelector('.rp').classList.toggle('no');
      view.querySelector('.split').classList.toggle('no-right');
    });
    ctx.topbar.querySelector('#clearBtn').addEventListener('click', () => {
      if (confirm('清空当前渠道的聊天历史？不可恢复。')) {
        post('/api/history/clear', { src: state.src }).then(() => loadMsgs(true));
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
          if (!confirm('将开放全部文件系统与命令执行权限，操作自动执行无需确认（系统关键目录仍受保护，修改自动备份可撤销）。确定吗？')) return;
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
    const offDoc = () => document.removeEventListener('pointerdown', closePop);
    localBtn.addEventListener('click', async () => {
      if (pop.hidden) { pop.hidden = false; await drawLocalPop(); }
      else pop.hidden = true;
    });

    await loadMsgs(true);
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
        '<div class="card">' +
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
                      : errBox('读不到人设列表', r._err || '')) +
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
        const nv = prompt('改这条记忆（改动会上云）：', it ? it.text : '');
        if (nv == null) return;                     // 取消
        const r = pick(await post('/api/memory/edit',
          { store: curStore, id: b.dataset.edit, text: nv }));
        if (r.ok) { toast('已改，同步层会推上云'); load(); }
        else toast('没改成：' + (r.err || r._err || ''));
      }));
      box.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
        if (!confirm('删掉这条记忆？会同步到云端（墓碑账本，不会被增量拉取复活）。')) return;
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
        if (!confirm('删除「' + b.dataset.del + '」的全部块？')) return;
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
    async function installZip(file, url, body) {
      if (!/\.zip$/i.test(file.name)) { toast('要 .zip 包'); return; }
      toast('正在装 ' + file.name + ' …');
      const b64 = await fileToB64(file);
      const r = pick(await post(url, Object.assign({ zip_b64: b64 }, body || {})));
      if (!r.ok) { toast('装失败：' + (r._err || r.err || '')); return; }
      toast('已装上（共 ' + (r.count || '?') + ' 个）' +
        (r.restart_needed ? ' · 有后台线程的插件要重启大脑才完全生效' : ''));
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
      v.innerHTML =
        '<div class="sech"><b>添加插件</b><div class="sp"></div><span class="muted">zip 上传</span></div>' +
        '<div class="drop" id="plDrop">' + icon('upload', 'md') +
        '<b>把插件 .zip 拖到这里</b><span class="muted">或点击选择文件 · 装上自动热重载，不用重启大脑</span></div>' +
        '<div class="sech"><b>已安装的插件</b><div class="sp"></div>' +
        '<span class="tag neutral">' + items.length + ' 个</span>' +
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

      bindDrop(v.querySelector('#plDrop'), (f) => installZip(f, '/api/plugins/install', { source: '客户端' }));
      v.querySelector('#plReload').addEventListener('click', async () => {
        const rr = pick(await post('/api/plugins/reload', {}));
        toast(rr.ok ? '已重载：' + (rr.count || '?') + ' 个插件' : '重载失败');
        plugins();
      });
      v.querySelectorAll('[data-un]').forEach((b) => b.addEventListener('click', async () => {
        if (!confirm('卸载插件 ' + b.dataset.un + '？')) return;
        await post('/api/plugins/uninstall', { name: b.dataset.un, purge_data: false });
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
      v.innerHTML =
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

      bindDrop(v.querySelector('#skDrop'), (f) => installZip(f, '/api/skill/install', { note: '客户端' }));
      v.querySelector('#skRe').addEventListener('click', async () => {
        await post('/api/skill/reload', {});
        toast('宿主重载中…');
        setTimeout(skills, 1200);
      });
      v.querySelectorAll('[data-sun]').forEach((b) => b.addEventListener('click', async () => {
        if (!confirm('卸载技能 ' + b.dataset.sun + '？')) return;
        const rr = pick(await post('/api/skill/uninstall', { skill: b.dataset.sun }));
        toast(rr.ok ? '已卸载' : '卸载失败：' + (rr._err || rr.err || ''));
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
    const dget = (p) => get(p, { base: state.dataSrc === 'cloud' ? 'cloud' : null });
    const dpost = (p, b) => post(p, b, { base: state.dataSrc === 'cloud' ? 'cloud' : null });
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

    async function ov() {
      ctx.topbar.querySelector('h1').textContent = '概览';
      const s = pick(await dget('/api/stats'));
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
      ctx.topbar.querySelector('h1').textContent = 'Token 分析';
      v.innerHTML = '<div class="wrap"><div class="muted" style="padding:20px 0">加载中…</div></div>';
      const [s, r] = await Promise.all([dget('/api/stats'), dget('/api/trace?last=500')]);
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
      ctx.topbar.querySelector('h1').textContent = '对话 Trace';
      const r = pick(await dget('/api/trace?last=60'));
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
      ctx.topbar.querySelector('h1').textContent = '聊天历史';
      // 控制台视角：本机是自己的会话，App / 微信的往来在这儿只读查看
      // （云端视角下微信群聊的账在 wechat_group 分区，也得能看）
      const CHANS = [['pc', '本机'], ['app', 'App'], ['wechat', '微信'], ['wechat_group', '微信群']];
      v.innerHTML = '<div class="seg" style="margin-bottom:12px">' +
        CHANS.map(([c, n], i) => '<button data-hc="' + c + '"' + (i === 0 ? ' class="on"' : '') + '>' + n + '</button>').join('') +
        '</div><div id="hcList"></div>';
      const box = v.querySelector('#hcList');
      let curHC = 'pc';
      const load = async () => {
        const r = pick(await dget('/api/history?limit=200&src=' + curHC));
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
      ctx.topbar.querySelector('h1').textContent = '日志';
      const r = pick(await dget('/api/logs?src=server&n=200'));
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
        if (!confirm('清空服务端日志？')) return;
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
    // 本地操控不再是设置里的一页：确认走聊天气泡，权限/目录/工具开关
    // 收在聊天输入框旁的盾牌面板里（见 renderChat）。
    // 「已绑定设备」删了：云端 brain 没有 /api/device/*（点了就是 no such api）。
    const subs = [['conn', '连接与设备'], ['model', '模型与服务商'], ['sync', '云端同步'], ['danger', '危险操作'], ['about', '关于']];
    ctx.panel.innerHTML = '<div class="phead"><h2>设置</h2></div><div class="plist">' +
      subs.map(([k, n], i) => li({ icon: k === 'conn' ? 'bell' : k === 'model' ? 'brain' : k === 'sync' ? 'heart' : k === 'danger' ? 'x' : 'set', title: n, on: i === 0, attrs: ' data-k="' + k + '"' })).join('') +
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
        '按顺序试 127.0.0.1:18787（SSH 隧道）与 127.0.0.1:8788（本机 brain）</span>' +
        '</div></div>' +

        '<div class="sec">手动配置（只有直连公网时才需要）</div>' +
        '<div class="card" style="margin-bottom:14px">' +
        '<div class="field"><label>Brain 地址</label>' +
        '<input class="input mono" id="sBase" value="' + esc(state.cfg.base || '') + '" placeholder="http://127.0.0.1:18787">' +
        '<div class="muted" style="font-size:11.5px">本机隧道 127.0.0.1:18787 · 本机直接跑 127.0.0.1:8788 · 直连云机 http://<地址>:8788</div></div>' +
        '<div class="field"><label>Brain Token</label>' +
        '<input class="input" id="sToken" type="password" placeholder="' + (has ? '已保存（留空 = 不改）' : '还没配') + '">' +
        '<div class="muted" style="font-size:11.5px">存在系统钥匙串（keytar），渲染层拿不到它</div></div>' +
        '<div style="display:flex;gap:8px;align-items:center">' +
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
        if (base) await API.saveCfg({ base });
        if (tok) {
          const r = await global.vigil.token.set(tok);
          if (r && r.warn) toast(r.warn);
        }
        res.textContent = '已保存，正在重连…';
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

    async function model() {
      ctx.topbar.querySelector('h1').textContent = '模型与服务商';
      const r = pick(await get('/api/models' + (state.src !== 'app' ? '?src=' + state.src : '')));
      if (!r.ok) { v.innerHTML = errBox('读不到模型配置', r._err || '先在「连接与设备」里把地址和 token 填对'); return; }
      const cur = r.current || {};
      const provs = r.providers || [];
      const groups = r.groups || {};
      // 可输入的组合框：候选来自服务商的模型清单，但本地部署的 Qwen 之类
      // 清单里没有的，直接打字就行（保存时按输入原样提交）。
      const combo = (dlId, arr, sel, ph) =>
        '<input class="input" id="' + dlId + '" list="' + dlId + '_dl" value="' + esc(sel || '') + '" placeholder="' + esc(ph || '留空 = 不启用') + '">' +
        '<datalist id="' + dlId + '_dl">' + (arr || []).map((m) => {
          const id = typeof m === 'string' ? m : (m.id || m.name);
          return '<option value="' + esc(id) + '">' + esc(id) + '</option>';
        }).join('') + '</datalist>';
      v.innerHTML = '<div class="card" style="margin-bottom:14px">' +
        '<div class="field"><label>服务商</label><select class="input" id="mProv">' +
        provs.map((p) => '<option value="' + esc(p.key) + '"' + (p.key === cur.provider ? ' selected' : '') + '>' + esc(p.name) + '</option>').join('') +
        '</select></div>' +
        '<div class="field"><label>接口地址</label><input class="input mono" id="mBase" value="' + esc(cur.api_base || '') + '"></div>' +
        '<div class="field"><label>密钥</label><input class="input" id="mKey" type="password" placeholder="' + (cur.api_key_set ? '已有一把，留空 = 不改' : '还没配') + '"></div>' +
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
        '<span class="muted" id="mRes" style="font-size:12px">来源：' + esc(r.source || '') + ' · 共 ' + esc(r.count || 0) + ' 个' +
        (r.err ? ' · ' + esc(String(r.err).slice(0, 60)) : '') + '</span></div>';
      // 下拉换服务商：把「接口地址」联动成这一家的默认地址（自定义除外，保留手填）。
      // 不做这一步的话，选完只改了服务商的名字，地址还停在上一家 —— 保存后
      // 服务端拿旧地址 + 新服务商名去调，看着切了其实没切。
      const provSel = v.querySelector('#mProv');
      const baseInp = v.querySelector('#mBase');
      provSel.addEventListener('change', () => {
        const p = provs.find((x) => x.key === provSel.value);
        if (p && p.key !== 'custom') baseInp.value = p.base || '';
      });
      v.querySelector('#mPull').addEventListener('click', async () => {
        const base = v.querySelector('#mBase').value.trim();
        const key = v.querySelector('#mKey').value.trim();
        const rr = await get('/api/models?force=1&base=' + encodeURIComponent(base) + '&key=' + encodeURIComponent(key));
        toast(rr.ok ? '已重新拉取' : '拉取失败');
        model();
      });
      // 语音合成音色（含训练音色）：/api/voices 是死数据清单，永远打得开。
      // pick() 返回的是普通对象不是 Promise，直接 await 拿结果。
      const vr = pick(await get('/api/voices'));
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
          const rr = pick(await post('/api/voice/apply', { key: key }));
          toast(rr.ok ? '音色已切换：' + (rr.desc || key) : '切换失败：' + (rr._err || rr.err || ''));
          if (rr.ok) { const d = box.querySelector('.muted'); if (d) d.textContent = rr.desc || key; }
        });
      }

      v.querySelector('#mSave').addEventListener('click', async () => {
        const payload = {
          provider: v.querySelector('#mProv').value,
          api_base: v.querySelector('#mBase').value.trim(),
          chat: v.querySelector('#mChat').value,
          vision: v.querySelector('#mVision').value,
          image: v.querySelector('#mImage').value,
          audio: v.querySelector('#mAudio').value,
        };
        const key = v.querySelector('#mKey').value.trim();
        if (key) payload.api_key = key;
        const rr = pick(await post('/api/models/apply', payload));
        v.querySelector('#mRes').textContent = rr.ok ? '已生效' : '没保存成：' + (rr._err || '');
        toast(rr.ok ? '已生效' : '保存失败');
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
        if (!confirm(b.dataset.t + '？')) return;
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

    const draw = { conn, model, sync, danger, about };
    bindLi(ctx.panel, (k, n) => {
      ctx.panel.querySelectorAll('.li').forEach((x) => x.classList.toggle('on', x === n));
      draw[k] && draw[k]();
    });
    // 首启向导引导：app.js 检测到"本地大脑在跑但还没配模型 key"时，
    // 把 state.settingsSub 指到 'model'，进来就直接是填 key 的表单
    const want = state.settingsSub;
    state.settingsSub = '';
    if (want && draw[want]) {
      ctx.panel.querySelectorAll('.li').forEach((x) => x.classList.toggle('on', x.dataset.k === want));
      draw[want]();
      return;
    }
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

  global.PAGES = [
    { id: 'chat', name: '工作台', icon: 'chat', render: renderChat },
    ...FUNC_GROUPS.map((g) => ({ id: g.id, name: g.name, icon: g.icon, render: makeGroupPage(g) })),
    { id: 'data', name: '数据', icon: 'chart', render: renderData },
  ];
})(window);
