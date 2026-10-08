// -*- coding: utf-8 -*-
/**
 * 零依赖 SVG 折线图。
 *
 * 为什么不用 ECharts 之类：CSP 里 script-src 是 'self'，外链脚本进不来；
 * 而且画两条线也用不上一整个图表库。颜色全部走 CSS 变量（style 属性里写
 * var(--accent)），切深色主题时自动跟着变，不用重画。
 *
 * 对外就两个东西（挂在 U 上，见 ui.js 末尾的合并）：
 *   U.lineChart(el, opt)  完整折线图：坐标轴、悬浮明细、图例自绘
 *   U.sparkline(el, pts)  迷你趋势线：无轴无线框，聊天右栏用
 *
 * opt = {
 *   series: [ { name, color, points: [{x, y}], area } ],   // x 毫秒时间戳，y 数值
 *   height, xFmt(ms), tipTitle(ms), yFmt(n), tipFmt(n),
 * }
 */
(function (global) {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';

  /** 千分位：31200 → 31,200（数字展示一律过它，别再裸拼） */
  function fmtNum(n) {
    n = Math.round(Number(n) || 0);
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  /** 紧凑：坐标轴/图例用。31200 → 31.2k，避免轴上挤一长串 */
  function fmtTok(n) {
    n = Number(n) || 0;
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    if (Math.abs(n) >= 1e4) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'k';
    return fmtNum(n);
  }

  /** 好看的刻度：最大值 → [0, …, top]，步长取 1/2/2.5/5 × 10^n */
  function niceTicks(max, want) {
    if (!(max > 0)) return [0, 1];
    const rough = max / Math.max(1, want || 4);
    const pow = Math.pow(10, Math.floor(Math.log10(rough)));
    let step = pow * 10;
    for (const m of [1, 2, 2.5, 5, 10]) { if (rough <= m * pow) { step = m * pow; break; } }
    const top = Math.ceil(max / step - 1e-9) * step;
    const out = [];
    for (let v = 0; v <= top + step * 1e-6; v += step) out.push(v);
    return out;
  }

  function h(name, attrs, parent) {
    const n = document.createElementNS(NS, name);
    for (const k in (attrs || {})) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  /**
   * 主图。返回 { destroy }；窗口尺寸变化时自动重画。
   * 数据为空时显示一句人话，不画空坐标轴。
   */
  function lineChart(el, opt) {
    if (!el) return { destroy() {} };
    const o = opt || {};
    const H = o.height || 240;
    const yFmt = o.yFmt || fmtTok;
    const tipFmt = o.tipFmt || fmtNum;
    const xFmt = o.xFmt || ((ms) => U.fmtTime(ms, 'time'));
    const tipTitle = o.tipTitle || ((ms) => U.fmtTime(ms, 'full'));
    const series = (o.series || []).filter((s) => s && s.points && s.points.length);

    el.innerHTML = '';
    el.classList.add('chartbox');
    if (!series.length) {
      const d = document.createElement('div');
      d.className = 'empty';
      d.style.cssText = 'height:' + H + 'px;display:flex;align-items:center;justify-content:center';
      d.textContent = '窗口里没有带 token 上报的轮次';
      el.appendChild(d);
      return { destroy() {} };
    }

    // ── 汇总 x 轴（并集、升序）；每条线按 x 查 y，缺的断开 ──
    const xs = [];
    const seen = new Set();
    series.forEach((s) => s.points.forEach((p) => {
      if (!seen.has(p.x)) { seen.add(p.x); xs.push(p.x); }
    }));
    xs.sort((a, b) => a - b);
    const ymap = series.map((s) => {
      const m = new Map();
      s.points.forEach((p) => m.set(p.x, Number(p.y) || 0));
      return m;
    });
    let maxY = 0;
    ymap.forEach((m) => m.forEach((v) => { if (v > maxY) maxY = v; }));

    const tip = document.createElement('div');
    tip.className = 'chart-tip';
    tip.hidden = true;
    const svg = h('svg', { height: H });
    el.appendChild(svg);
    el.appendChild(tip);

    let destroyed = false;
    let raf = 0;

    function draw() {
      if (destroyed || !el.isConnected) return;
      const W = Math.max(280, el.clientWidth || 460);
      svg.setAttribute('width', W);
      svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
      while (svg.firstChild) svg.removeChild(svg.firstChild);

      const M = { l: 48, r: 14, t: 12, b: 24 };
      const iw = W - M.l - M.r, ih = H - M.t - M.b;
      const ticks = niceTicks(maxY * 1.06 || 1, 4);
      const top = ticks[ticks.length - 1];
      const X = (ms) => M.l + (xs.length < 2 ? iw / 2 : ((ms - xs[0]) / (xs[xs.length - 1] - xs[0])) * iw);
      const Y = (v) => M.t + ih - (v / top) * ih;

      // 网格 + y 轴刻度（发丝线，数值紧凑）
      ticks.forEach((tv) => {
        const y = Y(tv);
        h('line', { x1: M.l, x2: W - M.r, y1: y, y2: y, stroke: 'var(--line-soft)', 'stroke-width': 1 }, svg);
        const t = h('text', { x: M.l - 8, y: y + 3.5, 'text-anchor': 'end', 'font-size': 10.5 }, svg);
        t.style.fill = 'var(--text-4)';
        t.style.fontFamily = 'var(--mono)';
        t.textContent = yFmt(tv);
      });

      // x 轴刻度：均匀取 5 个左右，首尾必有
      const nx = Math.min(5, xs.length);
      const idx = new Set();
      for (let i = 0; i < nx; i++) idx.add(Math.round((i / (nx - 1)) * (xs.length - 1)));
      idx.forEach((i) => {
        const t = h('text', { x: X(xs[i]), y: H - 7, 'text-anchor': i === 0 ? 'start' : i === xs.length - 1 ? 'end' : 'middle', 'font-size': 10.5 }, svg);
        t.style.fill = 'var(--text-4)';
        t.textContent = xFmt(xs[i]);
      });

      // 折线（点少时加圆点；area 时给第一条线铺一层 8% 的底）
      series.forEach((s, si) => {
        const m = ymap[si];
        let d = '', pen = false;
        xs.forEach((x) => {
          if (!m.has(x)) { pen = false; return; }
          d += (pen ? 'L' : 'M') + X(x).toFixed(1) + ' ' + Y(m.get(x)).toFixed(1);
          pen = true;
        });
        if (s.area && d) {
          let a = '', first = null, last = null;
          pen = false;
          xs.forEach((x) => {
            if (!m.has(x)) return;
            if (!first) first = x;
            last = x;
            a += (pen ? 'L' : 'M') + X(x).toFixed(1) + ' ' + Y(m.get(x)).toFixed(1);
            pen = true;
          });
          if (first != null) {
            a += 'L' + X(last).toFixed(1) + ' ' + (M.t + ih) + 'L' + X(first).toFixed(1) + ' ' + (M.t + ih) + 'Z';
            h('path', { d: a, fill: s.color || 'var(--accent)', 'fill-opacity': 0.07, stroke: 'none' }, svg);
          }
        }
        h('path', { d, fill: 'none', stroke: s.color || 'var(--accent)', 'stroke-width': 1.6, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, svg);
        if (xs.length <= 32) {
          xs.forEach((x) => {
            if (!m.has(x)) return;
            h('circle', { cx: X(x), cy: Y(m.get(x)), r: 2.6, fill: 'var(--bg)', stroke: s.color || 'var(--accent)', 'stroke-width': 1.4 }, svg);
          });
        }
      });

      // 悬浮：竖参考线 + 每条线一个点 + 明细框
      const guide = h('line', { y1: M.t, y2: M.t + ih, stroke: 'var(--line-strong)', 'stroke-width': 1, visibility: 'hidden' }, svg);
      const dots = series.map((s) =>
        h('circle', { r: 3.4, fill: 'var(--bg)', stroke: s.color || 'var(--accent)', 'stroke-width': 1.8, visibility: 'hidden' }, svg));

      svg.onpointermove = (ev) => {
        const rect = svg.getBoundingClientRect();
        const px = ev.clientX - rect.left;
        let best = 0, bd = Infinity;
        xs.forEach((x, i) => { const d2 = Math.abs(X(x) - px); if (d2 < bd) { bd = d2; best = i; } });
        const x = xs[best];
        guide.setAttribute('x1', X(x)); guide.setAttribute('x2', X(x));
        guide.setAttribute('visibility', 'visible');
        let html = '<div class="t">' + tipTitle(x) + '</div>';
        series.forEach((s, si) => {
          const m = ymap[si];
          const has = m.has(x);
          dots[si].setAttribute('cx', X(x));
          dots[si].setAttribute('cy', Y(has ? m.get(x) : 0));
          dots[si].setAttribute('visibility', has ? 'visible' : 'hidden');
          if (has) html += '<div class="r"><span class="dot" style="background:' + (s.color || 'var(--accent)') + '"></span>' +
            '<span>' + s.name + '</span><span class="n">' + tipFmt(m.get(x)) + '</span></div>';
        });
        const tot = series.reduce((acc, s) => { const m = ymap[series.indexOf(s)]; return acc + (m.has(x) ? m.get(x) : 0); }, 0);
        if (series.length > 1) html += '<div class="r" style="margin-top:2px;border-top:1px solid var(--line-soft);padding-top:3px"><span class="dot" style="background:transparent"></span><span>合计</span><span class="n">' + tipFmt(tot) + '</span></div>';
        tip.innerHTML = html;
        tip.hidden = false;
        const tw = tip.offsetWidth;
        const left = Math.min(Math.max(X(x) - tw / 2, 4), W - tw - 4);
        tip.style.left = left + 'px';
        tip.style.top = '6px';
      };
      svg.onpointerleave = () => {
        tip.hidden = true;
        guide.setAttribute('visibility', 'hidden');
        dots.forEach((d2) => d2.setAttribute('visibility', 'hidden'));
      };
    }

    const ro = ('ResizeObserver' in global) ? new ResizeObserver(() => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(draw);
    }) : null;
    if (ro) ro.observe(el);
    draw();

    return { destroy() { destroyed = true; if (ro) ro.disconnect(); } };
  }

  /**
   * 迷你趋势线：无轴无线框，一条线 + 底部 6% 淡铺 + 末点。
   * pts = [{x, y}]（x 只用于排序）；w/h 由容器决定（100% × 40px）。
   */
  function sparkline(el, pts, opt) {
    if (!el) return;
    const o = opt || {};
    const color = o.color || 'var(--accent)';
    const H = o.height || 40;
    el.innerHTML = '';
    el.classList.add('chartbox');
    const items = (pts || []).filter((p) => p && p.y != null).slice().sort((a, b) => a.x - b.x);
    if (items.length < 2) {
      const d = document.createElement('div');
      d.className = 'empty';
      d.style.cssText = 'height:' + H + 'px;display:flex;align-items:center';
      d.textContent = '还画不出趋势（不足两轮）';
      el.appendChild(d);
      return;
    }
    const svg = h('svg', { height: H, preserveAspectRatio: 'none' });
    el.appendChild(svg);

    let raf = 0;
    function draw() {
      if (!el.isConnected) return;
      const W = Math.max(60, el.clientWidth || 120);
      svg.setAttribute('width', W);
      svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      const ys = items.map((p) => Number(p.y) || 0);
      const max = Math.max.apply(null, ys) || 1;
      const min = Math.min.apply(null, ys);
      const span = (max - min) || 1;
      const X = (i) => 2 + (i / (items.length - 1)) * (W - 4);
      const Y = (v) => 4 + (1 - (v - min) / span) * (H - 10);
      let d = '', area = '';
      items.forEach((p, i) => {
        d += (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(Number(p.y) || 0).toFixed(1);
      });
      area = d + 'L' + X(items.length - 1).toFixed(1) + ' ' + (H - 2) + 'L' + X(0).toFixed(1) + ' ' + (H - 2) + 'Z';
      h('path', { d: area, fill: color, 'fill-opacity': 0.08, stroke: 'none' }, svg);
      h('path', { d, fill: 'none', stroke: color, 'stroke-width': 1.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, svg);
      const last = items[items.length - 1];
      h('circle', { cx: X(items.length - 1), cy: Y(Number(last.y) || 0), r: 2.4, fill: color }, svg);
    }

    const ro = ('ResizeObserver' in global) ? new ResizeObserver(() => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(draw);
    }) : null;
    if (ro) ro.observe(el);
    draw();
  }

  global.U = global.U || {};
  global.U.fmtNum = fmtNum;
  global.U.fmtTok = fmtTok;
  global.U.lineChart = lineChart;
  global.U.sparkline = sparkline;
})(window);
