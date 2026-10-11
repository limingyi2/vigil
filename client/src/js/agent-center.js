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
  function render(ctx) {
    ctx.view.classList.add('ac-view'); ctx.topbar.classList.add('ac-topbar');
    ctx.topbar.innerHTML = `<h1>Agent Center</h1><span class="sp"></span><span>本机 Codex · 状态监督</span>`;
    ctx.panel.innerHTML = `<div class="ac-sidebar"><h2>Codex 任务</h2><p>owned · Vigil 受管</p><div id="ac-tasks"></div><h2>历史观察</h2><p>observed · 只读元数据</p><button class="btn" id="ac-observe">读取历史列表</button><div id="ac-observed"></div></div>`;
    ctx.view.innerHTML = `<div class="ac-wrap">
      <section class="ac-card ac-connection"><div><h2>Codex App Server</h2><p id="ac-connection" role="status">正在读取连接状态…</p></div><div class="ac-actions"><button class="btn" data-ac-action="connect">连接</button><button class="btn" data-ac-action="reconnect">重新连接</button><button class="btn" data-ac-action="disconnect">断开</button><button class="btn" id="ac-refresh">刷新</button></div></section>
      <p class="ac-error" id="ac-error" role="alert" hidden></p><p class="ac-warning" id="ac-gap" role="status" hidden></p>
      <section class="ac-card"><h2>待处理事项 <span id="ac-pending-count"></span></h2><p class="ac-muted">仅处理 Vigil 创建的 owned 会话。逐条核对命令或文件变更；批准不会扩展到后续请求。</p><div id="ac-approvals"></div></section>
      <section class="ac-card"><div class="ac-heading"><h2>任务详情</h2><button class="btn" id="ac-interrupt" hidden>中断受管任务</button></div><div id="ac-detail"></div></section>
      <section class="ac-card"><div class="ac-heading"><h2>事件历史</h2><span class="ac-muted" id="ac-cursor"></span></div><p class="ac-muted">历史回放不触发通知。仅保留最近 500 条可用事件；P1 服务缓冲区最多 2000 条。</p><ol id="ac-events" class="ac-events" aria-label="任务事件历史"></ol></section>
      <details class="ac-card"><summary>新建受管任务</summary><p class="ac-muted">独立工作目录 · 只读沙箱 · 网络禁用。启动会调用所选 Codex 模型，可能等待审批；此阶段不能答复。</p><form id="ac-create"><label>Codex 模型<select id="ac-model" class="input" required></select></label><label>任务描述<textarea id="ac-prompt" class="input" maxlength="16000" rows="3" required placeholder="描述要交给 Codex 的任务"></textarea></label><button class="btn fill" id="ac-start" type="submit">创建并启动</button><p id="ac-action-result" role="status"></p></form></details>
      <p class="ac-muted">仅限 loopback 上的管理员连接。独立桌面会话的实时状态不可确认，始终显示 unknown。</p>
    </div>`;
    let selected = null, busy = false, disposed = false;
    const q = id => ctx.view.querySelector('#'+id) || ctx.panel.querySelector('#'+id);
    function paint(s) {
      if (disposed) return;
      const connected = !s.stale && s.status.connection_status === 'connected';
      q('ac-connection').textContent = `${s.stale ? '服务不可用 · 状态已过期' : ({connected:'已连接',connecting:'连接中',disconnected:'已断开'}[s.status.connection_status] || '未知')} · ${s.updatedAt ? '最近同步 '+fmtTime(s.updatedAt,'full') : '尚未同步'}`;
      q('ac-error').hidden = !s.error && !s.status.last_error;
      q('ac-error').textContent = s.error || s.status.last_error || '';
      q('ac-gap').hidden = !s.notice; q('ac-gap').textContent = s.notice;
      if (!selected) selected = s.owned[0] ? 'owned:'+s.owned[0].id : null;
      const list = (rows, owned) => rows.map(t => {
        const id = owned ? t.id : t.thread_id, key = (owned?'owned:':'observed:')+id;
        const status = owned && !s.stale ? t.status : 'unknown';
        return `<button class="ac-task ${selected===key?'on':''}" data-task="${esc(key)}" aria-pressed="${selected===key}"><strong>${owned?'任务':'历史线程'} ${esc(id.slice(0,8))}</strong>${badge(status)}<span>${owned?esc(t.model || 'Codex'):'历史元数据 · 不代表实时状态'}</span></button>`;
      }).join('') || '<p class="ac-muted ac-empty">暂无记录</p>';
      write(q('ac-tasks'),list(s.owned,true)); write(q('ac-observed'),list(s.observed,false));
      const task = s.owned.find(t=>'owned:'+t.id===selected) || s.observed.find(t=>'observed:'+t.thread_id===selected);
      const owned = task && task.ownership==='owned';
      write(q('ac-detail'),task ? `<div class="ac-heading"><strong>${owned?'受管任务 · owned':'历史观察 · observed'}</strong>${badge(owned && !s.stale ? task.status : 'unknown')}</div><dl class="ac-metrics"><div><dt>最近活动</dt><dd>${esc(owned ? fmtTime(task.last_activity,'full') || '—' : '仅历史读取 '+fmtTime(task.history_read_at,'full'))}</dd></div><div><dt>证据来源</dt><dd>${s.stale ? '缓存 · 已过期' : task.live && task.source==='realtime' ? '实时事件' : '历史元数据 · 无实时进度'}</dd></div>${owned?`<div><dt>当前工具操作</dt><dd>${esc(connected && task.live && task.current_operation ? operations[task.current_operation.type] || '工具操作' : '—')}</dd></div><div><dt>工具失败次数</dt><dd>${Number(task.tool_failures)||0}</dd></div>`:''}<div><dt>历史轮次</dt><dd>${Object.keys(task.turns || {}).length}</dd></div></dl>${owned && task.last_error?`<p class="ac-error">${esc(task.last_error)}</p>`:''}` : '<p class="ac-muted ac-empty">从左侧选择任务；连接后可创建受管任务。</p>');
      const waiting = s.approvals.filter(a=>a.state==='pending' && a.ownership==='owned' && (a.connection_generation||a.generation)===s.status.generation);
      q('ac-pending-count').textContent = waiting.length ? `(${waiting.length})` : '';
      write(q('ac-approvals'),s.approvals.filter(a=>a.ownership==='owned'&&['pending','expired','responding','responded','resolved'].includes(a.state)).slice().reverse().map(a=>approvalCard(a,s,connected)).join('') || '<p class="ac-muted ac-empty">暂无待处理事项</p>');
      bindApprovalControls();
      const events = selected && owned ? s.events.filter(e=>e.task===task.event_key) : selected ? [] : s.events;
      write(q('ac-events'),events.slice().reverse().map(e=>`<li><time>${esc(fmtTime(e.at,'full'))}</time><div><strong>${esc(e.method)}</strong><span>${esc(e.state ? labels[e.state] || e.state : '')} ${esc(e.type ? operations[e.type] || e.type : '')}${e.exitCode!=null?' · exit '+esc(e.exitCode):''}</span></div><small>${e.source==='history'?'历史元数据':e.delivery==='history'?'历史回放 · 原始实时事件':'实时增量'}</small></li>`).join('') || '<li class="ac-muted">暂无可用事件；observed 仅提供历史元数据。</li>');
      q('ac-cursor').textContent = 'cursor '+s.cursor;
      q('ac-interrupt').hidden = !(owned && connected && task.live && ['starting','running','waiting_approval','waiting_input'].includes(task.status));
      q('ac-interrupt').disabled = busy;
      ctx.view.querySelectorAll('[data-ac-action]').forEach(b=>b.disabled = busy || s.stale || b.dataset.acAction==='disconnect' && !connected);
      q('ac-observe').disabled = busy || !connected;
      const model = q('ac-model'), value = model.value;
      write(model,(s.status.models || []).map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join(''));
      if ([...model.options].some(o=>o.value===value)) model.value=value;
      else if (s.status.default_model) model.value=s.status.default_model;
      q('ac-start').disabled = busy || !connected || !model.options.length;
    }
    async function action(fn) {
      if (busy || disposed) return;
      busy=true; paint(controller.state);
      try { const r=API.pick(await fn()); if (!r.ok) throw new Error(r._err || r.err || '操作失败'); }
      catch(e) { if (!disposed) { q('ac-action-result').textContent=e.message; toast(e.message); } }
      finally { busy=false; await controller.poll(); }
    }
    function bindApprovalControls() {
      ctx.view.querySelectorAll('[data-ac-respond]').forEach(button=>button.onclick=()=>{
        const a=controller.state.approvals.find(x=>x.id===button.dataset.approval);if(!a)return;
        const identity={connection:a.connection,connection_generation:a.connection_generation||a.generation,thread_id:a.thread_id,turn_id:a.turn_id,request_id:a.request_id,item_id:a.item_id};
        action(()=>API.post('/api/agent-center/approvals/'+encodeURIComponent(a.id)+'/respond',{...identity,action:button.dataset.acRespond}));
      });
      ctx.view.querySelectorAll('[data-ac-answer]').forEach(form=>form.onsubmit=e=>{
        e.preventDefault();const a=controller.state.approvals.find(x=>x.id===form.dataset.acAnswer);if(!a)return;
        const answers={};for(const question of (a.risk?.questions||[])){const chosen=[...form.querySelectorAll(`[name="answer-${CSS.escape(question.id)}"]:checked`)].map(x=>x.value);const other=form.querySelector(`[name="other-${CSS.escape(question.id)}"]`);if(other&&other.value.trim())chosen.push(other.value.trim());answers[question.id]=chosen;}
        const identity={connection:a.connection,connection_generation:a.connection_generation||a.generation,thread_id:a.thread_id,turn_id:a.turn_id,request_id:a.request_id,item_id:a.item_id};
        action(()=>API.post('/api/agent-center/approvals/'+encodeURIComponent(a.id)+'/respond',{...identity,action:'answer',answers}));
      });
    }
    const selectTask = e=>{const b=e.target.closest('[data-task]');if(b){selected=b.dataset.task;paint(controller.state);}};
    ctx.panel.addEventListener('click',selectTask);
    ctx.view.querySelectorAll('[data-ac-action]').forEach(b=>b.onclick=()=>action(()=>API.post('/api/agent-center/'+b.dataset.acAction,{})));
    q('ac-refresh').onclick=()=>controller.poll();
    q('ac-observe').onclick=()=>action(()=>API.get('/api/agent-center/observed'));
    q('ac-interrupt').onclick=()=>action(()=>API.post('/api/agent-center/tasks/'+encodeURIComponent(selected.slice(6))+'/interrupt',{}));
    q('ac-create').onsubmit=e=>{e.preventDefault();const text=q('ac-prompt').value.trim(),model=q('ac-model').value;if(!text || !model)return;
      action(async()=>{const created=API.pick(await API.post('/api/agent-center/tasks',{model}));if(!created.ok)return {ok:false,json:created};
        selected='owned:'+created.id; const r=await API.post('/api/agent-center/tasks/'+encodeURIComponent(created.id)+'/turns',{text});
        if(r.ok && !disposed){q('ac-prompt').value='';q('ac-action-result').textContent='受管任务已启动';}return r;});};
    listeners.add(paint); ctx.onDispose(()=>{disposed=true;listeners.delete(paint);ctx.panel.removeEventListener('click',selectTask);});
    paint(controller.state); start();
    if (!global.vigil) { q('ac-error').hidden=false; q('ac-error').textContent=API.runtime.preview
      ? '浏览器预览：Agent Center 需要 Electron preload 和本机管理员连接。'
      : 'Electron preload 未加载，Agent Center IPC 不可用。'; }
  }
  global.VigilAgentCenter = {start,controller};
  global.PAGES.splice(1,0,{id:'agent-center',name:'Agent Center',icon:'activity',render});
})(window);
