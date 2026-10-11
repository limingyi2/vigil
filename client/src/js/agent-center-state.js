/* Pure polling state machine. Shared by the Electron page and local regression tests. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.AgentCenterState = factory();
})(typeof window === 'object' ? window : globalThis, function () {
  'use strict';
  const prefix = '/api/agent-center/';
  const kinds = { waiting_approval:'approval', waiting_input:'input', failed:'failure' };
  function create({request, notify = () => {}, changed = () => {}}) {
    const state = { status:{}, owned:[], observed:[], approvals:[], events:[], cursor:0,
      connection:null, ready:false, stale:true, error:'', gapCount:0, notice:'', updatedAt:0 };
    const seen = new Set();
    let pending = null;
    async function read(route) {
      const r = await request(prefix + route);
      if (!r || !r.ok) throw new Error(r && (r.err || r._err) || 'Agent Center 无响应');
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
        const status = await read('status');
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
        state.error = ''; state.updatedAt = Date.now();
      } catch (e) { state.error = e.message; state.stale = true; }
      changed(state);
      return state;
    }
    return { state, poll() { if (!pending) pending = sync().finally(() => { pending = null; }); return pending; } };
  }
  return {create};
});
