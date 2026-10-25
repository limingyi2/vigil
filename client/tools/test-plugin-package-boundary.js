'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const { verifyNoBundledPluginPaths, verifyNoBundledPluginManifests } = require('../electron/build-contract');

test('core Plugin Manager UI and Skill Host bridge remain allowed', () => {
  assert.equal(verifyNoBundledPluginPaths([
    '/src/js/pages/plugin-market.js',
    '/electron/plugin-resource.js',
    '/skill_host/server.js',
    '/assets/icon.ico',
  ]), true);
});

test('all plugin directory names are rejected independent of plugin identity', () => {
  for (const file of [
    '/resources/vigil-brain/_internal/plugins/kb.py',
    '/resources/vigil-brain/_internal/plugins/future_plugin/__init__.py',
    '/src/plugins/future_plugin/frontend.js',
    '\\resources\\vigil-brain\\plugins\\future_plugin\\frontend.css',
  ]) {
    assert.throws(() => verifyNoBundledPluginPaths([file]), /extension plugin payload/, file);
  }
});

test('runtime install trees are rejected generically in every output location', () => {
  for (const file of [
    '/resources/vigil-brain/data/installed_plugins/wallpaper/__init__.py',
    '/electron/extensions/installed_plugins/future_plugin/main.js',
  ]) {
    assert.throws(() => verifyNoBundledPluginPaths([file]), /extension plugin payload/, file);
  }
});

test('renamed extension packages are rejected by manifest in app.asar or extraResources', () => {
  const entry = { path: '/resources/future/manifest.json', content: {
    schema: 'vigil.brain-plugin/v1', name: 'future_plugin', version: '1.0.0',
  } };
  assert.throws(() => verifyNoBundledPluginManifests([entry]), /extension plugin manifest/);
  assert.equal(verifyNoBundledPluginManifests([{path:'/electron/package.json',content:{name:'vigil-client'}}]), true);
});
