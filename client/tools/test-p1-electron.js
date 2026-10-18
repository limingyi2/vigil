// Real production renderer + sandboxed preload; all services use fixtures.
const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');
const assert = require('assert/strict');
const client = path.resolve(__dirname, '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-p1-electron-'));
app.setPath('userData', path.join(tmp, 'userdata'));
let win, mode = 'length', created = 0, streams = 0, offline = false;
const jobs = new Map(), requests = [], results = [];
const conversations = [];
const historyClearCalls = [];
let conversationCounter = 0;
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const ev = async source => {
  try { return await win.webContents.executeJavaScript(source, true); }
  catch (error) { console.error('Renderer evaluation failed:', source, error && error.message || error); throw error; }
};
function traceRenderer(candidate) {
  candidate.webContents.on('console-message', (_event, level, message, line, source) =>
    console.log(`Renderer console [${level}] ${source}:${line}: ${message}`));
  candidate.webContents.on('render-process-gone', (_event, details) =>
    console.error('Renderer process exited:', details));
}
async function until(source) {
  const end = Date.now() + 12000;
  while (Date.now() < end) { if (await ev('Boolean(' + source + ')')) return; await wait(40); }
  throw Error('Timeout: ' + source);
}
async function untilMain(check, label) {
  const end = Date.now() + 12000;
  while (Date.now() < end) { if (check()) return; await wait(40); }
  throw Error('Timeout: ' + (label || 'main state'));
}
function ok(name) { results.push(name); console.log('PASS ' + name); }
function response(method, raw, body) {
  const p = new URL(raw, 'http://fixture').pathname;
  requests.push({method, path:p, body});
  let data = {};
  if (offline && ['/api/history','/api/creator/jobs'].includes(p)) return {ok:false,status:0,err:'fixture offline'};
  if (p === '/api/conversations' && method === 'GET') data = {ok:true,items:conversations};
  if (p === '/api/conversations' && method === 'POST') {
    const suffix=(++conversationCounter).toString(16).padStart(12,'0');
    const item={id:`00000000-0000-4000-8000-${suffix}`,title:'新对话',created_at:Date.now()/1000,updated_at:Date.now()/1000};
    conversations.unshift(item);data={ok:true,item};
  }
  if (p === '/api/persona') data = {name:'Fixture', key:'default', templates:[]};
  if (p === '/api/history') data = {items:[]};
  if (p === '/api/history/clear') {
    const clearedSessions = ['main','welcome',...conversations.map(item=>item.id)];
    historyClearCalls.push({body, src:body && body.src, clearedSessions});
    jobs.clear();
    for (let i = conversations.length - 1; i >= 0; i--) conversations.splice(i, 1);
    data = {ok:true,cleared_sessions:clearedSessions};
  }
  if (p === '/api/display/turns') data = {ok:true, items:[]};
  if (p === '/api/stats') data = {model:'fixture'};
  if (p === '/api/models') data = {current:{model:'fixture'}, models:[],providers:[]};
  if (p === '/api/plugins/frontend') data = {version:1,plugins:[]};
  if (p === '/api/creator/drafts') data = {ok:true,drafts:[]};
  if (p === '/api/creator/jobs' && method === 'POST') {
    const prior = [...jobs.values()].find(j => j.request_id === body.request_id);
    if (prior) data = prior;
    else {
      created++;
      data = {ok:true,job_id:'job_' + created,request_id:body.request_id,kind:body.kind,
        requirement:body.requirement,conversation_id:body.conversation_id||null,
        created_at:Date.now()/1000,status:'running',stage:'generating',event_cursor:0,content:''};
      jobs.set(data.job_id, data);
    }
  } else if (p === '/api/creator/jobs') data = {ok:true,items:[...jobs.values()]};
  else if (p.startsWith('/api/creator/jobs/')) {
    const jid = p.split('/')[4];
    data = jobs.get(jid) || {ok:false};
    if (p.endsWith('/cancel')) Object.assign(data,{status:'cancelled'});
  }
  return {ok:true,status:200,json:data};
}
async function main() {
  ipcMain.handle('api', (_, {method,path:p,body}) => response(method,p,body));
  ipcMain.handle('api:stream', async (event, {path:p,body}) => {
    streams++;
    if (!p.startsWith('/api/creator/jobs/')) {
      event.sender.send('api:stream:event',{type:'text',text:'部分回答'});
      event.sender.send('api:stream:event',{type:'error',msg:'连接中断，结果不完整'});
      return {ok:false};
    }
    const job = jobs.get(p.split('/')[4]);
    const send = (type,payload) => event.sender.send('api:stream:event',{job_id:job.job_id,seq:++job.event_cursor,type,payload});
    if (mode === 'no-native') {
      send('stage',{stage:'generating'});
      await wait(90);
      Object.assign(job,{content:'非流式完整响应',status:'failed',error:{message:'模型没按契约返回 JSON'}});
      send('snapshot',{...job});
      return {ok:true};
    }
    send('think_delta',{text:'可展示思考'});
    await wait(90);
    send('text_delta',{text:'候选正文'});
    job.content = '候选正文';
    await wait(mode === 'cancel' ? 700 : 150);
    if (mode === 'disconnect' && !job.reconnected) { job.reconnected = true; return {ok:false}; }
    if (job.status !== 'cancelled') Object.assign(job,{status:'failed',error:{class:mode,message:mode==='json'?'模型没按契约返回 JSON':'模型输出被截断'}});
    send('snapshot', {...job});
    return {ok:true};
  });
  let cfg = {wizard_done:true,roots:[],base:'http://fixture',device_id:'fixture',src:'pc'};
  for (const [name,value] of Object.entries({
    'app:instance-id':'p1-test-process',
    'app:version':'P1 test','plugins:load':{ok:true},'local:defaults':{roots:[],commands:[],danger:[]},
    'local:caps':[],'sidecar:status':{status:'stopped'},'token:get':'已保存','auto:connect':{ok:false},
    'device:whoami':{device_id:'fixture',base:'http://fixture',token_kind:'brain'},
    'wallpaper:request':{ok:true,items:[]},'updater:check':{}
  })) ipcMain.handle(name,()=>value);
  ipcMain.handle('cfg:get',()=>cfg);
  ipcMain.handle('cfg:set',(_,patch)=>(cfg={...cfg,...patch}));
  win = new BrowserWindow({show:false,width:1300,height:900,webPreferences:{
    preload:path.join(client,'electron/preload.js'),sandbox:true,contextIsolation:true,nodeIntegration:false}});
  traceRenderer(win);
  await win.loadFile(path.join(client,'src/index.html'));
  await until('document.querySelector("#creatorBtn")');
  await ev('document.querySelector("#creatorBtn").click()');
  async function submit(text) {
    await ev(`(()=>{const t=document.querySelector('#ta');t.value=${JSON.stringify(text)};t.dispatchEvent(new Event('input'));document.querySelector('#sendBtn').click()})()`);
  }
  await submit('生成测试插件');
  await until('document.querySelector("#msgs").textContent.includes("模型输出被截断")');
  assert.equal(await ev('document.querySelector("#msgs").textContent.includes("候选正文")'),false);
  assert.equal([...jobs.values()].at(-1).content,'候选正文');
  ok('Creator truncation is diagnosed in the task card; partial text stays in the job record');
  mode = 'json'; await submit('生成格式错误插件');
  await until('document.querySelector("#msgs").textContent.includes("没按契约返回 JSON")');
  ok('Creator JSON error is visible through production preload');
  mode = 'cancel'; await submit('取消测试');
  await until('[...document.querySelectorAll("[data-cr-cancel]")].some(b=>!b.hidden)');
  await ev('[...document.querySelectorAll("[data-cr-cancel]")].find(b=>!b.hidden).click()');
  await wait(850);
  assert.equal([...jobs.values()].at(-1).status, 'cancelled');
  ok('Cancellation remains terminal when late stream completes');
  assert.equal(created,3);
  assert.ok(requests.filter(r=>r.path==='/api/creator/jobs'&&r.method==='POST').every(r=>r.body.request_id));
  ok('Stable request IDs and isolated Creator subscriptions');
  await win.loadFile(path.join(client,'src/index.html'));
  await until('document.querySelector("#msgs")?.textContent.includes("没按契约返回 JSON")');
  assert.equal(created,3);
  ok('Refresh restores Creator request, partial output and safe error without generation');
  await ev('document.querySelector("#creatorBtn").click();document.querySelector("#creatorBtn").click()');
  await submit('普通聊天错误恢复');
  await until('document.querySelector("#msgs").textContent.includes("连接中断，结果不完整")');
  assert.ok(await ev('document.querySelector("#msgs").textContent.includes("部分回答")'));
  const beforeRefresh = streams;
  await win.loadFile(path.join(client,'src/index.html'));
  await until('document.querySelector("#msgs")?.textContent.includes("普通聊天错误恢复")');
  assert.ok(await ev('document.querySelector("#msgs").textContent.includes("部分回答")'));
  assert.equal(streams,beforeRefresh);
  ok('Main chat failure and partial answer survive reload without a second request');
  await ev('document.querySelector("#creatorBtn").click()');
  mode='disconnect'; await submit('重连不重跑');
  await until('document.querySelector("#msgs").textContent.includes("重连不重跑") && [...document.querySelectorAll("[data-cr-cancel]")].every(b=>b.hidden)');
  assert.equal(created,4);
  ok('Read-only reconnection never creates another Creator job');
  mode='no-native';
  const beforeNoNative = created;
  await submit('无原生流测试');
  await untilMain(() => created === beforeNoNative + 1
    && [...jobs.values()].at(-1).content === '非流式完整响应', 'no-native job');
  await until('Array.from(document.querySelectorAll(".cr-task [data-cr-note]")).at(-1).textContent.includes("模型没按契约返回 JSON")');
  assert.equal(await ev('document.querySelector("#msgs").textContent.includes("非流式完整响应")'),false);
  assert.equal(created,5);
  ok('No native token stream reports the provider error without dumping the raw response');
  offline=true; await submit('未送达请求恢复');
  await wait(200);
  await win.loadFile(path.join(client,'src/index.html'));
  await until('document.querySelector("#msgs")?.textContent.includes("未送达请求恢复")');
  assert.equal(created,5);
  ok('Offline pending input survives reload and is never silently resubmitted');
  offline=false;
  // A fresh BrowserWindow uses the same isolated profile, approximating a
  // client reopen without ever touching production userData.
  const previousWindow = win;
  win = new BrowserWindow({show:false,width:1300,height:900,webPreferences:{preload:path.join(client,'electron/preload.js'),sandbox:true,contextIsolation:true,nodeIntegration:false}});
  traceRenderer(win);
  previousWindow.destroy();
  await win.loadFile(path.join(client,'src/index.html'));
  await until('document.querySelector("#msgs")?.textContent.includes("未送达请求恢复")');
  assert.equal(created,5);
  ok('Client window reopen preserves pending display records');
  offline=true;
  await ev('window.vigil.cfg.set({device_id:"other-account",src:"wechat"})');
  await win.loadFile(path.join(client,'src/index.html'));
  await until('document.querySelector("#ta")');
  await wait(250);
  assert.equal(await ev('document.querySelector("#msgs").textContent.includes("未送达请求恢复")'),false);
  ok('Connection identity and channel isolate local pending records');
  offline=false;
  await ev('window.vigil.cfg.set({device_id:"fixture",src:"pc"})');
  await win.loadFile(path.join(client,'src/index.html'));
  await until('document.querySelector("#msgs")?.textContent.includes("未送达请求恢复")');
  await ev(`localStorage.setItem('vigil.display.v1:'+JSON.stringify(['http://fixture','fixture','brain','wechat','main']),JSON.stringify([{request_id:'wechat_keep'}]));localStorage.setItem('vigil.display.v1:'+JSON.stringify(['http://fixture','other-account','brain','pc','other-session']),JSON.stringify([{request_id:'identity_keep'}]));localStorage.setItem('vigil.display.v1:'+JSON.stringify(['http://fixture','fixture','brain','pc','other-session']),JSON.stringify([{request_id:'other_conversation_keep'}]))`);
  await ev('document.querySelector("#settingsOpen").click()');
  await until('document.querySelector("#settingsBackChat")');
  await ev('document.querySelectorAll(".settings-tile")[3].click()');
  await until('document.querySelector("#cfgSubnav [data-k=danger]")');
  await ev('document.querySelector("#cfgSubnav [data-k=danger]").click()');
  await until('Array.from(document.querySelectorAll("[data-p]")).some(b=>b.dataset.p==="/api/history/clear")');
  await ev('Array.from(document.querySelectorAll("[data-p]")).find(b=>b.dataset.p==="/api/history/clear").click()');
  await until('document.querySelector(".vigil-confirm [data-choice=ok]")');
  await ev('document.querySelector(".vigil-confirm [data-choice=ok]").click()');
  await until('document.querySelector("#toast")?.textContent.includes("清空聊天历史：已执行")');
  assert.equal(historyClearCalls.length,1);
  assert.equal(historyClearCalls[0].src,'pc');
  assert.equal(conversations.length,0);
  const localDisplayState = await ev(`(()=>{
    const keys=Object.keys(localStorage).filter(k=>k.startsWith('vigil.display.v1:'));
    const scoped=keys.map(k=>{try{return JSON.parse(k.slice('vigil.display.v1:'.length).replace(/:cleared$/,''))}catch(_){return null}}).filter(s=>Array.isArray(s));
    const currentSessions=scoped.filter(s=>s[0]==='http://fixture'&&s[1]==='fixture'&&s[2]==='brain'&&s[3]==='pc').map(s=>s[4]).sort();
    return {currentSessions,otherChannel:scoped.some(s=>s[3]==='wechat'),otherIdentity:scoped.some(s=>s[1]==='other-account')};
  })()`);
  assert.deepEqual(localDisplayState,{currentSessions:['other-session'],otherChannel:true,otherIdentity:true});
  await ev('document.querySelector("#gnav [data-page=chat]").click()');
  await until('document.querySelector("#msgs")');
  await until('document.querySelector("#msgs").textContent.trim()===""');
  await win.loadFile(path.join(client,'src/index.html'));
  await until('document.querySelector("#ta")');
  await wait(250);
  assert.equal(await ev('document.querySelector("#msgs").textContent.includes("未送达请求恢复")'),false);
  assert.equal(created,5);
  ok('Explicit clear removes persisted display records without replay');
  fs.writeFileSync(path.join(tmp,'results.json'),JSON.stringify({results,created,streams,historyClearCalls,realModelCalls:0},null,2));
  console.log('Evidence: ' + path.join(tmp,'results.json'));
  win.destroy(); app.exit(0);
}
app.whenReady().then(main).catch(error=>{console.error(error.stack); if(win)win.destroy();app.exit(1);});
