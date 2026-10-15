/* Agent Center sync and optional startup connection. Shared by the page and regression tests. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.AgentCenterState = factory();
})(typeof window === 'object' ? window : globalThis, function () {
  'use strict';
  const prefix = '/api/agent-center/';
  const kinds = { waiting_approval:'approval', waiting_input:'input', failed:'failure' };
  const connectionErrors = {
    codex_cli_missing:'未安装或未找到 Codex CLI，请安装后重新检测连接。',
    codex_cli_unavailable:'无法启动 Codex CLI，请检查安装与 Windows 执行权限。',
    codex_version_failed:'无法识别 Codex CLI 版本，请更新官方 CLI 后重试。',
    codex_detection_timeout:'Codex CLI 检测超时，请在终端检查后重试。',
    codex_login_required:'Codex CLI 尚未登录，请通过官方登录完成认证后连接。',
    codex_auth_check_failed:'官方登录状态检测失败，请运行 codex login status 检查后重试。',
    codex_login_failed:'官方登录未完成或失败，请重试或在终端运行 codex login。',
    codex_login_timeout:'官方登录等待超时，请重新登录。',
    reconnect_exhausted:'自动重连已达 3 次上限，请检查 Codex 后手动重新连接。',
    connect_failed:'受管 App Server 连接失败，请检查 CLI 后重新连接。',
    codex_rpc_error:'Codex App Server 拒绝请求，请检查登录状态及 CLI 版本。',
    rpc_timeout:'Codex App Server 响应超时，请重新连接。',
    transport_closed:'受管 App Server 已断开，正在尝试有限次重连。',
    transport_unavailable:'受管 App Server 连接不可用，请重新连接。',
    explicit_disconnect:'已手动断开；本次运行不会自动重新连接。',
    connection_cancelled:'连接已取消。',
  };
  function explainError(r) {
    const raw = String(r && (r.err || r._err || '') || '');
    if (connectionErrors[raw]) return connectionErrors[raw];
    const status = Number(r && (r.status || r._status) || 0);
    if (status === 404 || /agent_center_route_missing|no such api|not found/i.test(raw))
      return '当前 Brain 版本不支持 Agent Center，请检查版本';
    if (status === 401 || status === 403 || /bad token|permission|admin|forbidden|权限|token/i.test(raw))
      return 'Agent Center 连接被拒绝：请检查 Token 和管理员权限';
    if (/codex.*(not running|unavailable)|agent_center_unavailable|app server.*(未启动|不可用)/i.test(raw))
      return 'Codex App Server 未启动或当前不可用';
    return raw || 'Agent Center 暂时无法连接';
  }
  function create({request, connect, notify = () => {}, changed = () => {}}) {
    const state = { status:{}, owned:[], observed:[], approvals:[], events:[], cursor:0,
      connection:null, ready:false, stale:true, error:'', gapCount:0, notice:'', updatedAt:0 };
    const seen = new Set();
    let pending = null, startupChecked = false;
    async function read(route) {
      const r = await request(prefix + route);
      if (!r || !r.ok) {
        const error = new Error(explainError(r));
        error.code = r && (r.err || r._err) || '';
        error.status = r && (r.status || r._status) || 0;
        throw error;
      }
      return r;
    }
    function emit(event, tasks, status) {
      const kind = kinds[event.state];
      const task = tasks.find(t => t.event_key === event.task);
      if (!kind || event.source !== 'realtime' || event.connection !== status.connection_id ||
          event.generation !== status.generation || !task || task.ownership !== 'owned' ||
          task.connection_generation !== status.generation ||
          event.method === 'turn/late_completion' || event.method === 'request/unbound') return;
      // Status and request notifications for the same wait are one reminder.
      const key = [event.connection,event.generation,event.task,event.turn || task.turn_key,kind].join(':');
      if (seen.has(key)) return;
      seen.add(key); // Full connection lifetime: never evict and re-notify old identities.
      try { Promise.resolve(notify({key,kind,task:task.id})).catch(() => {}); } catch (_) {}
    }
    async function sync() {
      try {
        let status = await read('status'), connectionError = '';
        // Wait for an authenticated Brain response before the one startup attempt.
        // Later polls must respect a user's explicit disconnect, including on failure.
        if (connect && !startupChecked) {
          startupChecked = true;
          if (status.connection_status === 'disconnected') {
            try {
              const result = await connect();
              if (!result || !result.ok) connectionError = explainError(result);
            } catch (e) { connectionError = connectionErrors[e.message] || e.message || 'Codex 自动连接失败'; }
            status = await read('status');
          }
        }
        // Fetch events before snapshots: a task created during polling must be
        // present when its events are attributed. Never advance past an unseen task.
        const log = await read('events?after=' + state.cursor);
        const [tasks, approvals] = await Promise.all([read('tasks'), read('approvals')]);
        let history = !state.ready || status.connection_id !== state.connection;
        let events = log;
        const mismatch = log.connection_id !== status.connection_id;
        if (mismatch) throw new Error('连接正在切换，等待重新同步');
        if (log.gap || history && state.ready) {
          events = await read('events?after=0');
          if (events.connection_id !== status.connection_id) throw new Error('连接正在切换，等待重新同步');
          history = true;
          state.gapCount++;
          state.notice = '事件序列出现缺口或连接已重建，已重新同步可用历史和任务快照。缓冲区之外的早期事件无法恢复。';
        }
        const items = events.items || [];
        if (!history) items.filter(e => e.seq > state.cursor).forEach(e => emit(e, tasks.owned || [], status));
        // Initial hydration / resync never produces desktop notifications.
        const unique = new Map((history ? [] : state.events).map(e => [e.seq,e]));
        items.forEach(e => unique.set(e.seq,{...e,delivery:history ? 'history' : 'incremental'}));
        state.events = Array.from(unique.values()).sort((a,b) => a.seq-b.seq).slice(-500);
        state.status = status; state.owned = tasks.owned || []; state.observed = tasks.observed || [];
        state.approvals = approvals.items || []; state.cursor = events.cursor;
        state.connection = status.connection_id; state.ready = true; state.stale = false;
        state.error = connectionError; state.updatedAt = Date.now();
      } catch (e) { state.error = e.message; state.stale = true; }
      changed(state);
      return state;
    }
    return { state, poll() { if (!pending) pending = sync().finally(() => { pending = null; }); return pending; } };
  }
  return {create,connectionErrors};
});
