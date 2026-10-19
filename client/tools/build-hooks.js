'use strict';
const fs=require('fs'),path=require('path'),{execFileSync}=require('child_process');
const contract=require('../electron/build-contract');
exports.beforePack=async()=>{
  const root=path.resolve(__dirname,'../..');
  const venv=path.join(root,'venv','Scripts','python.exe');
  const python=process.env.VIGIL_BUILD_PYTHON || (fs.existsSync(venv)?venv:'python');
  const candidateId=String(process.env.VIGIL_CANDIDATE_ID||'');
  execFileSync(python,[path.join(root,'scripts/build_release.py'),'--verify',
    ...(process.env.VIGIL_BUILD_CANDIDATE==='1'?['--candidate']:[]),
    ...(candidateId?['--candidate-id',candidateId]:[])],{cwd:root,stdio:'inherit'});
};
exports.afterPack=async context=>{
  await exports.beforePack();
  const resources=path.join(context.appOutDir,'resources');
  const asar=require('@electron/asar');
  const client=JSON.parse(asar.extractFile(path.join(resources,'app.asar'),'build-info.json'));
  const root=path.join(resources,'vigil-brain'),brain=contract.read(path.join(root,'brain-manifest.json'));
  contract.compatible(client,brain);contract.verifyTree(root,brain);
};
