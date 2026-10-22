// Real Electron renderer/preload with isolated IPC fixtures for plugin market.
// No Brain, installer, user configuration, credentials, or model API is used.
const {app,BrowserWindow,ipcMain}=require('electron');
const path=require('path'),os=require('os'),assert=require('assert/strict');
const client=path.resolve(__dirname,'..');
const tmp=require('fs').mkdtempSync(path.join(os.tmpdir(),'vigil-plugin-market-'));
app.setPath('userData',path.join(tmp,'userdata'));
let win,installed=false,installCalls=0,resolveInstall;
let streamRequest,resolveStream,conversationCreated=false;
let wallpaperState={installed:false,enabled:false};
const alerts=[];
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const ev=source=>win.webContents.executeJavaScript(source,true);
async function until(source){const end=Date.now()+10000;while(Date.now()<end){if(await ev('Boolean('+source+')'))return;await wait(30);}throw Error('Timeout: '+source);}
function response(method,raw){
  const p=new URL(raw,'http://fixture').pathname;
  if(p==='/api/persona')return{ok:true,status:200,json:{name:'Fixture',key:'default',templates:[]}};
  if(p==='/api/history')return{ok:true,status:200,json:{items:[],display_turns:[]}};
  if(p==='/api/conversations' && method==='POST') {
    conversationCreated=true;
    return {ok:true,status:200,json:{ok:true,item:{id:'00000000-0000-4000-8000-000000000011',title:'P11 stream',has_content:true}}};
  }
  if(p==='/api/conversations')return{ok:true,status:200,json:{ok:true,items:conversationCreated?[{id:'00000000-0000-4000-8000-000000000011',title:'P11 stream',has_content:true}]:[]}};
  if(p==='/api/stats')return{ok:true,status:200,json:{model:'fixture'}};
  if(p==='/api/plugins/frontend'){
    if(!wallpaperState.installed)return{ok:true,status:200,json:{version:1,plugins:[]}};
    const dir=path.resolve(client,'../market/plugins/wallpaper');
    return{ok:true,status:200,json:{version:wallpaperState.enabled?2:3,plugins:[{name:'wallpaper',enabled:wallpaperState.enabled,
      js:require('fs').readFileSync(path.join(dir,'frontend.js'),'utf8'),css:require('fs').readFileSync(path.join(dir,'frontend.css'),'utf8')}]}};
  }
  if(p==='/api/plugin/wallpaper/config')return{ok:true,status:200,json:{ok:true,config:{enabled:false,opacity:.7,scrim:.4,blur:0,fps:60,wallpaper_id:null}}};
  if(p==='/api/plugin/wallpaper/library')return{ok:true,status:200,json:{ok:true,wallpapers:[],we_path:null}};
  if(p==='/api/market/list')return{ok:true,status:200,json:{state:'ready',state_message:'插件市场已就绪',plugins:[{name:'fixture_plugin',version:'1.0',author:'fixture',desc:'isolated',tags:[],installed},{name:'wallpaper',version:'4.0.0',author:'limingyi2',desc:'Standalone Wallpaper Engine renderer fixture',tags:['Wallpaper Engine'],installed:false}],installed_count:installed?1:0}};
  if(p==='/api/market/refresh')return{ok:true,status:200,json:{ok:true,state:'ready',state_message:'插件市场已就绪'}};
  if(p==='/api/market/install'){
    installCalls++;
    return new Promise(resolve=>{resolveInstall=resolve;});
  }
  return{ok:true,status:200,json:{ok:true,items:[]}};
}
async function run(){
  ipcMain.handle('api',(_,req)=>response(req.method,req.path));
  for(const [name,value]of Object.entries({'app:version':'fixture','app:instance-id':'fixture-process','auto:connect':{ok:false},'sidecar:status':{status:'stopped'},'token:get':'fixture','cfg:get':{wizard_done:true,roots:[],base:'http://fixture',device_id:'fixture',src:'pc'}}))ipcMain.handle(name,()=>value);
  ipcMain.handle('plugins:release-resources',()=>({ok:true}));
  ipcMain.handle('plugins:load',async()=>{
    if(!wallpaperState.installed||!wallpaperState.enabled)return{ok:true,loaded:0,errors:[]};
    const js=require('fs').readFileSync(path.resolve(client,'../market/plugins/wallpaper/frontend.js'),'utf8');
    await win.webContents.executeJavaScript('(function(){const vp=window.VigilPlugin;const prev=vp._currentOwner;vp._currentOwner="wallpaper";try{'+js+'}finally{vp._currentOwner=prev;}})();');
    return{ok:true,loaded:1,errors:[]};
  });
  ipcMain.handle('api:stream',(_,req)=>{streamRequest=req;return new Promise(resolve=>{resolveStream=resolve;});});
  ipcMain.handle('cfg:set',()=>({wizard_done:true,roots:[],base:'http://fixture',device_id:'fixture',src:'pc'}));
  win=new BrowserWindow({show:false,width:1200,height:800,webPreferences:{preload:path.join(client,'electron/preload.js'),sandbox:true,contextIsolation:true,nodeIntegration:false}});
  win.webContents.on('console-message',(_,level,message)=>{if(level>=2)console.log('renderer:',message);});
  await win.loadFile(path.join(client,'src/index.html'));
  await until('document.querySelector("#gnav [data-page=market]")');
  assert.equal(await ev('document.querySelectorAll("#settingsBtn,#settingsOpen").length'),1);
  assert.equal(await ev('document.querySelectorAll(".li[data-k=plugin_market],.li[data-k=extensions]").length'),0);
  await until('document.querySelector("#ta")');
  assert.equal(await ev('getComputedStyle(document.querySelector("#gnav")).display !== "none"'),true);
  assert.equal(await ev('document.querySelectorAll("#gnav [data-page=market]").length'),1);
  assert.equal(await ev('document.querySelectorAll("#gnav [data-page]")[1].dataset.page'),'market');
  assert.equal(await ev('document.querySelector("#gnav [data-page=market] .i").getBoundingClientRect().width'),20);
  await ev(`window.chatInput = document.querySelector('#ta'); chatInput.value = 'P11 draft';
    API.state.chatConversationId = 'conversation-p11'; API.state.workspaceId = 'workspace-p11';
    window.chatNode = document.createElement('div'); chatNode.id = 'p11-stream';
    document.querySelector('#msgs').appendChild(chatNode);
    window.fixtureRun = setInterval(() => chatNode.textContent = String(Number(chatNode.textContent || 0) + 1), 20);
    document.querySelector('#gnav [data-page=market]').click()`);
  await until('document.querySelector("#pmGrid [data-action=\\"install\\"]")');
  await wait(100);
  assert.equal(await ev('chatNode.isConnected && Number(chatNode.textContent) > 0'),true);
  assert.equal(await ev('document.querySelector("#parkedChat").hidden'),true);
  await ev('document.querySelector("#gnav [data-page=chat]").click()');
  assert.equal(await ev('document.querySelector("#ta") === chatInput && chatInput.value === "P11 draft"'),true);
  assert.equal(await ev('API.state.chatConversationId'), 'conversation-p11');
  assert.equal(await ev('API.state.workspaceId'), 'workspace-p11');
  await ev('clearInterval(fixtureRun); document.querySelector("#gnav [data-page=market]").click()');
  await until('document.querySelector("#pmGrid [data-action=install]")');
  assert.equal(BrowserWindow.getAllWindows().length,1);
  console.log('PASS visible primary market navigation, 20px icon, live chat/draft/selection preserved, same window');
  assert.ok(await ev('document.querySelector("#pmGrid").textContent.toLowerCase().includes("wallpaper")'));
  console.log('PASS plugin market is connected and lists Wallpaper without local repo configuration');
  const clickInstall='document.querySelector("#pmGrid [data-action=\\"install\\"]").click()';
  await ev(clickInstall);
  await until('document.querySelector("#pmGrid [data-action=\\"install\\"]").disabled');
  assert.equal(installCalls,1);
  assert.equal(await ev('document.querySelector("#pmGrid [data-action=\\"install\\"]").textContent'),'安装中...');
  await ev(clickInstall);await wait(80);assert.equal(installCalls,1,'disabled install cannot issue a duplicate request');
  resolveInstall({ok:true,status:200,json:{ok:false,err:'fixture install rejected'}});
  await until('Array.from(document.querySelectorAll("#toast > div")).some(x=>x.textContent.includes("fixture install rejected"))');
  assert.equal(await ev('document.querySelector("#pmGrid [data-action=\\"install\\"]").disabled'),false);
  assert.equal(await ev('document.querySelector("#pmGrid [data-action=\\"install\\"]").textContent'),'安装');
  console.log('PASS install failure preserves backend error and restores retry state');
  await ev(clickInstall);await until('document.querySelector("#pmGrid [data-action=\\"install\\"]").disabled');
  assert.equal(installCalls,2);
  await ev('document.querySelector("#gnav [data-page=chat]").click();document.querySelector("#gnav [data-page=market]").click()');
  await until('document.querySelector("#pmGrid [data-action=install]")');
  assert.equal(await ev('document.querySelector("#pmGrid [data-action=install]").disabled'),true);
  await ev(clickInstall);assert.equal(installCalls,2,'navigation cannot create a parallel install');
  installed=true;resolveInstall({ok:true,status:200,json:{ok:true,name:'fixture_plugin',msg:'installed'}});
  await until('document.querySelector("#pmGrid [data-action=\\"uninstall\\"]")');
  assert.ok(await ev('document.querySelector("#pmGrid").textContent.includes("已安装")'));
  assert.equal(installCalls,2);
  console.log('PASS install success refreshes market/plugin state from server response');
  await ev('document.querySelector("#pmRefresh").click()');
  await until('document.querySelector("#pmGrid [data-action=uninstall]")');
  assert.equal(await ev('document.querySelector("#pmGrid").textContent.includes("已安装")'),true);
  await ev('document.querySelector("#gnav [data-page=chat]").click(); chatInput.value="P11 stream"; chatInput.dispatchEvent(new Event("input",{bubbles:true})); document.querySelector("#sendBtn").click()');
  const deadline=Date.now()+10000;
  while(!streamRequest && Date.now()<deadline)await wait(30);
  assert.ok(streamRequest,'real renderer send uses preload stream IPC');
  const emit=evt=>win.webContents.send('api:stream:event',{...evt,request_id:streamRequest.body.request_id,run_id:'run-p11'});
  emit({type:'agent_run',seq:1});
  await ev('document.querySelector("#gnav [data-page=market]").click()');
  await until('document.querySelector("#pmGrid")');
  emit({type:'text',seq:2,text:'Reply while market is open'});
  emit({type:'done',seq:3,text:'Reply while market is open'});
  resolveStream({ok:true});
  await wait(100);
  await until('document.querySelector("#parkedChat").textContent.includes("Reply while market is open")');
  await ev('document.querySelector("#gnav [data-page=chat]").click()');
  assert.equal(await ev('document.querySelector("#msgs").textContent.includes("Reply while market is open")'),true);
  assert.equal(await ev('API.state.chatConversationId'),'00000000-0000-4000-8000-000000000011');
  assert.equal(await ev('document.querySelector("#sendBtn").getAttribute("aria-busy")'),'false');
  await ev('document.querySelector("#gnav [data-page=market]").click()');
  await until('document.querySelector("#pmGrid [data-action=uninstall]")');
  console.log('PASS real Agent stream IPC completes while market is open and reply remains visible after returning');
  for (const width of [1200,720]) {
    win.setSize(width+2,800); await wait(100); win.setSize(width,800); await wait(200);
    assert.equal(await ev('API.state.page'),'market');
    assert.equal(await ev('document.querySelector("#view").scrollWidth <= document.querySelector("#view").clientWidth'),true);
    const screenshot=await win.webContents.capturePage();
    require('fs').writeFileSync(path.join(client,'../reports/p11-market-'+width+'.png'),screenshot.toPNG());
  }
  console.log('PASS installed state survives refresh and market fits 1200px/720px windows');
  const assetArg=process.argv.find(arg=>arg.startsWith('--wallpaper-assets='));
  if(assetArg){
    const assets=JSON.parse(require('fs').readFileSync(assetArg.split('=').slice(1).join('='),'utf8'));
    await ev('const css=document.createElement("style");css.textContent='+JSON.stringify(assets.css)+';document.head.appendChild(css);true');
    await ev(assets.js);
    await until('document.querySelector("#gnav [data-page=wallpaper]")');
    await ev('document.querySelector("#gnav [data-page=wallpaper]").click()');
    await until('document.querySelector("#wp4-grid")');
    assert.equal(await ev('document.querySelectorAll("#gnav [data-page=wallpaper]").length'),1);
    assert.equal(BrowserWindow.getAllWindows().length,1);
    console.log('PASS real downloaded Wallpaper frontend registers and opens its plugin page');
  }
  await ev(`localStorage.setItem('vigil.primary-nav.order.v1',JSON.stringify(['wallpaper','chat']))`);
  assert.equal(await ev('!!document.querySelector("#gnav [data-page=wallpaper]")'),false);
  wallpaperState={installed:true,enabled:true};
  await ev('window.VigilPlugin.refresh()');
  await until('!!document.querySelector("#gnav [data-page=wallpaper]")');
  assert.equal(await ev('!!document.getElementById("plugin-css-wallpaper")'),true);
  assert.equal(await ev('PAGES.find(p=>p.id==="wallpaper").__pluginOwner'),'wallpaper');
  console.log('PASS plugin installation and enable register the wallpaper page and stylesheet');

  wallpaperState.enabled=false;
  await ev('window.VigilPlugin.refresh()');
  await until('!document.querySelector("#gnav [data-page=wallpaper]")');
  assert.equal(await ev('PAGES.some(p=>p.id==="wallpaper")'),false);
  assert.equal(await ev('!!document.getElementById("plugin-css-wallpaper")'),false);
  assert.equal(await ev('!!document.getElementById("wallpaper-bg-layer")'),false);
  console.log('PASS disable unregisters page, CSS and Wallpaper DOM without clearing navigation storage');

  wallpaperState={installed:false,enabled:false};
  await ev('window.VigilPlugin.refresh()');
  assert.equal(await ev('PAGES.some(p=>p.id==="wallpaper")'),false);
  await win.webContents.executeJavaScript('location.reload()');
  await until('!!document.querySelector("#gnav [data-page=market]")');
  assert.equal(await ev('!!document.querySelector("#gnav [data-page=wallpaper]")'),false);
  assert.ok(await ev('localStorage.getItem("vigil.primary-nav.order.v1").includes("wallpaper")'));
  wallpaperState={installed:true,enabled:true};
  await win.webContents.executeJavaScript('location.reload()');
  await until('!!document.querySelector("#gnav [data-page=wallpaper]")');
  console.log('PASS uninstall and renderer restart do not resurrect stale navigation; enabled install restores on restart');
  wallpaperState={installed:false,enabled:false};
  await ev('window.VigilPlugin.refresh()');
  await until('!document.querySelector("#gnav [data-page=wallpaper]")');
  win.destroy();app.exit(0);
}
app.whenReady().then(run).catch(error=>{console.error(error.stack||error);if(win)win.destroy();app.exit(1);});
