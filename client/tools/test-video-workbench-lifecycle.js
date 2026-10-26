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
      paused: true, playbackRate: 1,
      pause() { this.paused = true; calls.push(['pause', id]); },
      load() { calls.push(['load', id]); },
      click() { calls.push(['click', id]); },
      setAttribute(name, value) { this[name] = value; },
      removeAttribute(name) { delete this[name]; },
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

  const state = {settingsDetail: false, settingsSub: '', videoWorkbenchMode: 'external'};
  const window = {
    URL: {
      createObjectURL(file) { const url = 'blob:fixture/' + file.name; calls.push(['create-url', file.name]); return url; },
      revokeObjectURL(url) { calls.push(['revoke-url', url]); },
    },
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
  const cleanup = () => h.cleanups.forEach(fn => fn());
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
  cleanup();
  assert.equal(h.windowListeners.get('resize').size, 0);
  assert.equal(h.windowCallbacks.size, 0);
  assert.equal(h.eventCallbacks.size, 0);
});

test('unlaid-out workbench does not attach or launch software', async () => {
  const h = harness();
  await h.renderer(h.ctx);
  h.nodes.get('videoProfileSelect').value = 'profile-1';
  h.nodes.get('videoHostSlot').getBoundingClientRect = () => ({left: 0, top: 0, width: 0, height: 0});
  await h.nodes.get('videoStart').listeners.get('click')();
  assert.equal(h.calls.filter(row => row[0] === 'start').length, 0);
  assert.deepEqual(h.calls.filter(row => row[0] === 'action'), [['action', 'STATUS']]);
  assert.match(h.nodes.get('videoStatus').textContent, /显示区域尚未就绪/);
});

test('invalid native geometry removes hosted placeholder state and reports a usable error', async () => {
  const h = harness();
  await h.renderer(h.ctx);
  for (const callback of h.eventCallbacks) callback({type: 'attached', state: 'attached', pid: 41});
  const slot = h.nodes.get('videoHostSlot');
  assert.ok(slot.classList.values.has('is-hosted'));
  for (const callback of h.eventCallbacks) callback({
    type: 'bounds-error', state: 'bounds-invalid', message: '软件窗口尺寸无效，正在恢复独立窗口。',
  });
  assert.ok(!slot.classList.values.has('is-hosted'));
  assert.match(h.nodes.get('videoStatus').textContent, /尺寸无效/);
  assert.equal(h.calls.filter(row => row[0] === 'api').at(-1)[3].status.connected, false);
});

test('software settings opens the dedicated settings window at video-apps and reports failures', async () => {
  const h = harness();
  h.window.vigil.settings = {async open(options) {h.calls.push(['settings-open', options]);}};
  await h.renderer(h.ctx);
  await h.nodes.get('videoSettings').onclick();
  assert.equal(h.calls.find(row => row[0] === 'settings-open')[1].section, 'video-apps');
  assert.ok(!h.calls.some(row => row[0] === 'navigate'));
  h.window.vigil.settings.open = async () => {throw new Error('settings failed');};
  await h.nodes.get('videoSettings').onclick();
  assert.match(h.nodes.get('videoStatus').textContent, /settings failed/);
});

test('software settings API errors are visible and retry can recover', async () => {
  const source = fs.readFileSync(path.join(root, 'src/js/pages.js'), 'utf8');
  const start = source.indexOf('    async function videoApps() {');
  const end = source.indexOf('    async function about() {', start);
  const nodes = new Map();
  const v = {innerHTML: '', insertAdjacentHTML(_where, html) {this.innerHTML += html;}, querySelectorAll: () => [], querySelector(id) {
    if (id === '#vaDelete' && !this.innerHTML.includes('id="vaDelete"')) return null;
    if (!nodes.has(id)) nodes.set(id, {addEventListener(type, fn) {this[type] = fn;}});
    return nodes.get(id);
  }};
  let failed = true;
  let posted = null;
  const modelSettings = {ok:true, model_id:'fixture', models:[{id:'fixture',name:'Vision',provider:'fixture'}]};
  const render = vm.runInNewContext(`(${source.slice(start, end).trim()})`, {
    ctx: {topbar: {querySelector: () => ({textContent: ''})}}, v,
    global: {vigil: {videoWindow: {async profiles() {
      if (failed) throw new Error('Unexpected video window IPC sender');
      return [];
    }}}}, esc: value => String(value), toast() {}, confirmDialog: async () => false,
    pick: value => value, get: async () => modelSettings,
    post: async (path, body) => {posted = {path, body}; return modelSettings;},
  });
  await render();
  assert.match(v.innerHTML, /视频软件配置读取失败/);
  assert.equal(typeof nodes.get('#vaRetry').onclick, 'function');
  failed = false;
  await nodes.get('#vaRetry').onclick();
  assert.match(v.innerHTML, /新建配置/);
  assert.doesNotMatch(v.innerHTML, /读取失败/);
  assert.equal(posted, null, 'opening settings must not save or call a model');
  v.querySelector('#vaAnalysisModel').value = 'fixture';
  await nodes.get('#vaAnalysisSave').click();
  assert.equal(posted.path, '/api/video-analysis/settings');
  assert.equal(posted.body.model_id, 'fixture');
  assert.match(nodes.get('#vaAnalysisResult').textContent, /未发起模型请求/);
});

test('built-in player is the default and selecting a file does not upload or invoke analysis', async () => {
  const h = harness();
  delete h.state.videoWorkbenchMode;
  await h.renderer(h.ctx);
  assert.equal(h.nodes.get('videoMediaPane').hidden, false);
  assert.equal(h.nodes.get('videoExternalPane').hidden, true);
  assert.ok(!h.calls.some(row => row[0] === 'visible' && row[1]));
  h.nodes.get('videoFileInput').files = [{name: '<local>.webm', type: 'video/webm', size: 100}];
  h.nodes.get('videoFileInput').listeners.get('change')();
  assert.equal(h.nodes.get('videoPlayer').src, 'blob:fixture/<local>.webm');
  assert.match(h.nodes.get('videoPlayerStatus').textContent, /<local>.webm/);
  assert.equal(h.calls.filter(row => row[0] === 'api').length, 0);
  assert.equal(h.calls.filter(row => row[0] === 'start').length, 0);
});

test('replacing and disposing media releases object URLs, and switching modes pauses playback', async () => {
  const h = harness();
  await h.renderer(h.ctx);
  const input = h.nodes.get('videoFileInput');
  const change = input.listeners.get('change');
  input.files = [{name: 'first.webm', type: 'video/webm', size: 100}]; change();
  input.files = [{name: 'second.webm', type: 'video/webm', size: 100}]; change();
  assert.ok(h.calls.some(row => row[0] === 'revoke-url' && row[1] === 'blob:fixture/first.webm'));
  h.nodes.get('videoPlaybackRate').value = '1.5';
  h.nodes.get('videoPlaybackRate').listeners.get('change')();
  assert.equal(h.nodes.get('videoPlayer').playbackRate, 1.5);
  h.nodes.get('videoPlayer').paused = false;
  h.nodes.get('videoModeExternal').onclick();
  assert.equal(h.nodes.get('videoPlayer').paused, true);
  assert.equal(h.nodes.get('videoExternalPane').hidden, false);
  h.cleanups.forEach(fn => fn());
  assert.equal(h.nodes.get('videoPlayer').src, undefined);
  assert.ok(h.calls.some(row => row[0] === 'revoke-url' && row[1] === 'blob:fixture/second.webm'));
});

test('player works without native hosting and exposes invalid-file and decode errors', async () => {
  const h = harness();
  delete h.window.vigil;
  await h.renderer(h.ctx);
  assert.equal(h.nodes.get('videoModeExternal').disabled, true);
  const input = h.nodes.get('videoFileInput');
  input.files = [{name: 'unsafe.html', type: 'text/html', size: 100}];
  input.listeners.get('change')();
  assert.equal(h.calls.filter(row => row[0] === 'create-url').length, 0);
  input.files = [{name: 'clip.mkv', type: '', size: 100}];
  input.listeners.get('change')();
  h.nodes.get('videoPlayer').listeners.get('error')();
  assert.match(h.nodes.get('videoPlayerStatus').textContent, /编码不受支持/);
  h.cleanups.forEach(fn => fn());
});
