const test=require('node:test'),assert=require('node:assert/strict');
const {lookup}=require('../src/js/agent-state');
const scope={runId:'run1',requestId:'req1',conversationId:'conv1'};
const item={run_id:'run1',request_id:'req1',conversation_id:'conv1',status:'running'};
for(const status of ['running','paused','paused_budget','succeeded','failed','cancelled','interrupted']){
  test('authoritative '+status+' snapshot keeps request and conversation identity',async()=>{
    const calls=[];
    const result=await lookup(async url=>{calls.push(url);return{ok:true,status:200,json:{...item,status}}},scope);
    assert.equal(result.kind,'snapshot');assert.equal(result.item.status,status);
    assert.match(calls[0],/conversation_id=conv1/);
  });
}
test('missing ledger after restart is rechecked against current conversation without generation',async()=>{
  const calls=[];
  const result=await lookup(async url=>{calls.push(url);return calls.length===1?{ok:false,status:404}:{ok:true,status:200,json:{item:null}}},scope);
  assert.equal(result.kind,'missing');assert.equal(calls.length,2);assert.match(calls[1],/runs\/active/);
});
for(const status of [0,401,403,409,500]){
  test('HTTP '+status+' cannot become fictitious running/success state',async()=>{
    const result=await lookup(async()=>({ok:false,status}),scope);
    assert.equal(result.kind,[401,403].includes(status)?'forbidden':'unavailable');
  });
}
test('stale request or cross-conversation snapshot cannot take over composer',async()=>{
  for(const patch of [{request_id:'old'},{conversation_id:'other'},{run_id:'old'}]){
    const result=await lookup(async()=>({ok:true,status:200,json:{...item,...patch}}),scope);
    assert.equal(result.kind,'unavailable');
  }
});
