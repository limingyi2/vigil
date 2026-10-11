// Read-only compatibility probe of an installed WE sample; all caches/config are temporary.
const {app,BrowserWindow} = require('electron');
const fs=require('fs'), path=require('path'),os=require('os'),assert=require('assert/strict');
const {createWallpaperHost}=require('../electron/wallpaper-host');
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'vigil-real-scene-'));
app.setPath('userData',path.join(tmp,'electron'));
if(process.env.VIGIL_TEST_SOFTWARE_RENDERER!=='0')app.disableHardwareAcceleration();
app.commandLine.appendSwitch('enable-unsafe-swiftshader');
const host=createWallpaperHost({dataDir:path.join(tmp,'state'),logger:{info(){},warn(){},error(){},debug(){}}});let win;
async function main(){
  await app.whenReady();
  const inv=await host.request('GET','/inventory');
  const w=inv.data.wallpapers.filter(w=>w.sceneLive && w.scenePkgBytes>0 && String(w.contentrating).toLowerCase()==='everyone').sort((a,b)=>a.scenePkgBytes-b.scenePkgBytes)[0];
  assert(w,'An installed scene.pkg rated Everyone is required for this local probe');
  const base=inv.base;
  const params=new URLSearchParams({type:'scene',src:w.sceneLiveSrc,mediaBase:base+'/wallpaper-engine/scene-files',fit:'cover',sceneFps:'30',renderDpr:'0.8',muted:'true'});
  if(inv.data.weAssetsAvailable)params.set('localAssets','1');
  const src=base+'/wallpaper-engine/scene-live/index.html?'+params;
  fs.writeFileSync(path.join(tmp,'index.html'), '<iframe style="position:fixed;inset:0;width:100%;height:100%;border:0" src="'+src.replaceAll('&','&amp;')+'"></iframe>');
  win=new BrowserWindow({show:false,width:1000,height:650,webPreferences:{sandbox:true,contextIsolation:true,offscreen:true,backgroundThrottling:false}});
  await win.loadFile(path.join(tmp,'index.html'));
  const renderer=await win.webContents.executeJavaScript(`(()=>{const c=document.createElement('canvas'),g=c.getContext('webgl');if(!g)return 'WebGL unavailable';const d=g.getExtension('WEBGL_debug_renderer_info');return d?g.getParameter(d.UNMASKED_RENDERER_WEBGL):g.getParameter(g.RENDERER)})()`);
  console.log('[gpu] WebGL renderer='+renderer);
  await win.webContents.executeJavaScript(`window.addEventListener('message',e=>{if(e.data?.vigilWallpaper)window.sceneState=e.data;});`);
  let state;
  for(let i=0;i<600;i++){
    state=await win.webContents.executeJavaScript('window.sceneState');
    if(state?.fallback) {console.log(JSON.stringify((await host.request('GET','/diag-log')).data));break;}
    if(state?.state?.running && state?.state?.fps>0)break;
    await new Promise(r=>setTimeout(r,100));
  }
  assert(!state?.fallback && state?.state?.running && state?.state?.fps>0,'Real installed scene must produce WebGL frames: '+JSON.stringify(state));
  assert.equal(state.state.type,'scene');
  await new Promise(r=>setTimeout(r,1500));
  const diagnostics=(await host.request('GET','/diag-log')).data.entries;
  assert(diagnostics.some(e=>e.msg.includes('renderer started:')),'Must actually load and start the scene renderer');
  fs.mkdirSync(path.resolve(__dirname,'../_shots'),{recursive:true});
  fs.writeFileSync(path.resolve(__dirname,'../_shots/wallpaper-real-scene-code14.png'),(await win.webContents.capturePage()).toPNG());
  console.log('PASS: installed scene.pkg ('+Math.round(w.scenePkgBytes/1048576)+'MB) loaded; actual renderer started; fps='+Math.round(state.state.fps));
}
main().then(()=>finish(0),e=>{console.error(e);finish(1)});
function finish(code){win?.destroy();host.close();app.exit(code)}
