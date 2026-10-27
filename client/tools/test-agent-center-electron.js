// Actual Electron renderer + production preload/API handler + actual Brain HTTP routes.
// Only Codex structural events and unrelated page endpoints are local fixtures.
// 页面现为"桌宠 + Codex 连接"形态：受管任务/审批/时间线 UI 已由 pet/ 桌宠替代，
// 这里验收桌宠卡、连接引导、后台通知去重与 401 恢复，且不再出现受管任务请求。
const {app,BrowserWindow,ipcMain,Notification} = require('electron');
const {spawn} = require('child_process');
const fs=require('fs'),path=require('path'),os=require('os'),vm=require('vm');
const http=require('http'),https=require('https'),assert=require('assert/strict');
for(const stream of [process.stdout,process.stderr]) stream.on('error',e=>{if(e.code!=='EPIPE')throw e;});
const root=path.resolve(__dirname,'../..'),client=path.join(root,'client');
const output=process.env.VIGIL_TEST_OUTPUT || process.env.VIGIL_TEST_EVIDENCE_DIR || path.join(root,'docs/agent-center-codex-p2-evidence');fs.mkdirSync(output,{recursive:true});
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'vigil-p2-electron-'));
app.setPath('userData',path.join(tmp,'electron'));app.setAppUserModelId('Vigil.AgentCenter.P2.Test');
let win,fixture,ready,token,requestSeq=0;
const commands=new Map(),requests=[],results=[],errors=[],notifications=[],notificationOutcomes=[];
const trace=[],traceSeen=new Set(),externalLinks=[];
const wait=ms=>new Promise(r=>setTimeout(r,ms));
function result(name,detail){results.push({name,status:'passed',detail});}
function command(op,extra={}){return new Promise((resolve,reject)=>{const id=++requestSeq;const timeout=setTimeout(()=>{commands.delete(id);reject(Error('fixture timeout '+op));},20000);commands.set(id,v=>{clearTimeout(timeout);
  for(const e of v.events || []){const key=e.connection+':'+e.seq;if(e.method!=='fixture/rollover' && !traceSeen.has(key)){trace.push(e);traceSeen.add(key);}}
  resolve(v);});fixture.stdin.write(JSON.stringify({id,op,...extra})+'\n');});}
async function startFixture(){
  fixture=spawn(process.env.VIGIL_TEST_PYTHON || 'python',[path.join(root,'tools/agent_center_p2_fixture.py')],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,VIGIL_HOME:path.join(tmp,'brain'),VIGIL_TEST_START_DISCONNECTED:'1'}});
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
  vm.runInNewContext(apiCode,{ipcMain,URL,readCfg:()=>({base:'http://127.0.0.1:'+ready.port,src:'pc'}),
    sidecar:{ready:()=>false},connectionBase:()=> 'http://127.0.0.1:'+ready.port,
    DEFAULT_BASE:'',CLIENT_SRC:'pc',getToken:async()=>token,
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
  // 桌宠 IPC 用桩实现：验收 preload 通道与页面控件联动，不真启动 PySide6。
  let petState={status:'stopped',pid:null,lastError:''};let petStarts=0,petStops=0;
  const petCounts=()=>({starts:petStarts,stops:petStops});
  global.__petCounts=petCounts;
  const handlers={'cfg:get':()=>({wizard_done:true,roots:[]}), 'cfg:set':()=>({}), 'app:version':()=> 'P2 local fixture',
    'app:instance-id':()=> 'P2 local fixture',
    'wallpaper-extension:state':()=>({ok:true,installed:false,enabled:false,activeVersion:null}),
    'wallpaper-extension:load':()=>({ok:false,installed:false,enabled:false}),
    'shell:open':(_,url)=>{externalLinks.push(url);return{ok:true};},
    'plugins:load':()=>({ok:true}),'auto:connect':()=>({ok:false}), 'sidecar:status':()=>({status:'stopped'}),
    'token:get':()=> '已保存','local:caps':()=>[],'local:defaults':()=>({roots:[],commands:[]}),
    'wallpaper:request':(_,p)=>({ok:true,data:p.resource==='/settings'?{settings:{settingsVersion:7,glassAlpha:78,blur:10,id:''}}:{wallpapers:[]}}),'updater:check':()=>({}),'updater:state':()=>({}),
    'pet:start':()=>{petStarts++;petState={status:'running',pid:4321,lastError:''};return{ok:true,...petState};},
    'pet:stop':()=>{petStops++;petState={status:'stopped',pid:null,lastError:''};return{ok:true,...petState};},
    'pet:status':()=>({...petState})};
  for(const [channel,fn]of Object.entries(handlers))ipcMain.handle(channel,fn);
  win=new BrowserWindow({show:process.env.VIGIL_TEST_SHOW_WINDOW==='1',width:1380,height:880,frame:false,webPreferences:{preload:path.join(client,'electron/preload.js'),sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
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
  assert.equal(await ev('VigilAgentCenter.controller.state.status.connection_status'),'connected');
  assert.equal(requests.filter(r=>r.method==='POST' && r.path==='/api/agent-center/connect').length,1);
  assert.equal(requests.some(r=>r.method==='POST' && r.path==='/api/agent-center/tasks'),false);
  result('startup automatically connects once before opening Agent Center without starting a task');
  await ev(`document.querySelector('#gnav [data-page="agent-center"]').click()`);
  await until(`document.querySelector('#ac-pet-card')`);
  assert.equal(await ev(`document.querySelector('#ac-pet-card .ac-pet-chip').textContent.includes('未运行')`),true);
  assert.equal(await ev(`document.querySelector('#ac-pet-start').disabled`),false);
  assert.equal(await ev(`document.querySelector('#ac-pet-stop').disabled`),true);
  await shot('workspace-overview-light');
  result('overview shows pet control card in stopped state');
  await ev(`document.querySelector('#ac-pet-start').click()`);
  await until(`document.querySelector('#ac-pet-card .ac-pet-chip').textContent.includes('运行中')`);
  assert.equal(global.__petCounts().starts,1);
  assert.equal(await ev(`document.querySelector('#ac-pet-stop').disabled`),false);
  await ev(`document.querySelector('#ac-pet-stop').click()`);
  await until(`document.querySelector('#ac-pet-card .ac-pet-chip').textContent.includes('未运行')`);
  assert.equal(global.__petCounts().stops,1);
  result('pet start and stop flow through the preload IPC and update the status chip');
  for(const entry of [
    {error:'codex_cli_missing',installed:false,version:null,auth_status:'unknown',text:'未安装或未找到'},
    {error:'codex_login_required',auth_status:'logged_out',text:'尚未登录'},
    {error:'reconnect_exhausted',auth_status:'logged_in',text:'3 次上限'},
  ]){
    await command('runtime',entry);await poll();
    assert.equal(await ev(`document.querySelector('#ac-overview-connection').textContent.includes(${JSON.stringify(entry.text)})`),true);
    assert.equal(await ev(`document.querySelector('#ac-overview-connection [data-ac-help="login"]').disabled`),entry.installed===false);
    if(entry.auth_status==='logged_out'){
      await ev(`document.querySelector('#ac-overview-connection [data-ac-help="login"]').click()`);
      await until(`document.querySelector('#ac-overview-connection').textContent.includes('等待官方浏览器登录')`);
      assert.equal(requests.filter(r=>r.method==='POST' && r.path==='/api/agent-center/login').length,1);
      assert.equal(await ev(`document.querySelector('#ac-overview-connection [data-ac-help="connect"]').disabled`),true);
    }
  }
  await ev(`document.querySelector('#ac-overview-connection [data-ac-help="install"]').click();document.querySelector('#ac-overview-connection [data-ac-help="auth"]').click()`);
  await until(`document.querySelector('#ac-overview-connection')`);
  assert.deepEqual(externalLinks,['https://developers.openai.com/codex/cli','https://developers.openai.com/codex/auth']);
  await command('reconnect');await poll();
  result('missing CLI, signed-out and exhausted retry causes plus official install/login links are visible');
  assert.equal(notifications.length,0);assert.equal(await ev('API.runtime.kind'),'electron');
  assert.equal(win.webContents.getLastWebPreferences().sandbox,true);assert.equal(win.webContents.getLastWebPreferences().contextIsolation,true);
  result('actual page, production preload and no notification before events');
  // 审批事件仍走后台通知与 IPC 去重；页面不再渲染审批控件，也不发送答复请求。
  await command('start');await poll();await command('approval');await poll();
  const expected=Notification.isSupported()?1:0;assert.equal(notifications.length,expected);
  await command('approval',{duplicate:true});await poll();assert.equal(notifications.length,expected);
  const dup=await ev(`vigil.agentCenter.notify({kind:'approval',key:VigilAgentCenter.controller.state.connection+':'+VigilAgentCenter.controller.state.status.generation+':'+VigilAgentCenter.controller.state.owned[0].event_key+':'+VigilAgentCenter.controller.state.owned[0].turn_key+':approval'})`);
  if(expected)assert.equal(dup.duplicate,true);
  assert.equal(await ev(`document.querySelector('#ac-pet-card .ac-approval')!==null`),false);
  assert.equal(requests.some(r=>r.method==='POST' && r.path.includes('/approvals')),false);
  await shot('approval-light');result('waiting approval, duplicate event and IPC dedup; no in-page response controls',{nativeSupported:Notification.isSupported()});
  // Supervision and reminders continue away from the page; no detached DOM polling.
  await ev(`document.querySelector('#gnav [data-page="chat"]').click()`);
  await command('start',{turn:'p2-input'});await command('input');await poll();assert.equal(notifications.length,expected*2);
  win.webContents.send('agent-center:open');await until(`document.body.dataset.vigilPage==='agent-center' && document.querySelector('#ac-pet-card')`);
  result('blocking input notification away from page; native open event navigates back');
  token='wrong-fixture-token';await until('VigilAgentCenter.controller.state.stale');
  token=ready.token;await until('!VigilAgentCenter.controller.state.stale');
  result('401 recovery keeps controller healthy without managed-task UI');
  await wait(5100);
  await ev(`document.querySelector('#gnav [data-page="agent-center"]').click()`);
  await until(`document.querySelector('#ac-pet-card')`);
  for(const [width,height]of [[1380,880],[900,600]]){
    win.setSize(width,height);await wait(180);
    for(const theme of ['light','dark']){await ev(`document.documentElement.dataset.theme='${theme}'`);assert.equal(await ev('document.documentElement.scrollWidth>innerWidth'),false);await shot('page-'+theme+'-'+width);}
    result('light/dark layout '+width+'x'+height);
  }
  await ev(`document.body.classList.add('vwp-active')`);await shot('page-wallpaper-material');
  assert.equal(await ev(`getComputedStyle(document.querySelector('.ac-card')).backdropFilter`),'none');
  result('Wallpaper reading material without layered backdrop blur');
  await ev(`document.body.classList.remove('vwp-active')`);
  assert.equal(requests.some(r=>r.method==='POST' && r.path==='/api/agent-center/tasks'),false);
  assert.equal(requests.some(r=>r.path.includes('/approvals') && r.method==='POST'),false);
  const snapshot=await command('snapshot');fs.writeFileSync(path.join(output,'events.json'),JSON.stringify(trace,null,2)+'\n');
  assert.equal(JSON.stringify(snapshot.events).includes('PRIVATE'),false);
  assert.deepEqual(errors,[]);
  result('no renderer/preload errors, no task or approval response requests, sanitized events');
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
