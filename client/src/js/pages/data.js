/* Data page renderer. Loaded as a classic script after chart.js and before pages.js. */
(function (global) {
  'use strict';
  const { esc, icon, fmtTime, fmtNum, empty } = U;
  const { get, post, pick } = API;

  global.VigilRenderData = async function (ctx, helpers) {
    const { li, topbar, tag, toMs, bindLi, confirmDialog } = helpers;
    ctx.topbar.className = 'topbar';
    const subs = [['ov', '概览'], ['tok', 'Token 分析'], ['tr', '对话 Trace'], ['hi', '聊天历史'], ['lg', '日志']];
    ctx.panel.innerHTML = '<div class="phead"><h2>数据</h2></div><div class="plist">' +
      subs.map(([k, n], i) => li({ icon: k === 'ov' ? 'chart' : k === 'tok' ? 'activity' : k === 'lg' ? 'book' : k === 'tr' ? 'term' : 'chat', title: n, on: i === 0, attrs: ' data-k="' + k + '"' })).join('') +
      '</div>';
    // ── 数据源：只有本机内核这一份 ──
    // 以前这里可以在"云端 / 本机"之间切：微信真身的账在云端，本机只有 pc
    // 渠道。P8.1 起桌面端不再连云端，stats/trace/history/log 全来自本机 Brain。
    const dget = (p) => get(p, { timeoutMs: 8000 });
    const dpost = (p, b) => post(p, b, { timeoutMs: 8000 });
    // 数据源够不着（内核没起来）时不渲染一堆假 0，给一句能看懂的话
    const fail = (r) => {
      if (r && r.ok) return false;
      v.innerHTML = '<div class="wrap">' + empty('本机数据源不可达',
        (r && (r._err || (r._status ? 'HTTP ' + r._status : ''))) || '') + '</div>';
      return true;
    };
    ctx.topbar.innerHTML = topbar('概览', '', '<button class="btn oline" id="dRe">' + icon('refresh', 'sm') + '刷新</button>');
    ctx.view.innerHTML = '<div class="wrap" id="dView"></div>';
    const v = ctx.view.querySelector('#dView');
    const cur = () => ctx.topbar.querySelector('h1').textContent;

    let dataRenderId = 0;
    ctx.onDispose(() => { dataRenderId++; });
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
        '<div class="muted" style="font-size:12px;margin-top:8px">每轮上下文由会话历史与压缩摘要、风格约束、人设、约定账本和长期记忆组成；会话摘要按 conversation_id 隔离。</div></div>' +
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
      // （微信群聊的账在 wechat_group 分区，切分区也能看）
      const CHANS = [['pc', '本机']];
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
      // 错误日志只在异常时写入；正常对话记录在 Trace 中。之前日志页只读 error_server，
      // 没发生异常时会显得整页“什么都没有”，也看不到最近实际运行过的请求。
      const [lr, tr] = await Promise.all([
        dget('/api/logs?src=server&n=200'), dget('/api/trace?last=200'),
      ]);
      if (!isCurrent()) return;
      const logs = pick(lr);
      const traces = pick(tr);
      if (fail(logs)) return;
      if (fail(traces)) return;
      const errors = (logs.items || []).map(x => ({kind: 'error', t: x.t, level: x.level,
        text: [x.where, x.type, x.msg].filter(Boolean).join(' · '), detail: x.trace || ''}));
      const runs = (traces.items || []).map(x => ({kind: 'trace', t: x.t,
        level: '运行', text: [x.src, x.model].filter(Boolean).join(' · ') || '对话请求',
        detail: '输入 ' + (x.in_len == null ? '—' : fmtNum(x.in_len)) +
          ' · Token 入 ' + (x.tok_in == null ? '—' : fmtNum(x.tok_in)) +
          ' · Token 出 ' + (x.tok_out == null ? '—' : fmtNum(x.tok_out)) +
          ' · 耗时 ' + (x.ms ? (x.ms / 1000).toFixed(1) + 's' : '—')}));
      const items = errors.concat(runs).sort((a, b) => toMs(b.t) - toMs(a.t)).slice(0, 200);
      v.innerHTML = '<div class="card hd"><div class="h"><h3>本机运行记录与错误</h3><div class="sp"></div>' +
        '<button class="btn oline" id="lgClear">清空错误</button></div>' +
        '<div class="muted" style="font-size:11.5px;padding:0 14px 8px">运行记录来自对话 Trace；错误记录来自错误日志。清空操作只清除错误记录。</div>' +
        (items.length ? items.map((x) => '<div class="row" style="padding:8px 14px">' +
          '<span class="muted mono" style="font-size:11.5px">' + esc(fmtTime(x.t, 'full')) + '</span>' +
          tag(x.kind === 'error' ? (x.level === 'WARN' ? 'warn' : 'err') : 'neutral',
            x.kind === 'error' ? (x.level || 'ERROR') : '运行') +
          '<div class="lb"><span class="mono" style="font-size:12px">' + esc(String(x.text || '').slice(0, 220)) +
          (x.detail ? '<br><span class="muted">' + esc(String(x.detail).slice(0, 300)) + '</span>' : '') +
          '</span></div>' + '</div>').join('') : empty('暂无运行或错误记录', '对话请求会从 Trace 显示在这里')) + '</div>';
      v.querySelector('#lgClear')?.addEventListener('click', async () => {
        if (!await confirmDialog('清空本机错误日志？对话 Trace 不会被删除。此操作不可恢复。')) return;
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
    ov();
  };
})(window);
