/* Projection of existing App Server metadata; never consumes full reasoning. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.AgentTimeline = factory();
})(typeof window === 'object' ? window : globalThis, function () {
  'use strict';
  const tools = new Set(['commandExecution','fileChange','mcpToolCall','dynamicToolCall','webSearch']);
  const stateMethods = new Set(['turn/started','turn/completed','thread/status/changed','task/status/changed',
    'item/commandExecution/requestApproval','item/fileChange/requestApproval','item/permissions/requestApproval','item/tool/requestUserInput',
    'execCommandApproval','applyPatchApproval']);
  const summaryMethods = new Set(['item/reasoning/summaryTextDelta','item/reasoning/summaryPartAdded']);
  const key = (event, item = '') => [event.connection,event.generation,event.task,event.turn,item].join(':');
  function build(events, task) {
    if (task && task.ownership !== 'owned') return [];
    const rows = [], summaries = new Map(), starts = new Map(), seen = new Set(), states = new Map();
    const ordered = (events || []).slice().sort((a,b) => a.connection === b.connection
      ? Number(a.seq || 0) - Number(b.seq || 0) : Number(a.at || 0) - Number(b.at || 0));
    for (const event of ordered) {
      const method = event.method || '';
      if (method === 'item/reasoning/textDelta') continue;
      const connectionEvent = method === 'adapter/connected' || method === 'adapter/disconnected';
      if (!connectionEvent && (!task || event.task !== task.event_key)) continue;
      const id = key(event, String(event.seq));
      if (seen.has(id)) continue;
      seen.add(id);
      const base = {id, at:event.at, source:event.source, delivery:event.delivery, generation:event.generation, turn:event.turn};
      if (connectionEvent) {
        rows.push({...base,kind:'connection',state:method === 'adapter/connected' ? 'connected' : 'disconnected'});
      } else if (summaryMethods.has(method) && event.item_key && Number.isInteger(event.summary_index) && event.summary_index >= 0 && event.summary_index < 32) {
        const identity = key(event, event.item_key);
        let row = summaries.get(identity);
        if (!row) {
          row = {...base,id:'summary:'+identity,kind:'summary',itemKey:event.item_key,parts:[],truncated:false};
          summaries.set(identity,row); rows.push(row);
        }
        let part = row.parts.find(x => x.index === event.summary_index);
        if (!part) {part = {index:event.summary_index,text:''};row.parts.push(part);row.parts.sort((a,b)=>a.index-b.index);}
        if (method === 'item/reasoning/summaryTextDelta' && typeof event.summary_delta === 'string') {
          part.text = (part.text + event.summary_delta).slice(0,65536);
        }
        row.truncated ||= !!event.summary_truncated;
      } else if ((method === 'item/started' || method === 'item/completed') && tools.has(event.type)) {
        const identity = key(event, event.item_key || id);
        const started = method === 'item/started';
        const start = event.item_key ? starts.get(identity) : null;
        const duration = Number.isFinite(event.duration_ms) && event.duration_ms >= 0 ? event.duration_ms
          : start && Number.isFinite(start.at) && Number.isFinite(event.at) ? Math.max(0,(event.at-start.at)*1000) : null;
        rows.push({...base,kind:'tool',operationId:identity,type:event.type,phase:started?'started':'completed',
          status:typeof event.status === 'string'?event.status:null,exitCode:Number.isInteger(event.exitCode)?event.exitCode:null,
          durationMs:started?null:duration,missingStart:!started&&!start});
        if (started && event.item_key) starts.set(identity,event);
        else if (event.item_key) starts.delete(identity);
      } else if (stateMethods.has(method)) {
        const stateKey = [event.connection,event.generation,event.task].join(':');
        const previous = event.previous_state || states.get(stateKey);
        const lifecycle = method === 'turn/started' || method === 'turn/completed';
        if (event.state_transition === true || (lifecycle && event.state) || (event.state_transition === undefined && event.state && previous !== event.state)) {
          rows.push({...base,kind:'state',from:previous !== event.state ? previous || null : null,state:event.state || 'unknown'});
        }
        if ((event.state_transition !== false || lifecycle) && event.state) states.set(stateKey,event.state);
      }
    }
    // Snapshot text is authoritative when the event window has lost early chunks.
    const saved = task && task.reasoning_summary;
    if (saved) for (const item of saved.items || []) {
      let row = rows.find(x => x.kind === 'summary' && x.generation === saved.generation &&
        saved.turn_id === task.current_turn && x.turn === task.turn_key && x.itemKey === item.item_key);
      if (!row) {
        row = {id:['saved-summary',task.event_key,saved.generation,saved.turn_id,item.item_key].join(':'),kind:'summary',
          at:null,source:'snapshot',generation:saved.generation,itemKey:item.item_key,parts:[]};
        rows.push(row);
      }
      row.parts = (item.parts || []).filter(x=>typeof x.text === 'string').map(x=>({index:x.summary_index,text:x.text.slice(0,65536)}));
      row.truncated = !!saved.truncated;
    }
    return rows;
  }
  return {build};
});
