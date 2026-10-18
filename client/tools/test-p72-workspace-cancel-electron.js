// P7.2 e2e: real renderer/preload, a fixture Brain in the main process.
// Covers: folder workspaces (multiple chats, stable ownership, restart),
// Creator cancellation (confirm/fail/retry/disconnect), and long-source layout.
const {app,BrowserWindow,ipcMain} = require('electron');
const fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert/strict');

const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'vigil-p72-'));
const client=path.resolve(__dirname,'..');
app.setPath('userData',path.join(tmp,'profile'));
const evidenceDir=path.resolve(client,'..','reports','p7.2-evidence');
fs.mkdirSync(evidenceDir,{recursive:true});

let win;
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const ev=s=>win.webContents.executeJavaScript(s,true);
async function until(s,ms=250){for(let i=0;i<ms;i++){if(await ev(s))return true;await wait(40);}throw Error('Timed out: '+s);}

// ── fixture brain ────────────────────────────────────────────────
const WORKSPACE_PATH='F:\\vigil-demo-workspace';
const MISSING_PATH='F:\\vigil-demo-moved-away';
const state={
  workspaces:[], conversations:[], jobs:[], seq:0,
  cancelCalls:[], draftFetches:0, jobGetFailures:0, streamBroken:false, assignCalls:[],
};
function newId(){state.seq+=1;return `00000000-0000-4000-8000-${String(state.seq).padStart(12,'0')}`;}
function jobOf(id){return state.jobs.find(j=>j.job_id===id);}
function convOf(id){return state.conversations.find(c=>c.id===id);}
function settle(job){
  // A running job advances to "cancelled" once the fixture was asked to stop.
  if(job.status==='cancelling'&&job.confirmAt&&Date.now()>=job.confirmAt){
    job.status='cancelled';job.stage='cancelled';
    job.cancel_note='已取消：本地已停止接收模型输出，已生成的部分不会用于生成或安装草稿';
  }
  return job;
}
function fixture(method,raw,body){
  const p=String(raw||'').split('?')[0];
  let json={ok:true,items:[]},status=200;
  if(p==='/api/stats')json={model:'fixture',turns:0};
  if(p==='/api/persona')json={key:'default',name:'fixture',templates:[]};
  if(p==='/api/models/saved')json={current:{model:'fixture'},models:[]};
  if(p==='/api/plugins/frontend')json={version:1,plugins:[]};
  if(p==='/api/plugins'&&method==='GET')json={ok:true,plugins:[],manage:{items:[]},pending_restart:{pending:false,reasons:[]}};
  if(p==='/api/creator/drafts')json={ok:true,drafts:[]};
  if(p==='/api/history')json={ok:true,items:[]};
  if(p==='/api/agent/runs'||p.startsWith('/api/agent/runs/'))json={ok:true,items:[]};

  if(p==='/api/workspaces'&&method==='GET'){
    json={ok:true,items:state.workspaces.map(w=>({...w,conversation_count:state.conversations.filter(c=>c.workspace_id===w.id).length}))};
  } else if(p==='/api/workspaces'&&method==='POST'){
    const exists=state.workspaces.find(w=>w.path.toLowerCase()===String(body.path).toLowerCase());
    if(exists){json={ok:true,item:{...exists,existing:true}};}
    else {
      const item={id:newId(),path:body.path,name:path.basename(body.path)||body.path,
        created_at:Date.now()/1000,updated_at:Date.now()/1000};
      state.workspaces.unshift(item);status=201;json={ok:true,item:{...item,existing:false}};
    }
  } else if(p.startsWith('/api/workspaces/')&&p.endsWith('/delete')){
    const wid=p.split('/').slice(-2)[0];
    state.workspaces=state.workspaces.filter(w=>w.id!==wid);
    state.conversations.forEach(c=>{if(c.workspace_id===wid)c.workspace_id=null;});
    json={ok:true,item:{id:wid},detached:1};
  }

  if(p==='/api/conversations'&&method==='GET')json={ok:true,items:state.conversations};
  if(p==='/api/conversations'&&method==='POST'){
    const item={id:newId(),title:'新对话',created_at:Date.now()/1000,updated_at:Date.now()/1000,
      legacy:false,workspace_id:body.workspace_id||null};
    state.conversations.unshift(item);status=201;json={ok:true,item};
  }
  if(p.startsWith('/api/conversations/')&&p.endsWith('/workspace')){
    const cid=p.split('/').slice(-2)[0],conv=convOf(cid);
    state.assignCalls.push({id:cid,workspace_id:(body&&body.workspace_id)||null});
    if(conv){conv.workspace_id=body.workspace_id||null;json={ok:true,item:conv};}
    else {status=404;json={ok:false,err:'会话不存在'};}
  }
  if(p.startsWith('/api/conversations/')&&method==='POST'&&!p.endsWith('/delete')&&!p.endsWith('/workspace')){
    const cid=p.split('/').slice(-1)[0],conv=convOf(cid);
    if(conv&&body.title){conv.title=body.title;json={ok:true,item:conv};}else json={ok:true};
  }

  if(p==='/api/creator/jobs'&&method==='POST'){
    const job={job_id:'job_'+state.seq+'_'+Date.now(),request_id:body.request_id,kind:body.kind||'plugin',
      requirement:body.requirement,status:'queued',stage:'queued',content:'',event_cursor:0,
      // The real Brain scopes a job to the conversation it was started from.
      conversation_id:body.conversation_id||null,created_at:Date.now()/1000};
    state.jobs.push(job);
    // The model "starts" right away: after 150ms the job is generating.
    setTimeout(()=>{if(job.status==='queued'){job.status='running';job.stage='generating';
      job.content='{"files":{"x.py":"print(1)"}}';job.event_cursor=2;}},150);
    json={...job};
  } else if(p==='/api/creator/jobs'&&method==='GET'){
    json={ok:true,items:state.jobs};
  } else if(p.startsWith('/api/creator/jobs/')){
    const rest=p.slice('/api/creator/jobs/'.length);
    const parts=rest.split('/');
    const job=jobOf(parts[0]);
    if(!job){status=404;json={ok:false,err:'no such job'};}
    else if(parts[1]==='cancel'&&method==='POST'){
      state.cancelCalls.push(job.job_id);
      if(state.cancelCalls.length===1&&state.failFirstCancel){
        status=500;json={ok:false,err:'后端暂时无法确认'};
      } else if(job.status==='cancelled'){
        json={...job,cancel_state:'cancelled'};
      } else {
        job.status='cancelling';job.stage='cancelling';
        job.confirmAt=Date.now()+260;
        json={...job,cancel_state:'cancelling'};
      }
    } else if(parts[1]==='events'){
      json={ok:true,snapshot:settle(job),events:[]};
    } else {
      if(state.jobGetFailures>0){state.jobGetFailures-=1;status=503;json={ok:false,err:'backend unavailable'};}
      else json=settle(job);
    }
  }
  if(p==='/api/creator/draft'){state.draftFetches+=1;json={ok:true,draft:null};}
  if(p.startsWith('/api/agent-center/'))return {ok:false,status:404,json:{err:'no such api'}};
  return {ok:true,status,json};
}

async function send(text){
  await ev(`(()=>{const t=document.querySelector('#ta');t.value=${JSON.stringify(text)};t.dispatchEvent(new Event('input'));document.querySelector('#sendBtn').click();})()`);
  await until("document.querySelector('#ta').value === '' && !document.querySelector('#sendBtn').classList.contains('is-sending')",400);
}
const rows=sel=>`Array.from(document.querySelectorAll('${sel}')).map(n=>n.textContent.trim())`;
// Everything below is scoped to the LAST task card: earlier finished cards stay
// in the transcript and must not be mistaken for the live one.
const LAST=`(Array.from(document.querySelectorAll('.cr-task')).at(-1))`;
const noteSel=`(((${LAST})||{}).querySelector ? (${LAST}).querySelector('[data-cr-note]').textContent : '')`;
const cancelBtn=`(${LAST}.querySelector('[data-cr-cancel]'))`;
const retryBtn=`(${LAST}.querySelector('[data-cr-retry]'))`;
const phaseSel=`(${LAST}.querySelector('[data-cr-phase]').textContent)`;

async function shot(name){
  win.show();await wait(900);
  fs.writeFileSync(path.join(evidenceDir,name),(await win.webContents.capturePage()).toPNG());
  win.hide();
}

async function run(){
  ipcMain.handle('api',(_,a)=>fixture(a.method,a.path,a.body));
  ipcMain.handle('api:stream',async(e,a)=>{
    if(state.streamBroken)return {ok:false,status:0,err:'stream interrupted'};
    if(a.path.includes('/creator/')){
      const jid=a.path.split('/').slice(-2)[0],job=jobOf(jid);
      if(job)e.sender.send('api:stream:event',{job_id:jid,type:'snapshot',seq:job.event_cursor,payload:job});
    } else {
      e.sender.send('api:stream:event',{type:'text',text:'好的',request_id:a.body&&a.body.request_id});
      e.sender.send('api:stream:event',{type:'done',text:'好的',request_id:a.body&&a.body.request_id});
    }
    await wait(60);return {ok:true};
  });
  for(const [name,fn] of Object.entries({
    'cfg:get':()=>({wizard_done:true,roots:[],src:'pc'}), 'app:version':()=>'P7.2 fixture',
    'app:instance-id':()=>'p72-test-process',
    'plugins:load':()=>({ok:true}),'auto:connect':()=>({ok:false}),
    'sidecar:status':()=>({status:'stopped'}),'local:caps':()=>[],
    'local:defaults':()=>({roots:[],commands:[]}),
    'wallpaper:request':()=>({ok:true,data:{settings:{id:''},wallpapers:[]}}),
    'workspace:pick':()=>({ok:true,path:WORKSPACE_PATH}),
    'workspace:probe':(_,p)=>(String(p).includes('moved-away')
      ? {ok:true,path:p,exists:false,directory:false,readable:false}
      : {ok:true,path:p,exists:true,directory:true,readable:true}),
  }))ipcMain.handle(name,fn);

  win=new BrowserWindow({show:false,width:1440,height:940,webPreferences:{
    preload:path.join(client,'electron/preload.js'),sandbox:true,contextIsolation:true,nodeIntegration:false}});
  await win.loadFile(path.join(client,'src/index.html'));
  await until("!!document.querySelector('#wsPickBtn')");await wait(350);
  // The sidebar is a list, not a launcher: the only "打开文件夹" entry is the
  // composer picker. Nothing in the left column may create a workspace.
  assert.equal(await ev("!!document.querySelector('#openFolderBtn, #wsAddBtn, .ws-add')"),false,'侧栏不得有任何打开文件夹按钮');
  assert.equal(await ev("document.querySelector('#sessList').textContent.includes('打开文件夹')"),false,'侧栏文字里不得出现打开文件夹');

  // ── 1. 打开文件夹 = 一个工作区 ───────────────────────────────
  assert.equal(await ev("document.querySelector('#wsPickBtn').textContent.includes('选择工作空间')"),true);
  await ev("document.querySelector('#wsPickBtn').click()");
  await until("!document.querySelector('#wsPop').hidden");
  const folderButtons=await ev("[...document.querySelectorAll('button')].map(n=>n.textContent.trim()).filter(t=>t.includes('文件夹'))");
  assert.deepEqual(folderButtons,['打开本地文件夹'],'全界面只能有一个打开文件夹入口，且在选择器里：'+JSON.stringify(folderButtons));
  await ev("document.querySelector('#wspOpenFolder').click()");
  await until("!!document.querySelector('.ws-row')");
  const wsRow=await ev(`(()=>{const r=document.querySelector('.ws-row');return{name:r.querySelector('.ws-open .tx b').textContent,path:r.querySelector('.ws-path').textContent,count:r.querySelector('.ws-count').textContent,status:r.querySelector('.ws-status')?.textContent||'ok'}})()`);
  assert.equal(wsRow.name,'vigil-demo-workspace');
  assert.equal(wsRow.path,WORKSPACE_PATH);
  assert.equal(wsRow.status,'ok');
  assert.equal(await ev("!!document.querySelector('#wsAddBtn, .ws-add')"),false,'建完工作区侧栏仍不得冒出打开文件夹按钮');
  assert.equal(state.assignCalls.length,0,'没有会话时打开文件夹只建工作区，不改任何会话归属');
  console.log('PASS 打开文件夹建工作区：显示文件夹名、真实路径与目录状态');

  // ── 2. 同一工作区两个会话，切换后归属不变 ────────────────────
  await ev("document.querySelector('[data-ws-new]').click()");
  await until("document.querySelectorAll('.ws-chats .session-row').length===1");
  await ev("document.querySelector('[data-ws-new]').click()");
  await until("document.querySelectorAll('.ws-chats .session-row').length===2");
  const inWs=state.conversations.filter(c=>c.workspace_id===state.workspaces[0].id);
  assert.equal(inWs.length,2);
  assert.equal(await ev("!!document.querySelector('#chatWsBar') && !document.querySelector('#chatWsBar').hidden"),true);
  const chatBarText=await ev("document.querySelector('#chatWsBar').textContent");
  assert.ok(chatBarText.includes('vigil-demo-workspace'),chatBarText);
  assert.ok(chatBarText.includes('不等于已授权读写'),chatBarText);
  const ids=inWs.map(c=>c.id);
  await ev(`document.querySelector('[data-session="${ids[1]}"]').click()`);
  await wait(260);
  await ev(`document.querySelector('[data-session="${ids[0]}"]').click()`);
  await wait(260);
  assert.deepEqual(state.conversations.filter(c=>c.workspace_id===state.workspaces[0].id).map(c=>c.id),ids,
    '切换会话不得改变工作区归属');
  await shot('p72-workspace-two-chats.png');
  console.log('PASS 一个工作区两个会话，来回切换归属不变');

  // ── 3. 普通聊天不自动进工作区 ────────────────────────────────
  await ev("document.querySelector('#newTopicBtn').click()");
  await until("!document.querySelector('#hero').hidden");
  assert.equal(await ev("document.querySelector('#chatWsBar').hidden"),true);
  await send('这是一条普通聊天');
  await until("document.querySelectorAll('.session-chats .session-row').length>=1");
  const loose=state.conversations.find(c=>!c.workspace_id);
  assert.ok(loose,'普通聊天必须落在工作区之外');
  assert.equal(state.conversations.filter(c=>c.workspace_id).length,2,'发消息不得把会话塞进工作区');
  const groups=await ev(`(()=>{const ws=document.querySelector('.session-workspaces'),ch=document.querySelector('.session-chats');return{ws:ws.querySelectorAll('.session-row').length,chats:Array.from(ch.querySelectorAll('.session-row .tx b')).map(n=>n.textContent)}})()`);
  assert.equal(groups.ws,2);
  console.log('PASS 普通聊天留在“聊天”分组，不自动进入工作区');

  // ── 3b. 输入区排版 + 工作空间选择器 ──────────────────────────
  const layout=await ev(`(()=>{const r=(s)=>document.querySelector(s).getBoundingClientRect(),box=r('.cbox'),add=r('#clipBtn'),model=r('#modelBtn'),send=r('#sendBtn'),foot=r('.composer-foot');return{ph:document.querySelector('#ta').placeholder,addX:add.x,modelX:model.x,sendX:send.x,addIn:add.top>=box.top-1&&add.bottom<=box.bottom+1,modelIn:model.top>=box.top-1&&model.bottom<=box.bottom+1,footBelow:foot.top>=box.bottom-1,footText:document.querySelector('.composer-foot').textContent}})()`);
  assert.ok(layout.ph.includes('今天帮你做些什么'),layout.ph);
  assert.ok(layout.addX<layout.modelX && layout.modelX<layout.sendX,'加号在左、模型与发送在右：'+JSON.stringify(layout));
  assert.equal(layout.addIn,true,'附件按钮在输入框内');
  assert.equal(layout.modelIn,true,'模型按钮在输入框内');
  assert.equal(layout.footBelow,true,'工作空间与授权标签在输入框下方');
  assert.ok(layout.footText.includes('选择工作空间'),layout.footText);
  assert.ok(layout.footText.includes('仅授权目录'),layout.footText);
  // 那两个标签后面只能有文字：.caret 是全局闪烁光标，绝不能出现在这里
  assert.equal(await ev("document.querySelectorAll('.composer-foot .caret, .foot-chip .caret').length"),0,'输入区标签后面不得有闪烁光标块');
  await ev("document.querySelector('#wsPickBtn').click()");
  await until("!document.querySelector('#wsPop').hidden");
  const pop=await ev(`(()=>{const p=document.querySelector('#wsPop');return{search:!!p.querySelector('#wsSearch'),items:p.querySelectorAll('[data-ws-pick]').length,folder:!!p.querySelector('#wspOpenFolder'),note:(p.querySelector('.wsp-note')||{}).textContent||'',above:p.getBoundingClientRect().bottom<=document.querySelector('.cbox').getBoundingClientRect().top+1}})()`);
  assert.equal(pop.search,true,'选择器要能搜索');
  assert.ok(pop.items>=1,'选择器要列出已有工作区');
  assert.equal(pop.folder,true,'选择器要有“打开本地文件夹”');
  assert.ok(pop.note.includes('不授予读写权限'),pop.note);
  assert.equal(pop.above,true,'选择器弹在输入框上方');
  assert.equal(state.assignCalls.length,0,'只是打开选择器不得改动归属');
  await shot('p72-composer-workspace-picker.png');
  // 明确点一下才把当前会话放进工作区
  const looseId=state.conversations.find(c=>!c.workspace_id).id;
  await ev(`document.querySelector('#wsPop [data-ws-pick="${state.workspaces[0].id}"]').click()`);
  await until("!document.querySelector('#chatWsBar').hidden");
  assert.deepEqual(state.assignCalls,[{id:looseId,workspace_id:state.workspaces[0].id}]);
  assert.equal(state.conversations.find(c=>c.id===looseId).workspace_id,state.workspaces[0].id);
  // 再移出：会话仍然是普通聊天
  await ev("document.querySelector('#wsPickBtn').click()");
  await until("!document.querySelector('#wsPop').hidden && !!document.querySelector('#wsPop [data-ws-pick=\"\"]')");
  await ev('document.querySelector(\'#wsPop [data-ws-pick=""]\').click()');
  await until("document.querySelector('#chatWsBar').hidden");
  assert.equal(state.conversations.find(c=>c.id===looseId).workspace_id,null);
  assert.equal(state.assignCalls.length,2);
  console.log('PASS 输入区排版照图：+ 在左、模型与圆形发送在右、下方工作空间/授权标签；选择器只列出并显式归属');

  // 授权范围就在聊天框里改：点"仅授权目录"弹出同一个面板，不跳设置页
  await ev("document.querySelector('#permChip').click()");
  await until("!document.querySelector('#localPop').hidden && !!document.querySelector('#lpClose')");
  const lp=await ev(`(()=>{const p=document.querySelector('#localPop');return{page:API.state.page,modes:p.querySelectorAll('[data-mode]').length,pick:!!p.querySelector('#lpPick'),anchored:p.classList.contains('anchored'),labelled:p.textContent.includes('权限模式'),above:p.getBoundingClientRect().bottom<=document.querySelector('.cbox').getBoundingClientRect().top+1}})()`);
  assert.equal(lp.page,'chat','改授权范围不能跳出聊天页');
  assert.equal(lp.modes,3,'面板要有三档权限');
  assert.equal(lp.pick,true,'面板里要能直接选文件夹');
  assert.equal(lp.anchored,true,'面板要贴着标签展开');
  assert.equal(lp.above,true,'面板弹在输入框上方');
  assert.equal(lp.labelled,true);
  await shot('p72-composer-perm-panel.png');
  await ev("document.querySelector('#lpClose').click()");
  console.log('PASS 授权范围在聊天框内直接调整：弹出三档权限与目录白名单，不进设置页');

  // ── 4/5. 重启（渲染层重载）后恢复；已移动的目录要说清楚 ────
  state.workspaces.push({id:newId(),path:MISSING_PATH,name:'vigil-demo-moved-away',
    created_at:Date.now()/1000,updated_at:Date.now()/1000});
  await win.webContents.reload();
  await until("!!document.querySelector('#ta')");await wait(500);
  await until("document.querySelectorAll('.ws-row').length===2");
  const missing=await ev(`(()=>{const r=Array.from(document.querySelectorAll('.ws-row')).find(n=>n.textContent.includes('moved-away'));return{status:r?.querySelector('.ws-status')?.textContent||'',bad:!!r?.querySelector('.ws-status.bad')}})()`);
  assert.equal(missing.bad,true,'目录不存在必须标红');
  assert.ok(missing.status.includes('目录不存在或已移动'),missing.status);
  console.log('PASS 已移动/不存在的目录给出明确状态');

  const afterReload=await ev(`(()=>{const r=Array.from(document.querySelectorAll('.ws-row')).find(n=>n.textContent.includes('vigil-demo-workspace'));return{name:r.querySelector('.ws-open .tx b').textContent,count:r.querySelector('.ws-count').textContent,loose:document.querySelectorAll('.session-chats .session-row').length}})()`);
  assert.equal(afterReload.name,'vigil-demo-workspace');
  assert.equal(afterReload.count,'2');
  assert.equal(state.conversations.filter(c=>c.workspace_id).length,2);
  assert.equal(afterReload.loose>=1,true);
  if (await ev("document.querySelectorAll('.ws-chats .session-row').length") !== 2) {
    await ev("document.querySelector('.ws-row [data-ws-toggle]').click()");
  }
  await until("document.querySelectorAll('.ws-chats .session-row').length===2");
  console.log('PASS 重载后工作区、会话归属、普通聊天分组都恢复');

  // ── 5b. 多尺寸下界面不溢出（含工作区条）────────────────────
  await ev("document.querySelector('.ws-chats .session-row .session-open').click()");
  await until("!document.querySelector('#chatWsBar').hidden");
  win.show();win.focus();await wait(300);
  let smallest=9999;
  for(const [w,h] of [[1280,800],[900,700],[600,650],[800,540]]){
    win.setContentSize(w,h);
    await wait(350);
    // Measure against the viewport the renderer really got: a window manager may
    // clamp a resize, and what matters is that no size produces overflow.
    const m=await ev(`(()=>{const c=document.querySelector('.composer').getBoundingClientRect(),b=document.querySelector('#chatWsBar'),ms=document.querySelector('#msgs');return{vw:window.innerWidth,vh:window.innerHeight,composerBottom:c.bottom,composerLeft:c.left,pageOverflow:document.documentElement.scrollWidth>window.innerWidth+1,msgsOverflow:ms.scrollWidth>ms.clientWidth+1,barVisible:!b.hidden}})()`);
    smallest=Math.min(smallest,m.vw);
    assert.ok(m.composerBottom<=m.vh+0.5,`${w}x${h} 输入框溢出：${JSON.stringify(m)}`);
    assert.ok(m.composerLeft>=0 && m.composerLeft<m.vw,`${w}x${h} 输入框越界`);
    assert.equal(m.pageOverflow,false,`${w}x${h} 页面横向溢出`);
    assert.equal(m.msgsOverflow,false,`${w}x${h} 聊天区横向溢出`);
    assert.equal(m.barVisible,true,`${w}x${h} 工作区条应可见`);
  }
  assert.ok(smallest<=900,`没有测到窄窗口：最窄 ${smallest}px`);
  win.setContentSize(1440,904);await wait(200);
  win.hide();
  console.log('PASS 1280/900/600/800 四种宽度下界面都不溢出（最窄 ' + smallest + 'px）');

  // ── 6. Creator 取消：正在取消 → 已取消 ──────────────────────
  await ev("document.querySelector('#creatorBtn').click()");
  state.failFirstCancel=true;
  await send('做一个提醒插件');
  await until(`!!${cancelBtn}`);
  await until(`${phaseSel}.includes('生成中') || ${phaseSel}.includes('等待模型')`,200);
  const rawMarkers=await ev("document.querySelector('.cr-live-turn, .turn .cr-task') ? document.querySelector('#msgs').textContent.includes('\"files\"') : false");
  assert.equal(rawMarkers,false,'聊天区不得直接铺模型原始输出');
  await shot('p72-creator-task-card.png');

  // 点击后必须"立刻"进入正在取消并禁用（同步读，不等网络）。
  const immediate=await ev(`(()=>{const b=${cancelBtn};b.click();return{label:b.textContent,disabled:b.disabled,note:${noteSel}}})()`);
  assert.equal(immediate.disabled,true,'点一次就禁用，防重复请求');
  assert.ok(immediate.label.includes('正在取消'),immediate.label);
  assert.ok(immediate.note.includes('等待模型连接关闭'),immediate.note);
  // 第一次取消失败：必须给出原因并允许重试。
  await until(`(${noteSel}).includes('取消失败')`);
  const btn=await ev(`(()=>{const b=${cancelBtn};return{label:b.textContent,disabled:b.disabled,hidden:b.hidden}})()`);
  assert.equal(btn.hidden,false);
  assert.equal(btn.disabled,false);
  assert.ok(btn.label.includes('重试取消'),btn.label);
  const failNote=await ev(noteSel);
  assert.ok(failNote.includes('后端暂时无法确认'),failNote);
  console.log('PASS 取消失败：说明原因 + 重试取消按钮可用');

  // 重试成功 → 后端确认 → 已取消
  await ev(`${cancelBtn}.click()`);
  await until(`(${noteSel}).includes('已取消')`,120);
  const done=await ev(`(()=>{const b=${cancelBtn};return{hidden:b.hidden,note:${noteSel},phase:${phaseSel}}})()`);
  assert.equal(done.hidden,true,'已取消后不再显示取消按钮');
  assert.ok(done.phase.includes('已取消'),done.phase);
  assert.ok(done.note.includes('不会用于生成或安装草稿'),done.note);
  assert.equal(state.draftFetches,0,'已取消的任务不得再去取草稿');
  assert.equal(await ev("document.querySelectorAll('.crturn').length"),0,'已取消的任务不得出现草稿卡');
  // Drop the earlier failure toast so the evidence shows only the cancelled card.
  await ev("(()=>{const t=document.getElementById('toast');if(t)t.innerHTML='';})()");
  await shot('p72-creator-cancelled.png');
  console.log('PASS 取消确认后显示已取消、移除加载态、不生成也不安装草稿');

  // ── 7. 取消过程中断线：状态不丢，可重试连接 ─────────────────
  state.cancelCalls.length=0;
  state.failFirstCancel=false;
  state.streamBroken=true;
  state.jobGetFailures=5;
  await send('再做一个插件');
  await until(`!!${cancelBtn}`);
  await until(`(${noteSel}).includes('连接中断')`,150);
  assert.equal(await ev(`!!${retryBtn} && !${retryBtn}.hidden`),true,'断线后必须给重试连接');
  const lostNote=await ev(noteSel);
  assert.ok(/记录已保留在后台/.test(lostNote),lostNote);
  // 断线期间仍然可以取消；状态由后端确认，不靠界面猜。
  await ev(`${cancelBtn}.click()`);
  await until(`(${noteSel}).includes('取消请求已受理') || (${noteSel}).includes('正在关闭模型连接')`,150);
  state.streamBroken=false;
  state.jobGetFailures=0;
  await ev(`${retryBtn}.click()`);
  await until(`(${noteSel}).includes('已取消')`,150);
  assert.equal(state.draftFetches,0,'断线恢复后也不得出现草稿');
  console.log('PASS 取消 + 断线：状态保留，重试连接后仍是已取消');

  // ── 8. 大段源码：默认折叠、限高、可展开、页面不溢出 ──────────
  const big=Array.from({length:420},(_,i)=>`def step_${i}(x):  # 很长的一行注释 ${'#'+i} `+ 'value = '+i).join('\n');
  const draft={id:'draft_p72_big',kind:'plugin',summary:'大文件折叠验收',version:1,model:'fixture',
    requirement:'big file',risks:'仅用于验收',validation:{ok:true,errors:[],warnings:[]},
    files:[{name:'vigil_big/__init__.py',content:big},
           {name:'vigil_big/frontend.js',content:'window.VigilPlugin={registerPage(){}};'}]};
  const job=state.jobs[state.jobs.length-1];
  job.status='succeeded';job.stage='succeeded';job.draft_id=draft.id;
  const before=fixture;
  ipcMain.removeHandler('api');
  ipcMain.handle('api',(_,a)=>{
    if(String(a.path).startsWith('/api/creator/draft'))return {ok:true,status:200,json:{ok:true,draft}};
    return before(a.method,a.path,a.body);
  });
  // Reopen the app: the finished job rehydrates from durable state into a draft.
  await win.webContents.reload();
  await until("!!document.querySelector('#ta')");
  // A restored cancelled job must not offer a cancel button again.
  await until("Array.from(document.querySelectorAll('.cr-task')).some(n=>n.querySelector('[data-cr-phase]').textContent.includes('已取消'))",200);
  const restored=await ev(`Array.from(document.querySelectorAll('.cr-task')).map(n=>({phase:n.querySelector('[data-cr-phase]').textContent,note:n.querySelector('[data-cr-note]').textContent,cancelHidden:(n.querySelector('[data-cr-cancel]')||{}).hidden}))`);
  const cancelledCard=restored.find(r=>r.phase.includes('已取消'));
  assert.equal(cancelledCard.cancelHidden,true,'重启后已取消的任务不得再出现取消按钮');
  assert.ok(cancelledCard.note.includes('已取消'),cancelledCard.note);
  console.log('PASS 重启后取消状态保持一致，且不再显示取消按钮');
  await until("!!document.querySelector('.crturn [data-cr-file-toggle=\"0\"]')",300);
  const card='.crturn[data-crid="draft_p72_big"]';
  const collapsed=await ev(`(()=>{const r=document.querySelector('${card}'),p=r.querySelector('[data-cr-file-preview="0"]'),b=r.querySelector('[data-cr-file-body="0"]'),m=document.querySelector('#msgs');return{toggle:r.querySelector('[data-cr-file-toggle="0"]').textContent,bodyHidden:b.hidden,previewHidden:p.hidden,hasFull:b.textContent.includes('step_419'),pageOverflow:document.documentElement.scrollWidth>window.innerWidth+1,msgsOverflow:m.scrollWidth>m.clientWidth+1}})()`);
  assert.equal(collapsed.toggle,'展开源码');
  assert.equal(collapsed.bodyHidden,true);
  assert.equal(collapsed.previewHidden,false);
  assert.equal(collapsed.hasFull,false,'未展开时不得渲染完整源码');
  assert.equal(collapsed.pageOverflow,false,'长文件不得撑破页面');
  assert.equal(collapsed.msgsOverflow,false,'长文件不得撑破聊天区');
  await ev(`document.querySelector('${card}').scrollIntoView({block:'center'})`);
  await shot('p72-draft-collapsed.png');
  await ev(`document.querySelector('${card} [data-cr-file-toggle="0"]').click()`);
  await until(`document.querySelector('${card} [data-cr-file-body="0"]').hidden===false`);
  const expanded=await ev(`(()=>{const b=document.querySelector('${card} [data-cr-file-body="0"]'),box=b.querySelector('.cr-code'),m=document.querySelector('#msgs');return{hasFull:b.textContent.includes('step_419'),scrollable:!!box&&box.scrollHeight>box.clientHeight,boxHeight:box?box.clientHeight:0,pageOverflow:document.documentElement.scrollWidth>window.innerWidth+1,msgsOverflow:m.scrollWidth>m.clientWidth+1}})()`);
  assert.equal(expanded.hasFull,true,'展开后应能看到完整源码');
  assert.equal(expanded.scrollable,true,'源码区必须独立滚动');
  assert.ok(expanded.boxHeight<=430,JSON.stringify(expanded));
  assert.equal(expanded.pageOverflow,false);
  assert.equal(expanded.msgsOverflow,false);
  await ev(`document.querySelector('${card}').scrollIntoView({block:'center'})`);
  await shot('p72-draft-big-file.png');
  await ev(`document.querySelector('${card} [data-cr-file-toggle="0"]').click()`);
  await until(`document.querySelector('${card} [data-cr-file-body="0"]').hidden===true`);
  console.log('PASS 大段源码默认折叠、展开时限高独立滚动、页面无横向溢出');

  // ── 9. 安装仍然是两步人工审核 ───────────────────────────────
  assert.equal(await ev(`document.querySelector('${card} #crReview')!==null`),true);
  await ev(`document.querySelector('${card} #crReview').click()`);
  await until(`document.querySelector('${card} #crReviewBox').hidden===false`);
  const approveText=await ev(`document.querySelector('${card} #crApprovalState').textContent`);
  assert.ok(approveText.includes('尚未批准'),approveText);
  assert.equal(await ev(`document.querySelector('${card} #crApprovalState').textContent.includes('审批已消费')`),false);
  console.log('PASS 安装仍需明确的“批准 → 仅本次安装”两步');
}

app.whenReady().then(run).then(()=>{win.destroy();app.exit(0);})
  .catch(e=>{console.error(e&&e.stack||e);app.exit(1);});
