const test = require('node:test');
const assert = require('node:assert/strict');
const {create} = require('../src/js/agent-center-state');
function fixture() {
  const f = {status:{ok:true,connection_id:'c1',generation:'g1',connection_status:'connected'},
    tasks:{ok:true,owned:[{id:'task',event_key:'hash',turn_key:'turn',ownership:'owned',source:'realtime',live:true,connection_generation:'g1'}],observed:[]},
    items:[],cursor:0,gap:false,failed:false,notices:[],paths:[]};
  f.controller=create({notify:e=>f.notices.push(e),request:async path=>{
    f.paths.push(path);if(f.failed)return{ok:false,err:'unavailable'};
    if(path.endsWith('/status'))return structuredClone(f.status);
    if(path.endsWith('/tasks'))return structuredClone(f.tasks);
    if(path.endsWith('/approvals'))return{ok:true,items:[]};
    const after=Number(path.split('after=')[1]);
    return {ok:true,items:f.items.filter(e=>e.seq>after),cursor:f.cursor,gap:f.gap && after>0,connection_id:f.status.connection_id};
  }});
  f.add=(state,extra={})=>{f.items.push({seq:++f.cursor,source:'realtime',connection:f.status.connection_id,generation:f.status.generation,task:'hash',turn:'turn',state,method:'thread/status/changed',...extra});};
  return f;
}
test('initial realtime backlog is history, no notification',async()=>{const f=fixture();f.add('waiting_approval');await f.controller.poll();assert.equal(f.notices.length,0);assert.equal(f.controller.state.events[0].delivery,'history');});
test('status and duplicate approval requests notify exactly once',async()=>{const f=fixture();await f.controller.poll();f.add('waiting_approval');f.add('waiting_approval',{method:'item/commandExecution/requestApproval'});await f.controller.poll();await f.controller.poll();assert.equal(f.notices.length,1);assert.ok(f.paths.includes('/api/agent-center/events?after=2'));});
test('clear failure and blocking input are separate reminders',async()=>{const f=fixture();await f.controller.poll();f.add('waiting_input');f.add('failed');await f.controller.poll();assert.deepEqual(f.notices.map(e=>e.kind),['input','failure']);});
test('tool failure without task failure produces no task failure alert',async()=>{const f=fixture();await f.controller.poll();f.add('running',{method:'item/completed',status:'failed',exitCode:1});await f.controller.poll();assert.equal(f.notices.length,0);});
test('history, observed, stale generations and late completions never notify',async()=>{const f=fixture();await f.controller.poll();f.add('failed',{source:'history'});f.add('failed',{generation:'old'});f.add('failed',{task:'observed'});f.add('failed',{method:'turn/late_completion'});await f.controller.poll();assert.equal(f.notices.length,0);});
test('HTTP recovery retains cursor and consumes new events once',async()=>{const f=fixture();await f.controller.poll();f.failed=true;await f.controller.poll();assert.equal(f.controller.state.stale,true);f.add('waiting_approval');f.failed=false;await f.controller.poll();assert.equal(f.controller.state.stale,false);assert.equal(f.notices.length,1);});
test('gap explicitly warns, resyncs and suppresses replay notifications',async()=>{const f=fixture();f.add('running');await f.controller.poll();f.add('failed');f.gap=true;await f.controller.poll();assert.equal(f.notices.length,0);assert.equal(f.controller.state.gapCount,1);assert.match(f.controller.state.notice,/无法恢复/);assert.ok(f.paths.includes('/api/agent-center/events?after=0'));});
test('server restart cursor reset resyncs instead of ignoring new sequence',async()=>{const f=fixture();f.add('running');await f.controller.poll();f.status.connection_id='c2';f.items=[];f.cursor=0;f.add('failed');await f.controller.poll();assert.equal(f.notices.length,0);assert.equal(f.controller.state.cursor,1);assert.equal(f.controller.state.connection,'c2');});
test('new turn and generation have independent reminder identity',async()=>{const f=fixture();await f.controller.poll();f.add('waiting_approval');await f.controller.poll();f.add('waiting_approval',{turn:'turn2'});await f.controller.poll();f.status.generation='g2';f.tasks.owned[0].connection_generation='g2';f.add('waiting_approval');await f.controller.poll();assert.equal(f.notices.length,3);});
test('poll is serialized and permission denial remains visible',async()=>{const f=fixture();f.failed=true;const a=f.controller.poll(),b=f.controller.poll();assert.equal(a,b);await a;assert.equal(f.controller.state.error,'unavailable');assert.equal(f.controller.state.ready,false);});
test('events are read before task snapshot to avoid losing newly created task alerts',async()=>{
  const f=fixture();await f.controller.poll();f.paths=[];f.add('waiting_input');await f.controller.poll();
  assert.ok(f.paths.findIndex(p=>p.includes('/events?'))<f.paths.findIndex(p=>p.endsWith('/tasks')));
  assert.equal(f.notices.length,1);
});
test('missing Agent Center route is reported as a Brain version mismatch',async()=>{
  const c=create({request:async()=>({ok:false,status:404,err:'agent_center_route_missing'})});
  await c.poll();
  assert.equal(c.state.error,'当前 Brain 版本不支持 Agent Center，请检查版本');
  assert.equal(c.state.stale,true);
});
