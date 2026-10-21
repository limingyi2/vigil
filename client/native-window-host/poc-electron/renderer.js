(() => {
  const api = window.p15Host;
  const input = document.getElementById('exePath');
  const args = document.getElementById('arguments');
  const surface = document.getElementById('surface');
  const status = document.getElementById('status');
  const events = document.getElementById('events');
  let hosted = false;

  function readBounds() {
    const rect = surface.getBoundingClientRect();
    return { x: rect.left, y: rect.top, width: rect.width, height: rect.height, dpr: window.devicePixelRatio || 1 };
  }
  function syncBounds() { api.setBounds(readBounds()); }
  function note(text) {
    status.textContent = text;
    const line = document.createElement('div'); line.textContent = new Date().toLocaleTimeString() + ' ' + text;
    events.prepend(line);
    while (events.children.length > 4) events.lastElementChild.remove();
  }
  async function start(config) {
    const exePath = config && config.exePath || input.value;
    if (!exePath) { note('请先通过文件选择器指定 EXE。'); return; }
    input.value = exePath;
    try {
      syncBounds();
      await api.start({ exePath, arguments: config && config.arguments !== undefined ? config.arguments : args.value, bounds: readBounds() });
      note('已启动进程，正在查找窗口…');
    } catch (error) { note('启动失败：' + (error && error.message || error)); }
  }

  document.getElementById('choose').addEventListener('click', async () => {
    try { const path = await api.pickExe(); if (path) { input.value = path; note('已选择 ' + path); } }
    catch (error) { note('选择失败：' + (error && error.message || error)); }
  });
  document.getElementById('start').addEventListener('click', () => start());
  document.getElementById('attach').addEventListener('click', () => { syncBounds(); api.action('ATTACH'); api.action('SHOW'); note('正在重新嵌入…'); });
  document.getElementById('detach').addEventListener('click', () => { api.action('DETACH'); hosted = false; surface.classList.remove('hosted'); note('正在恢复软件原窗口…'); });
  document.getElementById('focus').addEventListener('click', () => api.action('FOCUS'));
  document.getElementById('closeApp').addEventListener('click', () => { api.action('CLOSE_APP'); note('已请求软件自行关闭（WM_CLOSE）；不会强制结束进程。'); });
  document.getElementById('closeWindow').addEventListener('click', () => api.action('CLOSE_WINDOW'));
  document.getElementById('dpr').textContent = 'DPI 比例 ' + (window.devicePixelRatio || 1).toFixed(2) + '×';

  const observer = new ResizeObserver(syncBounds);
  observer.observe(surface);
  window.addEventListener('resize', syncBounds);
  api.onEvent(event => {
    if (!event || !event.type) return;
    if (event.type === 'attached') {
      hosted = true; surface.classList.add('hosted');
      note(`已托管 PID ${event.pid} / HWND ${event.hwnd} · ${event.dpi} DPI`);
      syncBounds();
    } else if (event.type === 'fallback') {
      hosted = false; surface.classList.remove('hosted');
      note('未能嵌入，已恢复为独立窗口：' + event.reason);
    } else if (event.type === 'detached') {
      hosted = false; surface.classList.remove('hosted');
      note(event.restored ? '已解除托管，原窗口样式和位置已恢复。' : '解除托管未能确认完全恢复，请检查软件独立窗口。');
    } else if (event.type === 'error') {
      note(`原生窗口错误 ${event.code}（${event.stage}）：${event.message}`);
    } else if (event.type === 'started') note('进程已启动，PID ' + event.pid + '，正在发现主窗口…');
    else if (event.type === 'recreated') note('检测到软件窗口重建，正在重新托管…');
    else if (event.type === 'helper-process-error') note('原生宿主不可用：' + event.message + '。请独立启动软件。');
  });
  api.onAutoStart(config => start(config));
  syncBounds();
})();
