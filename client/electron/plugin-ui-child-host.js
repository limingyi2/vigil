'use strict';

// Runs only in a second Electron process. Plugin renderers, their Chromium
// browser process and their lifecycle are therefore independent of Core.
const { app, BrowserWindow, ipcMain, protocol, session } = require('electron');
const net = require('net');
const path = require('path');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');

protocol.registerSchemesAsPrivileged([{ scheme: 'vigil-plugin', privileges: {
  standard: true, secure: true, supportFetchAPI: true, corsEnabled: false,
} }]);

const port = Number(process.argv[process.argv.indexOf('--vigil-plugin-ui-host') + 1]);
const token = process.argv[process.argv.indexOf('--vigil-plugin-ui-host') + 2];
const hostProfile = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-plugin-ui-host-'));
app.setPath('userData', hostProfile);
const windows = new Map();
const contents = new Map();
const readyWindows = new Set();
const pluginDescriptors = new Map();
const generations = new Map();
const recoveryTimers = new Map();
const pending = new Map();
let socket;
let seq = 0;
let input = '';

function send(message) {
  if (!socket || socket.destroyed) throw new Error('Plugin host broker connection is closed');
  socket.write(JSON.stringify(message) + '\n');
}
function broker(pluginId, request) {
  const id = ++seq;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('Plugin Broker timeout')); }, 10000);
    pending.set(id, { resolve, reject, timer });
    try { send({ type: 'broker', id, pluginId, request }); }
    catch (error) { clearTimeout(timer); pending.delete(id); reject(error); }
  });
}
function receive(message) {
  if (message.type === 'response') {
    const item = pending.get(message.id);
    if (!item) return;
    pending.delete(message.id); clearTimeout(item.timer);
    if (message.ok) item.resolve(message.value); else item.reject(new Error(message.error || 'Broker denied request'));
    return;
  }
  if (message.type === 'command') {
    const id = String(message.pluginId || '');
    if (message.op === 'close') { closePlugin(id); send({ type: 'response', id: message.id, ok: true }); }
    else if (message.op === 'open') openPlugin(message.plugin).then(value => send({ type: 'response', id: message.id, ok: true, value }), error => send({ type: 'response', id: message.id, ok: false, error: String(error.message || error) }));
    else if (message.op === 'crash' && process.env.VIGIL_TEST_MODE === '1') {
      const win = windows.get(id);
      if (!win || win.isDestroyed()) send({ type: 'response', id: message.id, ok: false, error: 'Plugin renderer not found' });
      else { send({ type: 'response', id: message.id, ok: true }); setImmediate(() => { if (!win.isDestroyed()) win.webContents.forcefullyCrashRenderer(); }); }
    }
    else if (message.op === 'state') send({ type: 'response', id: message.id, ok: true, value: [...windows.entries()].map(([pluginId, win]) => ({ id: pluginId, ready: readyWindows.has(pluginId), pid: win.webContents.getOSProcessId(), preferences: win.webContents.getLastWebPreferences(), url: win.webContents.getURL() })) });
    else send({ type: 'response', id: message.id, ok: false, error: 'Unknown host command' });
  }
}
function closePlugin(id, preserveRecovery = false) {
  generations.set(id, (generations.get(id) || 0) + 1);
  if (!preserveRecovery) { clearTimeout(recoveryTimers.get(id)); recoveryTimers.delete(id); }
  pluginDescriptors.delete(id);
  const win = windows.get(id);
  windows.delete(id);
  readyWindows.delete(id);
  if (!win) return;
  contents.delete(win.webContents.id);
  if (!win.isDestroyed()) win.destroy();
}
function hostUrl(id) {
  const origin = 'p-' + crypto.createHash('sha256').update(id).digest('hex').slice(0, 20);
  return { origin, url: 'vigil-plugin://' + origin + '/index.html' };
}
async function openPlugin(plugin) {
  if (!plugin || !/^([a-zA-Z0-9][a-zA-Z0-9_-]{0,39})$/.test(plugin.id) || typeof plugin.source !== 'string') throw new Error('Invalid plugin host descriptor');
  const existing = windows.get(plugin.id);
  if (existing && !existing.isDestroyed()) { existing.show(); existing.focus(); return { ok: true, id: plugin.id, existing: true }; }
  const generation = (generations.get(plugin.id) || 0) + 1;
  generations.set(plugin.id, generation);
  pluginDescriptors.set(plugin.id, plugin);
  const { origin, url } = hostUrl(plugin.id);
  const partition = 'plugin-ui-' + plugin.id;
  const guestSession = session.fromPartition(partition);
  guestSession.protocol.handle('vigil-plugin', async request => {
    if (new URL(request.url).hostname !== origin || new URL(request.url).pathname !== '/index.html') return new Response('Not found', { status: 404 });
    let mediaOrigin = '';
    if (plugin.id === 'desktop-wallpaper' && typeof plugin.mediaOrigin === 'string') {
      try {
        const parsed = new URL(plugin.mediaOrigin);
        if (parsed.protocol === 'http:' && parsed.hostname === '127.0.0.1' && !parsed.username && !parsed.password) mediaOrigin = ' ' + parsed.origin;
      } catch (_) {}
    }
    let wallpaperOrigin = '';
    if (plugin.id === 'desktop-wallpaper' && typeof plugin.wallpaperBase === 'string') {
      try {
        const parsed = new URL(plugin.wallpaperBase);
        if (parsed.protocol === 'http:' && parsed.hostname === '127.0.0.1' && !parsed.username && !parsed.password
          && /^\/w\/[a-f0-9]{32,64}$/.test(parsed.pathname)) wallpaperOrigin = ' ' + parsed.origin;
      } catch (_) {}
    }
    const csp = `default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:${mediaOrigin}${wallpaperOrigin}; media-src blob:${mediaOrigin}${wallpaperOrigin}; frame-src${wallpaperOrigin || " 'none'"}; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'`;
    return new Response(require('fs').readFileSync(path.join(__dirname, '..', 'src', 'plugin-host.html')),
      { headers: { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': csp, 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer' } });
  });
  const win = new BrowserWindow({ width: 980, height: 760, minWidth: 680, minHeight: 480, show: false, title: String(plugin.name || 'Plugin').slice(0, 80),
    webPreferences: { preload: path.join(__dirname, 'plugin-ui-preload.js'), partition, nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, allowRunningInsecureContent: false, devTools: !app.isPackaged } });
  windows.set(plugin.id, win); contents.set(win.webContents.id, plugin.id);
  console.info('[plugin-host] opening guest:', plugin.id, 'sourceBytes=' + Buffer.byteLength(plugin.source, 'utf8'));
  win.webContents.on('console-message', (_event, level, message) => console.log('[plugin-renderer]', plugin.id, level, message));
  win.webContents.on('did-fail-load', (_event, code, description, failedUrl) => console.warn('[plugin-renderer] load failed:', code, description, failedUrl));
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', (event, target) => { if (target !== url) event.preventDefault(); });
  win.webContents.on('will-frame-navigate', event => event.preventDefault());
  win.webContents.on('render-process-gone', (_event, details) => {
    console.warn('[plugin-host] renderer crashed:', plugin.id, JSON.stringify(details || {}));
    try { send({ type: 'renderer-crashed', pluginId: plugin.id, reason: String(details?.reason || 'unknown') }); } catch (_) {}
    closePlugin(plugin.id);
    setImmediate(() => app.quit());
  });
  win.once('closed', () => { contents.delete(win.webContents.id); if (windows.get(plugin.id) === win) windows.delete(plugin.id); });
  await win.loadURL(url);
  const payload = JSON.stringify({ name: String(plugin.name || 'Plugin').slice(0, 80), source: plugin.source, style: String(plugin.style || '') });
  const script = `(()=>{const p=${payload};window.__VIGIL_PLUGIN_UI_MODE__='management';document.title=p.name;document.getElementById('plugin-title').textContent=p.name;if(p.style){const s=document.createElement('style');s.textContent=p.style;document.head.appendChild(s)}const listeners=Object.create(null);let page=null;window.VigilPlugin={registerPage(x){if(!x||typeof x.render!=='function'||page)return false;page=x;Promise.resolve(window.vigilPlugin.registerPage({id:String(x.id||''),name:String(x.name||''),icon:String(x.icon||'puzzle')})).catch(()=>{});const root=document.getElementById('plugin-view');try{x.render({view:root,topbar:document.getElementById('plugin-title'),panel:root,src:'pc',refresh:()=>x.render({view:root,topbar:document.getElementById('plugin-title'),panel:root,src:'pc',refresh:()=>{}}),navigate:()=>{},onDispose:()=>{}});return true}catch(e){root.textContent='插件页面加载失败：'+String(e.message||e);return false}},injectCSS(n,c){const s=document.createElement('style');s.dataset.pluginStyle=String(n||'').slice(0,64);s.textContent=String(c||'');document.head.appendChild(s)},on(n,f){if(typeof f==='function')(listeners[n]=listeners[n]||[]).push(f)},emit(n,v){(listeners[n]||[]).forEach(f=>{try{f(v)}catch(e){console.error(e)}})}};window.VigilAPI={get:u=>window.vigilPlugin.get(u),post:(u,b)=>window.vigilPlugin.post(u,b)};window.API=window.VigilAPI;try{${plugin.source}}catch(e){document.getElementById('plugin-view').textContent='插件页面加载失败：'+String(e.message||e);console.error(e)}})()`;
  await win.webContents.executeJavaScript(script);
  readyWindows.add(plugin.id);
  console.info('[plugin-host] bootstrap executed:', plugin.id, 'view=' + (await win.webContents.executeJavaScript("document.getElementById('plugin-view')?.innerText || ''")));
  if (!win.isDestroyed()) win.show();
  return { ok: true, id: plugin.id };
}

ipcMain.handle('plugin-ui:request', async (event, request) => {
  const pluginId = contents.get(event.sender.id);
  if (!pluginId || event.senderFrame !== event.sender.mainFrame) throw new Error('Plugin renderer identity revoked');
  try { return await broker(pluginId, request); }
  catch (error) { return { ok: false, err: String(error.message || error).slice(0, 200) }; }
});
ipcMain.handle('plugin-ui:register-page', async (event, page) => {
  const pluginId = contents.get(event.sender.id);
  if (!pluginId || event.senderFrame !== event.sender.mainFrame) throw new Error('Plugin renderer identity revoked');
  try { return await broker(pluginId, { operation: 'register-page', page }); }
  catch (error) { return { ok: false, err: String(error.message || error).slice(0, 200) }; }
});

app.whenReady().then(() => {
  if (!Number.isInteger(port) || port < 1 || port > 65535 || typeof token !== 'string' || token.length < 32) throw new Error('Invalid host authentication configuration');
  socket = net.createConnection({ host: '127.0.0.1', port });
  socket.setEncoding('utf8');
  socket.on('connect', () => send({ type: 'hello', token }));
  socket.on('data', chunk => {
    input += chunk;
    if (input.length > 2 * 1024 * 1024) { socket.destroy(new Error('Host protocol frame too large')); return; }
    let pos;
    while ((pos = input.indexOf('\n')) >= 0) {
      const line = input.slice(0, pos); input = input.slice(pos + 1);
      try { receive(JSON.parse(line)); } catch (error) { console.error('[plugin-host] bad parent frame:', error.message); }
    }
  });
  socket.on('error', error => { console.error('[plugin-host] parent connection failed:', error.message); app.quit(); });
  socket.on('close', () => app.quit());
});
app.on('before-quit', () => {
  for (const win of windows.values()) if (!win.isDestroyed()) win.destroy();
  windows.clear(); contents.clear();
});
app.on('will-quit', () => { try { fs.rmSync(hostProfile, { recursive: true, force: true }); } catch (_) {} });
app.on('window-all-closed', () => { if (windows.size === 0 && recoveryTimers.size === 0) app.quit(); });
