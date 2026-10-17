(function () {
  'use strict';
  if (!window.vigil || typeof window.vigil.api !== 'function') return;
  if (typeof window.__vigilBranchAtlasDispose === 'function') {
    try { window.__vigilBranchAtlasDispose(); } catch (_) {}
  }

  const root = document.createElement('div');
  root.id = 'branch-atlas-root';
  root.innerHTML =
    '<button type="button" class="ba-launch" data-action="toggle" aria-expanded="false">Git 分支图谱</button>' +
    '<section class="ba-panel" data-panel hidden aria-label="Git 分支图谱">' +
      '<header class="ba-head">' +
        '<div><div class="ba-title">Branch Atlas</div><div class="ba-subtitle">本地 Git 分支与提交拓扑 · 只读</div></div>' +
        '<button type="button" class="ba-close" data-action="close" aria-label="关闭">×</button>' +
      '</header>' +
      '<div class="ba-toolbar">' +
        '<button type="button" class="ba-button primary" data-action="pick">选择仓库</button>' +
        '<button type="button" class="ba-button" data-action="refresh" disabled>刷新</button>' +
        '<span class="ba-current" data-current>尚未选择仓库</span>' +
      '</div>' +
      '<div class="ba-status" data-status role="status">选择一个包含 .git 的仓库根目录即可查看。</div>' +
      '<div class="ba-meta" data-meta hidden></div>' +
      '<div class="ba-columns">' +
        '<section class="ba-section"><h3>分支</h3><div class="ba-branches" data-branches>尚无数据</div></section>' +
        '<section class="ba-section"><h3>工作区状态</h3><pre class="ba-worktree" data-worktree>尚无数据</pre></section>' +
      '</div>' +
      '<section class="ba-section ba-graph-section"><h3>最近提交拓扑 <span data-graph-count></span></h3>' +
        '<pre class="ba-graph" data-graph>选择仓库后显示 git log --graph。</pre></section>' +
      '<footer class="ba-foot">只读取 Git 元数据；不会切换分支、提交、拉取或修改项目文件。</footer>' +
    '</section>';
  document.body.appendChild(root);

  const q = selector => root.querySelector(selector);
  const panel = q('[data-panel]');
  const launch = q('[data-action="toggle"]');
  const pickButton = q('[data-action="pick"]');
  const refreshButton = q('[data-action="refresh"]');
  const currentLabel = q('[data-current]');
  const status = q('[data-status]');
  const meta = q('[data-meta]');
  const branches = q('[data-branches]');
  const worktree = q('[data-worktree]');
  const graph = q('[data-graph]');
  const graphCount = q('[data-graph-count]');
  let selectedPath = '';
  let busy = false;

  function setStatus(message, isError) {
    status.textContent = String(message || '');
    status.classList.toggle('is-error', !!isError);
    status.classList.toggle('is-busy', !!busy);
  }

  function toggle(open) {
    const visible = typeof open === 'boolean' ? open : panel.hidden;
    panel.hidden = !visible;
    launch.setAttribute('aria-expanded', String(visible));
  }

  function fillList(container, lines, emptyText) {
    container.replaceChildren();
    if (!lines || !lines.length) {
      const empty = document.createElement('div');
      empty.className = 'ba-empty';
      empty.textContent = emptyText || '没有分支';
      container.appendChild(empty);
      return;
    }
    for (const line of lines) {
      const item = document.createElement('div');
      item.className = 'ba-branch';
      const text = String(line || '').trim();
      item.textContent = text || '（空分支名）';
      if (text.startsWith('*')) item.classList.add('is-current');
      container.appendChild(item);
    }
  }

  function render(data) {
    const branchCount = Number(data.branch_count) || (data.branches || []).length;
    currentLabel.textContent = data.path || selectedPath;
    meta.hidden = false;
    meta.textContent = '当前分支：' + (data.branch || '(detached HEAD)') +
      ' · 分支 ' + branchCount + ' 个' +
      (data.branches_truncated ? '（列表已截断）' : '');
    fillList(branches, data.branches || [], '没有本地或远程分支');
    worktree.textContent = data.status || '（工作区状态为空）';
    graph.textContent = data.graph || '（仓库中没有可显示的提交）';
    graphCount.textContent = data.graph_truncated ? '（已截断）' : '';
    refreshButton.disabled = false;
    setStatus('读取完成。');
  }

  async function refresh() {
    if (busy) return;
    if (!selectedPath) {
      setStatus('请先选择一个本地 Git 仓库文件夹。', true);
      return;
    }
    busy = true;
    pickButton.disabled = true;
    refreshButton.disabled = true;
    setStatus('正在读取分支和提交记录，请稍候…');
    try {
      const response = await window.vigil.api(
        'POST', '/api/plugin/branch-atlas/inspect', { path: selectedPath });
      const data = response && response.json;
      if (!response || !response.ok || !data || data.ok === false) {
        const detail = (data && data.err) || (response && response.err) ||
          ('HTTP ' + String((response && response.status) || 0));
        setStatus('读取失败：' + detail, true);
        return;
      }
      render(data);
    } catch (error) {
      setStatus('读取失败：' + String(error && error.message || error), true);
    } finally {
      busy = false;
      pickButton.disabled = false;
      refreshButton.disabled = !selectedPath;
      status.classList.remove('is-busy');
    }
  }

  async function pickDirectory() {
    if (busy) return;
    const picker = window.vigil.projectPicker;
    if (!picker || typeof picker.pickDirectory !== 'function') {
      setStatus('当前客户端还没有本地文件夹选择器；请更新 Vigil 客户端后重试。', true);
      return;
    }
    try {
      const result = await picker.pickDirectory();
      if (!result || !result.ok) {
        setStatus('打开文件夹选择器失败：' + String((result && result.err) || '未知错误'), true);
        return;
      }
      if (result.canceled) return;
      if (!result.path) {
        setStatus('选择器没有返回文件夹路径。', true);
        return;
      }
      selectedPath = String(result.path);
      currentLabel.textContent = selectedPath;
      await refresh();
    } catch (error) {
      setStatus('打开文件夹选择器失败：' + String(error && error.message || error), true);
    }
  }

  root.addEventListener('click', event => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const action = button.getAttribute('data-action');
    if (action === 'toggle') toggle();
    else if (action === 'close') toggle(false);
    else if (action === 'pick') pickDirectory();
    else if (action === 'refresh') refresh();
  });

  window.__vigilBranchAtlasDispose = function () {
    root.remove();
    delete window.__vigilBranchAtlasDispose;
  };
})();
