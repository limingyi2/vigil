const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {EventEmitter} = require('node:events');
const {PassThrough} = require('node:stream');
const {normalizeDipBounds} = require('../electron/native-window-bounds');

function harness() {
  const handlers = new Map(), listeners = new Map(), events = [], children = [];
  const settingsTarget = {isDestroyed: () => false, webContents: {}};
  const pickedFrom = [];
  let profileData = [{id: 'editor', name: 'Editor', exePath: 'C:/fixture/editor.exe'}];
  const target = Object.assign(new EventEmitter(), {
    isDestroyed: () => false, getContentBounds: () => ({x: -300, y: 100}),
    getNativeWindowHandle: () => Buffer.alloc(8), focus() {},
    webContents: {send: (_channel, event) => events.push(event)},
  });
  const screen = {dipToScreenRect(_target, rect) {
    assert.ok(Object.values(rect).every(Number.isInteger), 'Electron must receive integers');
    return {x: rect.x * 2, y: rect.y * 2, width: rect.width * 2, height: rect.height * 2};
  }};
  const ipcMain = {handle: (n, fn) => handlers.set(n, fn), on: (n, fn) => listeners.set(n, fn),
    removeHandler() {}, removeListener() {}};
  const filename = path.resolve(__dirname, '../electron/native-window-service.js');
  const module = {exports: {}};
  const fakeFs = {existsSync: () => true, statSync: () => ({isFile: () => true}),
    mkdirSync() {}, appendFileSync() {},
    readFileSync: () => JSON.stringify(profileData),
    writeFileSync: (_file, data) => {profileData = JSON.parse(data);}};
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    module, __dirname: path.dirname(filename), Buffer, setTimeout, clearTimeout,
    process: {platform: 'win32'}, console: {warn() {}},
    require(name) {
      if (name === 'electron') return {app: {getPath: () => 'C:/fixture'},
        dialog: {async showOpenDialog(parent) {pickedFrom.push(parent); return {canceled: true};}}, screen};
      if (name === 'fs') return fakeFs;
      if (name === './native-window-bounds') return {normalizeDipBounds};
      if (name === 'child_process') return {spawn() {
        const child = Object.assign(new EventEmitter(), {stdin: new PassThrough(),
          stdout: new PassThrough(), stderr: new PassThrough(), commands: ''});
        child.stdin.on('data', bytes => {child.commands += bytes.toString();});
        children.push(child); return child;
      }};
      return require(name);
    },
  }, {filename});
  const service = module.exports.createNativeWindowService({ipcMain, getWindow: () => target,
    getSettingsWindow: () => settingsTarget});
  return {service, handlers, listeners, events, children, screen, settingsTarget, pickedFrom,
    event: {sender: target.webContents}, settingsEvent: {sender: settingsTarget.webContents}};
}
const fractional = {x: 11.25, y: 12.5, width: 640.75, height: 360.25};

test('fractional CSS rectangles become covering integer DIP rectangles on negative-origin displays', () => {
  assert.deepEqual(normalizeDipBounds(fractional, {x: -300, y: 100}),
    {x: -289, y: 112, width: 641, height: 361});
  for (const width of [undefined, 0, 1, -100, NaN, Infinity, 2147483648]) {
    assert.equal(normalizeDipBounds({...fractional, width}, {x: 0, y: 0}), null);
  }
});

test('invalid geometry and conversion failure never spawn a helper or send START', () => {
  const h = harness();
  assert.equal(h.service.startProfile('editor').error, 'invalid_bounds');
  assert.throws(() => h.handlers.get('video-window:start')(h.event,
    {exePath: 'C:/fixture/editor.exe'}, {...fractional, width: 0}));
  h.screen.dipToScreenRect = () => {throw new Error('conversion failure');};
  assert.equal(h.service.startProfile('editor', fractional).error, 'invalid_bounds');
  assert.equal(h.children.length, 0);
  h.service.dispose();
});

test('fractional start, resize and hide/re-entry retain valid geometry without a 1x1 fallback', () => {
  const h = harness();
  assert.equal(h.service.startProfile('editor', fractional).ok, true);
  const child = h.children[0];
  child.stdout.write('{"type":"ready"}\n');
  assert.match(child.commands, /START\t[^\n]+\t-578\t224\t1282\t722\n/);
  h.listeners.get('video-window:bounds')(h.event, {...fractional, width: 0});
  assert.doesNotMatch(child.commands, /BOUNDS[^\n]+\t1\t1\n/);
  h.listeners.get('video-window:visible')(h.event, false);
  assert.equal(h.service.startProfile('editor').error, 'invalid_bounds');
  h.listeners.get('video-window:bounds')(h.event, fractional);
  assert.equal(h.service.startProfile('editor').ok, true);
  h.service.dispose();
});

test('a native 1x1 attached event is rejected and detached instead of advertised as success', () => {
  const h = harness();
  h.service.startProfile('editor', fractional);
  const child = h.children[0];
  child.stdout.write('{"type":"ready"}\n');
  child.stdout.write('{"type":"attached","pid":42,"screenRect":[0,0,1,1]}\n');
  child.stdout.write('{"type":"state","state":"attached"}\n');
  assert.match(child.commands, /DETACH\n/);
  assert.equal(h.service.status().state, 'bounds-invalid');
  assert.ok(h.events.some(e => e.type === 'bounds-error'));
  assert.ok(!h.events.some(e => e.type === 'attached' || e.state === 'attached'));
  h.service.dispose();
});

test('settings window can manage software profiles and owns its file picker, without window-control access', async () => {
  const h = harness();
  assert.equal(h.handlers.get('video-window:profiles')(h.settingsEvent)[0].id, 'editor');
  const saved = h.handlers.get('video-window:save-profile')(h.settingsEvent,
    {id: 'player', name: 'Player', exePath: 'C:/fixture/player.exe'});
  assert.equal(saved.length, 2);
  await h.handlers.get('video-window:pick-exe')(h.settingsEvent);
  assert.equal(h.pickedFrom[0], h.settingsTarget);
  assert.equal(h.handlers.get('video-window:delete-profile')(h.settingsEvent, 'player').length, 1);
  assert.throws(() => h.handlers.get('video-window:start')(h.settingsEvent,
    {exePath: 'C:/fixture/editor.exe'}, fractional), /Unexpected/);
  assert.throws(() => h.handlers.get('video-window:profiles')({sender: {}}), /Unexpected/);
  h.settingsTarget.isDestroyed = () => true;
  assert.throws(() => h.handlers.get('video-window:profiles')(h.settingsEvent), /Unexpected/);
  assert.equal(h.children.length, 0);
  h.service.dispose();
});
