const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const pageFile = path.join(root, 'src/js/pages/plugin-market.js');
const pageSource = fs.readFileSync(pageFile, 'utf8');

function harness() {
  const calls = [];
  const listeners = new Map();
  const elements = new Map();
  const surfaces = new Map();
  class Surface {
    constructor(id) {
      this.id = id; this.innerHTML = ''; this.className = ''; this.style = {}; this.scrollTop = 0;
      this.callbacks = new Map();
      this.classList = {
        add() {}, remove() {}, toggle() {},
        [Symbol.iterator]() { return [][Symbol.iterator](); },
      };
    }
    cloneNode() { return new Surface(this.id); }
    replaceWith(replacement) { surfaces.set(this.id, replacement); }
    appendChild() {}
    querySelector(selector) { return element(selector.replace(/^#/, '')); }
    querySelectorAll() { return []; }
    addEventListener(type, callback) { this.callbacks.set(type, callback); }
    insertAdjacentHTML() {}
  }
  function element(id) {
    if (!elements.has(id)) {
      const callbacks = new Map();
      elements.set(id, {
        id, innerHTML: '', textContent: '', disabled: false, dataset: {}, style: {}, callbacks,
        classList: {add() {}, remove() {}},
        addEventListener(type, callback) { callbacks.set(type, callback); },
        appendChild() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        insertAdjacentHTML() {},
      });
    }
    return elements.get(id);
  }
  const window = {
    addEventListener(type, callback) {
      const set = listeners.get(type) || new Set();
      set.add(callback);
      listeners.set(type, set);
    },
    removeEventListener(type, callback) { listeners.get(type)?.delete(callback); },
    dispatchEvent(event) { for (const callback of [...(listeners.get(event.type) || [])]) callback(event); },
  };
  const document = {
    body: {dataset: {}, classList: {
      values: new Set(),
      add(...names) { names.forEach(name => this.values.add(name)); },
      remove(...names) { names.forEach(name => this.values.delete(name)); },
      toggle(name, enabled) { if (enabled) this.values.add(name); else this.values.delete(name); },
      [Symbol.iterator]() { return this.values[Symbol.iterator](); },
    }, appendChild() {}},
    createElement: () => element('created'),
    getElementById: id => surfaces.get(id) || null,
  };
  for (const id of ['view', 'topbar', 'panel']) surfaces.set(id, new Surface(id));
  const API = {
    runtime: {kind: 'test'},
    get: async route => {
      calls.push(['GET', route]);
      return {ok: true, status: 200, json: {state: 'ready', installed_count: 0, plugins: []}};
    },
    post: async (route, body) => {
      calls.push(['POST', route, body]);
      return {ok: true, json: {ok: true}};
    },
  };
  const context = vm.createContext({
    window, API, U: {esc: String, icon: () => '', toast() {}},
    state: {version: 'test'},
    document,
    Event: class Event { constructor(type) { this.type = type; } },
    Blob: class Blob {}, URL: {createObjectURL() { return ''; }, revokeObjectURL() {}},
    setTimeout,
  });
  vm.runInContext(pageSource, context, {filename: pageFile});
  const cleanups = [];
  const ctx = {
    panel: surfaces.get('panel'), view: surfaces.get('view'), topbar: surfaces.get('topbar'),
    onDispose(callback) { cleanups.push(callback); },
  };
  return {calls, cleanups, ctx, document, element, listeners, renderer: window.VigilRenderPluginMarket, surfaces, window};
}

function appNavigator(h, onChatDispose) {
  const appSource = fs.readFileSync(path.join(root, 'src/js/app.js'), 'utf8');
  const start = appSource.indexOf('  async function go(id) {');
  const end = appSource.indexOf('\n  // ── 「返回设置」', start);
  assert.ok(start >= 0 && end > start, 'app.go source boundary exists');
  const goSource = appSource.slice(start, end).trim();
  const factory = `(function () {
    let disposePage = onChatDispose;
    let navigationId = 0;
    let settingsBackObserver = null;
    let parkedChat = null;
    const parking = document.createElement('div');
    parking.hidden = true; parking.id = 'parkedChat'; document.body.appendChild(parking);
    ${goSource}
    return {go, state};
  })()`;
  const sandbox = {
    document: h.document,
    viewEl: h.surfaces.get('view'), topbarEl: h.surfaces.get('topbar'), panelEl: h.surfaces.get('panel'),
    gnavEl: {querySelectorAll: () => []},
    PAGES: [
      {id: 'chat', render() {}},
      {id: 'market', render: ctx => h.renderer(ctx, async () => true)},
      {id: 'other', render() {}},
    ],
    state: {page: 'chat', src: 'pc', settingsDetail: false},
    esc: String, ensureSettingsBack() {}, watchSettingsBack() {}, onChatDispose,
  };
  return vm.runInNewContext(factory, sandbox, {filename: 'app.js'});
}

test('market renderer is registered once and script loads after its dependencies', () => {
  const html = fs.readFileSync(path.join(root, 'src/index.html'), 'utf8');
  const pages = fs.readFileSync(path.join(root, 'src/js/pages.js'), 'utf8');
  const order = ['js/ui.js', 'js/api.js', 'js/pages/video-workbench.js',
    'js/pages/plugin-market.js', 'js/pages.js', 'js/app.js']
    .map(script => html.indexOf(`src="${script}"`));
  assert.ok(order.every(index => index >= 0));
  assert.deepEqual(order, [...order].sort((a, b) => a - b));
  assert.equal((pages.match(/id: 'market'/g) || []).length, 1);
  assert.match(pages, /render: renderPluginMarket/);
  assert.match(pages, /VigilRenderPluginMarket\(ctx, confirmDialog\)/);
  assert.doesNotMatch(pages, /async function renderPluginMarket\s*\(/);
  assert.equal((pageSource.match(/async function renderPluginMarket\s*\(/g) || []).length, 1);
});

test('first entry loads once, refresh uses the existing route, and disposal removes the update listener', async () => {
  const h = harness();
  assert.equal(typeof h.renderer, 'function');
  await h.renderer(h.ctx, async () => true);
  assert.deepEqual(h.calls, [['GET', '/api/market/list']]);
  assert.equal(h.listeners.get('vigil-market-changed').size, 1);

  h.window.dispatchEvent(new Event('vigil-market-changed'));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls.filter(call => call[0] === 'GET').length, 2);

  await h.element('pmRefresh').callbacks.get('click')();
  const refreshCall = h.calls.find(call => call[0] === 'POST');
  assert.equal(refreshCall[1], '/api/market/refresh');
  assert.deepEqual(JSON.parse(JSON.stringify(refreshCall[2])), {});
  assert.equal(h.calls.filter(call => call[0] === 'GET').length, 3);

  h.cleanups.forEach(cleanup => cleanup());
  assert.equal(h.listeners.get('vigil-market-changed').size, 0);
  h.window.dispatchEvent(new Event('vigil-market-changed'));
  assert.equal(h.calls.filter(call => call[0] === 'GET').length, 3);
});

test('re-entering the market does not keep stale listeners or duplicate subscriptions', async () => {
  const h = harness();
  await h.renderer(h.ctx, async () => true);
  h.cleanups.splice(0).forEach(cleanup => cleanup());
  await h.renderer(h.ctx, async () => true);
  assert.equal(h.listeners.get('vigil-market-changed').size, 1);
  h.window.dispatchEvent(new Event('vigil-market-changed'));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls.filter(call => call[0] === 'GET').length, 3);
});

test('chat to market to chat parks and restores the existing chat surfaces', async () => {
  const h = harness();
  const originalView = h.surfaces.get('view');
  let chatDisposed = 0;
  const app = appNavigator(h, () => { chatDisposed++; });
  await app.go('market');
  assert.equal(chatDisposed, 0);
  assert.notEqual(h.surfaces.get('view'), originalView);
  assert.equal(h.listeners.get('vigil-market-changed').size, 1);
  await app.go('chat');
  assert.equal(h.surfaces.get('view'), originalView);
  assert.equal(app.state.page, 'chat');
  assert.equal(h.listeners.get('vigil-market-changed').size, 0);
  assert.equal(chatDisposed, 0);
});
