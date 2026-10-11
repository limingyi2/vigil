// -*- coding: utf-8 -*-
/**
 * 小工具：DOM、格式化、提示、二级导航。
 * 没有框架，就这几件事 —— 用 innerHTML 拼字符串，所有插值都过 esc()。
 */
(function (global) {
  'use strict';

  /** HTML 转义。**所有**要插进 innerHTML 的变量都必须过这里。 */
  function esc(v) {
    return String(v === undefined || v === null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /** 图标：`svg` 名字对应 index.html 里的 <symbol id="i-名字"> */
  function icon(name, cls) {
    return '<svg class="i ' + (cls || '') + '"><use href="#i-' + esc(name) + '"/></svg>';
  }

  function el(html) {
    const t = document.createElement('template');
    t.innerHTML = String(html || '').trim();
    return t.content.firstElementChild;
  }

  /** 时间戳 → 14:32 / 10-06 / 2026-10-06 */
  function fmtTime(t, style) {
    const n = typeof t === 'number' ? (t < 1e12 ? t * 1000 : t) : Date.parse(t);
    if (!n || isNaN(n)) return String(t || '');
    const d = new Date(n);
    const p = (x) => String(x).padStart(2, '0');
    const hm = p(d.getHours()) + ':' + p(d.getMinutes());
    if (style === 'time') return hm;
    if (style === 'day') return p(d.getMonth() + 1) + '-' + p(d.getDate());
    if (style === 'full') return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + hm;
    return hm;
  }

  /** 相对时间：刚刚 / 3 分钟前 / 昨天 / 10-06 */
  function fmtAgo(t) {
    const n = typeof t === 'number' ? (t < 1e12 ? t * 1000 : t) : Date.parse(t);
    if (!n || isNaN(n)) return '';
    const s = (Date.now() - n) / 1000;
    if (s < 60) return '刚刚';
    if (s < 3600) return Math.floor(s / 60) + ' 分钟前';
    if (s < 86400) return Math.floor(s / 3600) + ' 小时前';
    if (s < 172800) return '昨天';
    return fmtTime(n, 'day');
  }

  function bytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(1) + ' MB';
  }

  let toastBox = null;
  function toast(msg, ms) {
    if (!toastBox) toastBox = document.getElementById('toast');
    if (!toastBox) return;
    const d = document.createElement('div');
    d.textContent = String(msg == null ? '' : msg);
    toastBox.appendChild(d);
    setTimeout(() => d.remove(), ms || 2600);
  }

  /**
   * 分段器：点按钮切换同一页里的内容块（每页只显示一件事）。
   * 作用域必须落在同一页 —— 面板和主区用同一个 key 关联。
   */
  function bindSeg(segEl, onPick) {
    if (!segEl) return;
    const btns = segEl.querySelectorAll('button[data-sub]');
    btns.forEach((b) => b.addEventListener('click', () => {
      btns.forEach((x) => x.classList.toggle('on', x === b));
      onPick && onPick(b.dataset.sub);
    }));
  }

  /** 空态：一句话说清楚"为什么没有"和"下一步做什么"，不要只写"暂无数据" */
  function empty(text, hint) {
    return '<div class="empty">' + esc(text) + (hint ? '<br><span class="muted">' + esc(hint) + '</span>' : '') + '</div>';
  }

  /**
   * 自动探测失败的说明。把"试过哪些地址、各自什么结果"摆出来，
   * 而不是丢一句"连不上"让人猜。
   */
  function autoHintHtml(auto) {
    const tried = (auto && auto.tried) || [];
    const head = auto && auto.needToken
      ? '地址能连上，但服务端不认这是本机请求 —— 它只把 token 交给回环地址。'
      : '没找到大脑。它要么没起来，要么不在这几个地址上。';
    return '<b style="font-size:13px">' + esc(head) + '</b>' +
      '<div class="muted" style="font-size:12px;margin-top:6px;line-height:1.7">' +
      '客户端按顺序试：SSH 隧道 <span class="mono">127.0.0.1:18787</span> → 本机 brain <span class="mono">127.0.0.1:8788</span>。' +
      '这两个只要有一个通，就什么都不用填。</div>' +
      (tried.length
        ? '<div class="mono" style="font-size:11.5px;margin-top:8px;color:var(--text-4)">' +
          tried.map((x) => esc(x)).join('<br>') + '</div>'
        : '') +
      '<div class="muted" style="font-size:11.5px;margin-top:8px">' +
      '直连云机（公网 IP）时拿不到 token，必须在下面手填一次。</div>';
  }

  /**
   * 轻量 Markdown 渲染：标题 / 加粗 / 斜体 / 列表 / 代码块 / 行内代码 / 链接。
   * 不依赖外部库，足够 AI 回复用。用户消息不走这个（纯文本更安全）。
   */
  function md(text, options) {
    const codeActions = !!(options && options.codeActions);
    const raw = String(text == null ? '' : text);
    if (!raw) return '';
    // 代码块先拎出来，免得里面的 * # 被当格式
    const blocks = [];
    let s = raw.replace(/```([\s\S]*?)```/g, (_, code) => {
      blocks.push(code);
      return '\x00CODE' + (blocks.length - 1) + '\x00';
    });
    // 逐行处理
    const lines = s.replace(/\r/g, '').split('\n');
    const out = [];
    let inUl = false, inOl = false;
    const closeLists = () => {
      if (inUl) { out.push('</ul>'); inUl = false; }
      if (inOl) { out.push('</ol>'); inOl = false; }
    };
    for (let line of lines) {
      const trimmed = line.trim();
      if (!trimmed) { closeLists(); out.push(''); continue; }
      if (codeActions && /^\x00CODE\d+\x00$/.test(trimmed)) {
        closeLists(); out.push(trimmed); continue;
      }
      // 标题
      let m = trimmed.match(/^(#{1,4})\s+(.*)$/);
      if (m) {
        closeLists();
        const level = m[1].length;
        out.push('<h' + level + '>' + inline(m[2]) + '</h' + level + '>');
        continue;
      }
      // 无序列表
      m = trimmed.match(/^[-*+]\s+(.*)$/);
      if (m) {
        if (inOl) { out.push('</ol>'); inOl = false; }
        if (!inUl) { out.push('<ul>'); inUl = true; }
        out.push('<li>' + inline(m[1]) + '</li>');
        continue;
      }
      // 有序列表
      m = trimmed.match(/^\d+\.\s+(.*)$/);
      if (m) {
        if (inUl) { out.push('</ul>'); inUl = false; }
        if (!inOl) { out.push('<ol>'); inOl = true; }
        out.push('<li>' + inline(m[1]) + '</li>');
        continue;
      }
      // 引用
      m = trimmed.match(/^>\s?(.*)$/);
      if (m) {
        closeLists();
        out.push('<blockquote>' + inline(m[1]) + '</blockquote>');
        continue;
      }
      // 普通段落
      closeLists();
      out.push('<p>' + inline(trimmed) + '</p>');
    }
    closeLists();
    let html = out.join('\n');
    // 把代码块放回去
    html = html.replace(/\x00CODE(\d+)\x00/g, (_, i) => {
      const block = blocks[Number(i)];
      let code = block.replace(/^\n/, '');
      if (!codeActions) return '<pre><code>' + esc(code) + '</code></pre>';
      const match = block.match(/^([\w.+#-]+)\r?\n/);
      const language = match ? match[1] : '代码';
      code = match ? block.slice(match[0].length) : block.replace(/^\r?\n/, '');
      return '<div class="codeblock"><div class="codehead"><span>' + esc(language) + '</span>' +
        '<button type="button" data-copy-code aria-label="复制代码">复制</button></div>' +
        '<pre><code>' + esc(code) + '</code></pre></div>';
    });
    return html;

    function inline(t) {
      return esc(t)
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
        .replace(/\*([^*]+)\*/g, '<i>$1</i>')
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    }
  }

  global.U = { esc, icon, el, fmtTime, fmtAgo, bytes, toast, bindSeg, empty, autoHintHtml, md };
})(window);
