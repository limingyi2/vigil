(function (global) {
  'use strict';
  const {esc,icon,fmtTime,toast} = U;
  const listeners = new Set();
  const labels = {idle:'就绪',starting:'启动中',running:'运行中',waiting_approval:'等待审批',
    waiting_input:'等待输入',completed:'已完成',failed:'失败',interrupted:'已中断',unknown:'未知'};
  const operations = {commandExecution:'执行命令',fileChange:'文件操作',mcpToolCall:'MCP 工具',
    dynamicToolCall:'动态工具',webSearch:'网页检索'};
  let started = false, timer;
  const controller = AgentCenterState.create({
    request:async path => API.pick(await API.get(path)),
    notify:async event => {
      toast(event.kind === 'approval' ? 'Codex 等待审批 · 答复尚未开放' : event.kind === 'input' ? 'Codex 需要补充输入' : 'Codex 任务失败，请查看 Agent Center',5000);
      const bridge = global.vigil && global.vigil.agentCenter;
      if (bridge) {
        const r = await bridge.notify(event);
        if (!r.ok) toast('桌面通知不可用，提醒已显示在客户端内');
      }
    },
    changed:state => listeners.forEach(fn => fn(state)),
  });
  function start() {
    if (started || !global.vigil) return;
    started = true;
    const tick = async () => {
      await controller.poll();
      const active = controller.state.owned.some(t => ['starting','running','waiting_approval','waiting_input'].includes(t.status));
      timer = setTimeout(tick,active ? 2000 : 5000);
    };
    tick();
    global.addEventListener('beforeunload', () => clearTimeout(timer),{once:true});
  }
  function badge(status) {
    return `<span class="ac-state ac-${esc(status)}">${esc(labels[status] || '未知')}</span>`;
  }
  function write(el, html) { if (el.innerHTML !== html) el.innerHTML = html; }
  function approvalCard(a,s,connected) {
    const generation=a.connection_generation||a.generation;
    const stale=s.stale||!connected||generation!==s.status.generation;
    const risk=a.risk||{}, usable=!!a.response_enabled&&!stale&&a.state==='pending';
    const identity=`<details class="ac-risk-identity"><summary>核对请求归属</summary><dl><div><dt>连接</dt><dd>${esc(a.connection)}</dd></div><div><dt>连接代次</dt><dd>${esc(generation)}</dd></div><div><dt>Thread</dt><dd>${esc(a.thread_id)}</dd></div><div><dt>Turn</dt><dd>${esc(a.turn_id)}</dd></div><div><dt>Request ID</dt><dd>${esc(String(a.request_id))}</dd></div><div><dt>Item ID</dt><dd>${esc(a.item_id)}</dd></div></dl></details>`;
    let detail='';
    if(a.method==='item/commandExecution/requestApproval') detail=`<div class="ac-risk"><b>拟执行命令</b><pre>${esc(risk.command||'缺失')}</pre><p>工作目录：<code>${esc(risk.cwd||'缺失')}</code></p>${risk.reason?`<p>原因：${esc(risk.reason)}</p>`:''}${risk.environment_id?`<p>执行环境：<code>${esc(risk.environment_id)}</code></p>`:''}${risk.command_actions?`<details><summary>Codex 解析出的操作范围</summary><pre>${esc(JSON.stringify(risk.command_actions,null,2))}</pre></details>`:''}${risk.additional_permissions?`<div class="ac-warning">请求额外文件/网络权限：<pre>${esc(JSON.stringify(risk.additional_permissions,null,2))}</pre></div>`:''}${risk.network_approval_context?`<div class="ac-warning">网络审批上下文：<pre>${esc(JSON.stringify(risk.network_approval_context,null,2))}</pre></div>`:''}${risk.proposed_execpolicy_amendment||risk.proposed_network_policy_amendments?`<div class="ac-warning">请求持久权限策略变更：<pre>${esc(JSON.stringify({execpolicy:risk.proposed_execpolicy_amendment,network:risk.proposed_network_policy_amendments},null,2))}</pre></div>`:''}</div>`;
    else if(a.method==='item/fileChange/requestApproval') detail=`<div class="ac-risk"><b>文件修改预览（${(risk.changes||[]).length} 项）</b>${risk.reason?`<p>原因：${esc(risk.reason)}</p>`:''}${risk.grant_root?`<p class="ac-warning">请求授权根目录：<code>${esc(risk.grant_root)}</code></p>`:''}${(risk.changes||[]).map(c=>`<details class="ac-file-change"><summary>${esc(c.kind)} · ${esc(c.path)}</summary><pre>${esc(c.diff)}</pre></details>`).join('')}</div>`;
    else if(a.method==='item/tool/requestUserInput') detail=`<div class="ac-risk"><b>Codex 请求补充输入</b><form class="ac-answer-form" data-ac-answer="${esc(a.id)}">${(risk.questions||[]).map(q=>`<fieldset><legend>${esc(q.header)} · ${esc(q.question)}</legend>${(q.options||[]).map((o,i)=>`<label class="ac-choice"><input type="checkbox" name="answer-${esc(q.id)}" value="${esc(o.label)}"><span><b>${esc(o.label)}</b><small>${esc(o.description)}</small></span></label>`).join('')}${q.isOther||!q.options?.length?`<label class="ac-free-answer">${q.isSecret?'安全输入':'补充回答'}<input type="${q.isSecret?'password':'text'}" name="other-${esc(q.id)}" maxlength="8192" autocomplete="${q.isSecret?'new-password':'off'}"></label>`:''}${q.isSecret?'<small>此答案仅在提交时发送，不写入本地历史。</small>':''}</fieldset>`).join('')}<button class="btn fill" type="submit" ${usable?'':'disabled'}>提交回答</button></form></div>`;
    const denied=a.state==='expired'?'请求已过期，不能答复':a.state==='responded'||a.state==='resolved'?'请求已处理':stale?'连接或状态已过期，不能答复':a.response_enabled?'':'风险信息不完整或协议类型不支持，已禁用答复';
    const decisions=a.method==='item/tool/requestUserInput'?`<span class="ac-muted">需要你填写并主动提交</span>`:usable?`<button class="btn" data-ac-respond="decline" data-approval="${esc(a.id)}">拒绝本次请求</button><button class="btn fill" data-ac-respond="accept" data-approval="${esc(a.id)}">仅本次批准</button>`:`<span class="ac-muted">${esc(denied)}</span>`;
    return `<article class="ac-approval ac-approval-full"><header><strong>${a.method==='item/tool/requestUserInput'?'需要输入':a.method==='item/fileChange/requestApproval'?'文件修改审批':'命令审批'}</strong><span class="ac-state">${esc(a.state)}</span></header><p>owned 受管会话 · 任务 ${esc(a.task_id.slice(0,8))} · ${esc(fmtTime(a.created_at,'full'))} · ${Math.max(0,Math.ceil((a.expires_at-Date.now()/1000)/60))} 分钟后过期</p>${detail}${identity}<footer>${decisions}</footer><small>所有批准仅对此请求生效，由你点击提交；Codex 原生沙箱与权限校验仍由 App Server 执行。</small></article>`;
  }
  const pendingAgents = [
    { id:'deepseek-harness', name:'DeepSeek Harness', type:'待接入适配器', purpose:'模型代理与任务执行',
      connection_source:'未配置 Agent Adapter' },
    { id:'work-buddy', name:'Work Buddy', type:'待接入适配器', purpose:'办公协作与自动化',
      connection_source:'未配置 Agent Adapter' },
  ];
  const overviewLabels = { online:'在线', connecting:'连接中', disconnected:'已断开',
    stale:'状态已过期', unsupported:'版本不支持', error:'连接错误', unknown:'未知', pending:'待接入' };
  function overviewError(raw) {
    const text = String(raw || '');
    if (/当前 Brain 版本不支持|agent_center_route_missing|no such api|not found/i.test(text))
      return '当前 Brain 版本不支持 Agent Center，请检查版本';
    if (/Token|权限|bad token|forbidden|admin/i.test(text))
      return 'Agent Center 连接被拒绝：请检查 Token 和管理员权限';
    if (/App Server|unavailable|不可用|未启动/i.test(text))
      return 'Codex App Server 未启动或当前不可用';
    return text || '';
  }
  function codexOverview(s) {
    const status = s.error && /版本不支持/.test(s.error) ? 'unsupported' :
      s.error ? 'error' : s.stale ? (s.ready ? 'stale' : 'unknown') :
      ({connected:'online',connecting:'connecting',disconnected:'disconnected'}[s.status.connection_status] || 'unknown');
    const active = (s.owned || []).find(t => ['starting','running','waiting_approval','waiting_input'].includes(t.status));
    const latestActivity = (s.owned || []).slice().sort((a,b) => Number(b.last_activity || 0) - Number(a.last_activity || 0))[0];
    const pending = (s.approvals || []).filter(a => a.ownership === 'owned' && a.state === 'pending' &&
      (a.connection_generation || a.generation) === s.status.generation).length;
    const capabilities = ['受管任务', 'owned / observed'];
    if (s.status.approval_response_enabled) capabilities.push('原生审批响应');
    else capabilities.push('审批只读');
    return { id:'codex', name:'Codex', type:'Coding Agent', purpose:'代码、文件与任务执行',
      status, statusText:overviewLabels[status], current_task:active ? (active.current_operation ?
        (operations[active.current_operation.type] || '执行中') : (labels[active.status] || '执行中')) : '暂无运行中的任务',
      taskCount:(s.owned || []).length, pending, capabilities,
      recent_activity:latestActivity ? fmtTime(latestActivity.last_activity || latestActivity.created_at, 'full') || '暂无活动记录' : '暂无活动记录',
      connection_source:'本机 Brain · Codex App Server', error:overviewError(s.error || s.status.last_error),
      connected:status === 'online' };
  }
  function agentCard(a) {
    const ready = a.id === 'codex' && a.status === 'online';
    const statusClass = 'ac-agent-status-' + a.status;
    const action = a.id === 'codex' ? '打开管理' : '查看接入要求';
    return `<article class="ac-agent-card ac-agent-${esc(a.id)} ${a.id === 'codex' ? 'is-live' : 'is-pending'}" data-ac-agent-card="${esc(a.id)}" tabindex="0" role="button" aria-label="${esc(a.name)}：${esc(a.statusText)}">
      <header class="ac-agent-card-head"><span class="ac-agent-mark">${icon(a.id === 'codex' ? 'activity' : 'plug','sm')}</span><div><h3>${esc(a.name)}</h3><p>${esc(a.type)} · ${esc(a.purpose)}</p></div><span class="ac-agent-status ${statusClass}"><i></i>${esc(a.statusText)}</span></header>
      <div class="ac-agent-current"><span>当前任务</span><strong>${esc(a.current_task || '—')}</strong>${a.error ? `<small>${esc(a.error)}</small>` : ''}</div>
      <div class="ac-agent-meta"><span><b>${esc(a.taskCount == null ? '—' : a.taskCount)}</b> 个任务</span><span><b>${esc(a.pending || 0)}</b> 待处理</span></div>
      <div class="ac-agent-activity"><span>最近活动</span><strong>${esc(a.recent_activity || '暂无活动记录')}</strong></div>
      <div class="ac-agent-chips">${(a.capabilities || []).map(x => `<span>${esc(x)}</span>`).join('')}</div>
      <footer><span class="ac-agent-source">${esc(a.connection_source)}</span><button class="btn ${ready ? 'fill' : ''}" type="button" data-ac-agent-action="${esc(a.id)}">${action}</button></footer>
    </article>`;
  }
  function render(ctx) {
    ctx.view.classList.add('ac-view'); ctx.topbar.classList.add('ac-topbar');
    ctx.topbar.innerHTML = `<h1>Agent 工作室</h1><span class="sp"></span><span>统一查看和管理多个 Agent</span>`;
    const overviewHtml = `<div class="ac-workspace" id="ac-overview">
      <section class="ac-workspace-hero"><div><p class="ac-eyebrow">AGENT WORKSPACE</p><h2>你的 Agent 团队</h2><p class="ac-muted">把不同来源的 Agent 放在同一张工作台上。只有接入真实 Adapter 的 Agent 才会显示在线和任务控制。</p></div><div class="ac-hero-actions"><span class="ac-sync-note" id="ac-overview-updated">等待同步…</span><button class="btn" id="ac-add-agent" type="button">${icon('plus','xs')} 添加 Agent</button><button class="btn" id="ac-overview-refresh" type="button">${icon('refresh','xs')} 刷新</button></div></section>
      <p class="ac-error" id="ac-overview-error" role="alert" hidden></p>
      <div class="ac-section-heading"><div><h2>Agent 总览</h2><p class="ac-muted" id="ac-agent-count">正在读取连接状态…</p></div><span class="ac-muted">桌面控制台 · 本机工作区</span></div>
      <section class="ac-agent-grid" id="ac-agent-grid" aria-label="Agent 总览"></section>
      <section class="ac-recent-strip"><div class="ac-section-heading"><div><h2>最近任务</h2><p class="ac-muted">只显示已接入 Agent 的轻量摘要</p></div></div><div id="ac-recent-tasks" class="ac-recent-list"></div></section>
    </div><div id="ac-agent-detail-shell" hidden></div>`;
    ctx.view.innerHTML = overviewHtml;
    ctx.panel.innerHTML = `<div class="ac-studio-nav"><div class="phead"><h2>Agent 工作室</h2><p>多 Agent 总览</p></div><button class="ac-nav-item on" id="ac-nav-overview" type="button">${icon('activity','sm')} <span>全部 Agent</span><b id="ac-nav-count">1</b></button><div class="ac-nav-hint">Codex 当前已接入<br>其他 Agent 等待 Adapter</div></div>`;
    let selected = null, busy = false, disposed = false, detailOpen = false, summaryTaskKey = null;
    const q = id => ctx.view.querySelector('#' + id) || ctx.panel.querySelector('#' + id);
    function write(el, html) { if (el && el.innerHTML !== html) el.innerHTML = html; }
    function currentTask(s) { return (s.owned || []).find(t => ['starting','running','waiting_approval','waiting_input'].includes(t.status)); }
    function renderPanelOverview() {
      ctx.panel.innerHTML = `<div class="ac-studio-nav"><div class="phead"><h2>Agent 工作室</h2><p>多 Agent 总览</p></div><button class="ac-nav-item on" id="ac-nav-overview" type="button">${icon('activity','sm')} <span>全部 Agent</span><b id="ac-nav-count">1</b></button><div class="ac-nav-hint">Codex 当前已接入<br>其他 Agent 等待 Adapter</div></div>`;
    }
    function renderPanelDetail() {
      ctx.panel.innerHTML = `<div class="ac-sidebar"><button class="btn ac-back-panel" id="ac-back-panel" type="button">${icon('arrow-up','xs')} 返回 Agent 总览</button><h2>Codex 任务</h2><p>owned · Vigil 受管</p><div id="ac-tasks"></div><h2>历史观察</h2><p>observed · 只读元数据</p><button class="btn" id="ac-observe">读取历史列表</button><div id="ac-observed"></div></div>`;
    }
    function detailTemplate() {
      return `<div class="ac-detail-page"><header class="ac-detail-header"><div><button class="btn ac-back-inline" id="ac-back" type="button">${icon('arrow-up','xs')} Agent 总览</button><p class="ac-eyebrow">CONNECTED AGENT · CODEX</p><h2>Codex 管理台</h2><p class="ac-muted">这里显示真实 App Server 状态、owned / observed 身份和受保护的原生审批。</p></div><div class="ac-actions"><button class="btn" data-ac-action="connect">连接</button><button class="btn" data-ac-action="reconnect">重新连接</button><button class="btn" data-ac-action="disconnect">断开</button><button class="btn" id="ac-refresh">刷新</button></div></header>
        <section class="ac-card ac-connection"><div><h2>Codex App Server</h2><p id="ac-connection" role="status">正在读取连接状态…</p></div></section>
        <p class="ac-error" id="ac-error" role="alert" hidden></p><p class="ac-warning" id="ac-gap" role="status" hidden></p>
        <section class="ac-card"><h2>待处理事项 <span id="ac-pending-count"></span></h2><p class="ac-muted">仅处理 Vigil 创建的 owned 会话。逐条核对命令或文件变更；批准不会扩展到后续请求。</p><div id="ac-approvals"></div></section>
        <section class="ac-card"><div class="ac-heading"><h2>任务详情</h2><button class="btn" id="ac-interrupt" hidden>中断受管任务</button></div><div id="ac-detail"></div></section>
        <details class="ac-card" id="ac-reasoning-summary" hidden><summary>思考摘要</summary><p class="ac-muted" id="ac-reasoning-note"></p><div id="ac-reasoning-parts"></div></details>
        <section class="ac-card"><div class="ac-heading"><h2>事件历史</h2><span class="ac-muted" id="ac-cursor"></span></div><p class="ac-muted">历史回放不触发通知。仅保留最近 500 条可用事件；P1 服务缓冲区最多 2000 条。</p><ol id="ac-events" class="ac-events" aria-label="任务事件历史"></ol></section>
        <details class="ac-card"><summary>新建受管任务</summary><p class="ac-muted">独立工作目录 · 只读沙箱 · 网络禁用。启动会调用所选 Codex 模型，可能等待审批；此阶段不能答复。</p><form id="ac-create"><label>Codex 模型<select id="ac-model" class="input" required></select></label><label>任务描述<textarea id="ac-prompt" class="input" maxlength="16000" rows="3" required placeholder="描述要交给 Codex 的任务"></textarea></label><button class="btn fill" id="ac-start" type="submit">创建并启动</button><p id="ac-action-result" role="status"></p></form></details>
        <p class="ac-muted">仅限 loopback 上的管理员连接。独立桌面会话的实时状态不可确认，始终显示 unknown。</p></div>`;
    }
    function paintOverview(s) {
      const live = codexOverview(s);
      const agents = [live, ...pendingAgents.map(x => ({...x,status:'pending',statusText:'待接入',current_task:'尚无真实连接',taskCount:null,pending:0,recent_activity:'尚无活动记录',capabilities:['无控制能力']}))];
      write(q('ac-agent-grid'), agents.map(agentCard).join(''));
      const connectedCount = live.status === 'online' ? 1 : 0;
      const offlineCount = live.status === 'disconnected' ? 1 : 0;
      const unknownCount = connectedCount || offlineCount ? 0 : 1;
      q('ac-agent-count').textContent = `${connectedCount} 个在线 · ${offlineCount} 个离线 · ${unknownCount} 个状态待确认 · ${pendingAgents.length} 个待接入`;
      q('ac-nav-count') && (q('ac-nav-count').textContent = String(1 + pendingAgents.length));
      q('ac-overview-updated').textContent = s.updatedAt ? '同步于 ' + fmtTime(s.updatedAt, 'full') : '等待同步…';
      const error = overviewError(s.error || (s.stale && s.ready ? '状态已过期' : ''));
      q('ac-overview-error').hidden = !error; q('ac-overview-error').textContent = error;
      const recent = (s.owned || []).slice().sort((a,b) => (b.last_activity || 0) - (a.last_activity || 0)).slice(0,4);
      write(q('ac-recent-tasks'), recent.map(t => `<div class="ac-recent-item"><span class="ac-state ac-${esc(t.status)}">${esc(labels[t.status] || '未知')}</span><strong>${esc(t.model || 'Codex')}</strong><span>${esc(t.last_error || (t.current_operation ? operations[t.current_operation.type] || '执行中' : '受管任务'))}</span><time>${esc(fmtTime(t.last_activity,'full') || '—')}</time></div>`).join('') || '<p class="ac-muted ac-empty">暂无已创建任务</p>');
    }
    function paintDetail(s) {
      const connected = !s.stale && s.status.connection_status === 'connected';
      q('ac-connection').textContent = `${s.stale ? '服务不可用 · 状态已过期' : ({connected:'已连接',connecting:'连接中',disconnected:'已断开'}[s.status.connection_status] || '未知')} · ${s.updatedAt ? '最近同步 '+fmtTime(s.updatedAt,'full') : '尚未同步'}`;
      q('ac-error').hidden = !s.error && !s.status.last_error; q('ac-error').textContent = overviewError(s.error || s.status.last_error || '');
      q('ac-gap').hidden = !s.notice; q('ac-gap').textContent = s.notice;
      if (!selected) selected = s.owned[0] ? 'owned:'+s.owned[0].id : null;
      const list = (rows, owned) => rows.map(t => { const id = owned ? t.id : t.thread_id, key = (owned?'owned:':'observed:')+id, status = owned && !s.stale ? t.status : 'unknown'; return `<button class="ac-task ${selected===key?'on':''}" data-task="${esc(key)}" aria-pressed="${selected===key}"><strong>${owned?'任务':'历史线程'} ${esc(id.slice(0,8))}</strong>${badge(status)}<span>${owned?esc(t.model || 'Codex'):'历史元数据 · 不代表实时状态'}</span></button>`; }).join('') || '<p class="ac-muted ac-empty">暂无记录</p>';
      write(q('ac-tasks'), list(s.owned,true)); write(q('ac-observed'), list(s.observed,false));
      const task = s.owned.find(t=>'owned:'+t.id===selected) || s.observed.find(t=>'observed:'+t.thread_id===selected), owned = task && task.ownership==='owned';
      const summary = owned && task.reasoning_summary;
      const parts = summary ? (summary.items || []).flatMap(item => (item.parts || []).filter(part => part.text)) : [];
      if (summaryTaskKey !== selected) { q('ac-reasoning-summary').open = false; summaryTaskKey = selected; }
      q('ac-reasoning-summary').hidden = !owned;
      q('ac-reasoning-note').textContent = parts.length
        ? ((connected && task.live && summary.turn_id === task.current_turn && summary.generation === s.status.generation && !['completed','failed','interrupted'].includes(task.status)) ? '当前轮次的思考摘要' : '已保存的最近轮次思考摘要') + (summary.truncated ? ' · 摘要较长，已截断' : '')
        : '此任务暂无思考摘要';
      write(q('ac-reasoning-parts'), parts.map((part, i) => `<section class="ac-reasoning-part"><h3>第 ${i + 1} 段</h3><div class="ac-reasoning-text">${esc(part.text)}</div></section>`).join(''));
      write(q('ac-detail'), task ? `<div class="ac-heading"><strong>${owned?'受管任务 · owned':'历史观察 · observed'}</strong>${badge(owned && !s.stale ? task.status : 'unknown')}</div><dl class="ac-metrics"><div><dt>最近活动</dt><dd>${esc(owned ? fmtTime(task.last_activity,'full') || '—' : '仅历史读取 '+fmtTime(task.history_read_at,'full'))}</dd></div><div><dt>证据来源</dt><dd>${s.stale ? '缓存 · 已过期' : task.live && task.source==='realtime' ? '实时事件' : '历史元数据 · 无实时进度'}</dd></div>${owned?`<div><dt>当前工具操作</dt><dd>${esc(connected && task.live && task.current_operation ? operations[task.current_operation.type] || '工具操作' : '—')}</dd></div><div><dt>工具失败次数</dt><dd>${Number(task.tool_failures)||0}</dd></div>`:''}<div><dt>历史轮次</dt><dd>${Object.keys(task.turns || {}).length}</dd></div></dl>${owned && task.last_error?`<p class="ac-error">${esc(task.last_error)}</p>`:''}` : '<p class="ac-muted ac-empty">从左侧选择任务；连接后可创建受管任务。</p>');
      const waiting = s.approvals.filter(a=>a.state==='pending'&&a.ownership==='owned'&&(a.connection_generation||a.generation)===s.status.generation);
      q('ac-pending-count').textContent = waiting.length ? `(${waiting.length})` : '';
      write(q('ac-approvals'), s.approvals.filter(a=>a.ownership==='owned'&&['pending','expired','responding','responded','resolved'].includes(a.state)).slice().reverse().map(a=>approvalCard(a,s,connected)).join('') || '<p class="ac-muted ac-empty">暂无待处理事项</p>');
      bindApprovalControls();
      const events = selected && owned ? s.events.filter(e=>e.task===task.event_key) : selected ? [] : s.events;
      write(q('ac-events'), events.slice().reverse().map(e=>`<li><time>${esc(fmtTime(e.at,'full'))}</time><div><strong>${esc(e.method)}</strong><span>${esc(e.state ? labels[e.state] || e.state : '')} ${esc(e.type ? operations[e.type] || e.type : '')}${e.exitCode!=null?' · exit '+esc(e.exitCode):''}</span></div><small>${e.source==='history'?'历史元数据':e.delivery==='history'?'历史回放 · 原始实时事件':'实时增量'}</small></li>`).join('') || '<li class="ac-muted">暂无可用事件；observed 仅提供历史元数据。</li>');
      q('ac-cursor').textContent = 'cursor '+s.cursor;
      q('ac-interrupt').hidden = !(owned && connected && task.live && ['starting','running','waiting_approval','waiting_input'].includes(task.status)); q('ac-interrupt').disabled = busy;
      ctx.view.querySelectorAll('[data-ac-action]').forEach(b=>b.disabled=busy||s.stale||(b.dataset.acAction==='disconnect'&&!connected)); q('ac-observe').disabled=busy||!connected;
      const model=q('ac-model'), value=model.value; write(model,(s.status.models||[]).map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join('')); if ([...model.options].some(o=>o.value===value)) model.value=value; else if(s.status.default_model) model.value=s.status.default_model; q('ac-start').disabled=busy||!connected||!model.options.length;
    }
    function paint(s) { paintOverview(s); if (detailOpen) paintDetail(s); }
    async function action(fn) { if(busy||disposed)return; busy=true; paint(controller.state); try{const r=API.pick(await fn());if(!r.ok)throw new Error(overviewError(r._err||r.err)||'操作失败');}catch(e){if(!disposed){q('ac-action-result').textContent=e.message;toast(e.message);}}finally{busy=false;await controller.poll();} }
    function bindApprovalControls() {
      ctx.view.querySelectorAll('[data-ac-respond]').forEach(button=>button.onclick=()=>{const a=controller.state.approvals.find(x=>x.id===button.dataset.approval);if(!a)return;const identity={connection:a.connection,connection_generation:a.connection_generation||a.generation,thread_id:a.thread_id,turn_id:a.turn_id,request_id:a.request_id,item_id:a.item_id};action(()=>API.post('/api/agent-center/approvals/'+encodeURIComponent(a.id)+'/respond',{...identity,action:button.dataset.acRespond}));});
      ctx.view.querySelectorAll('[data-ac-answer]').forEach(form=>form.onsubmit=e=>{e.preventDefault();const a=controller.state.approvals.find(x=>x.id===form.dataset.acAnswer);if(!a)return;const answers={};for(const question of(a.risk?.questions||[])){const chosen=[...form.querySelectorAll(`[name="answer-${CSS.escape(question.id)}"]:checked`)].map(x=>x.value);const other=form.querySelector(`[name="other-${CSS.escape(question.id)}"]`);if(other&&other.value.trim())chosen.push(other.value.trim());answers[question.id]=chosen;}const identity={connection:a.connection,connection_generation:a.connection_generation||a.generation,thread_id:a.thread_id,turn_id:a.turn_id,request_id:a.request_id,item_id:a.item_id};action(()=>API.post('/api/agent-center/approvals/'+encodeURIComponent(a.id)+'/respond',{...identity,action:'answer',answers}));});
    }
    const selectTask=e=>{const b=e.target.closest('[data-task]');if(b){selected=b.dataset.task;paint(controller.state);}};
    const onViewClick=e=>{const card=e.target.closest('[data-ac-agent-card]');if(card){if(card.dataset.acAgentCard==='codex')openCodex();else toast('该 Agent 尚未接入可信 Adapter，当前没有在线状态、任务或控制能力。',4500);}};
    const onViewKey=e=>{if((e.key==='Enter'||e.key===' ')&&e.target.closest('[data-ac-agent-card]')){e.preventDefault();e.target.closest('[data-ac-agent-card]').click();}};
    function openCodex(){if(detailOpen)return;detailOpen=true;q('ac-overview').hidden=true;const shell=q('ac-agent-detail-shell');shell.hidden=false;shell.innerHTML=detailTemplate();renderPanelDetail();ctx.topbar.querySelector('h1').textContent='Codex 管理台';bindDetail();paint(controller.state);}
    function closeDetail(){if(!detailOpen)return;detailOpen=false;q('ac-agent-detail-shell').hidden=true;q('ac-agent-detail-shell').innerHTML='';renderPanelOverview();ctx.topbar.querySelector('h1').textContent='Agent 工作室';paint(controller.state);}
    function bindDetail(){q('ac-back').onclick=closeDetail;q('ac-back-panel').onclick=closeDetail;ctx.view.querySelectorAll('[data-ac-action]').forEach(b=>b.onclick=()=>action(()=>API.post('/api/agent-center/'+b.dataset.acAction,{})));q('ac-refresh').onclick=()=>controller.poll();q('ac-observe').onclick=()=>action(()=>API.get('/api/agent-center/observed'));q('ac-interrupt').onclick=()=>action(()=>API.post('/api/agent-center/tasks/'+encodeURIComponent(selected.slice(6))+'/interrupt',{}));q('ac-create').onsubmit=e=>{e.preventDefault();const text=q('ac-prompt').value.trim(),model=q('ac-model').value;if(!text||!model)return;action(async()=>{const created=API.pick(await API.post('/api/agent-center/tasks',{model}));if(!created.ok)return{ok:false,json:created};selected='owned:'+created.id;const r=await API.post('/api/agent-center/tasks/'+encodeURIComponent(created.id)+'/turns',{text});if(r.ok&&!disposed){q('ac-prompt').value='';q('ac-action-result').textContent='受管任务已启动';}return r;});};}
    q('ac-overview-refresh').onclick=()=>controller.poll();q('ac-add-agent').onclick=()=>toast('通用 Agent Adapter 尚未开放；请先接入可信的状态、任务和权限接口。',4500);ctx.view.addEventListener('click',onViewClick);ctx.view.addEventListener('keydown',onViewKey);ctx.panel.addEventListener('click',selectTask);listeners.add(paint);ctx.onDispose(()=>{disposed=true;listeners.delete(paint);ctx.view.removeEventListener('click',onViewClick);ctx.view.removeEventListener('keydown',onViewKey);ctx.panel.removeEventListener('click',selectTask);});paint(controller.state);start();
    if(!global.vigil){q('ac-overview-error').hidden=false;q('ac-overview-error').textContent=API.runtime.preview?'浏览器预览：Agent Center 需要 Electron preload 和本机管理员连接。':'Electron preload 未加载，Agent Center IPC 不可用。';}
  }

  global.VigilAgentCenter = {start,controller};
  global.PAGES.splice(1,0,{id:'agent-center',name:'Agent Center',icon:'activity',render});
})(window);
