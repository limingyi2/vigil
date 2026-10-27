// Read-only Memory Tide view for the local Brain memory API.
(function (global) {
  'use strict';

  const rendererModuleUrl = new URL('../memory-atlas/tide-renderer.js', document.currentScript.src);
  const palette = ['#dce9df', '#accdd8', '#f1cc91', '#b2d6c9', '#ccc0e0', '#ddb9a2', '#aec7dc'];
  function storeLabel(name) {
    if (name === 'memory') return '主仓库';
    const stem = String(name).replace(/\.memory$/, '');
    const channels = [['wechat_group', '微信群'], ['wechat', '微信'], ['app', '旧 App'], ['pc', 'PC']];
    for (const [key, label] of channels) {
      const suffix = '.' + key;
      if (stem.endsWith(suffix)) {
        const persona = stem.slice(0, -suffix.length);
        return (persona ? persona + ' · ' : '默认人设 · ') + label + ' 渠道';
      }
    }
    return String(name) + ' · 历史分区';
  }
  const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g,
    (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);

  async function mount(host) {
    const api = global.API || global.VigilAPI;
    if (!api || !host) throw new Error('客户端 API 不可用');
    let disposed = false;
    let graphics = null;
    let frame = 0;
    let poll = 0;
    let resizeObserver = null;
    let memories = [];
    let edges = [];
    let points = [];
    let flashIds = new Set();
    let knownIds = new Set();
    let initialLoadDone = false;
    let currentStore = 'memory';
    let selectedCategory = '全部记忆';
    let search = '';
    let hovered = null;
    let pinned = null;
    let rotationX = -.06;
    let rotationY = .04;
    let zoom = 1;
    let autoRotate = !global.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let nebulaEnabled = false;
    let drag = null;
    let lastFrame = 0;
    let snapshotGeneration = 0;

    try { nebulaEnabled = global.localStorage.getItem('vigil-memory-atlas-nebula') === 'on'; } catch (_) {}

    host.innerHTML = `
      <section class="ma-shell" aria-label="记忆星图">
        <div class="ma-stage" id="maStage">
          <div class="ma-heading"><span>PERSONAL MEMORY SPACE</span><h2>记忆，彼此相连。</h2><p>每一颗星，都是一段已保存的真实记忆</p></div>
          <div class="ma-dimension" id="maDimension">读取本机向量中</div>
          <div class="ma-labels" id="clusterLabels" aria-hidden="true"></div>
          <canvas id="maCanvas" role="img" aria-label="本机记忆三维星图。拖动旋转，滚轮缩放，悬停查看记忆。"></canvas>
          <div class="ma-stage-message" id="maMessage" role="status"></div>
          <div class="ma-controls" aria-label="星图视角控制">
            <button type="button" id="maRotate" aria-label="暂停自动旋转" title="暂停自动旋转">Ⅱ</button>
            <button type="button" id="maZoomOut" aria-label="缩小" title="缩小">−</button>
            <button type="button" id="maZoomIn" aria-label="放大" title="放大">＋</button>
            <button type="button" id="maReset" aria-label="重置视角" title="重置视角">↺</button>
            <button type="button" id="maNebula" aria-pressed="false" title="开启星云">星云</button>
          </div>
          <div class="ma-hint">拖拽旋转 <i>·</i> 滚轮缩放 <i>·</i> 悬停查看记忆</div>
        </div>
        <aside class="ma-inspector">
          <div class="ma-library">
            <div class="ma-rowhead"><span>记忆簇</span><span class="ma-count" id="maClusterCount">—</span></div>
            <label class="ma-search"><span aria-hidden="true">⌕</span><input id="maSearch" type="search" placeholder="搜索真实记忆…" aria-label="搜索记忆"></label>
            <div class="ma-clusters" id="maClusters"></div>
            <div class="ma-stats"><div><b id="maTotal">—</b><span>可视记忆</span></div><div><b id="maEdges">—</b><span>语义连接</span></div></div>
            <div class="ma-data-note" id="maDataNote">星点位置来自本机已有向量；本页面不会生成或写入记忆。</div>
          </div>
          <div class="ma-detail-head"><span id="maDetailTitle">记忆详情</span><span class="ma-count" id="maDetailCount">悬停星点</span></div>
          <div class="ma-detail" id="maDetail"><div class="ma-empty">将鼠标移到星点上，查看这条记忆和相似连接。</div></div>
          <div class="ma-footer"><label for="maStore">记忆分区</label><select id="maStore" aria-label="选择记忆分区"></select></div>
          <div class="ma-footer"><span id="maSyncStatus">本机 Brain · 只读</span><button class="ma-refresh" id="maRefresh" type="button">刷新</button></div>
        </aside>
      </section>`;

    const $ = (selector) => host.querySelector(selector);
    const canvas = $('#maCanvas');
    const message = $('#maMessage');
    const tooltip = $('#maDetail');
    const stage = $('#maStage');
    const storeSelect = $('#maStore');

    function active(memory) {
      const matchesCategory = selectedCategory === '全部记忆' || memory.category === selectedCategory;
      const query = search.toLocaleLowerCase();
      const matchesSearch = !query || `${memory.text} ${memory.category} ${(memory.tags || []).join(' ')}`
        .toLocaleLowerCase().includes(query);
      return matchesCategory && matchesSearch;
    }

    function setMessage(text, kind) {
      message.textContent = text || '';
      message.className = 'ma-stage-message' + (text ? ' visible' : '') + (kind ? ' ' + kind : '');
    }

    function updateRotationButton() {
      const button = $('#maRotate');
      button.textContent = autoRotate ? 'Ⅱ' : '▶';
      button.title = autoRotate ? '暂停自动旋转' : '开始自动旋转';
      button.setAttribute('aria-label', button.title);
    }

    function showDetail(index) {
      const title = $('#maDetailTitle');
      const count = $('#maDetailCount');
      const body = $('#maDetail');
      if (index == null || !memories[index]) {
        title.textContent = '记忆详情';
        count.textContent = '悬停星点';
        body.innerHTML = '<div class="ma-empty">将鼠标移到星点上，查看这条记忆和相似连接。</div>';
        return;
      }
      const memory = memories[index];
      title.textContent = '记忆详情';
      count.textContent = 'NODE ' + String(index + 1).padStart(3, '0');
      const similar = edges.filter((edge) => edge.a === index || edge.b === index)
        .sort((a, b) => b.sim - a.sim).slice(0, 3);
      body.replaceChildren();
      const meta = document.createElement('div');
      meta.className = 'ma-memory-meta';
      const dot = document.createElement('i');
      dot.style.background = palette[Math.max(0, [...new Set(memories.map((item) => item.category))].indexOf(memory.category)) % palette.length];
      const label = document.createElement('span');
      label.textContent = memory.category || '记忆';
      const date = document.createElement('time');
      date.textContent = memory.created_at || '';
      meta.append(dot, label, date);
      const text = document.createElement('p');
      text.className = 'ma-memory-text';
      text.textContent = memory.text || '';
      body.append(meta, text);
      if ((memory.tags || []).length) {
        const tags = document.createElement('div');
        tags.className = 'ma-tags';
        for (const value of memory.tags) {
          const chip = document.createElement('span');
          chip.textContent = '#' + value;
          tags.append(chip);
        }
        body.append(tags);
      }
      for (const edge of similar) {
        const other = memories[edge.a === index ? edge.b : edge.a];
        if (!other) continue;
        const row = document.createElement('div');
        row.className = 'ma-similarity';
        const name = document.createElement('span');
        name.textContent = '相似记忆';
        const track = document.createElement('i');
        const fill = document.createElement('b');
        fill.style.width = Math.max(0, Math.min(100, Math.round(edge.sim * 100))) + '%';
        track.append(fill);
        const score = document.createElement('strong');
        score.textContent = Math.round(edge.sim * 100) + '%';
        row.append(name, track, score);
        body.append(row);
      }
    }

    function updateControls() {
      const categories = [...new Set(memories.map((memory) => memory.category || '未分类'))];
      const list = $('#maClusters');
      list.replaceChildren();
      const values = ['全部记忆', ...categories];
      values.forEach((category, index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'ma-cluster' + (category === selectedCategory ? ' selected' : '');
        button.setAttribute('aria-pressed', String(category === selectedCategory));
        const dot = document.createElement('i');
        dot.style.color = palette[Math.max(0, index - 1) % palette.length];
        const name = document.createElement('span');
        name.textContent = category;
        const count = document.createElement('small');
        count.textContent = String(category === '全部记忆' ? memories.length : memories.filter((memory) => memory.category === category).length);
        button.append(dot, name, count);
        button.addEventListener('click', () => {
          selectedCategory = category;
          hovered = pinned = null;
          showDetail(null);
          updateControls();
        });
        list.append(button);
      });
      $('#maClusterCount').textContent = String(categories.length).padStart(2, '0') + ' 类';
      $('#maTotal').textContent = String(memories.length);
      $('#maEdges').textContent = String(edges.length);
      $('#maDimension').textContent = memories.length
        ? `本机向量 · ${Number($('#maDimension').dataset.dimension || 0)}D → 3D`
        : '等待可视记忆';
      const note = $('#maDataNote');
      note.textContent = memories.length
        ? `展示 ${memories.length} 条已向量化记忆；${Number(note.dataset.unembedded || 0)} 条尚无向量，不绘制假星点。`
        : '没有可显示的已向量化记忆。记忆写入仍由现有聊天流程完成。';
    }

    async function loadStores() {
      const response = api.pick(await api.get('/api/memory/stores'));
      if (!response.ok) throw new Error(response._err || '无法读取记忆分区');
      const names = Array.isArray(response.items) ? response.items : [];
      const choices = names.length ? names : ['memory'];
      storeSelect.replaceChildren();
      for (const name of choices) {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = storeLabel(name);
        storeSelect.append(option);
      }
      storeSelect.title = '记忆主仓库（唯一记忆库）；历史分区仅在残留数据时列出，不会搬动或删除数据。';
      currentStore = choices.includes('memory') ? 'memory' : choices[0];
      storeSelect.value = currentStore;
    }

    async function loadSnapshot(isPoll) {
      if (disposed || !currentStore) return;
      const generation = ++snapshotGeneration;
      $('#maSyncStatus').textContent = isPoll ? '正在同步…' : '正在读取本机记忆…';
      const query = `/api/memory/atlas?store=${encodeURIComponent(currentStore)}&limit=100`;
      const response = api.pick(await api.get(query, { timeoutMs: 12000 }));
      if (disposed || generation !== snapshotGeneration) return;
      if (!response.ok) {
        $('#maSyncStatus').textContent = '读取失败';
        setMessage(response._err || '无法读取该分区的记忆星图。', 'error');
        return;
      }
      const next = (response.items || []).map((item) => ({
        ...item,
        id: String(item.id || ''),
        category: String(item.category || '记忆'),
        text: String(item.text || ''),
        tags: Array.isArray(item.tags) ? item.tags.map(String) : [],
        pos: Array.isArray(item.pos) ? item.pos.map(Number) : [0, 0, 0],
        flashAt: 0,
      }));
      if (isPoll && initialLoadDone) {
        const now = performance.now();
        for (const item of next) {
          if (!knownIds.has(item.id)) {
            item.flashAt = now;
            flashIds.add(item.id);
          }
        }
      }
      knownIds = new Set(next.map((item) => item.id));
      initialLoadDone = true;
      memories = next;
      edges = Array.isArray(response.edges) ? response.edges : [];
      $('#maDimension').dataset.dimension = String(response.dimension || 0);
      $('#maDataNote').dataset.unembedded = String(response.unembedded || 0);
      $('#maSyncStatus').textContent = `本机只读 · ${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} 更新`;
      setMessage(response.total && !memories.length
        ? `记忆共 ${response.total} 条，但当前都没有可用向量。此页面不会为它们生成假坐标。`
        : '', response.total && !memories.length ? 'empty' : '');
      updateControls();
      if (hovered != null && !memories[hovered]) hovered = null;
      if (pinned != null && !memories[pinned]) pinned = null;
      showDetail(hovered ?? pinned);
    }

    function nearest(x, y) {
      let best = null;
      let distance = 19;
      for (const point of points) {
        if (!point.visible) continue;
        const d = Math.hypot(point.x - x, point.y - y);
        if (d < distance) { best = point.i; distance = d; }
      }
      return best;
    }

    function draw(now) {
      if (disposed || !graphics) return;
      const dt = lastFrame ? Math.min(50, now - lastFrame) : 0;
      lastFrame = now;
      if (autoRotate && !drag) rotationY += dt * .000008;
      points = graphics.render({ memories, edges, rotationX, rotationY, zoom, hovered, pinned,
        active, now, flashIds, nebulaEnabled });
      frame = global.requestAnimationFrame(draw);
    }

    function onPointerDown(event) {
      pointerX = event.clientX;
      pointerY = event.clientY;
      drag = { x: event.clientX, y: event.clientY, rx: rotationX, ry: rotationY, moved: false };
      canvas.setPointerCapture(event.pointerId);
    }
    function onPointerMove(event) {
      const rect = canvas.getBoundingClientRect();
      if (drag) {
        const dx = event.clientX - drag.x;
        const dy = event.clientY - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
        rotationY = drag.ry + dx * .006;
        rotationX = Math.max(-1.45, Math.min(1.45, drag.rx + dy * .006));
        return;
      }
      const index = nearest(event.clientX - rect.left, event.clientY - rect.top);
      if (index !== hovered) {
        hovered = index;
        showDetail(index ?? pinned);
      }
    }
    function onPointerUp(event) {
      if (!drag) return;
      if (!drag.moved) {
        pointerX = event.clientX;
        pointerY = event.clientY;
        const rect = canvas.getBoundingClientRect();
        const index = nearest(pointerX - rect.left, pointerY - rect.top);
        pinned = index === pinned ? null : index;
        showDetail(hovered ?? pinned);
      }
      drag = null;
    }
    let pointerX = 0;
    let pointerY = 0;
    canvas.addEventListener('pointerdown', onPointerDown);
    canvas.addEventListener('pointermove', (event) => { pointerX = event.clientX; pointerY = event.clientY; onPointerMove(event); });
    canvas.addEventListener('pointerup', onPointerUp);
    canvas.addEventListener('pointerleave', () => { if (!drag) { hovered = null; showDetail(pinned); } });
    canvas.addEventListener('wheel', (event) => {
      event.preventDefault();
      zoom = Math.max(.65, Math.min(1.8, zoom * (event.deltaY < 0 ? 1.08 : .92)));
    }, { passive: false });
    $('#maSearch').addEventListener('input', (event) => {
      search = event.target.value.trim();
      hovered = pinned = null;
      showDetail(null);
    });
    $('#maRotate').addEventListener('click', () => { autoRotate = !autoRotate; updateRotationButton(); });
    $('#maZoomIn').addEventListener('click', () => { zoom = Math.min(1.8, zoom * 1.12); });
    $('#maZoomOut').addEventListener('click', () => { zoom = Math.max(.65, zoom / 1.12); });
    $('#maReset').addEventListener('click', () => { rotationX = -.06; rotationY = .04; zoom = 1; });
    $('#maNebula').addEventListener('click', (event) => {
      nebulaEnabled = !nebulaEnabled;
      event.currentTarget.setAttribute('aria-pressed', String(nebulaEnabled));
      event.currentTarget.title = nebulaEnabled ? '关闭星云' : '开启星云';
      try { global.localStorage.setItem('vigil-memory-atlas-nebula', nebulaEnabled ? 'on' : 'off'); } catch (_) {}
    });
    $('#maRefresh').addEventListener('click', () => loadSnapshot(true));
    storeSelect.addEventListener('change', async () => {
      currentStore = storeSelect.value;
      snapshotGeneration++;
      knownIds = new Set();
      initialLoadDone = false;
      selectedCategory = '全部记忆';
      hovered = pinned = null;
      memories = [];
      edges = [];
      flashIds.clear();
      updateControls();
      showDetail(null);
      setMessage('正在读取所选分区…', 'empty');
      await loadSnapshot(false);
    });

    try {
      const module = await import(rendererModuleUrl.href);
      graphics = await module.createTideRenderer(canvas);
      if (disposed) { graphics.dispose?.(); return () => {}; }
      resizeObserver = new ResizeObserver(() => graphics && graphics.resize());
      resizeObserver.observe(canvas);
      graphics.resize();
      frame = global.requestAnimationFrame(draw);
      updateRotationButton();
      $('#maNebula').setAttribute('aria-pressed', String(nebulaEnabled));
      $('#maNebula').title = nebulaEnabled ? '关闭星云' : '开启星云';
    } catch (error) {
      console.error('[memory-atlas] renderer initialization failed', error);
      $('#maSyncStatus').textContent = '星图启动失败';
      setMessage('星图渲染器未能启动。请检查 WebGL 2 或查看客户端日志。', 'error');
    }

    if (graphics) {
      try {
      await loadStores();
      await loadSnapshot(false);
      poll = global.setInterval(() => loadSnapshot(true), 15000);
      } catch (error) {
        console.error('[memory-atlas] API initialization failed', error);
        $('#maSyncStatus').textContent = '无法读取本机记忆';
        setMessage(error && error.message || '无法读取本机记忆分区。', 'error');
      }
    }

    return function dispose() {
      if (disposed) return;
      disposed = true;
      global.clearInterval(poll);
      global.cancelAnimationFrame(frame);
      resizeObserver?.disconnect();
      graphics?.dispose?.();
      graphics = null;
      host.replaceChildren();
    };
  }

  global.MemoryAtlasPage = { mount };
})(window);
