'use strict';
const assert=require('assert/strict'),fs=require('fs'),path=require('path');
let modelGate;
exports.respond=async(req,res,call,wait)=>{
  if(process.env.VIGIL_P12_REAL==='1')return require('./test-p12-real').respond(req,res,call);
  const taskIndex=call.messages.findLastIndex(m=>m.role==='user' && String(m.content).includes('P12_'));
  const task=taskIndex>=0 ? call.messages[taskIndex].content : '';
  const send=delta=>res.write('data: '+JSON.stringify({choices:[{delta}]})+'\n\n');
  res.writeHead(200,{'content-type':'text/event-stream','cache-control':'no-cache',connection:'close'});
  const finished=taskIndex>=0 && call.messages.slice(taskIndex+1).some(m=>m.role==='tool' || String(m.content).startsWith('工具已执行'));
  const resumed=call.messages.some(m=>String(m.content).includes('断点续跑'));
  send({reasoning_content:'Provider reasoning fixture'});
  if(task.includes('P12_PAUSE') && !resumed) {
    if(!finished) send({tool_calls:[{index:0,id:'pause-tool',type:'function',function:{name:'smoke_ping',arguments:'{"mode":"ok"}'}}]});
    else {send({content:'正在检查文件。'});await new Promise(resolve=>{modelGate=resolve;});if(res.destroyed)return;}
  } else if(finished || resumed || task.includes('P12_CHAT')) {
    const result=call.messages.findLast(m=>m.role==='tool')?.content;
    send({content:result || '你好。'});
  } else if(task.includes('P12_XML')) {
    for(const fragment of ['先检查工具。\n','<to','ol_call><fun','ction=smoke_ping><parameter=mode>ok</parameter></function></tool_call>']){
      send({content:fragment});await wait(35);
    }
  } else if(task.includes('P12_NATIVE')) {
    const mode=['3','4','5'].includes(process.env.VIGIL_P12_ROUND) ? 'crash' : 'ok';
    send({tool_calls:[{index:0,id:'native-'+Date.now(),type:'function',function:{name:'smoke_ping',arguments:JSON.stringify({mode})}}]});
  } else if(task.includes('P12_UNKNOWN')) {
    send({tool_calls:[{index:0,id:'unknown-'+Date.now(),type:'function',function:{name:'smoke_ping',arguments:'{"mode":"fail"}'}}]});
  } else if(task.includes('P12_PAUSE') || task.includes('P12_RESTART')) {
    send({content:'正在检查文件。'});
    await new Promise(resolve=>{modelGate=resolve;});
    if(res.destroyed)return;
    send({content:'检查完成。'});
  } else { send({content:'你好。'}); }
  res.write('data: '+JSON.stringify({choices:[{delta:{},finish_reason:'stop'}]})+'\n\n');
  res.end('data: [DONE]\n\n');
};
exports.release=()=>{if(modelGate){modelGate();modelGate=null;}};

exports.run=async h=>{
  if(process.env.VIGIL_P12_REAL==='1')return require('./test-p12-real').run(h);
  const {base,port,pluginTrace}=await h.startBrain(h.modelBase,null,{agentEnabled:true});
  const wsReply=await fetch(base+'/api/workspaces',{method:'POST',headers:{Authorization:'Bearer '+h.token,'Content-Type':'application/json'},
    body:JSON.stringify({src:'pc',path:h.repo,name:'P12 验收工作区'})});
  assert.ok(wsReply.ok);
  await h.startElectron(base);
  await h.waitFor('window.API?.state.conn.ok','Brain connection');
  await h.waitFor('document.querySelector(".session-workspaces .session-group-title")','workspace secondary heading');
  await h.evaluate('document.querySelector("[data-ws-new]").click();true');
  await h.waitFor('API.state.chatConversationId','workspace conversation');
  assert.equal(await h.evaluate('document.querySelectorAll("#settingsBtn,#settingsOpen").length'),1,'exactly one Settings entry');
  await h.evaluate(`(()=>{const a=document.querySelector('#gnav [data-page=market]'),b=document.querySelector('#gnav [data-page=chat]'),dt=new DataTransfer();a.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:dt}));b.dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:dt}));return true})()`);
  assert.equal(await h.evaluate('document.querySelector("#gnav .gbtn").dataset.page'),'market','drag changes primary order');
  // 折叠按钮已下线：一级栏宽度改为拖 #gRail（这里用方向键，避免合成 PointerEvent 拿不到 pointerId）
  assert.equal(await h.evaluate('document.querySelector("#primaryNavToggle")'),null,'collapse toggle is gone');
  assert.equal(await h.evaluate('getComputedStyle(document.documentElement).getPropertyValue("--rail").trim()'),'62px','rail default width');
  await h.evaluate(`(()=>{const g=document.querySelector('#gRail');for(let i=0;i<3;i++)g.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));return true})()`);
  assert.equal(await h.evaluate('getComputedStyle(document.documentElement).getPropertyValue("--rail").trim()'),'98px','arrow keys widen the rail');
  assert.equal(await h.evaluate('localStorage.getItem("vigil.gutter.--rail")'),'98','rail width saved');
  await h.screenshot('navigation-rail-widened');
  await h.evaluate('location.reload();true');
  await h.waitFor('window.API?.state.conn.ok','persisted navigation');
  assert.equal(await h.evaluate('document.querySelector("#gnav .gbtn").dataset.page'),'market');
  assert.equal(await h.evaluate('getComputedStyle(document.documentElement).getPropertyValue("--rail").trim()'),'98px','rail width survives reload');
  await h.evaluate(`(()=>{document.querySelector('#gRail').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));return true})()`);
  assert.equal(await h.evaluate('getComputedStyle(document.documentElement).getPropertyValue("--rail").trim()'),'62px','double click resets the rail');
  await h.waitFor('document.querySelector("#ta")','chat restored after nav preference reload');
  await h.screenshot('navigation-expanded');
  await h.evaluate('window.__vigilE2EEvents=[];window.vigil.onStreamEvent(e=>window.__vigilE2EEvents.push(e));true');
  console.log('PASS one Settings entry, drag ordering, draggable rail width persists across reload');
  const results=[];
  for(const task of ['P12_XML','P12_NATIVE']){
    // The test plugin is intentionally not among ordinary chat's four
    // read-only tools. Make the fixture request an explicit operation so it
    // follows the authorized Agent Run path that exposes plugin schemas.
    await h.submit(task+' 请执行隔离测试工具 smoke_ping');
    await h.waitFor('window.__vigilE2EEvents.some(e=>e.type==="tool"&&e.state==="started")',task+' tool start');
    const startProgress=await h.evaluate('document.querySelector("#msgs .turn:last-child .agent-thinking")?.textContent||""');
    assert.ok(startProgress.includes('正在执行工具操作'),JSON.stringify({task,startProgress}));
    if(task==='P12_NATIVE'){
      if(['4','5'].includes(process.env.VIGIL_P12_ROUND)){
        await h.evaluate('window.p12LiveTurn=document.querySelector("#msgs .turn:last-child");true');
        await h.wait(8500); // Exercise the existing 8-second history poll during a gated tool.
        assert.equal(await h.evaluate('window.p12LiveTurn.isConnected'),true,'history polling must not detach a live stream subscription');
      }
      if(['3','5'].includes(process.env.VIGIL_P12_ROUND)){
        const sameComposer=await h.evaluate('window.p12SameComposer=document.querySelector("#ta");document.querySelector("#gnav [data-page=chat]").click();document.querySelector("#ta")===window.p12SameComposer');
        assert.equal(sameComposer,true,'reselecting Chat must preserve the live subscription');
        await h.screenshot('running-light');
        const cid=await h.evaluate('API.state.chatConversationId');
        await h.evaluate('window.savedComposer=document.querySelector("#ta");document.querySelector("#gnav [data-page=market]").click();true');
        await h.waitFor('document.querySelector("#pmGrid")','primary market');
        await h.screenshot('market-light');
        await h.evaluate('document.querySelector("#gnav [data-page=chat]").click();true');
        assert.equal(await h.evaluate('document.querySelector("#ta")===window.savedComposer'),true);
        assert.equal(await h.evaluate('API.state.chatConversationId'),cid);
        assert.equal(await h.evaluate('document.querySelector("#gnav [data-page=market] .i").getBoundingClientRect().width'),20);
      }
      fs.writeFileSync(path.join(h.root,'release-blocked-plugin'),'release');
    }
    await h.waitFor('window.__vigilE2EEvents.some(e=>e.type==="done")','stream completion');
    const events=await h.evaluate('window.__vigilE2EEvents');
    assert.equal(events.some(e=>['text','done','run_snapshot','error'].includes(e.type)&&/<(?:tool_call|function|parameter)/i.test(e.text||'')),false);
    assert.ok(events.some(e=>e.type==='tool'&&e.state==='started'));
    assert.ok(events.some(e=>e.type==='tool'&&e.state==='started'&&e.purpose),'tool start carries its purpose');
    assert.equal(await h.evaluate('document.querySelector("#msgs").textContent.includes("Provider reasoning fixture")'),false);
    assert.equal(await h.evaluate('(()=>{const rows=[...document.querySelectorAll("#msgs .turn:last-child #liveSteps > *")];return rows[0]?.classList.contains("agent-thinking") && rows.findIndex(n=>n.matches("[data-step-key]"))>0})()'),true,'Chinese thinking state precedes actual tool evidence');
    const collapsedPurpose=await h.evaluate(`(()=>{const card=document.querySelector('#msgs .turn:last-child details.agent-step');const purpose=card?.querySelector('summary .agent-step-summary-purpose');if(!card||!purpose)return null;purpose.scrollIntoView({block:'center'});const r=purpose.getBoundingClientRect(),s=getComputedStyle(purpose);return {open:card.open,width:r.width,height:r.height,display:s.display,visibility:s.visibility,insideSummary:purpose.closest('summary')===card.querySelector('summary'),text:purpose.textContent}})()`);
    assert.ok(collapsedPurpose && !collapsedPurpose.open && collapsedPurpose.insideSummary
      && collapsedPurpose.width>0 && collapsedPurpose.height>0
      && collapsedPurpose.display!=='none' && collapsedPurpose.visibility==='visible',
      'purpose is visibly laid out inside the collapsed summary');
    assert.match(collapsedPurpose.text,/操作目的/);
    assert.equal(await h.evaluate('document.querySelectorAll("#msgs .turn:last-child details.agent-step[data-step-key]").length'),1,
      'tool start and completion update one stable card');
    const expanded=await h.evaluate(`(()=>{const card=document.querySelector('#msgs .turn:last-child details.agent-step');card.open=true;const p=card.querySelector(':scope > .agent-step-purpose');const r=p?.getBoundingClientRect();return {text:p?.textContent,width:r?.width,height:r?.height}})()`);
    assert.ok(expanded.text?.includes('操作目的') && expanded.width>0 && expanded.height>0,
      'expanding the card exposes the full purpose');
    await h.evaluate('document.querySelector("#msgs .turn:last-child details.agent-step").open=false;true');
    assert.ok(await h.evaluate('[...document.querySelectorAll("#msgs .turn:last-child .agent-thinking")].some(n=>n.textContent.includes("任务已完成"))'),'terminal progress reflects actual completion');
    assert.ok(events.some(e=>e.type==='tool'&&e.state==='returned'));
    assert.equal(await h.evaluate('document.querySelector("#sendBtn").getAttribute("aria-busy")'),'false');
    assert.ok(await h.evaluate('document.querySelector("#msgs").textContent.includes("VIGIL_PLUGIN_CALL_OK") || document.querySelector("#msgs").textContent.includes("你好。")'));
    results.push({task,events});
    if(task==='P12_NATIVE' && ['3','5'].includes(process.env.VIGIL_P12_ROUND)){
      assert.equal(await h.evaluate('document.querySelectorAll("[data-step-key]").length'),2,'one evidence row per tool, never duplicates on snapshots');
      await h.screenshot('completed-light');
      await h.evaluate('document.documentElement.dataset.theme="dark";true');
      await h.screenshot('completed-dark');
      await h.evaluate('document.querySelector("#modelBtn").click();true');
      await h.waitFor('document.querySelector("#modelPop")?.hidden===false','model picker');
      assert.equal(await h.evaluate('Boolean(document.querySelector("#mqContextNote"))'),false);
      assert.equal(await h.evaluate('[...document.querySelectorAll(".mqrow .lb")].every(n=>n.children.length===1)'),true);
      await h.screenshot('model-picker-dark');
      await h.evaluate('document.documentElement.dataset.theme="light";true');
      await h.screenshot('model-picker-light');
      await h.evaluate('document.querySelector("#mqClose").click();document.querySelector("#clipBtn").click();true');
      await h.evaluate('document.querySelector("#plusPop [data-cr=plugin]").click();true');
      assert.equal(await h.evaluate('document.querySelector("#creatorBtn").textContent'),'创作：插件');
      assert.equal(await h.evaluate('document.querySelectorAll("#creatorBar,[data-kind]").length'),0,'no duplicate Creator controls below menu');
      await h.screenshot('add-model-menu-light');
      await h.evaluate('document.querySelector("#plusPop [data-cr=off]").click();true');
      await h.evaluate('document.querySelector("#plusClose").click();true');
    }
    await h.evaluate('window.__vigilE2EEvents=[];true');
    console.log('PASS real Electron + Brain '+task+' streaming, tool execution and final answer');
  }
  await h.evaluate('window.__vigilE2EEvents=[];true');
  await h.submit('P12_UNKNOWN 请执行并核对工具返回');
  await h.waitFor('window.__vigilE2EEvents.some(e=>e.type==="tool"&&e.state==="unknown") && window.__vigilE2EEvents.some(e=>e.type==="error")',
    'unknown tool outcome and terminal error');
  const unknownUi=await h.evaluate(`(()=>{const card=document.querySelector('#msgs .turn:last-child details.agent-step');const p=card?.querySelector('summary .agent-step-summary-purpose');return {state:card?.dataset.state,opened:card?.open,summary:card?.querySelector('summary')?.textContent,visible:!!p&&p.getBoundingClientRect().width>0&&getComputedStyle(p).visibility==='visible',progress:document.querySelector('#msgs .turn:last-child .agent-thinking')?.textContent}})()`);
  assert.equal(unknownUi.state,'unknown');
  assert.equal(unknownUi.opened,false,'unknown outcome remains a folded evidence card');
  assert.ok(unknownUi.visible && unknownUi.summary.includes('结果未知') && unknownUi.summary.includes('操作目的'));
  assert.equal(unknownUi.progress,'任务失败','task termination may report failure while the tool card preserves its unknown result');
  assert.equal(await h.evaluate('document.querySelectorAll("#msgs .turn:last-child details.agent-step[data-step-key]").length'),1,
    'unknown result updates the existing tool card instead of duplicating it');
  const unknownEvents=await h.evaluate('window.__vigilE2EEvents.filter(e=>e.type==="tool").map(e=>e.state)');
  results.push({task:'P12_UNKNOWN',toolStates:unknownEvents,ui:unknownUi});
  console.log('PASS P12 round1 end-to-end protocol boundaries');
  console.log('PASS Electron renderer preserves unknown tool outcome without success wording or duplicate cards');
  fs.writeFileSync(path.join(h.evidenceDir,'round1-events.json'),JSON.stringify({raw:h.modelRequests.map(x=>({messages:x.messages,toolNames:x.toolNames})),results},null,2),{flag:'wx'});
  if(process.env.VIGIL_P12_ROUND==='1')return;
  await h.submit('P12_PAUSE 请执行隔离测试工具 smoke_ping');
  await h.waitFor('document.querySelector("#sendBtn").getAttribute("aria-label")==="暂停任务"','active run Pause');
  await h.waitFor('document.querySelector("#msgs").textContent.includes("正在检查文件")','pause after a confirmed tool effect');
  await h.evaluate('document.querySelector("#sendBtn").click();document.querySelector("#sendBtn").click();true');
  await h.waitFor('document.querySelector("#sendBtn").getAttribute("aria-label").startsWith("继续")','pause confirmed and resume available',5000);
  exports.release();
  console.log('PASS pause uses conversation owner and duplicate clicks do not keep the composer busy');
  const confirmedEffects=fs.readFileSync(pluginTrace,'utf8').trim().split('\n').length;
  await h.evaluate('window.__vigilE2EEvents=[];document.querySelector("#sendBtn").click();true');
  await h.waitFor('window.__vigilE2EEvents.some(e=>e.type==="done")','resumed run completion');
  assert.equal(await h.evaluate('document.querySelector("#sendBtn").getAttribute("aria-busy")'),'false');
  assert.equal(fs.readFileSync(pluginTrace,'utf8').trim().split('\n').length,confirmedEffects);
  console.log('PASS pause/resume returns to Send without replaying a confirmed tool');
  await h.evaluate('window.__vigilE2EEvents=[];true');
  await h.submit('P12_RESTART 请执行隔离测试工具 smoke_ping');
  await h.waitFor('document.querySelector("#sendBtn").getAttribute("aria-label")==="暂停任务"','run before Brain restart');
  const requestDeadline=Date.now()+10000;
  while(!h.modelRequests.some(call=>call.userText.includes('P12_RESTART')) && Date.now()<requestDeadline) await h.wait(25);
  assert.ok(h.modelRequests.some(call=>call.userText.includes('P12_RESTART')),'restart test waits until its Provider request is recorded');
  const beforeRestart=h.modelRequests.length;
  await h.stopBrain();
  exports.release();
  await h.startBrain(h.modelBase,port,{agentEnabled:true});
  await h.waitFor('document.querySelector("#sendBtn").getAttribute("aria-label")!=="暂停任务"','Brain restart reconciles actual interrupted snapshot');
  assert.equal(h.modelRequests.length,beforeRestart,'restart recovery must not generate again');
  console.log('PASS Brain process restart keeps durable interrupted status and clears stale Pause');
  const cid=await h.evaluate('API.state.chatConversationId');
  await h.submit('P12_CHAT after restart');
  await h.waitFor('document.querySelector("#sendBtn").getAttribute("aria-busy")==="false"','next message after restart');
  assert.equal(await h.evaluate('API.state.chatConversationId'),cid);
  console.log('PASS next message after task completion/restart stays in the same conversation');
  await h.evaluate('window.__vigilE2EEvents=[];true');
  await h.submit('P12_RESTART ledger replacement 请执行隔离测试工具 smoke_ping');
  await h.waitFor('window.__vigilE2EEvents.some(e=>e.type==="agent_run") && document.querySelector("#sendBtn").getAttribute("aria-label")==="暂停任务"','run before ledger replacement');
  const lostId=await h.evaluate('window.__vigilE2EEvents.find(e=>e.type==="agent_run").run_id');
  await h.evaluate('document.querySelector("#sendBtn").click();true');
  await h.waitFor('document.querySelector("#sendBtn").getAttribute("aria-label").startsWith("继续")','pause before ledger replacement');
  exports.release();
  await h.wait(100);
  const effectsBeforeMissingResume=fs.readFileSync(pluginTrace,'utf8').trim().split('\n').filter(Boolean).length;
  // P17 双库之后 Agent Run 落在主库 data/db/vigil.db；升级前的机器上还是
  // 独立文件 data/agent_runs.sqlite3。两个都试，先存在的那个为准。
  const agentDbCandidates=[
    require('path').join(h.home,'data','db','vigil.db'),
    require('path').join(h.home,'data/agent_runs.sqlite3')];
  const agentDb=agentDbCandidates.find(p=>require('fs').existsSync(p));
  if(!agentDb) throw new Error('no agent run database found under '+h.home);
  require('child_process').execFileSync(process.env.VIGIL_TEST_PYTHON,['-c',
    'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("DELETE FROM runs WHERE id=?",(sys.argv[2],)); c.commit()',
    agentDb,lostId],{windowsHide:true});
  await h.evaluate('document.querySelector("#sendBtn").click();true');
  await h.waitFor('document.querySelector("#sendBtn").getAttribute("aria-label")==="发送消息"','missing task clears stale Pause');
  const missingTaskUi=await h.evaluate('({text:document.querySelector("#msgs .turn:last-child")?.textContent||"",events:window.__vigilE2EEvents})');
  assert.ok(missingTaskUi.text.includes('任务失败') && missingTaskUi.events.some(e=>e.type==='error'),JSON.stringify(missingTaskUi));
  assert.equal(fs.readFileSync(pluginTrace,'utf8').trim().split('\n').filter(Boolean).length,effectsBeforeMissingResume,
    'resuming a missing durable task must not replay the tool side effect');
  exports.release();
  console.log('PASS missing durable task resume fails visibly without replaying the tool side effect');
  for(let round=1;round<=10;round++){
    await h.evaluate('window.__vigilE2EEvents=[];true');
    await h.submit('P12_CHAT round '+round);
    await h.waitFor('window.__vigilE2EEvents.some(e=>e.type==="done") && document.querySelector("#sendBtn").getAttribute("aria-label")==="发送消息"','ordinary chat '+round);
  }
  console.log('PASS ten consecutive real streamed chat turns all restore Send');
};
