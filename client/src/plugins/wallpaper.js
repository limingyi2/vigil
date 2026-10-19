// Vigil UI adapter; actual Wallpaper Engine host and WebWallGL are vendored upstream.
(function () {
  'use strict';
  const defaults = { settingsVersion: 7, id: '', scrim: .24, blur: 10, glassAlpha: 78, wallpaperOpacity: 0,
    wallpaperBlur: 0, backgroundBrightness: 100, backgroundContrast: 100, backgroundSaturate: 100,
    objectFit: 'cover', sceneLiveFps: 30, videoVolume: .5, videoAudioEnabled: false,
    playbackRate: 1, pauseOnHidden: true, pauseOnBlur: false, flip: false };
  let settings = { ...defaults }, inventory = { wallpapers: [] }, base = '', started, view, tab = 'library';
  let error = '', status = '未选择壁纸', stage, media, scrim, frame, video, selected, generation = 0;
  let paused = false, serial = Promise.resolve(), persistTimer, fallbackTimer, firstFrame = false, frameReady = false;
  let lastControl = '', currentKey = '', search = '', filter = 'all', rotationTimer, lastSelectedId = '';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const typeName = t => ({ scene: '实时场景', web: '网页', video: '视频', image: '图片', application: '应用（不支持）' }[t] || t);
  const api = async (method, resource, body) => {
    const r = await window.vigil.wallpaper.request(method, resource, body);
    if (!r?.ok) throw new Error(r?.err || '壁纸服务不可用');
    if (r.base) base = r.base;
    return r.data;
  };
  function report(e) { error = e.message || String(e); console.error('[wallpaper]', error); syncStatus(); }
  function persist() {
    clearTimeout(persistTimer);
    const snapshot = { ...settings };
    snapshot.rotationGroups = [{ id: 'vigil-library', name: 'Vigil 壁纸库', interval: settings.rotationInterval || 30,
      order: 'sequence', wallpaperIds: inventory.wallpapers.filter(w => w.type !== 'application').map(w => w.id) }];
    serial = serial.catch(() => {}).then(() => api('PUT', '/settings', snapshot)).catch(report);
    return serial;
  }
  function change(key, value) {
    settings[key] = value;
    applyStyles(); controls();
    clearTimeout(persistTimer); persistTimer = setTimeout(persist, 180);
    if (key === 'rotationEnabled' || key === 'rotationInterval') configureRotation();
  }
  function applyStyles() {
    const style = document.documentElement.style;
    style.setProperty('--vwp-rgb', '18,20,24');
    style.setProperty('--vwp-edge', '255,255,255');
    style.setProperty('--vwp-alpha', settings.glassAlpha / 100);
    style.setProperty('--vwp-blur', settings.blur + 'px');
    if (!media) return;
    media.style.opacity = 1 - settings.wallpaperOpacity / 100;
    const effects = [];
    if (Number(settings.wallpaperBlur) > 0) effects.push(`blur(${settings.wallpaperBlur}px)`);
    if (Number(settings.backgroundBrightness) !== 100) effects.push(`brightness(${settings.backgroundBrightness}%)`);
    if (Number(settings.backgroundContrast) !== 100) effects.push(`contrast(${settings.backgroundContrast}%)`);
    if (Number(settings.backgroundSaturate) !== 100) effects.push(`saturate(${settings.backgroundSaturate}%)`);
    media.style.filter = effects.length ? effects.join(' ') : 'none';
    media.style.transform = settings.flip ? 'scaleX(-1)' : '';
    media.querySelectorAll('img,video').forEach(e => e.style.objectFit = settings.objectFit);
    scrim.style.background = `rgba(0,0,0,${settings.scrim})`;
  }
  function activateStage() {
    stage.hidden = false;
    document.body.classList.add('vwp-active');
    applyStyles();
  }
  function deactivateStage() {
    stage.hidden = true;
    document.body.classList.remove('vwp-active');
  }
  function shouldPause() { return paused || settings.pauseOnHidden && document.hidden || settings.pauseOnBlur && !document.hasFocus(); }
  function controls(force) {
    const volume = settings.videoAudioEnabled ? settings.videoVolume : 0;
    if (video) {
      video.muted = volume === 0; video.volume = volume; video.playbackRate = settings.playbackRate;
      if (shouldPause()) video.pause(); else video.play().catch(() => {});
    }
    if (frame && frameReady) {
      const control = { vigilWallpaper: true, paused: shouldPause(), volume,
        fit: settings.objectFit === 'fill' ? 'stretch' : settings.objectFit };
      const key = JSON.stringify(control);
      if (force || key !== lastControl) { frame.contentWindow?.postMessage(control, new URL(base).origin); lastControl = key; }
    }
  }
  function release() {
    clearTimeout(fallbackTimer); frame?.setAttribute('src', 'about:blank'); frame = null;
    if (video) { video.pause(); video.removeAttribute('src'); video.load(); video = null; }
    media?.replaceChildren(); currentKey = ''; lastControl = ''; firstFrame = false; frameReady = false;
  }
  function showPreview(w) {
    if (!w.frameUrl && !w.preview) return;
    const img = document.createElement('img'); img.alt = '';
    if (w.frameUrl && w.preview) img.onerror = () => { img.onerror = null; img.src = w.preview; };
    img.src = w.frameUrl || w.preview;
    media.append(img);
  }
  function fallback(w, reason, run) {
    if (run !== generation || firstFrame) return;
    frame?.setAttribute('src', 'about:blank'); frame?.remove(); frame = null;
    if (w.sceneVideo) { createVideo(w.sceneVideo); status = reason + ' · 使用内嵌视频'; }
    else status = reason + ' · 保留预览图（壁纸仍已启用）';
    syncStatus();
  }
  function createVideo(url) {
    video = document.createElement('video'); video.loop = true; video.autoplay = true; video.playsInline = true;
    video.muted = !settings.videoAudioEnabled; video.src = url; media.append(video);
    video.addEventListener('canplay', () => { status = '视频播放就绪'; controls(); syncStatus(); }, { once: true });
    video.addEventListener('error', () => { status = '视频无法解码，保留预览图'; video?.remove(); syncStatus(); }, { once: true });
    controls();
  }
  function mount(force) {
    selected = inventory.wallpapers.find(w => w.id === settings.id);
    if (!settings.id) { generation++; release(); stage.hidden = true; document.body.classList.remove('vwp-active'); status = '壁纸已关闭'; syncStatus(); return; }
    if (!selected) { status = '上次选择的壁纸暂未找到，配置已保留；请刷新壁纸库'; syncStatus(); return; }
    const key = selected.id + ':' + settings.sceneLiveFps;
    if (!force && currentKey === key) { controls(); return; }
    release(); currentKey = key; const run = ++generation; const w = selected;
    // Keep the normal local layout visible while an image is decoding. The
    // old order exposed a raw, unstyled image during cold start.
    deactivateStage();
    if (w.type === 'image') {
      const img = document.createElement('img'); img.alt = ''; img.style.opacity = '0';
      img.addEventListener('load', () => {
        if (run !== generation) return;
        img.style.opacity = '1';
        activateStage();
      }, { once: true });
      img.addEventListener('error', () => {
        if (run !== generation) return;
        media.replaceChildren();
        deactivateStage();
      }, { once: true });
      img.src = w.media; media.append(img);
      if (img.complete && img.naturalWidth) queueMicrotask(() => img.dispatchEvent(new Event('load')));
      status = '图片壁纸已启用';
    } else {
      showPreview(w);
      activateStage();
      if (w.type === 'video') { status = '正在加载视频'; createVideo(w.media); }
    }
    if ((w.type === 'scene' && w.sceneLive) || (w.type === 'web' && w.webLive)) {
      frame = document.createElement('iframe'); frame.title = 'WebWallGL 壁纸渲染器';
      // This frame contains only our trusted renderer. Workshop HTML is nested in its strict sandbox.
      frame.sandbox = 'allow-scripts allow-same-origin'; frame.style.opacity = '0';
      const params = new URLSearchParams({ type: w.type, src: w.type === 'web' ? w.webLiveSrc : w.sceneLiveSrc,
        mediaBase: base + '/wallpaper-engine/scene-files', fit: settings.objectFit,
        sceneFps: String(settings.sceneLiveFps), renderDpr: '0', resources: 'native',
        muted: String(!settings.videoAudioEnabled) });
      if (w.type === 'web') params.set('webSandbox', 'strict');
      if (inventory.weAssetsAvailable) params.set('localAssets', '1');
      frame.src = base + '/wallpaper-engine/scene-live/index.html?' + params;
      media.append(frame);
      const mountedFrame = frame;
      frame.addEventListener('load', () => {
        if (frame !== mountedFrame || run !== generation) return;
        frameReady = true; controls(true);
      });
      status = 'WebWallGL 正在准备首帧';
      // Large scene packages need time to download and compile. Never clear the persisted choice on failure.
      fallbackTimer = setTimeout(() => fallback(w, '实时渲染尚未出帧', run),
        Math.min(240000, 45000 + (w.scenePkgBytes || 0) / 1048576 * 1000));
    } else { status = '该项目没有可实时加载的入口，显示预览图'; }
    applyStyles(); syncStatus();
  }
  function syncStatus() {
    if (!view?.isConnected) return;
    const s = view.querySelector('[data-status]'); if (s) s.textContent = status;
    const e = view.querySelector('[data-error]'); if (e) e.textContent = error;
    const checkbox = view.querySelector('#vwp-enabled'); if (checkbox) checkbox.checked = !!settings.id;
  }
  function select(id) { error = ''; settings.id = id; if (id) lastSelectedId = id; paused = false; mount(true); persist(); render(); }
  async function refresh() {
    const raw = await api('GET', '/inventory');
    const previousSelection = (raw.wallpapers || []).find(w => w.id === settings.id);
    inventory = { ...raw, wallpapers: (raw.wallpapers || []).filter(w => w.type !== 'video') };
    if (filter === 'video') filter = 'all';
    if (previousSelection?.type === 'video') {
      settings.id = ''; lastSelectedId = ''; paused = false;
      generation++; release(); stage.hidden = true; document.body.classList.remove('vwp-active');
      status = '独立视频壁纸已停用'; await persist();
    } else mount();
    configureRotation();
  }
  function configureRotation() {
    clearInterval(rotationTimer);
    if (!settings.rotationEnabled) return;
    rotationTimer = setInterval(() => {
      if (shouldPause() || !settings.id) return;
      const list = inventory.wallpapers.filter(w => w.type !== 'application' && (w.playable || w.sceneLive));
      if (list.length > 1) select(list[(list.findIndex(w => w.id === settings.id) + 1) % list.length].id);
    }, Math.max(10, settings.rotationInterval || 30) * 1000);
  }
  const row = (key, label, min, max, step = 1, suffix = '', hint = '') =>
    `<div class="vwp-row"><label for="vwp-${key}">${label}${hint ? '<small>' + hint + '</small>' : ''}</label><input id="vwp-${key}" data-range="${key}" type="range" min="${min}" max="${max}" step="${step}" value="${settings[key]}"><output>${Math.round(settings[key] * (suffix === '%' && max === 1 ? 100 : 1))}${suffix}</output></div>`;
  const check = (key, label, hint = '') => `<div class="vwp-row"><label for="vwp-${key}">${label}<small>${hint}</small></label><input id="vwp-${key}" data-check="${key}" type="checkbox" ${settings[key] ? 'checked' : ''}></div>`;
  function render() {
    // The router reuses the view node. Background rotation must never replace
    // the contents of another page after navigation.
    if (!view?.isConnected || document.body.dataset.vigilPage !== 'wallpaper') return;
    const w = inventory.wallpapers.find(x => x.id === settings.id);
    const body = tab === 'library' ? `<div class="vwp-toolbar"><input type="search" id="vwp-search" aria-label="搜索壁纸" placeholder="搜索你的壁纸…" value="${esc(search)}"><select id="vwp-filter" aria-label="壁纸类型">${[['all', '全部类型'], ['scene', '实时场景'], ['web', '网页'], ['image', '图片']].map(([v, n]) => `<option value="${v}" ${filter === v ? 'selected' : ''}>${n}</option>`).join('')}</select><button class="vwp-button" id="vwp-refresh">刷新</button><button class="vwp-button primary" id="vwp-import">导入图片</button></div><div class="vwp-grid" id="vwp-grid"></div>`
      : tab === 'appearance' ? row('glassAlpha', '面板不透明度', 70, 95, 1, '%', '壁纸切换时保持文字清晰，壁纸仍会从面板后透出') + row('blur', '玻璃雾化', 0, 16, 1, 'px') + row('scrim', '暗色遮罩', 0, 1, .01, '%') + row('wallpaperOpacity', '壁纸透明度', 0, 90, 1, '%') + row('wallpaperBlur', '壁纸模糊', 0, 60, 1, 'px') + row('backgroundBrightness', '亮度', 40, 160, 1, '%') + row('backgroundContrast', '对比度', 40, 200, 1, '%') + row('backgroundSaturate', '饱和度', 0, 200, 1, '%')
      : tab === 'playback' ? check('videoAudioEnabled', '启用壁纸声音') + row('videoVolume', '音量', 0, 1, .01, '%') + check('flip', '水平翻转') + check('pauseOnHidden', '最小化时暂停', '暂停播放，不关闭壁纸') + check('pauseOnBlur', '失去焦点时暂停') + check('rotationEnabled', '自动轮播') + row('rotationInterval', '轮播间隔', 10, 300, 10, 's') + `<div class="vwp-row"><label for="vwp-fit">填充方式</label><select id="vwp-fit">${[['cover','填充（裁剪）'],['contain','适应（完整显示）'],['fill','拉伸']].map(([v,n])=>`<option value="${v}" ${settings.objectFit === v ? 'selected' : ''}>${n}</option>`).join('')}</select></div><div class="vwp-row"><label for="vwp-fps">场景渲染帧率</label><select id="vwp-fps">${[15,30,60].map(v=>`<option ${settings.sceneLiveFps === v ? 'selected' : ''}>${v}</option>`).join('')}</select></div>`
      : tab === 'system' ? `<h3>本机壁纸库</h3><p class="vwp-note">自动发现 Steam 的 Wallpaper Engine 工程。也可选择包含壁纸工程子目录的文件夹；每个工程应含 project.json 和 scene.pkg / index.html / 图片文件。独立视频壁纸已隐藏。</p><p class="vwp-note">Steam：${esc(inventory.installDir || '未发现安装，可使用自定义目录')}<br>自定义目录：${esc(inventory.uploadDir || '')}</p><button class="vwp-button" id="vwp-dir">选择工程目录</button><p class="vwp-note">配置存储在客户端用户数据目录，与后端连接地址和端口无关。切换页面、断开后端、刷新窗口均保留选择。</p>`
      : `<h3>Wallpaper Engine · Vigil</h3><p class="vwp-note">已接入 dsh-wallpaper-engine 1.3.1 的本机宿主与 WebWallGL 2.1.0 场景渲染器。独立视频壁纸已隐藏；网页通过严格沙箱与 WE API 加载，场景实时 WebGL 渲染。</p><p class="vwp-note">Vigil 提供独立的设置界面。上游的吉祥物、字体集、系统音频与 Now Playing 等扩展尚未接入。Application 类型不执行。</p><button class="vwp-button" id="vwp-upstream">查看上游项目</button>`;
    view.innerHTML = `<div class="vwp-shell"><header class="vwp-header"><div><h2>壁纸与外观</h2><div class="vwp-caption">选择壁纸，让工作空间动起来 · 本机独立运行</div></div><span class="vwp-badge">WebWallGL 2.1.0</span></header><div class="vwp-sheet vwp-player">${w?.preview ? `<img src="${esc(w.preview)}" alt="">` : ''}<div class="vwp-now"><strong>${esc(w?.title || '选择一张喜欢的壁纸')}</strong><div class="vwp-state" data-status aria-live="polite">${esc(status)}</div></div><label><input id="vwp-enabled" type="checkbox" ${settings.id ? 'checked' : ''}> 启用</label><button class="vwp-button" id="vwp-pause" ${!settings.id ? 'disabled' : ''}>${paused ? '继续' : '暂停'}</button><button class="vwp-button" id="vwp-close">关闭</button></div><nav class="vwp-tabs" role="tablist" aria-label="壁纸设置">${[['library','壁纸库'],['appearance','外观'],['playback','播放'],['system','系统'],['about','关于']].map(([id,name])=>`<button role="tab" id="vwp-tab-${id}" aria-controls="vwp-content" aria-selected="${id === tab}" data-tab="${id}">${name}</button>`).join('')}</nav><div class="vwp-sheet vwp-content ${tab === 'library' ? 'vwp-library' : ''}" id="vwp-content" role="tabpanel" aria-labelledby="vwp-tab-${tab}" tabindex="0">${body}</div><div class="vwp-error" data-error role="alert">${esc(error)}</div></div>`;
    view.querySelectorAll('[data-tab]').forEach(e => e.onclick = () => { tab = e.dataset.tab; render(); });
    view.querySelectorAll('[data-range]').forEach(e => e.oninput = () => { change(e.dataset.range, Number(e.value));
      const suffix = ['blur','wallpaperBlur'].includes(e.dataset.range) ? 'px' : e.dataset.range === 'playbackRate' ? '×' : e.dataset.range === 'rotationInterval' ? 's' : '%';
      e.nextElementSibling.textContent = Math.round(Number(e.value) * (e.max === '1' ? 100 : 1) * 10) / 10 + suffix; });
    view.querySelectorAll('[data-check]').forEach(e => e.onchange = () => change(e.dataset.check, e.checked));
    const bind = (id, fn) => { const e = view.querySelector('#' + id); if (e) e.onclick = fn; };
    bind('vwp-close', () => select(''));
    bind('vwp-pause', () => { paused = !paused; controls(); render(); });
    bind('vwp-refresh', async () => { try { await refresh(); render(); } catch(e) { report(e); } });
    bind('vwp-import', async () => { try { const r = await window.vigil.wallpaper.importFile(); if (!r.ok) throw new Error(r.err); if (!r.canceled) { await refresh(); render(); } } catch(e) { report(e); } });
    bind('vwp-dir', async () => { try { const r = await window.vigil.wallpaper.directory(); if (!r.ok) throw new Error(r.err); if (!r.canceled) { await refresh(); render(); } } catch(e) { report(e); } });
    bind('vwp-upstream', () => window.vigil.openExternal('https://github.com/elysia395/dsh-wallpaper-engine'));
    view.querySelector('#vwp-enabled').onchange = e => {
      const id = e.target.checked ? (w?.id || lastSelectedId) : '';
      if (e.target.checked && !id) { report(new Error('请先在壁纸库中选择一张壁纸')); e.target.checked = false; return; }
      select(id);
    };
    const fit = view.querySelector('#vwp-fit'); if (fit) fit.onchange = () => change('objectFit', fit.value);
    const fps = view.querySelector('#vwp-fps'); if (fps) fps.onchange = () => { change('sceneLiveFps', Number(fps.value)); mount(true); };
    const q = view.querySelector('#vwp-search'); if (q) q.oninput = () => { search = q.value; renderGrid(); };
    const f = view.querySelector('#vwp-filter'); if (f) f.onchange = () => { filter = f.value; renderGrid(); };
    renderGrid();
  }
  function renderGrid() {
    const grid = view?.querySelector('#vwp-grid'); if (!grid) return;
    const list = inventory.wallpapers.filter(w=>w.type !== 'application' && (filter === 'all' || w.type === filter) && w.title.toLowerCase().includes(search.toLowerCase()));
    grid.innerHTML = list.length ? list.map(w=>`<button class="vwp-tile" data-id="${esc(w.id)}" title="${esc(w.title)}" aria-pressed="${w.id === settings.id}"><div class="vwp-thumb">${w.preview || w.type === 'image' ? `<img src="${esc(w.preview || w.media)}" loading="lazy" alt="">` : '<span>' + esc(typeName(w.type)) + '</span>'}</div><strong>${esc(w.title)}</strong><small>${esc(typeName(w.type))}</small></button>`).join('') : '<div class="vwp-empty">没有找到壁纸。<br>导入图片，或在「系统」中选择你的工程目录。</div>';
    grid.querySelectorAll('[data-id]').forEach(e=>e.onclick=()=>select(e.dataset.id));
  }
  async function init() {
    if (started || !window.vigil?.wallpaper) return started;
    stage = document.createElement('div'); stage.className = 'vwp-stage'; stage.hidden = true;
    media = document.createElement('div'); media.className = 'vwp-media';
    scrim = document.createElement('div'); scrim.className = 'vwp-scrim'; stage.append(media, scrim); document.body.prepend(stage);
    window.VigilPlugin.registerPage({ id:'wallpaper', name:'壁纸', icon:'image', async render(ctx) {
      view = ctx.view; if (ctx.topbar) ctx.topbar.innerHTML = '<h1>Wallpaper Engine</h1>';
      view.innerHTML = '<div class="vwp-empty">正在读取本机壁纸库…</div>';
      await started; render();
    } });
    new MutationObserver(applyStyles).observe(document.documentElement, { attributes:true, attributeFilter:['data-theme'] });
    document.addEventListener('visibilitychange', () => controls());
    window.addEventListener('blur', () => controls()); window.addEventListener('focus', () => controls());
    window.addEventListener('message', e => {
      if (!frame || e.source !== frame.contentWindow || e.origin !== new URL(base).origin || e.data?.vigilWallpaper !== true) return;
      if (e.data.fallback || e.data.state?.webError) { fallback(selected, '实时引擎报告加载失败', generation); return; }
      if (e.data.state?.type === selected?.type && (e.data.state?.running || selected?.type === 'web' && e.data.webReady)) {
        if (!firstFrame) { firstFrame = true; clearTimeout(fallbackTimer); frame.style.opacity = '1'; status = selected.type === 'scene' ? 'WebWallGL 实时渲染中' : '网页壁纸运行中'; syncStatus(); controls(true); }
      }
    });
    const pointer = e => { if (frame && frameReady) frame.contentWindow?.postMessage({ vigilWallpaper:true,
      pointer:{ u:e.clientX/innerWidth, v:e.clientY/innerHeight, buttons:e.buttons, mods:0 } }, new URL(base).origin); };
    for (const event of ['pointermove','pointerdown','pointerup']) window.addEventListener(event, pointer, { passive:true });
    started = (async () => { try {
      const saved = await api('GET', '/settings'); settings = { ...defaults, ...(saved.settings || {}) };
      const oldAlpha = Number(settings.glassAlpha), oldBlur = Number(settings.blur);
      settings.glassAlpha = Math.max(70, Math.min(95, Number.isFinite(oldAlpha) ? oldAlpha : defaults.glassAlpha));
      settings.blur = Math.max(0, Math.min(16, Number.isFinite(oldBlur) ? oldBlur : defaults.blur));
      const migratedAppearance = settings.glassAlpha !== oldAlpha || settings.blur !== oldBlur || settings.settingsVersion !== defaults.settingsVersion;
      settings.settingsVersion = defaults.settingsVersion;
      settings.rotationInterval = settings.rotationGroups?.find(g=>g.id === 'vigil-library')?.interval || 30;
      lastSelectedId = settings.id;
      await refresh(); applyStyles();
      if (migratedAppearance) await persist();
    } catch(e) { report(e); } })();
    return started;
  }
  window.VigilWallpaper = { init };
})();
