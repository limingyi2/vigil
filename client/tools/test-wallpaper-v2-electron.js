// Full client DOM regression with the real upstream host; all persisted state is isolated.
const { app, BrowserWindow, ipcMain, nativeImage } = require('electron');
const fs = require('fs');
const path = require('path');
const root = process.env.VIGIL_TEST_CLIENT_ROOT || path.resolve(__dirname, '..');
const { createWallpaperHost } = require(path.join(root, 'electron/wallpaper-host'));
const os = require('os');
const assert = require('assert/strict');
const { spawnSync } = require('child_process');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-wallpaper-v2-'));
const p8Diag=process.env.P8_WALLPAPER_DIAG==='1';
const p8DiagFile=process.env.P8_WALLPAPER_DIAG_FILE||path.join(tmp,'wallpaper-webgl-diagnostic.jsonl');
function p8Trace(kind,data={}){if(p8Diag)fs.appendFileSync(p8DiagFile,JSON.stringify({t:Date.now(),monotonic:process.hrtime.bigint().toString(),kind,...data})+'\n')}
app.setPath('userData', path.join(tmp, 'electron'));
if(process.env.VIGIL_TEST_SOFTWARE_RENDERER!=='0')app.disableHardwareAcceleration();
app.commandLine.appendSwitch('enable-unsafe-swiftshader');
const steam = path.join(tmp, 'steam');
process.env.DSH_WE_STEAM_ROOT = steam;
fs.mkdirSync(path.join(steam, 'steamapps/common/wallpaper_engine'), { recursive: true });
// Restrict discovery to the synthetic Steam root, keeping the user's real library out of this regression.
const testVendor = path.join(tmp, 'vendor');
if (!process.env.VIGIL_TEST_CLIENT_ROOT) {
  fs.cpSync(path.resolve(__dirname, '../vendor/dsh-wallpaper-engine'), testVendor, { recursive: true });
  const hostSource = path.join(testVendor, 'lib/index.js');
  fs.writeFileSync(hostSource, fs.readFileSync(hostSource, 'utf8').replace(
    'return [...env, ...(reg ? [reg] : []), ...STEAM_PROBE_DIRS, ...wsl];', 'return [...env];'));
}
const host = createWallpaperHost({ dataDir: path.join(tmp, 'state'),
  ...(process.env.VIGIL_TEST_CLIENT_ROOT ? {} : { vendorDir: testVendor }),
  logger:{info(){},warn(){},error(){},debug(){}} });
const wait = ms => new Promise(r => setTimeout(r, ms));
let win;
async function main() {
  await app.whenReady();
  const png = nativeImage.createFromBitmap(Buffer.from([80, 160, 40, 255]), { width:1, height:1 }).toPNG();
  const workshop = path.join(steam, 'steamapps/workshop/content/431960');
  function project(id, obj, extra) {
    const dir = path.join(workshop, id); fs.mkdirSync(dir, {recursive:true});
    fs.writeFileSync(path.join(dir, 'project.json'), JSON.stringify({ preview:'preview.png', contentrating:'Everyone', ...obj }));
    fs.writeFileSync(path.join(dir, 'preview.png'), png); extra(dir); return dir;
  }
  const vidDir = project('990001', { title:'Fixture Video', type:'video', file:'test.mp4' }, () => {});
  assert.equal(spawnSync('ffmpeg', ['-v','error','-f','lavfi','-i','color=c=green:s=160x90:d=1','-c:v','libx264','-pix_fmt','yuv420p','-y',path.join(vidDir,'test.mp4')], {windowsHide:true}).status, 0);
  project('990002', {title:'Fixture Web', type:'web', file:'index.html'}, dir => {
    fs.mkdirSync(path.join(dir,'assets')); fs.writeFileSync(path.join(dir,'assets/image.png'), png);
    fs.writeFileSync(path.join(dir,'index.html'), '<html><body style="margin:0;background:#229966"><img src="assets/image.png"><script>window.wallpaperPropertyListener={applyUserProperties(){}};</script></body></html>');
  });
  project('990003', {title:'Fixture Scene', type:'scene', file:'scene.json'}, dir => {
    fs.writeFileSync(path.join(dir,'scene.json'), JSON.stringify({general:{clearcolor:'0.1 0.3 0.5', orthogonalprojection:{width:160,height:90}},objects:[]}));
  });
  ipcMain.handle('fixture:wallpaper', async (_, m, p, b) => {try{
    p8Trace('wallpaper-host-request',{method:m,resource:p});
    const r=await host.request(m,p,b);
    p8Trace('wallpaper-host-response',{method:m,resource:p,status:'ok'});
    if(p==='/inventory')r.data.wallpapers=r.data.wallpapers.filter(w=>/^99000[123]$/.test(w.id));
    return{ok:true,...r};
  }catch(e){p8Trace('wallpaper-host-response',{method:m,resource:p,status:'error',error:e.message});return{ok:false,err:e.message}}});
  ipcMain.handle('fixture:api', async (_, method, p) => {
    if(p==='/api/plugins/frontend') return {ok:true,json:{version:Date.now(),plugins:[{name:'wallpaper',enabled:true,css:'.main {background:white!important}',js:'throw new Error("old wallpaper must not execute")'}]}};
    if(p==='/api/persona') return {ok:true,json:{key:'default'}};
    if(p==='/api/history') return {ok:true,json:{items:[]}};
    return {ok:true,json:{}};
  });
  fs.writeFileSync(path.join(tmp,'preload.js'), `const{contextBridge,ipcRenderer}=require('electron');const noop=()=>{};contextBridge.exposeInMainWorld('vigil',{
    api:(m,p)=>ipcRenderer.invoke('fixture:api',m,p),appVersion:async()=>'0.1.6',
    wallpaper:{request:(m,p,b)=>ipcRenderer.invoke('fixture:wallpaper',m,p,b),importFile:async()=>({ok:true,canceled:true}),directory:async()=>({ok:true,canceled:true})},
    cfg:{get:async()=>({wizard_done:true}),set:async()=>({})},plugins:{load:async()=>({ok:true,version:Date.now()})},
    win:{minimize:noop,maximize:noop,close:noop,onState:noop},local:{onConfirm:noop,onStep:noop,onDone:noop},
    sidecar:{status:async()=>({status:'stopped'}),onState:noop},updater:{check:async()=>({})},
    token:{has:async()=>true},onStreamEvent:noop,openExternal:noop});`);
  await host.request('PUT', '/settings', { settingsVersion: 6, id: '990001' });
  await assert.rejects(host.importFile(path.join(vidDir, 'test.mp4')), /仅支持 PNG 和 JPG 图片/);
  win = new BrowserWindow({show:false,width:1200,height:900,webPreferences:{preload:path.join(tmp,'preload.js'),sandbox:true,contextIsolation:true,offscreen:true,backgroundThrottling:false}});
  win.webContents.on('console-message',(_,level,message,line,sourceId)=>{if(level>=2) console.log('[renderer]',message.slice(0,350));p8Trace('renderer-console',{level,message,line,sourceId});});
  for(const event of ['did-start-navigation','did-frame-finish-load','did-fail-load','render-process-gone','frame-created'])win.webContents.on(event,(...args)=>p8Trace('electron-'+event,{args:args.map(v=>v&&typeof v==='object'?(v.url||v.reason||v.errorCode||v.frame?.url||String(v)):v)}));
  await win.loadFile(path.join(root,'src/index.html'));
  const renderer=await win.webContents.executeJavaScript(`(()=>{const c=document.createElement('canvas'),g=c.getContext('webgl');if(!g)return 'WebGL unavailable';const d=g.getExtension('WEBGL_debug_renderer_info');return d?g.getParameter(d.UNMASKED_RENDERER_WEBGL):g.getParameter(g.RENDERER)})()`);
  console.log('[gpu] WebGL renderer='+renderer);
  const ev = s => win.webContents.executeJavaScript(s);
  async function until(s, ms=15000) {
    for(let i=0;i<ms/100;i++){if(await ev('Boolean(' + s + ')'))return;await wait(100)}
    throw Error('Timeout: '+s);
  }
  await until(`document.querySelector('[data-page="wallpaper"]')`);
  await ev(`document.querySelector('[data-page="wallpaper"]').click()`);
  await until(`document.querySelectorAll('.vwp-tile').length===2`);
  assert.equal(await ev(`!document.querySelector('[data-id="990001"]') && ![...document.querySelector('#vwp-filter').options].some(o=>o.value==='video')`), true);
  assert.equal(await ev(`document.getElementById('vwp-enabled').checked===false && !document.body.classList.contains('vwp-active')`), true);
  assert.equal((await host.request('GET', '/settings')).data.settings.id, '');
  assert.equal(await ev(`document.getElementById('plugin-css-wallpaper')===null`), true);
  await ev(`document.querySelector('[data-id="990002"]').click()`);
  await until(`document.querySelector('[data-status]')?.textContent==='网页壁纸运行中'`,25000);
  assert.equal(await ev(`document.getElementById('vwp-enabled').checked`),true);
  await ev(`document.querySelector('[data-tab="appearance"]').click();const slider=document.querySelector('[data-range="glassAlpha"]');slider.value='41';slider.dispatchEvent(new Event('input'));`);
  await wait(400);
  assert.equal((await host.request('GET','/settings')).data.settings.glassAlpha,70);
  await ev(`document.querySelector('[data-tab="library"]').click()`);
  assert.equal(await ev(`document.querySelector('.vwp-stage').getBoundingClientRect().width===innerWidth && document.querySelector('.vwp-stage').getBoundingClientRect().height===innerHeight`),true);
  await wait(250);
  const shots=path.resolve(__dirname,'../_shots');
  fs.mkdirSync(shots,{recursive:true});
  fs.writeFileSync(path.join(shots,'wallpaper-v2-code14.png'),(await win.webContents.capturePage()).toPNG());
  assert.equal((await host.request('GET','/settings')).data.settings.id,'990002');
  const epoch = await ev(`performance.timeOrigin`);
  await wait(11500); // The old timestamp-based backend reload happens at ten seconds.
  assert.equal(await ev(`performance.timeOrigin`),epoch);
  assert.equal(await ev(`document.body.classList.contains('vwp-active')`),true);
  await ev(`document.querySelector('[data-page="chat"]').click()`);
  assert.equal(await ev(`document.body.classList.contains('vwp-active')`),true);
  const chatGlass = await ev(`(()=>{const rgb=e=>getComputedStyle(e).backgroundColor;return {right:rgb(document.querySelector('.rp')),rightCard:rgb(document.querySelector('.rp .card')),composer:rgb(document.querySelector('.composer')),input:rgb(document.querySelector('.cbox'))}})()`);
  assert.notEqual(chatGlass.right,'rgb(250, 250, 247)');
  assert.notEqual(chatGlass.rightCard,'rgb(255, 255, 255)');
  if(process.env.P8_DIAG_SKIP_CHAT_MATERIAL!=='1')assert.notEqual(chatGlass.composer,'rgba(0, 0, 0, 0)');
  if(process.env.P8_DIAG_SKIP_CHAT_MATERIAL!=='1')assert.notEqual(chatGlass.input,'rgb(255, 255, 255)');
  fs.writeFileSync(path.join(shots,'wallpaper-chat-code14.png'),(await win.webContents.capturePage()).toPNG());
  await win.reload();
  await until(`document.querySelector('.vwp-stage iframe')`);
  await ev(`document.querySelector('[data-page="wallpaper"]').click()`);
  await until(`document.querySelector('[data-status]')?.textContent==='网页壁纸运行中'`,25000);
  assert.equal(await ev(`document.documentElement.style.getPropertyValue('--vwp-alpha')`),'0.7');
  await ev(`document.querySelector('[data-page="wallpaper"]').click()`);
  await until(`document.querySelector('[data-id="990002"]')`);
  await ev(`document.querySelector('[data-id="990003"]').click()`);
  p8Trace('fixture-select',{id:'990003',stage:'standalone'});
  if(p8Diag){const started=Date.now();const poll=setInterval(async()=>{try{const detail=await ev(`(()=>({status:document.querySelector('[data-status]')?.textContent,hidden:document.hidden,focused:document.hasFocus(),iframes:[...document.querySelectorAll('iframe')].map(f=>({src:f.src,connected:f.isConnected})),frames:performance.getEntriesByType('resource').slice(-15).map(r=>r.name)}))()`);p8Trace('standalone-poll',{elapsedMs:Date.now()-started,detail})}catch(e){p8Trace('standalone-poll-error',{error:e.message})}},1000);try{await until(`document.querySelector('[data-status]')?.textContent==='WebWallGL 实时渲染中'`,25000)}catch(e){p8Trace('standalone-timeout',{error:e.message});throw e}finally{clearInterval(poll)}}else await until(`document.querySelector('[data-status]')?.textContent==='WebWallGL 实时渲染中'`,25000);
  assert.equal((await host.request('GET','/settings')).data.settings.id,'990003');
  await ev(`document.getElementById('vwp-close').click()`);
  await until(`!document.body.classList.contains('vwp-active')`);
  console.log('PASS: standalone videos hidden, full viewport, checkbox, persistence, navigation, timestamp backend >10s, reload recovery, strict web, WebGL scene first frame, disable');
}
main().then(()=>cleanup(0),e=>{console.error(e);cleanup(1)});
function cleanup(code){win?.destroy();host.close();app.exit(code)}
