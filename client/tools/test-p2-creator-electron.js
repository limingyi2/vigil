// Production renderer/preload, isolated services and no model/network requests.
const {app,BrowserWindow,ipcMain} = require('electron');
const fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert/strict');
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'vigil-p2-creator-ui-'));
const client=path.resolve(__dirname,'..');
app.setPath('userData',path.join(tmp,'profile'));
let win,job,stage='parse',chatRequests=0,creatorRequests=0;
const jobs=[];
const sessions=[];
let sessionCounter=0;
const approvalCalls=[];
const evidenceDir=path.resolve(client,'..','reports','p6.2-evidence');
fs.mkdirSync(evidenceDir,{recursive:true});
const fullSource=Array.from({length:10},(_,i)=>`# vigil_smoke.py line ${i+1}`).join('\n');
const fixtureDraft={id:'draft_fold_ui',kind:'plugin',summary:'折叠交互验收',version:1,model:'fixture',
  requirement:'UI-only Creator file preview fixture',risks:'fixture only',validation:{ok:true,errors:[],warnings:[]},
  files:[{name:'vigil_smoke.py',content:fullSource},{name:'README.md',content:'fixture readme'}]};
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const ev=s=>win.webContents.executeJavaScript(s,true);
async function until(s){for(let i=0;i<250;i++){if(await ev(s))return;await wait(40);}throw Error('Timed out: '+s);}
function fixture(method,raw,body){
  const p=raw.split('?')[0];let json={ok:true,items:[]};
  if(p==='/api/stats')json={model:'fixture',turns:0};
  if(p==='/api/conversations' && method==='GET')json={ok:true,items:sessions};
  if(p==='/api/conversations' && method==='POST'){
    const n=(++sessionCounter).toString(16).padStart(12,'0');
    const item={id:`00000000-0000-4000-8000-${n}`,title:'新对话',created_at:Date.now()/1000,updated_at:Date.now()/1000,legacy:false};
    sessions.unshift(item);json={ok:true,item};
  }
  if(p==='/api/persona')json={key:'default',name:'fixture',templates:[]};
  if(p==='/api/models/saved')json={current:{model:'fixture'},models:[]};
  if(p==='/api/plugins/frontend')json={version:1,plugins:[]};
  if(p==='/api/creator/drafts')json={ok:true,drafts:[]};
  if(p==='/api/creator/draft')json={ok:true,draft:fixtureDraft};
  if(p==='/api/creator/approve' && method==='POST'){
    approvalCalls.push({path:p,body});
    json={ok:true,approval:{approval_id:'fixture_once_123456789',version:fixtureDraft.version,target_name:'vigil_smoke'}};
  }
  if(p==='/api/creator/install' && method==='POST'){
    approvalCalls.push({path:p,body});
    json={ok:true,install:{package:'vigil_smoke',restart_needed:true,dynamic_loaded:false}};
  }
  if(p==='/api/creator/jobs' && method==='POST'){
    creatorRequests++;
    if(stage==='missing_api')return {ok:false,status:404,target:'http://127.0.0.1:61285',json:{err:'no such api'}};
    if(stage==='fold_ui'){
      job={job_id:'job_fold_ui',request_id:body.request_id,kind:'plugin',requirement:body.requirement,
        status:'succeeded',draft_id:fixtureDraft.id,content:'fixture generated',event_cursor:1};
      jobs.push(job);json={...job};
    } else {
      job={job_id:'job_'+jobs.length,request_id:body.request_id,kind:'plugin',requirement:body.requirement,status:'failed',stage,content:'',event_cursor:1,
        error:{stage,error_code:stage+'_failed',message:'Expecting value (line 3, column 10) <img src=x onerror=alert(1)>',hint:'检查配置后重试',log_id:'job_log_'+jobs.length}};
      jobs.push(job);json={...job,status:'queued',error:null,event_cursor:0};
    }
  } else if(p==='/api/creator/jobs')json={ok:true,items:jobs};
  else if(p.startsWith('/api/creator/jobs/'))json=job;
  if(p.startsWith('/api/agent-center/'))return {ok:false,status:404,json:{err:'no such api'}};
  return {ok:true,status:200,json};
}
async function send(text){
  await ev(`document.querySelector('#ta').value=${JSON.stringify(text)};document.querySelector('#ta').dispatchEvent(new Event('input'));document.querySelector('#sendBtn').click()`);
  await until("document.querySelector('#ta').value === '' && !document.querySelector('#sendBtn').classList.contains('is-sending')");
}
async function run(){
  ipcMain.handle('api',(_,a)=>fixture(a.method,a.path,a.body));
  ipcMain.handle('api:stream',async(e,a)=>{
    if(a.path.includes('/creator/'))e.sender.send('api:stream:event',{job_id:job.job_id,type:'snapshot',payload:job});
    else {chatRequests++;e.sender.send('api:stream:event',{type:'text',text:'safe result',request_id:a.body.request_id});e.sender.send('api:stream:event',{type:'done',text:'safe result',request_id:a.body.request_id});}
    await wait(30);return {ok:true};
  });
  for(const [name,fn] of Object.entries({
    'cfg:get':()=>({wizard_done:true,roots:[]}), 'app:version':()=> 'P2 fixture',
    'app:instance-id':()=> 'p2-creator-test-process',
    'plugins:load':()=>({ok:true}),'auto:connect':()=>({ok:false}),
    'sidecar:status':()=>({status:'stopped'}),'local:caps':()=>[],
    'local:defaults':()=>({roots:[],commands:[]}),
    'wallpaper:request':()=>({ok:true,data:{settings:{id:''},wallpapers:[]}}),
  }))ipcMain.handle(name,fn);
  win=new BrowserWindow({show:false,webPreferences:{preload:path.join(client,'electron/preload.js'),sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
  await win.loadFile(path.join(client,'src/index.html'));
  await until("!!document.querySelector('#ta')");await wait(350);
  for(let i=0;i<20;i++)await send('bounded fixture '+i);
  assert.equal(chatRequests,20);console.log('PASS 20 ordinary streaming turns complete without cross-turn updates');
  await ev("document.querySelector('#creatorBtn').click()");
  for(const s of ['model_request','network','auth','parse','codegen','static_validation','draft_save']){
    stage=s;await send('creator '+s);
    await until(`Array.from(document.querySelectorAll('.stream-status')).at(-1)?.textContent.includes(${JSON.stringify(s+'_failed')})`);
    const text=await ev("Array.from(document.querySelectorAll('.stream-status')).at(-1).textContent");
    for(const wanted of [s+'_failed','line 3, column 10','检查配置后重试','job_log_'])assert.ok(text.includes(wanted),text);
    assert.equal(await ev("document.querySelectorAll('.turn img[src=x]').length"),0);
  }
  console.log('PASS all seven Creator stages show structured errors, parser location, advice and log ID safely');
  stage='fold_ui';await send('render Creator file preview fixture');
  await until("!!document.querySelector('.crturn[data-crid=\"draft_fold_ui\"] [data-cr-file-toggle=\"0\"]')");
  const card=".crturn[data-crid=\"draft_fold_ui\"]";
  const beforeReview=await ev("(()=>{const c=document.querySelector('.composer').getBoundingClientRect(),m=document.querySelector('#msgs');return{composer:{top:c.top,bottom:c.bottom},messageScroll:m.scrollHeight>m.clientHeight,scrollTop:m.scrollTop}})()");
  await ev(`document.querySelector('${card} #crReview').click()`);
  await until(`document.querySelector('${card} #crReviewBox').hidden===false`);
  const reviewLayout=await ev("(()=>{const c=document.querySelector('.composer').getBoundingClientRect(),d=document.querySelector('#crReviewBox').getBoundingClientRect(),m=document.querySelector('#msgs');return{viewportWidth:innerWidth,composer:{top:c.top,bottom:c.bottom},drawer:{top:d.top,right:d.right,width:d.width,bottom:d.bottom,position:getComputedStyle(document.querySelector('#crReviewBox')).position},messageScroll:m.scrollHeight>m.clientHeight,scrollTop:m.scrollTop}})()");
  assert.deepEqual(reviewLayout.composer,beforeReview.composer,'opening installation review must not move the composer');
  assert.equal(reviewLayout.drawer.position,'fixed');assert.equal(reviewLayout.drawer.top,0);
  assert.ok(reviewLayout.drawer.width<=520 && Math.abs(reviewLayout.drawer.right-reviewLayout.viewportWidth)<=1,JSON.stringify(reviewLayout));
  assert.equal(reviewLayout.messageScroll,true,'message history stays independently scrollable');
  assert.equal(reviewLayout.scrollTop,beforeReview.scrollTop,'opening review must not scroll the chat');
  win.show();await wait(2600);
  fs.writeFileSync(path.join(evidenceDir,'installation-steps-drawer.png'),(await win.webContents.capturePage()).toPNG());
  win.hide();
  await ev(`document.querySelector('${card} #crCancel').click()`);
  await until(`document.querySelector('${card} #crReviewBox').hidden===true`);
  assert.deepEqual(await ev("(()=>{const c=document.querySelector('.composer').getBoundingClientRect();return{top:c.top,bottom:c.bottom}})()"),beforeReview.composer);
  console.log('PASS Creator installation review opens in a fixed drawer without moving the composer or chat scroll');
  let fileState=await ev(`(()=>{const r=document.querySelector('${card}'),p=r.querySelector('[data-cr-file-preview="0"]'),b=r.querySelector('[data-cr-file-body="0"]');return {toggle:r.querySelector('[data-cr-file-toggle="0"]')?.textContent,copy:r.querySelector('[data-copy-file="0"]')?.textContent,bodyHidden:b?.hidden,previewHidden:p?.hidden,previewText:p?.textContent||'',bodyText:b?.textContent||'',otherHidden:r.querySelector('[data-cr-file-body="1"]')?.hidden}})()`);
  assert.equal(fileState.toggle,'展开源码');assert.equal(fileState.copy,'复制完整源码');
  assert.equal(fileState.bodyHidden,true);assert.equal(fileState.previewHidden,false);
  assert.ok(fileState.previewText.includes('line 6'));assert.ok(!fileState.previewText.includes('line 7'));
  assert.ok(!fileState.bodyText.includes('line 10'),'full source must not be rendered before expansion');
  assert.equal(fileState.otherHidden,true);console.log('PASS Creator file card starts with a six-line collapsed preview and both actions');
  await ev(`document.querySelector('${card} [data-cr-file-toggle="0"]').click()`);
  await until(`document.querySelector('${card} [data-cr-file-body="0"]').hidden===false`);
  fileState=await ev(`(()=>{const r=document.querySelector('${card}');return {toggle:r.querySelector('[data-cr-file-toggle="0"]').textContent,expanded:r.querySelector('[data-cr-file-toggle="0"]').getAttribute('aria-expanded'),bodyHidden:r.querySelector('[data-cr-file-body="0"]').hidden,previewHidden:r.querySelector('[data-cr-file-preview="0"]').hidden,bodyText:r.querySelector('[data-cr-file-body="0"]').textContent,otherHidden:r.querySelector('[data-cr-file-body="1"]').hidden}})()`);
  assert.equal(fileState.toggle,'收起源码');assert.equal(fileState.expanded,'true');assert.equal(fileState.bodyHidden,false);assert.equal(fileState.previewHidden,true);
  assert.ok(fileState.bodyText.includes('line 10'),'expanded source must include the complete file');assert.equal(fileState.otherHidden,true);
  console.log('PASS clicking expand renders the full file without expanding neighboring files');
  await ev(`document.querySelector('${card} [data-cr-file-toggle="0"]').click()`);
  await until(`document.querySelector('${card} [data-cr-file-body="0"]').hidden===true`);
  fileState=await ev(`(()=>{const r=document.querySelector('${card}');return {toggle:r.querySelector('[data-cr-file-toggle="0"]').textContent,expanded:r.querySelector('[data-cr-file-toggle="0"]').getAttribute('aria-expanded'),bodyHidden:r.querySelector('[data-cr-file-body="0"]').hidden,previewHidden:r.querySelector('[data-cr-file-preview="0"]').hidden,otherHidden:r.querySelector('[data-cr-file-body="1"]').hidden}})()`);
  assert.equal(fileState.toggle,'展开源码');assert.equal(fileState.expanded,'false');assert.equal(fileState.bodyHidden,true);assert.equal(fileState.previewHidden,false);assert.equal(fileState.otherHidden,true);
  await ev("window.__creatorClipboardWrites=[];Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>{window.__creatorClipboardWrites.push(value);}}});true");
  await ev(`document.querySelector('${card} [data-copy-file="0"]').click()`);
  await until("window.__creatorClipboardWrites.length===1");
  assert.deepEqual(await ev('window.__creatorClipboardWrites'),[fullSource]);
  assert.equal(await ev(`document.querySelector('${card} [data-cr-file-body="0"]').hidden`),true);
  assert.equal(await ev(`document.querySelector('${card} [data-copy-file="0"]').textContent`),'已复制');
  assert.equal(await ev(`document.querySelector('${card} [data-cr-file-body="1"]').hidden`),true);
  console.log('PASS collapse restores preview and copy writes the entire file while collapsed; other file stays collapsed');
  await ev(`document.querySelector('${card} #crReview').click()`);
  await until(`document.querySelector('${card} #crReviewBox').hidden===false`);
  await ev(`document.querySelector('${card} #crApproveNow').click()`);
  await until(`document.querySelector('${card} #crInstallNow').dataset.approval==='fixture_once_123456789'`);
  assert.equal(approvalCalls[0].path,'/api/creator/approve');
  await ev(`document.querySelector('${card} #crInstallNow').click()`);
  await until("!!document.querySelector('.vigil-confirm [data-choice=ok]')");
  await ev("document.querySelector('.vigil-confirm [data-choice=ok]').click()");
  await until(`document.querySelector('${card} #crInstallNow').textContent.includes('已安装')`);
  assert.deepEqual(approvalCalls[1],{path:'/api/creator/install',body:{approval_id:'fixture_once_123456789'}});
  assert.ok(await ev(`document.querySelector('${card} #crApprovalState').textContent.includes('审批已消费')`));
  console.log('PASS drawer preserves explicit approval, one-time install confirmation, and consumed-approval result');
  await ev("document.querySelector('#gnav [data-page=\"agent-center\"]').click()");
  await until("!!document.querySelector('#ac-agent-grid')");
  await ev("document.querySelector('#gnav [data-page=\"chat\"]').click()");
  await until("!!document.querySelector('#ta') && document.querySelector('#msgs').textContent.includes('draft_save_failed')");
  console.log('PASS Creator failure details survive page navigation and rehydration');
  stage='missing_api';await send('missing Creator endpoint fixture');
  await until("document.querySelector('#msgs').textContent.includes('creator_api_missing')");
  let text=await ev("document.querySelector('#msgs').textContent");
  for(const wanted of ['404','http://127.0.0.1:61285','未开始生成'])assert.ok(text.includes(wanted),text);
  const sent=creatorRequests;
  await ev("document.querySelector('#gnav [data-page=\"agent-center\"]').click()");
  await until("!!document.querySelector('#ac-agent-grid')");
  await ev("document.querySelector('#gnav [data-page=\"chat\"]').click()");
  await until("!!document.querySelector('#ta') && document.querySelector('#msgs').textContent.includes('creator_api_missing')");
  assert.equal(creatorRequests,sent);
  text=await ev("document.querySelector('#msgs').textContent");
  assert.ok(text.includes('404') && text.includes('http://127.0.0.1:61285'));
  const records=await ev("Object.keys(localStorage).filter(k=>k.startsWith('vigil.display.v1:')&&!k.endsWith(':cleared')).flatMap(k=>JSON.parse(localStorage.getItem(k)))");
  assert.equal(records.find(r=>r.input==='missing Creator endpoint fixture').status,'failed');
  console.log('PASS missing Creator API shows HTTP/target, persists failed and never resubmits on navigation');
}
app.whenReady().then(run).then(()=>{win.destroy();app.exit(0);}).catch(e=>{console.error(e.stack);app.exit(1);});
