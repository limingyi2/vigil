'use strict';
const fs=require('fs'),path=require('path'),{execFileSync}=require('child_process');
const contract=require('../electron/build-contract');
exports.beforePack=async()=>{
  const root=path.resolve(__dirname,'../..');
  const nativeHost=path.join(root,'client','native-window-host','build','Release','native-window-host.exe');
  if(!fs.existsSync(nativeHost))throw new Error('Required native window host is missing; build it with client/native-window-host/build.cmd before packaging.');
  const venv=path.join(root,'venv','Scripts','python.exe');
  const python=process.env.VIGIL_BUILD_PYTHON || (fs.existsSync(venv)?venv:'python');
  execFileSync(python,[path.join(root,'scripts/build_release.py'),'--verify',
    ...(process.env.VIGIL_BUILD_CANDIDATE==='1'?['--candidate']:[])],{cwd:root,stdio:'inherit'});
};
exports.afterPack=async context=>{
  await exports.beforePack();
  const resources=path.join(context.appOutDir,'resources');
  const packagedNativeHost=path.join(resources,'native-window-host','native-window-host.exe');
  if(!fs.existsSync(packagedNativeHost))throw new Error('Packaged native window host is missing: '+packagedNativeHost);
  const asar=require('@electron/asar');
  const archive=path.join(resources,'app.asar');
  const files=asar.listPackage(archive).map(file=>file.replace(/\\/g,'/'));
  contract.verifyNoBundledPluginPaths(files);
  contract.verifyNoBundledPluginManifests(files.filter(file=>/\/manifest\.json$/i.test(file)).map(file=>({
    path:file,content:asar.extractFile(archive,file.replace(/^\//,'')).toString('utf8')
  })));
  // extraResources lives outside app.asar and needs the same extension checks.
  const resourceFiles=[];
  const walk=dir=>{for(const item of fs.readdirSync(dir,{withFileTypes:true})){
    const full=path.join(dir,item.name),rel=path.relative(resources,full).replace(/\\/g,'/');
    if(item.isDirectory())walk(full);else resourceFiles.push({path:rel,full});
  }};
  walk(resources);
  contract.verifyNoBundledPluginPaths(resourceFiles.map(item=>item.path));
  contract.verifyNoBundledPluginManifests(resourceFiles.filter(item=>/\/manifest\.json$/i.test(item.path)).map(item=>({
    path:item.path,content:fs.readFileSync(item.full,'utf8')
  })));
  const required=['src/js/pages.js','src/js/pages/data.js','src/js/pages/plugin-market.js','src/js/pages/video-workbench.js'];
  for(const file of required)if(!files.includes('/'+file))throw new Error('Packaged client module is missing: '+file);
  const html=asar.extractFile(archive,'src/index.html').toString();
  const position=file=>html.indexOf('src="'+file+'"');
  const dependencies=[
    ['js/chart.js','js/pages/data.js'],
    ['js/pages/video-workbench.js','js/pages.js'],
    ['js/pages/plugin-market.js','js/pages.js'],
    ['js/pages/data.js','js/pages.js'],
    ['js/pages.js','js/app.js'],
  ].map(([before,after])=>[position(before),position(after)]);
  if(dependencies.some(([before,after])=>before<0||after<0||before>=after))throw new Error('Packaged client script loading order is invalid.');
  const client=JSON.parse(asar.extractFile(archive,'build-info.json'));
  const root=path.join(resources,'vigil-brain'),brain=contract.read(path.join(root,'brain-manifest.json'));
  for(const rel of ['plugins','_internal/plugins']) {
    if(fs.existsSync(path.join(root,rel))) throw new Error('Packaged Brain contains a plugin source tree: '+rel);
  }
  contract.compatible(client,brain);contract.verifyTree(root,brain);
};
