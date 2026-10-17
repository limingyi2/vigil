'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const https = require('https');
const {EventEmitter} = require('events');
const {Readable} = require('stream');
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const updates = require('../electron/public-updates');

const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

test('GitHub latest manifest and installer download verify redirects, asset name, size and SHA-256', async t => {
  const installer = Buffer.from('isolated Vigil installer bytes');
  const manifest = Buffer.from(JSON.stringify({
    code: 21, ver: '1.1.0', source_commit: '9'.repeat(40), candidate: false,
    sha256: sha256(installer), size: installer.length, setup_file: 'Vigil.Setup.1.1.0.exe',
    notes: 'Unified Electron and Brain update',
  }));
  const release = Buffer.from(JSON.stringify({assets: [
    {name: 'release-manifest.json', size: manifest.length,
      browser_download_url: 'https://github.com/limingyi2/vigil/releases/download/v1.1.0/release-manifest.json'},
    {name: 'Vigil.Setup.1.1.0.exe', size: installer.length,
      browser_download_url: 'https://github.com/limingyi2/vigil/releases/download/v1.1.0/Vigil.Setup.1.1.0.exe'},
  ]}));
  const replies = new Map([
    ['https://api.github.com/repos/limingyi2/vigil/releases/latest', {status: 200, body: release}],
    ['https://github.com/limingyi2/vigil/releases/download/v1.1.0/release-manifest.json',
      {status: 302, location: 'https://release-assets.githubusercontent.com/manifest'}],
    ['https://release-assets.githubusercontent.com/manifest', {status: 200, body: manifest}],
    ['https://github.com/limingyi2/vigil/releases/download/v1.1.0/Vigil.Setup.1.1.0.exe',
      {status: 302, location: 'https://objects.githubusercontent.com/setup'}],
    ['https://objects.githubusercontent.com/setup', {status: 200, body: installer}],
  ]);
  const originalGet = https.get;
  const requested = [];
  https.get = (url, _options, callback) => {
    const key = url.href || String(url);
    requested.push(key);
    const reply = replies.get(key);
    const req = new EventEmitter();
    req.destroy = error => { if (error) process.nextTick(() => req.emit('error', error)); };
    process.nextTick(() => {
      if (!reply) { const error = new Error('Unexpected URL: ' + key); req.emit('error', error); return; }
      const res = Readable.from(reply.body ? [reply.body] : []);
      res.statusCode = reply.status;
      res.headers = {...(reply.location ? {location: reply.location} : {}),
        ...(reply.body ? {'content-length': String(reply.body.length)} : {})};
      callback(res);
    });
    return req;
  };
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-public-update-'));
  const destination = path.join(folder, 'Vigil.Setup.1.1.0.exe');
  t.after(() => {
    https.get = originalGet;
    fs.rmSync(folder, {recursive: true, force: true});
  });

  const latest = await updates.latest();
  assert.equal(latest.ok, true, latest.err);
  assert.equal(latest.json.setup_file, 'Vigil.Setup.1.1.0.exe');
  assert.equal(latest.json.public_download, true);
  const result = await updates.download(latest.json.setup_url, destination);
  assert.equal(result.ok, true, result.err);
  assert.equal(result.size, installer.length);
  assert.equal(fs.statSync(destination).size, latest.json.size);
  assert.equal(sha256(fs.readFileSync(destination)), latest.json.sha256);
  assert.ok(requested.includes('https://release-assets.githubusercontent.com/manifest'));
  assert.ok(requested.includes('https://objects.githubusercontent.com/setup'));
});

test('GitHub updater rejects candidate manifests, wrong asset names and untrusted hosts', () => {
  const manifest = {candidate: false, code: 21, sha256: 'a'.repeat(64), size: 10,
    setup_file: 'Vigil.Setup.1.1.0.exe'};
  const release = {assets: [{name: manifest.setup_file, size: 10,
    browser_download_url: 'https://github.com/limingyi2/vigil/releases/download/v1.1.0/setup.exe'}]};
  assert.throws(() => updates.validateManifest({...manifest, candidate: true}, release));
  assert.throws(() => updates.validateManifest(manifest, {assets: []}));
  assert.throws(() => updates.allowed('http://github.com/limingyi2/vigil/releases/download/x.exe'));
  assert.throws(() => updates.allowed('https://example.invalid/setup.exe'));
});
