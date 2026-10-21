'use strict';
const assert=require('assert/strict'),fs=require('fs'),path=require('path');
let modelGate;
exports.respond=async(req,res,call,wait)=>{
  const task=call.messages.findLast(m=>m.role==='user' && String(m.content).includes('P12_'))?.content || '';
  const send=delta=>res.write('data: '+JSON.stringify({choices:[{delta}]})+'\n\n');
  res.writeHead(200,{'content-type':'text/event-stream','cache-control':'no-cache',connection:'close'});
  const finished=call.messages.some(m=>m.role==='tool' || String(m.content).startsWith('工具已执行'));
  if(finished || task.includes('P12_CHAT')) {
    send({content:'已完成任务。可以继续发送下一条消息。'});
  } else if(task.includes('P12_XML')) {
    for(const fragment of ['先检查工具。\n','<to','ol_call><fun','ction=smoke_ping><parameter=mode>ok</parameter></function></tool_call>']){
      send({content:fragment});await wait(35);
    }
  } else if(task.includes('P12_NATIVE')) {
    send({tool_calls:[{index:0,id:'native-'+Date.now(),type:'function',function:{name:'smoke_ping',arguments:'{"mode":"ok"}'}}]});
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
  const {base,port}=await h.startBrain(h.modelBase,null,{agentEnabled:true});
  await h.startElectron(base);
  await h.waitFor('window.API?.state.conn.ok','Brain connection');
  await h.wait(200);
  const results=[];
  for(const task of ['P12_XML','P12_NATIVE']){
    await h.submit(task);
    await h.waitFor('window.__vigilE2EEvents.some(e=>e.type==="done")','stream completion');
    const events=await h.evaluate('window.__vigilE2EEvents');
    console.log('P12 trace',JSON.stringify(events),JSON.stringify(h.modelRequests.map(c=>c.userText)));
    assert.equal(events.some(e=>['text','done','run_snapshot','error'].includes(e.type)&&/<(?:tool_call|function|parameter)/i.test(e.text||'')),false);
    assert.ok(events.some(e=>e.type==='tool'&&e.state==='started'));
    assert.ok(events.some(e=>e.type==='tool'&&e.state==='returned'));
    assert.equal(await h.evaluate('document.querySelector("#sendBtn").getAttribute("aria-busy")'),'false');
    assert.ok(await h.evaluate('document.querySelector("#msgs").textContent.includes("已完成任务")'));
    results.push({task,events});
    await h.evaluate('window.__vigilE2EEvents=[];true');
    console.log('PASS real Electron + Brain '+task+' streaming, tool execution and final answer');
  }
  fs.mkdirSync(path.join(h.repo,'reports/p12-evidence'),{recursive:true});
  fs.writeFileSync(path.join(h.repo,'reports/p12-evidence/round1-events.json'),JSON.stringify({raw:h.modelRequests.map(x=>({messages:x.messages,toolNames:x.toolNames})),results},null,2));
  console.log('PASS P12 round1 end-to-end protocol boundaries');
};
