'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const { verifyNoBundledPluginPaths, verifyNoBundledPluginManifests } = require('../electron/build-contract');

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
    '/src/js/plugins/future_plugin/manifest.json',
    '/electron/extensions/plugins/example/main.js',
    '/assets/market/plugins/example.zip',
  ]) {
    assert.throws(() => verifyNoBundledPluginPaths([file]), /extension plugin payload/, file);
  }
});

test('renamed extension packages are rejected by their manifest in app.asar or extraResources', () => {
  const entry = { path: '/src/js/future_plugin/manifest.json', content: {
    schema: 'vigil.brain-plugin/v1', name: 'future_plugin', version: '1.0.0',
  } };
  assert.throws(() => verifyNoBundledPluginManifests([entry]), /extension plugin manifest/);
  assert.equal(verifyNoBundledPluginManifests([{path:'/electron/package.json',content:{name:'vigil-client'}}]), true);
});

test('Windows separators are normalized before package-boundary checks', () => {
  assert.throws(
    () => verifyNoBundledPluginPaths(['\\src\\js\\plugins\\wallpaper\\manifest.json']),
    /extension plugin payload/,
  );
});
