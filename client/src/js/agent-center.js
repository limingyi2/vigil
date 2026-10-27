(function (global) {
  'use strict';
  const {esc,icon,fmtTime,toast} = U;
  const listeners = new Set();
  let started = false, timer;
  // 状态轮询与桌面通知保留：审批事件、Codex 能力上报仍由 Brain 侧驱动；
  // 页面本身不再渲染受管任务，改由桌宠展示本机 Codex 状态。
  const controller = AgentCenterState.create({
    request:async path => API.pick(await API.get(path)),
    connect:async () => API.pick(await API.post('/api/agent-center/connect',{})),
    notify:async event => {
      toast(event.kind === 'approval' ? 'Codex 等待审批，请查看 Agent Center' : event.kind === 'input' ? 'Codex 需要补充输入' : 'Codex 任务失败，请查看 Agent Center',5000);
      const bridge = global.vigil && global.vigil.agentCenter;
      if (bridge) {
        const r = await bridge.notify(event);
        if (!r.ok) toast('桌面通知不可用，提醒已显示在客户端内');
      }
    },
    changed:state => {
      publishCapabilityState(state);
      listeners.forEach(fn => fn(state));
    },
  });
  function publishCapabilityState(s) {
    if (!global.vigil || typeof global.vigil.api !== 'function' || !s) return;
    const connection = String(s.status && s.status.connection_status || 'unknown');
    const connected = !s.stale && connection === 'connected';
    global.vigil.api('POST', '/api/capabilities/events', {
      capability: 'codex', type: 'state',
      status: {
        connected,
        state: s.stale ? 'stale' : connection,
        login: s.status && s.status.runtime && s.status.runtime.auth_status || 'unknown',
        tasks: Array.isArray(s.owned) ? s.owned.length : 0,
        pending: Array.isArray(s.approvals) ? s.approvals.filter(x => x.state === 'pending').length : 0,
      },
    }).catch(() => {});
  }
  function start() {
    if (started || !global.vigil) return;
    started = true;
    const tick = async () => {
      await controller.poll();
      timer = setTimeout(tick,5000);
    };
    tick();
    global.addEventListener('beforeunload', () => clearTimeout(timer),{once:true});
  }
  function overviewError(raw) {
    const text = String(raw || '');
    if (AgentCenterState.connectionErrors[text]) return AgentCenterState.connectionErrors[text];
    if (Object.values(AgentCenterState.connectionErrors).includes(text)) return text;
    if (/当前 Brain 版本不支持|agent_center_route_missing|no such api|not found/i.test(text))
      return '当前 Brain 版本不支持 Agent Center，请检查版本';
    if (/Token|权限|bad token|forbidden|admin/i.test(text))
      return 'Agent Center 连接被拒绝：请检查 Token 和管理员权限';
    if (/App Server|unavailable|不可用|未启动/i.test(text))
      return 'Codex App Server 未启动或当前不可用';
    return text || '';
  }
  function connectionHelp(s, busy) {
    const runtime=s.status.runtime || {}, code=s.status.last_error || runtime.error;
    const auth={unchecked:'等待检测',unknown:'未知',logged_in:'已登录（官方 CLI 本地状态）',logged_out:'未登录',logging_in:'等待官方浏览器登录'}[runtime.auth_status] || '等待检测';
    const blocked=busy || s.stale || runtime.auth_status==='logging_in' || s.status.connection_status==='connecting';
    return `<h3>本机 Codex 连接</h3><p>CLI：${esc(runtime.version || (runtime.installed===false?'未找到':'等待检测'))} · 登录：${esc(auth)}</p>${code?`<p role="status">${esc(overviewError(code))}</p>`:''}<p class="ac-muted">桌宠直接读取本机 Codex 的会话与额度；受管 App Server 仅供后台通知使用。已有 Codex 桌面会话仅可读取历史。</p><div class="ac-actions"><button class="btn" data-ac-help="install">官方安装说明</button><button class="btn" data-ac-help="auth">官方登录说明</button><button class="btn" data-ac-help="login" ${blocked||runtime.installed===false||s.status.connection_status==='connected'?'disabled':''}>通过官方 CLI 登录</button><button class="btn" data-ac-help="connect" ${blocked?'disabled':''}>重新检测并连接</button></div><p class="ac-muted">终端安装：<code>npm install -g @openai/codex</code> · 终端登录：<code>codex login</code>。账号认证由 Codex 管理。</p>`;
  }
  const petLabels = {stopped:'未运行',starting:'启动中',running:'运行中',unavailable:'不可用'};
  function petCard(pet) {
    const status = pet && pet.status || 'stopped';
    const running = status === 'running';
    const chip = `ac-pet-chip ac-pet-${status}`;
    return `<section class="ac-card ac-pet" id="ac-pet-card">
      <div class="ac-heading"><h2>Codex 桌宠</h2><span class="${chip}"><i></i>${esc(petLabels[status] || '未知')}</span></div>
      <p class="ac-muted">移植自 codex-bailong-desktop-pet 的桌面小助手：桌面上直接查看 Codex 任务状态、完成提醒、额度环与 Token 日报。进程独立运行，与客户端互不阻塞；同一时刻只能有一个实例。</p>
      <p class="ac-muted">当前构建不含人物动画素材，桌宠以状态卡片模式显示；动画可在桌宠设置里从上游发布渠道单独下载。</p>
      <div class="ac-actions">
        <button class="btn fill" id="ac-pet-start" type="button" ${running||status==='starting'?'disabled':''}>${icon('activity','xs')} 启动桌宠</button>
        <button class="btn" id="ac-pet-stop" type="button" ${running?'':'disabled'}>退出桌宠</button>
        <span class="ac-muted" id="ac-pet-updated"></span>
      </div>
      <p class="ac-error" id="ac-pet-error" role="alert" ${pet && pet.lastError ? '' : 'hidden'}>${esc(pet && pet.lastError || '')}</p>
    </section>`;
  }
  function render(ctx) {
    ctx.view.classList.add('ac-view'); ctx.topbar.classList.add('ac-topbar');
    ctx.topbar.innerHTML = `<h1>Agent 工作室</h1><span class="sp"></span><span>桌宠与 Codex 本机状态</span>`;
    ctx.view.innerHTML = `<div class="ac-workspace" id="ac-overview">
      <section class="ac-workspace-hero"><div><p class="ac-eyebrow">AGENT WORKSPACE</p><h2>你的桌面助手</h2><p class="ac-muted">桌宠常驻桌面展示 Codex 实时状态；这里负责把它拉起来，以及保证本机 Codex CLI 可用。</p></div><div class="ac-hero-actions"><span class="ac-sync-note" id="ac-overview-updated">等待同步…</span><button class="btn" id="ac-overview-refresh" type="button">${icon('refresh','xs')} 刷新</button></div></section>
      <p class="ac-error" id="ac-overview-error" role="alert" hidden></p>
      <div id="ac-pet-host"></div>
      <section class="ac-card" id="ac-overview-connection"></section>
    </div>`;
    ctx.panel.innerHTML = `<div class="ac-studio-nav"><div class="phead"><h2>Agent 工作室</h2><p>桌宠与 Codex</p></div><button class="ac-nav-item on" id="ac-nav-overview" type="button">${icon('activity','sm')} <span>总览</span></button><div class="ac-nav-hint">桌宠独立进程<br>直读本机 Codex 数据</div></div>`;
    let busy = false, disposed = false, petTimer;
    const q = id => ctx.view.querySelector('#' + id);
    function write(el, html) { if (el && el.innerHTML !== html) el.innerHTML = html; }
    async function refreshPet(paint=true) {
      if (disposed || !global.vigil || !global.vigil.pet) return;
      const r = await global.vigil.pet.status().catch(() => null);
      if (disposed) return;
      const host = q('ac-pet-host');
      if (host) {
        const html = petCard(r);
        if (host.innerHTML !== html) host.innerHTML = html;
        bindPet();
        q('ac-pet-updated').textContent = '同步于 ' + fmtTime(Date.now()/1000,'full');
      }
    }
    function petAction(fn, label) {
      if (busy) return; busy = true;
      const host = q('ac-pet-host');
      host && host.querySelectorAll('button').forEach(b => b.disabled = true);
      fn().then(r => {
        if (r && r.ok === false && r.error) toast(r.error, 6000);
      }).catch(() => toast(label + '失败', 5000)).finally(() => { busy = false; refreshPet(); });
    }
    function bindPet() {
      const startBtn = q('ac-pet-start'), stopBtn = q('ac-pet-stop');
      if (startBtn) startBtn.onclick = () => petAction(() => global.vigil.pet.start(), '启动桌宠');
      if (stopBtn) stopBtn.onclick = () => petAction(() => global.vigil.pet.stop(), '退出桌宠');
    }
    function paint(s) {
      write(q('ac-overview-connection'), connectionHelp(s,busy));
      q('ac-overview-updated').textContent = s.updatedAt ? '同步于 ' + fmtTime(s.updatedAt, 'full') : '等待同步…';
      const error = overviewError(s.error || (s.stale && s.ready ? '状态已过期' : ''));
      q('ac-overview-error').hidden = !error; q('ac-overview-error').textContent = error;
    }
    const onViewClick=e=>{const help=e.target.closest('[data-ac-help]');if(help){const kind=help.dataset.acHelp;if(help.disabled)return;if(kind==='install'||kind==='auth'){global.vigil.openExternal(kind==='install'?'https://developers.openai.com/codex/cli':'https://developers.openai.com/codex/auth').catch(()=>toast('无法打开浏览器，请检查系统默认浏览器。'));}else{busy=true;API.post('/api/agent-center/'+kind,{}).then(API.pick).then(r=>{if(!r.ok)throw new Error(overviewError(r._err||r.err)||'操作失败');}).catch(err=>toast(err.message||'操作失败')).finally(async()=>{busy=false;await controller.poll();});}}};
    ctx.view.addEventListener('click',onViewClick);
    listeners.add(paint); paint(controller.state); start();
    refreshPet(); petTimer = setInterval(refreshPet, 5000);
    ctx.onDispose(()=>{disposed=true;listeners.delete(paint);ctx.view.removeEventListener('click',onViewClick);clearInterval(petTimer);});
    if(!global.vigil){q('ac-overview-error').hidden=false;q('ac-overview-error').textContent=API.runtime.preview?'浏览器预览：Agent Center 需要 Electron preload 和本机管理员连接。':'Electron preload 未加载，桌宠与 Agent Center IPC 不可用。';}
  }

  global.VigilAgentCenter = {start,controller};
  global.PAGES.splice(1,0,{id:'agent-center',name:'Agent Center',icon:'activity',render});
})(window);
