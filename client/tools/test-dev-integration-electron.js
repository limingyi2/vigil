// Focused merge regression: real renderer/preload, isolated IPC services only.
// No production main process, credentials, model calls, or clipboard reads.
const {app, BrowserWindow, ipcMain} = require('electron');
const fs = require('fs'), path = require('path'), os = require('os');
const assert = require('assert/strict');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-dev-ui-'));
app.setPath('userData', path.join(tmp, 'electron'));
const client = path.resolve(__dirname, '..');
const calls = [], chunks = [];
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
  await until("!document.querySelector('#sendBtn').classList.contains('is-sending')");
  assert.ok(calls.some(c => c.path === '/api/creator/draft'));
  console.log('PASS Creator retains separate draft route');
  await ev("document.querySelector('#creatorBtn').click(); document.querySelector('#creatorBtn').click()");
  // Paste a deliberately undecodable video. Upload must finish before metadata failure.
  await ev(`(() => { const data=new DataTransfer(); data.items.add(new File(['undecodable safe fixture'],'safe.mp4',{type:'video/mp4'})); document.querySelector('#ta').dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true})); document.querySelector('#sendBtn').click(); })()`);
  await until("!document.querySelector('#sendBtn').classList.contains('is-sending')");
  const attachmentCall = calls.find(c => c.path === '/api/chat' && c.body.attachments?.length);
  assert.ok(attachmentCall);
  assert.equal(chunks.length, 1);
  assert.equal(chunks[0].bytes.toString(), 'undecodable safe fixture');
  assert.equal(chunks[0].headers['X-Attachment-Confirmed'], '1');
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
