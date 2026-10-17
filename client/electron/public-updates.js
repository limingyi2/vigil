'use strict';
// Public release source needs no personal cloud server or repository credentials.
const https=require('https'),fs=require('fs');
const REPO='https://api.github.com/repos/limingyi2/vigil';
function allowed(raw){
  const u=new URL(raw);
  if(u.protocol!=='https:' || u.username || u.password || !['api.github.com','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'].includes(u.hostname))throw Error('不允许的更新下载目标');
  return u;
}
function response(raw,redirects=0){return new Promise((resolve,reject)=>{
  let u;try{u=allowed(raw);}catch(e){reject(e);return;}
  const req=https.get(u,{headers:{'User-Agent':'Vigil-Updater','Accept':'application/vnd.github+json'},timeout:30000},res=>{
    if([301,302,303,307,308].includes(res.statusCode)){res.resume();if(redirects>=4)return reject(Error('更新重定向过多'));response(new URL(res.headers.location,u).href,redirects+1).then(resolve,reject);return;}
    if(res.statusCode!==200){res.resume();reject(Error('更新源 HTTP '+res.statusCode));return;}
    resolve(res);
  });req.on('timeout',()=>req.destroy(Error('更新连接超时')));req.on('error',reject);
});}
async function json(url){const r=await response(url);let text='';for await(const b of r){text+=b;if(text.length>256*1024){r.destroy();throw Error('更新清单过大');}}return JSON.parse(text);}
function validateManifest(m,release){
  if(m.candidate!==false || !Number.isSafeInteger(m.code) || m.code<1 || !/^[a-f0-9]{64}$/.test(m.sha256||'') || !Number.isSafeInteger(m.size) || m.size<1)throw Error('更新清单无效或为验收包');
  const asset=(release.assets||[]).find(a=>a.name===m.setup_file && a.size===m.size);
  if(!asset || !asset.browser_download_url.startsWith('https://github.com/limingyi2/vigil/releases/download/'))throw Error('发布安装包不存在');
  allowed(asset.browser_download_url);
  return {...m,setup_url:asset.browser_download_url,public_download:true};
}
async function latest(){
  try{const r=await json(REPO+'/releases/latest');const a=(r.assets||[]).find(x=>x.name==='release-manifest.json');if(!a)throw Error('当前发布尚无统一更新清单，可从 GitHub 手动下载安装');
    return {ok:true,json:validateManifest(await json(a.browser_download_url),r)};
  }catch(e){return {ok:false,status:0,err:e.message};}
}
async function download(url,dest,onProgress){
  try{const res=await response(url),total=Number(res.headers['content-length'])||0;let got=0;
    const stream=fs.createWriteStream(dest+'.part');
    await new Promise((resolve,reject)=>{res.on('data',b=>{got+=b.length;if(onProgress)onProgress(got,total);});res.on('error',reject);res.on('aborted',()=>reject(Error('更新下载中断')));stream.on('error',reject);stream.on('close',resolve);res.pipe(stream);});
    if(total && got!==total)throw Error('更新文件不完整');fs.renameSync(dest+'.part',dest);return {ok:true,size:got};
  }catch(e){return {ok:false,err:e.message};}
}
module.exports={latest,download,validateManifest,allowed};
