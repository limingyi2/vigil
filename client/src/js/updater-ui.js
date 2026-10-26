// -*- coding: utf-8 -*-
/**
 * 左下角的更新圆圈：有新版本才出现，点一下开始下载，进度是圆环慢慢闭合，
 * 下好以后再点一下安装。没有新版时整个按钮隐藏 —— 不弹窗，也不占常驻位置。
 *
 * 状态全部来自主进程（updater:state 快照 + 事件），这里只负责画：
 *   available   空环 + 向上箭头，点一下下载
 *   downloading 圆环按百分比闭合，中间写百分比
 *   verifying   环满 + 省略号，转一圈表示在算 sha256
 *   ready       环满 + 箭头，点一下安装（会退出客户端）
 *   error       红环 + 感叹号，点一下重试
 *
 * 挂载点由 app.js 在 buildNav 之后给（gnav 底部那一格里），
 * 因为导航是每次重建的，这里必须能重挂而不是只在启动时插一次。
 */
(function () {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const R = 19;                       // 半径（viewBox 44×44，留 3px 描边余量）
  const C = 2 * Math.PI * R;          // 周长：dasharray 用，闭合比例靠 dashoffset

  let btn = null;
  let bar = null;
  let glyph = null;
  let tip = null;
  let bound = false;
  let state = { phase: 'idle' };
  let lastToastPhase = '';

  const clamp = (n) => Math.max(0, Math.min(100, Math.round(Number(n) || 0)));

  function bytes(n) {
    const v = Number(n) || 0;
    if (v >= 1024 * 1024 * 1024) return (v / 1024 / 1024 / 1024).toFixed(1) + ' GB';
    if (v >= 1024 * 1024) return (v / 1024 / 1024).toFixed(0) + ' MB';
    if (v >= 1024) return (v / 1024).toFixed(0) + ' KB';
    return v + ' B';
  }

  const arrowSvg = '<svg class="i" aria-hidden="true"><use href="#i-arrow-up"/></svg>';

  function tipText(s) {
    const ver = s.ver ? 'v' + s.ver : '新版本';
    switch (s.phase) {
      case 'available': return '发现新版本 ' + ver + ' · 点击下载';
      case 'downloading': return '正在下载 ' + clamp(s.percent) + '%' +
        (s.total ? '（' + bytes(s.got) + ' / ' + bytes(s.total) + '）' : '');
      case 'verifying': return '正在校验安装包…';
      case 'ready': return '更新已下载 · 点击安装（会关闭客户端）';
      case 'installing': return '正在启动安装程序…';
      case 'error': return '更新失败：' + (s.err || '未知错误') + ' · 点击重试';
      default: return '';
    }
  }

  function build() {
    const el = document.createElement('button');
    el.type = 'button';
    el.id = 'updRing';
    el.className = 'upd-ring';
    el.hidden = true;

    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('class', 'upd-ring-svg');
    svg.setAttribute('viewBox', '0 0 44 44');
    svg.setAttribute('aria-hidden', 'true');
    const track = document.createElementNS(NS, 'circle');
    track.setAttribute('class', 'upd-ring-track');
    track.setAttribute('cx', '22'); track.setAttribute('cy', '22'); track.setAttribute('r', String(R));
    bar = document.createElementNS(NS, 'circle');
    bar.setAttribute('class', 'upd-ring-bar');
    bar.setAttribute('cx', '22'); bar.setAttribute('cy', '22'); bar.setAttribute('r', String(R));
    bar.style.strokeDasharray = C.toFixed(2);
    bar.style.strokeDashoffset = C.toFixed(2);
    svg.appendChild(track); svg.appendChild(bar);

    glyph = document.createElement('span');
    glyph.className = 'upd-ring-glyph';

    tip = document.createElement('span');
    tip.className = 'upd-ring-tip';
    tip.setAttribute('role', 'tooltip');

    el.appendChild(svg); el.appendChild(glyph); el.appendChild(tip);
    el.addEventListener('click', onClick);
    return el;
  }

  // 点一下：没下载就开始下；下好了就装；出错就重试。下载/校验/安装中不响应。
  async function onClick() {
    const api = window.vigil && window.vigil.updater;
    if (!api) return;
    const phase = state.phase;
    try {
      if (phase === 'available' || phase === 'error') await api.download();
      else if (phase === 'ready') await api.install();
    } catch (e) {
      if (window.U && window.U.toast) window.U.toast('更新操作失败：' + String((e && e.message) || e));
    }
  }

  function paint() {
    if (!btn) return;
    const phase = state.phase || 'idle';
    if (phase === 'idle') {
      btn.hidden = true;
      document.body.classList.remove('has-update');
      return;
    }
    btn.hidden = false;
    btn.dataset.phase = phase;
    document.body.classList.add('has-update');

    // 只有下载/校验/安装才画进度；available 停在 0（空环 + 箭头）
    let pct = 0;
    if (phase === 'downloading') pct = clamp(state.percent);
    else if (phase === 'verifying' || phase === 'ready' || phase === 'installing') pct = 100;
    bar.style.strokeDashoffset = (C * (1 - pct / 100)).toFixed(2);

    if (phase === 'downloading') glyph.innerHTML = '<b>' + pct + '%</b>';
    else if (phase === 'verifying' || phase === 'installing') glyph.innerHTML = '<b>···</b>';
    else if (phase === 'error') glyph.innerHTML = '<b>!</b>';
    else glyph.innerHTML = arrowSvg;

    const text = tipText(state);
    tip.textContent = text;
    btn.setAttribute('aria-label', text);
    btn.setAttribute('title', '');   // 用自绘提示，别叠一个系统 tooltip

    // 只在"下好了"和"失败了"提醒一次：这两件事用户不知道会一直等下去
    if (phase !== lastToastPhase && window.U && window.U.toast) {
      if (phase === 'ready') window.U.toast('更新已下载好，点左下角圆圈安装');
      else if (phase === 'error') window.U.toast('更新没成功：' + (state.err || '未知错误'));
    }
    lastToastPhase = phase;
  }

  function adopt(snapshot) {
    if (!snapshot || !snapshot.phase) return;
    state = snapshot;
    paint();
  }

  function mount(host) {
    if (!host) return;
    if (!btn) btn = build();
    // 挂在设置按钮上方；导航重建时这格会被换掉，所以每次都重插一遍
    host.insertBefore(btn, host.firstElementChild);
    paint();
  }

  function start() {
    const api = window.vigil && window.vigil.updater;
    if (!api || bound) return;
    bound = true;
    if (typeof api.onState === 'function') api.onState(adopt);
    Promise.resolve(api.state()).then(adopt).catch(() => { /* 主进程还没准备好 */ });
  }

  window.VigilUpdater = { mount, start, state: () => state, _adopt: adopt };
  start();
})();
