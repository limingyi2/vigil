const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const {EventEmitter}=require('node:events');
const identity={version:'fixture',commit:'fixture-sha',source_digest:'fixture-tree',protocol:1,
  capabilities:['creator_jobs','agent_runs','models_saved','chat_stream'],candidate:true};

function harness(t,{spawn,clock=Date.now}={}){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vigil-sidecar-start-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const resources=path.join(root,'resources');fs.mkdirSync(path.join(resources,'vigil-brain'),{recursive:true});
  const userData=path.join(root,'profile'),readyFiles=[];
  const childProcess={spawn:spawn||(()=>{throw Error('unexpected spawn')}),execFile:()=>{}};
  const contract={
    read(file){
      if(file.endsWith('build-info.json'))return identity;
      if(file.endsWith('brain-manifest.json'))return {...identity,files:{'vigil-brain.exe':'fixture'}};
      return JSON.parse(fs.readFileSync(file,'utf8'));
    },compatible:()=>true,verifyTree:()=>true,
  };
  const fakeProcess={platform:'win32',env:{},resourcesPath:resources,execPath:'C:\\Kestrel.exe'};
  let interval=null;
  const module={exports:{}};
  const source=fs.readFileSync(path.join(__dirname,'../electron/sidecar.js'),'utf8');
  const fakeDate=class extends Date { static now(){return clock()} };
  vm.runInNewContext(source,{
    require(id){
      if(id==='child_process')return childProcess;
      if(id==='fs')return fs;
      if(id==='path')return path;
      if(id==='http')return require('node:http');
      if(id==='crypto')return crypto;
      if(id==='util')return require('node:util');
      if(id==='./build-contract')return contract;
      if(id==='./update-transaction')return {pending:()=>null};
      throw new Error('Unexpected import '+id);
    },
    process:fakeProcess,module,exports:module.exports,Date:fakeDate,
    __dirname:path.join(__dirname,'../electron'),
    setInterval(fn){interval=fn;return 1},clearInterval(){},setTimeout(){},
    console,
  },{filename:path.join(__dirname,'../electron/sidecar.js')});
  const app={isPackaged:true,getVersion:()=>identity.version,getPath:()=>userData,on(){}};
  module.exports.init(app,()=>({}));
  return {api:module.exports,root,get interval(){return interval},readyFiles,fakeProcess};
}

test('synchronous Windows spawn UNKNOWN preserves process creation diagnostics',t=>{
  const fixture=Object.assign(new Error('spawn UNKNOWN'),{code:'UNKNOWN',errno:-4094,syscall:'spawn'});
  const h=harness(t,{spawn:()=>{throw fixture}});
  h.api.maybeStart();
  const status=h.api.status();
  assert.equal(status.status,'failed');
  assert.equal(status.failureKind,'process_create');
  assert.equal(status.lastFailure.code,'UNKNOWN');
  assert.equal(status.lastFailure.errno,-4094);
  assert.equal(status.lastFailure.syscall,'spawn');
  assert.equal(status.lastFailure.path,path.join(h.root,'resources','vigil-brain','vigil-brain.exe'));
  assert.equal(status.lastFailure.cwd,path.join(h.root,'profile','brain'));
  assert.deepEqual(Array.from(status.lastFailure.spawnargs),[]);
  assert.match(status.diagnosis,/code=UNKNOWN/);
});

test('a failed Brain health identity check is classified separately',async t=>{
  let child;
  const h=harness(t,{spawn:()=>{
    child=new EventEmitter();child.pid=73;child.stdout=new EventEmitter();child.stderr=new EventEmitter();child.kill=()=>{};return child;
  }});
  h.api.maybeStart();
  const status=h.api.status();
  const readyFile=path.join(h.root,'profile','runtime','ready-'+status.instanceId+'.json');
  fs.writeFileSync(readyFile,JSON.stringify({pid:74,instance_id:status.instanceId,port:1234}));
  h.interval();
  await new Promise(resolve=>setImmediate(resolve));
  const failed=h.api.status();
  assert.equal(failed.failureKind,'health_check');
  assert.match(failed.lastErr,/身份验证失败/);
});

test('the 120-second startup deadline has its own failure classification',t=>{
  let now=1000,child;
  const h=harness(t,{clock:()=>now,spawn:()=>{
    child=new EventEmitter();child.pid=73;child.stdout=new EventEmitter();child.stderr=new EventEmitter();child.kill=()=>{};return child;
  }});
  h.api.maybeStart();
  now+=120001;
  h.interval();
  const status=h.api.status();
  assert.equal(status.failureKind,'startup_timeout');
  assert.equal(status.lastFailure.timeoutMs,120000);
});
