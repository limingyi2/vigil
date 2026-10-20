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
test('the client only ever connects to the bundled brain',()=>{
  const source=fs.readFileSync(path.join(__dirname,'../electron/main.js'),'utf8');
  // 远程 Brain 的入口必须整条链消失，不是只把按钮藏起来。
  // 旧键名只允许出现在迁移清单（LEGACY_REMOTE_KEYS）里。
  for(const needle of ['cfg.connection_mode','cfg.remote_base','cfg.cloud_base','base === \'cloud\'','pollBrain(cbase'])
    assert.equal(source.includes(needle),false,'leftover remote brain path: '+needle);
  assert.ok(source.includes('LEGACY_REMOTE_KEYS'),'legacy keys should be archived by migration');
  const fn=source.slice(source.indexOf('function connectionBase('),source.indexOf('async function autoConnect()'));
  assert.ok(fn.includes('function testBrain('),'test hook should sit next to connectionBase');
  const context={URL,app:{isPackaged:true},process:{env:{VIGIL_TEST_BRAIN:'https://brain.example.com',VIGIL_TEST_TOKEN:'x'}},
    sidecar:{base:()=>'http://127.0.0.1:41234'}};
  vm.runInNewContext(fn+';this.pick=connectionBase;this.hook=testBrain',context);
  // 打包后的发行版：外部地址一律不认，环境变量也不行
  assert.equal(context.pick(),'http://127.0.0.1:41234');
  assert.equal(context.hook(),null);
  // 解包运行的测试钩子：只认回环，且要显式给 token
  const dev={URL,app:{isPackaged:false},process:{env:{VIGIL_TEST_BRAIN:'http://127.0.0.1:8080',VIGIL_TEST_TOKEN:'t'}},
    sidecar:{base:()=>'http://127.0.0.1:41234'}};
  vm.runInNewContext(fn+';this.pick=connectionBase;this.hook=testBrain',dev);
  assert.equal(dev.pick(),'http://127.0.0.1:8080');
  assert.equal(dev.hook().token,'t');
  const evil={URL,app:{isPackaged:false},process:{env:{VIGIL_TEST_BRAIN:'https://brain.example.com'}},
    sidecar:{base:()=>'http://127.0.0.1:41234'}};
  vm.runInNewContext(fn+';this.pick=connectionBase;this.hook=testBrain',evil);
  assert.equal(evil.pick(),'http://127.0.0.1:41234');
  assert.equal(evil.hook(),null);
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
  const originalCopy=fs.cpSync;let copyAttempted=false;
  fs.cpSync=function(){copyAttempted=true;throw new Error('copy must not be reached for overlapping roots')};
  try {
    assert.throws(()=>tx.checkpoint(install,install,identity),/应用目录与用户数据目录必须分离/);
  } finally { fs.cpSync=originalCopy; }
  assert.equal(copyAttempted,false,'overlapping install and data roots must be rejected before filesystem changes');
});
