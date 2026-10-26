const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
require('./test-wallpaper-settings-isolation.cjs');
require('./test-update-manager.cjs');
require('./test-video-media.cjs');
require('./test-plugin-market-lifecycle.js');
require('./test-data-page-lifecycle.js');
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

function pages(options={}) {
  const observers=new Set(),storage=new Map(),handlers=new Set(),toasts=[],dialogs=[];
  function element() {
    const children = new Map();
    return {
      className:'', innerHTML:'', isConnected:false, listeners:{},
      querySelector(selector) {
        if (!children.has(selector)) children.set(selector, {textContent:'', value:'', onclick:null, focus(){}});
        return children.get(selector);
      },
      addEventListener(type, fn) { this.listeners[type]=fn; },
      remove() { this.isConnected=false; },
      focus() {},
    };
  }
  const document={body:{appendChild(node){node.isConnected=true;dialogs.push(node);}},
    createElement:()=>element(), addEventListener(){}, removeEventListener(){}};
  const window={vigil:{onStreamEvent:fn=>{handlers.add(fn);return ()=>handlers.delete(fn);}}};
  const context={window,console,setTimeout,clearTimeout,confirm:options.confirm || (()=>true),navigator:{clipboard:{writeText:options.writeClipboard || (async()=>{})}},
    document,
    MutationObserver:class {constructor(fn){this.fn=fn;} observe(){observers.add(this);} disconnect(){observers.delete(this);}},
    localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,v)=>storage.set(key,v)},
    U:{esc:String,icon:()=>'',md:source=>'<rendered>'+source+'</rendered>',toast:msg=>{toasts.push(String(msg));}},
    API:{state:{cfg:{},src:'pc'},get:options.get || (async()=>({ok:true,plugins:[],pending_restart:{pending:true,reasons:[]}})),post:options.post || (async()=>({ok:false})),pick:x=>x},
  };
  let source=fs.readFileSync(path.join(client,'src/js/pages.js'),'utf8');
  // Expose existing functions only in this isolated VM, without a production test API.
  source=source.replace('  global.PAGES = [','  global.p2 = {subscribeUntilDetached,creatorErrorText,creatorRequestFailure,rememberTurn,localRecords,watchCreatorJob,creatorActionError,bindDraftCard,draftCardHtml};\n  global.PAGES = [');
  vm.runInNewContext(source,context);
  return {...window.p2,handlers,notify:()=>[...observers].forEach(o=>o.fn()),observers,toasts,
    confirmNext(value=true) {
      const overlay=dialogs.find(item=>item.isConnected);
      assert.ok(overlay,'expected an in-app confirmation dialog');
      overlay.querySelector(`[data-choice="${value?'ok':'cancel'}"]`).onclick();
    }};
}

function draftRoot() {
  const nodes = {
    '#crApproveNow': {disabled:false, textContent:'明确批准', style:{}},
    '#crInstallNow': {disabled:false, textContent:'仅本次安装', style:{display:'none'}, dataset:{}},
    '#crApprovalState': {textContent:'尚未批准'},
    '[data-cr-file-toggle="0"]': {textContent:'展开源码', dataset:{crFileToggle:'0'}, attrs:{}, setAttribute(k,v){this.attrs[k]=v;}},
    '[data-cr-file-toggle="1"]': {textContent:'展开源码', dataset:{crFileToggle:'1'}, attrs:{}, setAttribute(k,v){this.attrs[k]=v;}},
    '[data-copy-file="0"]': {textContent:'复制完整源码', dataset:{copyFile:'0'}, isConnected:true},
    '[data-cr-file-preview="0"]': {hidden:false},
    '[data-cr-file-preview="1"]': {hidden:false},
    '[data-cr-file-body="0"]': {hidden:true, innerHTML:'', dataset:{}},
    '[data-cr-file-body="1"]': {hidden:true, innerHTML:'', dataset:{}},
  };
  const listeners = {};
  return {
    nodes, listeners,
    addEventListener(type, callback) { listeners[type] = callback; },
    querySelector(selector) { return nodes[selector] || null; },
    click(selector) {
      const button = nodes[selector];
      const closest = candidate => {
        if (candidate === selector) return button;
        if (candidate === '[data-cr-file-toggle]' && selector.startsWith('[data-cr-file-toggle=')) return button;
        if (candidate === '[data-copy-file]' && selector.startsWith('[data-copy-file=')) return button;
        return null;
      };
      return listeners.click({target:{closest}});
    },
  };
}
function root() {
  const nodes=new Map();
  const node=()=>({textContent:'',hidden:false,className:'',dataset:{},disabled:false,
    setAttribute(key,v){this[key]=v;},querySelector(){return node();},addEventListener(){}});
  return {isConnected:true,innerHTML:'',dataset:{},
    querySelectorAll(){return [];},
    querySelector(selector){
      if (!nodes.has(selector)) nodes.set(selector,node());
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
  // P7.2: the diagnosis lives in the task card note, never in the model output.
  const note=r.querySelector('[data-cr-note]');
  for(const text of ['JSON 解析','json_parse_error','line 3, column 10','换模型重试','job_safe'])assert.ok(note.textContent.includes(text),text);
  assert.equal(note.role,'alert');assert.equal(note.dataset.tone,'err');
  assert.equal(r.innerHTML.includes('<script>'),false);
  // A terminal job can never offer cancellation again.
  assert.equal(r.querySelector('[data-cr-cancel]').hidden,true);
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

test('Creator files render compact previews and lazy-render independent source panels', async () => {
  const p = pages();
  const content = Array.from({length:80}, (_,i)=>'line '+(i+1)).join('\n');
  const html = p.draftCardHtml({id:'cr_files', kind:'plugin', version:1, files:[
    {name:'large.py', content}, {name:'small.json', content:'{"ok":true}'},
  ]});
  assert.ok(html.includes('data-cr-file-toggle="0" aria-expanded="false">展开源码'));
  assert.ok(html.includes('data-copy-file="0">复制完整源码'));
  assert.ok(html.includes('预览前 6 行 · 还有 74 行'));
  assert.ok(!html.includes('line 80'), 'full file should not be emitted into initial preview HTML');
  const r = draftRoot();
  p.bindDraftCard(r, {id:'cr_files', kind:'plugin', version:1, files:[
    {name:'large.py', content}, {name:'small.json', content:'{"ok":true}'},
  ]});
  await r.click('[data-cr-file-toggle="0"]');
  assert.equal(r.nodes['[data-cr-file-body="0"]'].hidden, false);
  assert.equal(r.nodes['[data-cr-file-preview="0"]'].hidden, true);
  assert.equal(r.nodes['[data-cr-file-body="0"]'].dataset.rendered, 'true');
  assert.ok(r.nodes['[data-cr-file-body="0"]'].innerHTML.includes('line 80'));
  assert.equal(r.nodes['[data-cr-file-body="1"]'].hidden, true, 'other file remains collapsed');
  assert.equal(r.nodes['[data-cr-file-toggle="0"]'].textContent, '收起源码');
  await r.click('[data-cr-file-toggle="0"]');
  assert.equal(r.nodes['[data-cr-file-body="0"]'].hidden, true);
  assert.equal(r.nodes['[data-cr-file-preview="0"]'].hidden, false);
  assert.equal(r.nodes['[data-cr-file-toggle="0"]'].attrs['aria-expanded'], 'false');
});

test('Creator full source can be copied without expanding its preview', async () => {
  let copied = '';
  const p = pages({writeClipboard:async value=>{copied=value;}});
  const content = 'line 1\nline 2\nline 3';
  const r = draftRoot();
  p.bindDraftCard(r, {id:'cr_copy', kind:'plugin', version:1, files:[{name:'demo.py', content}]});
  assert.equal(r.nodes['[data-cr-file-body="0"]'].hidden, true);
  await r.click('[data-copy-file="0"]');
  assert.equal(copied, content);
  assert.equal(r.nodes['[data-cr-file-body="0"]'].hidden, true);
  assert.equal(r.nodes['[data-copy-file="0"]'].textContent, '已复制');
});

test('Creator approval and install clicks consume the returned one-time approval', async () => {
  const calls = [];
  const p = pages({post:async (path, body) => {
    calls.push({path, body});
    if (path === '/api/creator/approve') return {ok:true, approval:{version:2, approval_id:'ca_fixture_approval'}};
    if (path === '/api/creator/install') return {ok:true, approval:{target_name:'fixture_plugin'}, install:{restart_needed:true,dynamic_loaded:false,package:'fixture_plugin'}};
    return {ok:false, _status:404, _err:'unexpected route'};
  }});
  const r = draftRoot();
  p.bindDraftCard(r, {id:'cr_fixture', kind:'plugin', summary:'fixture', version:2, files:[{name:'fixture.py'}]});
  await r.click('#crApproveNow');
  assert.equal(calls[0].path, '/api/creator/approve');
  assert.equal(calls[0].body.id, 'cr_fixture');
  assert.equal(r.nodes['#crApproveNow'].style.display, 'none');
  assert.equal(r.nodes['#crInstallNow'].style.display, 'inline-block');
  assert.equal(r.nodes['#crInstallNow'].dataset.approval, 'ca_fixture_approval');
  assert.match(r.nodes['#crApprovalState'].textContent, /已批准草稿 v2/);
  const installRequest=r.click('#crInstallNow'); p.confirmNext(true); await installRequest;
  assert.equal(calls[1].path, '/api/creator/install');
  assert.equal(calls[1].body.approval_id, 'ca_fixture_approval');
  assert.equal(r.nodes['#crInstallNow'].textContent, '已安装 · 待重启');
  assert.equal(r.nodes['#crInstallNow'].disabled, true);
  assert.match(r.nodes['#crApprovalState'].textContent, /安装成功/);
  assert.match(r.nodes['#crApprovalState'].textContent, /单文件插件/);
  assert.match(r.nodes['#crApprovalState'].textContent, /Brain 插件目录/);
  assert.match(r.nodes['#crApprovalState'].textContent, /重启 Brain 后/);
  assert.match(r.nodes['#crApprovalState'].textContent, /TOOLS \/ TOOL_SPECS/);
  assert.match(r.nodes['#crInstallNow'].textContent, /待重启/);
});

test('Creator approval transport failure displays destination and error, then re-enables approval', async () => {
  const p = pages({post:async()=>({ok:false, _status:0, _target:'http://127.0.0.1:61285', _err:'connection refused'})});
  const r = draftRoot();
  p.bindDraftCard(r, {id:'cr_fixture', kind:'plugin', summary:'fixture', version:1, files:[{name:'fixture.py'}]});
  await r.click('#crApproveNow');
  assert.equal(r.nodes['#crApproveNow'].disabled, false);
  assert.equal(r.nodes['#crApproveNow'].textContent, '明确批准');
  for (const expected of ['connection refused', 'http://127.0.0.1:61285']) {
    assert.ok(r.nodes['#crApprovalState'].textContent.includes(expected), r.nodes['#crApprovalState'].textContent);
    assert.ok(p.toasts.some(t => t.includes(expected)), p.toasts.join(' | '));
  }
});

test('Creator install failure prevents blind retry and asks for fresh approval', async () => {
  const p = pages({post:async path => {
    if (path === '/api/creator/approve') return {ok:true, approval:{version:1, approval_id:'ca_expired'}};
    return {ok:false, _status:400, _target:'http://127.0.0.1:61285', err:'审批已过期'};
  }});
  const r = draftRoot();
  p.bindDraftCard(r, {id:'cr_fixture', kind:'plugin', summary:'fixture', version:1, files:[{name:'fixture.py'}]});
  await r.click('#crApproveNow');
  const installRequest=r.click('#crInstallNow'); p.confirmNext(true); await installRequest;
  assert.equal(r.nodes['#crInstallNow'].disabled, true);
  assert.equal(r.nodes['#crInstallNow'].textContent, '需重新批准');
  assert.match(r.nodes['#crApprovalState'].textContent, /重新验证/);
  assert.ok(p.toasts.some(t => t.includes('审批已过期')));
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

