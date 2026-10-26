const assert = require('node:assert/strict');
const test = require('node:test');

const { findWindowForContents } = require('../electron/plugin-load-target');
const { normalizeDipBounds } = require('../electron/native-window-bounds');

test('plugin loads are routed only to the BrowserWindow that owns the IPC sender', () => {
  const mainContents = {};
  const settingsContents = {};
  const main = { webContents: mainContents, isDestroyed: () => false };
  const settings = { webContents: settingsContents, isDestroyed: () => false };
  assert.equal(findWindowForContents(settingsContents, [main, settings]), settings);
  assert.equal(findWindowForContents(mainContents, [main, settings]), main);
});

test('plugin loads reject unknown or destroyed BrowserWindow senders', () => {
  const sender = {};
  const destroyed = { webContents: sender, isDestroyed: () => true };
  assert.equal(findWindowForContents(sender, [destroyed]), null);
  assert.equal(findWindowForContents({}, [{ webContents: sender, isDestroyed: () => false }]), null);
  assert.equal(findWindowForContents(sender, null), null);
});

test('bounds conversion keeps valid coordinates and rejects missing layout', () => {
  assert.deepEqual(
    normalizeDipBounds({ x: 11, y: 12, width: 640, height: 360, dpr: 2 }, { x: 100, y: 200 }),
    { x: 111, y: 212, width: 640, height: 360 },
  );
  assert.equal(normalizeDipBounds(undefined, { x: 100, y: 200 }), null);
});

test('non-finite or overflowing geometry is rejected before reaching Electron screen APIs', () => {
  assert.equal(normalizeDipBounds({ x: Infinity, y: 0, width: 640, height: 360 }, { x: 0, y: 0 }), null);
  assert.equal(normalizeDipBounds({ x: 0, y: 0, width: NaN, height: 360 }, { x: 0, y: 0 }), null);
  assert.equal(normalizeDipBounds({ x: Number.MAX_VALUE, y: 0, width: 1, height: 1 }, { x: Number.MAX_VALUE, y: 0 }), null);
});

