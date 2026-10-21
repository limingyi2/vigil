// wallpaper 插件 v3.0 —— 接入 Wallpaper Engine (Steam)
// 扫描 WE 创意工坊，把视频/网页/场景壁纸渲染到客户端背景
(function () {
  'use strict';

  // ========== 工具 ==========
  // 插件运行在 Electron 的 file:// 渲染层，CSP 禁止直接 fetch。
  // 所有大脑请求必须经过主进程代理的 VigilAPI，token 也只留在主进程。
  function apiGet(path) {
    var api = window.VigilAPI || window.API;
    if (!api || typeof api.get !== 'function') {
      return Promise.resolve({ ok: false, err: '客户端 API 尚未就绪' });
    }
    return api.get(path).then(function (r) {
      if (!r || !r.ok) {
        return { ok: false, err: (r && (r.err || (r.json && r.json.err))) || '请求失败' };
      }
      return r.json || { ok: false, err: '接口返回为空' };
    });
  }
  function apiPost(path, body) {
    var api = window.VigilAPI || window.API;
    if (!api || typeof api.post !== 'function') {
      return Promise.resolve({ ok: false, err: '客户端 API 尚未就绪' });
    }
    return api.post(path, body || {}).then(function (r) {
      if (!r || !r.ok) {
        return { ok: false, err: (r && (r.err || (r.json && r.json.err))) || '请求失败' };
      }
      return r.json || { ok: false, err: '接口返回为空' };
    });
  }
  function isDark() {
    return document.documentElement.getAttribute('data-theme') === 'dark'
      || document.body.classList.contains('dark')
      || (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  }

  // ========== 背景层 ==========
  var bgLayer = document.getElementById('wallpaper-bg-layer');
  if (!bgLayer) {
    bgLayer = document.createElement('div');
    bgLayer.id = 'wallpaper-bg-layer';
    bgLayer.className = 'wallpaper-bg';
    document.body.appendChild(bgLayer);
  }
  var scrimLayer = document.getElementById('wallpaper-scrim-layer');
  if (!scrimLayer) {
    scrimLayer = document.createElement('div');
    scrimLayer.id = 'wallpaper-scrim-layer';
    scrimLayer.className = 'wallpaper-scrim';
    document.body.appendChild(scrimLayer);
  }

  // ========== 状态 ==========
  var currentConfig = null;
  var library = [];       // WE 壁纸库
  var wePath = null;
  var libraryError = '';
  var ready = Promise.resolve();
  var sceneInstance = null;
  var sceneResizeObserver = null;
  var visibilityHandler = null;
  var applyGeneration = 0;
  var enginePromise = null;
  var engineScript = null;
  var engineGlobalListeners = [];
  var themeChangeHandler = null;
  var disposed = false;
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }
  function setResource(element, resource) {
    if (!window.vigil || !window.vigil.plugins || !window.vigil.plugins.resourceUrl) {
      console.error('[wallpaper] 客户端缺少媒体桥，请升级客户端');
      return;
    }
    window.vigil.plugins.resourceUrl(resource).then(function (r) {
      if (r && r.ok && element.isConnected) element.src = r.url;
      else if (!r || !r.ok) console.error('[wallpaper] 媒体地址失败:', r && r.err);
    });
  }

  // 媒体代理 URL
  function mediaUrl(id, file) {
    return '/api/plugin/wallpaper/we/media?id=' + encodeURIComponent(id)
      + '&file=' + encodeURIComponent(file);
  }
  function previewUrl(id) {
    return '/api/plugin/wallpaper/we/preview?id=' + encodeURIComponent(id);
  }

  function destroyScene() {
    if (sceneResizeObserver) { sceneResizeObserver.disconnect(); sceneResizeObserver = null; }
    if (sceneInstance) {
      try { sceneInstance.destroy({ releasePkgCache: true }); } catch (e) { console.warn('[wallpaper scene] destroy failed', e); }
      sceneInstance = null;
    }
  }
  function loadSceneEngine() {
    if (window.WebWallGL && typeof window.WebWallGL.mount === 'function') return Promise.resolve(window.WebWallGL);
    if (enginePromise) return enginePromise;
    enginePromise = new Promise(function (resolve, reject) {
      var script = engineScript = document.createElement('script');
      var originalAdd = window.addEventListener;
      var captured = [];
      window.addEventListener = function (type, listener, options) {
        if (type === 'contextmenu') captured.push({ type: type, listener: listener, options: options });
        return originalAdd.call(this, type, listener, options);
      };
      function restoreAdd() {
        window.addEventListener = originalAdd;
        engineGlobalListeners = captured;
      }
      script.src = new URL('../node_modules/webwallgl/webwallgl.global.min.js', location.href).href;
      script.async = true;
      script.onload = function () {
        restoreAdd();
        if (window.WebWallGL && typeof window.WebWallGL.mount === 'function') resolve(window.WebWallGL);
        else reject(new Error('WebWallGL 3.0.0 加载完成但 mount() 不可用'));
      };
      script.onerror = function () { restoreAdd(); reject(new Error('无法从本地安装包加载 WebWallGL 3.0.0'));
        enginePromise = null; };
      document.head.appendChild(script);
    });
    return enginePromise;
  }
  async function mountScene(container, id, generation) {
    var status = document.createElement('div');
    status.className = 'wp-scene-status';
    status.textContent = '正在初始化 WebWallGL Scene…';
    container.appendChild(status);
    try {
      var engine = await loadSceneEngine();
      if (generation !== applyGeneration || !container.isConnected) return;
      // The loopback URL is a short lived media ticket. Each subsequent relative
      // path is still resolved and containment-checked by the Brain media route.
      var ticket = await window.vigil.plugins.resourceUrl(mediaUrl(id, 'project.json'));
      if (!ticket || !ticket.ok) throw new Error((ticket && ticket.err) || 'Brain 媒体票据申请失败');
      var base = ticket.url.replace(/project\.json$/, '');
      if (base === ticket.url) throw new Error('媒体桥返回了无效的 Scene 资源基址');
      var instance = await engine.mount(container, {
        source: engine.httpSource(base), fit: 'cover', fps: 30, volume: 0,
        autoplay: true, scripts: false, mountTimeoutMs: 45000,
        onDiagnostic: function (message, level) { console.info('[wallpaper scene][' + level + ']', String(message).slice(0, 240)); },
        onReady: function () { container.dataset.liveReady = 'true'; console.info('[wallpaper scene] live first frame ready; id=' + id); },
        onError: function (error) { console.error('[wallpaper scene] render error; id=' + id, error); },
      });
      if (generation !== applyGeneration || !container.isConnected) {
        instance.destroy({ releasePkgCache: true });
        return;
      }
      sceneInstance = instance;
      container.removeChild(status);
      instance.canvas.classList.add('wp-scene-canvas');
      sceneResizeObserver = new ResizeObserver(function () {
        // WebWallGL owns its ResizeObserver; trigger a layout read so the browser
        // commits the new DPR / viewport dimensions before the next animation frame.
        void container.getBoundingClientRect();
      });
      sceneResizeObserver.observe(container);
      if (instance.info) container.dataset.sceneInfo = JSON.stringify(instance.info);
      console.info('[wallpaper scene] live scene mounted; id=' + id, instance.info || {});
    } catch (error) {
      if (generation !== applyGeneration || !container.isConnected) return;
      status.classList.add('error');
      status.textContent = 'Scene 实时渲染失败：' + String(error && error.message || error) + '（下方仅为预览图）';
      var fallback = document.createElement('img');
      fallback.className = 'wp-scene-fallback';
      fallback.alt = 'Scene 预览图（不是实时渲染）';
      container.appendChild(fallback);
      setResource(fallback, previewUrl(id));
      console.error('[wallpaper scene] live mount failed; preview fallback only; id=' + id, error);
    }
  }
  function disposeWallpaper() {
    disposed = true;
    applyGeneration++;
    destroyScene();
    if (visibilityHandler) document.removeEventListener('visibilitychange', visibilityHandler);
    visibilityHandler = null;
    bgLayer.replaceChildren();
    scrimLayer.replaceChildren();
    bgLayer.style.display = 'none';
    scrimLayer.style.display = 'none';
    document.body.classList.remove('wallpaper-active');
    document.documentElement.style.removeProperty('--wp-glass-blur');
    bgLayer.remove();
    scrimLayer.remove();
    if (themeChangeHandler && window.matchMedia) {
      window.matchMedia('(prefers-color-scheme: dark)').removeEventListener('change', themeChangeHandler);
      themeChangeHandler = null;
    }
    engineGlobalListeners.forEach(function (entry) {
      try { window.removeEventListener(entry.type, entry.listener, entry.options); } catch (_) {}
    });
    engineGlobalListeners = [];
    if (engineScript) { engineScript.remove(); engineScript = null; }
    enginePromise = null;
    try { window.WebWallGL = null; } catch (_) {}
    if (window.vigil && window.vigil.plugins && window.vigil.plugins.releaseResources) {
      window.vigil.plugins.releaseResources().catch(function (error) {
        console.warn('[wallpaper] media ticket cleanup failed', error);
      });
    }
  }

  // ========== 应用壁纸到背景层 ==========
  function applyWallpaper(cfg) {
    var generation = ++applyGeneration;
    destroyScene();
    // 清空背景层
    bgLayer.replaceChildren();

    if (!cfg || !cfg.enabled || !cfg.wallpaper_id) {
      bgLayer.style.display = 'none';
      scrimLayer.style.display = 'none';
      document.body.classList.remove('wallpaper-active');
      return;
    }

    document.body.classList.add('wallpaper-active');
    bgLayer.style.display = 'block';
    scrimLayer.style.display = 'block';
    bgLayer.style.opacity = cfg.opacity != null ? cfg.opacity : 0.7;

    var id = cfg.wallpaper_id;
    var type = cfg.wallpaper_type;
    var file = cfg.wallpaper_file;

    if (type === 'video' && file) {
      // 视频壁纸：<video> 循环播放
      var v = document.createElement('video');
      v.autoplay = true;
      v.loop = true;
      v.muted = cfg.muted !== false;
      v.playsInline = true;
      v.style.cssText = 'width:100%;height:100%;object-fit:cover;object-position:center;';
      bgLayer.appendChild(v);
      setResource(v, mediaUrl(id, file));
    } else if (type === 'web' && file) {
      // 网页壁纸：<iframe sandbox> 隔离
      var ifr = document.createElement('iframe');
      ifr.sandbox = 'allow-scripts';
      ifr.style.cssText = 'width:100%;height:100%;border:none;display:block;';
      bgLayer.appendChild(ifr);
      setResource(ifr, mediaUrl(id, file));
    } else if (type === 'scene' && id) {
      var scene = document.createElement('div');
      scene.className = 'wp-scene-stage';
      bgLayer.appendChild(scene);
      mountScene(scene, id, generation);
    } else {
      // Unsupported types are explicitly preview-only and never claim live playback.
      var img = document.createElement('img');
      img.style.cssText = 'width:100%;height:100%;object-fit:cover;object-position:center;';
      bgLayer.appendChild(img);
      setResource(img, previewUrl(id));
    }

    if (!visibilityHandler) {
      visibilityHandler = function () {
        if (!sceneInstance) return;
        if (document.hidden) {
          sceneInstance.pause();
          var activeStage = bgLayer.querySelector('.wp-scene-stage');
          if (activeStage) activeStage.dataset.paused = 'true';
        } else if (currentConfig && currentConfig.enabled) {
          sceneInstance.resume();
          var activeStage = bgLayer.querySelector('.wp-scene-stage');
          if (activeStage) activeStage.dataset.paused = 'false';
        }
      };
      document.addEventListener('visibilitychange', visibilityHandler);
    }

    // 遮罩
    var scrimColor = isDark() ? 'rgba(0,0,0,' : 'rgba(255,255,255,';
    scrimLayer.style.background = scrimColor + (cfg.scrim != null ? cfg.scrim : 0.4) + ')';

    // 毛玻璃（通过 CSS 变量传给面板）
    document.documentElement.style.setProperty('--wp-glass-blur', (cfg.blur || 0) + 'px');
  }

  // ========== 加载配置 & 壁纸库 ==========
  function loadConfig() {
    return apiGet('/api/plugin/wallpaper/config').then(function (r) {
      if (disposed) return;
      if (r && r.ok) {
        currentConfig = r.config;
        wePath = r.config.we_path;
        applyWallpaper(currentConfig);
      } else {
        console.error('[wallpaper] 配置读取失败:', (r && r.err) || '未知错误');
      }
    });
  }
  function loadLibrary(force) {
    return apiGet('/api/plugin/wallpaper/we/library' + (force ? '?refresh=1' : '')).then(function (r) {
      if (r && r.ok) {
        libraryError = '';
        library = r.wallpapers || [];
        wePath = r.we_path;
      } else {
        libraryError = (r && r.err) || '读取壁纸库失败';
        console.error('[wallpaper] 壁纸库读取失败:', (r && r.err) || '未知错误');
      }
      return library;
    });
  }
  function saveConfig(partial) {
    if (!currentConfig) currentConfig = {};
    Object.assign(currentConfig, partial);
    apiPost('/api/plugin/wallpaper/config', partial).then(function (r) {
      if (r && r.ok) { currentConfig = r.config; applyWallpaper(currentConfig); }
      else console.error('[wallpaper] 配置保存失败:', (r && r.err) || '未知错误');
    });
  }

  // ========== 设置页渲染 ==========
  function renderSettings(ctx) {
    ctx.view.innerHTML = '<div class="wp-empty">正在读取壁纸配置和壁纸库…</div>';
    return ready.then(function () {
      if (ctx.view.isConnected) drawSettings(ctx);
    });
  }
  function drawSettings(ctx) {
    var cfg = currentConfig || {};
    var html = '<div class="wp-settings">';

    // 标题 + WE 路径
    html += '<div class="wp-header"><h2>壁纸 (Wallpaper Engine)</h2>'
      + '<span class="wp-path">' + esc(wePath ? 'WE: ' + wePath : '未检测到 Wallpaper Engine') + '</span></div>';

    // 启用开关
    html += '<div class="wp-row"><label>启用壁纸</label>'
      + '<input type="checkbox" id="wp-enabled" ' + (cfg.enabled ? 'checked' : '') + '></div>';

    // 壁纸库网格
    html += '<div class="wp-section"><div class="wp-section-title">壁纸库 (' + library.length + ')</div>'
      + '<div class="wp-library" id="wp-library">';
    if (library.length === 0) {
      html += '<div class="wp-empty">' + esc(libraryError || (wePath ? '壁纸库为空，订阅壁纸后重新扫描。' : '未找到 Wallpaper Engine 创意工坊目录。')) + '</div>';
    }
    html += '</div></div>';

    // 当前选中
    if (cfg.wallpaper_id) {
      var cur = library.find(function (w) { return w.id === cfg.wallpaper_id; });
      html += '<div class="wp-current">当前: ' + esc(cur ? cur.title : cfg.wallpaper_id)
        + ' [' + esc(cfg.wallpaper_type || '?') + ']</div>';
    }

    // 调节滑块
    html += '<div class="wp-section"><div class="wp-section-title">效果调节</div>';
    html += '<div class="wp-row"><label>壁纸不透明度 <span id="wp-opacity-val">' + Math.round((cfg.opacity != null ? cfg.opacity : 0.7) * 100) + '%</span></label>'
      + '<input type="range" id="wp-opacity" min="0" max="100" value="' + Math.round((cfg.opacity != null ? cfg.opacity : 0.7) * 100) + '"></div>';
    html += '<div class="wp-row"><label>遮罩不透明度 <span id="wp-scrim-val">' + Math.round((cfg.scrim != null ? cfg.scrim : 0.4) * 100) + '%</span></label>'
      + '<input type="range" id="wp-scrim" min="0" max="95" value="' + Math.round((cfg.scrim != null ? cfg.scrim : 0.4) * 100) + '"></div>';
    html += '<div class="wp-row"><label>毛玻璃模糊 <span id="wp-blur-val">' + (cfg.blur || 0) + 'px</span></label>'
      + '<input type="range" id="wp-blur" min="0" max="40" value="' + (cfg.blur || 0) + '"></div>';
    html += '<div class="wp-row"><label>视频静音</label>'
      + '<input type="checkbox" id="wp-muted" ' + (cfg.muted !== false ? 'checked' : '') + '></div>';
    html += '</div>';

    // 关闭按钮
    html += '<div class="wp-actions"><button id="wp-close">关闭壁纸</button>';
    html += '<button id="wp-refresh">重新扫描库</button>';
    html += '</div>';

    html += '</div>';
    ctx.view.innerHTML = html;

    // 绑定事件
    function bind(id, ev, fn) { var el = document.getElementById(id); if (el) el.addEventListener(ev, fn); }

    bind('wp-enabled', 'change', function (e) { saveConfig({ enabled: e.target.checked }); });
    bind('wp-muted', 'change', function (e) { saveConfig({ muted: e.target.checked }); });
    bind('wp-close', 'click', function () { saveConfig({ enabled: false, wallpaper_id: null }); });
    bind('wp-refresh', 'click', function () { loadLibrary(true).then(function () { renderSettings(ctx); }); });

    // 滑块实时预览
    function bindSlider(id, valId, key, scale) {
      var el = document.getElementById(id), valEl = document.getElementById(valId);
      if (!el) return;
      el.addEventListener('input', function () {
        var v = parseInt(el.value) / (scale || 1);
        valEl.textContent = el.value + (key === 'blur' ? 'px' : '%');
        applyWallpaper(Object.assign({}, currentConfig, (function () { var o = {}; o[key] = v; return o; })()));
      });
      el.addEventListener('change', function () {
        var v = parseInt(el.value) / (scale || 1);
        var o = {}; o[key] = v; saveConfig(o);
      });
    }
    bindSlider('wp-opacity', 'wp-opacity-val', 'opacity', 100);
    bindSlider('wp-scrim', 'wp-scrim-val', 'scrim', 100);
    bindSlider('wp-blur', 'wp-blur-val', 'blur', 1);

    // 渲染壁纸库网格
    var libEl = document.getElementById('wp-library');
    if (libEl && library.length > 0) {
      var grid = '';
      library.forEach(function (w) {
        var active = cfg.wallpaper_id === w.id ? ' active' : '';
        var typeLabel = { video: '视频', web: '网页', scene: '场景' }[w.type] || w.type;
        grid += '<div class="wp-card' + active + '" data-id="' + esc(w.id) + '" data-type="' + esc(w.type) + '" data-file="' + esc(w.file || '') + '">'
          + '<div class="wp-thumb"><img data-resource="' + esc(previewUrl(w.id)) + '" loading="lazy" alt=""></div>'
          + '<div class="wp-info"><div class="wp-title">' + esc(w.title) + '</div>'
          + '<div class="wp-type">' + esc(typeLabel) + '</div></div></div>';
      });
      libEl.innerHTML = grid;
      libEl.querySelectorAll('img[data-resource]').forEach(function (img) {
        setResource(img, img.dataset.resource);
      });

      // 点击选择
      var cards = libEl.querySelectorAll('.wp-card');
      for (var i = 0; i < cards.length; i++) {
        cards[i].addEventListener('click', function (e) {
          var card = e.currentTarget;
          var id = card.getAttribute('data-id');
          var type = card.getAttribute('data-type');
          var file = card.getAttribute('data-file');
          // 更新 active 状态
          for (var j = 0; j < cards.length; j++) cards[j].classList.remove('active');
          card.classList.add('active');
          saveConfig({ enabled: true, wallpaper_id: id, wallpaper_type: type, wallpaper_file: file });
        });
      }
    }
  }

  // ========== 注册页面 ==========
  function registerPage() {
    if (window.VigilPlugin && typeof window.VigilPlugin.registerPage === 'function') {
      // after: 导航栏按钮多，push 到末尾会被滚动区吞掉；插在插件市场后面保证看得见
      window.VigilPlugin.registerPage({ id: 'wallpaper', name: '壁纸', icon: 'i-img', render: renderSettings, after: 'market' });
      window.VigilPlugin.registerCleanup('wallpaper', disposeWallpaper);
    }
  }

  // ========== 启动 ==========
  function init() {
    disposed = false;
    ready = Promise.all([loadConfig(), loadLibrary()]);
    registerPage();
    if (window.matchMedia) {
      themeChangeHandler = function () {
        if (currentConfig) applyWallpaper(currentConfig);
      };
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', themeChangeHandler);
    }
    console.log('[wallpaper v3] Wallpaper Engine 接入已加载');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
