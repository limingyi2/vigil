const assert = require('node:assert/strict');
const test = require('node:test');

const { findWindowForContents } = require('../electron/plugin-load-target');

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
