'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const Module = require('node:module');

test('local_file_read returns bounded, addressable chunks for large files', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-localtool-read-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const source = path.join(root, 'large.txt');
  fs.writeFileSync(source, '0123456789'.repeat(50000));
  const originalLoad = Module._load;
  Module._load = function(request, parent, isMain) {
    if (request === 'electron') return {
      shell: {}, desktopCapturer: {}, app: {getPath: () => root},
    };
    return originalLoad.call(this, request, parent, isMain);
  };
  let localtool;
  try {
    delete require.cache[require.resolve('../electron/localtool')];
    localtool = require('../electron/localtool');
  } finally {
    Module._load = originalLoad;
  }
  // The manual tool UI sends empty optional inputs as empty strings.
  const first = await localtool.run('local_file_read',
    {path: source, offset: '', length: ''}, {roots: [root]});
  assert.match(first.text, /下一段从 offset=204800 开始/);
  assert.ok(first.text.length < 205000);
  const next = await localtool.run('local_file_read',
    {path: source, offset: 204800, length: 512}, {roots: [root]});
  assert.match(next.text, /字节范围 204800-205312/);
  assert.ok(next.text.endsWith('0123456789'.repeat(51) + '01'));
  const invalid = await localtool.run('local_file_read',
    {path: source, offset: -1, length: 512}, {roots: [root]});
  assert.match(invalid.text, /偏移必须/);

  const unicodeSource = '汉'.repeat(200000);
  const unicodePath = path.join(root, 'unicode.txt');
  fs.writeFileSync(unicodePath, unicodeSource, 'utf8');
  let offset = 0;
  let reconstructed = '';
  while (offset < Buffer.byteLength(unicodeSource)) {
    const part = await localtool.run('local_file_read',
      offset === 0 ? {path: unicodePath} : {path: unicodePath, offset, length: 204800},
      {roots: [root]});
    const range = part.text.match(/字节范围 (\d+)-(\d+)/);
    assert.ok(range, part.text.slice(0, 160));
    assert.ok(!part.text.includes('\ufffd'), 'UTF-8 chunk contains a replacement character');
    reconstructed += part.text.slice(part.text.indexOf('\n') + 1);
    offset = Number(range[2]);
  }
  assert.equal(reconstructed, unicodeSource);
});
