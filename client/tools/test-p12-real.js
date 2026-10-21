// Explicit opt-in only. Credentials remain in this test process, never in artifacts.
'use strict';
const fs=require('fs'),path=require('path'),assert=require('assert/strict'),{execFileSync}=require('child_process');
let active,requests=0;
const observations=[];
function current(){
  if(active)return active;
  const config=path.join(process.env.APPDATA,'vigil-client/brain/config/config.json');
  const script='import sys,json;sys.path.insert(0,sys.argv[1]);import model_hub;print(json.dumps(model_hub.current(json.load(open(sys.argv[2],encoding="utf-8-sig")))))';
  active=JSON.parse(execFileSync(process.env.VIGIL_TEST_PYTHON,['-c',script,path.resolve(__dirname,'../../brain'),config],{encoding:'utf8',windowsHide:true}));
  if(!active.api_key || !active.api_base.startsWith('https://'))throw Error('Configured real model unavailable');
  return active;
}
exports.respond=async(req,res,call)=>{
  const cfg=current();
  const ledger=path.resolve(__dirname,'../../reports/p12-evidence/real-request-budget.json');
  const gate=fs.existsSync(ledger)?JSON.parse(fs.readFileSync(ledger,'utf8')):{used:0,limit:12};
  if(gate.used>=gate.limit){res.writeHead(429);res.end('Controlled acceptance request limit reached');return;}
  gate.used++;fs.writeFileSync(ledger,JSON.stringify(gate));requests++;
  const payload={...call.payload,model:cfg.chat,max_tokens:2048,
    tools:(call.payload.tools || []).filter(t=>['p12_file_find','p12_file_read'].includes(t.function.name))};
  if(!payload.tools.length){delete payload.tools;delete payload.tool_choice;}
  const observation={number:requests,model:cfg.chat,protocol:[],rawEvents:[],status:'pending'};
  observations.push(observation);
  try{
    const response=await fetch(cfg.api_base.replace(/\/$/,'')+'/chat/completions',{
      method:'POST',headers:{Authorization:'Bearer '+cfg.api_key,'Content-Type':'application/json'},
      body:JSON.stringify(payload),signal:AbortSignal.timeout(60000)});
    observation.http=response.status;
    res.writeHead(response.status,{'Content-Type':response.headers.get('content-type') || 'application/json'});
    let buffer='';const decoder=new TextDecoder();
    for await(const chunk of response.body){
      if(!res.destroyed)res.write(chunk);
      buffer+=decoder.decode(chunk,{stream:true});
      let end;
      while((end=buffer.indexOf('\n'))>=0){
        const line=buffer.slice(0,end).trim();buffer=buffer.slice(end+1);
        if(!line.startsWith('data:') || line.includes('[DONE]'))continue;
        try{
          const event=JSON.parse(line.slice(5));observation.rawEvents.push(event);
          const delta=event.choices?.[0]?.delta;
          if(delta?.tool_calls && !observation.protocol.includes('native_fc'))observation.protocol.push('native_fc');
          if(delta?.content?.includes('<tool_call') && !observation.protocol.includes('xml_fc'))observation.protocol.push('xml_fc');
        }catch(error){observation.parseError=true;}
      }
    }
    observation.status=response.ok?'received':'http_error';res.end();
  }catch(error){observation.status='unavailable';observation.error=error.name;if(!res.headersSent)res.writeHead(502);res.end();}
};
exports.run=async h=>{
  const cfg=current(),{base}=await h.startBrain(h.modelBase,null,{agentEnabled:true});
  await h.startElectron(base);await h.waitFor('API.state.conn.ok','real-model Brain connection');
  await h.wait(200);
  const results=[];
  async function finish(name){
    await h.waitFor('window.__vigilE2EEvents.some(e=>["done","error"].includes(e.type))',name,120000);
    const events=await h.evaluate('window.__vigilE2EEvents');
    const terminal=events.findLast(e=>['done','error'].includes(e.type));
    const button=await h.evaluate('({label:document.querySelector("#sendBtn").getAttribute("aria-label"),busy:document.querySelector("#sendBtn").getAttribute("aria-busy")})');
    results.push({name,events,terminal,button});
    assert.equal(terminal.type,'done',JSON.stringify(terminal));assert.equal(button.busy,'false');assert.equal(button.label,'发送消息');
    assert.equal(events.some(e=>['text','done','run_snapshot','error'].includes(e.type)&&/<(?:tool_call|function=|parameter=)/i.test(e.text || '')),false);
    await h.evaluate('window.__vigilE2EEvents=[];true');
    console.log('PASS real model '+name+' SSE='+events.length+' state='+button.label);
  }
  try{
    if(process.env.VIGIL_P12_PAUSE_ONLY!=='1'){
    await h.submit('P12_REAL_SEARCH：这是一项只读验收。必须先调用 p12_file_find 搜索 README*，再调用 p12_file_read 读取找到的 README 文件。读取后继续推理，用两句中文概括该项目。不要调用其他工具，不要写文件。');
    await finish('actual file search/read and post-tool reasoning');
    const tools=results[0].events.filter(e=>e.type==='tool' && e.state==='returned').map(e=>e.tool);
    assert.ok(tools.includes('p12_file_find'));assert.ok(tools.includes('p12_file_read'));
    await h.screenshot('real-model-completed');
    await h.submit('P12_REAL_FOLLOWUP：基于刚才的文件内容，用一句话说项目名称；不要调用工具。');
    await finish('continuous follow-up');
    }
    await h.submit('P12_REAL_PAUSE：请继续使用已读取的项目内容，给出三个简短的改进建议，不要调用工具。');
    await h.waitFor('document.querySelector("#sendBtn").getAttribute("aria-label")==="暂停任务"','real model in flight');
    await h.screenshot('real-model-running');
    await h.evaluate('document.querySelector("#sendBtn").click();true');
    await h.waitFor('document.querySelector("#sendBtn").getAttribute("aria-label").startsWith("继续")','real pause snapshot',15000);
    await h.evaluate('window.__vigilE2EEvents=[];document.querySelector("#sendBtn").click();true');
    await finish('pause and resume');
  }finally{
    const report={model:cfg.chat,provider:cfg.provider,requestLimit:12,requests,observations,results};
    fs.mkdirSync(path.join(h.repo,'reports/p12-evidence'),{recursive:true});
    fs.writeFileSync(path.join(h.repo,'reports/p12-evidence/real-model.json'),JSON.stringify(report,null,2));
  }
};
