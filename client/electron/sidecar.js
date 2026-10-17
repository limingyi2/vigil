'use strict';
// Managed local runtime: only the bundled EXE, exact build, exact spawned PID.
const {spawn, execFile} = require('child_process');
const fs = require('fs'), path = require('path'), http = require('http'), crypto = require('crypto');
const contract = require('./build-contract');
let app, getter = () => ({}), child = null, timer = null, generation = 0;
let expected, readyFile = '', checking = false;
let state = {enabled:false,status:'stopped',exe:'',home:'',port:0,pid:0,restarts:0,lastErr:'',logTail:[]};
const listeners = new Set();
const ready = () => state.status === 'ready' && !!state.port;
const base = () => ready() ? 'http://127.0.0.1:' + state.port : '';
function emit() { for (const fn of listeners) { try { fn({...state,base:base()}); } catch (_) {} } }
function token() {
  try { return String(contract.read(path.join(state.home,'config/config.json')).brain_token || ''); }
  catch (_) { return ''; }
}
function probeHealth(port, timeout = 3000, route = '/api/health', auth = '') {
  return new Promise(resolve => {
    const req = http.get({host:'127.0.0.1',port,path:route,timeout,
      headers:auth ? {Authorization:'Bearer ' + auth} : {}}, res => {
      if(route!=='/api/runtime' && route!=='/api/health'){res.resume();resolve(res.statusCode===200 ? {} : null);return;}
      let data = '';res.on('data', b => {data += b;if(data.length>65536)req.destroy();});
      res.on('end', () => {try {resolve(res.statusCode===200 ? JSON.parse(data) : null);}catch(_){resolve(null);}});
      res.on('error',()=>resolve(null));
    });
    req.on('timeout',()=>req.destroy());req.on('error',()=>resolve(null));
  });
}
function fail(message) { clearInterval(timer);timer=null;state.status='failed';state.lastErr=message;emit(); }
async function checkReady(epoch) {
  if(checking || generation!==epoch)return;
  checking=true;
  try {
    if(!fs.existsSync(readyFile))return;
    const info=contract.read(readyFile);
    if(info.pid!==state.pid || info.instance_id!==state.instanceId || !Number.isInteger(info.port) || info.port<1 || info.port>65535)
      throw Error('Brain 启动身份验证失败');
    contract.compatible(expected,info);
    const auth=token();if(!auth)throw Error('本地 Brain 认证未初始化');
    const runtime=await probeHealth(info.port,3000,'/api/runtime',auth);
    if(generation!==epoch)return;
    if(!runtime || runtime.pid!==state.pid || runtime.instance_id!==state.instanceId)throw Error('Brain 认证或进程身份不匹配');
    contract.compatible(expected,runtime);
    for(const route of ['/api/creator/jobs','/api/agent/runs']) {
      if(!await probeHealth(info.port,3000,route,auth))throw Error('Brain 缺少关键接口：'+route);
    }
    if(generation!==epoch)return;
    clearInterval(timer);timer=null;state.port=info.port;state.status='ready';state.build=runtime;state.lastErr='';emit();
  } catch(e) { if(generation===epoch){fail(e.message);if(child)child.kill();} }
  finally { checking=false; }
}
function maybeStart(cfg = getter()) {
  if(child || cfg.connection_mode==='remote')return;
  const epoch=++generation;
  try {
    const resources=app.isPackaged ? process.resourcesPath : path.resolve(__dirname,'../../dist/brain-release');
    const root=path.join(resources,'vigil-brain');
    expected=contract.read(path.join(__dirname,'../build-info.json'));
    if(expected.version!==app.getVersion())throw Error('Electron 与本地 Brain 版本不匹配；请修复完整 Vigil 安装');
    const manifest=contract.read(path.join(root,'brain-manifest.json'));
    contract.compatible(expected,manifest);contract.verifyTree(root,manifest);
    state={...state,enabled:true,status:'starting',exe:path.join(root,'vigil-brain.exe'),
      home:path.join(app.getPath('userData'),'brain'),port:0,pid:0,lastErr:'',instanceId:crypto.randomUUID(),build:null};
    const run=path.join(app.getPath('userData'),'runtime');fs.mkdirSync(run,{recursive:true});
    readyFile=path.join(run,'ready-'+state.instanceId+'.json');
    const previous=require('./update-transaction').pending(app.getPath('userData'));
    const legacy=[cfg.sidecar_exe && path.dirname(cfg.sidecar_exe),previous && path.join(previous.backupDir,'resources/vigil-brain'),
      path.join(process.resourcesPath || '', 'vigil-brain')].filter(Boolean);
    const env={...process.env,VIGIL_MANAGED:'1',VIGIL_HOME:state.home,VIGIL_NODE_EXE:process.execPath,
      VIGIL_READY_FILE:readyFile,VIGIL_INSTANCE_ID:state.instanceId,VIGIL_LEGACY_HOMES:JSON.stringify([...new Set(legacy)]),
      BRAIN_MUTEX:'Local\\VigilBrain-'+crypto.createHash('sha256').update(state.home).digest('hex').slice(0,20)};
    delete env.VIGIL_PORT;delete env.VIGIL_BRAIN_EXE;
    const sync=cfg.sync || {};
    if(sync.cloud_url)env.VIGIL_CLOUD_URL=String(sync.cloud_url);
    if(sync.cloud_token)env.VIGIL_CLOUD_TOKEN=String(sync.cloud_token);
    fs.mkdirSync(state.home,{recursive:true});
    child=spawn(state.exe,[],{cwd:state.home,windowsHide:true,stdio:['ignore','ignore','ignore'],env});
    state.pid=child.pid || 0;emit();
    child.on('error',()=>{if(generation===epoch)fail('随包 Brain 启动失败，请修复 Vigil 安装');});
    child.on('exit',()=>{
      if(generation!==epoch)return;
      child=null;clearInterval(timer);timer=null;
      if(state.status==='stopped' || state.status==='failed')return;
      state.port=0;
      if(state.restarts<3){state.restarts++;state.status='starting';emit();setTimeout(()=>{if(generation===epoch)maybeStart(getter());},1500);}
      else fail('本地 Brain 连续退出，请检查数据迁移与安装完整性');
    });
    const start=Date.now();timer=setInterval(()=>{
      if(Date.now()-start>120000){fail('本地 Brain 启动超时；未回退到其他地址');if(child)child.kill();return;}
      checkReady(epoch);
    },250);
  } catch(e) { state.enabled=true;fail(e.message); }
}
function stop() {
  generation++;clearInterval(timer);timer=null;checking=false;
  const c=child;child=null;state.status='stopped';state.port=0;state.pid=0;
  if(c && c.pid){
    if(process.platform==='win32')execFile('taskkill',['/pid',String(c.pid),'/T','/F'],{windowsHide:true},()=>{});
    else c.kill();
  }
  if(readyFile){try{fs.unlinkSync(readyFile);}catch(_){}}
  emit();
}
function restart(cfg) {stop();setTimeout(()=>maybeStart(cfg || getter()),1500);}
function init(electronApp,cfgGetter) {app=electronApp;getter=cfgGetter;app.on('before-quit',stop);app.on('will-quit',stop);}
module.exports={init,maybeStart,stop,restart,ready,base,token,probeHealth,
  status:()=>({...state,base:base()}),onState:fn=>{listeners.add(fn);return()=>listeners.delete(fn);}};
