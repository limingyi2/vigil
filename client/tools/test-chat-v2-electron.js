// Real Electron renderer + local wallpaper host, with isolated API/IPC fixtures.
// No Brain, model API, production config, history or credential store is opened.
const { app, BrowserWindow, ipcMain, nativeImage } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');
const assert = require('assert/strict');
const crypto = require('crypto');
// GUI launchers can close their inherited pipes before Electron exits. Only a
// disconnected output pipe is benign; other stream errors still fail the test.
for(const stream of [process.stdout,process.stderr]) {
  stream.on('error',error=>{ if(error.code!=='EPIPE')throw error; });
}
const client = path.resolve(__dirname, '..');
const baseline = process.argv.includes('--baseline');
const src = baseline ? path.join(client, '_shots/ui-v2/baseline-src/src') : path.join(client, 'src');
const shots = path.join(client, '_shots/ui-v2');
const evidenceDir = path.resolve(client, '..', 'reports', 'p6.1-evidence');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-chat-v2-'));
app.setPath('userData', path.join(tmp, 'electron'));
app.commandLine.appendSwitch('enable-unsafe-swiftshader');
const wallpaperDiag = process.env.P8_WALLPAPER_DIAG === '1';
const wallpaperDiagFile = process.env.P8_WALLPAPER_DIAG_FILE || path.join(tmp, 'wallpaper-webgl-diagnostic.jsonl');
function wallpaperTrace(kind, data={}) {
  if (!wallpaperDiag) return;
  fs.appendFileSync(wallpaperDiagFile, JSON.stringify({t:Date.now(), monotonic:process.hrtime.bigint().toString(), kind, ...data})+'\n');
}
const results = [], errors = [], requests = [];
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
let win, host, history = [], failure = false, historyError = false, personaEmpty = false;
const legacyConversationId = '11111111-1111-4111-8111-111111111111';
const conversations = [{id:legacyConversationId,title:'之前的聊天',created_at:Date.now()/1000,updated_at:Date.now()/1000,legacy:true}];
const otherHistories = new Map();
const creatorJobs = new Map();
const creatorJobById = new Map();
const fixtureDraft = {id:'draft_p6_session',kind:'plugin',summary:'会话隔离草稿',version:1,model:'fixture',
  requirement:'P6_SESSION_DRAFT',risks:'fixture only',validation:{ok:true,errors:[],warnings:[]},
  files:[{name:'p6_session.py',content:'NAME = "p6_session"\nDESC = "fixture"\n'}]};
let uploadFailure = false;
let savedClipboard;
let gpu;
const answer = '# 执行结果\n\n这是一段用于检查长文本排版的回复。' + '内容应有舒适的行高和留白。'.repeat(18) +
  '\n\n## 下一步\n\n- 保留本地工具\n- 检查聊天布局\n\n> 每一步都有清晰的结果。\n\n[说明链接](https://example.com)\n\n```js\nconst vigil = "ready";\nconsole.log(vigil);\n```';
function result(name, detail) { results.push({ name, status:'passed', ...(detail ? { detail } : {}) }); console.log('PASS ' + name); }
function fixtureApi(method, raw, body, base) {
  const url = new URL(raw, 'http://fixture'); const p = url.pathname;
  requests.push({ method, path:p, query:Object.fromEntries(url.searchParams.entries()), body, base });
  let json = {};
  if (p === '/api/stats') json = { model:'mimo-v2.6-flash', name:'测试 Agent', turns:4, tok_in:2400, tok_out:600 };
  if (p === '/api/persona') json = { key:'default', name:'Vigil', templates:[{key:'default',label:'Vigil',desc:'日常对话与任务协作'},{key:'focus',label:'专注助手',desc:'清晰地拆解目标，逐步完成工作'}] };
  if (p === '/api/persona' && personaEmpty) json={key:'',templates:[]};
  if (p === '/api/persona/full') json = { fields:{name:'Vigil',personality:'清晰、耐心',background:'协助思考和执行任务'} };
  if (p === '/api/persona/save') json = { ok:true, reloaded:true };
  if (p === '/api/memory/stores') json = {items:['memory']};
  if (p === '/api/memory/list') json = {items:[{id:'fixture',text:'隔离记忆条目',type:'fact'}]};
  if (p === '/api/plugin/kb/stats') json = {docs:1,count:2,dir:'temporary fixture/kb',scope:'pc'};
  if (p === '/api/plugin/kb/docs') json = {items:[{name:'fixture.md',chunks:2,size:100}]};
  if (p === '/api/history' && historyError) return {ok:false,status:503,err:'隔离测试：历史服务暂时不可用'};
  if (p === '/api/conversations' && method === 'GET') json = {ok:true,items:conversations.map(s=>s.legacy
    ? {...s,title:history.find(m=>['user','me','self'].includes(String(m.role).toLowerCase())&&m.text)?.text||'旧对话'} : s)
    .sort((a,b)=>b.updated_at-a.updated_at)};
  if (p === '/api/conversations' && method === 'POST') {
    const item={id:crypto.randomUUID(),title:'新对话',created_at:Date.now()/1000,updated_at:Date.now()/1000,legacy:false};
    conversations.push(item); otherHistories.set(item.id,[]); json={ok:true,item};
  }
  if (p.startsWith('/api/conversations/') && method === 'POST') {
    const item=conversations.find(x=>x.id===p.split('/').at(-1)); if(item){item.title=body.title;item.updated_at=Date.now()/1000;json={ok:true,item};}
  }
  if (p === '/api/plugins/frontend') json = { version:1, plugins:[] };
  if (p === '/api/history') { const cid=url.searchParams.get('conversation_id'); json = { items:cid===legacyConversationId?history:(otherHistories.get(cid)||[]), display_turns:[] }; }
  if (p === '/api/creator/jobs' && method === 'POST') {
    const job={job_id:'job_'+crypto.randomUUID(),request_id:body.request_id,kind:body.kind,requirement:body.requirement,
      conversation_id:body.conversation_id,created_at:Date.now()/1000,status:'succeeded',stage:'complete',draft_id:fixtureDraft.id,content:'fixture generated',event_cursor:1};
    creatorJobs.set(body.conversation_id,[...(creatorJobs.get(body.conversation_id)||[]),job]);
    creatorJobById.set(job.job_id,job);json={ok:true,...job};
  }
  if (p === '/api/creator/jobs' && method === 'GET') json={ok:true,items:creatorJobs.get(url.searchParams.get('conversation_id'))||[]};
  if (p.startsWith('/api/creator/jobs/') && !p.endsWith('/events')) json={ok:true,...(creatorJobById.get(p.split('/').at(-1))||{})};
  if (p === '/api/creator/draft') json={ok:true,draft:fixtureDraft};
  if (p === '/api/creator/drafts') json={ok:true,drafts:[]};
  if (p === '/api/history/clear') history = [];
  if (p === '/api/models') json = { current:{ model:'mimo-v2.6-flash', api_key_set:true }, providers:[], models:[] };
  if (p === '/api/attachments/capabilities') json = {video:false,video_frames:true};
  if (p === '/api/trace') json = { items:[] };
  return { ok:true, json };
}
async function setup() {
  const steam = path.join(tmp, 'steam');
  process.env.DSH_WE_STEAM_ROOT = steam;
  const dir = path.join(steam, 'steamapps/workshop/content/431960/990003');
  fs.mkdirSync(dir, { recursive:true });
  fs.mkdirSync(path.join(steam,'steamapps/common/wallpaper_engine'), { recursive:true });
  fs.writeFileSync(path.join(dir,'project.json'), JSON.stringify({ title:'Isolated UI scene', type:'scene', file:'scene.json', preview:'preview.png' }));
  fs.writeFileSync(path.join(dir,'scene.json'), JSON.stringify({ general:{ clearcolor:'0.5 0.65 0.82', orthogonalprojection:{width:160,height:90}},objects:[] }));
  fs.writeFileSync(path.join(dir,'preview.png'), nativeImage.createFromBitmap(Buffer.from([195,165,115,255]),{width:1,height:1}).toPNG());
  // A large gallery exercises height constraints using local fixtures only.
  for(let i=0;i<48;i++) {
    const galleryDir=path.join(steam,'steamapps/workshop/content/431960',String(990100+i));
    fs.mkdirSync(galleryDir,{recursive:true});
    fs.writeFileSync(path.join(galleryDir,'project.json'),JSON.stringify({title:'Gallery '+String(i).padStart(2,'0')+' · 长标题壁纸用于检查省略与可读性',type:'web',file:'index.html',preview:'preview.png'}));
    fs.copyFileSync(path.join(dir,'preview.png'),path.join(galleryDir,'preview.png'));
    fs.writeFileSync(path.join(galleryDir,'index.html'),'<html><body style="margin:0;background:#47658c">Isolated wallpaper</body></html>');
  }
  const vendor = path.join(tmp,'vendor');
  fs.cpSync(path.join(client,'vendor/dsh-wallpaper-engine'), vendor, { recursive:true });
  const sourceFile = path.join(vendor,'lib/index.js');
  const source = fs.readFileSync(sourceFile,'utf8');
  const needle = 'return [...env, ...(reg ? [reg] : []), ...STEAM_PROBE_DIRS, ...wsl];';
  assert.ok(source.includes(needle), 'fixture must restrict discovery to its temporary Steam root');
  fs.writeFileSync(sourceFile, source.replace(needle,'return [...env];'));
  host = require(path.join(client,'electron/wallpaper-host')).createWallpaperHost({ dataDir:path.join(tmp,'wallpaper'), vendorDir:vendor, logger:{info(){},warn(){},error(){},debug(){}} });
  // Automation must not depend on whether another window becomes foreground.
  // This changes only the temporary fixture settings, not playback defaults.
  await host.request('PUT','/settings',{settingsVersion:7,pauseOnHidden:false,pauseOnBlur:false});
  // Production bridge and production channel names; only the service responses
  // are fixtures. No synthetic window.vigil or extra test bridge is injected.
  ipcMain.handle('wallpaper:request', async (_, {method,resource,body}) => {
    const started=Date.now(); wallpaperTrace('wallpaper-host-request',{method,resource});
    try { const result={ok:true,...await host.request(method,resource,body)}; wallpaperTrace('wallpaper-host-response',{method,resource,status:'ok',elapsedMs:Date.now()-started}); return result; }
    catch(e) { wallpaperTrace('wallpaper-host-response',{method,resource,status:'error',error:e.message,elapsedMs:Date.now()-started}); return {ok:false,err:e.message}; }
  });
  ipcMain.handle('api', (_, {method,path:p,body,base}) => fixtureApi(method,p,body,base));
  ipcMain.handle('app:instance-id',()=> 'ui-v2-test-process');
  ipcMain.handle('api:stream', async (event,{path:p,body}) => {
    if (p.includes('/api/creator/jobs/')) {
      const job=creatorJobById.get(p.split('/')[4]);
      if(job)event.sender.send('api:stream:event',{job_id:job.job_id,type:'snapshot',payload:job});
      await wait(30);return {ok:true};
    }
    requests.push({method:'STREAM',path:p,body});
    if (failure) { await wait(120); event.sender.send('api:stream:event',{type:'error',msg:'隔离测试错误'}); return {ok:false}; }
    const activeHistory=body.conversation_id===legacyConversationId?history:otherHistories.get(body.conversation_id);
    if(activeHistory) {
      activeHistory.push({role:'user',text:body.text,t:Date.now()});
      const conv=conversations.find(x=>x.id===body.conversation_id);
      if(conv && !conv.legacy && conv.title==='新对话')conv.title=String(body.text||'').trim().slice(0,48)||conv.title;
    }
    event.sender.send('api:stream:event',{type:'think',text:'检查布局'});
    await wait(120);
    for (const text of [answer.slice(0,100),answer.slice(100)]) { event.sender.send('api:stream:event',{type:'text',text}); await wait(120); }
    if(activeHistory) activeHistory.push({role:'assistant',text:answer,t:Date.now()});
    event.sender.send('api:stream:event',{type:'done'});
    return {ok:true};
  });
  let cfg={wizard_done:true,roots:[]};
  ipcMain.handle('app:version',()=> 'UI V2 round 2');
  ipcMain.handle('cfg:get',()=>cfg);
  ipcMain.handle('cfg:set',(_,patch)=> (cfg={...cfg,...patch}));
  ipcMain.handle('settings:open',(_,body)=>{requests.push({method:'IPC',path:'settings:open',body});return {ok:true};});
  ipcMain.handle('plugins:load',()=>({ok:true}));
  ipcMain.handle('local:defaults',()=>({roots:['C:/fixture'],commands:[],danger:['delete']}));
  ipcMain.handle('local:caps',()=>[]);
  ipcMain.handle('sidecar:status',()=>({status:'stopped'}));
  ipcMain.handle('token:get',()=> '已保存');
  ipcMain.handle('wallpaper:import',()=>({ok:true,canceled:true}));
  ipcMain.handle('attachment:chunk',()=>uploadFailure?{ok:false,err:'fixture upload failed'}:{ok:true});
  ipcMain.handle('wallpaper:directory',()=>({ok:true,canceled:true}));
  ipcMain.handle('updater:check',()=>({}));
  ipcMain.handle('auto:connect',()=>({ok:false}));
  ipcMain.on('win:minimize',()=>win && win.minimize());
  ipcMain.on('win:maximize',()=> { if(win)win.isMaximized()?win.unmaximize():win.maximize(); });
  ipcMain.on('win:close',()=>win && win.close());
  win = new BrowserWindow({show:true,frame:false,width:1400,height:900,webPreferences:{preload:path.join(client,'electron/preload.js'),sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
  win.webContents.on('preload-error',(_,file,error)=>errors.push('preload: '+file+' '+error.message));
  win.webContents.on('console-message',(_,level,message,line,sourceId)=>{
    if(level>=3) errors.push(message);
    wallpaperTrace('renderer-console',{level,message,line,sourceId});
  });
  for (const event of ['did-start-navigation','did-frame-finish-load','did-fail-load','render-process-gone','dom-ready','frame-created']) {
    win.webContents.on(event,(...args)=>wallpaperTrace('electron-'+event,{args:args.map(v=>v && typeof v==='object' ? (v.url||v.reason||v.errorCode||v.frame?.url||String(v)) : v)}));
  }
  win.webContents.on('destroyed',()=>wallpaperTrace('webcontents-destroyed'));
  if (wallpaperDiag) {
    try {
      win.webContents.debugger.attach('1.3');
      win.webContents.debugger.on('message',(_,method,params)=>{
        if (method.startsWith('Network.') || method.startsWith('Page.frame') || method==='Runtime.exceptionThrown' || method==='Log.entryAdded') {
          if (method.startsWith('Network.') && !/wallpaper|scene|project\.json|\.js(?:\?|$)|\.wasm(?:\?|$)/i.test(JSON.stringify(params))) return;
          wallpaperTrace('cdp-'+method,params);
        }
      });
      for (const method of ['Network.enable','Page.enable','Runtime.enable','Log.enable']) await win.webContents.debugger.sendCommand(method);
      await win.webContents.debugger.sendCommand('Page.addScriptToEvaluateOnNewDocument',{source:`(()=>{const log=(kind,data={})=>{try{console.info('[P8-WALLPAPER-DIAG]',JSON.stringify({kind,url:location.href,t:Date.now(),...data}))}catch(_){}};addEventListener('error',e=>log('page-error',{message:e.message,source:e.filename,line:e.lineno,column:e.colno}));addEventListener('unhandledrejection',e=>log('unhandled-rejection',{reason:String(e.reason)}));const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){const result=get.call(this,type,...args);if(/webgl/i.test(String(type)))log('webgl-context',{type,created:!!result,width:this.width,height:this.height,renderer:result&&(()=>{try{return result.getParameter(result.RENDERER)}catch(_){return ''}})()});return result};const raf=requestAnimationFrame;let first=true;window.requestAnimationFrame=function(cb){return raf.call(this,(ts)=>{if(first){first=false;log('first-animation-frame',{ts})}return cb(ts)})};let lastStatus;const seen=new WeakSet();const inspect=()=>{for(const f of document.querySelectorAll('iframe'))if(!seen.has(f)){seen.add(f);f.addEventListener('load',()=>log('iframe-load',{src:f.src}));f.addEventListener('error',()=>log('iframe-error',{src:f.src}));log('iframe-created',{src:f.src,connected:f.isConnected})}const s=document.querySelector('[data-status]');if(s&&s.textContent!==lastStatus){lastStatus=s.textContent;log('status-transition',{status:lastStatus,iframe:[...document.querySelectorAll('iframe')].map(f=>({src:f.src,connected:f.isConnected}))})}};new MutationObserver(inspect).observe(document,{subtree:true,childList:true,attributes:true,characterData:true});addEventListener('pagehide',()=>log('pagehide'));addEventListener('beforeunload',()=>log('beforeunload'));inspect()})();`});
      wallpaperTrace('cdp-attached');
    } catch (e) { wallpaperTrace('cdp-attach-error',{error:e.message}); }
  }
  await win.loadFile(path.join(src,'index.html'));
}
const ev = async s => { try { return await win.webContents.executeJavaScript(s, true); } catch (e) { console.error('Renderer eval failed:', s, e.message); throw e; } };
async function until(s, ms=15000) { const end=Date.now()+ms; while(Date.now()<end) { if(await ev('Boolean('+s+')'))return; await wait(60); } throw Error('Timeout: '+s); }
async function wallpaperDiagnosticSnapshot(stage) {
  if (!wallpaperDiag) return;
  wallpaperTrace('wallpaper-snapshot', {stage,requests:requests.filter(r=>String(r.path||'').includes('wallpaper')||String(r.path||'').includes('scene')).slice(-30)});
  try { wallpaperTrace('renderer-snapshot',{stage,detail:await ev(`(()=>({url:location.href,hidden:document.hidden,focused:document.hasFocus(),page:document.body.dataset.vigilPage,bodyClass:document.body.className,status:document.querySelector('[data-status]')?.textContent,iframes:[...document.querySelectorAll('iframe')].map(f=>({src:f.src,loading:f.loading,connected:f.isConnected,rect:(()=>{const r=f.getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height}})()})),resources:performance.getEntriesByType('resource').slice(-35).map(r=>({name:r.name,duration:r.duration,transferSize:r.transferSize})),scroll:{height:document.documentElement.scrollHeight,client:document.documentElement.clientHeight}}))()`) }); }
  catch(e) { wallpaperTrace('renderer-snapshot-error',{stage,error:e.message}); }
  for (const frame of win.webContents.mainFrame.frames) {
    try { wallpaperTrace('frame-snapshot',{stage,url:frame.url,detail:await frame.executeJavaScript(`(()=>({url:location.href,hidden:document.hidden,state:window.__wp?.getState?.(),stats:window.__wpStats?.frame?.(),canvases:[...document.querySelectorAll('canvas')].map(c=>({w:c.width,h:c.height,rect:(()=>{const r=c.getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height}})()}))}))()`) }); }
    catch(e) { wallpaperTrace('frame-snapshot-error',{stage,url:frame.url,error:e.message}); }
  }
}
async function wallpaperSceneRun() {
  await wallpaperDiagnosticSnapshot('before-fixture-selection');
  wallpaperTrace('fixture-select',{id:'990003',title:'Isolated UI scene'});
  let polling=false;
  const poll = wallpaperDiag ? setInterval(()=>{if(polling)return;polling=true;wallpaperDiagnosticSnapshot('during-first-frame').catch(e=>wallpaperTrace('poll-error',{error:e.message})).finally(()=>polling=false)},1000) : null;
  await ev(`document.querySelector('[data-id="990003"]').click()`);
  try { await until(`document.querySelector('[data-status]')?.textContent==='WebWallGL 实时渲染中'`,30000); }
  catch(e) {
    await wallpaperDiagnosticSnapshot('first-frame-timeout');
    console.log('Scene diagnostics: '+await ev(`JSON.stringify({status:document.querySelector('[data-status]')?.textContent,error:document.querySelector('[data-error]')?.textContent,hidden:document.hidden,focused:document.hasFocus(),iframe:document.querySelector('.vwp-media iframe')?.src})`));
    for(const frame of win.webContents.mainFrame.frames) {
      try { console.log('Frame diagnostics: '+await frame.executeJavaScript(`JSON.stringify({hidden:document.hidden,state:window.__wp?.getState?.(),stats:window.__wpStats?.frame?.()})`)); } catch(_) {}
    }
    throw e;
  } finally { if(poll)clearInterval(poll); }
  await wallpaperDiagnosticSnapshot('first-frame-reached');
}
async function shot(name) { await wait(100); fs.writeFileSync(path.join(shots,name+'.png'),(await win.webContents.capturePage()).toPNG()); }
async function chat() {
  // Independent legacy layout scenarios start with empty fixture storage.
  // Refresh/reopen retention is covered by test-p1-electron.js.
  await ev(`Object.keys(localStorage).filter(k=>k.startsWith('vigil.display.v1:')).forEach(k=>localStorage.removeItem(k));document.querySelector('#gnav [data-page="chat"]').click()`);
  await until(`document.querySelector('#ta') && document.querySelector('[data-session="${legacyConversationId}"]')`);
  if(await ev(`API.state.chatConversationId!==${JSON.stringify(legacyConversationId)}`)) {
    await ev(`document.querySelector('[data-session="${legacyConversationId}"]').click()`);
  }
  await until(`document.querySelector('#hero') && !document.querySelector('#hero').hidden`); await wait(100);
}
async function measure() {
  return ev(`(()=>{const rect=s=>{let r=document.querySelector(s).getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom}};return{viewport:{width:innerWidth,height:innerHeight},input:rect('.cbox'),composer:rect('.composer'),chat:rect('.chat'),hero:rect('#hero'),scroll:rect('#msgs'),context:getComputedStyle(document.querySelector('#rp')).display,overflow:document.documentElement.scrollWidth>innerWidth}})()`);
}
async function wallpaperLayout() {
  return ev(`(()=>{const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom}};const grid=document.querySelector('#vwp-grid'),view=document.querySelector('#view'),tile=document.querySelector('.vwp-tile');return{viewport:{width:innerWidth,height:innerHeight},view:rect('#view'),shell:rect('.vwp-shell'),grid:rect('#vwp-grid'),toolbar:rect('.vwp-toolbar'),gridScroll:grid.scrollHeight>grid.clientHeight,outerScroll:view.scrollHeight>view.clientHeight+1,overflow:document.documentElement.scrollWidth>innerWidth,panel:getComputedStyle(document.querySelector('#panel')).display,gutter:getComputedStyle(document.querySelector('#gPanel')).display,surface:getComputedStyle(document.querySelector('.vwp-sheet')).backgroundColor,text:getComputedStyle(tile).color,titleSize:getComputedStyle(tile.querySelector('strong')).fontSize,blur:getComputedStyle(document.querySelector('.vwp-sheet')).backdropFilter}})()`);
}
async function wallpaperSettingsChecks() {
  await until(`document.querySelectorAll('.vwp-tile').length===49`);
  for(const theme of ['light','dark']) {
    await ev(`document.documentElement.dataset.theme='${theme}'`);
    const m=await wallpaperLayout();
    assert.equal(m.panel,'none'); assert.equal(m.gutter,'none');
    assert.equal(m.outerScroll,false); assert.equal(m.gridScroll,true); assert.equal(m.overflow,false);
    assert.equal(m.titleSize,'14px'); assert.equal(m.blur,'none');
    assert.equal(m.surface,theme==='light'?'rgb(255, 255, 255)':'rgb(32, 38, 48)');
    assert.equal(m.text,theme==='light'?'rgb(32, 39, 51)':'rgb(239, 243, 248)');
    await shot('wallpaper-settings-'+theme); result('wallpaper settings '+theme+' contrast and gallery layout',m);
  }
  for(const [width,height] of [[1920,1080],[1280,800],[800,540],[600,650]]) {
    win.setSize(width,height); await wait(120);
    const m=await wallpaperLayout();
    assert.equal(m.overflow,false); assert.equal(m.outerScroll,false); assert.equal(m.gridScroll,true);
    assert.ok(m.grid.height>90); assert.ok(m.shell.bottom<=height-26+1);
    const toolbarBefore=m.toolbar.y;
    await ev(`document.querySelector('#vwp-grid').scrollTop=700`);
    assert.equal((await wallpaperLayout()).toolbar.y,toolbarBefore);
    await ev(`document.querySelector('#vwp-grid').scrollTop=0`);
    await shot('wallpaper-settings-'+width+'x'+height);
    result('wallpaper gallery viewport '+width+'x'+height,m);
  }
  win.setSize(900,420); await wait(120);
  assert.equal((await wallpaperLayout()).outerScroll,true);
  await ev(`document.querySelector('#view').scrollTop=9999`);
  assert.ok((await wallpaperLayout()).shell.bottom<=420-26+1);
  result('very short wallpaper window has accessible outer scroll');
  win.setSize(1400,900); await wait(120);
  await ev(`document.querySelector('#vwp-search').value='Gallery 07';document.querySelector('#vwp-search').dispatchEvent(new Event('input'))`);
  assert.equal(await ev(`document.querySelectorAll('.vwp-tile').length`),1);
  assert.ok(await ev(`document.querySelector('.vwp-tile').title.includes('Gallery 07')`));
  await ev(`document.querySelector('#vwp-search').value='';document.querySelector('#vwp-search').dispatchEvent(new Event('input'));document.querySelector('#vwp-filter').value='scene';document.querySelector('#vwp-filter').dispatchEvent(new Event('change'))`);
  assert.equal(await ev(`document.querySelectorAll('.vwp-tile').length`),1);
  await ev(`document.querySelector('#vwp-filter').value='all';document.querySelector('#vwp-filter').dispatchEvent(new Event('change'))`);
  for(const tab of ['appearance','playback','system','about']) {
    await ev(`document.querySelector('[data-tab="${tab}"]').click()`);
    assert.equal(await ev(`document.querySelector('#vwp-content').getAttribute('aria-labelledby')`),'vwp-tab-'+tab);
    assert.equal(await ev(`document.querySelector('#view').scrollHeight>document.querySelector('#view').clientHeight+1`),false);
  }
  await ev(`document.querySelector('[data-tab="system"]').click();document.querySelector('#vwp-dir').click();document.querySelector('[data-tab="library"]').click();document.querySelector('#vwp-import').click();document.querySelector('#vwp-refresh').click()`);
  await until(`document.querySelectorAll('.vwp-tile').length===49`);
  await ev(`document.documentElement.dataset.theme='light'`);
  result('wallpaper search/filter, all tabs, canceled import/directory and refresh');
}
async function main() {
  await app.whenReady(); fs.mkdirSync(shots,{recursive:true}); fs.mkdirSync(evidenceDir,{recursive:true}); await setup();
  for(const stream of [process.stdout,process.stderr]) {
    stream.emit('error',Object.assign(new Error('isolated disconnected-pipe fixture'),{code:'EPIPE'}));
  }
  result('isolated EPIPE simulation does not open a main-process error dialog');
  await until(`document.querySelector('#hero') && !document.querySelector('#hero').hidden`);
  await wait(400);
  gpu = { features:app.getGPUFeatureStatus(), webgl:await ev(`(()=>{const c=document.createElement('canvas'),g=c.getContext('webgl');if(!g)return 'unavailable';const d=g.getExtension('WEBGL_debug_renderer_info');return d?g.getParameter(d.UNMASKED_RENDERER_WEBGL):g.getParameter(g.RENDERER)})()`) };
  wallpaperTrace('environment',{electron:process.versions.electron,chrome:process.versions.chrome,platform:process.platform,gpu,predecessorState:{fixtureSelection:'not-started',visibleFrames:await ev(`document.querySelectorAll('iframe').length`),page:await ev(`document.body.dataset.vigilPage`),classes:await ev(`document.body.className`),resourceEntries:await ev(`performance.getEntriesByType('resource').length`)}});
  if(baseline) { await shot('before-light'); await ev(`document.querySelector('#themeBtn').click()`); await shot('before-dark'); return; }
  if (process.env.P8_WALLPAPER_ONLY === '1') {
    await ev(`document.querySelector('#gnav [data-page="wallpaper"]').click()`); await until(`document.querySelector('[data-id="990003"]')`);
    await wallpaperSceneRun(); console.log('PASS: isolated chat harness Wallpaper scene first frame'); return;
  }
  assert.equal(await ev(`document.querySelector('#sendBtn').disabled`),true);
  assert.equal(await ev(`document.querySelector('#rpBtn').getAttribute('aria-expanded')`),'false');
  assert.equal((await measure()).context,'none');
  assert.equal(await ev(`document.querySelector('#hero').textContent.includes('有什么可以帮你')`),true);
  result('empty session and default collapsed context',await measure());
  const type=await ev(`(()=>{const size=s=>getComputedStyle(document.querySelector(s)).fontSize;return{body:size('body'),input:size('#ta'),welcome:size('#hero h1'),button:size('#rpBtn'),model:size('#modelBtn'),sidebar:size('.phead h2'),zoom:getComputedStyle(document.body).zoom,transform:getComputedStyle(document.body).transform}})()`);
  assert.deepEqual(type,{body:'15px',input:'15px',welcome:'32px',button:'14px',model:'14px',sidebar:'14px',zoom:'1',transform:'none'});
  assert.equal(win.webContents.getZoomFactor(),1);
  assert.equal((await measure()).input.width,860);
  result('computed typography, 860px composer and no global scale',{...type,electronZoom:win.webContents.getZoomFactor()});
  const prefs=win.webContents.getLastWebPreferences();
  assert.equal(prefs.contextIsolation,true); assert.equal(prefs.nodeIntegration,false); assert.equal(prefs.sandbox,true);
  assert.equal(await ev(`API.runtime.kind`),'electron');
  assert.equal(await ev(`window.vigil.platform`),process.platform);
  assert.equal(await ev(`window.vigil.appVersion()`),'UI V2 round 2');
  await ev(`window.vigil.cfg.set({uiProbe:true})`);
  assert.equal(await ev(`window.vigil.cfg.get().then(c=>c.uiProbe)`),true);
  result('production preload, contextBridge, configuration IPC and security settings');
  await ev(`document.querySelector('#winMax').click()`); await until(`true`); await wait(100);
  assert.equal(win.isMaximized(),true);
  await ev(`document.querySelector('#winMax').click()`); await wait(100);
  assert.equal(win.isMaximized(),false);
  await ev(`document.querySelector('#winMin').click()`); await wait(100);
  assert.equal(win.isMinimized(),true);
  win.restore(); await wait(100);
  result('titlebar maximize/minimize via production preload IPC');
  await shot('after-light-empty');
  await ev(`document.querySelector('[data-hint]').click()`);
  assert.equal(await ev(`document.querySelector('#sendBtn').disabled`),false);
  await ev(`document.querySelector('#ta').value='   ';document.querySelector('#ta').dispatchEvent(new Event('input'));`);
  assert.equal(await ev(`document.querySelector('#sendBtn').disabled`),true);
  result('quick question and blank-content disabled state');
  await ev(`document.querySelector('#sessionsBtn').click()`);
  assert.equal(await ev(`getComputedStyle(document.querySelector('#panel')).display`),'none');
  await ev(`document.querySelector('#sessionsBtn').click();document.querySelector('#rpBtn').click()`);
  assert.equal((await measure()).context,'block');
  await shot('after-light-context');
  await ev(`document.querySelector('#rpBtn').click();document.querySelector('#themeBtn').click()`);
  await shot('after-dark-empty'); result('session list and context toggle, dark theme');
  await ev(`const ta=document.querySelector('#ta');ta.value='多行内容\\n'.repeat(30);ta.dispatchEvent(new Event('input'))`);
  assert.ok(await ev(`document.querySelector('#ta').clientHeight<=180 && document.querySelector('#ta').clientHeight>=120`));
  assert.equal(await ev(`getComputedStyle(document.querySelector('#ta')).overflowY`),'auto');
  await shot('after-dark-multiline'); result('textarea auto-height and max-height');
  await chat(); // Historical sessions are entered explicitly; startup stays on the welcome page.
  let count=requests.filter(r=>r.method==='STREAM').length;
  await ev(`document.querySelector('#ta').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',shiftKey:true,bubbles:true,cancelable:true}))`);
  assert.equal(requests.filter(r=>r.method==='STREAM').length,count);
  await ev(`const input=document.querySelector('#ta');input.value='换行检查';input.focus();input.setSelectionRange(input.value.length,input.value.length)`);
  win.webContents.sendInputEvent({type:'keyDown',keyCode:'Enter',modifiers:['shift']});
  win.webContents.sendInputEvent({type:'char',keyCode:'\r',modifiers:['shift']});
  win.webContents.sendInputEvent({type:'keyUp',keyCode:'Enter',modifiers:['shift']});
  await wait(80);
  assert.ok(await ev(`document.querySelector('#ta').value.includes('\\n')`));
  assert.equal(requests.filter(r=>r.method==='STREAM').length,count);
  await ev(`document.querySelector('#ta').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true}))`);
  assert.equal(requests.filter(r=>r.method==='STREAM').length,count);
  await ev(`document.querySelector('#ta').value='检查界面';document.querySelector('#ta').dispatchEvent(new Event('input'));document.querySelector('#ta').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}))`);
  await until(`document.querySelector('#sendBtn').classList.contains('is-sending')`);
  assert.equal(await ev(`document.querySelector('#hero').hidden`),true);
  assert.equal(await ev(`document.querySelector('#modelBtn').disabled`),true);
  await until(`!document.querySelector('#sendBtn').classList.contains('is-sending')`);
  assert.equal(requests.filter(r=>r.method==='STREAM').length,count+1);
  const sent = requests.filter(r=>r.method==='STREAM').at(-1).body;
  assert.match(sent.request_id, /^req_[a-z0-9-]+$/);
  assert.deepEqual({text:sent.text,src:sent.src},{text:'检查界面',src:'pc'});
  assert.equal(await ev(`document.querySelector('#sendBtn').disabled`),true);
  assert.equal(await ev(`document.querySelector('.codehead span').textContent`),'js');
  assert.ok(await ev(`U.md(${JSON.stringify('```\nfirst\nsecond\n```')},{codeActions:true}).includes('<code>first\\nsecond\\n</code>')`));
  result('Enter, Shift+Enter, IME, stream state and deduplicated IPC payload');
  await ev(`document.querySelector('#clearBtn').click()`);
  await until(`document.querySelector('.vigil-confirm[role="dialog"]')`);
  await ev(`document.querySelector('.vigil-confirm [data-choice="cancel"]').click()`);
  assert.equal(await ev(`document.querySelector('.vigil-confirm-backdrop')`),null);
  result('destructive history clear retains a themed in-app confirmation');
  // Clipboard write executes in the real Electron renderer, not a mocked copy handler.
  savedClipboard = Object.fromEntries(require('electron').clipboard.availableFormats().map(format => [format,require('electron').clipboard.readBuffer(format)]));
  win.show(); win.focus(); await wait(150);
  await ev(`document.querySelector('[data-copy-code]').click()`);
  await until(`document.querySelector('[data-copy-code]').textContent==='已复制'`);
  const copied = require('electron').clipboard.readText();
  assert.ok(copied.startsWith('const vigil = "ready";'));
  result('Markdown headings/lists/quotes/link/code and code copy');
  await ev(`document.querySelector('#msgs').scrollTop=document.querySelector('#msgs').scrollHeight`);
  await shot('after-dark-chat');
  await ev(`document.querySelector('#localBtn').click()`); await until(`!document.querySelector('#localPop').hidden && document.querySelector('#lpClose')`);
  await ev(`document.querySelector('#lpClose').click()`); result('existing safety control opens and closes');
  // Load history through the existing refresh entry and select a real session row.
  await ev(`document.querySelector('#reloadBtn').click()`); await until(`document.querySelector('#msgs .codeblock')`);
  assert.equal(await ev(`document.querySelector('#hero').hidden`),true);
  await ev(`document.querySelector('[data-session]').click()`);
  assert.equal(await ev(`document.querySelector('[data-session]').closest('.session-row').classList.contains('on')`),true);
  result('history restored and persistent conversation selection is interactive');
  await until(`document.querySelector('[data-session="${legacyConversationId}"]').textContent.includes('检查界面')`);
  const legacyHistoryText=await ev(`document.querySelector('#msgs').textContent`);
  assert.ok(legacyHistoryText.includes('检查界面'));
  const conversationLegacy=await ev(`API.state.chatConversationId`);
  await ev(`document.querySelector('#newTopicBtn').click()`);
  await until(`API.state.chatConversationId==='' && document.querySelector('#hero') && !document.querySelector('#hero').hidden`);
  assert.equal(await ev(`document.querySelector('#msgs').textContent`),'');
  await ev(`document.querySelector('#ta').value='A_ONLY';document.querySelector('#ta').dispatchEvent(new Event('input'));document.querySelector('#sendBtn').click()`);
  await until(`API.state.chatConversationId && API.state.chatConversationId!==${JSON.stringify(conversationLegacy)} && document.querySelector('#msgs').textContent.includes('A_ONLY') && document.querySelector('#sendBtn').getAttribute('aria-busy')==='false'`);
  const conversationA=await ev(`API.state.chatConversationId`);
  assert.equal(requests.filter(r=>r.method==='STREAM').at(-1).body.conversation_id,conversationA);
  await ev(`document.querySelector('#newTopicBtn').click()`);
  await until(`API.state.chatConversationId==='' && document.querySelector('#hero') && !document.querySelector('#hero').hidden`);
  assert.equal(await ev(`document.querySelector('#msgs').textContent`),'');
  await ev(`document.querySelector('#ta').value='B_ONLY';document.querySelector('#ta').dispatchEvent(new Event('input'));document.querySelector('#sendBtn').click()`);
  await until(`API.state.chatConversationId && API.state.chatConversationId!==${JSON.stringify(conversationA)} && document.querySelector('#msgs').textContent.includes('B_ONLY') && document.querySelector('#sendBtn').getAttribute('aria-busy')==='false'`);
  const conversationB=await ev(`API.state.chatConversationId`);
  assert.equal(requests.filter(r=>r.method==='STREAM').at(-1).body.conversation_id,conversationB);
  await ev(`document.querySelector('[data-session="${conversationA}"]').click()`);
  await until(`document.querySelector('#msgs').textContent.includes('A_ONLY')`);
  assert.equal(await ev(`document.querySelector('#msgs').textContent.includes('B_ONLY')`),false);
  await ev(`document.querySelector('#ta').value='A_CONTINUE';document.querySelector('#ta').dispatchEvent(new Event('input'));document.querySelector('#sendBtn').click()`);
  await until(`document.querySelector('#msgs').textContent.includes('A_CONTINUE') && document.querySelector('#sendBtn').getAttribute('aria-busy')==='false'`);
  assert.equal(requests.filter(r=>r.method==='STREAM').at(-1).body.conversation_id,conversationA);
  await ev(`document.querySelector('#creatorBtn').click()`);
  await ev(`document.querySelector('#ta').value='P6_SESSION_DRAFT';document.querySelector('#ta').dispatchEvent(new Event('input'))`);
  await until(`document.querySelector('#creatorBtn').classList.contains('on') && !document.querySelector('#sendBtn').disabled`);
  await ev(`document.querySelector('#sendBtn').click()`);
  await until(`document.querySelector('.crturn[data-crid="draft_p6_session"]')`);
  assert.ok(requests.some(r=>r.path==='/api/creator/jobs' && r.method==='POST' && r.body.conversation_id===conversationA));
  await ev(`document.querySelector('[data-session="${conversationB}"]').click()`);
  await until(`document.querySelector('#msgs').textContent.includes('B_ONLY')`);
  assert.equal(await ev(`document.querySelector('#msgs').textContent.includes('A_CONTINUE')`),false);
  assert.equal(await ev(`document.querySelectorAll('.crturn[data-crid="draft_p6_session"]').length`),0);
  assert.ok(requests.some(r=>r.path==='/api/creator/jobs' && r.method==='GET' && r.query.conversation_id===conversationB));
  await ev(`document.querySelector('#msgs').scrollTop=0`);
  await until(`document.querySelector('[data-session="${conversationB}"] .tx b').textContent==='B_ONLY'`);
  fs.writeFileSync(path.join(evidenceDir,'chat-b-isolated.png'),(await win.webContents.capturePage()).toPNG());
  await ev(`document.querySelector('[data-session="${conversationA}"]').click()`);
  await until(`document.querySelector('.crturn[data-crid="draft_p6_session"]')`);
  assert.equal(await ev(`document.querySelector('#msgs').textContent.includes('B_ONLY')`),false);
  fs.writeFileSync(path.join(evidenceDir,'chat-a-draft-restored.png'),(await win.webContents.capturePage()).toPNG());
  await ev(`document.querySelector('#creatorBtn').click();document.querySelector('#creatorBtn').click()`);
  await until(`document.querySelector('#creatorBtn').textContent.includes('关')`);
  await ev(`document.querySelector('[data-session="${conversationB}"]').click()`);
  await until(`document.querySelector('#msgs').textContent.includes('B_ONLY')`);
  await ev(`document.querySelector('[data-rename="${conversationB}"]').click()`);
  await ev(`document.querySelector('.session-title-edit').value='B 独立会话';document.querySelector('.session-row button[aria-label="保存标题"]').click()`);
  await until(`document.querySelector('[data-session="${conversationB}"]').textContent.includes('B 独立')`);
  win.webContents.reload();
  await until(`API.state.chatConversationId===${JSON.stringify(conversationB)} && document.querySelector('#msgs').textContent.includes('B_ONLY')`);
  result('A/B independent messages, scoped Creator draft restore, continuation, legacy label and renderer restart');
  await ev(`document.querySelector('#themeBtn').click()`); await shot('after-light-chat');
  await ev(`document.querySelector('#themeBtn').click()`);
  result('ordinary light-theme history and code rendering');
  // Inspect matched content edges and a fixed composer while scrolling long history.
  await ev(`document.querySelector('[data-session="${legacyConversationId}"]').click()`);
  await until(`API.state.chatConversationId===${JSON.stringify(legacyConversationId)}`);
  await ev(`Object.keys(localStorage).filter(k=>k.startsWith('vigil.display.v1:')).forEach(k=>localStorage.removeItem(k))`);
  const many=Array.from({length:40},(_,i)=>({role:i%2?'assistant':'user',text:i%2?answer:'问题 '+i,t:Date.now()-40000+i*1000}));
  history=many; await ev(`document.querySelector('#reloadBtn').click()`); await until(`document.querySelectorAll('#msgs .turn').length===40`);
  const align=await ev(`(()=>{const t=document.querySelector('#msgs .turn').getBoundingClientRect(),c=document.querySelector('.cbox').getBoundingClientRect();return{left:Math.abs(t.left-c.left),right:Math.abs(t.right-c.right),width:c.width}})()`);
  assert.ok(align.left<=2 && align.right<=2 && align.width<=860,JSON.stringify(align));
  const before=await measure(); await ev(`document.querySelector('#msgs').scrollTop=0`); await wait(80);
  assert.equal((await measure()).composer.y,before.composer.y); result('860px aligned content and composer outside message scroll',align);
  await shot('after-dark-long-history');
  // Failure paths and image-only attachment use the original send handler.
  history=[]; failure=true; await chat();
  await ev(`document.querySelector('#ta').value='错误场景';document.querySelector('#ta').dispatchEvent(new Event('input'));document.querySelector('#sendBtn').click()`);
  await until(`document.querySelector('#msgs').textContent.includes('隔离测试错误')`);
  assert.equal(await ev(`document.querySelector('#sendBtn').disabled`),true); result('stream error UI and send state recovery');
  await shot('after-error');
  history=[]; failure=false; await chat();
  const pngBase64=nativeImage.createFromBitmap(Buffer.from([100,150,200,255]),{width:1,height:1}).toPNG().toString('base64');
  await ev(`window.__fixtureFileInput=null;const original=document.createElement.bind(document);window.__originalCreate=original;document.createElement=function(tag){const n=original(tag);if(tag==='input'){n.click=()=>{window.__fixtureFileInput=n}}return n};document.querySelector('#clipBtn').click();document.createElement=window.__originalCreate;const file=new File([Uint8Array.from(atob('${pngBase64}'),c=>c.charCodeAt(0))],'fixture.png',{type:'image/png'});const files=new DataTransfer();files.items.add(file);window.__fixtureFileInput.files=files.files;window.__fixtureFileInput.onchange();`);
  await until(`!document.querySelector('#attach').hidden`);
  assert.equal(await ev(`document.querySelector('#sendBtn').disabled`),false);
  await ev(`document.querySelector('#atClr').click()`); assert.equal(await ev(`document.querySelector('#sendBtn').disabled`),true);
  result('attachment FileReader, preview, removal and image-only send eligibility');
  await ev(`window.__fixtureFileInput.onchange()`); await until(`!document.querySelector('#attach').hidden`);
  await until(`!document.querySelector('#sendBtn').disabled`);
  await ev(`document.querySelector('#sendBtn').click()`);
  await until(`document.querySelector('#sendBtn').classList.contains('is-sending')`);
  await until(`!document.querySelector('#sendBtn').classList.contains('is-sending')`);
  const imageSent = requests.filter(r=>r.method==='STREAM').at(-1).body;
  assert.match(imageSent.request_id, /^req_[a-z0-9-]+$/);
  assert.deepEqual({text:imageSent.text,img:imageSent.img,src:imageSent.src},{text:'',img:pngBase64,src:'pc'});
  result('image-only send through original stream IPC');
  await ev(`(()=>{window.confirm=()=>{throw new Error('native confirm forbidden')};window.__fixtureFileInput=null;const original=document.createElement.bind(document);window.__originalCreate=original;document.createElement=function(tag){const n=original(tag);if(tag==='input'){n.click=()=>{window.__fixtureFileInput=n}}return n};document.querySelector('#clipBtn').click();document.createElement=window.__originalCreate;const file=new File([new Uint8Array([0,0,0,24,102,116,121,112])],'fixture.mp4',{type:'video/mp4'});const files=new DataTransfer();files.items.add(file);window.__fixtureFileInput.files=files.files;window.__fixtureFileInput.onchange()})()`);
  await until(`document.querySelector('[data-video-choice="frames"]') && document.querySelector('[data-video-choice="original"]')`);
  assert.ok(await ev(`document.querySelector('#attach').textContent.includes('当前模型不支持直接理解完整视频')`));
  await ev(`document.querySelector('[data-video-choice="original"]').click()`);
  assert.equal(await ev(`document.querySelector('[data-video-choice="original"]').classList.contains('on')`),true);
  await ev(`document.querySelector('#atClr').click()`);
  result('video limits show inline keyframe/original choices without native confirm; cancel removes attachment');
  uploadFailure=true;
  await ev(`(()=>{window.__fixtureFileInput=null;const original=document.createElement.bind(document);document.createElement=function(tag){const n=original(tag);if(tag==='input')n.click=()=>{window.__fixtureFileInput=n};return n};document.querySelector('#clipBtn').click();document.createElement=original;const file=new File(['fixture'],'failure.pdf',{type:'application/pdf'});const files=new DataTransfer();files.items.add(file);window.__fixtureFileInput.files=files.files;window.__fixtureFileInput.onchange()})()`);
  await until(`!document.querySelector('#sendBtn').disabled`);
  await ev(`document.querySelector('#sendBtn').click()`);
  await until(`document.querySelector('.attachment-status.failed')`);
  assert.ok(await ev(`document.querySelector('.attachment-status').textContent.includes('fixture upload failed')`));
  await ev(`document.querySelector('#atClr').click()`); uploadFailure=false;
  result('attachment upload failure remains inline and does not claim chat success');
  // Current dev opens the model picker and delegates management to the
  // dedicated settings window via production IPC.
  await ev(`document.querySelector('#modelBtn').click()`);
  await until(`!document.querySelector('#modelPop').hidden`);
  await ev(`document.querySelector('#mqManage').click()`); await wait(80);
  assert.ok(requests.some(r=>r.path==='settings:open'&&r.body.section==='model'));
  await ev(`document.querySelector('#gnav [data-page="persona"]').click()`);
  await until(`API.state.page==='persona'`);
  assert.equal(await ev(`getComputedStyle(document.body).getPropertyValue('--chat-width').trim()`),'');
  assert.equal(await ev(`document.querySelector('.main').classList.contains('chatwrap')`),false);
  result('existing model settings navigation and non-chat scope');
  history=[]; await chat();
  const navCount=await ev(`document.querySelectorAll('#gnav .gbtn').length`);
  assert.ok(navCount>=7); result('global function entries retained',navCount);
  historyError=true;
  await ev(`document.querySelector('#reloadBtn').click()`);
  await until(`document.querySelector('#retryHistory')`);
  assert.equal(await ev(`document.querySelector('#hero').hidden`),true);
  assert.equal(await ev(`getComputedStyle(document.querySelector('#chatLoadState')).display`),'flex');
  await shot('round2-history-error');
  historyError=false;
  await ev(`document.querySelector('#retryHistory').click()`);
  await until(`!document.querySelector('#hero').hidden`);
  result('history failure is explicit and retry restores empty welcome');
  await ev(`document.querySelector('#gnav [data-page="persona"]').click()`);
  await until(`document.querySelectorAll('.persona-view .pcard').length===2`);
  assert.ok(requests.some(r=>r.path==='/api/persona'&&r.base==='cloud'));
  assert.equal(await ev(`getComputedStyle(document.querySelector('.pgrid')).display`),'flex');
  assert.equal(await ev(`getComputedStyle(document.querySelector('.pch b')).fontSize`),'16px');
  await shot('round2-persona-list');
  await ev(`document.querySelector('[data-edit="default"]').click()`);
  await until(`document.querySelector('[data-pf="name"]')?.value==='Vigil'`);
  await shot('round2-persona-editor');
  await ev(`document.querySelector('#pfSave').click()`);
  await until(`document.querySelector('#pfMsg').textContent.includes('已保存')`);
  const saved=requests.findLast(r=>r.path==='/api/persona/save');
  assert.equal(saved.base,'cloud'); assert.equal(saved.body.key,'default'); assert.equal(saved.body.src,'wechat_group');
  result('persona data, compact list, editor and existing cloud save IPC');
  await ev(`document.querySelector('#themeBtn').click();document.querySelector('#pfBack').click()`);
  await until(`document.querySelector('.persona-view .pcard')`);
  await shot('round2-persona-light');
  for(const [width,height] of [[900,700],[600,650]]) {
    win.setSize(width,height); await wait(120);
    assert.equal(await ev(`document.documentElement.scrollWidth>innerWidth`),false);
    assert.equal(await ev(`getComputedStyle(document.querySelector('.pft .btn')).fontSize`),'14px');
    await shot('round2-persona-'+width+'x'+height);
  }
  win.setSize(1400,900); await wait(100);
  await ev(`document.querySelector('#themeBtn').click()`);
  result('persona light/dark and narrow-window layouts');
  personaEmpty=true;
  await ev(`document.querySelector('#reloadBtn').click()`);
  await until(`document.querySelector('.persona-view .empty')?.textContent.includes('暂无人格配置')`);
  result('persona empty state is distinguished from service failure');
  personaEmpty=false;
  await ev(`document.querySelector('#panel [data-k="memory"]').click()`);
  await until(`document.querySelector('#memList')?.textContent.includes('隔离记忆条目')`);
  assert.equal(await ev(`document.querySelector('.persona-view')`),null);
  result('memory data loads and persona styles leave the subsection');
  await ev(`document.querySelector('#panel [data-k="kb"]').click()`);
  await until(`document.querySelector('#kbView')?.textContent.includes('fixture.md')`);
  result('knowledge-base data and document entry retained');
  await chat();
  // Exercise settings UI and the unmodified renderer/host with local fixtures.
  await ev(`document.querySelector('#gnav [data-page="wallpaper"]').click()`); await until(`document.querySelector('[data-id="990003"]')`);
  await wallpaperSettingsChecks();
  await wallpaperSceneRun();
  assert.equal(await ev(`document.body.classList.contains('vwp-active')`),true);
  const wallpaperMaterial=await wallpaperLayout();
  assert.equal(wallpaperMaterial.text,'rgb(239, 243, 248)');
  assert.equal(wallpaperMaterial.blur,'none');
  await shot('wallpaper-settings-active'); result('wallpaper-on settings uses readable tint without stacked blur',wallpaperMaterial);
  await ev(`document.querySelector('#vwp-pause').click()`); assert.equal(await ev(`document.querySelector('#vwp-pause').textContent`),'继续');
  await ev(`document.querySelector('#vwp-pause').click();document.querySelector('#vwp-import').click();document.querySelector('[data-tab="appearance"]').click()`);
  assert.ok(await ev(`Boolean(document.querySelector('[data-range="glassAlpha"]') && document.querySelector('[data-range="wallpaperBlur"]'))`));
  result('real wallpaper iframe/host, pause/resume, import entry and appearance settings');
  await chat(); await shot('after-wallpaper-empty');
  // Trigger an actual rotation callback after leaving the settings page. The
  // playback selection may change, but the shared chat view must stay intact.
  await ev(`document.querySelector('#gnav [data-page="wallpaper"]').click()`);
  await until(`document.querySelector('[data-tab="playback"]')`);
  await ev(`document.querySelector('[data-tab="playback"]').click();document.querySelector('#vwp-rotationInterval').value='10';document.querySelector('#vwp-rotationInterval').dispatchEvent(new Event('input'));window.__rotationCallback=null;window.__originalInterval=window.setInterval;window.setInterval=(fn,ms,...args)=>{if(ms===10000)window.__rotationCallback=fn;return window.__originalInterval(fn,ms,...args)};document.querySelector('#vwp-rotationEnabled').checked=true;document.querySelector('#vwp-rotationEnabled').dispatchEvent(new Event('change'));window.setInterval=window.__originalInterval;void 0`);
  assert.equal(await ev(`typeof window.__rotationCallback`),'function');
  await chat();
  const rotationBefore=(await host.request('GET','/settings')).data.settings.id;
  await ev(`window.__rotationCallback()`);
  await until(`document.querySelector('#hero') && !document.querySelector('#hero').hidden && API.state.page==='chat'`);
  await wait(250);
  assert.notEqual((await host.request('GET','/settings')).data.settings.id,rotationBefore);
  assert.ok(await ev(`Boolean(document.querySelector('#ta') && !document.querySelector('.vwp-shell'))`));
  await ev(`document.querySelector('#gnav [data-page="wallpaper"]').click()`);
  await until(`document.querySelector('#vwp-rotationEnabled')`);
  await ev(`document.querySelector('#vwp-rotationEnabled').checked=false;document.querySelector('#vwp-rotationEnabled').dispatchEvent(new Event('change'))`);
  await chat();
  result('background wallpaper rotation preserves the current chat page');
  const material=await ev(`(()=>{const h=getComputedStyle(document.querySelector('#hero')),c=getComputedStyle(document.querySelector('.cbox'));return{welcomeBackground:h.backgroundColor,welcomeBorder:h.borderWidth,welcomeBlur:h.backdropFilter,inputBlur:c.backdropFilter}})()`);
  assert.equal(material.welcomeBackground,'rgba(0, 0, 0, 0)'); assert.equal(material.welcomeBorder,'0px'); assert.equal(material.welcomeBlur,'none');
  result('wallpaper welcome has no card or backdrop blur',material);
  history=[{role:'assistant',text:answer,t:Date.now()}];
  await ev(`document.querySelector('#reloadBtn').click()`); await until(`document.querySelector('.codeblock')`); await shot('after-wallpaper-chat');
  result('wallpaper reading surface and unchanged chat DOM');
  for(const [width,height] of [[1280,800],[900,700],[600,650],[800,540]]) {
    win.setSize(width,height); await wait(120);
    const m=await measure(); assert.equal(m.overflow,false); assert.ok(m.input.x>=0 && m.input.x+m.input.width<=width); assert.ok(m.composer.bottom<=height);
    await ev(`document.querySelector('#rpBtn').click()`); assert.equal((await measure()).context,'block');
    await ev(`document.querySelector('#rpBtn').click()`);
    await shot('after-wallpaper-'+width+'x'+height); result('window '+width+'x'+height,m);
  }
  win.setSize(600,650); await wait(120);
  assert.equal(await ev(`getComputedStyle(document.querySelector('#panel')).display`),'none');
  await ev(`document.querySelector('#sessionsBtn').click()`); assert.equal(await ev(`getComputedStyle(document.querySelector('#panel')).display`),'flex');
  await shot('after-wallpaper-narrow-sessions');
  await ev(`document.querySelector('#sessionsBtn').click()`); assert.equal(await ev(`getComputedStyle(document.querySelector('#panel')).display`),'none');
  result('narrow window session overlay can be hidden and restored');
  win.setSize(1400,900); await wait(120);
  await ev(`document.querySelector('#gnav [data-page="persona"]').click()`);
  await until(`document.querySelector('.persona-view .pcard')`);
  await shot('round2-wallpaper-persona');
  await ev(`document.querySelector('#gnav [data-page="wallpaper"]').click()`);
  await until(`document.querySelector('#vwp-close')`);
  await ev(`document.querySelector('#vwp-close').click()`);
  await until(`!document.body.classList.contains('vwp-active')`);
  history=[]; await chat();
  await shot('round2-wallpaper-off');
  result('wallpaper persona material and disabling restores ordinary layout');
  assert.deepEqual(errors,[]); result('no renderer errors');
  // Real Electron without a bridge must show a failure, while an ordinary
  // browser UA without a bridge has an explicit, non-interactive preview.
  const clientWindow=win;
  for(const preview of [false,true]) {
    win=new BrowserWindow({show:true,width:1280,height:800,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false}});
    if(preview)win.webContents.setUserAgent('Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36');
    await win.loadFile(path.join(src,'index.html'));
    await until(preview ? `document.querySelector('#hero') && !document.querySelector('#hero').hidden` : `document.querySelector('#retryHistory')`);
    assert.equal(await ev(`API.runtime.kind`),preview?'browser-preview':'electron-missing-preload');
    if(preview)assert.ok(await ev(`document.querySelector('.chat-connection').textContent.includes('浏览器预览')`));
    else assert.ok(await ev(`document.querySelector('#chatLoadState').textContent.includes('preload 未加载')`));
    await ev(`document.querySelector('#ta').value='禁止发送';document.querySelector('#ta').dispatchEvent(new Event('input'))`);
    assert.equal(await ev(`document.querySelector('#sendBtn').disabled`),true);
    const n=requests.length;
    await ev(`document.querySelector('#ta').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}))`);
    assert.equal(requests.length,n);
    await shot(preview?'round2-browser-preview':'round2-missing-preload');
    result(preview?'browser UA preview placeholder and disabled send':'Electron missing-preload diagnostic and disabled send');
    win.destroy();
  }
  win=clientWindow;
}
const timeout=setTimeout(()=>{console.error('Test exceeded 120 seconds');app.exit(1)},120000);
main().then(()=>finish(0)).catch(e=>{console.error(e.stack);results.push({status:'failed',error:e.message});finish(1)});
async function finish(code) {
  clearTimeout(timeout);
  fs.writeFileSync(path.join(shots,baseline?'baseline.json':'results.json'),JSON.stringify({fixture:true,preload:'client/electron/preload.js (production, unchanged)',electron:process.versions.electron,chrome:process.versions.chrome,productionMain:false,realModelCalls:0,hardwareAcceleration:'Electron default enabled',gpu,results,errors,requests},null,2));
  if(win && !win.isDestroyed())win.destroy(); if(host)await host.close();
  if(savedClipboard) { const clipboard=require('electron').clipboard; clipboard.clear(); for(const [format,buffer] of Object.entries(savedClipboard))clipboard.writeBuffer(format,buffer); }
  const resolved=path.resolve(tmp);
  if(resolved.startsWith(path.resolve(os.tmpdir())+path.sep) && path.basename(resolved).startsWith('vigil-chat-v2-')) {
    try { fs.rmSync(resolved,{recursive:true,force:true}); } catch (_) { console.log('Retained locked temporary Electron cache: '+resolved); }
  }
  app.exit(code);
}
