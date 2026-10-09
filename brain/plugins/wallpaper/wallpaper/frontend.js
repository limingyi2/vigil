// wallpaper 插件前端：自定义背景层 + 壁纸设置页
// 综合 DSH 三款壁纸插件的实现思路
(function () {
  'use strict';

  // ========== 工具函数 ==========
  function apiGet(path) {
    return fetch(path).then(function (r) { return r.json(); });
  }
  function apiPost(path, body) {
    return fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }).then(function (r) { return r.json(); });
  }

  // ========== 背景层创建 ==========
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

  // ========== 配置状态 ==========
  var currentConfig = null;
  var presets = {};

  // 判断是否暗色主题
  function isDark() {
    return document.documentElement.getAttribute('data-theme') === 'dark'
      || document.body.classList.contains('dark')
      || (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  }

  // 应用配置到背景层
  function applyConfig(cfg) {
    if (!cfg || !cfg.enabled) {
      bgLayer.style.display = 'none';
      scrimLayer.style.display = 'none';
      document.body.classList.remove('wallpaper-active');
      return;
    }
    document.body.classList.add('wallpaper-active');
    bgLayer.style.display = 'block';
    scrimLayer.style.display = 'block';

    // 壁纸不透明度
    bgLayer.style.opacity = (cfg.opacity / 100).toFixed(2);

    // 填充方式
    bgLayer.style.backgroundSize = cfg.fit === 'contain' ? 'contain' : 'cover';
    bgLayer.style.backgroundPosition = 'center center';
    bgLayer.style.backgroundRepeat = 'no-repeat';

    // 背景内容
    if (cfg.type === 'preset' && presets[cfg.preset]) {
      bgLayer.style.backgroundImage = presets[cfg.preset].css;
    } else if (cfg.type === 'url' && cfg.image_url) {
      bgLayer.style.backgroundImage = 'url("' + cfg.image_url + '")';
    } else if (cfg.type === 'image' && cfg.image_file) {
      // 从后端获取图片（base64）
      apiGet('/api/plugin/wallpaper/image?file=' + encodeURIComponent(cfg.image_file))
        .then(function (r) {
          if (r && r.ok && r.data) {
            bgLayer.style.backgroundImage = 'url("data:' + r.content_type + ';base64,' + r.data + '")';
          }
        });
    }

    // 遮罩层
    var scrimColor = isDark() ? 'rgba(0,0,0,' : 'rgba(255,255,255,';
    scrimLayer.style.background = scrimColor + (cfg.scrim / 100).toFixed(2) + ')';

    // 面板不透明度 + 毛玻璃（通过 CSS 变量控制）
    document.documentElement.style.setProperty('--wp-panel-opacity', (cfg.panel_opacity / 100).toFixed(2));
    document.documentElement.style.setProperty('--wp-glass-blur', cfg.glass_blur + 'px');
  }

  // 加载配置
  function loadConfig() {
    apiGet('/api/plugin/wallpaper/config').then(function (r) {
      if (r && r.ok) {
        currentConfig = r.config;
        presets = r.presets || {};
        applyConfig(currentConfig);
      }
    });
  }

  // 保存配置
  function saveConfig(partial) {
    if (!currentConfig) currentConfig = {};
    Object.assign(currentConfig, partial);
    apiPost('/api/plugin/wallpaper/config', partial).then(function (r) {
      if (r && r.ok) {
        currentConfig = r.config;
        applyConfig(currentConfig);
      }
    });
  }

  // ========== 设置页 ==========
  function renderSettings(ctx) {
    var cfg = currentConfig || {};
    var html = '<div class="wp-settings">';
    html += '<h2>壁纸设置</h2>';

    // 启用开关
    html += '<div class="wp-row"><label>启用壁纸</label>'
      + '<input type="checkbox" id="wp-enabled" ' + (cfg.enabled ? 'checked' : '') + '></div>';

    // 类型选择
    html += '<div class="wp-row"><label>壁纸类型</label>'
      + '<select id="wp-type">'
      + '<option value="preset"' + (cfg.type === 'preset' ? ' selected' : '') + '>渐变预设</option>'
      + '<option value="image"' + (cfg.type === 'image' ? ' selected' : '') + '>本地上传</option>'
      + '<option value="url"' + (cfg.type === 'url' ? ' selected' : '') + '>图片URL</option>'
      + '</select></div>';

    // 预设选择
    html += '<div class="wp-row" id="wp-preset-row"><label>预设</label><div class="wp-presets">';
    for (var key in presets) {
      var p = presets[key];
      html += '<div class="wp-preset-item' + (cfg.preset === key ? ' active' : '') + '"'
        + ' data-key="' + key + '" style="background:' + p.css + '" title="' + p.name + '"></div>';
    }
    html += '</div></div>';

    // 上传
    html += '<div class="wp-row" id="wp-upload-row" style="display:none">'
      + '<label>上传图片</label>'
      + '<input type="file" id="wp-upload" accept="image/*">'
      + (cfg.image_file ? '<span class="wp-hint">已上传图片</span>' : '')
      + '</div>';

    // URL
    html += '<div class="wp-row" id="wp-url-row" style="display:none">'
      + '<label>图片URL</label>'
      + '<input type="text" id="wp-url" value="' + (cfg.image_url || '') + '" placeholder="https://...">'
      + '</div>';

    // 滑块
    html += '<div class="wp-row"><label>壁纸不透明度 <span id="wp-opacity-val">' + (cfg.opacity != null ? cfg.opacity : 60) + '%</span></label>'
      + '<input type="range" id="wp-opacity" min="0" max="100" value="' + (cfg.opacity != null ? cfg.opacity : 60) + '"></div>';

    html += '<div class="wp-row"><label>遮罩不透明度 <span id="wp-scrim-val">' + (cfg.scrim != null ? cfg.scrim : 30) + '%</span></label>'
      + '<input type="range" id="wp-scrim" min="0" max="95" value="' + (cfg.scrim != null ? cfg.scrim : 30) + '"></div>';

    html += '<div class="wp-row"><label>面板不透明度 <span id="wp-panel-val">' + (cfg.panel_opacity != null ? cfg.panel_opacity : 85) + '%</span></label>'
      + '<input type="range" id="wp-panel" min="0" max="100" value="' + (cfg.panel_opacity != null ? cfg.panel_opacity : 85) + '"></div>';

    html += '<div class="wp-row"><label>毛玻璃模糊 <span id="wp-blur-val">' + (cfg.glass_blur != null ? cfg.glass_blur : 12) + 'px</span></label>'
      + '<input type="range" id="wp-blur" min="0" max="40" value="' + (cfg.glass_blur != null ? cfg.glass_blur : 12) + '"></div>';

    // 填充方式
    html += '<div class="wp-row"><label>填充方式</label>'
      + '<select id="wp-fit">'
      + '<option value="cover"' + (cfg.fit === 'cover' ? ' selected' : '') + '>填充（裁剪）</option>'
      + '<option value="contain"' + (cfg.fit === 'contain' ? ' selected' : '') + '>适应（完整）</option>'
      + '</select></div>';

    html += '</div>';

    ctx.view.innerHTML = html;

    // 绑定事件
    function bind(id, event, handler) {
      var el = document.getElementById(id);
      if (el) el.addEventListener(event, handler);
    }

    bind('wp-enabled', 'change', function (e) { saveConfig({ enabled: e.target.checked }); });
    bind('wp-type', 'change', function (e) {
      saveConfig({ type: e.target.value });
      document.getElementById('wp-preset-row').style.display = e.target.value === 'preset' ? '' : 'none';
      document.getElementById('wp-upload-row').style.display = e.target.value === 'image' ? '' : 'none';
      document.getElementById('wp-url-row').style.display = e.target.value === 'url' ? '' : 'none';
    });
    bind('wp-fit', 'change', function (e) { saveConfig({ fit: e.target.value }); });

    // 预设点击
    var presetItems = ctx.view.querySelectorAll('.wp-preset-item');
    for (var i = 0; i < presetItems.length; i++) {
      presetItems[i].addEventListener('click', function (e) {
        var key = e.target.getAttribute('data-key');
        saveConfig({ preset: key, type: 'preset' });
        // 更新 active 状态
        for (var j = 0; j < presetItems.length; j++) {
          presetItems[j].classList.toggle('active', presetItems[j].getAttribute('data-key') === key);
        }
      });
    }

    // 滑块（实时预览，松开保存）
    function bindSlider(id, valId, key) {
      var el = document.getElementById(id);
      var valEl = document.getElementById(valId);
      if (!el) return;
      el.addEventListener('input', function () {
        valEl.textContent = el.value + (key === 'glass_blur' ? 'px' : '%');
        // 实时预览
        var preview = {};
        preview[key] = parseInt(el.value);
        applyConfig(Object.assign({}, currentConfig, preview));
      });
      el.addEventListener('change', function () {
        var save = {};
        save[key] = parseInt(el.value);
        saveConfig(save);
      });
    }
    bindSlider('wp-opacity', 'wp-opacity-val', 'opacity');
    bindSlider('wp-scrim', 'wp-scrim-val', 'scrim');
    bindSlider('wp-panel', 'wp-panel-val', 'panel_opacity');
    bindSlider('wp-blur', 'wp-blur-val', 'glass_blur');

    // URL
    bind('wp-url', 'change', function (e) { saveConfig({ image_url: e.target.value, type: 'url' }); });

    // 上传
    bind('wp-upload', 'change', function (e) {
      var file = e.target.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function (ev) {
        var base64 = ev.target.result.split(',')[1];
        apiPost('/api/plugin/wallpaper/upload', { filename: file.name, data: base64 })
          .then(function (r) {
            if (r && r.ok) {
              currentConfig = r.config;
              applyConfig(currentConfig);
              alert('上传成功！');
            } else {
              alert('上传失败：' + (r && r.err ? r.err : '未知错误'));
            }
          });
      };
      reader.readAsDataURL(file);
    });
  }

  // ========== 注册页面 ==========
  function registerPage() {
    if (window.VigilPlugin && typeof window.VigilPlugin.registerPage === 'function') {
      window.VigilPlugin.registerPage({
        id: 'wallpaper',
        name: '壁纸',
        icon: 'i-img',
        render: renderSettings,
      });
    }
  }

  // ========== 启动 ==========
  function init() {
    loadConfig();
    registerPage();
    // 监听主题变化
    if (window.matchMedia) {
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
        if (currentConfig) applyConfig(currentConfig);
      });
    }
    console.log('[wallpaper] 前端注入成功');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
