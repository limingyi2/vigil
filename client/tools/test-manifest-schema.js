'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Ajv2020 = require('ajv/dist/2020');
const addFormats = require('ajv-formats');

const root = path.resolve(__dirname, '../..');
const schema = require('../electron/extension-manifest.schema.json');
const ajv = new Ajv2020({ allErrors: true, strict: true });
addFormats(ajv);
const validate = ajv.compile(schema);
const wallpaper = JSON.parse(fs.readFileSync(path.join(root, 'docs/examples/extensions/wallpaper.manifest.example.json')));
const bridge = JSON.parse(fs.readFileSync(path.join(root, 'docs/examples/extensions/wechat-bridge.manifest.example.json')));

assert.equal(validate(wallpaper), true, JSON.stringify(validate.errors));
assert.equal(validate(bridge), true, JSON.stringify(validate.errors));
for (const mutation of [
  { ...wallpaper, permissions: [...wallpaper.permissions, 'filesystem.anything'] },
  { ...wallpaper, kind: 'arbitrary-code' },
  { ...wallpaper, protocol: { ...wallpaper.protocol, name: 'vigil-channel-bridge' } },
  Object.fromEntries(Object.entries(wallpaper).filter(([key]) => key !== 'entry')),
  { ...bridge, renderer: { script: 'entry.js', style: 'entry.css' } },
  { ...bridge, permissions: ['desktop.wallpaper.control'] },
]) assert.equal(validate(mutation), false);

console.log('PASS Draft 2020-12 Manifest validation: examples, unknown permissions/types and type/protocol boundaries');
