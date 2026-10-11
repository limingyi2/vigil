// Real Electron renderer + local wallpaper host, with isolated API/IPC fixtures.
// No Brain, model API, production config, history or credential store is opened.
const { app, BrowserWindow, ipcMain, nativeImage } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');
const assert = require('assert/strict');
const client = path.resolve(__dirname, '..');
const baseline = process.argv.includes('--baseline');
const src = baseline ? path.join(client, '_shots/ui-v2/baseline-src/src') : path.join(client, 'src');
const shots = path.join(client, '_shots/ui-v2');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-chat-v2-'));
app.setPath('userData', path.join(tmp, 'electron'));
app.commandLine.appendSwitch('enable-unsafe-swiftshader');
const results = [], errors = [], requests = [];
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
let win, host, history = [], failure = false, historyError = false, personaEmpty = false;
let savedClipboard;
let gpu;
const answer = '# 执行结果\n\n这是一段用于检查长文本排版的回复。' + '内容应有舒适的行高和留白。'.repeat(18) +
  '\n\n## 下一步\n\n- 保留本地工具\n- 检查聊天布局\n\n> 每一步都有清晰的结果。\n\n[说明链接](https://example.com)\n\n```js\nconst vigil = "ready";\nconsole.log(vigil);\n```';
function result(name, detail) { results.push({ name, status:'passed', ...(detail ? { detail } : {}) }); console.log('PASS ' + name); }
function fixtureApi(method, raw, body, base) {
  const url = new URL(raw, 'http://fixture'); const p = url.pathname;
  requests.push({ method, path:p, body, base });
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
  if (p === '/api/plugins/frontend') json = { version:1, plugins:[] };
  if (p === '/api/history') json = { items:history };
  if (p === '/api/history/clear') history = [];
  if (p === '/api/models') json = { current:{ model:'mimo-v2.6-flash', api_key_set:true }, providers:[], models:[] };
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
  ipcMain.handle('wallpaper:request', async (_, {method,resource,body}) => { try { return {ok:true,...await host.request(method,resource,body)}; } catch(e) { return {ok:false,err:e.message}; } });
  ipcMain.handle('api', (_, {method,path:p,body,base}) => fixtureApi(method,p,body,base));
  ipcMain.handle('api:stream', async (event,{path:p,body}) => {
    requests.push({method:'STREAM',path:p,body});
    if (failure) { await wait(120); event.sender.send('api:stream:event',{type:'error',msg:'隔离测试错误'}); return {ok:false}; }
    history.push({role:'user',text:body.text,t:Date.now()});
    event.sender.send('api:stream:event',{type:'think',text:'检查布局'});
    await wait(120);
    for (const text of [answer.slice(0,100),answer.slice(100)]) { event.sender.send('api:stream:event',{type:'text',text}); await wait(120); }
    history.push({role:'assistant',text:answer,t:Date.now()});
    event.sender.send('api:stream:event',{type:'done'});
    return {ok:true};
  });
  let cfg={wizard_done:true,roots:[]};
  ipcMain.handle('app:version',()=> 'UI V2 round 2');
  ipcMain.handle('cfg:get',()=>cfg);
  ipcMain.handle('cfg:set',(_,patch)=> (cfg={...cfg,...patch}));
  ipcMain.handle('plugins:load',()=>({ok:true}));
  ipcMain.handle('local:defaults',()=>({roots:['C:/fixture'],commands:[],danger:['delete']}));
  ipcMain.handle('local:caps',()=>[]);
  ipcMain.handle('sidecar:status',()=>({status:'stopped'}));
  ipcMain.handle('token:get',()=> '已保存');
  ipcMain.handle('wallpaper:import',()=>({ok:true,canceled:true}));
  ipcMain.handle('wallpaper:directory',()=>({ok:true,canceled:true}));
  ipcMain.handle('updater:check',()=>({}));
  ipcMain.handle('auto:connect',()=>({ok:false}));
  ipcMain.on('win:minimize',()=>win && win.minimize());
  ipcMain.on('win:maximize',()=> { if(win)win.isMaximized()?win.unmaximize():win.maximize(); });
  ipcMain.on('win:close',()=>win && win.close());
  win = new BrowserWindow({show:true,frame:false,width:1400,height:900,webPreferences:{preload:path.join(client,'electron/preload.js'),sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
  win.webContents.on('preload-error',(_,file,error)=>errors.push('preload: '+file+' '+error.message));
  win.webContents.on('console-message',(_,level,message)=>{ if(level>=3) errors.push(message); });
  await win.loadFile(path.join(src,'index.html'));
}
const ev = s => win.webContents.executeJavaScript(s, true);
async function until(s, ms=15000) { const end=Date.now()+ms; while(Date.now()<end) { if(await ev('Boolean('+s+')'))return; await wait(60); } throw Error('Timeout: '+s); }
async function shot(name) { await wait(100); fs.writeFileSync(path.join(shots,name+'.png'),(await win.webContents.capturePage()).toPNG()); }
async function chat() { await ev(`document.querySelector('#gnav [data-page="chat"]').click()`); await until(`document.querySelector('#ta') && document.querySelector('#hero') && !document.querySelector('#hero').hidden`); await wait(100); }
async function measure() {
  return ev(`(()=>{const rect=s=>{let r=document.querySelector(s).getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom}};return{viewport:{width:innerWidth,height:innerHeight},input:rect('.cbox'),composer:rect('.composer'),chat:rect('.chat'),hero:rect('#hero'),scroll:rect('#msgs'),context:getComputedStyle(document.querySelector('#rp')).display,overflow:document.documentElement.scrollWidth>innerWidth}})()`);
}
async function main() {
  await app.whenReady(); fs.mkdirSync(shots,{recursive:true}); await setup();
  await until(`document.querySelector('#hero') && !document.querySelector('#hero').hidden`);
  await wait(400);
  gpu = { features:app.getGPUFeatureStatus(), webgl:await ev(`(()=>{const c=document.createElement('canvas'),g=c.getContext('webgl');if(!g)return 'unavailable';const d=g.getExtension('WEBGL_debug_renderer_info');return d?g.getParameter(d.UNMASKED_RENDERER_WEBGL):g.getParameter(g.RENDERER)})()`) };
  if(baseline) { await shot('before-light'); await ev(`document.querySelector('#themeBtn').click()`); await shot('before-dark'); return; }
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
  assert.deepEqual(requests.filter(r=>r.method==='STREAM').at(-1).body,{text:'检查界面',src:'pc'});
  assert.equal(await ev(`document.querySelector('#sendBtn').disabled`),true);
  assert.equal(await ev(`document.querySelector('.codehead span').textContent`),'js');
  assert.ok(await ev(`U.md(${JSON.stringify('```\nfirst\nsecond\n```')},{codeActions:true}).includes('<code>first\\nsecond\\n</code>')`));
  result('Enter, Shift+Enter, IME, stream state and unchanged IPC payload');
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
  await ev(`document.querySelector('[data-goto]').click()`);
  assert.equal(await ev(`document.querySelector('[data-goto]').classList.contains('on')`),true);
  result('history restored and session selection preserved');
  await ev(`document.querySelector('#themeBtn').click()`); await shot('after-light-chat');
  await ev(`document.querySelector('#themeBtn').click()`);
  result('ordinary light-theme history and code rendering');
  // Inspect matched content edges and a fixed composer while scrolling long history.
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
  await ev(`document.querySelector('#sendBtn').click()`); await until(`!document.querySelector('#sendBtn').classList.contains('is-sending')`);
  assert.deepEqual(requests.filter(r=>r.method==='STREAM').at(-1).body,{text:'',img:pngBase64,src:'pc'});
  result('image-only send through original stream IPC');
  // Existing settings route is used; no new model-request contract is introduced.
  await ev(`document.querySelector('#modelBtn').click()`); await until(`API.state.page==='system'`);
  await until(`document.querySelector('#olModel')`);
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
  // Exercise the unmodified wallpaper plugin and host with a synthetic scene.
  await ev(`document.querySelector('#gnav [data-page="wallpaper"]').click()`); await until(`document.querySelector('[data-id="990003"]')`);
  await ev(`document.querySelector('[data-id="990003"]').click()`);
  try { await until(`document.querySelector('[data-status]')?.textContent==='WebWallGL 实时渲染中'`,30000); }
  catch(e) {
    console.log('Scene diagnostics: '+await ev(`JSON.stringify({status:document.querySelector('[data-status]')?.textContent,error:document.querySelector('[data-error]')?.textContent,hidden:document.hidden,focused:document.hasFocus(),iframe:document.querySelector('.vwp-media iframe')?.src})`));
    for(const frame of win.webContents.mainFrame.frames) {
      try { console.log('Frame diagnostics: '+await frame.executeJavaScript(`JSON.stringify({hidden:document.hidden,state:window.__wp?.getState?.(),stats:window.__wpStats?.frame?.()})`)); } catch(_) {}
    }
    throw e;
  }
  assert.equal(await ev(`document.body.classList.contains('vwp-active')`),true);
  await ev(`document.querySelector('#vwp-pause').click()`); assert.equal(await ev(`document.querySelector('#vwp-pause').textContent`),'继续');
  await ev(`document.querySelector('#vwp-pause').click();document.querySelector('#vwp-import').click();document.querySelector('[data-tab="appearance"]').click()`);
  assert.ok(await ev(`Boolean(document.querySelector('[data-range="glassAlpha"]') && document.querySelector('[data-range="wallpaperBlur"]'))`));
  result('real wallpaper iframe/host, pause/resume, import entry and appearance settings');
  await chat(); await shot('after-wallpaper-empty');
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
