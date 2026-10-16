// Real installed Electron + bundled Brain. All profile/model data are fixtures.
const fs=require('fs'),path=require('path'),assert=require('assert/strict'),{spawn,execFileSync}=require('child_process');
const WS=require('ws');
const [install,profile,modelBase,mode='normal']=process.argv.slice(2);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let child,ws,next=0;const waiting=new Map();
async function evaluate(expression){return new Promise((resolve,reject)=>{const id=++next;waiting.set(id,{resolve,reject});ws.send(JSON.stringify({id,method:'Runtime.evaluate',params:{expression,awaitPromise:true,returnByValue:true}}));});}
async function run(){
  const env={...process.env,VIGIL_USER_DATA:profile,VIGIL_BRAIN_EXE:'Z:/old/missing-brain.exe',
    PATH:process.env.SystemRoot+'\\System32',VIGIL_SIDECAR_DISABLE:'1'};
  delete env.ELECTRON_RUN_AS_NODE;delete env.VIGIL_CLOUD_TOKEN;delete env.VIGIL_CLOUD_URL;
  child=spawn(path.join(install,'Vigil.exe'),['--remote-debugging-port=0'],{env,windowsHide:true,stdio:'ignore'});
  let port;
  for(let i=0;i<300;i++){
    if(child.exitCode!=null)throw Error('Installed Electron exited '+child.exitCode);
    try{port=Number(fs.readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').split('\n')[0]);const tabs=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();const t=tabs.find(t=>t.type==='page'&&t.url.includes('index.html')&&!t.url.includes('settings'));
      if(t){ws=new WS(t.webSocketDebuggerUrl);await new Promise(r=>ws.once('open',r));break;}
    }catch(_){}await sleep(250);
  }
  assert.ok(ws,'Installed renderer unavailable');
  ws.on('message',raw=>{const r=JSON.parse(raw);const w=waiting.get(r.id);if(!w)return;waiting.delete(r.id);if(r.error||r.result?.exceptionDetails)w.reject(Error('Installed renderer evaluation failed'));else w.resolve(r.result?.result?.value);});
  let bridge=false;
  for(let i=0;i<100;i++){try{bridge=await evaluate('!!window.vigil?.sidecar');if(bridge)break;}catch(_){}await sleep(100);}
  assert.ok(bridge,'Installed preload unavailable');
  let state;
  for(let i=0;i<480;i++){state=await evaluate('vigil.sidecar.status()');if(['ready','failed'].includes(state.status))break;await sleep(250);}
  if(mode==='mismatch'){
    assert.equal(state.status,'failed');assert.equal(state.base,'');
    const r=await evaluate("vigil.api('GET','/api/creator/jobs')");assert.equal(r.status,0);
    console.log('PASS mismatched installed Brain blocked without stale-port fallback');return;
  }
  assert.equal(state.status,'ready',state.lastErr);assert.ok(state.exe.startsWith(install));assert.equal(state.home,path.join(profile,'brain'));
  const runtime=await evaluate("vigil.api('GET','/api/runtime')");assert.equal(runtime.status,200);assert.equal(runtime.json.pid,state.pid);
  assert.ok(runtime.json.capabilities.includes('creator_jobs')&&runtime.json.capabilities.includes('agent_runs'));
  const unauth=await fetch(state.base+'/api/creator/jobs');assert.equal(unauth.status,401);
  const migratedModel=await evaluate("vigil.api('GET','/api/models/saved')");
  assert.equal(migratedModel.status,200);assert.equal(migratedModel.json.current.provider,'custom');
  assert.equal(migratedModel.json.current.model,'fixture-model');assert.equal(migratedModel.json.current.has_key,true);
  const legacyDraft=await evaluate("vigil.api('GET','/api/creator/draft?id=cr_20261017000100_fixture')");
  assert.equal(legacyDraft.status,200);assert.equal(legacyDraft.json?.draft?.id,'cr_20261017000100_fixture');
  let host;
  for(let i=0;i<30;i++){host=await evaluate("vigil.api('POST','/api/skill/status',{})");if(host.json?.alive)break;await sleep(100);}
  assert.equal(host.json?.alive,true,'Bundled Electron Node Skill Host failed without system Node');
  const applied=await evaluate(`vigil.api('POST','/api/models/apply',{provider:'custom',api_base:${JSON.stringify(modelBase)},api_key:'fixture-model-key',model:'fixture-model'})`);
  assert.equal(applied.status,200);assert.notEqual(applied.json?.ok,false);
  const current=await evaluate("vigil.api('GET','/api/models/saved')");assert.equal(current.json.current.has_key,true);
  const chat=await evaluate(`(async()=>{const events=[];let text='';let final='';const off=vigil.onStreamEvent(e=>{events.push(e.type);if(e.type==='text')text+=e.text||'';if(e.type==='done')final=e.text||'';});try{const r=await vigil.apiStream('/api/chat/stream',{text:'fixture hello',src:'pc',request_id:'request_fixture_'+crypto.randomUUID()});return {ok:r.ok,events,hasReply:(text+final).includes('Hello fixture')};}finally{off();}})()`);
  assert.ok(chat.events.includes('done'),JSON.stringify(chat));
  assert.equal(chat.hasReply,true,'Ordinary chat did not return the fixture model response: '+JSON.stringify(chat));
  const session='unified-install-fixture';
  const created=await evaluate(`vigil.api('POST','/api/creator/jobs',{kind:'plugin',requirement:'Create a minimal isolated fixture plugin. Do not install.',session_id:'${session}',request_id:'creator_fixture_'+crypto.randomUUID()})`);
  assert.equal(created.status,200);const jid=created.json.job_id;
  const streamed=await evaluate(`(async()=>{const events=[];const off=vigil.onCreatorEvent(e=>{if(e.job_id===${JSON.stringify(jid)})events.push(e);});try{return {result:await vigil.apiStream('/api/creator/jobs/${jid}/events?after=0',null),events};}finally{off();}})()`);
  assert.equal(streamed.result?.ok,true,'Creator Jobs SSE subscription failed');
  assert.ok(streamed.events.some(e=>e.type==='text_delta'&&e.payload?.text),'Creator Jobs did not stream generated text');
  let job;
  for(let i=0;i<120;i++){const r=await evaluate(`vigil.api('GET','/api/creator/jobs/${jid}?session_id=${session}')`);job=r.json;if(['failed','succeeded'].includes(job.status))break;await sleep(250);}
  assert.equal(job.status,'succeeded',JSON.stringify({status:job.status,error:job.error?.code}));
  assert.ok(job.draft_id,'Creator Job did not associate its generated draft');
  const draft=await evaluate(`vigil.api('GET','/api/creator/draft?id='+encodeURIComponent(${JSON.stringify(job.draft_id)}))`);
  assert.equal(draft.status,200);assert.equal(draft.json?.draft?.id,job.draft_id);
  assert.ok(draft.json.draft.files.some(f=>f.name==='fixture_plugin.py'&&f.content.includes('NAME = "fixture_plugin"')));

  const failed=await evaluate(`vigil.api('POST','/api/creator/jobs',{kind:'plugin',requirement:'fixture forced failure',session_id:'${session}',request_id:'creator_failed_'+crypto.randomUUID()})`);
  assert.equal(failed.status,200);const failedId=failed.json.job_id;let failure;
  for(let i=0;i<120;i++){const r=await evaluate(`vigil.api('GET','/api/creator/jobs/${failedId}?session_id=${session}')`);failure=r.json;if(['failed','succeeded'].includes(failure.status))break;await sleep(250);}
  assert.equal(failure.status,'failed');assert.ok(failure.error?.log_id);assert.equal(failure.error?.http_status,503);
  const savedFailures=await evaluate("vigil.api('GET','/api/creator/jobs')");
  assert.ok(savedFailures.json.items.some(item=>item.job_id===failedId&&item.status==='failed'));
  assert.ok(fs.existsSync(path.join(profile,'brain/data')));
  assert.equal(fs.existsSync(path.join(install,'resources/vigil-brain/config')),false);
  console.log('PASS installed pair, migrated model and draft, isolated model fixture, chat, Creator SSE, saved failure and generated draft');
}
run().then(()=>{if(ws)ws.close();if(child?.pid)execFileSync('taskkill',['/PID',String(child.pid),'/T','/F'],{stdio:'ignore'});}).catch(e=>{console.error(e.message);if(ws)ws.close();if(child?.pid){try{execFileSync('taskkill',['/PID',String(child.pid),'/T','/F'],{stdio:'ignore'});}catch(_){}}process.exitCode=1;});
