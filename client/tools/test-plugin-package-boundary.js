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

test('bundled plugins inside the Brain payload are expected, not rejected', () => {
  assert.equal(verifyNoBundledPluginPaths([
    '/resources/vigil-brain/_internal/plugins/kb.py',
    '/resources/vigil-brain/_internal/plugins/gate.py',
    '/resources/vigil-brain/_internal/plugins/branch-atlas/frontend.js',
  ]), true);
});

test('the wallpaper extension is rejected wherever it appears', () => {
  for (const file of [
    '/resources/vigil-brain/_internal/plugins/wallpaper/__init__.py',
    '/src/plugins/wallpaper/manifest.json',
    '/src/js/plugins/wallpaper/frontend.js',
    '\\resources\\vigil-brain\\_internal\\plugins\\wallpaper\\frontend.css',
  ]) {
    assert.throws(() => verifyNoBundledPluginPaths([file]), /extension plugin payload/, file);
  }
});

test('runtime install trees are rejected in the client package', () => {
  for (const file of [
    '/resources/vigil-brain/data/installed_plugins/wallpaper/__init__.py',
    '/electron/extensions/installed_plugins/example/main.js',
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
