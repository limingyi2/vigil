// Real production renderer + sandboxed preload; all services use fixtures.
const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');
const assert = require('assert/strict');
const client = path.resolve(__dirname, '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-p1-electron-'));
app.setPath('userData', path.join(tmp, 'userdata'));
let win, mode = 'length', created = 0, streams = 0;
const jobs = new Map(), requests = [], results = [];
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const ev = source => win.webContents.executeJavaScript(source, true);
async function until(source) {
  const end = Date.now() + 12000;
  while (Date.now() < end) { if (await ev('Boolean(' + source + ')')) return; await wait(40); }
  throw Error('Timeout: ' + source);
}
function ok(name) { results.push(name); console.log('PASS ' + name); }
function response(method, raw, body) {
  const p = new URL(raw, 'http://fixture').pathname;
  requests.push({method, path:p, body});
  let data = {};
  if (p === '/api/persona') data = {name:'Fixture', key:'default', templates:[]};
  if (p === '/api/history') data = {items:[]};
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
        requirement:body.requirement,created_at:Date.now()/1000,status:'running',stage:'generating',event_cursor:0,content:''};
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
      event.sender.send('api:stream:event',{type:'error',msg:'模型连接中断或超时'});
      return {ok:false};
    }
    const job = jobs.get(p.split('/')[4]);
    const send = (type,payload) => event.sender.send('api:stream:event',{job_id:job.job_id,seq:++job.event_cursor,type,payload});
    send('think_delta',{text:'可展示思考'});
    await wait(90);
    send('text_delta',{text:'候选正文'});
    job.content = '候选正文';
    await wait(mode === 'cancel' ? 700 : 150);
    if (mode === 'disconnect') return {ok:false};
    if (job.status !== 'cancelled') Object.assign(job,{status:'failed',error:{class:mode,message:mode==='json'?'模型没按契约返回 JSON':'模型输出被截断'}});
    send('snapshot', {...job});
    return {ok:true};
  });
  let cfg = {wizard_done:true,roots:[],base:'http://fixture',device_id:'fixture',src:'pc'};
  for (const [name,value] of Object.entries({
    'app:version':'P1 test','cfg:get':cfg,'plugins:load':{ok:true},'local:defaults':{roots:[],commands:[],danger:[]},
    'local:caps':[],'sidecar:status':{status:'stopped'},'token:get':'已保存','auto:connect':{ok:false},
    'device:whoami':{device_id:'fixture',base:'http://fixture',token_kind:'brain'},
    'wallpaper:request':{ok:true,items:[]},'updater:check':{}
  })) ipcMain.handle(name,()=>value);
  ipcMain.handle('cfg:set',(_,patch)=>(cfg={...cfg,...patch}));
  win = new BrowserWindow({show:false,width:1300,height:900,webPreferences:{
    preload:path.join(client,'electron/preload.js'),sandbox:true,contextIsolation:true,nodeIntegration:false}});
  await win.loadFile(path.join(client,'src/index.html'));
  await until('document.querySelector("#creatorBtn")');
  await ev('document.querySelector("#creatorBtn").click()');
  async function submit(text) {
    await ev(`(()=>{const t=document.querySelector('#ta');t.value=${JSON.stringify(text)};t.dispatchEvent(new Event('input'));document.querySelector('#sendBtn').click()})()`);
  }
  await submit('生成测试插件');
  await until('document.querySelector("#msgs").textContent.includes("模型输出被截断")');
  assert.ok(await ev('document.querySelector("#msgs").textContent.includes("候选正文")'));
  ok('Creator real delta events preserve partial content on truncation');
  mode = 'json'; await submit('生成格式错误插件');
  await until('document.querySelector("#msgs").textContent.includes("没按契约返回 JSON")');
  ok('Creator JSON error is visible through production preload');
  mode = 'cancel'; await submit('取消测试');
  await until('[...document.querySelectorAll(".creator-cancel")].some(b=>!b.hidden)');
  await ev('[...document.querySelectorAll(".creator-cancel")].find(b=>!b.hidden).click()');
  await wait(850);
  assert.equal([...jobs.values()].at(-1).status, 'cancelled');
  ok('Cancellation remains terminal when late stream completes');
  assert.equal(created,3);
  assert.ok(requests.filter(r=>r.path==='/api/creator/jobs'&&r.method==='POST').every(r=>r.body.request_id));
  ok('Stable request IDs and isolated Creator subscriptions');
  fs.writeFileSync(path.join(tmp,'results.json'),JSON.stringify({results,created,streams,realModelCalls:0},null,2));
  console.log('Evidence: ' + path.join(tmp,'results.json'));
  win.destroy(); app.exit(0);
}
app.whenReady().then(main).catch(error=>{console.error(error.stack); if(win)win.destroy();app.exit(1);});
