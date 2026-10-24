'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const { verifyNoBundledPluginPaths } = require('../electron/build-contract');

test('core plugin management and resource bridge are allowed in the client package', () => {
  assert.equal(verifyNoBundledPluginPaths([
    '/src/js/pages/plugin-market.js',
    '/electron/plugin-resource.js',
    '/assets/icon.ico',
  ]), true);
});

test('extension source directories are rejected regardless of plugin name or nesting', () => {
  for (const file of [
    '/src/plugins/wallpaper/manifest.json',
    '/src/js/plugins/future-extension/index.js',
    '/electron/extensions/plugins/example/main.js',
    '/assets/market/plugins/example.zip',
  ]) {
    assert.throws(() => verifyNoBundledPluginPaths([file]), /extension plugin payload/, file);
  }
});

test('Windows separators are normalized before package-boundary checks', () => {
  assert.throws(
    () => verifyNoBundledPluginPaths(['\\src\\js\\plugins\\wallpaper\\manifest.json']),
    /extension plugin payload/,
  );
});
