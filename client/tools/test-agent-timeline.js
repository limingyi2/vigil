const {test}=require('node:test');
const assert=require('node:assert/strict');
const {build}=require('../src/js/agent-timeline');
const task={ownership:'owned',event_key:'task-a',current_turn:'turn-raw',turn_key:'turn-a'};
const e=(seq,method,extra={})=>({seq,method,at:seq,connection:'c',generation:'g',task:'task-a',turn:'turn-a',source:'realtime',...extra});

test('timeline follows sequence order and shows status transitions independently of tools',()=>{
  const rows=build([e(3,'turn/completed',{state:'completed',previous_state:'running',state_transition:true}),
    e(2,'item/started',{type:'commandExecution',item_key:'tool',state:'running',state_transition:false}),
    e(1,'turn/started',{at:99,state:'running',previous_state:'starting',state_transition:true})],task);
  assert.deepEqual(rows.map(x=>x.kind),['state','tool','state']);
  assert.equal(rows[0].from,'starting');assert.equal(rows[2].state,'completed');
});
test('summary boundaries group parts and preserve repeated delta text',()=>{
  const rows=build([e(1,'item/reasoning/summaryPartAdded',{item_key:'r',summary_index:0}),
    e(2,'item/reasoning/summaryTextDelta',{item_key:'r',summary_index:0,summary_delta:'same'}),
    e(3,'item/reasoning/summaryTextDelta',{item_key:'r',summary_index:0,summary_delta:'same'}),
    e(4,'item/reasoning/summaryPartAdded',{item_key:'r',summary_index:1}),
    e(5,'item/reasoning/summaryTextDelta',{item_key:'r',summary_index:1,summary_delta:'next'})],task);
  assert.equal(rows.length,1);assert.deepEqual(rows[0].parts,[{index:0,text:'samesame'},{index:1,text:'next'}]);
});
test('duplicate delivered sequences do not append a delta twice',()=>{
  const delta=e(1,'item/reasoning/summaryTextDelta',{item_key:'r',summary_index:0,summary_delta:'once'});
  assert.equal(build([delta,delta],task)[0].parts[0].text,'once');
});
test('snapshot recovers missing early chunks without duplicating a live summary',()=>{
  const saved={...task,reasoning_summary:{turn_id:'turn-raw',generation:'g',items:[{item_key:'r',parts:[{summary_index:0,text:'complete summary'}]}],truncated:true}};
  const rows=build([e(9,'item/reasoning/summaryTextDelta',{item_key:'r',summary_index:0,summary_delta:'tail'})],saved);
  assert.equal(rows.length,1);assert.equal(rows[0].parts[0].text,'complete summary');assert.equal(rows[0].truncated,true);
  const restored=build([],saved);assert.equal(restored[0].source,'snapshot');assert.equal(restored[0].at,null);
});
test('full chain and unrelated raw payloads are never projected',()=>{
  const rows=build([e(1,'item/reasoning/textDelta',{delta:'RAW-CHAIN'}),
    e(2,'item/completed',{type:'reasoning',content:['RAW-CHAIN']}),
    e(3,'item/reasoning/summaryTextDelta',{item_key:'r',summary_index:0,summary_delta:'summary',content:'RAW-CHAIN'})],task);
  assert.equal(rows.length,1);assert.equal(JSON.stringify(rows).includes('RAW-CHAIN'),false);
});
test('concurrent calls pair by item and turn identity, with native duration preferred',()=>{
  const rows=build([e(1,'item/started',{type:'commandExecution',item_key:'a'}),
    e(2,'item/started',{type:'commandExecution',item_key:'b'}),
    e(3,'item/completed',{type:'commandExecution',item_key:'b',status:'completed',duration_ms:25,exitCode:0}),
    e(4,'item/completed',{type:'commandExecution',item_key:'a',status:'failed',exitCode:7})],task);
  assert.equal(rows[1].operationId,rows[2].operationId);assert.equal(rows[0].operationId,rows[3].operationId);
  assert.equal(rows[2].durationMs,25);assert.equal(rows[3].durationMs,3000);
});
test('missing starts and old metadata do not fabricate a tool start or success',()=>{
  const rows=build([e(1,'item/completed',{type:'fileChange'})],task);
  assert.equal(rows.length,1);assert.equal(rows[0].missingStart,true);assert.equal(rows[0].durationMs,null);assert.equal(rows[0].status,null);
});
test('foreign tasks and observed sessions cannot leak into the selected timeline',()=>{
  assert.deepEqual(build([e(1,'turn/started',{task:'foreign',state:'running'})],task),[]);
  assert.deepEqual(build([e(1,'adapter/connected')],{...task,ownership:'observed'}),[]);
});
test('connection and generation isolate tool identity and retain connection lifecycle',()=>{
  const rows=build([e(1,'adapter/connected'),e(2,'item/started',{type:'mcpToolCall',item_key:'a'}),
    e(3,'adapter/disconnected'),e(4,'item/completed',{generation:'other',type:'mcpToolCall',item_key:'a',status:'completed'})],task);
  assert.deepEqual(rows.filter(x=>x.kind==='connection').map(x=>x.state),['connected','disconnected']);
  assert.equal(rows[3].missingStart,true);
});
test('legacy lifecycle metadata remains readable and redundant status rows are omitted',()=>{
  const rows=build([e(1,'turn/started',{state:'running'}),e(2,'thread/status/changed',{state:'running'}),
    e(3,'thread/status/changed',{state:'waiting_approval'}),e(4,'thread/status/changed',{state:'running',state_transition:false})],task);
  assert.deepEqual(rows.map(x=>x.state),['running','waiting_approval']);
});
test('turn started remains visible when the RPC response already updated task state',()=>{
  const rows=build([e(1,'turn/started',{state:'running',previous_state:'running',state_transition:false})],task);
  assert.equal(rows.length,1);assert.equal(rows[0].state,'running');assert.equal(rows[0].from,null);
});
test('legacy approval method names retain their existing waiting status',()=>{
  const rows=build([e(1,'execCommandApproval',{state:'waiting_approval',previous_state:'running',state_transition:true})],task);
  assert.equal(rows[0].from,'running');assert.equal(rows[0].state,'waiting_approval');
});
