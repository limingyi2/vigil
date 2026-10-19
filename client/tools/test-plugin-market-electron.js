// Real Electron renderer/preload with isolated IPC fixtures for plugin market.
// No Brain, installer, user configuration, credentials, or model API is used.
const {app,BrowserWindow,ipcMain}=require('electron');
const path=require('path'),os=require('os'),assert=require('assert/strict');
const client=path.resolve(__dirname,'..');
const tmp=require('fs').mkdtempSync(path.join(os.tmpdir(),'vigil-plugin-market-'));
app.setPath('userData',path.join(tmp,'userdata'));
let win,installed=false,marketConfigured=false,installCalls=0,resolveInstall;
const alerts=[];
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const ev=source=>win.webContents.executeJavaScript(source,true);
async function until(source){const end=Date.now()+10000;while(Date.now()<end){if(await ev('Boolean('+source+')'))return;await wait(30);}throw Error('Timeout: '+source);}
function response(method,raw){
  const p=new URL(raw,'http://fixture').pathname;
  if(p==='/api/persona')return{ok:true,status:200,json:{name:'Fixture',key:'default',templates:[]}};
  if(p==='/api/history')return{ok:true,status:200,json:{items:[],display_turns:[]}};
  if(p==='/api/stats')return{ok:true,status:200,json:{model:'fixture'}};
  if(p==='/api/plugins/frontend')return{ok:true,status:200,json:{version:1,plugins:[]}};
  if(p==='/api/market/list')return{ok:true,status:200,json:{state:marketConfigured?'ready':'not_configured',state_message:marketConfigured?'插件市场已就绪':'尚未配置本地插件市场仓库',plugins:marketConfigured?[{name:'fixture_plugin',version:'1.0',author:'fixture',desc:'isolated',tags:[],installed}]:[],installed_count:installed?1:0}};
  if(p==='/api/market/refresh')return{ok:true,status:200,json:{ok:false,state:'not_configured',err:'尚未配置本地插件市场仓库'}};
  if(p==='/api/market/install'){
    installCalls++;
    return new Promise(resolve=>{resolveInstall=resolve;});
  }
  return{ok:true,status:200,json:{ok:true,items:[]}};
}
async function run(){
  ipcMain.handle('api',(_,req)=>response(req.method,req.path));
  for(const [name,value]of Object.entries({'app:version':'fixture','app:instance-id':'fixture-process','plugins:load':{ok:true},'auto:connect':{ok:false},'sidecar:status':{status:'stopped'},'token:get':'fixture','cfg:get':{wizard_done:true,roots:[],base:'http://fixture',device_id:'fixture',src:'pc'},'wallpaper:request':{ok:true,data:{settings:{},wallpapers:[]}},'wallpaper-extension:state':{ok:true,installed:false,enabled:false}}))ipcMain.handle(name,()=>value);
  ipcMain.handle('plugin-ui:list',()=>[]);
  ipcMain.handle('api:stream',()=>({ok:false,err:'unused'}));
  ipcMain.handle('cfg:set',()=>({wizard_done:true,roots:[],base:'http://fixture',device_id:'fixture',src:'pc'}));
  win=new BrowserWindow({show:false,width:1200,height:800,webPreferences:{preload:path.join(client,'electron/preload.js'),sandbox:true,contextIsolation:true,nodeIntegration:false}});
  await win.loadFile(path.join(client,'src/index.html'));
  await until('document.querySelector("#gnav [data-page=\\"settings\\"]")');
  await ev('document.querySelector("#gnav [data-page=\\"settings\\"]").click()');
  await until('document.querySelectorAll(".settings-tile").length>0');
  assert.equal(await ev('Array.from(document.querySelectorAll(".settings-tile b")).filter(x=>x.textContent==="插件与扩展").length'),1);
  assert.equal(await ev('Array.from(document.querySelectorAll(".settings-tile b")).filter(x=>x.textContent==="壁纸与外观").length'),0);
  assert.equal(await ev('Array.from(document.querySelectorAll(".settings-tile b")).filter(x=>x.textContent==="桌面扩展").length'),0);
  console.log('PASS settings home has one generic plugin/extensions entry');
  await until('document.querySelector("#gnav [data-page=\\"ability\\"]")');
  await ev('document.querySelector("#gnav [data-page=\\"ability\\"]").click()');
  await until('document.querySelector(".li[data-k=\\"plugin_market\\"]")');
  assert.equal(await ev('document.querySelectorAll(".li[data-k=\\"wechat\\"]").length'),0,'Core settings no longer register a fixed WeChat Bridge page');
  await ev('document.querySelector(".li[data-k=\\"extensions\\"]").click()');
  await until('document.querySelector("#wallpaperInstall")');
  assert.ok(await ev('document.querySelector("#topbar").textContent.includes("插件与扩展")'));
  console.log('PASS generic extension manager remains reachable and displays installed-extension lifecycle controls');
  await ev('document.querySelector(".li[data-k=\\"plugin_market\\"]").click()');
  await until('document.querySelector("#pmStatus").textContent.includes("尚未配置")');
  await ev('document.querySelector("#pmRefresh").click()');
  await until('Array.from(document.querySelectorAll("#toast > div")).some(x=>x.textContent.includes("尚未配置"))');
  console.log('PASS market distinguishes missing configuration and reports refresh failure');
  marketConfigured=true;
  await ev('document.querySelector("#gnav [data-page=\\"chat\\"]").click()');
  await until('document.querySelector("#gnav [data-page=\\"ability\\"]")');
  await ev('document.querySelector("#gnav [data-page=\\"ability\\"]").click()');
  await until('document.querySelector(".li[data-k=\\"plugin_market\\"]")');
  await ev('document.querySelector(".li[data-k=\\"plugin_market\\"]").click()');
  await until('document.querySelector("#pmGrid [data-action=\\"install\\"]")');
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
  installed=true;resolveInstall({ok:true,status:200,json:{ok:true,name:'fixture_plugin',msg:'installed'}});
  await until('document.querySelector("#pmGrid [data-action=\\"uninstall\\"]")');
  assert.ok(await ev('document.querySelector("#pmGrid").textContent.includes("已安装")'));
  assert.equal(installCalls,2);
  console.log('PASS install success refreshes market/plugin state from server response');
  win.destroy();app.exit(0);
}
app.whenReady().then(run).catch(error=>{console.error(error.stack||error);if(win)win.destroy();app.exit(1);});
