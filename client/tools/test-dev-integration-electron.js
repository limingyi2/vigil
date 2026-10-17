// Focused merge regression: real renderer/preload, isolated IPC services only.
// No production main process, credentials, model calls, or clipboard reads.
const {app, BrowserWindow, ipcMain} = require('electron');
const fs = require('fs'), path = require('path'), os = require('os');
const assert = require('assert/strict');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-dev-ui-'));
app.setPath('userData', path.join(tmp, 'electron'));
const client = path.resolve(__dirname, '..');
const calls = [], chunks = [];
let failNextUpload = false;
let win;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const ev = script => win.webContents.executeJavaScript(script, true);
async function until(script) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) { if (await ev(script)) return; await delay(40); }
  throw Error('Timed out: ' + script);
}
function fixture(method, raw, body) {
  const p = raw.split('?')[0]; calls.push({method, path:p, body});
  let json = {ok:true, items:[]};
  if (p === '/api/stats') json = {model:'fixture-mimo', turns:0};
  if (p === '/api/history') json = {items:[]};
  if (p === '/api/persona') json = {key:'default', name:'Vigil', templates:[]};
  if (p === '/api/plugins/frontend') json = {version:1, plugins:[]};
  if (p === '/api/models/saved') json = {current:{model:'fixture-mimo'}, models:[]};
  if (p === '/api/creator/drafts') json = {ok:true, drafts:[]};
  if (p === '/api/creator/draft') return {ok:false, status:422, json:{ok:false, err:'isolated draft fixture'}};
  if (p === '/api/attachments/capabilities') json = {ok:true, video:false, video_frames:false};
  if (p === '/api/chat') json = {ok:true, reply:'fixture attachment accepted'};
  if (p.startsWith('/api/agent-center/')) return {ok:false, status:404, json:{err:'no such api'}};
  return {ok:true, status:200, json};
}
async function run() {
  ipcMain.handle('api', (_, {method, path:p, body}) => fixture(method, p, body));
  ipcMain.handle('attachment:chunk', (_, {bytes, headers}) => {
    if (failNextUpload) {
      failNextUpload = false;
      return {ok:false, status:413, err:'fixture upload failed'};
    }
    chunks.push({bytes:Buffer.from(bytes), headers});
    return {ok:true, status:200, json:{ok:true}};
  });
  ipcMain.handle('api:stream', (event, {path:p, body}) => {
    calls.push({method:'STREAM', path:p, body});
    event.sender.send('api:stream:event', {type:'text', text:'fixture text accepted'});
    event.sender.send('api:stream:event', {type:'done'});
    return {ok:true};
  });
  const handlers = {
    'cfg:get':()=>({wizard_done:true, roots:[]}), 'app:version':()=> 'dev-fixture',
    'plugins:load':()=>({ok:true}), 'auto:connect':()=>({ok:false}),
    'sidecar:status':()=>({status:'stopped'}), 'local:caps':()=>[],
    'local:defaults':()=>({roots:[], commands:[]}),
    'wallpaper:request':()=>({ok:true, data:{settings:{id:''}, wallpapers:[]}}),
  };
  for (const [name, fn] of Object.entries(handlers)) ipcMain.handle(name, fn);
  win = new BrowserWindow({show:false, webPreferences:{
    preload:path.join(client, 'electron/preload.js'), sandbox:true,
    contextIsolation:true, nodeIntegration:false, backgroundThrottling:false,
  }});
  await win.loadFile(path.join(client, 'src/index.html'));
  await until("!!document.querySelector('#ta') && !document.querySelector('#hero').hidden");
  await delay(300); // let startup's connection refresh settle before interacting
  assert.equal(await ev("!!document.querySelector('#creatorBtn') && !!document.querySelector('#modelPop') && !!document.querySelector('#clipBtn')"), true);
  console.log('PASS Creator, model switch, and attachment controls coexist');
  await ev("window.confirm=()=>true; document.querySelector('#ta').value='safe text'; document.querySelector('#ta').dispatchEvent(new Event('input')); document.querySelector('#sendBtn').click()");
  await until("!document.querySelector('#sendBtn').classList.contains('is-sending')");
  assert.ok(calls.some(c => c.path === '/api/chat/stream'));
  console.log('PASS ordinary text retains streaming route');
  await ev("document.querySelector('#creatorBtn').click(); document.querySelector('#ta').value='safe draft'; document.querySelector('#ta').dispatchEvent(new Event('input')); document.querySelector('#sendBtn').click()");
  const creatorDeadline=Date.now()+10000;
  while(!calls.some(c=>c.path==='/api/creator/jobs')&&Date.now()<creatorDeadline) await delay(30);
  assert.ok(calls.some(c => c.path === '/api/creator/jobs'));
  console.log('PASS Creator retains separate draft route');
  await ev("document.querySelector('#creatorBtn').click(); document.querySelector('#creatorBtn').click()");
  const pngBase64='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j9ZsAAAAASUVORK5CYII=';
  await ev(`(() => { window.confirm=()=>true; const raw=atob('${pngBase64}'); const bytes=Uint8Array.from(raw,c=>c.charCodeAt(0)); const transfer=new DataTransfer(); transfer.items.add(new File([bytes],'small.png',{type:'image/png'})); document.querySelector('#ta').dispatchEvent(new ClipboardEvent('paste',{clipboardData:transfer,bubbles:true,cancelable:true})); })()`);
  await until("document.querySelector('.attachment-status')?.textContent==='图片已就绪'");
  assert.ok(await ev("document.querySelector('#attach').textContent.includes('small.png') && document.querySelector('#attach').textContent.includes('B')"));
  await ev("document.querySelector('#ta').value='检查这张图片';document.querySelector('#ta').dispatchEvent(new Event('input'));document.querySelector('#sendBtn').click()");
  await until("!document.querySelector('#sendBtn').classList.contains('is-sending')");
  const imageCall = calls.filter(c => c.path === '/api/chat/stream').at(-1);
  assert.ok(imageCall && imageCall.body.img === pngBase64);
  console.log('PASS small image shows file details and follows the existing vision chat path');
  // A code file uses the same confirmed, chunked upload path as documents.
  await ev(`(() => { const transfer=new DataTransfer(); transfer.items.add(new File(['print(1)'],'sample.py',{type:'text/x-python'})); document.querySelector('#ta').dispatchEvent(new ClipboardEvent('paste',{clipboardData:transfer,bubbles:true,cancelable:true})); document.querySelector('#sendBtn').click(); })()`);
  await until("!document.querySelector('#sendBtn').classList.contains('is-sending')");
  const codeCall = calls.find(c => c.path === '/api/chat' && c.body.attachments?.some(a => a.name === 'sample.py'));
  assert.ok(codeCall);
  assert.equal(chunks.at(-1).bytes.toString(), 'print(1)');
  assert.equal(codeCall.body.attachments[0].kind, 'file');
  console.log('PASS source code files upload as text attachments');
  // Oversized files remain visible as failed and cannot create a chat request.
  const beforeLarge = calls.length;
  await ev(`(() => { const file=new File(['x'],'large.py',{type:'text/x-python'}); Object.defineProperty(file,'size',{value:256*1024*1024+1}); const transfer=new DataTransfer(); transfer.items.add(file); document.querySelector('#ta').dispatchEvent(new ClipboardEvent('paste',{clipboardData:transfer,bubbles:true,cancelable:true})); })()`);
  await until("document.querySelector('.attachment-status.failed')?.textContent.includes('256 MB')");
  assert.equal(await ev("document.querySelector('#sendBtn').disabled"),true);
  await ev("document.querySelector('#sendBtn').click()");
  assert.equal(calls.length, beforeLarge);
  assert.ok(await ev("document.querySelector('#toast').textContent.includes('256 MB')"));
  await ev("document.querySelector('#atClr').click()");
  console.log('PASS oversized file shows a clear failure and cannot enter chat analysis');
  // A rejected chunk also must stop before /api/chat.
  const beforeFailedUpload = calls.filter(c => c.path === '/api/chat' || c.path === '/api/chat/stream').length;
  failNextUpload = true;
  await ev(`(() => { const transfer=new DataTransfer(); transfer.items.add(new File(['safe code'],'retry.py',{type:'text/x-python'})); document.querySelector('#ta').dispatchEvent(new ClipboardEvent('paste',{clipboardData:transfer,bubbles:true,cancelable:true})); document.querySelector('#sendBtn').click(); })()`);
  await until("document.querySelector('.attachment-status.failed')?.textContent.includes('fixture upload failed')");
  assert.equal(calls.filter(c => c.path === '/api/chat' || c.path === '/api/chat/stream').length, beforeFailedUpload);
  assert.equal(await ev("document.querySelector('#sendBtn').disabled"),true);
  await ev("document.querySelector('#atClr').click()");
  console.log('PASS failed chunk upload cannot enter chat analysis');
  // Paste a deliberately undecodable video. Upload must finish before metadata failure.
  await ev(`(() => { window.__framePromptSeen=false; window.confirm=(message)=>{if(String(message).includes('关键帧'))window.__framePromptSeen=true;return true}; const data=new DataTransfer(); data.items.add(new File(['undecodable safe fixture'],'safe.mp4',{type:'video/mp4'})); document.querySelector('#ta').dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true})); document.querySelector('#sendBtn').click(); })()`);
  await until("!document.querySelector('#sendBtn').classList.contains('is-sending')");
  const attachmentCall = calls.find(c => c.path === '/api/chat' && c.body.attachments?.some(a => a.name === 'safe.mp4'));
  assert.ok(attachmentCall);
  assert.equal(chunks.at(-1).bytes.toString(), 'undecodable safe fixture');
  assert.equal(chunks.at(-1).headers['X-Attachment-Confirmed'], '1');
  assert.equal(await ev('window.__framePromptSeen'), true);
  assert.equal(attachmentCall.body.attachments[0].kind, 'video');
  assert.ok(!('img' in attachmentCall.body));
  console.log('PASS pasted undecodable video uploads and sends metadata despite frame failure');
  await ev("document.querySelector('#gnav [data-page=\"agent-center\"]').click()");
  await until("!!document.querySelector('#ac-agent-grid') && document.querySelector('#ac-overview-error').textContent.includes('当前 Brain 版本不支持')");
  assert.equal(await ev("document.querySelectorAll('.ac-agent-card').length"), 3);
  console.log('PASS Agent workspace retains three cards and accurate missing-route error');
  win.destroy();
  app.exit(0);
}
app.whenReady().then(run).catch(error => {console.error(error.stack || error); app.exit(1);});
