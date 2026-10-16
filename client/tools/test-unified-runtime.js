const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('fs'),os=require('os'),path=require('path'),vm=require('vm');
const contract=require('../electron/build-contract'),tx=require('../electron/update-transaction');
const identity={version:'fixture',commit:'fixture-sha',source_digest:'fixture-tree',protocol:1,capabilities:contract.REQUIRED,candidate:true};
test('public update source rejects candidates and untrusted download origins',()=>{
  const source=require('../electron/public-updates');
  for(const url of ['http://github.com/x','https://github.com.evil.example/x','https://fixture@github.com/x'])assert.throws(()=>source.allowed(url));
  const m={candidate:false,code:21,sha256:'a'.repeat(64),size:123,setup_file:'Vigil.exe'};
  const release={assets:[{name:'Vigil.exe',size:123,browser_download_url:'https://github.com/limingyi2/vigil/releases/download/v1/Vigil.exe'}]};
  assert.equal(source.validateManifest(m,release).public_download,true);
  assert.throws(()=>source.validateManifest({...m,candidate:true},release));
});
test('exact paired build and required protocol/capabilities are mandatory',()=>{
  assert.equal(contract.compatible(identity,{...identity}),true);
  for(const patch of [{version:'old'},{commit:'old'},{source_digest:'other'},{protocol:2},{capabilities:['creator_jobs']},{candidate:false}])
    assert.throws(()=>contract.compatible(identity,{...identity,...patch}),/版本或接口不匹配/);
});
test('missing or changed Brain artifact fails before spawn',t=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'vigil-contract-'));t.after(()=>fs.rmSync(tmp,{recursive:true,force:true}));
  assert.throws(()=>contract.verifyTree(tmp,{files:{}}));
  const exe=path.join(tmp,'vigil-brain.exe');fs.writeFileSync(exe,'fixture');
  const manifest={files:{'vigil-brain.exe':contract.hash(exe)}};contract.verifyTree(tmp,manifest);
  fs.writeFileSync(exe,'other');assert.throws(()=>contract.verifyTree(tmp,manifest));
});
test('local mode ignores legacy EXE, sidecar=false, environment EXE and stale remote base',()=>{
  const source=fs.readFileSync(path.join(__dirname,'../electron/main.js'),'utf8');
  const fn=source.slice(source.indexOf('function connectionBase('),source.indexOf('async function autoConnect()'));
  const context={URL,readCfg:()=>({}),sidecar:{base:()=>''}};vm.runInNewContext(fn+';this.pick=connectionBase',context);
  assert.equal(context.pick({base:'http://127.0.0.1:18787',sidecar:false,sidecar_exe:'old.exe'}),'');
  assert.equal(context.pick({connection_mode:'remote',remote_base:'https://brain.example.com'}),'https://brain.example.com');
  assert.equal(context.pick({connection_mode:'remote',remote_base:'https://user:fixture@brain.example.com/?token=fixture'}),'');
});
test('rollback snapshot preserves a complete pair, checks hashes, never contains user home',t=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'vigil-update-'));t.after(()=>fs.rmSync(tmp,{recursive:true,force:true}));
  const install=path.join(tmp,'app'),home=path.join(tmp,'profile');fs.mkdirSync(install);
  fs.writeFileSync(path.join(install,'Vigil.exe'),'old-client');fs.mkdirSync(path.join(install,'resources'));
  fs.writeFileSync(path.join(install,'resources/brain.bin'),'old-brain');
  const snapshot=tx.checkpoint(install,home,identity);
  assert.equal(tx.pending(home).status,'prepared');
  assert.deepEqual(tx.inventory(snapshot.backupDir),snapshot.files);
  tx.markHealthy(home,{...identity,source_digest:'wrong'});assert.equal(tx.pending(home).status,'prepared');
  tx.markHealthy(home,identity);assert.equal(tx.pending(home).status,'healthy');
  assert.throws(()=>tx.checkpoint(install,path.join(install,'data'),identity));
});
