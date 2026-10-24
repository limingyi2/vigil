// Real installed Electron + bundled Brain. All profile/model data are fixtures.
const fs=require('fs'),path=require('path'),assert=require('assert/strict'),{spawn,execFileSync}=require('child_process');
const WS=require('ws');
const [install,profile,modelBase,mode='normal',pluginZip]=process.argv.slice(2);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let child,ws,next=0;const waiting=new Map();
async function evaluate(expression){return new Promise((resolve,reject)=>{const id=++next;waiting.set(id,{resolve,reject,expression});ws.send(JSON.stringify({id,method:'Runtime.evaluate',params:{expression,awaitPromise:true,returnByValue:true}}));});}
async function run(){
  const env={...process.env,VIGIL_USER_DATA:profile,VIGIL_BRAIN_EXE:'Z:/old/missing-brain.exe',
    PATH:process.env.SystemRoot+'\\System32',VIGIL_SIDECAR_DISABLE:'1'};
  delete env.ELECTRON_RUN_AS_NODE;delete env.VIGIL_CLOUD_TOKEN;delete env.VIGIL_CLOUD_URL;
  const executable=fs.readdirSync(install).find(name=>/^Kestrel\.exe$/i.test(name))||
    fs.readdirSync(install).find(name=>/^Vigil\.exe$/i.test(name));
  assert.ok(executable,'Installed Electron executable was not found');
  child=spawn(path.join(install,executable),['--remote-debugging-port=0'],{env,windowsHide:true,stdio:'ignore'});
  let port;
  for(let i=0;i<300;i++){
    if(child.exitCode!=null)throw Error('Installed Electron exited '+child.exitCode);
    try{port=Number(fs.readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').split('\n')[0]);const tabs=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();const t=tabs.find(t=>t.type==='page'&&t.url.includes('index.html')&&!t.url.includes('settings'));
      if(t){ws=new WS(t.webSocketDebuggerUrl);await new Promise(r=>ws.once('open',r));break;}
    }catch(_){}await sleep(250);
  }
  assert.ok(ws,'Installed renderer unavailable');
  ws.on('message',raw=>{const r=JSON.parse(raw);const w=waiting.get(r.id);if(!w)return;waiting.delete(r.id);if(r.error||r.result?.exceptionDetails){const detail=r.error?.message||r.result?.exceptionDetails?.text||'unknown renderer error';w.reject(Error(`Installed renderer evaluation failed: ${detail}; expression=${String(w.expression).slice(0,180)}`));}else w.resolve(r.result?.result?.value);});
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
  let ready=false;
  for(let i=0;i<100;i++){
    ready=await evaluate("Boolean(document.querySelector('#newTopicBtn')&&document.querySelector('#sessList')&&document.querySelector('#ta'))");
    if(ready)break;await sleep(100);
  }
  assert.ok(ready,'Installed chat session controls did not render');
  const initialConversation=await evaluate('API.state.chatConversationId');
  assert.equal(initialConversation,'','A new Electron process must open the welcome page without selecting a conversation');
  assert.ok(await evaluate("!document.querySelector('#hero').hidden"),'Installed renderer did not show the welcome page');
  const initialSessions=await evaluate("vigil.api('GET','/api/conversations?src=pc')");
  assert.equal(initialSessions.status,200);
  assert.ok(initialSessions.json.items.some(item=>item.legacy),'Migrated legacy chat history was not shown in the session list');
  const startNewConversation=async()=>{
    const before=await evaluate("vigil.api('GET','/api/conversations?src=pc')");
    assert.equal(before.status,200);
    const beforeIds=before.json.items.map(item=>item.id).sort();
    await evaluate("document.querySelector('#newTopicBtn').click();true");
    let home=false;
    for(let i=0;i<100;i++){
      home=await evaluate("API.state.chatConversationId===''&&!document.querySelector('#hero').hidden&&document.querySelector('#msgs').children.length===0");
      if(home)break;
      await sleep(100);
    }
    assert.ok(home,'New conversation did not show a clean welcome page');
    const after=await evaluate("vigil.api('GET','/api/conversations?src=pc')");
    assert.equal(after.status,200);
    assert.deepEqual(after.json.items.map(item=>item.id).sort(),beforeIds,'Clicking new conversation must not create an empty persisted session');
  };
  const sendUiMessage=async(text,reply)=>{
    await evaluate(`(()=>{const t=document.querySelector('#ta');t.value=${JSON.stringify(text)};t.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#sendBtn').click();return true})()`);
    for(let i=0;i<180;i++){
      const found=await evaluate(`document.querySelector('#msgs')?.innerText.includes(${JSON.stringify(reply)})`);
      if(found){
        const id=await evaluate('API.state.chatConversationId');
        if(id)return id;
      }
      await sleep(100);
    }
    throw Error('Installed chat did not render expected reply: '+reply);
  };
  await startNewConversation();
  const conversationA=await sendUiMessage('A_ONLY','Hello fixture');
  assert.ok(conversationA,'First user message did not create and activate conversation A');
  let titled=false;
  for(let i=0;i<100;i++){
    titled=await evaluate(`document.querySelector('[data-session="${conversationA}"]')?.textContent.includes('A_ONLY')`);
    if(titled)break;await sleep(50);
  }
  assert.ok(titled,'First user message did not create a conversation title');
  await startNewConversation();
  const conversationB=await sendUiMessage('B_ONLY','Hello fixture');
  assert.ok(conversationB,'First user message did not create and activate conversation B');
  await evaluate(`document.querySelector('[data-session="${conversationA}"]').click();true`);
  await sendUiMessage('A_CONTINUE','A_CONTEXT_OK');
  await evaluate(`document.querySelector('[data-session="${conversationB}"]').click();true`);
  await sendUiMessage('B_CONTINUE','B_CONTEXT_OK');
  const providerStatus=await(await fetch(modelBase.replace(/\/v1\/?$/,'/__fixture/status'))).json();
  const contextChecks=providerStatus.conversation_checks||[];
  for(const id of ['A','B']){
    const checks=contextChecks.filter(x=>x.conversation===id&&x.chat_request);
    assert.ok(checks.length&&checks.every(x=>x.ok),
      `Fake Provider chat request saw another session in ${id} context: ${JSON.stringify(contextChecks)}`);
  }
  await evaluate('location.reload();true');
  let restored=false;
  for(let i=0;i<120;i++){
    restored=await evaluate(`typeof API!=='undefined'&&API.state.chatConversationId===${JSON.stringify(conversationB)}&&document.querySelector('#msgs')?.innerText.includes('B_CONTEXT_OK')`);
    if(restored)break;await sleep(100);
  }
  assert.ok(restored,'Installed renderer restart did not restore the selected conversation and its messages');
  console.log('PASS installed chat UI creates, switches, continues and restores isolated A/B conversations');
  await evaluate("window.confirm=()=>{throw Error('native confirm forbidden')};const active=document.querySelector('.session-row.on [data-delete]');if(!active)throw Error('active conversation delete action missing');active.click();true");
  let confirmReady=false;
  for(let i=0;i<50;i++){
    confirmReady=await evaluate("Boolean(document.querySelector('.vigil-confirm [data-choice=cancel]'))");
    if(confirmReady)break;await sleep(50);
  }
  assert.ok(confirmReady,'Installed destructive action did not show the themed in-app confirmation');
  await evaluate("document.querySelector('.vigil-confirm [data-choice=cancel]').click();true");
  assert.ok(await evaluate("document.querySelector('#msgs').innerText.includes('B_CONTEXT_OK')"),
    'Canceling the in-app confirmation changed the conversation');
  await evaluate(`(()=>{window.__fixtureFileInput=null;const original=document.createElement.bind(document);document.createElement=function(tag){const n=original(tag);if(tag==='input')n.click=()=>{window.__fixtureFileInput=n};return n};document.querySelector('#clipBtn').click();const picker=document.querySelector('#plusPop [data-plus="file"]');if(!picker)throw Error('attachment menu item missing');picker.click();document.createElement=original;const file=new File([new Uint8Array([0,0,0,24,102,116,121,112])],'fixture.mp4',{type:'video/mp4'});const files=new DataTransfer();files.items.add(file);if(!window.__fixtureFileInput)throw Error('native file picker was not requested');window.__fixtureFileInput.files=files.files;window.__fixtureFileInput.onchange();true})()`);
  let videoReady=false;
  for(let i=0;i<100;i++){
    videoReady=await evaluate("Boolean(document.querySelector('[data-video-choice=frames]')&&document.querySelector('[data-video-choice=original]'))");
    if(videoReady)break;await sleep(50);
  }
  assert.ok(videoReady,'Installed video attachment did not show inline keyframe/original choices');
  assert.ok(await evaluate("document.querySelector('#attach').textContent.includes('当前模型不支持直接理解完整视频')"),
    'Installed video attachment omitted the unsupported-model explanation');
  await evaluate("document.querySelector('[data-video-choice=original]').click();true");
  assert.ok(await evaluate("document.querySelector('[data-video-choice=original]').classList.contains('on')"),
    'Installed original-video option did not activate');
  await evaluate("document.querySelector('#atClr').click();true");
  assert.ok(await evaluate("document.querySelector('#attach').hidden"),'Installed attachment removal did not clear the video');
  console.log('PASS installed UI keeps destructive confirmation themed and video choices inline without native dialogs');
  const created=await evaluate(`vigil.api('POST','/api/creator/jobs',{kind:'plugin',requirement:'Create a minimal isolated fixture plugin. Do not install.',request_id:'creator_fixture_'+crypto.randomUUID()})`);
  assert.equal(created.status,200);const jid=created.json.job_id;
  const streamed=await evaluate(`(async()=>{const events=[];const off=vigil.onCreatorEvent(e=>{if(e.job_id===${JSON.stringify(jid)})events.push(e);});try{return {result:await vigil.apiStream('/api/creator/jobs/${jid}/events?after=0',null),events};}finally{off();}})()`);
  assert.equal(streamed.result?.ok,true,'Creator Jobs SSE subscription failed: '+JSON.stringify(streamed.result));
  assert.ok(streamed.events.some(e=>e.type==='text_delta'&&e.payload?.text),'Creator Jobs did not stream generated text');
  let job;
  for(let i=0;i<120;i++){const r=await evaluate(`vigil.api('GET','/api/creator/jobs/${jid}')`);job=r.json;if(['failed','succeeded'].includes(job.status))break;await sleep(250);}
  assert.equal(job.status,'succeeded',JSON.stringify({status:job.status,error:job.error?.code}));
  assert.ok(job.draft_id,'Creator Job did not associate its generated draft');
  const draft=await evaluate(`vigil.api('GET','/api/creator/draft?id='+encodeURIComponent(${JSON.stringify(job.draft_id)}))`);
  assert.equal(draft.status,200);assert.equal(draft.json?.draft?.id,job.draft_id);
  assert.ok(draft.json.draft.files.some(f=>f.name==='fixture_plugin.py'&&f.content.includes('NAME = "fixture_plugin"')));

  const failed=await evaluate(`vigil.api('POST','/api/creator/jobs',{kind:'plugin',requirement:'fixture forced failure',request_id:'creator_failed_'+crypto.randomUUID()})`);
  assert.equal(failed.status,200);const failedId=failed.json.job_id;let failure;
  for(let i=0;i<120;i++){const r=await evaluate(`vigil.api('GET','/api/creator/jobs/${failedId}')`);failure=r.json;if(['failed','succeeded'].includes(failure.status))break;await sleep(250);}
  assert.equal(failure.status,'failed');assert.ok(failure.error?.log_id);assert.equal(failure.error?.http_status,503);
  const savedFailures=await evaluate("vigil.api('GET','/api/creator/jobs')");
  assert.ok(savedFailures.json.items.some(item=>item.job_id===failedId&&item.status==='failed'));

  assert.ok(pluginZip&&fs.existsSync(pluginZip),'Isolated smoke plugin archive is missing');
  const pluginB64=fs.readFileSync(pluginZip).toString('base64');
  const installed=await evaluate(`vigil.api('POST','/api/plugins/install',{zip_b64:${JSON.stringify(pluginB64)},source:'windows-acceptance'})`);
  assert.equal(installed.status,200);assert.equal(installed.json?.ok,true,JSON.stringify(installed.json));
  const reloaded=await evaluate("vigil.api('POST','/api/market/lifecycle',{action:'reload',name:'vigil_smoke'})");
  assert.equal(reloaded.status,200);assert.equal(reloaded.json?.ok,true,JSON.stringify(reloaded.json));
  const pluginList=await evaluate("vigil.api('GET','/api/plugins')");
  assert.ok(pluginList.json?.plugins?.some(p=>p.name==='vigil_smoke'));
  const smokeChat=await evaluate(`(async()=>{const events=[];let text='';let final='';const off=vigil.onStreamEvent(e=>{events.push(e.type);if(e.type==='text')text+=e.text||'';if(e.type==='done')final=e.text||'';});try{const r=await vigil.apiStream('/api/chat/stream',{text:'请实际调用 smoke_ping 工具并原样返回执行结果',src:'pc',request_id:'smoke_ping_'+crypto.randomUUID()});return {ok:r.ok,events,reply:text+final};}finally{off();}})()`);
  assert.ok(smokeChat.ok&&smokeChat.events.includes('done'),'Installed plugin chat stream did not complete: '+JSON.stringify(smokeChat));
  assert.equal(smokeChat.reply,'VIGIL_PLUGIN_CALL_OK','Real installed-chat tool result did not reach the Electron renderer');
  if(mode==='uninstall-legacy'){
    const removed=await evaluate("vigil.api('POST','/api/plugins/uninstall',{name:'user_fixture',purge_data:false})");
    assert.equal(removed.status,200);assert.equal(removed.json?.ok,true,JSON.stringify(removed.json));
    assert.ok(!fs.existsSync(path.join(profile,'brain/data/installed_plugins/user_fixture.py')),
      'Plugin Manager left the migrated plugin installed');
  }
  assert.ok(fs.existsSync(path.join(profile,'brain/data')));
  assert.equal(fs.existsSync(path.join(install,'resources/vigil-brain/config')),false);
  console.log('PASS installed pair, migrated data, chat, installed smoke_ping tool call, Creator SSE, saved failure and generated draft');
}
run().then(()=>{if(ws)ws.close();if(child?.pid)execFileSync('taskkill',['/PID',String(child.pid),'/T','/F'],{stdio:'ignore'});}).catch(e=>{console.error(e.stack||e.message);if(ws)ws.close();if(child?.pid){try{execFileSync('taskkill',['/PID',String(child.pid),'/T','/F'],{stdio:'ignore'});}catch(_){}}process.exitCode=1;});
