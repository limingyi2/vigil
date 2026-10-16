// Production renderer + sandboxed production preload. No real model or tools.
const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');
const assert = require('assert/strict');
const client = path.resolve(__dirname, '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-p2-electron-'));
app.setPath('userData', path.join(tmp, 'userdata'));
let win, mode = 'success', streams = 0, cancels = 0;
const runs = new Map(), results = [], rendererErrors = [];
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const ev = source => win.webContents.executeJavaScript(source, true);
async function until(source) {
  const end = Date.now()+10000;
  while (Date.now()<end) { if (await ev('Boolean('+source+')')) return; await wait(30); }
  throw Error('Timeout: '+source);
}
function ok(name) { results.push(name); console.log('PASS '+name); }
function response(method, raw) {
  const p = new URL(raw, 'http://fixture').pathname;
  let data = {};
  if (p === '/api/persona') data = {name:'Fixture',key:'default',templates:[]};
  if (p === '/api/history') data = {items:[],display_turns:[...runs.values()]};
  if (p === '/api/creator/jobs') data = {items:[]};
  if (p === '/api/stats') data = {model:'fixture'};
  if (p === '/api/models') data = {current:{model:'fixture'},models:[],providers:[]};
  if (p === '/api/plugins/frontend') data = {version:1,plugins:[]};
  if (p.endsWith('/cancel') && p.startsWith('/api/agent/runs/')) {
    cancels++;
    data = runs.get(p.split('/')[4]);
    assert.ok(data);
    Object.assign(data,{status:'cancelled',error:{message:'已取消后续调度；在途操作可能仍在执行'}});
  }
  return {ok:true,status:200,json:data};
}
async function main() {
  ipcMain.handle('api',(_, {method,path:p})=>response(method,p));
  ipcMain.handle('api:stream', async (event, {body})=>{
    streams++;
    assert.match(body.request_id,/^req_/);
    const run = {run_id:'run_'+streams,request_id:body.request_id,input:body.text,status:'running',content:'',created_at:Date.now()/1000};
    runs.set(run.run_id,run);
    const send = (type,extra={})=>{if(!event.sender.isDestroyed())event.sender.send('api:stream:event',{type,run_id:run.run_id,request_id:run.request_id,...extra});};
    send('agent_run');
    send('stage',{stage:'waiting_tool'});
    send('text',{text:'中间正文'});
    run.content = '中间正文';
    if(mode === 'active') { await wait(900); return {ok:true}; }
    await wait(mode === 'cancel' ? 500 : 100);
    if (run.status === 'cancelled') { send('error',{text:run.content,status:'cancelled',msg:'已取消后续调度'}); return {ok:true}; }
    send('run_snapshot',{text:'中间正文\n快照结果'});
    send('done',{text:'中间正文\n快照结果\n最终完成'});
    Object.assign(run,{status:'succeeded',content:'中间正文\n快照结果\n最终完成'});
    return {ok:true};
  });
  let cfg = {wizard_done:true,roots:[],base:'http://fixture',device_id:'fixture',src:'pc'};
  for(const [name,value] of Object.entries({
    'app:version':'P2 test','plugins:load':{ok:true},'local:defaults':{roots:[],commands:[],danger:[]},
    'local:caps':[],'sidecar:status':{status:'stopped'},'token:get':'已保存','auto:connect':{ok:false},
    'device:whoami':{device_id:'fixture',base:'http://fixture',token_kind:'brain'},
    'wallpaper:request':{ok:true,data:{settings:{},wallpapers:[]}},'updater:check':{}
  })) ipcMain.handle(name,()=>value);
  ipcMain.handle('cfg:get',()=>cfg);
  ipcMain.handle('cfg:set',(_,p)=>(cfg={...cfg,...p}));
  win = new BrowserWindow({show:false,width:1300,height:900,webPreferences:{
    preload:path.join(client,'electron/preload.js'),sandbox:true,contextIsolation:true,nodeIntegration:false}});
  win.webContents.on('console-message',(_e,level,message)=>{if(level===3)rendererErrors.push(message);});
  await win.loadFile(path.join(client,'src/index.html'));
  await until('document.querySelector("#ta")');
  async function submit(text) {
    await ev(`(()=>{const t=document.querySelector('#ta');t.value=${JSON.stringify(text)};t.dispatchEvent(new Event('input'));document.querySelector('#sendBtn').click()})()`);
  }
  await submit('P2多步任务');
  await until('document.querySelector("#msgs").textContent.includes("最终完成")');
  assert.equal(await ev('(document.querySelector("#msgs").textContent.match(/中间正文/g)||[]).length'),1);
  ok('Run snapshot replaces preview; authoritative final text is not duplicated');
  mode='cancel'; await submit('P2取消任务');
  await until('[...document.querySelectorAll(".agent-cancel")].some(b=>b.isConnected&&!b.disabled)');
  await ev('[...document.querySelectorAll(".agent-cancel")].find(b=>!b.disabled).click()');
  await until('document.querySelector("#msgs").textContent.includes("已取消后续调度")');
  await wait(550);
  assert.equal(cancels,1);
  assert.equal([...runs.values()].at(-1).status,'cancelled');
  ok('Live run cancellation uses the owner-scoped API and preserves cancelled state');
  mode='active'; await submit('P2刷新恢复任务');
  await until('document.querySelector("#msgs").textContent.includes("中间正文")&&document.querySelector(".agent-cancel")');
  await wait(100);
  const beforeReload = streams;
  await win.loadFile(path.join(client,'src/index.html'));
  await until('document.querySelector("#msgs")?.textContent.includes("P2刷新恢复任务")&&document.querySelector(".agent-cancel")');
  assert.equal(streams,beforeReload);
  ok('Reload restores server run and partial output without generating again');
  await ev('document.querySelector(".agent-cancel").click()');
  await until('[...document.querySelectorAll(".agent-cancel")].length===0');
  assert.equal(cancels,2);
  assert.equal(streams,beforeReload);
  ok('Restored run remains cancellable without opening a new executor');
  assert.deepEqual(rendererErrors,[]);
  ok('Production renderer has no uncaught errors');
  fs.writeFileSync(path.join(tmp,'results.json'),JSON.stringify({results,streams,cancels,rendererErrors,realModelCalls:0},null,2));
  console.log('Evidence: '+path.join(tmp,'results.json'));
  win.destroy(); app.exit(0);
}
app.whenReady().then(main).catch(error=>{console.error(error.stack);if(win)win.destroy();app.exit(1);});
