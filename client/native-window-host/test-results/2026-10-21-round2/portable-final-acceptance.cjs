const WebSocket = require('ws');
const wait = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const targets = await fetch('http://127.0.0.1:9358/json/list').then(r => r.json());
  const target = targets.find(x => x.type === 'page');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.once('open', resolve); ws.once('error', reject); });
  let id = 0; const pending = new Map();
  ws.on('message', bytes => { const m = JSON.parse(bytes); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
  const evaluate = expression => new Promise((resolve, reject) => {
    const key = ++id; const timer = setTimeout(() => reject(new Error('CDP timeout')), 12000);
    pending.set(key, m => { clearTimeout(timer); if (m.error) return reject(new Error(m.error.message)); if (m.result?.exceptionDetails) return reject(new Error(m.result.exceptionDetails.exception?.description || m.result.exceptionDetails.text)); resolve(m.result?.result?.value); });
    ws.send(JSON.stringify({ id: key, method: 'Runtime.evaluate', params: { expression, returnByValue: true, awaitPromise: true } }));
  });
  const enc = value => JSON.stringify(value);
  const events = async () => JSON.parse(await evaluate('JSON.stringify(window.__p15Events || [])'));
  const waitEvent = async (start, type, predicate = () => true, timeout = 20000) => {
    const end = Date.now() + timeout;
    while (Date.now() < end) { const found = (await events()).slice(start).find(e => e.type === type && predicate(e)); if (found) return found; await wait(100); }
    throw new Error(`Timed out waiting for ${type}`);
  };
  await evaluate("[...document.querySelectorAll('.gbtn')].find(x=>x.textContent.includes('视频工作台')).click(); 'ok'");
  await wait(600);
  await evaluate("window.__p15Events=[]; window.vigil.videoWindow.onEvent(e=>window.__p15Events.push(e)); 'ok'");
  const profiles = [
    {id:'p15-r4-a',name:'Character Map',exePath:'C:\\Windows\\System32\\charmap.exe',args:''},
    {id:'p15-r4-b',name:'Notepad',exePath:'C:\\Windows\\System32\\notepad.exe',args:''},
  ];
  for (const p of profiles) await evaluate(`window.vigil.videoWindow.saveProfile(${enc(p)})`);
  const bounds = await evaluate("(()=>{const r=document.querySelector('#videoHostSlot').getBoundingClientRect();return {x:r.left,y:r.top,width:r.width,height:r.height,dpr:devicePixelRatio}})()");
  const results = [{type:'workbench',bounds}];
  let firstAttachHwnd = 0;
  for (let index=0; index<3; index++) {
    const profile = index === 1 ? profiles[1] : profiles[0];
    const start = await evaluate('window.__p15Events.length');
    await evaluate(`window.vigil.videoWindow.start(${enc(profile)},${enc(bounds)})`);
    await evaluate('window.vigil.videoWindow.visible(true)');
    const attached = await waitEvent(start,'attached');
    results.push({profile:profile.name,attached});
    if (index === 0) {
      firstAttachHwnd = attached.hwnd;
      await evaluate('window.vigil.videoWindow.action("FOCUS")');
      const focus = await waitEvent(start,'focus');
      results.push({profile:profile.name,focus});
      await evaluate('window.vigil.videoWindow.action("DETACH")');
      const detached = await waitEvent(start,'detached');
      results.push({profile:profile.name,detached});
      await evaluate('window.vigil.videoWindow.action("ATTACH")');
      const reattachStart = await evaluate('window.__p15Events.length');
      await evaluate(`window.vigil.videoWindow.start(${enc(profile)},${enc(bounds)})`);
      const reattached = await waitEvent(reattachStart,'attached');
      results.push({profile:profile.name,reattached,sameHwnd:reattached.hwnd===firstAttachHwnd});
      const marketButton = await evaluate("[...document.querySelectorAll('.gbtn')].find(x=>x.textContent.includes('插件市场')) ? true : false");
      if (!marketButton) throw new Error('Navigation to 插件市场 was unavailable');
      await evaluate("[...document.querySelectorAll('.gbtn')].find(x=>x.textContent.includes('插件市场')).click(); 'ok'");
      await wait(400);
      const slotHidden = await evaluate("!document.querySelector('#videoHostSlot')");
      await evaluate("[...document.querySelectorAll('.gbtn')].find(x=>x.textContent.includes('视频工作台')).click(); 'ok'");
      await wait(500);
      await evaluate('window.vigil.videoWindow.action("STATUS")');
      const statusStart = await evaluate('window.__p15Events.length') - 1;
      const status = await waitEvent(Math.max(0,statusStart),'state',e=>e.state==='attached');
      results.push({profile:profile.name,pageSwitch:{slotHidden,slotRestored:!!(await evaluate("document.querySelector('#videoHostSlot')")),status}});
      await evaluate('window.vigil.win.minimize()');
      await wait(400);
      await evaluate('window.vigil.win.maximize()');
      await wait(500);
      await evaluate('window.vigil.win.maximize()');
      await wait(700);
      await evaluate('window.vigil.videoWindow.action("STATUS")');
      const restoredStatus = await waitEvent(await evaluate('window.__p15Events.length')-1,'state',e=>e.state==='attached');
      results.push({profile:profile.name,minimizeRestore:{status:restoredStatus}});
    }
    await evaluate('window.vigil.videoWindow.action("CLOSE_APP")');
    const ended = await waitEvent(start,'exited',undefined,20000);
    results.push({profile:profile.name,ended});
  }
  console.log(JSON.stringify(results,null,2));
  ws.close();
})().catch(e=>{ console.error(e.stack||e); process.exitCode=1; });
