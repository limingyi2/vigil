/** Plugin market page renderer. Loaded as a classic script before pages.js. */
(function (global) {
  'use strict';
  const { esc, icon, toast } = U;
  const { state } = API;
  const marketPending = new Map();
  async function renderPluginMarket(ctx, confirm) {
    let disposed = false;
    const onMarketChanged = () => { if (!disposed) void load(); };
    window.addEventListener('vigil-market-changed', onMarketChanged);
    ctx.onDispose(() => { disposed = true; window.removeEventListener('vigil-market-changed', onMarketChanged); });
    ctx.panel.innerHTML = '';
    ctx.view.classList.add('market-view');
    ctx.topbar.innerHTML =
      '<span class="tag">§ 插件市场</span>' +
      '<div style="flex:1"></div>' +
      '<button class="btn ghost" id="pmExport">导出诊断</button>' +
      '<button class="btn ghost" id="pmRefresh">刷新市场</button>' +
      '<span class="muted" id="pmRepo" style="margin-left:12px"></span>';

    ctx.view.innerHTML =
      '<div class="wrap market-wrap">' +
      '<h1>插件市场</h1><p class="muted market-intro">为工作区添加插件，管理已安装的功能。</p>' +
      '<div id="pmStatus" class="muted" role="status" style="margin-bottom:12px"></div>' +
      '<div id="pmGrid" class="market-grid"></div>' +
      '<div id="pmEmpty" class="muted" role="status" style="text-align:center;padding:40px;display:none"></div>' +
      '</div>';

    let marketData = null;
    let marketTransport = null;

    async function load() {
      if (disposed) return;
      const grid = ctx.view.querySelector('#pmGrid');
      grid.innerHTML = '<div class="muted">加载中...</div>';
      try {
        const r = await API.get('/api/market/list');
        if (disposed) return;
        marketTransport = r ? { ok: !!r.ok, status: Number(r.status) || 0,
          diagnostic: r.diagnostic || null,
          market: r.json ? { state: r.json.state, source: r.json.source,
            market_version: r.json.market_version, updated: r.json.updated,
            fetched_at: r.json.fetched_at, diagnostic: r.json.diagnostic || null } : null,
          error: String(r.err || '').slice(0, 180) } : { ok: false, error: 'No response' };
        if (!r || !r.ok || !r.json) {
          const diag = r && r.diagnostic;
          const detail = diag ? '[' + esc(diag.stage || 'electron') + '/' + esc(diag.kind || 'unknown') + '] ' + esc(diag.message || '') : '';
          const msg = (r && r.err) || '未知错误';
          grid.innerHTML = '<div class="muted">加载失败：' + esc(msg) + (detail ? '<br><small>' + detail + '</small>' : '') + '</div>';
          marketTransport = { ok: false, status: Number(r && r.status) || 0, diagnostic: diag || null,
            error: String(msg).slice(0, 180) };
          return;
        }
        marketData = r.json;
        ctx.topbar.querySelector('#pmRepo').textContent =
          '已安装 ' + (r.json.installed_count || 0) + ' 个';
        const d = r.json.diagnostic;
        const diagText = d ? ' [' + d.stage + '/' + d.kind + '] ' + d.message : '';
        ctx.view.querySelector('#pmStatus').textContent = r.json.state === 'ready' ? '' : (r.json.state_message || '市场状态未知') + diagText;
        draw();
      } catch (e) {
        if (disposed) return;
        grid.innerHTML = '<div class="muted">加载失败：' + esc(e.message) + '</div>';
      }
    }

    function draw() {
      const grid = ctx.view.querySelector('#pmGrid');
      const empty = ctx.view.querySelector('#pmEmpty');
      const plugins = (marketData && marketData.plugins) || [];
      if (!plugins.length) {
        grid.innerHTML = '';
        empty.textContent = (marketData && marketData.state_message) || '市场状态未知';
        empty.style.display = 'block';
        return;
      }
      empty.style.display = 'none';
      grid.innerHTML = plugins.map(function (p) {
        const tags = (p.tags || []).map(function (t) {
          return '<span class="tag" style="margin-right:4px;margin-bottom:4px">' + esc(t) + '</span>';
        }).join('');
        const localVersion = p.installed ? (p.version || '未知') : '';
        const marketVersion = p.market_version || p.version || '未知';
        // 非市场条目（内置/本地残留）没有"市场版本"可言，别显示 v未知。
        const versionLabel = p.in_market === false
          ? '<span class="muted" style="font-size:12px">本地组件</span>'
          : '<span class="muted" style="font-size:12px">市场 v' + esc(marketVersion) + '</span>';
        const state = p.state === 'restart_required' ? p.state :
          (p.runtime_state || p.state || (p.installed ? 'unknown' : 'not_installed'));
        const updateAvailable = p.installed && p.update_state === 'update_available';
        const actionButtons = p.installed
          ? (p.state === 'restart_required' ? '<button class="btn" disabled>待重启</button>' :
            (p.builtin ?
            '<button class="btn" data-action="reload" data-name="' + esc(p.name) + '">重载</button>' +
            '<button class="btn" data-action="' + (p.enabled === false ? 'enable' : 'disable') + '" data-name="' + esc(p.name) + '">' + (p.enabled === false ? '启用' : '禁用') + '</button>'
            : '<button class="btn" data-action="' + (updateAvailable ? 'update' : 'reload') + '" data-name="' + esc(p.name) + '">' + (updateAvailable ? '更新' : '重载') + '</button>' +
              '<button class="btn" data-action="' + (p.enabled === false ? 'enable' : 'disable') + '" data-name="' + esc(p.name) + '">' + (p.enabled === false ? '启用' : '禁用') + '</button>' +
              (p.removable === false ? '' : '<button class="btn danger" data-action="uninstall" data-name="' + esc(p.name) + '">卸载</button>')))
          : '<button class="btn" data-action="install" data-name="' + esc(p.name) + '">安装</button>';
        const badge = p.builtin
          ? '<span class="tag" style="margin-left:8px">系统内置 · 不可卸载</span>'
          : p.installed
          ? '<span class="tag" style="background:var(--ok-bg);color:var(--ok);margin-left:8px">已安装</span>'
          : '<span class="tag" style="margin-left:8px">未安装</span>';
        return (
          '<div class="card" style="display:flex;flex-direction:column;gap:8px">' +
          '<div style="display:flex;align-items:center;gap:8px">' +
          '<span class="market-icon" aria-hidden="true">' + icon('puzzle', 'md') + '</span>' +
          '<strong>' + esc(p.name) + '</strong>' +
          versionLabel +
          badge +
          '</div>' +
          '<div class="muted" style="font-size:12px">' + esc(p.author || '未知') + '</div>' +
          '<div style="flex:1;font-size:13px;line-height:1.5">' + esc(p.desc || '') + '</div>' +
          (p.builtin ? '<div class="market-meta">随 Vigil 程序发布，可停用；不能单独卸载或更新。</div>' : '') +
          '<div style="margin-top:4px">' + tags + '</div>' +
          (p.installed && p.version ? '<div class="market-meta">本地 v' + esc(localVersion) + ' · ' + esc(updateAvailable ? '有可用更新' : (p.update_state === 'ahead' ? '本地版本较新' : '已是最新')) + '</div>' : '') +
          '<div class="market-meta">运行状态：' + esc(state) +
            (p.lifecycle_enabled === false || p.enabled === false ? ' · 全局已停用' : ' · 全局已启用') +
            (p.lifecycle_enabled && p.effective_by_src && p.effective_by_src.pc === false
              ? ' · PC 渠道关闭' :
               p.lifecycle_enabled && p.effective_by_src && p.effective_by_src.pc === true
              ? ' · PC 渠道可用' : '') + '</div>' +
          (p.diagnostic ? '<div class="market-error">诊断：' + esc(p.diagnostic) + '</div>' : '') +
          '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px">' + actionButtons + '</div>' +
          '</div>'
        );
      }).join('');

      // 绑定按钮事件
      grid.querySelectorAll('button[data-action]').forEach(function (btn) {
        if (marketPending.has(btn.dataset.name)) {
          btn.disabled = true;
          btn.textContent = marketPending.get(btn.dataset.name) === 'install' ? '安装中...' : '卸载中...';
        }
        btn.addEventListener('click', async function () {
          const action = btn.dataset.action;
          const name = btn.dataset.name;
          if (marketPending.has(name)) return;
          if (action === 'uninstall' && !await confirm('确认卸载“' + name + '”？插件会停止并移除，但默认保留用户数据。')) return;
          marketPending.set(name, action);
          btn.disabled = true;
          btn.textContent = ({ install: '安装中...', update: '更新中...', uninstall: '卸载中...', enable: '启用中...', disable: '禁用中...', reload: '重载中...' })[action] || '处理中...';
          try {
            const path = action === 'enable' || action === 'disable' || action === 'reload'
              ? '/api/market/lifecycle' : '/api/market/' + action;
            const body = path.endsWith('/lifecycle') ? { name: name, action: action } : { name: name };
            const r = await API.post(path, body);
            if (r && r.ok && r.json && r.json.ok) {
              marketPending.delete(name);
              if (!disposed) await load();
              if (r.json.msg || r.json.runtime_warning) toast(r.json.msg || r.json.runtime_warning);
            } else {
              const body = r && r.json;
              toast((body && (body.err || body.runtime_warning)) || (r && r.err) || '操作失败');
              btn.disabled = false;
              btn.textContent = action === 'install' ? '安装' : action === 'update' ? '更新' : action === 'uninstall' ? '卸载' : action === 'reload' ? '重载' : action === 'enable' ? '启用' : '禁用';
            }
          } catch (e) {
            toast('操作失败：' + e.message);
            btn.disabled = false;
            btn.textContent = action === 'install' ? '安装' : action === 'update' ? '更新' : action === 'uninstall' ? '卸载' : action === 'reload' ? '重载' : action === 'enable' ? '启用' : '禁用';
          } finally {
            marketPending.delete(name);
            if (!disposed && marketData) await load();
            if (disposed) window.dispatchEvent(new Event('vigil-market-changed'));
          }
        });
      });
    }

    ctx.topbar.querySelector('#pmRefresh').addEventListener('click', async function () {
      const btn = ctx.topbar.querySelector('#pmRefresh');
      btn.disabled = true;
      btn.textContent = '刷新中...';
      try {
        const result = await API.post('/api/market/refresh', {});
        if (!result || !result.ok || !result.json || !result.json.ok) {
          const body = result && result.json;
          const diagnostic = body && body.diagnostic;
          const message = (body && (body.err || body.state_message)) || (result && result.err) || '刷新失败';
          toast(message + (diagnostic ? ' [' + diagnostic.stage + '/' + diagnostic.kind + ']' : ''));
        }
      } catch (e) { toast('刷新失败：' + e.message); }
      await load();
      btn.disabled = false;
      btn.textContent = '刷新市场';
    });

    ctx.topbar.querySelector('#pmExport').addEventListener('click', async function () {
      const button = ctx.topbar.querySelector('#pmExport');
      button.disabled = true;
      try {
        await load();
        const data = { schema: 'vigil.market-diagnostic/v1', created_at: new Date().toISOString(),
          app_version: state.version || '', runtime: API.runtime.kind, transport: marketTransport };
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = 'vigil-market-diagnostic-' + new Date().toISOString().replace(/[:.]/g, '-') + '.json';
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        toast('已导出市场诊断；文件不包含 Token');
      } catch (error) { toast('导出诊断失败：' + String(error.message || error)); }
      finally { button.disabled = false; }
    });

    await load();
  }
  global.VigilRenderPluginMarket = renderPluginMarket;
})(window);
