const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { create } = require('../electron/local-patches');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-patches-'));
  // Verify the resolved cleanup target stays directly under the intended temp root.
  t.after(() => {
    assert.equal(path.dirname(fs.realpathSync(dir)), fs.realpathSync(os.tmpdir()));
    fs.rmSync(dir, { recursive: true, force: true });
  });
  const baseRoot = path.join(dir, 'base'), userData = path.join(dir, 'data');
  fs.mkdirSync(path.join(baseRoot, 'electron'), { recursive: true });
  fs.mkdirSync(path.join(baseRoot, 'src'));
  fs.writeFileSync(path.join(baseRoot, 'package.json'), '{"version":"1.0.0"}');
  fs.writeFileSync(path.join(baseRoot, 'electron/main.js'), 'module.exports = 1;');
  const options = { baseRoot, userData };
  const patches = create(options);
  function payload(rel = 'electron/main.js', text = 'module.exports = 2;', before = sha('module.exports = 1;')) {
    const source = fs.mkdtempSync(path.join(dir, 'payload-'));
    // Construct malicious paths only in the manifest, never as filesystem writes.
    if (!rel.includes('..') && !rel.includes(':') && !path.isAbsolute(rel)) {
      fs.mkdirSync(path.dirname(path.join(source, 'files', rel)), { recursive: true });
      fs.writeFileSync(path.join(source, 'files', rel), text);
    }
    fs.writeFileSync(path.join(source, 'patch.json'), JSON.stringify({ schema: 1,
      base_id: patches.status().base_id, files: [{ path: rel, before_sha256: before, sha256: sha(text) }] }));
    return source;
  }
  return { dir, baseRoot, userData, options, patches, payload };
}

test('stage leaves installed app intact, restart loads overlay, rollback restores base', t => {
  const f = fixture(t);
  const result = f.patches.stage(f.payload());
  assert.equal(result.restart_required, true);
  assert.equal(require(path.join(f.baseRoot, 'electron/main.js')), 1);
  const next = create(f.options);
  assert.equal(require(path.join(next.select(), 'electron/main.js')), 2);
  next.healthy();
  assert.equal(next.status().phase, 'healthy');
  assert.equal(next.rollback().restart_required, true);
  assert.equal(create(f.options).select(), f.baseRoot);
});

test('crashed overlay restores last healthy version on next startup', t => {
  const f = fixture(t);
  f.patches.stage(f.payload());
  f.patches.select();
  const statePath = path.join(f.userData, 'local-patches/state.json');
  const state = JSON.parse(fs.readFileSync(statePath));
  state.pid = 2147483647;
  fs.writeFileSync(statePath, JSON.stringify(state));
  assert.equal(create(f.options).select(), f.baseRoot);
  assert.match(f.patches.status().last_error, /恢复上一版/);
});

test('tampered payload, stale baseline, invalid JS, forbidden paths are rejected', t => {
  const f = fixture(t);
  for (const rel of ['../escape.js', '/escape.js', 'src/../../escape.js',
    'electron/bootstrap.js', 'electron/local-patches.js', 'package.json',
    'node_modules/keytar/index.js', 'resources/vigil-brain.exe'])
    assert.throws(() => f.patches.stage(f.payload(rel)), /不允许/);
  assert.throws(() => f.patches.stage(f.payload('electron/main.js', 'function {')), SyntaxError);
  assert.throws(() => f.patches.stage(f.payload('electron/main.js', 'module.exports=2;', '0'.repeat(64))), /已变化/);
  const source = f.payload();
  fs.writeFileSync(path.join(source, 'files/electron/main.js'), 'tampered');
  assert.throws(() => f.patches.stage(source), /校验失败/);
  assert.equal(f.patches.status().pending, null);
});

test('tampered staged tree is discarded and a full update invalidates overlays', t => {
  const f = fixture(t);
  const staged = f.patches.stage(f.payload());
  const overlay = path.join(f.userData, 'local-patches/versions', staged.id, 'app/electron/main.js');
  fs.writeFileSync(overlay, 'tampered');
  assert.equal(create(f.options).select(), f.baseRoot);
  assert.equal(f.patches.status().pending, null);
  fs.writeFileSync(path.join(f.baseRoot, 'package.json'), '{"version":"2.0.0"}');
  assert.equal(create(f.options).select(), f.baseRoot);
  assert.equal(create(f.options).status().active, null);
});

test('new files work, repeated staging is refused and changes bind current file hashes', t => {
  const f = fixture(t);
  f.patches.stage(f.payload('src/new.js', 'const fixture = true;', null));
  assert.throws(() => f.patches.stage(f.payload()), /已有待重启/);
  const root = f.patches.select();
  f.patches.healthy();
  assert.equal(fs.readFileSync(path.join(root, 'src/new.js'), 'utf8'), 'const fixture = true;');
  f.patches.stage(f.payload());
  assert.equal(require(path.join(f.patches.select(), 'electron/main.js')), 2);
});
