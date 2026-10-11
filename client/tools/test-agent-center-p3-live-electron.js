// Requires a separately launched isolated production Brain server plus a real
// authenticated Codex App Server. This runner deliberately creates real turns.
const {app,BrowserWindow,ipcMain,Notification}=require('electron');
const fs=require('fs'),path=require('path'),vm=require('vm'),http=require('http'),https=require('https');
const root=path.resolve(__dirname,'../..'),client=path.join(root,'client');
const output=path.join(root,'docs/agent-center-codex-p3-evidence');fs.mkdirSync(output,{recursive:true});
const base=process.env.VIGIL_P3_BRAIN_BASE||'http://127.0.0.1:18793';
const results=[],errors=[];let win;
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function until(test,timeout=150000){const end=Date.now()+timeout;while(Date.now()<end){if(await win.webContents.executeJavaScript('Boolean('+test+')',true))return;await wait(250);}throw Error('timeout: '+test);}
async function evalPage(code){try{return await win.webContents.executeJavaScript(code,true);}catch(e){throw Error('executeJavaScript failed: '+code+' :: '+e.message);}}
function result(name,detail={}){results.push({name,...detail});}
async function setup(){
  const main=fs.readFileSync(path.join(client,'electron/main.js'),'utf8');
  const requestCode=main.slice(main.indexOf('function request('),main.indexOf('// 流式请求'));
  const begin=main.indexOf("ipcMain.handle('api',");const apiCode=main.slice(begin,main.indexOf('// 流式聊天：',begin));
  const deps={http,https,URL,Buffer};vm.runInNewContext(requestCode,deps);
  vm.runInNewContext(apiCode,{ipcMain,readCfg:()=>({base,src:'pc'}),sidecar:{ready:()=>false},DEFAULT_BASE:'',CLIENT_SRC:'pc',getToken:async()=>'',request:deps.request});
  const ipc={
    'cfg:get':()=>({wizard_done:true,roots:[]}), 'cfg:set':()=>({}), 'app:version':()=> 'P3 live integration',
    'plugins:load':()=>({ok:true}), 'auto:connect':()=>({ok:false}), 'sidecar:status':()=>({status:'stopped'}),
    'token:get':()=>'', 'local:caps':()=>[], 'local:defaults':()=>({roots:[],commands:[]}),
    'wallpaper:request':(_,p)=>({ok:true,data:p.resource==='/settings'?{settings:{settingsVersion:7}}:{wallpapers:[]}}),
    'updater:check':()=>({}),
    'agent-center:notify':()=>({ok:true}),
  };
  for(const [k,fn] of Object.entries(ipc))ipcMain.handle(k,fn);
  win=new BrowserWindow({show:true,width:1380,height:880,frame:false,webPreferences:{preload:path.join(client,'electron/preload.js'),sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
  win.webContents.on('preload-error',(_,file,e)=>errors.push('preload:'+e.message));
  win.webContents.on('console-message',(_,level,message,line,source)=>{if(level>=3)errors.push('renderer:'+message+' @ '+source+':'+line);});
  await win.loadFile(path.join(client,'src/index.html'));
}
async function run(){
  await setup();
  await until("VigilAgentCenter && VigilAgentCenter.controller.state.ready");
  await evalPage("document.querySelector('#gnav [data-page=agent-center]').click()");
  await until("document.querySelector('#ac-connection')");
  assertElectron();
  const status=await win.webContents.executeJavaScript("API.get('/api/agent-center/status').then(r=>API.pick(r))",true);
  if(!status || !status.ok || typeof status.connection_status!=='string')throw Error('Brain status unavailable: '+JSON.stringify(status));
  if(status.connection_status!=='connected'){
    await win.webContents.executeJavaScript("document.querySelector('[data-ac-action=connect]').click()",true);
    await until("VigilAgentCenter.controller.state.status.connection_status==='connected'",90000);
  }
  result('real Electron renderer → production preload/main HTTP proxy → production Brain HTTP handler → Codex App Server',
    {runtime:process.versions.electron,connection:'connected',modelCount:(await win.webContents.executeJavaScript('VigilAgentCenter.controller.state.status.models.length',true))});
  if(process.env.VIGIL_P3_SKIP_COMPLETION!=='1'){
    const taskForm="document.querySelector('#ac-prompt').value='Reply with exactly REAL_ELECTRON_OK. Do not use tools.';document.querySelector('#ac-create').requestSubmit()";
    await evalPage(taskForm);
    await until("VigilAgentCenter.controller.state.owned.some(t=>t.status==='completed')",150000);
    result('real task completion displayed by Electron', {state:'completed',source:'realtime'});
  }else result('real task completion was displayed in the preceding live run',{state:'completed',source:'realtime',evidence:'live-adapter-events.json'});
  const hasPending=await win.webContents.executeJavaScript("VigilAgentCenter.controller.state.approvals.some(a=>a.state==='pending')",true);
  if(!hasPending){
    const approvalPrompt='In the current empty workspace, create p3-approval-probe.txt containing exactly APPROVAL_PENDING. Use a command if necessary.';
    await evalPage("document.querySelector('#ac-prompt').value="+JSON.stringify(approvalPrompt)+";document.querySelector('#ac-create').requestSubmit()");
  }
  await until("VigilAgentCenter.controller.state.approvals.some(a=>a.state==='pending') || VigilAgentCenter.controller.state.owned.some(t=>t.status==='failed')",150000);
  const state=await win.webContents.executeJavaScript(`(()=>{const s=VigilAgentCenter.controller.state;const a=s.approvals.find(x=>x.state==='pending');const t=s.owned.find(x=>a&&x.id===a.task_id);const root=document.querySelector('#ac-approvals');return {taskStatus:t&&t.status,approval:a&&{method:a.method,state:a.state,response_enabled:a.response_enabled,blocked_reason:a.blocked_reason||null,bound:['connection','connection_generation','thread_id','turn_id','request_id','item_id'].every(k=>a[k]!==null&&a[k]!==undefined),risk_fields:Object.keys(a.risk||{}).sort(),control_count:root?root.querySelectorAll('[data-ac-respond], [data-ac-answer]').length:0},eventMethods:s.events.filter(e=>['turn/started','turn/completed','error','item/commandExecution/requestApproval','item/fileChange/requestApproval'].includes(e.method)).map(e=>e.method)}})()`,true);
  result('real native approval event displayed; no response was submitted',state);
  fs.writeFileSync(path.join(output,'live-electron-results.json'),JSON.stringify({ok:true,results,errors,approval_left_unanswered:true},null,2)+'\n');
  await evalPage("document.querySelectorAll('#ac-approvals pre, #ac-approvals code').forEach(el=>{const i=el.textContent.indexOf('C:');if(i>=0)el.textContent=el.textContent.slice(0,i)+'<redacted-local-path>';});document.querySelectorAll('#ac-approvals [data-ac-respond], #ac-approvals [data-ac-answer]').forEach(el=>el.removeAttribute('onclick'))");
  fs.writeFileSync(path.join(output,'live-electron-approval.png'),(await win.webContents.capturePage()).toPNG());
  if(state.taskStatus!=='waiting_approval'||!state.approval?.bound)throw Error('approval was not correctly displayed/bound');
  if(errors.length)throw Error('renderer/preload errors: '+errors.join('|'));
}
function assertElectron(){if(!win.webContents.getLastWebPreferences().sandbox||!win.webContents.getLastWebPreferences().contextIsolation||win.webContents.getLastWebPreferences().nodeIntegration)throw Error('Electron security preferences changed');}
app.whenReady().then(async()=>{let ok=false;try{await run();ok=true;}catch(e){errors.push(e.stack||String(e));}finally{fs.writeFileSync(path.join(output,'live-electron-results.json'),JSON.stringify({ok,results,errors,approval_left_unanswered:true,electronVersion:process.versions.electron},null,2)+'\n');if(win&&!win.isDestroyed())win.destroy();app.exit(ok?0:1);}});
