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
  function render(ctx) {
    ctx.view.classList.add('ac-view'); ctx.topbar.classList.add('ac-topbar');
    ctx.topbar.innerHTML = `<h1>Agent Center</h1><span class="sp"></span><span>本机 Codex · 状态监督</span>`;
    ctx.panel.innerHTML = `<div class="ac-sidebar"><h2>Codex 任务</h2><p>owned · Vigil 受管</p><div id="ac-tasks"></div><h2>历史观察</h2><p>observed · 只读元数据</p><button class="btn" id="ac-observe">读取历史列表</button><div id="ac-observed"></div></div>`;
    ctx.view.innerHTML = `<div class="ac-wrap">
      <section class="ac-card ac-connection"><div><h2>Codex App Server</h2><p id="ac-connection" role="status">正在读取连接状态…</p></div><div class="ac-actions"><button class="btn" data-ac-action="connect">连接</button><button class="btn" data-ac-action="reconnect">重新连接</button><button class="btn" data-ac-action="disconnect">断开</button><button class="btn" id="ac-refresh">刷新</button></div></section>
      <p class="ac-error" id="ac-error" role="alert" hidden></p><p class="ac-warning" id="ac-gap" role="status" hidden></p>
      <section class="ac-card"><h2>待处理事项 <span id="ac-pending-count"></span></h2><p class="ac-muted">审批与输入答复尚未开放；这里只查看和提醒，不会自动批准或拒绝。</p><div id="ac-approvals"></div></section>
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
      const waiting = s.approvals.filter(a=>a.state==='pending' && a.ownership==='owned' && a.generation===s.status.generation);
      q('ac-pending-count').textContent = waiting.length ? `(${waiting.length})` : '';
      write(q('ac-approvals'),waiting.map(a=>`<div class="ac-approval"><strong>${a.method==='item/tool/requestUserInput'?'需要输入':'等待审批'}</strong><span>任务 ${esc(a.task_id.slice(0,8))} · ${esc(fmtTime(a.created_at,'full'))}</span><span class="ac-muted">${s.stale?'状态已过期 · 等待同步':'仅查看 · 答复未开放'}</span></div>`).join('') || '<p class="ac-muted ac-empty">暂无待处理事项</p>');
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
