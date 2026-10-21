const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const Module = require('node:module');
const path = require('node:path');

const userData = path.join(__dirname, 'electron-bridge-user-data');
const sentEvents = [];
const handlers = new Map();
const listeners = new Map();
const childProcesses = [];
const ipcMain = {
  handle: (name, fn) => handlers.set(name, fn),
  on: (name, fn) => listeners.set(name, fn),
  removeHandler: name => handlers.delete(name),
  removeListener: name => listeners.delete(name),
};
const webContents = { send: (channel, event) => sentEvents.push({ channel, event }) };
const window = { isDestroyed: () => false, webContents, getNativeWindowHandle: () => Buffer.alloc(8), focus() {} };

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.stdin = new PassThrough();
    this.stdout = new PassThrough();
    this.stderr = new PassThrough();
    this.commands = '';
    this.stdin.on('data', bytes => { this.commands += bytes.toString('utf8'); });
  }
  sendEvent(event) { this.stdout.write(JSON.stringify(event) + '\n'); }
}

const originalLoad = Module._load;
const originalSpawn = require('node:child_process').spawn;
require('node:child_process').spawn = (...args) => {
  const child = new FakeChild();
  childProcesses.push({ child, args });
  return child;
};
Module._load = function(request, parent, isMain) {
  if (request === 'electron') return { app: { getPath: () => userData }, dialog: {} };
  return originalLoad.call(this, request, parent, isMain);
};

async function main() {
  const { createNativeWindowService } = require('../../../electron/native-window-service');
  const service = createNativeWindowService({ ipcMain, getWindow: () => window });
  const event = { sender: webContents };
  const profile = { id: 'bridge-charmap', name: 'Character Map', exePath: path.join(process.env.WINDIR || 'C:\\Windows', 'System32', 'charmap.exe') };
  const started = await handlers.get('video-window:start')(event, profile, { x: 1, y: 2, width: 900, height: 500, dpr: 1 });
  assert.equal(started.ok, true);
  const { child, args } = childProcesses[0];
  assert.equal(args[0], path.resolve(__dirname, '..', '..', 'build', 'Release', 'native-window-host.exe'));
  child.sendEvent({ type: 'ready' });
  child.sendEvent({ type: 'started', pid: 4100 });
  child.sendEvent({ type: 'state', state: 'starting', pid: 4100, hwnd: 0 });
  child.sendEvent({ type: 'state', state: 'attached', pid: 4100, windowPid: 4100, hwnd: 88220 });
  await new Promise(resolve => setImmediate(resolve));

  await handlers.get('video-window:start')(event, profile, { x: 1, y: 2, width: 900, height: 500, dpr: 1 });
  const stateEvents = sentEvents.filter(item => item.channel === 'video-window:event' && item.event.type === 'state');
  assert.deepEqual(stateEvents.map(item => item.event.state), ['starting', 'attached']);
  assert.equal(childProcesses.length, 1, 'repeated START must reuse the same helper process');
  assert.equal((child.commands.match(/^START\t/gm) || []).length, 2, 'repeated START must reach the existing native session');

  let shutdownSettled = false;
  const shutdown = service.shutdown().then(value => { shutdownSettled = true; return value; });
  child.sendEvent({ type: 'restore-failed', stage: 'set-parent', code: 5, hwnd: 88220, valid: true });
  child.sendEvent({ type: 'state', state: 'restore-failed', pid: 4100, hwnd: 88220 });
  child.sendEvent({ type: 'shutdown-blocked', reason: 'retrying' });
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(shutdownSettled, false, 'shutdown must wait while restoration is blocked');
  child.sendEvent({ type: 'restore-complete', stage: 'complete', code: 0, hwnd: 88220, valid: true });
  child.sendEvent({ type: 'shutdown-complete' });
  assert.equal(await shutdown, true, 'shutdown should become safe only after shutdown-complete');
  child.emit('exit', 0, null);
  await new Promise(resolve => setImmediate(resolve));
  const allStates = sentEvents.filter(item => item.channel === 'video-window:event' && item.event.type === 'state').map(item => item.event.state);
  assert.deepEqual(allStates, ['starting', 'attached', 'restore-failed']);
  assert(sentEvents.some(item => item.event.type === 'start-reused') === false, 'the bridge must not invent native start-reused events');
  assert(sentEvents.some(item => item.event.type === 'helper-exit' && item.event.code === 0));
  service.dispose();
  console.log(JSON.stringify({ result: 'PASS', helperSpawns: childProcesses.length, startCommands: 2, uiStates: allStates, shutdownWaitedForRestore: true }, null, 2));
}

main().catch(error => { console.error(error.stack || error); process.exitCode = 1; }).finally(() => {
  Module._load = originalLoad;
  require('node:child_process').spawn = originalSpawn;
});
