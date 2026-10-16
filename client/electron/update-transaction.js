'use strict';
const fs=require('fs'),path=require('path'),crypto=require('crypto'),{spawn}=require('child_process');
const {hash}=require('./build-contract');
function atomic(file,value){fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file+'.tmp',JSON.stringify(value,null,2));fs.renameSync(file+'.tmp',file);}
function inventory(root){
  const files={};
  function visit(dir){for(const d of fs.readdirSync(dir,{withFileTypes:true})){
    const p=path.join(dir,d.name);if(d.isSymbolicLink())throw Error('应用目录包含链接，无法安全备份');
    if(d.isDirectory())visit(p);else files[path.relative(root,p)]=hash(p);
  }}visit(root);return files;
}
function checkpoint(installDir,userData,target){
  installDir=path.resolve(installDir);userData=path.resolve(userData);
  if(!fs.existsSync(path.join(installDir,'Vigil.exe')))return null;
  if(userData.startsWith(installDir+path.sep) || installDir.startsWith(userData+path.sep))throw Error('应用目录与用户数据目录必须分离');
  const root=path.join(userData,'updates'),id=Date.now()+'-'+crypto.randomBytes(4).toString('hex');
  const backupDir=path.join(root,'rollback-'+id);fs.mkdirSync(root,{recursive:true});
  const files=inventory(installDir);
  fs.cpSync(installDir,backupDir,{recursive:true,errorOnExist:true,force:false});
  const copied=inventory(backupDir);
  if(JSON.stringify(files)!==JSON.stringify(copied))throw Error('旧版本备份校验失败，取消更新');
  const value={schema:1,id,installDir,backupDir,files,target,status:'prepared'};
  atomic(path.join(root,'pending.json'),value);return value;
}
function pending(userData){try{return JSON.parse(fs.readFileSync(path.join(userData,'updates/pending.json'),'utf8').replace(/^\uFEFF/,''));}catch(_){return null;}}
function markHealthy(userData,build){const p=pending(userData);if(p && p.target.commit===build.commit && p.target.source_digest===build.source_digest){p.status='healthy';atomic(path.join(userData,'updates/pending.json'),p);}}
function launchRollback(userData,waitPid=process.pid){
  const p=pending(userData);if(!p)throw Error('没有可恢复的旧版本');
  const root=path.join(userData,'updates'),script=path.join(root,'restore.ps1'),request=path.join(root,'restore.json');
  fs.copyFileSync(path.join(__dirname,'rollback.ps1'),script);
  atomic(request,{...p,waitPid,userData});
  const c=spawn('powershell.exe',['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',script,'-RequestPath',request],
    {detached:true,windowsHide:true,stdio:'ignore'});c.unref();return c.pid;
}
module.exports={checkpoint,pending,markHealthy,launchRollback,inventory};
