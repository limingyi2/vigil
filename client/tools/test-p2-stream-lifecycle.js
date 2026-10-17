const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {EventEmitter} = require('node:events');
const client = path.resolve(__dirname, '..');

function preload() {
  const ipc = new EventEmitter();
  ipc.invoke = async () => ({});
  let api;
  vm.runInNewContext(fs.readFileSync(path.join(client,'electron/preload.js'),'utf8'), {
    process:{platform:process.platform},
    require: name => {assert.equal(name,'electron');return {ipcRenderer:ipc,contextBridge:{exposeInMainWorld:(_,value)=>api=value}};},
  });
  return {api,ipc};
}

test('native project picker exposes only the directory-selection IPC', async () => {
  const {api,ipc} = preload();
  const calls = [];
  ipc.invoke = async (...args) => { calls.push(args); return {ok:true, path:'C:/Projects/demo'}; };
  const result = await api.projectPicker.pickDirectory();
  assert.equal(result.ok, true);
  assert.equal(result.path, 'C:/Projects/demo');
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], 'project-picker:directory');
});

test('Branch Atlas frontend parses and uses the native picker plus read-only inspect route', () => {
  const file = path.resolve(__dirname, '../../brain/plugins/branch-atlas/frontend.js');
  const source = fs.readFileSync(file, 'utf8');
  assert.doesNotThrow(() => new Function(source));
  assert.match(source, /projectPicker/);
  assert.ok(source.includes("'/api/plugin/branch-atlas/inspect'"));
  assert.match(source, /不会切换分支/);
});

test('100 shared chat/Creator rounds release IPC and subscriber callbacks', () => {
  const {api,ipc} = preload();
  let calls = 0;
  for (let i=0;i<100;i++) {
    const offChat = api.onStreamEvent(()=>calls++);
    const offCreator = api.onCreatorEvent(()=>calls++);
    assert.equal(ipc.listenerCount('api:stream:event'),1);
    ipc.emit('api:stream:event',{},{});
    offChat();offChat();
    ipc.emit('api:stream:event',{},{});
    offCreator();
    assert.equal(ipc.listenerCount('api:stream:event'),0);
    ipc.emit('api:stream:event',{},{});
  }
  assert.equal(calls,300);
});

test('shared subscriptions isolate errors, reentrant removal and duplicate callbacks', () => {
  const {api,ipc} = preload();
  let calls=0;
  const fn=()=>calls++;
  const a=api.onStreamEvent(fn),b=api.onStreamEvent(fn);
  const bad=api.onCreatorEvent(()=>{throw Error('isolated');});
  ipc.emit('api:stream:event',{},{});a();ipc.emit('api:stream:event',{},{});
  assert.equal(calls,3);b();bad();
  let next=()=>{};
  const first=api.onStreamEvent(()=>next());next=api.onCreatorEvent(()=>calls++);
  ipc.emit('api:stream:event',{},{});assert.equal(calls,3);first();
  assert.throws(()=>api.onStreamEvent(null),/function/);
  assert.equal(ipc.listenerCount('api:stream:event'),0);
});

function pages() {
  const observers=new Set(),storage=new Map(),handlers=new Set();
  const window={vigil:{onStreamEvent:fn=>{handlers.add(fn);return ()=>handlers.delete(fn);}}};
  const context={window,console,setTimeout,clearTimeout,
    document:{body:{}},
    MutationObserver:class {constructor(fn){this.fn=fn;} observe(){observers.add(this);} disconnect(){observers.delete(this);}},
    localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,v)=>storage.set(key,v)},
    U:{esc:String,icon:()=>'',toast:()=>{}},
    API:{state:{cfg:{},src:'pc'},get:async()=>({ok:false}),post:async()=>({ok:false}),pick:x=>x},
  };
  let source=fs.readFileSync(path.join(client,'src/js/pages.js'),'utf8');
  // Expose existing functions only in this isolated VM, without a production test API.
  source=source.replace('  global.PAGES = [','  global.p2 = {subscribeUntilDetached,creatorErrorText,creatorRequestFailure,rememberTurn,localRecords,watchCreatorJob};\n  global.PAGES = [');
  vm.runInNewContext(source,context);
  return {...window.p2,handlers,notify:()=>[...observers].forEach(o=>o.fn()),observers};
}

function root() {
  const nodes=new Map();
  return {isConnected:true,innerHTML:'',querySelector(selector){
    if (!nodes.has(selector)) nodes.set(selector,{textContent:'',hidden:false,setAttribute(key,v){this[key]=v;}});
    return nodes.get(selector);
  }};
}

test('detaching a chat bubble removes its subscription and observer immediately', () => {
  const p=pages(),r=root();let calls=0;
  const close=p.subscribeUntilDetached(r,fn=>{p.handlers.add(fn);return ()=>p.handlers.delete(fn);},()=>calls++);
  [...p.handlers].forEach(fn=>fn({}));assert.equal(calls,1);
  r.isConnected=false;p.notify();
  assert.equal(p.handlers.size,0);assert.equal(p.observers.size,0);close();
});

test('terminal cleanup is idempotent and later events cannot affect completed turns', () => {
  const p=pages();let calls=0;
  for(let i=0;i<100;i++) {
    const close=p.subscribeUntilDetached(root(),fn=>{p.handlers.add(fn);return ()=>p.handlers.delete(fn);},()=>calls++);
    [...p.handlers].forEach(fn=>fn({type:'done'}));close();close();
  }
  assert.equal(calls,100);assert.equal(p.handlers.size,0);assert.equal(p.observers.size,0);
});

test('Creator terminal snapshot shows stage, code, parser facts, advice and log ID as text', async () => {
  const p=pages(),r=root();
  const error={stage:'parse',code:'json_parse_error',message:'Expecting value (line 3, column 10) <script>bad()</script>',hint:'换模型重试',log_id:'job_safe'};
  await p.watchCreatorJob(r,{job_id:'job_safe',request_id:'req_test',status:'failed',content:'',error});
  const status=r.querySelector('.stream-status');
  for(const text of ['JSON 解析','json_parse_error','line 3, column 10','换模型重试','job_safe'])assert.ok(status.textContent.includes(text),text);
  assert.equal(status.role,'alert');assert.equal(r.innerHTML.includes('<script>'),false);
  assert.equal(p.handlers.size,0);assert.equal(p.observers.size,0);
});

test('each Creator failure stage remains distinguishable on reload', () => {
  const p=pages();
  for(const stage of ['model_request','network','auth','parse','codegen','static_validation','draft_save']) {
    const text=p.creatorErrorText({stage,error_code:stage+'_failed',message:'失败',hint:'建议',log_id:'job_id'});
    assert.ok(text.includes(stage+'_failed'));assert.ok(text.includes('建议'));assert.ok(text.includes('job_id'));
  }
});

test('Creator 404 is a durable failed request with HTTP status and actual destination', () => {
  const p=pages();
  const failure=p.creatorRequestFailure({_status:404,_target:'http://127.0.0.1:61285',err:'no such api'});
  assert.equal(failure.status,'failed');
  p.rememberTurn('req_missing_api',failure);
  const saved=p.localRecords()[0];
  assert.equal(saved.status,'failed');
  const text=p.creatorErrorText(saved.error);
  for(const wanted of ['Creator jobs 接口','404','http://127.0.0.1:61285','creator_api_missing','sidecar'])assert.ok(text.includes(wanted),text);
});

test('Creator rejects confirmed client errors but preserves uncertain network/server outcomes', () => {
  const p=pages();
  for(const status of [400,401,403,409,429])assert.equal(p.creatorRequestFailure({_status:status}).status,'failed');
  for(const status of [0,408,500,502,504])assert.equal(p.creatorRequestFailure({_status:status}).status,'pending');
});

test('API pick preserves transport status and safe destination without trusting JSON ok on HTTP failure', () => {
  const window={};vm.runInNewContext(fs.readFileSync(path.join(client,'src/js/api.js'),'utf8'),{window,navigator:{userAgent:'Electron/28'}});
  const r=window.API.pick({ok:false,status:404,target:'http://127.0.0.1:61285',json:{ok:true,err:'no such api'}});
  assert.equal(r.ok,false);assert.equal(r._status,404);assert.equal(r._target,'http://127.0.0.1:61285');
});

test('production chat disposes both terminal events, exceptions, fallback and missing-done timeout', () => {
  const source=fs.readFileSync(path.join(client,'src/js/pages.js'),'utf8');
  const chat=source.slice(source.indexOf('// 监听流式事件'),source.indexOf("view.querySelector('#sendBtn').addEventListener('click', send)"));
  assert.match(chat,/subscribeUntilDetached\(document.getElementById\(turnId\)/);
  for(const marker of ["evt.type === 'done'","evt.type === 'error'",'if (!streamDone) {','} catch (e) {']) {
    const index=chat.indexOf(marker);assert.ok(index>=0);assert.ok(chat.slice(index,index+1800).includes('stopListening()'),marker);
  }
  assert.doesNotMatch(chat,/console\.log\([^\n]*(evt\.text|apiStream returned)/);
});
