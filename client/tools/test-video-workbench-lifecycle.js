const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const pageFile = path.join(root, 'src/js/pages/video-workbench.js');
const pageSource = fs.readFileSync(pageFile, 'utf8');

function harness() {
  const calls = [];
  const windowListeners = new Map();
  const windowCallbacks = new Set();
  const eventCallbacks = new Set();
  const resizeObservers = [];
  const intersectionObservers = [];
  const nodes = new Map();

  function node(id) {
    if (nodes.has(id)) return nodes.get(id);
    const listeners = new Map();
    const item = {
      id, textContent: '', value: '', isConnected: true, listeners,
      classList: {
        values: new Set(),
        add(value) { this.values.add(value); },
        remove(value) { this.values.delete(value); },
      },
      addEventListener(type, fn) { listeners.set(type, fn); },
      replaceChildren(...children) { this.children = children; },
      add(child) { (this.children ||= []).push(child); },
      getBoundingClientRect() { return {left: 11, top: 12, width: 640, height: 360}; },
    };
    nodes.set(id, item);
    return item;
  }

  class FakeResizeObserver {
    constructor(callback) { this.callback = callback; this.disconnected = false; resizeObservers.push(this); }
    observe(target) { this.target = target; }
    disconnect() { this.disconnected = true; }
  }
  class FakeIntersectionObserver {
    constructor(callback, options) { this.callback = callback; this.options = options; this.disconnected = false; intersectionObservers.push(this); }
    observe(target) { this.target = target; }
    disconnect() { this.disconnected = true; }
  }

  const state = {settingsDetail: false, settingsSub: ''};
  const window = {
    devicePixelRatio: 2,
    addEventListener(type, fn) {
      const set = windowListeners.get(type) || new Set();
      set.add(fn);
      windowListeners.set(type, set);
    },
    removeEventListener(type, fn) { windowListeners.get(type)?.delete(fn); },
    vigil: {
      videoWindow: {
        profiles: async () => { calls.push(['profiles']); return [{id: 'profile-1', name: 'Editor'}]; },
        action: async action => { calls.push(['action', action]); },
        start: async (...args) => { calls.push(['start', ...args]); },
        bounds: bounds => calls.push(['bounds', bounds]),
        visible: visible => calls.push(['visible', visible]),
        onWindowVisibility(callback) {
          windowCallbacks.add(callback);
          return () => windowCallbacks.delete(callback);
        },
        onEvent(callback) {
          eventCallbacks.add(callback);
          return () => eventCallbacks.delete(callback);
        },
      },
      api: async (...args) => { calls.push(['api', ...args]); },
    },
  };
  const context = vm.createContext({
    window,
    API: {state},
    ResizeObserver: FakeResizeObserver,
    IntersectionObserver: FakeIntersectionObserver,
    Option: function Option(text, value) { this.text = text; this.value = value; },
  });
  vm.runInContext(pageSource, context, {filename: pageFile});

  const cleanups = [];
  const ctx = {
    topbar: {innerHTML: ''},
    view: {
      innerHTML: '',
      querySelector(selector) { return node(selector.slice(1)); },
    },
    navigate: id => calls.push(['navigate', id]),
    onDispose: callback => cleanups.push(callback),
  };
  return {
    calls, cleanups, ctx, eventCallbacks, intersectionObservers, nodes,
    resizeObservers, state, window, windowCallbacks, windowListeners,
    renderer: window.VigilRenderVideoWorkbench,
  };
}

function appNavigator(h, onChatDispose) {
  const appSource = fs.readFileSync(path.join(root, 'src/js/app.js'), 'utf8');
  const start = appSource.indexOf('  async function go(id) {');
  const end = appSource.indexOf('\n  // ── 「返回设置」', start);
  assert.ok(start >= 0 && end > start, 'app.go source boundary exists');
  const goSource = appSource.slice(start, end).trim();
  const bodyClasses = new Set();
  const body = {
    dataset: {},
    classList: {
      toggle(name, enabled) { if (enabled) bodyClasses.add(name); else bodyClasses.delete(name); },
      remove(...names) { names.forEach(name => bodyClasses.delete(name)); },
      add(...names) { names.forEach(name => bodyClasses.add(name)); },
      [Symbol.iterator]() { return bodyClasses[Symbol.iterator](); },
    },
    appendChild() {},
  };
  const document = {
    body,
    createElement: () => ({hidden: false, appendChild() {}}),
    getElementById: () => null,
  };
  const view = Object.assign(h.ctx.view, {id: 'view', className: '', style: {}});
  const topbar = Object.assign(h.ctx.topbar, {
    id: 'topbar', className: '', querySelector: () => null,
    insertAdjacentHTML() {},
  });
  const panel = {id: 'panel', innerHTML: ''};
  const pages = [
    {id: 'chat', render() {}},
    {id: 'video-workbench', render: h.renderer},
    {id: 'other', render() {}},
  ];
  const sandbox = {
    state: {page: 'chat', src: 'pc', settingsDetail: false},
    document, viewEl: view, topbarEl: topbar, panelEl: panel,
    gnavEl: {querySelectorAll: () => []}, PAGES: pages,
    esc: value => String(value), ensureSettingsBack() {}, watchSettingsBack() {},
    onChatDispose,
  };
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
  return vm.runInNewContext(factory, sandbox, {filename: 'app.js'});
}

test('video workbench is registered once and loaded before the page shell', () => {
  const html = fs.readFileSync(path.join(root, 'src/index.html'), 'utf8');
  const pages = fs.readFileSync(path.join(root, 'src/js/pages.js'), 'utf8');
  const app = fs.readFileSync(path.join(root, 'src/js/app.js'), 'utf8');
  const order = ['js/ui.js', 'js/api.js', 'js/pages/video-workbench.js', 'js/pages.js', 'js/app.js']
    .map(script => html.indexOf(`src="${script}"`));
  assert.ok(order.every(index => index >= 0));
  assert.deepEqual(order, [...order].sort((a, b) => a - b));
  assert.equal((pages.match(/id: 'video-workbench'/g) || []).length, 1);
  assert.match(pages, /render: global\.VigilRenderVideoWorkbench/);
  assert.doesNotMatch(pages, /async function renderVideoWorkbench\s*\(/);
  assert.equal((pageSource.match(/async function renderVideoWorkbench\s*\(/g) || []).length, 1);
  assert.match(app, /disposePage\(\);[\s\S]*?await page\.render\(ctx\)/);
  assert.match(app, /onDispose: fn => cleanups\.push\(fn\)/);
});

test('entering from chat preserves markup, profile/actions and capability request contract', async () => {
  const h = harness();
  assert.equal(typeof h.renderer, 'function');
  let chatDisposed = 0;
  const app = appNavigator(h, () => { chatDisposed++; });
  await app.go('video-workbench');
  assert.equal(chatDisposed, 1);
  assert.match(h.ctx.topbar.innerHTML, /视频工作台/);
  assert.match(h.ctx.view.innerHTML, /class="video-workbench"/);
  assert.match(h.ctx.view.innerHTML, /id="videoHostSlot"/);
  assert.match(h.ctx.view.innerHTML, /id="videoStart"/);
  assert.deepEqual(h.calls.filter(row => row[0] === 'profiles'), [['profiles']]);
  assert.deepEqual(h.calls.filter(row => row[0] === 'action'), [['action', 'STATUS']]);

  h.nodes.get('videoProfileSelect').value = 'profile-1';
  await h.nodes.get('videoStart').listeners.get('click')();
  assert.deepEqual(h.calls.filter(row => row[0] === 'action'), [
    ['action', 'STATUS'], ['action', 'ATTACH'],
  ]);
  const start = h.calls.find(row => row[0] === 'start');
  assert.equal(start[1].id, 'profile-1');
  assert.deepEqual(JSON.parse(JSON.stringify(start[2])), {x: 11, y: 12, width: 640, height: 360, dpr: 2});
  assert.ok(h.calls.some(row => row[0] === 'bounds' && row[1].dpr === 2));
  assert.ok(h.calls.some(row => row[0] === 'visible' && row[1] === true));

  for (const callback of [...h.eventCallbacks]) callback({type: 'attached', state: 'attached', pid: 41});
  const capability = h.calls.find(row => row[0] === 'api');
  assert.equal(capability[1], 'POST');
  assert.equal(capability[2], '/api/capabilities/events');
  assert.deepEqual(JSON.parse(JSON.stringify(capability[3])), {
    capability: 'video_workbench', type: 'state',
    status: {connected: true, state: 'attached', hosted_software: null, pid: 41},
  });
});

test('leaving and re-entering releases observers and listeners without duplicate subscriptions', async () => {
  const h = harness();
  await h.renderer(h.ctx);
  const cleanup = h.cleanups[0];
  assert.equal(h.windowListeners.get('resize').size, 1);
  assert.equal(h.windowCallbacks.size, 1);
  assert.equal(h.eventCallbacks.size, 1);

  // Leaving video workbench for another page invokes the ctx.onDispose callback.
  cleanup();
  cleanup();
  assert.equal(h.resizeObservers[0].disconnected, true);
  assert.equal(h.intersectionObservers[0].disconnected, true);
  assert.equal(h.windowListeners.get('resize').size, 0);
  assert.equal(h.windowCallbacks.size, 0);
  assert.equal(h.eventCallbacks.size, 0);
  assert.deepEqual(h.calls.filter(row => row[0] === 'visible').at(-1), ['visible', false]);
  assert.equal(h.calls.filter(row => row[0] === 'navigate').length, 0);

  await h.renderer(h.ctx);
  assert.equal(h.windowListeners.get('resize').size, 1);
  assert.equal(h.windowCallbacks.size, 1);
  assert.equal(h.eventCallbacks.size, 1);
  assert.equal(h.calls.filter(row => row[0] === 'profiles').length, 2);
  h.cleanups[1]();
  assert.equal(h.windowListeners.get('resize').size, 0);
  assert.equal(h.windowCallbacks.size, 0);
  assert.equal(h.eventCallbacks.size, 0);
});
