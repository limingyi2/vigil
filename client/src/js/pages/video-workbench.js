(function (global) {
  'use strict';
  const { state } = API;

  async function renderVideoWorkbench(ctx) {
    ctx.topbar.innerHTML = '<div class="top-title">视频工作台</div><div class="top-subtitle">托管本机 Windows 软件</div>';
    ctx.view.innerHTML = `
      <section class="video-workbench">
        <div class="video-host-frame"><div class="video-host-slot" id="videoHostSlot"><span>启动软件后，窗口将在此处显示</span></div></div>
        <div class="video-tools">
          <label class="video-profile-label"><span>软件</span><select id="videoProfileSelect"><option value="">尚未配置软件</option></select></label>
          <button class="btn primary" id="videoStart">启动并托管</button>
          <button class="btn" id="videoDetach">解除托管</button>
          <button class="btn video-settings-link" id="videoSettings">软件设置</button>
          <span class="video-status" id="videoStatus" role="status">在“软件设置”中添加本机 Windows 软件。</span>
        </div>
        <section class="video-ai-workspace" aria-label="AI 剪视频工作区预留区域">
          <div><span class="video-ai-eyebrow">后续接入</span><h2>AI 剪视频工作区</h2><p>此区域预留给后续 AI 视频剪辑功能。</p></div>
        </section>
      </section>`;

    const $ = id => ctx.view.querySelector('#' + id);
    const api = global.vigil && global.vigil.videoWindow;
    const status = $('videoStatus');
    const setStatus = value => { status.textContent = String(value || ''); };
    if (!api) { setStatus('此页面需要在 Windows 桌面客户端中运行。'); return; }
    const slot = $('videoHostSlot');
    const select = $('videoProfileSelect');
    let profileRows = [];
    let active = true;
    let windowShown = true;
    let slotInView = true;
    const getBounds = () => {
      const rect = slot.getBoundingClientRect();
      return { x: rect.left, y: rect.top, width: rect.width, height: rect.height, dpr: window.devicePixelRatio || 1 };
    };
    const updateBounds = () => { if (active && slot.isConnected) api.bounds(getBounds()); };
    // 容器的显隐由“页面还在 + 主窗口没藏起来 + 绑定区域在视口里”共同决定。
    // 不用 document.visibilityState：窗口被别的程序遮住时它也会变 hidden，
    // 而恢复时并不保证回发事件，容器会一直停在隐藏状态。
    const applyVisibility = () => {
      if (!active || !slot.isConnected) return;
      updateBounds();
      api.visible(windowShown && slotInView);
    };
    const observer = new ResizeObserver(updateBounds);
    observer.observe(slot);
    window.addEventListener('resize', updateBounds);
    const offWindowVisibility = api.onWindowVisibility(visible => { windowShown = visible; applyVisibility(); });
    const intersection = new IntersectionObserver(entries => {
      const entry = entries[entries.length - 1];
      slotInView = !!entry && entry.isIntersecting;
      applyVisibility();
    }, { threshold: 0 });
    intersection.observe(slot);
    const offEvents = api.onEvent(event => {
      if (!active || !event) return;
      // Native window events are the source of truth for the shared capability
      // registry.  The Agent can therefore answer from the same state shown
      // in this workbench instead of maintaining a second local status cache.
      if (global.vigil && typeof global.vigil.api === 'function') {
        const terminal = ['exited', 'detached', 'fallback', 'window-closed-resident',
          'helper-exit', 'session-released'].includes(event.type);
        const live = !terminal && (['starting', 'attaching', 'attached', 'resident',
          'window-hidden', 'window-rebuilding', 'busy'].includes(event.state) ||
          ['attached', 'start-reused', 'recreated'].includes(event.type));
        global.vigil.api('POST', '/api/capabilities/events', {
          capability: 'video_workbench', type: 'state',
          status: {
            connected: live,
            state: event.state || event.type || 'unknown',
            hosted_software: event.profileName || event.exePath || null,
            pid: event.pid || null,
          },
        }).catch(() => {});
      }
      // 托管成功后占位文字必须让位；否则容器被藏起来时页面看起来像是“没启动”。
      if (event.type === 'attached' || event.type === 'start-reused' || event.type === 'recreated') {
        slot.classList.add('is-hosted');
        applyVisibility();
      } else if (event.type === 'detached' || event.type === 'fallback' || event.type === 'exited' ||
                 event.type === 'window-closed-resident' || event.type === 'helper-exit') {
        slot.classList.remove('is-hosted');
      }
      const labels = {
        ready: '原生窗口模块已就绪。', started: '软件进程已启动，正在发现主窗口…',
        'adopted-existing': '软件已经在运行，正在接管它现有的窗口…',
        attached: `软件窗口已就位（PID ${event.pid}）。`,
        detached: event.restored ? '已解除托管，软件窗口已恢复。' : '解除托管时未能完整恢复窗口。',
        fallback: '无法托管此窗口，软件已恢复为独立窗口。',
        'start-reused': '软件已在运行，复用现有进程与窗口。',
        'restore-complete': '软件窗口恢复检查通过。',
        'restore-pending': '软件窗口恢复未完成，Vigil 将保留窗口引用并重试。',
        'restore-failed': `软件窗口恢复失败（${event.stage || '未知阶段'}，Win32 ${event.code ?? '未知'}）；窗口仍由宿主保留并重试。`,
        'restore-window-gone': '目标窗口已不存在，已按窗口销毁状态清理托管引用。',
        'shutdown-blocked': '软件窗口尚未安全恢复，Vigil 保持运行并继续重试。',
        'shutdown-complete': '外部软件窗口已安全恢复，Vigil 可以退出。',
        'popup-standalone': '软件弹窗保持独立显示。',
        'window-hidden': '软件窗口暂时隐藏，进程仍在运行。',
        'window-visible': '软件窗口已恢复显示。',
        'window-lost': '软件窗口正在重建或关闭，正在检查进程状态…',
        'window-closed-resident': '软件窗口已关闭，但后台进程仍驻留；可以启动其他配置。',
        'session-released': '已释放旧软件的托管会话，软件进程未被结束。',
        focus: event.focusSet ? '已向软件窗口切换焦点。' : '已请求软件窗口获取焦点；Windows 未确认焦点切换。',
        'close-requested': '已请求软件正常关闭。',
        'tracking-fallback': 'Windows 作业跟踪不可用，正在跟踪已发现的进程。',
        recreated: '检测到软件重建窗口，正在重新托管…',
        exited: '软件已退出，可以启动其他配置。',
        state: ({ idle: '请选择并启动软件。', starting: '软件正在启动或等待窗口。', attaching: '正在重新托管已运行的软件窗口。', attached: '软件窗口已托管。', 'window-hidden': '软件窗口暂时隐藏。', 'window-rebuilding': '软件正在重建主窗口。', detached: '软件仍在运行，窗口已解除托管。', resident: '软件进程仍驻留，可以启动其他配置。', fallback: '软件正在独立窗口运行。', busy: '另一个配置仍在运行。', 'restore-failed': '窗口恢复失败，保留 HWND 并自动重试。' })[event.state] || `软件状态：${event.state || '未知'}`,
      };
      if (labels[event.type]) setStatus(labels[event.type]);
      else if (event.type === 'error') setStatus(`窗口托管错误（${event.stage}，${event.code}）：${event.message}`);
      else if (event.type === 'helper-error' || event.type === 'protocol-error') setStatus(event.message || '原生窗口模块错误。');
      else if (event.type === 'helper-exit') setStatus('原生窗口模块已退出。');
    });
    async function refreshProfiles() {
      profileRows = await api.profiles();
      const previous = select.value;
      select.replaceChildren(new Option(profileRows.length ? '选择软件' : '尚未配置软件', ''));
      profileRows.forEach(profile => select.add(new Option(profile.name, profile.id)));
      if (profileRows.some(profile => profile.id === previous)) select.value = previous;
      else if (profileRows.length === 1) select.value = profileRows[0].id;
    }
    $('videoStart').addEventListener('click', async () => {
      try {
        const launchProfile = profileRows.find(row => row.id === select.value);
        if (!launchProfile) { setStatus('请先在“软件设置”中添加或选择软件。'); return; }
        await api.action('ATTACH');
        setStatus('正在启动软件…'); await api.start(launchProfile, getBounds()); applyVisibility();
      } catch (error) { setStatus(error.message); }
    });
    $('videoDetach').onclick = () => api.action('DETACH');
    $('videoSettings').onclick = () => { state.settingsDetail = true; state.settingsSub = 'video-apps'; ctx.navigate('system'); };
    await refreshProfiles();
    api.action('STATUS');
    applyVisibility();
    ctx.onDispose(() => {
      active = false; observer.disconnect(); intersection.disconnect();
      window.removeEventListener('resize', updateBounds); offWindowVisibility(); offEvents();
      api.visible(false);
    });
  }


  global.VigilRenderVideoWorkbench = renderVideoWorkbench;
})(window);
