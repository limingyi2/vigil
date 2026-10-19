'use strict';

const path = require('path');
const { pathToFileURL } = require('url');
const crypto = require('crypto');
const fs = require('fs');
const { createPluginUiHostManager } = require('./plugin-ui-host-manager');

const PLUGIN_ID = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$/;
const MAX_BODY = 64 * 1024;
const HOST_HTML = path.join(__dirname, '..', 'src', 'plugin-host.html');
const diag = (...args) => { if (process.env.VIGIL_DIAG_PLUGIN_LIFECYCLE === '1') console.log('[plugin-ui-diag]', Date.now(), ...args); };

function safeJsonSize(value) {
  try { return Buffer.byteLength(JSON.stringify(value == null ? null : value), 'utf8'); }
  catch (_) { return Infinity; }
}

function createPluginUiBroker({ BrowserWindow, app, session, coreWindow, getBrainAssets, getWallpaperState,
  getWallpaperAssets, requestBrain, resourceUrl, wallpaperRequest, wallpaperImport,
  wallpaperDirectory, mediaOrigin, openExternal, requestPermissions = async (_entry, missing) => missing,
  wallpaperPermissions = () => [], onSurfaceChanged = () => {} }) {
  const registrations = new Map();
  const originKeyFor = id => 'p-' + crypto.createHash('sha256').update(id).digest('hex').slice(0, 20);
  const hostUrlFor = id => 'vigil-plugin://' + originKeyFor(id) + '/index.html';
  const coreUrl = pathToFileURL(path.join(__dirname, '..', 'src', 'index.html')).href;
  const grantsPath = path.join(app.getPath('userData'), 'plugin-ui-permissions.json');
  let savedGrants = {};
  try {
    const parsed = JSON.parse(fs.readFileSync(grantsPath, 'utf8'));
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) savedGrants = parsed;
  } catch (error) { if (error.code !== 'ENOENT') console.warn('[plugin-ui] permission grants unreadable; denying saved grants'); }
  let disposed = false;
  const deniedThisSession = new Set();
  const host = createPluginUiHostManager({ app, onBrokerRequest });

  function saveGrants() {
    const temp = grantsPath + '.tmp-' + crypto.randomBytes(6).toString('hex');
    fs.mkdirSync(path.dirname(grantsPath), { recursive: true });
    fs.writeFileSync(temp, JSON.stringify(savedGrants, null, 2) + '\n', { mode: 0o600 });
    fs.renameSync(temp, grantsPath);
  }

  async function grantedPermissions(entry, requested) {
    if (entry.kind === 'desktop') {
      const existing = wallpaperPermissions(entry);
      return existing.includes('desktop.ui.contribute')
        ? ['plugin.ui.contribute', ...existing]
        : [];
    }
    const policy = new Set(['plugin.ui.contribute', 'brain.plugin.' + entry.pluginName + '.api']);
    const requestedList = Array.isArray(requested) ? requested.map(String) : [];
    const wanted = [...new Set(['plugin.ui.contribute', ...requestedList])].filter(p => policy.has(p));
    const key = entry.id;
    const current = Array.isArray(savedGrants[key]) ? savedGrants[key].filter(p => policy.has(p)) : [];
    const missing = wanted.filter(p => !current.includes(p));
    if (missing.length && !deniedThisSession.has(key)) {
      const allowed = await requestPermissions(entry, missing);
      if (!Array.isArray(allowed)) return current;
      const accepted = missing.filter(permission => allowed.includes(permission));
      if (accepted.length !== missing.length) deniedThisSession.add(key);
      if (!accepted.length) return current;
      savedGrants[key] = [...new Set([...current, ...accepted])];
      try { saveGrants(); } catch (error) { console.error('[plugin-ui] could not persist permission grants:', error.message); return current; }
    }
    return Array.isArray(savedGrants[key]) ? savedGrants[key].filter(p => policy.has(p)) : current;
  }

  function normalizeId(raw) {
    const id = String(raw || '');
    if (!PLUGIN_ID.test(id)) throw new Error('插件标识格式无效');
    return id;
  }

  async function sync() {
    if (disposed) return [];
    diag('registry sync begin', JSON.stringify([...registrations.keys()]));
    const next = new Map();
    const brain = await getBrainAssets().catch(error => { console.warn('[plugin-ui] Brain registry unavailable:', error.message); return null; });
    if (Array.isArray(brain?.plugins)) {
      const installed = new Set(brain.plugins.filter(item => item && item.name).map(item => 'brain-' + normalizeId(item.name)));
      let changed = false;
      for (const key of Object.keys(savedGrants)) if (key.startsWith('brain-') && !installed.has(key)) { delete savedGrants[key]; changed = true; }
      if (changed) { try { saveGrants(); } catch (error) { console.warn('[plugin-ui] stale grants could not be removed:', error.message); } }
    }
    for (const item of Array.isArray(brain?.plugins) ? brain.plugins : []) {
      const pluginName = normalizeId(item.name);
      if (!item.enabled || !item.js) continue;
      const id = 'brain-' + pluginName;
      const surfaces = Array.isArray(item.ui?.surfaces) ? item.ui.surfaces.filter(x => x === 'content-background') : [];
      next.set(id, { id, pluginName, name: String(item.name).slice(0, 80), source: String(item.js), style: String(item.css || ''), permissions: item.permissions, kind: 'directory', surfaces });
    }
    const state = await getWallpaperState().catch(() => null);
    if (state?.ok && state.installed && state.enabled) {
      const assets = await getWallpaperAssets().catch(() => null);
      if (assets?.ok && assets.script) next.set('desktop-wallpaper', {
        id: 'desktop-wallpaper', pluginName: 'wallpaper', name: 'Wallpaper Engine', source: String(assets.script), style: String(assets.style || ''), kind: 'desktop',
        surfaces: Array.isArray(assets.ui?.surfaces) ? assets.ui.surfaces.filter(x => x === 'content-background') : [],
      });
    }
    for (const id of registrations.keys()) if (!next.has(id)) revoke(id);
    for (const [id, entry] of next) {
      const previous = registrations.get(id);
      if (previous && (previous.source !== entry.source || previous.style !== entry.style || previous.kind !== entry.kind
        || JSON.stringify(previous.surfaces || []) !== JSON.stringify(entry.surfaces || []))) closeWindow(previous);
      const grants = await grantedPermissions(entry, entry.permissions);
      if (!grants.includes('plugin.ui.contribute')) { revoke(id); continue; }
      if (previous && previous.source === entry.source && previous.style === entry.style && previous.kind === entry.kind
        && JSON.stringify(previous.surfaces || []) === JSON.stringify(entry.surfaces || [])) {
        previous.permissions = entry.permissions;
        previous.grants = grants;
        registrations.set(id, previous);
      } else registrations.set(id, { ...entry, grants, window: null, opening: false });
    }
    diag('registry sync end', JSON.stringify([...registrations.keys()]));
    return [...registrations.values()].map(({ id, name, surfaces }) => ({ id, name, icon: 'puzzle', surfaces: surfaces || [],
      surfaceUrls: (surfaces || []).map(surface => ({ surface, url: hostUrlFor(id).replace('/index.html', '/surface.html') })) }));
  }

  async function surfaceAssets(rawId) {
    const id = normalizeId(rawId);
    const entry = registrations.get(id);
    if (!entry || !entry.grants.includes('plugin.ui.contribute') || !entry.surfaces?.includes('content-background')) {
      throw new Error('插件内容背景未启用');
    }
    const media = id === 'desktop-wallpaper' ? await mediaOrigin?.().catch(() => null) : null;
    const wallpaper = id === 'desktop-wallpaper' ? await wallpaperRequest('GET', '/settings').catch(() => null) : null;
    return { id, source: entry.source, style: entry.style, mediaOrigin: media || '', wallpaperBase: wallpaper?.base || '' };
  }

  async function surfaceAssetsForOrigin(originKey) {
    const id = [...registrations.keys()].find(candidate => originKeyFor(candidate) === originKey);
    if (!id) throw new Error('插件内容背景来源不存在');
    return surfaceAssets(id);
  }

  async function surfaceRequest(rawId, request) {
    const id = normalizeId(rawId);
    const entry = registrations.get(id);
    if (!entry || !entry.grants.includes('plugin.ui.contribute') || !entry.surfaces?.includes('content-background')) {
      throw new Error('插件内容背景权限已撤销');
    }
    if (!request || typeof request !== 'object') throw new Error('内容背景请求被拒绝');
    if (id === 'desktop-wallpaper') {
      const read = request.method === 'GET' && ['/settings', '/inventory'].includes(request.resource) && request.body == null;
      const update = request.method === 'PUT' && request.resource === '/settings' && request.body && typeof request.body === 'object'
        && safeJsonSize(request.body) <= MAX_BODY;
      if (!entry.grants.includes('desktop.wallpaper.control') || request.operation !== 'wallpaper.request' || (!read && !update)) {
        diag('surface request denied', id, JSON.stringify(request));
        throw new Error('内容背景请求被拒绝');
      }
      return { ok: true, ...(await wallpaperRequest(request.method, request.resource, update ? request.body : null)) };
    }
    return requestForEntry(entry, request);
  }

  function closeWindow(entry) {
    const current = entry?.window || entry?.opening;
    if (entry) entry.window = null;
    if (entry) entry.opening = false;
    if (current) { diag('renderer close begin', entry.id); void host.close(entry.id); }
  }

  function revoke(id) {
    const entry = registrations.get(id);
    if (!entry) return;
    diag('permission revoke begin', id, 'window=' + (entry.window?.id || 'none'));
    // Remove authorization before destroying the renderer so an in-flight IPC
    // request is denied as soon as disable/uninstall is observed.
    registrations.delete(id);
    closeWindow(entry);
    diag('permission revoke end', id, 'registered=' + registrations.has(id));
  }

  function assertCore(event) {
    const owner = BrowserWindow.fromWebContents(event.sender);
    if (!owner || owner !== coreWindow() || event.senderFrame !== event.sender.mainFrame) throw new Error('仅 Core 主页面可管理插件 UI');
    const url = event.senderFrame.url || '';
    if (url !== coreUrl) throw new Error('Core 页面来源无效');
  }

  async function open(event, rawId) {
    assertCore(event);
    const id = normalizeId(rawId);
    const entry = registrations.get(id);
    if (!entry || !entry.grants.includes('plugin.ui.contribute')) throw new Error('插件未启用或未获准注册 UI');
    const media = entry.id === 'desktop-wallpaper' ? await mediaOrigin?.().catch(() => null) : null;
    const wallpaper = entry.id === 'desktop-wallpaper' ? await wallpaperRequest('GET', '/settings').catch(() => null) : null;
    const descriptor = { id: entry.id, name: entry.name, source: entry.source, style: entry.style,
      mediaOrigin: media || '', wallpaperBase: wallpaper?.base || '' };
    if (entry.window) return host.open(descriptor);
    entry.opening = true;
    let result;
    try { result = await host.open(descriptor); }
    catch (error) { entry.opening = false; throw error; }
    if (registrations.get(id) !== entry || !entry.grants.includes('plugin.ui.contribute')) {
      entry.opening = false;
      await host.close(id);
      throw new Error('插件在 UI 启动期间已禁用');
    }
    entry.opening = false;
    entry.window = true;
    return result;
  }

  async function requestForEntry(entry, request) {
    if (!request || typeof request !== 'object' || safeJsonSize(request) > MAX_BODY) throw new Error('Broker 请求格式无效');
    const operation = String(request.operation || 'brain.request');
    if (entry.id === 'desktop-wallpaper' && entry.grants.includes('desktop.wallpaper.control')) {
      if (operation === 'wallpaper.request') {
        const method = String(request.method || '').toUpperCase();
        const resource = request.resource;
        if (!['GET', 'PUT', 'POST'].includes(method) || typeof resource !== 'string' || resource.length > 512
          || !resource.startsWith('/') || resource.includes('..') || resource.includes('\\')
          || !/^\/[a-zA-Z0-9/_-]+(?:\?[a-zA-Z0-9_=&-]*)?$/.test(resource)) throw new Error('Wallpaper 请求参数被拒绝');
        const result = { ok: true, ...(await wallpaperRequest(method, resource, request.body)) };
        if (method === 'PUT' && resource.split('?')[0] === '/settings') onSurfaceChanged(entry.id);
        return result;
      }
      if (operation === 'wallpaper.import') return wallpaperImport(entry.window);
      if (operation === 'wallpaper.directory') return wallpaperDirectory(entry.window);
      if (operation === 'open-external') {
        if (request.url !== 'https://github.com/elysia395/dsh-wallpaper-engine') throw new Error('外部地址未授权');
        return openExternal(request.url);
      }
    }
    const method = String(request.method || '').toUpperCase();
    const rawPath = request.path;
    if (operation === 'resource-url') {
      if (!entry.grants.includes('desktop.wallpaper.control') || entry.id !== 'desktop-wallpaper') throw new Error('媒体资源权限未授予');
      if (typeof rawPath !== 'string' || rawPath.length > 2048 || !rawPath.startsWith('/api/plugin/wallpaper/we/')) throw new Error('资源路径被拒绝');
      return { ok: true, url: await resourceUrl(rawPath) };
    }
    if (!['GET', 'POST'].includes(method) || typeof rawPath !== 'string' || rawPath.length > 2048) throw new Error('Broker 请求参数无效');
    let parsed;
    try { parsed = new URL(rawPath, 'http://plugin.invalid'); } catch (_) { throw new Error('Broker 路径无效'); }
    if (parsed.origin !== 'http://plugin.invalid' || parsed.pathname.split('/').includes('..') || parsed.pathname.includes('\\')) throw new Error('Broker 路径越界');
    if (entry.pluginName === 'wallpaper' && entry.grants.includes('brain.plugin.wallpaper.api')) {
      const allowed = new Set(['/api/plugin/wallpaper/config', '/api/plugin/wallpaper/we/library']);
      const isLibrary = parsed.pathname === '/api/plugin/wallpaper/we/library';
      if (!allowed.has(parsed.pathname) || (method === 'POST' && parsed.pathname !== '/api/plugin/wallpaper/config')
        || (method === 'GET' && parsed.pathname === '/api/plugin/wallpaper/config' && parsed.search)
        || (isLibrary && parsed.search && parsed.search !== '?refresh=1')) throw new Error('Wallpaper Broker 操作未授权');
      return requestBrain(method, parsed.pathname + parsed.search, method === 'POST' ? request.body : null);
    }
    const ownPrefix = '/api/plugin/' + entry.pluginName + '/';
    if (!parsed.pathname.startsWith(ownPrefix) || !entry.grants.includes('brain.plugin.' + entry.pluginName + '.api')) throw new Error('插件 API 权限未授予');
    return requestBrain(method, parsed.pathname + parsed.search, method === 'POST' ? request.body : null);
  }

  async function onBrokerRequest(rawId, request) {
    const entry = registrations.get(normalizeId(rawId));
    if (!entry || !entry.grants.includes('plugin.ui.contribute')) throw new Error('插件 UI 来源已撤销');
    if (request?.operation === 'register-page') {
      const page = request.page;
      if (!page || typeof page !== 'object' || !/^[a-zA-Z0-9_-]{1,64}$/.test(page.id) || !page.name) throw new Error('插件页面描述无效');
      return { ok: true, plugin: entry.id };
    }
    return requestForEntry(entry, request);
  }

  async function request(event, request) {
    assertCore(event);
    throw new Error('Plugin Broker requests are accepted only from the authenticated isolated host');
  }

  async function registerPage(event, page) {
    assertCore(event);
    const entry = registrations.get(normalizeId(page?.pluginId));
    if (!entry) throw new Error('插件 UI 来源已撤销');
    diag('plugin page registration begin', entry.id, event.sender.id);
    if (!page || typeof page !== 'object' || !/^[a-zA-Z0-9_-]{1,64}$/.test(page.id) || !page.name) throw new Error('插件页面描述无效');
    diag('plugin page registration end', entry.id, event.sender.id);
    return { ok: true, plugin: entry.id };
  }

  function close(id) { const key = normalizeId(id); revoke(key); }
  function closeAll() { disposed = true; for (const id of [...registrations.keys()]) revoke(id); host.closeAll(); }
  async function getHostState() { try { return await host.getState(); } catch (_) { return []; } }
  function getHostDebugPort() { return host.getDebugPort(); }
  function crashTestHost(id) {
    if (process.env.VIGIL_TEST_MODE !== '1') throw new Error('Unavailable');
    const key = normalizeId(id);
    if (!registrations.get(key)?.window) throw new Error('Plugin window not found');
    return host.crash(key);
  }
  function getState() { return [...registrations.keys()]; }
  function getOriginKeys() { return [...registrations.keys()].map(originKeyFor); }
  return { sync, open, request, registerPage, surfaceAssets, surfaceAssetsForOrigin, surfaceRequest, close, closeAll, getState, getOriginKeys, getHostState, getHostDebugPort, crashTestHost };
}

module.exports = { createPluginUiBroker };
