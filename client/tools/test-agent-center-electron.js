// Actual Electron renderer + production preload/API handler + actual Brain HTTP routes.
// Only Codex structural events and unrelated page endpoints are local fixtures.
const {app,BrowserWindow,ipcMain,Notification} = require('electron');
const {spawn} = require('child_process');
const fs=require('fs'),path=require('path'),os=require('os'),vm=require('vm');
const http=require('http'),https=require('https'),assert=require('assert/strict');
for(const stream of [process.stdout,process.stderr]) stream.on('error',e=>{if(e.code!=='EPIPE')throw e;});
const root=path.resolve(__dirname,'../..'),client=path.join(root,'client');
const output=path.join(root,'docs/agent-center-codex-p2-evidence');fs.mkdirSync(output,{recursive:true});
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'vigil-p2-electron-'));
app.setPath('userData',path.join(tmp,'electron'));app.setAppUserModelId('Vigil.AgentCenter.P2.Test');
let win,fixture,ready,token,requestSeq=0;
const commands=new Map(),requests=[],results=[],errors=[],notifications=[],notificationOutcomes=[];
const trace=[],traceSeen=new Set();
const wait=ms=>new Promise(r=>setTimeout(r,ms));
function result(name,detail){results.push({name,status:'passed',detail});}
function command(op,extra={}){return new Promise((resolve,reject)=>{const id=++requestSeq;const timeout=setTimeout(()=>{commands.delete(id);reject(Error('fixture timeout '+op));},20000);commands.set(id,v=>{clearTimeout(timeout);
  for(const e of v.events || []){const key=e.connection+':'+e.seq;if(e.method!=='fixture/rollover' && !traceSeen.has(key)){trace.push(e);traceSeen.add(key);}}
  resolve(v);});fixture.stdin.write(JSON.stringify({id,op,...extra})+'\n');});}
async function startFixture(){
  fixture=spawn(process.env.VIGIL_TEST_PYTHON || 'python',[path.join(root,'tools/agent_center_p2_fixture.py')],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
  const connection=new Promise((resolve,reject)=>{
    let buf='';fixture.stdout.on('data',chunk=>{buf+=chunk;let i;while((i=buf.indexOf('\n'))>=0){const line=buf.slice(0,i);buf=buf.slice(i+1);if(!line.trim())continue;
      try{const v=JSON.parse(line);if(v.ready)resolve(v);else if(commands.has(v.id)){commands.get(v.id)(v);commands.delete(v.id);}}catch(e){reject(Error('fixture invalid output'));}}});
    fixture.once('error',reject);fixture.once('exit',code=>{if(code)reject(Error('fixture exit '+code));});
  });
  fixture.stderr.on('data',b=>errors.push('fixture: '+b.toString().slice(0,400)));
  ready=await Promise.race([connection,wait(15000).then(()=>{throw Error('fixture startup timeout');})]);token=ready.token;
}
function unrelated(p){
  if(p.startsWith('/api/persona'))return{key:'default',name:'Vigil',templates:[{key:'default',label:'Vigil'}]};
  if(p.startsWith('/api/history'))return{items:[]};
  if(p.startsWith('/api/plugins/frontend'))return{version:1,plugins:[]};
  if(p.startsWith('/api/models'))return{current:{model:'local-fixture',api_key_set:true},models:[],providers:[]};
  if(p.startsWith('/api/stats'))return{turns:0};return{items:[]};
}
async function setup(){
  await startFixture();
  const source=fs.readFileSync(path.join(client,'electron/main.js'),'utf8');
  const requestCode=source.slice(source.indexOf('function request('),source.indexOf('// 流式请求'));
  const begin=source.indexOf("ipcMain.handle('api',");
  const apiCode=source.slice(begin,source.indexOf('// 流式聊天：',begin));
  const deps={http,https,URL,Buffer};vm.runInNewContext(requestCode,deps);
  vm.runInNewContext(apiCode,{ipcMain,readCfg:()=>({base:'http://127.0.0.1:'+ready.port,src:'pc'}),
    sidecar:{ready:()=>false},DEFAULT_BASE:'',CLIENT_SRC:'pc',getToken:async()=>token,
    request:async(method,url,body,auth,timeout)=>{
      const p=new URL(url).pathname;requests.push({method,path:new URL(url).pathname+new URL(url).search,authenticated:auth===ready.token});
      if(p.startsWith('/api/agent-center/'))return deps.request(method,url,body,auth,timeout);
      return{ok:true,status:200,json:unrelated(p)};
    }});
  // Record native API attempts and OS show/failed events without replacing show().
  class RecordedNotification {
    static isSupported(){return Notification.isSupported();}
    constructor(options){const n=new Notification(options);notifications.push(options);
      n.on('show',()=>notificationOutcomes.push('show'));n.on('failed',()=>notificationOutcomes.push('failed'));return n;}
  }
  require('../electron/agent-center-notifications').register({ipcMain,Notification:RecordedNotification,getWindow:()=>win,indexPath:path.join(client,'src/index.html')});
  const handlers={'cfg:get':()=>({wizard_done:true,roots:[]}), 'cfg:set':()=>({}), 'app:version':()=> 'P2 local fixture',
    'plugins:load':()=>({ok:true}),'auto:connect':()=>({ok:false}), 'sidecar:status':()=>({status:'stopped'}),
    'token:get':()=> '已保存','local:caps':()=>[],'local:defaults':()=>({roots:[],commands:[]}),
    'wallpaper:request':(_,p)=>({ok:true,data:p.resource==='/settings'?{settings:{settingsVersion:7,glassAlpha:78,blur:10,id:''}}:{wallpapers:[]}}),'updater:check':()=>({})};
  for(const [channel,fn]of Object.entries(handlers))ipcMain.handle(channel,fn);
  win=new BrowserWindow({show:true,width:1380,height:880,frame:false,webPreferences:{preload:path.join(client,'electron/preload.js'),sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
  win.webContents.on('preload-error',(_,file,e)=>errors.push('preload '+e.message));
  win.webContents.on('console-message',(_,level,message)=>{if(level>=3)errors.push(message);});
  await win.loadFile(path.join(client,'src/index.html'));
}
const ev=s=>win.webContents.executeJavaScript(s,true);
async function until(s,timeout=12000){const end=Date.now()+timeout;while(Date.now()<end){if(await ev('Boolean('+s+')'))return;await wait(80);}throw Error('renderer timeout '+s);}
async function poll(){await ev('VigilAgentCenter.controller.poll()');}
async function shot(name){await wait(150);fs.writeFileSync(path.join(output,name+'.png'),(await win.webContents.capturePage()).toPNG());}
async function run(){
  await setup();
  await until(`document.querySelector('#gnav [data-page="agent-center"]') && VigilAgentCenter.controller.state.ready`);
  await ev(`document.querySelector('#gnav [data-page="agent-center"]').click()`);
  await until(`document.querySelectorAll('#ac-agent-grid .ac-agent-card').length===3`);
  assert.equal(await ev(`document.querySelectorAll('#ac-agent-grid .ac-agent-card.is-live').length`),1);
  assert.equal(await ev(`document.querySelectorAll('#ac-agent-grid .ac-agent-card.is-pending').length`),2);
  assert.equal(await ev(`document.querySelector('#ac-agent-grid .ac-agent-deepseek-harness').textContent.includes('待接入')`),true);
  await shot('workspace-overview-light');
  result('workspace overview shows one live Codex and two honest pending adapters');
  await ev(`document.querySelector('[data-ac-agent-card="codex"]').click()`);
  await until(`document.querySelector('#ac-detail') && document.querySelector('#ac-tasks .ac-task')`);
  assert.equal(notifications.length,0);assert.equal(await ev('API.runtime.kind'),'electron');
  assert.equal(win.webContents.getLastWebPreferences().sandbox,true);assert.equal(win.webContents.getLastWebPreferences().contextIsolation,true);
  result('actual page, production preload and initial historical replay without notification');
  await command('start');await until(`document.querySelector('#ac-detail').textContent.includes('运行中')`);
  await command('tool');await until(`document.querySelector('#ac-detail').textContent.includes('执行命令')`);
  result('automatic polling shows running and current tool type');
  await command('tool_failed');await poll();assert.match(await ev(`document.querySelector('#ac-detail').textContent`),/工具失败次数1/);
  assert.equal(notifications.length,0);result('tool failure count without falsely failing task');
  await command('waiting_status');await poll();await command('approval');await poll();
  await until(`document.querySelector('#ac-approvals').textContent.includes('命令审批')`);
  const expected=Notification.isSupported()?1:0;assert.equal(notifications.length,expected);
  await command('approval',{duplicate:true});await poll();assert.equal(notifications.length,expected);
  const dup=await ev(`vigil.agentCenter.notify({kind:'approval',key:VigilAgentCenter.controller.state.connection+':'+VigilAgentCenter.controller.state.status.generation+':'+VigilAgentCenter.controller.state.owned[0].event_key+':'+VigilAgentCenter.controller.state.owned[0].turn_key+':approval'})`);
  if(expected)assert.equal(dup.duplicate,true);
  assert.equal(await ev(`document.querySelectorAll('#ac-approvals button').length`),0);
  assert.equal(requests.some(r=>r.method==='POST' && r.path.includes('/approvals')),false);
  await shot('approval-light');result('waiting approval, duplicate event and IPC dedup; no response controls',{nativeSupported:Notification.isSupported()});
  await command('disconnect');await poll();assert.match(await ev(`document.querySelector('#ac-connection').textContent`),/已断开/);
  assert.match(await ev(`document.querySelector('#ac-detail').textContent`),/未知/);
  assert.equal(await ev(`document.querySelector('#ac-interrupt').hidden`),true);
  assert.equal(await ev(`getComputedStyle(document.querySelector('#ac-interrupt')).display`),'none');
  await ev(`document.querySelector('[data-ac-action="reconnect"]').click()`);
  await until(`document.querySelector('#ac-connection').textContent.includes('已连接') && !VigilAgentCenter.controller.state.approvals.some(a=>a.state==='pending')`);
  assert.match(await ev(`document.querySelector('#ac-detail').textContent`),/历史元数据/);assert.equal(notifications.length,expected);
  result('disconnect and reconnect expire pending; recovered task unknown/history');
  await ev(`document.querySelector('#ac-observed .ac-task').click()`);
  assert.match(await ev(`document.querySelector('#ac-detail').textContent`),/observed.*未知/);
  assert.equal(await ev(`document.querySelector('#ac-interrupt').hidden`),true);
  assert.equal(await ev(`getComputedStyle(document.querySelector('#ac-interrupt')).display`),'none');
  assert.equal(await ev(`document.querySelector('#ac-detail').textContent.includes('运行中')`),false);
  result('observed inProgress history is unknown with no live progress or control');
  await ev(`document.querySelector('#ac-tasks .ac-task').click()`);
  await command('start',{turn:'p2-completion'});await command('complete');await poll();assert.match(await ev(`document.querySelector('#ac-detail').textContent`),/已完成/);
  result('normal completed task state');
  await command('start',{turn:'p2-failed'});await command('fail');await poll();assert.match(await ev(`document.querySelector('#ac-detail').textContent`),/失败/);
  assert.equal(notifications.length,expected*2);result('clear task failure notification');
  // Supervision and reminders continue away from the page; no detached DOM polling.
  await ev(`document.querySelector('#gnav [data-page="chat"]').click()`);
  await command('start',{turn:'p2-input'});await command('input');await poll();assert.equal(notifications.length,expected*3);
  win.webContents.send('agent-center:open');await until(`document.body.dataset.vigilPage==='agent-center' && document.querySelector('#ac-agent-grid')`);
  await ev(`document.querySelector('[data-ac-agent-card="codex"]').click()`);
  await until(`document.querySelector('#ac-detail')`);
  result('blocking input notification away from page; native open event navigates back');
  await command('gap');await poll();assert.equal(await ev(`document.querySelector('#ac-gap').hidden`),false);
  assert.match(await ev(`document.querySelector('#ac-gap').textContent`),/无法恢复/);assert.equal(notifications.length,expected*3);
  result('cursor gap explicitly warned and resynchronized without historical reminder');
  await command('restart');await poll();assert.equal(await ev('VigilAgentCenter.controller.state.connection'),'p2-restarted-connection');
  result('server connection and cursor reset recovered');
  token='wrong-fixture-token';await poll();assert.equal(await ev('VigilAgentCenter.controller.state.stale'),true);
  assert.equal(await ev(`document.querySelector('#ac-start').disabled`),true);
  token=ready.token;await poll();assert.equal(await ev('VigilAgentCenter.controller.state.stale'),false);
  result('401 remains visible and disables operations; authenticated recovery');
  await wait(5100);
  for(const [width,height]of [[1380,880],[900,600]]){
    win.setSize(width,height);await wait(180);
    for(const theme of ['light','dark']){await ev(`document.documentElement.dataset.theme='${theme}'`);assert.equal(await ev('document.documentElement.scrollWidth>innerWidth'),false);await shot('page-'+theme+'-'+width);}
    result('light/dark layout '+width+'x'+height);
  }
  await ev(`document.body.classList.add('vwp-active')`);await shot('page-wallpaper-material');
  assert.equal(await ev(`getComputedStyle(document.querySelector('.ac-card')).backdropFilter`),'none');
  result('Wallpaper reading material without layered backdrop blur');
  await ev(`document.body.classList.remove('vwp-active');document.querySelector('#ac-create').closest('details').open=true;document.querySelector('#ac-prompt').value='local fixture task';document.querySelector('#ac-create').requestSubmit()`);
  await until(`VigilAgentCenter.controller.state.owned.length===2 && document.querySelector('#ac-action-result').textContent.includes('已启动')`);
  result('create/start uses existing P1 endpoints and current Codex model selector');
  await until(`!document.querySelector('#ac-interrupt').hidden`);await ev(`document.querySelector('#ac-interrupt').click()`);
  await until(`document.querySelector('#ac-detail').textContent.includes('已中断')`);
  result('owned interrupt uses existing guarded endpoint');
  const snapshot=await command('snapshot');fs.writeFileSync(path.join(output,'events.json'),JSON.stringify(trace,null,2)+'\n');
  assert.equal(JSON.stringify(snapshot.events).includes('PRIVATE'),false);
  assert.equal(requests.some(r=>r.path.includes('/approvals') && r.method==='POST'),false);
  assert.deepEqual(errors,[]);
  result('no renderer/preload errors, no approval response, sanitized events');
}
app.whenReady().then(async()=>{
  let ok=false;
  try{await run();ok=true;}catch(e){errors.push(e.stack);}
  finally{
    fs.writeFileSync(path.join(output,'electron-results.json'),JSON.stringify({ok,results,errors,requests,
      nativeNotificationSupported:Notification.isSupported(),nativeAttempts:notifications.length,nativeOutcomes:notificationOutcomes,
      modelApiRequests:0,electronVersion:process.versions.electron},null,2)+'\n');
    if(win && !win.isDestroyed())win.destroy();
    if(fixture && fixture.exitCode===null){fixture.stdin.write(JSON.stringify({id:++requestSeq,op:'quit'})+'\n');fixture.stdin.end();await Promise.race([new Promise(r=>fixture.once('exit',r)),wait(5000)]);if(fixture.exitCode===null)fixture.kill();}
    app.exit(ok?0:1);
  }
});
