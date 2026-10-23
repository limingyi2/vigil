// Exercise a genuinely fresh installed app with a local fake OpenAI-compatible provider.
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const { spawn, execFileSync } = require('child_process');
const http = require('http');
const WS = require('ws');

const [install, mode = 'first'] = process.argv.slice(2);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let app, ws;
let nextId = 0;
let completionRequests = 0;
let freshChatRequests = 0;
const pending = new Map();
const provider = http.createServer((req, res) => {
  if (req.method === 'GET') {
    const body = JSON.stringify({ data: [{ id: 'fresh-fixture-model' }] });
    res.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) });
    res.end(body);
    return;
  }
  let raw = '';
  req.on('data', chunk => { raw += chunk; });
  req.on('end', () => {
    completionRequests++;
    const body = JSON.parse(raw);
    if ((body.messages || []).some(message => JSON.stringify(message).includes('P8_FRESH_CHAT_SENTINEL'))) {
      freshChatRequests++;
    }
    assert.equal(req.headers.authorization, 'Bearer fresh-fixture-key');
    const response = JSON.stringify({ choices: [{ message: { content: 'FRESH_INSTALL_CHAT_OK' }, finish_reason: 'stop' }],
      usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } });
    res.writeHead(200, { 'Content-Type': body.stream ? 'text/event-stream' : 'application/json' });
    if (body.stream) res.end(`data: ${JSON.stringify({ choices: [{ delta: { content: 'FRESH_INSTALL_CHAT_OK' } }] })}\n\ndata: ${JSON.stringify({ choices: [{ delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } })}\n\ndata: [DONE]\n\n`);
    else res.end(response);
  });
});

async function evaluate(expression) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, { resolve, reject, expression });
    ws.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: { expression, awaitPromise: true, returnByValue: true } }));
  });
}

async function start() {
  const env = { ...process.env, PATH: `${process.env.SystemRoot}\\System32`, VIGIL_SIDECAR_DISABLE: '1', VIGIL_TEST_NO_UPDATES: '1' };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.VIGIL_CLOUD_TOKEN;
  delete env.VIGIL_CLOUD_URL;
  app = spawn(path.join(install, fs.existsSync(path.join(install, 'Kestrel.exe')) ? 'Kestrel.exe' : 'Vigil.exe'), ['--remote-debugging-port=0'], { env, windowsHide: true, stdio: 'ignore' });
  let connected = false;
  for (let i = 0; i < 360; i++) {
    if (app.exitCode != null) throw Error(`Installed Electron exited ${app.exitCode}`);
    try {
      const activePort = Number(fs.readFileSync(path.join(process.env.VIGIL_USER_DATA, 'DevToolsActivePort'), 'utf8').split('\n')[0]);
      const tabs = await (await fetch(`http://127.0.0.1:${activePort}/json/list`)).json();
      const tab = tabs.find(item => item.type === 'page' && item.url.includes('index.html') && !item.url.includes('settings'));
      if (tab) {
        ws = new WS(tab.webSocketDebuggerUrl);
        await new Promise((resolve, reject) => { ws.once('open', resolve); ws.once('error', reject); });
        connected = true;
        break;
      }
    } catch (_) { }
    await sleep(250);
  }
  assert.ok(connected, 'Installed Electron renderer did not become available');
  ws.on('message', raw => {
    const message = JSON.parse(raw);
    const item = pending.get(message.id);
    if (!item) return;
    pending.delete(message.id);
    if (message.error || message.result?.exceptionDetails) {
      const exception = message.result?.exceptionDetails?.exception;
      item.reject(Error(message.error?.message || exception?.description || message.result?.exceptionDetails?.text || 'renderer evaluation failed'));
    }
    else item.resolve(message.result?.result?.value);
  });
  let bridge = false;
  for (let i = 0; i < 100; i++) {
    try { bridge = await evaluate('!!window.vigil?.sidecar'); } catch (_) { }
    if (bridge) break;
    await sleep(100);
  }
  assert.ok(bridge, 'Installed preload was not available');
  let health;
  for (let i = 0; i < 480; i++) {
    health = await evaluate('vigil.sidecar.status()');
    if (['ready', 'failed'].includes(health.status)) break;
    await sleep(250);
  }
  assert.equal(health.status, 'ready', health.lastErr);
  assert.ok(health.exe.startsWith(install), 'Brain executable was not loaded from the fresh install');
  const runtime = await evaluate("vigil.api('GET','/api/runtime')");
  assert.equal(runtime.status, 200, 'Bundled Brain health endpoint did not return 200');
  assert.equal(runtime.json.pid, health.pid);
  const home = health.home;
  assert.ok(home && !home.startsWith(install), 'Brain user data should live outside the application install directory');
  const wallpaperExtensionApi = await evaluate('Boolean(vigil.wallpaperExtension)');
  assert.equal(wallpaperExtensionApi, false, 'Core must not expose the retired Wallpaper desktop-extension API');
  assert.equal(fs.existsSync(path.join(home, 'data', 'installed_plugins', 'wallpaper')), false,
    'Fresh Core must not bundle or install Wallpaper implicitly');
  const initial = await evaluate("Boolean(document.querySelector('#hero')&&!document.querySelector('#hero').hidden&&document.querySelector('#ta')&&document.querySelector('#msgs').children.length===0)");
  assert.ok(initial, 'Fresh launch did not show an empty welcome page and composer');
  let clientBrainConnected = false;
  for (let i = 0; i < 120; i++) {
    clientBrainConnected = await evaluate('Boolean(API.state.conn&&API.state.conn.ok)');
    if (clientBrainConnected) break;
    await sleep(100);
  }
  assert.ok(clientBrainConnected, 'Electron renderer did not finish connecting to its bundled Brain');

  let conversationId;
  if (mode === 'first') {
    const port = provider.address().port;
    const applied = await evaluate(`vigil.api('POST','/api/models/apply',{provider:'custom',api_base:${JSON.stringify(`http://127.0.0.1:${port}/v1`)},api_key:'fresh-fixture-key',chat:'fresh-fixture-model'})`);
    assert.equal(applied.status, 200, 'Could not configure the local Fake Provider');
    assert.equal(applied.json?.ok, true, 'Brain rejected the local Fake Provider fixture');
    await evaluate("(()=>{const input=document.querySelector('#ta');input.value='P8_FRESH_CHAT_SENTINEL';input.dispatchEvent(new Event('input',{bubbles:true}));return true})()");
    let composerReady = false;
    for (let i = 0; i < 120; i++) {
      composerReady = await evaluate("Boolean(document.querySelector('#sendBtn')&&!document.querySelector('#sendBtn').disabled)");
      if (composerReady) break;
      await sleep(100);
    }
    assert.ok(composerReady, 'Installed composer did not become ready after Brain startup');
    await evaluate("document.querySelector('#sendBtn').click();true");
    let visible = false;
    for (let i = 0; i < 180; i++) {
      visible = await evaluate("document.querySelector('#msgs')?.innerText.includes('FRESH_INSTALL_CHAT_OK')");
      if (visible) break;
      await sleep(100);
    }
    const chatDiagnostic = await evaluate(`(()=>({providerRequests:${completionRequests},freshChatRequests:${freshChatRequests},busy:document.querySelector('#sendBtn')?.getAttribute('aria-busy'),disabled:document.querySelector('#sendBtn')?.disabled,composerLength:document.querySelector('#ta')?.value.length||0,activeConversation:Boolean(API.state.chatConversationId),turnCount:document.querySelectorAll('#msgs .turn').length,assistantTurnCount:document.querySelectorAll('#msgs .turn:not(.me)').length,visible:document.querySelector('#msgs')?.innerText.includes('FRESH_INSTALL_CHAT_OK')}))()`);
    console.log('FRESH_CHAT_DIAGNOSTIC ' + JSON.stringify(chatDiagnostic));
    assert.ok(visible, 'Fresh install could not send and display a Fake Provider chat response');
    assert.ok(completionRequests > 0, 'Fake Provider did not receive a chat completion request');
    assert.ok(freshChatRequests > 0, 'Fake Provider did not receive the actual fresh-chat sentinel');
    conversationId = await evaluate('API.state.chatConversationId');
    assert.ok(conversationId, 'First sent message did not create a conversation');
  } else {
    const sessions = await evaluate("vigil.api('GET','/api/conversations?src=pc')");
    assert.equal(sessions.status, 200);
    const found = sessions.json.items[0];
    assert.ok(found, 'Chat history did not survive an Electron restart');
    conversationId = found.id;
    let rowReady = false;
    for (let i = 0; i < 100; i++) {
      rowReady = await evaluate(`Boolean(document.querySelector('[data-session="${conversationId}"]'))`);
      if (rowReady) break;
      await sleep(100);
    }
    assert.ok(rowReady, 'Persisted conversation was not rendered in the restarted session list');
    await evaluate(`document.querySelector('[data-session="${conversationId}"]').click();true`);
    let restored = false;
    for (let i = 0; i < 100; i++) {
      restored = await evaluate(`API.state.chatConversationId===${JSON.stringify(conversationId)}&&document.querySelector('#msgs')?.innerText.includes('FRESH_INSTALL_CHAT_OK')`);
      if (restored) break;
      await sleep(100);
    }
    assert.ok(restored, 'Restarted Electron could not restore the first conversation and response');
    const savedHistory = await evaluate(`vigil.api('GET','/api/history?src=pc&conversation_id=${conversationId}')`);
    assert.equal(savedHistory.status, 200);
    assert.ok(savedHistory.json.display_turns?.some(turn => turn.input === 'P8_FRESH_CHAT_SENTINEL' &&
      turn.content === 'FRESH_INSTALL_CHAT_OK' && turn.status === 'succeeded'),
      'Brain did not restore the persisted successful chat turn from its conversation-scoped display records');
  }
  console.log(JSON.stringify({ result: 'passed', mode, conversation_id: conversationId, brain_home: home,
    brain_health: 'ready', welcome_page: true, fake_provider_chat: mode === 'first' ? 'passed' : 'restored',
    fake_provider_requests: completionRequests,
    fresh_chat_provider_requests: freshChatRequests,
    wallpaper_uninstalled_and_idle: true, durable_chat_history: mode === 'restart' }));
}

async function stop() {
  if (ws) ws.close();
  if (app?.pid) {
    try { execFileSync('taskkill', ['/PID', String(app.pid), '/T', '/F'], { stdio: 'ignore' }); } catch (_) { }
  }
}

provider.listen(0, '127.0.0.1', async () => {
  try { await start(); }
  catch (error) { console.error(error.stack || error.message); process.exitCode = 1; }
  finally { await stop(); provider.close(); }
});
