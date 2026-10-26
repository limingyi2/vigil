const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const dataFile = path.join(root, 'src/js/pages/data.js');
const dataSource = fs.readFileSync(dataFile, 'utf8');

function harness() {
  const calls = [];
  const cleanups = [];
  const charts = [];
  const elements = new Map();
  const windowListeners = new Map();
  let deferredStats = false;
  let resolveStats = null;
  const navItems = ['ov', 'tok', 'tr', 'hi', 'lg'].map(k => ({
    dataset: {k}, classList: {toggle() {}}, callbacks: new Map(),
    addEventListener(type, fn) { this.callbacks.set(type, fn); },
  }));
  class Surface {
    constructor(id) {
      this.id = id; this.className = ''; this.style = {}; this.scrollTop = 0;
      this.isConnected = true; this.callbacks = new Map(); this.innerHTML = '';
      this.classList = {toggle() {}, add() {}, remove() {}};
    }
    cloneNode() { return new Surface(this.id); }
    replaceWith(node) { surfaces.set(this.id, node); this.isConnected = false; }
    appendChild() {}
    querySelector(selector) { return element(selector); }
    querySelectorAll(selector) { return selector === '.li' || selector === '.li[data-k]' ? navItems : []; }
    addEventListener(type, fn) { this.callbacks.set(type, fn); }
    insertAdjacentHTML() {}
  }
  const surfaces = new Map();
  function element(selector) {
    const key = selector.startsWith('#') ? selector.slice(1) : selector;
    if (!elements.has(key)) {
      const callbacks = new Map();
      elements.set(key, {
        id: key, innerHTML: '', textContent: '', dataset: {}, disabled: false, isConnected: true,
        callbacks, classList: {toggle() {}, add() {}, remove() {}},
        addEventListener(type, fn) { callbacks.set(type, fn); },
        querySelector(selector) { return element(selector); }, querySelectorAll() { return []; },
        insertAdjacentHTML() {},
      });
    }
    return elements.get(key);
  }
  const window = {
    addEventListener(type, fn) {
      const set = windowListeners.get(type) || new Set(); set.add(fn); windowListeners.set(type, set);
    },
    removeEventListener(type, fn) { windowListeners.get(type)?.delete(fn); },
    dispatchEvent(event) { for (const fn of [...(windowListeners.get(event.type) || [])]) fn(event); },
  };
  const document = {
    body: {dataset: {}, classList: new Set(), appendChild() {}},
    addEventListener() {}, removeEventListener() {}, createElement: () => element('created'),
  };
  for (const id of ['view', 'topbar', 'panel']) surfaces.set(id, new Surface(id));
  const replies = {
    '/api/stats': {ok: true, tok_in: 4, tok_out: 6, turns: 2, model: 'test-model'},
    '/api/trace?last=500': {ok: true, items: [
      {t: 1_800_000_000_000, tok_in: 4, tok_out: 6},
    ]},
    '/api/trace?last=60': {ok: true, items: []},
    '/api/trace?last=200': {ok: true, items: [
      {t: 1_800_000_000_000, src: 'pc', model: 'test-model', in_len: 12, tok_in: 8, tok_out: 3, ms: 900},
    ]},
    '/api/history?limit=200&src=pc': {ok: true, items: []},
    '/api/logs?src=server&n=200': {ok: true, items: []},
  };
  const API = {
    get: async (route, options) => {
      calls.push(['GET', route, options]);
      if (route === '/api/stats' && deferredStats) {
        return new Promise(resolve => { resolveStats = () => resolve({json: {ok: true, turns: 99}}); });
      }
      return {json: replies[route] || {ok: true}};
    },
    post: async (route, body, options) => { calls.push(['POST', route, body, options]); return {json: {ok: true}}; },
    pick: reply => reply.json,
    state: {},
  };
  const U = {
    esc: value => String(value == null ? '' : value), icon: () => '', fmtTime: () => '',
    fmtNum: value => String(value), empty: text => `<div>${text}</div>`,
    lineChart: (el, options) => charts.push({el, options}),
  };
  const context = vm.createContext({window, API, U, document, console, setTimeout, clearTimeout});
  vm.runInContext(dataSource, context, {filename: dataFile});
  vm.runInContext(fs.readFileSync(path.join(root, 'src/js/pages.js'), 'utf8'), context,
    {filename: path.join(root, 'src/js/pages.js')});
  const ctx = {
    panel: surfaces.get('panel'), view: surfaces.get('view'), topbar: surfaces.get('topbar'),
    onDispose: callback => cleanups.push(callback),
  };
  return {API, calls, charts, cleanups, ctx, document, elements, navItems, replies, surfaces, window, windowListeners,
    setDeferredStats(value) { deferredStats = value; }, get resolveStats() { return resolveStats; },
    renderer: window.PAGES.find(p => p.id === 'data').render};
}

function settle() { return new Promise(resolve => setImmediate(resolve)); }

function appNavigator(h) {
  const appSource = fs.readFileSync(path.join(root, 'src/js/app.js'), 'utf8');
  const start = appSource.indexOf('  async function go(id) {');
  const end = appSource.indexOf('\n  // ── 「返回设置」', start);
  assert.ok(start >= 0 && end > start, 'app.go source boundary exists');
  const body = {
    dataset: {}, classList: {
      add() {}, remove() {}, toggle() {},
      [Symbol.iterator]() { return [][Symbol.iterator](); },
    }, appendChild() {},
  };
  const document = {
    body, createElement: () => ({id: 'parking', appendChild() {}}),
    getElementById: id => h.surfaces.get(id) || null,
  };
  const view = Object.assign(h.surfaces.get('view'), {id: 'view'});
  const topbar = Object.assign(h.surfaces.get('topbar'), {id: 'topbar'});
  const panel = Object.assign(h.surfaces.get('panel'), {id: 'panel'});
  const sandbox = {
    state: {page: '', src: 'pc', settingsDetail: false}, document,
    viewEl: view, topbarEl: topbar, panelEl: panel, gnavEl: {querySelectorAll: () => []},
    PAGES: [{id: 'data', render: h.renderer}, {id: 'other', render() {}}],
    esc: String, ensureSettingsBack() {}, watchSettingsBack() {},
  };
  const factory = `(function () {
    let disposePage = () => {};
    let navigationId = 0;
    let settingsBackObserver = null;
    let parkedChat = null;
    const parking = document.createElement('div');
    parking.hidden = true; parking.id = 'parkedChat'; document.body.appendChild(parking);
    ${appSource.slice(start, end).trim()}
    return {go, state};
  })()`;
  return vm.runInNewContext(factory, sandbox, {filename: 'app.js'});
}

test('data renderer is registered once and chart/data/pages scripts load in order', () => {
  const html = fs.readFileSync(path.join(root, 'src/index.html'), 'utf8');
  const pages = fs.readFileSync(path.join(root, 'src/js/pages.js'), 'utf8');
  const packageJson = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
  const order = ['js/chart.js', 'js/pages/data.js', 'js/pages.js', 'js/app.js']
    .map(script => html.indexOf(`src="${script}"`));
  assert.ok(order.every(index => index >= 0));
  assert.deepEqual(order, [...order].sort((a, b) => a - b));
  assert.ok(packageJson.build.files.includes('src/**'));
  assert.equal((pages.match(/id: 'data'/g) || []).length, 1);
  assert.match(pages, /render: renderData/);
  assert.match(pages, /VigilRenderData\(ctx, \{ li, topbar, tag, toMs, bindLi, confirmDialog \}\)/);
  assert.doesNotMatch(pages, /async function renderData\s*\(/);
  assert.equal((dataSource.match(/VigilRenderData = async function/g) || []).length, 1);
  assert.match(dataSource, /U\.lineChart\(chartEl/);
});

test('ability page no longer exposes the skills management interface', () => {
  const pages = fs.readFileSync(path.join(root, 'src/js/pages.js'), 'utf8');
  const start = pages.indexOf('  async function renderAbility(ctx) {');
  const end = pages.indexOf('  // ═══ 7. 数据', start);
  assert.ok(start >= 0 && end > start, 'ability page source boundary exists');
  const ability = pages.slice(start, end);
  assert.doesNotMatch(ability, /\['sk', '技能'/);
  assert.doesNotMatch(ability, /\/api\/skill\//);
  assert.match(ability, /\['pl', '插件'/);
  assert.match(ability, /\['ft', '开关'/);
});

test('chat attachments render as preview cards inside the composer', () => {
  const pages = fs.readFileSync(path.join(root, 'src/js/pages.js'), 'utf8');
  const styles = fs.readFileSync(path.join(root, 'src/styles.css'), 'utf8');
  const chat = pages.slice(pages.indexOf("'    <div class=\"composer\">"), pages.indexOf("const msgs = view.querySelector('#msgs')"));
  assert.match(chat, /<div class="cbox"><div class="attach" id="attach" hidden><\/div>/);
  assert.match(styles, /\.athumb \{ width: 184px; height: 148px/);
  assert.match(styles, /\.attach \{ display: flex; align-items: flex-start/);
  assert.match(styles, /\.attachment-row #atClr/);
});

test('first entry preserves the stats request and initializes the page', async () => {
  const h = harness();
  await h.renderer(h.ctx);
  await settle();
  assert.deepEqual(h.calls.map(call => call.slice(0, 2)), [['GET', '/api/stats']]);
  assert.equal(JSON.stringify(h.calls[0][2]), '{"timeoutMs":8000}');
  assert.match(h.ctx.panel.innerHTML, /data-k="ov"/);
  assert.match(h.ctx.topbar.innerHTML, /刷新/);
  assert.match(h.elements.get('dView').innerHTML, /今日轮次/);
  assert.equal(h.cleanups.length, 1);
});

test('switching to Token analysis keeps its requests, query parameters, and chart dependency', async () => {
  const h = harness();
  await h.renderer(h.ctx);
  await settle();
  await h.navItems.find(item => item.dataset.k === 'tok').callbacks.get('click')();
  await settle();
  assert.deepEqual(h.calls.map(call => call.slice(0, 2)), [
    ['GET', '/api/stats'], ['GET', '/api/stats'], ['GET', '/api/trace?last=500'],
  ]);
  assert.equal(h.charts.length, 1);
  assert.equal(h.charts[0].options.height, 260);
  assert.match(h.elements.get('dView').innerHTML, /Token 用量折线/);
  assert.ok(h.calls.slice(1).every(call => JSON.stringify(call[2]) === '{"timeoutMs":8000}'));
});

test('Trace, history, and logs read the expected local endpoints', async () => {
  const h = harness();
  await h.renderer(h.ctx);
  await settle();
  for (const sub of ['tr', 'hi', 'lg']) {
    await h.navItems.find(item => item.dataset.k === sub).callbacks.get('click')();
    await settle();
  }
  assert.deepEqual(h.calls.filter(call => call[0] === 'GET').map(call => call[1]), [
    '/api/stats', '/api/trace?last=60', '/api/history?limit=200&src=pc',
    '/api/logs?src=server&n=200', '/api/trace?last=200',
  ]);
  assert.match(h.elements.get('dView').innerHTML, /本机运行记录与错误/);
  assert.match(h.elements.get('dView').innerHTML, /test-model/);
  assert.match(h.elements.get('dView').innerHTML, /清空错误/);
});

test('leaving and re-entering disposes the previous renderer and does not retain listeners', async () => {
  const h = harness();
  await h.renderer(h.ctx);
  await settle();
  h.cleanups.splice(0).forEach(cleanup => cleanup());
  h.surfaces.get('view').isConnected = false;
  assert.equal(h.windowListeners.size, 0);
  await h.renderer(h.ctx);
  await settle();
  assert.equal(h.cleanups.length, 1);
  assert.equal(h.calls.filter(call => call[1] === '/api/stats').length, 2);
});

test('app.go disposes the data page on page change and renders it again on return', async () => {
  const h = harness();
  const app = appNavigator(h);
  await app.go('data');
  await settle();
  await app.go('other');
  await app.go('data');
  await settle();
  assert.equal(app.state.page, 'data');
  assert.equal(h.calls.filter(call => call[1] === '/api/stats').length, 2);
});

test('disposing while an API request is pending prevents stale rendering', async () => {
  const h = harness();
  let resolveStats;
  h.setDeferredStats(true);
  await h.renderer(h.ctx);
  h.cleanups.forEach(cleanup => cleanup());
  h.resolveStats();
  await settle();
  assert.doesNotMatch(h.elements.get('dView').innerHTML, /99/);
});
