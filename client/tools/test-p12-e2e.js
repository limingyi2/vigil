'use strict';
const assert=require('assert/strict'),fs=require('fs'),path=require('path');
let modelGate;
exports.respond=async(req,res,call,wait)=>{
  if(process.env.VIGIL_P12_REAL==='1')return require('./test-p12-real').respond(req,res,call);
  const task=call.messages.findLast(m=>m.role==='user' && String(m.content).includes('P12_'))?.content || '';
  const send=delta=>res.write('data: '+JSON.stringify({choices:[{delta}]})+'\n\n');
  res.writeHead(200,{'content-type':'text/event-stream','cache-control':'no-cache',connection:'close'});
  const finished=call.messages.some(m=>m.role==='tool' || String(m.content).startsWith('工具已执行'));
  const resumed=call.messages.some(m=>String(m.content).includes('断点续跑'));
  send({reasoning_content:'Provider reasoning fixture'});
  if(task.includes('P12_PAUSE') && !resumed) {
    if(!finished) send({tool_calls:[{index:0,id:'pause-tool',type:'function',function:{name:'smoke_ping',arguments:'{"mode":"ok"}'}}]});
    else {send({content:'正在检查文件。'});await new Promise(resolve=>{modelGate=resolve;});if(res.destroyed)return;}
  } else if(finished || resumed || task.includes('P12_CHAT')) {
    send({content:'已完成任务。可以继续发送下一条消息。'});
  } else if(task.includes('P12_XML')) {
    for(const fragment of ['先检查工具。\n','<to','ol_call><fun','ction=smoke_ping><parameter=mode>ok</parameter></function></tool_call>']){
      send({content:fragment});await wait(35);
    }
  } else if(task.includes('P12_NATIVE')) {
    send({tool_calls:[{index:0,id:'native-'+Date.now(),type:'function',function:{name:'smoke_ping',arguments:'{"mode":"crash"}'}}]});
  } else if(task.includes('P12_PAUSE') || task.includes('P12_RESTART')) {
    send({content:'正在检查文件。'});
    await new Promise(resolve=>{modelGate=resolve;});
    if(res.destroyed)return;
    send({content:'检查完成。'});
  } else { send({content:'已完成任务。'}); }
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
  await h.evaluate('document.querySelector("#primaryNavToggle").click();true');
  assert.equal(await h.evaluate('getComputedStyle(document.querySelector("#gnav")).visibility'),'hidden');
  assert.equal(await h.evaluate('document.querySelector("#primaryNavToggle").getAttribute("aria-label")'),'展开一级导航');
  await h.screenshot('navigation-collapsed');
  await h.evaluate('location.reload();true');
  await h.waitFor('document.querySelector("#primaryNavToggle") && window.API?.state.conn.ok','persisted navigation');
  assert.equal(await h.evaluate('document.querySelector("#gnav .gbtn").dataset.page'),'market');
  assert.equal(await h.evaluate('document.body.classList.contains("primary-nav-collapsed")'),true);
  await h.evaluate('document.querySelector("#primaryNavToggle").click();true');
  await h.waitFor('document.querySelector("#ta")','chat restored after nav preference reload');
  await h.screenshot('navigation-expanded');
  await h.evaluate('window.__vigilE2EEvents=[];window.vigil.onStreamEvent(e=>window.__vigilE2EEvents.push(e));true');
  console.log('PASS one Settings entry, drag ordering and hide/show persist across reload');
  const results=[];
  for(const task of ['P12_XML','P12_NATIVE']){
    await h.submit(task);
    if(task==='P12_NATIVE'){
      await h.waitFor('window.__vigilE2EEvents.some(e=>e.type==="tool"&&e.state==="started")','real in-flight tool');
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
    assert.equal(await h.evaluate('document.querySelector("#msgs").textContent.includes("Provider reasoning fixture")'),false);
    assert.equal(await h.evaluate('(()=>{const rows=[...document.querySelectorAll("#msgs .turn:last-child #liveSteps > *")];return rows[0]?.classList.contains("agent-thinking") && rows.findIndex(n=>n.matches("[data-step-key]"))>0})()'),true,'Chinese thinking state precedes actual tool evidence');
    assert.ok(events.some(e=>e.type==='tool'&&e.state==='returned'));
    assert.equal(await h.evaluate('document.querySelector("#sendBtn").getAttribute("aria-busy")'),'false');
    assert.ok(await h.evaluate('document.querySelector("#msgs").textContent.includes("已完成任务")'));
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
  fs.mkdirSync(path.join(h.repo,'reports/p12-evidence'),{recursive:true});
  fs.writeFileSync(path.join(h.repo,'reports/p12-evidence/round1-events.json'),JSON.stringify({raw:h.modelRequests.map(x=>({messages:x.messages,toolNames:x.toolNames})),results},null,2));
  console.log('PASS P12 round1 end-to-end protocol boundaries');
  if(process.env.VIGIL_P12_ROUND==='1')return;
  await h.submit('P12_PAUSE');
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
  await h.submit('P12_RESTART');
  await h.waitFor('document.querySelector("#sendBtn").getAttribute("aria-label")==="暂停任务"','run before Brain restart');
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
  await h.submit('P12_RESTART ledger replacement');
  await h.waitFor('window.__vigilE2EEvents.some(e=>e.type==="agent_run") && document.querySelector("#sendBtn").getAttribute("aria-label")==="暂停任务"','run before ledger replacement');
  const lostId=await h.evaluate('window.__vigilE2EEvents.find(e=>e.type==="agent_run").run_id');
  require('child_process').execFileSync(process.env.VIGIL_TEST_PYTHON,['-c',
    'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("DELETE FROM runs WHERE id=?",(sys.argv[2],)); c.commit()',
    path.join(h.home,'data/agent_runs.sqlite3'),lostId],{windowsHide:true});
  await h.evaluate('document.querySelector("#sendBtn").click();true');
  await h.waitFor('document.querySelector("#sendBtn").getAttribute("aria-label")==="发送消息"','missing task clears stale Pause');
  assert.ok(await h.evaluate('document.querySelector("#msgs").textContent.includes("任务已不存在")'));
  exports.release();
  console.log('PASS authoritative missing ledger record restores Send and reports interruption without replay');
  for(let round=1;round<=10;round++){
    await h.evaluate('window.__vigilE2EEvents=[];true');
    await h.submit('P12_CHAT round '+round);
    await h.waitFor('window.__vigilE2EEvents.some(e=>e.type==="done") && document.querySelector("#sendBtn").getAttribute("aria-label")==="发送消息"','ordinary chat '+round);
  }
  console.log('PASS ten consecutive real streamed chat turns all restore Send');
};
