// wallpaper 插件前端：加渐变背景层 + 注册「壁纸」设置页
(function () {
  'use strict';

  // 1. 加一个固定背景层
  const layer = document.createElement('div');
  layer.id = 'wallpaper-bg-layer';
  layer.style.cssText = [
    'position:fixed', 'inset:0', 'z-index:-1',
    'background:linear-gradient(135deg,#667eea 0%,#764ba2 100%)',
    'opacity:0.12', 'pointer-events:none', 'transition:opacity 0.3s',
  ].join(';');
  document.body.appendChild(layer);

  // 2. 注册一个设置页
  if (window.VigilPlugin && typeof window.VigilPlugin.registerPage === 'function') {
    window.VigilPlugin.registerPage({
      id: 'wallpaper',
      name: '壁纸',
      icon: 'i-img',
      render: function (ctx) {
        ctx.view.innerHTML =
          '<div class="wrap">' +
          '<div class="card"><h2>壁纸设置</h2>' +
          '<p>这是由 wallpaper 插件自动注入的页面。</p>' +
          '<p>后端接口测试：<span id="wp-hello">加载中...</span></p>' +
          '<button class="btn" id="wp-toggle" style="margin-top:12px">切换背景显隐</button>' +
          '</div></div>';

        // 调后端接口验证链路
        if (window.VigilAPI && window.VigilAPI.get) {
          window.VigilAPI.get('/api/plugin/wallpaper/hello').then(function (r) {
            const el = document.getElementById('wp-hello');
            if (el) el.textContent = (r && r.ok && r.json) ? r.json.msg : '失败';
          });
        }

        // 切换背景
        const btn = document.getElementById('wp-toggle');
        if (btn) btn.addEventListener('click', function () {
          const l = document.getElementById('wallpaper-bg-layer');
          if (l) l.style.opacity = l.style.opacity === '0' ? '0.12' : '0';
        });
      },
    });
  }

  console.log('[wallpaper] 前端注入成功');
})();
