(function(root) {
  'use strict';
  const stopped = new Set(['succeeded','failed','cancelled','interrupted','paused','paused_budget']);
  async function lookup(get, {runId, conversationId, requestId, src='pc'}) {
    const scope='?src='+encodeURIComponent(src)+'&conversation_id='+encodeURIComponent(conversationId || '');
    const read=async path=>{
      try { return await get(path); }
      catch (_) { return {ok:false,status:0}; }
    };
    const classify=r=>[401,403].includes(r.status)?'forbidden':'unavailable';
    const response=await read('/api/agent/runs/'+encodeURIComponent(runId)+scope);
    if(response.ok && response.json?.ok!==false) {
      const item=response.json;
      if(item?.run_id!==runId || (requestId && item.request_id!==requestId) ||
         (item.conversation_id || '')!==(conversationId || '') ||
         !['queued','running',...stopped].includes(item.status))return {kind:'unavailable'};
      return {kind:'snapshot',item};
    }
    if(response.status!==404)return {kind:classify(response)};
    // A 404 may follow a Brain restart, ledger replacement or stale local row.
    // Recheck the authoritative current conversation; never create a new run.
    const active=await read('/api/agent/runs/active'+scope);
    if(!active.ok || active.json?.ok===false)return {kind:classify(active)};
    if(!Object.hasOwn(active.json || {},'item'))return {kind:'unavailable'};
    const item=active.json.item;
    if(item && (item.conversation_id || '')!==(conversationId || ''))return {kind:'unavailable'};
    if(item?.run_id===runId)return {kind:'snapshot',item};
    return item?{kind:'replaced',item}:{kind:'missing'};
  }
  const api={lookup,stopped};
  root.VigilAgentState=api;
  if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
