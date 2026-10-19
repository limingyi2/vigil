// Model settings regression: real renderer/preload, isolated IPC and model API.
// No production main process, credentials, model calls, or clipboard reads.
const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');
const assert = require('assert/strict');
const { spawnSync } = require('child_process');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-model-settings-'));
const client = path.resolve(__dirname, '..');
const indexPath = path.join(client, 'src', 'index.html');
const preloadPath = path.join(client, 'electron', 'preload.js');
const calls = [];
let mainWindow = null;
let settingsWindow = null;
let modelRows = [];
let current = { provider: 'custom', provider_name: '自定义', base: '', model: '', name: '', has_key: false };
let contextTier = 128000;
let contextCapacity = 500000;
let runMode = 'cloud';
const cliArgs = process.argv.slice(2);
const restartMode = cliArgs.includes('--verify-restart');
const stateArgIndex = cliArgs.indexOf('--fixture-state');
const fixtureStatePath = stateArgIndex >= 0 ? cliArgs[stateArgIndex + 1] : path.join(tmp, 'model-state.json');

if (restartMode) {
  assert.ok(fixtureStatePath, 'restart run requires a persisted fixture state');
  const saved = JSON.parse(fs.readFileSync(fixtureStatePath, 'utf8'));
  modelRows = saved.modelRows;
  current = saved.current;
  contextTier = saved.contextTier;
}

app.setPath('userData', path.join(tmp, 'electron'));
app.on('window-all-closed', () => {});
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const makeWindow = () => new BrowserWindow({
  show: false,
  width: 1120,
  height: 790,
  webPreferences: {
    preload: preloadPath,
    sandbox: true,
    contextIsolation: true,
    nodeIntegration: false,
    backgroundThrottling: false,
  },
});
const waitFor = async (fn, label, target = () => mainWindow) => {
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {
    const win = target();
    if (win && !win.isDestroyed()) {
      try { if (await win.webContents.executeJavaScript(fn, true)) return; }
      catch (_) { /* document is still loading */ }
    }
    await delay(40);
  }
  throw new Error('Timed out waiting for ' + label);
};
const evalIn = (win, script) => win.webContents.executeJavaScript(script, true);
const savedPayload = () => ({
  ok: true,
  models: modelRows.map(row => ({ ...row, active: row.provider === current.provider
    && row.base === current.base && row.model === current.model })),
  current: { ...current },
  context_window: contextPolicy(),
});
function contextPolicy() {
  const effective = contextCapacity ? Math.min(contextTier,contextCapacity) : contextTier;
  const preset = [96000,128000,300000].includes(effective);
  return { selected: contextTier, effective,
    label: preset ? effective/1000+'K' : '受限 '+effective.toLocaleString('en-US')+' Token',
    limited: effective < contextTier, reason: effective < contextTier ? 'model_capacity' : '' };
}
const runStatus = () => ({ok:true, mode:runMode, cloud:{model:current.model},
  local:{model:'fixture-local',num_ctx:12288,num_predict:2048,installed:['fixture-local'],reachable:true}});
const persistFixtureState = () => {
  if (fixtureStatePath) fs.writeFileSync(fixtureStatePath, JSON.stringify({ modelRows, current, contextTier }));
};

function apiFixture(method, rawPath, body) {
  const url = new URL(rawPath, 'http://fixture.local');
  const p = url.pathname;
  calls.push({ method, path: p, body });
  if (method === 'GET' && p === '/api/persona')
    return { ok: true, status: 200, json: { ok: true, key: 'default', name: 'Vigil', templates: [] } };
  if (method === 'GET' && p === '/api/stats')
    return { ok: true, status: 200, json: { ok: true, model: current.model || '未配置', turns: 0 } };
  if (method === 'GET' && p === '/api/run-mode')
    return {ok:true,status:200,json:runStatus()};
  if (method === 'POST' && p === '/api/run-mode') {
    runMode = body.mode;
    return {ok:true,status:200,json:{ok:true,status:runStatus(),warnings:[]}};
  }
  if (method === 'POST' && p === '/api/models/context') {
    contextTier = body.tier; persistFixtureState();
    return {ok:true,status:200,json:{ok:true,context_window:contextPolicy()}};
  }
  if (method === 'GET' && p === '/api/history')
    return { ok: true, status: 200, json: { items: [] } };
  if (method === 'GET' && p === '/api/plugins/frontend')
    return { ok: true, status: 200, json: { version: 1, plugins: [] } };
  if (method === 'GET' && p === '/api/models/saved')
    return { ok: true, status: 200, json: savedPayload() };
  if (method === 'POST' && p === '/api/models/list')
    return { ok: true, status: 200, json: {
      ok: true,
      current: { ...current, api_key: '', api_key_set: false,
        keys_by_provider: { custom: false }, model_mode: 'cloud', ollama: {}, delegation: {} },
      providers: [{ key: 'custom', name: '自定义（OpenAI 兼容）', base: '' }],
      groups: { chat: [], vision: [], image: [], audio: [] }, source: 'fixture', count: 0,
    } };
  if (method === 'POST' && p === '/api/models/save') {
    const mask = body.api_key ? body.api_key.slice(0, 6) + '****' + body.api_key.slice(-3) : '';
    const row = {
      provider: body.provider,
      provider_name: '自定义（OpenAI 兼容）',
      base: body.base,
      model: body.model,
      name: body.name || body.model,
      has_key: !!body.api_key,
      key_mask: mask,
      active: false,
      unsaved: false,
    };
    modelRows = modelRows.filter(old => !(old.provider === row.provider
      && old.base === row.base && old.model === row.model));
    modelRows.push(row);
    persistFixtureState();
    return { ok: true, status: 200, json: savedPayload() };
  }
  if (method === 'POST' && p === '/api/models/quick-switch') {
    const row = modelRows.find(m => m.provider === body.provider
      && m.base === body.base && m.model === body.model);
    if (!row) return { ok: true, status: 200, json: { ok: false, err: '测试清单没有此模型' } };
    current = { ...row, has_key: row.has_key };
    persistFixtureState();
    return { ok: true, status: 200, json: { ok: true, current: { ...current } } };
  }
  if (method === 'POST' && p === '/api/models/forget') {
    modelRows = modelRows.filter(m => !(m.provider === body.provider
      && m.base === body.base && m.model === body.model));
    return { ok: true, status: 200, json: { ok: true, removed: true } };
  }
  if (method === 'GET' && p === '/api/voices')
    return { ok: true, status: 200, json: { ok: true, items: [] } };
  return { ok: true, status: 200, json: { ok: true, items: [] } };
}

async function launchMain() {
  mainWindow = makeWindow();
  await mainWindow.loadFile(indexPath);
  await waitFor("!!document.querySelector('#modelBtn')", 'main chat renderer');
  return mainWindow;
}

async function run() {
  ipcMain.handle('api', (_event, { method, path: requestPath, body }) =>
    apiFixture(method, requestPath, body));
  ipcMain.handle('cfg:get', () => ({ wizard_done: true, base: 'http://127.0.0.1:18787', sync: {}, roots: [] }));
  ipcMain.handle('app:version', () => 'model-settings-fixture');
  ipcMain.handle('app:instance-id', () => 'model-settings-test-process');
  ipcMain.handle('wallpaper:request', () => null);
  ipcMain.handle('auto:connect', () => ({ ok: false }));
  ipcMain.handle('updater:check', () => ({ ok: true, update: false }));
  ipcMain.handle('sidecar:status', () => ({ status: 'stopped' }));
  ipcMain.handle('plugins:load', () => ({ ok: true }));
  ipcMain.handle('settings:open', async (_event, options) => {
    if (settingsWindow && !settingsWindow.isDestroyed()) return { ok: true, existing: true };
    settingsWindow = makeWindow();
    await settingsWindow.loadFile(indexPath, { query: {
      window: 'settings', section: options.section || 'model', source: options.source || 'local',
    } });
    return { ok: true, existing: false };
  });

  if (restartMode) {
    await launchMain();
    await evalIn(mainWindow, "document.querySelector('#modelBtn').click()");
    await waitFor("document.querySelector('#mqList').textContent.includes('隔离测试模型')", 'model after client process restart');
    await waitFor("document.querySelector('#modelBtn').textContent.includes('隔离测试模型')", 'active model after client process restart');
    await waitFor("document.querySelector('[data-context-tier=\"300000\"]').getAttribute('aria-checked')==='true'", 'context tier after process restart');
    assert.equal(await evalIn(mainWindow, "document.querySelector('#mqContextValue').textContent"), '300K');
    console.log('PASS a fresh Electron process reads the persisted model and active selection');
    return;
  }

  await launchMain();
  assert.equal(await evalIn(mainWindow, "document.querySelector('#gnav').lastElementChild.id"), 'settingsBtn');
  await evalIn(mainWindow, "document.querySelector('#settingsBtn').click()");
  await waitFor("!!document.querySelector('#mAddToggle')", 'settings model manager', () => settingsWindow);
  assert.equal(await evalIn(settingsWindow, "document.body.classList.contains('settings-window')"), true);
  assert.equal(await evalIn(settingsWindow, "getComputedStyle(document.querySelector('#gnav')).display"), 'none');

  await evalIn(settingsWindow, `(() => {
    document.querySelector('#mAddToggle').click();
    document.querySelector('#mAddName').value = '隔离测试模型';
    document.querySelector('#mAddProvider').value = 'custom';
    document.querySelector('#mAddBase').value = 'http://127.0.0.1:19091/v1';
    document.querySelector('#mAddModel').value = 'fixture/chat-model';
    document.querySelector('#mAddKey').value = 'sk-abcd12345678xyz';
    document.querySelector('#mAddSave').click();
  })()`);
  await waitFor("document.querySelector('#mSavedRows').textContent.includes('隔离测试模型')", 'saved test model', () => settingsWindow);
  const managerText = await evalIn(settingsWindow, "document.querySelector('#mSavedRows').textContent");
  assert.ok(managerText.includes('sk-abc****xyz'), managerText);
  assert.ok(!managerText.includes('sk-abcd12345678xyz'), 'the model list must not expose the API key');
  assert.equal(await evalIn(settingsWindow, "document.querySelector('#mAddKey').value"), '');

  // Recreate the main client window against the same saved model source.
  settingsWindow.destroy();
  settingsWindow = null;
  mainWindow.destroy();
  await launchMain();
  await evalIn(mainWindow, "document.querySelector('#modelBtn').click()");
  await waitFor("document.querySelector('#mqList').textContent.includes('隔离测试模型')", 'model after client window restart');
  assert.equal(await evalIn(mainWindow, "document.querySelector('#modelPop .lph').textContent.includes('选择模型')"), true);
  assert.equal(await evalIn(mainWindow, "document.querySelectorAll('#mqList .mqrow').length"), 1);
  assert.equal(await evalIn(mainWindow, "document.querySelector('#mqSearch').hidden"), true);
  await evalIn(mainWindow, "document.querySelector('#mqSearchToggle').click()");
  assert.equal(await evalIn(mainWindow, "document.querySelector('#mqSearch').hidden"), false);
  await evalIn(mainWindow, "document.querySelector('#mqSearchToggle').click()");
  await evalIn(mainWindow, "document.querySelector('#mqList .mqrow').click()");
  await waitFor("document.querySelector('#modelBtn').textContent.includes('隔离测试模型')", 'model switch result');
  assert.equal(await evalIn(mainWindow, "document.querySelectorAll('#mqList .mqrow.on').length"), 1);
  assert.equal(await evalIn(mainWindow, "document.querySelector('#mqList .mqrow.on').getAttribute('aria-pressed')"), 'true');
  assert.equal(await evalIn(mainWindow, "!!document.querySelector('#mqList .mqrow.on .mq-check svg')"), true);
  await waitFor("!document.querySelector('#mqMsg').textContent.includes('正在切到')", 'model switch status completion');
  const pickerAnchor = await evalIn(mainWindow, "(()=>{const p=document.querySelector('#modelPop').getBoundingClientRect(),b=document.querySelector('#modelBtn').getBoundingClientRect();return{viewport:{width:innerWidth,height:innerHeight},popup:{left:p.left,right:p.right,top:p.top,bottom:p.bottom,width:p.width},button:{left:b.left,right:b.right,top:b.top},searchHidden:document.querySelector('#mqSearch').hidden}})()");
  assert.ok(pickerAnchor.popup.left<=pickerAnchor.button.left && pickerAnchor.popup.right>=pickerAnchor.button.right, JSON.stringify(pickerAnchor));
  assert.ok(pickerAnchor.popup.width<=361, JSON.stringify(pickerAnchor));
  assert.ok(pickerAnchor.popup.bottom<=pickerAnchor.button.top, JSON.stringify(pickerAnchor));
  assert.equal(pickerAnchor.searchHidden,true);
  await evalIn(mainWindow, "document.querySelector('#mqContextToggle').click()");
  assert.deepEqual(await evalIn(mainWindow, "Array.from(document.querySelectorAll('#mqContextMenu button')).map(b=>b.textContent.trim())"), ['96K','128K','300K']);
  for (const tier of [96000,128000,300000]) {
    await evalIn(mainWindow, `document.querySelector('[data-context-tier="${tier}"]').click()`);
    await waitFor(`document.querySelector('#mqContextValue').textContent==='${tier/1000}K'`, 'saved context '+tier);
    assert.equal(await evalIn(mainWindow, "document.querySelectorAll('#mqContextMenu [aria-checked=\"true\"]').length"),1);
  }
  assert.equal(await evalIn(mainWindow,"document.body.innerText.includes('BM25') || document.body.innerText.includes('向量检索') || document.body.innerText.includes('历史检索')"),false);
  contextCapacity=100000;
  await evalIn(mainWindow,"document.querySelector('#mqClose').click();document.querySelector('#modelBtn').click()");
  await waitFor("document.querySelector('#mqContextValue').textContent==='受限 100,000 Token'",'capacity limit');
  assert.ok(await evalIn(mainWindow,"document.querySelector('#mqContextNote').textContent.includes('受限 100,000 Token')"));
  contextCapacity=0;
  await evalIn(mainWindow,"document.querySelector('#mqClose').click();document.querySelector('#modelBtn').click()");
  await waitFor("document.querySelector('#mqContextValue').textContent==='300K'",'unknown capacity uses selected target');
  contextCapacity=500000;
  await evalIn(mainWindow,"document.querySelector('#mqClose').click();document.querySelector('#modelBtn').click()");
  await waitFor("document.querySelector('#mqContextValue').textContent==='300K'",'restored capable model');
  await evalIn(mainWindow,"document.querySelector('#mqContextToggle').click()");
  // Keep the same chat nodes and draft through all execution modes.
  await evalIn(mainWindow,"document.querySelector('#msgs').insertAdjacentHTML('beforeend','<div id=preserved-message>已存在的会话消息</div>');document.querySelector('#ta').value='未发送草稿';window.__chatNode=document.querySelector('#msgs');true");
  for (const mode of ['local','hybrid','cloud']) {
    await evalIn(mainWindow,"document.querySelector('#mqClose').click();document.querySelector('#modeChip').click()");
    await evalIn(mainWindow,`document.querySelector('#plusPop [data-mode="${mode}"]').click()`);
    await waitFor(`document.querySelector('#modeChip').dataset.mode==='${mode}'`, 'mode '+mode);
    assert.equal(await evalIn(mainWindow,"document.querySelector('#msgs')===window.__chatNode && !!document.querySelector('#preserved-message') && document.querySelector('#ta').value==='未发送草稿'"),true);
  }
  await evalIn(mainWindow,"document.querySelector('#plusPop').hidden=true;document.querySelector('#modelBtn').click()");
  await waitFor("!document.querySelector('#modelPop').hidden",'context menu after mode switching');
  await evalIn(mainWindow,"document.querySelector('#mqContextToggle').click()");
  await evalIn(mainWindow,"document.querySelector('#mqContextMenu button').focus();document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true}))");
  assert.equal(await evalIn(mainWindow,"document.activeElement.dataset.contextTier"),'300000');
  console.log('PASS three cloud context tiers, capacity limits, retrieval implementation remains hidden, mode switches preserve chat');
  await evalIn(mainWindow, "document.documentElement.dataset.theme='dark'");
  const evidenceDir = path.resolve(client, '..', '.local-dev', 'context-evidence');
  fs.mkdirSync(evidenceDir, { recursive: true });
  mainWindow.show(); await delay(180);
  await delay(2600);
  fs.writeFileSync(path.join(evidenceDir, 'model-picker-dark.png'), (await mainWindow.webContents.capturePage()).toPNG());
  mainWindow.hide();
  await evalIn(mainWindow, "document.querySelector('#mqManage').click()");
  await waitFor("!!document.querySelector('#mAddToggle')", 'model management from quick switch', () => settingsWindow);
  console.log('PASS compact model picker shows one current marker and opens model management');
  settingsWindow.destroy();
  settingsWindow = null;
  const switchCall = calls.findLast(call => call.path === '/api/models/quick-switch');
  assert.deepEqual(switchCall.body, {
    provider: 'custom', base: 'http://127.0.0.1:19091/v1', model: 'fixture/chat-model',
  });
  mainWindow.destroy();
  mainWindow = null;
  const child = spawnSync('node', [
    path.join(client, 'node_modules', 'electron', 'cli.js'),
    path.join(__dirname, 'model-settings-test-harness'),
    '--verify-restart', '--fixture-state', fixtureStatePath,
  ], { cwd: client, encoding: 'utf8', timeout: 30000, windowsHide: true });
  assert.equal(child.status, 0, [child.stdout, child.stderr, child.error && child.error.message].filter(Boolean).join('\n'));
  assert.ok(child.stdout.includes('PASS a fresh Electron process reads the persisted model and active selection'), child.stdout);
  console.log('PASS bottom-left settings opens its own model window');
  console.log('PASS named model is saved with a masked API key');
  console.log('PASS recreated client reads and switches to the saved model');
  console.log('PASS full client process restart reads the persisted model and active selection');
}

app.whenReady().then(run).then(() => 0).catch(error => {
  console.error(error.stack || error);
  return 1;
}).then(exitCode => {
  if (settingsWindow && !settingsWindow.isDestroyed()) settingsWindow.destroy();
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.destroy();
  try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (_) {}
  app.exit(exitCode);
});
