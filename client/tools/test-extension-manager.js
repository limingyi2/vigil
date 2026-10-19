'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { WallpaperExtensionManager, EXTENSION_ID, semverGte } = require('../electron/extension-manager');

const hash = value => crypto.createHash('sha256').update(value).digest('hex');
function writePackage(root, version = '1.1.7', options = {}) {
  fs.mkdirSync(root, { recursive: true });
  const contents = {
    'wallpaper-host.js': 'host-' + version,
    'wallpaper.js': 'renderer-' + version,
    'wallpaper.css': '.wallpaper-' + version + '{}',
  };
  for (const [file, value] of Object.entries(contents)) fs.writeFileSync(path.join(root, file), value);
  const manifest = {
    schema: 'vigil.extension/v1', id: EXTENSION_ID, version, kind: 'desktop-extension',
    entry: 'wallpaper-host.js', renderer: { script: 'wallpaper.js', style: 'wallpaper.css' }, min_core: '1.1.7',
    protocol: { name: 'vigil-desktop-extension', major: 1, min_minor: 0 },
    artifacts: [{ platform: 'windows-x64', url: 'https://example.invalid/wallpaper.zip', sha256: 'a'.repeat(64) }],
    permissions: ['desktop.wallpaper.control', 'desktop.ui.contribute'],
    files: Object.entries(contents).map(([file, value]) => ({ path: file, sha256: hash(value) })),
    data_migrations: [],
  };
  if (options.permission) manifest.permissions = [options.permission];
  if (options.entry) manifest.entry = options.entry;
  if (options.minCore) manifest.min_core = options.minCore;
  if (options.protocol) manifest.protocol.major = options.protocol;
  if (options.badHash) manifest.files[0].sha256 = '0'.repeat(64);
  fs.writeFileSync(path.join(root, 'manifest.json'), JSON.stringify(manifest, null, 2));
}

async function run() {
  assert.equal(semverGte('1.1.7', '1.1.6'), true);
  assert.equal(semverGte('1.1.7', '1.1.8'), false);
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-extension-test-'));
  const userData = path.join(temp, 'user-data');
  const preserved = path.join(userData, 'wallpaper-engine', 'settings.json');
  fs.mkdirSync(path.dirname(preserved), { recursive: true }); fs.writeFileSync(preserved, '{"fixture":true}');
  let created = 0, closed = 0, failVersion = '';
  const hostFactory = (_root, manifest) => {
    created++;
    return {
      async request(_method, _resource) {
        if (manifest.version === failVersion) throw new Error('fixture health failure');
        return { data: { ok: true } };
      },
      close() { closed++; },
    };
  };
  const manager = new WallpaperExtensionManager({ userData, coreVersion: '1.1.7', hostFactory });
  assert.equal((await manager.getState()).installed, false);
  assert.equal(created, 0, 'uninstalled state must not create a host');
  const packageV1 = path.join(temp, 'package-v1'); writePackage(packageV1);
  await assert.rejects(() => manager.installFromDirectory(packageV1), /权限尚未由用户授予/);
  const grants = ['desktop.wallpaper.control', 'desktop.ui.contribute'];
  const afterInstall = await manager.installFromDirectory(packageV1, { grantedPermissions: grants });
  assert.equal(afterInstall.installed, true);
  assert.equal(afterInstall.enabled, false);
  assert.equal(created, 0, 'installed but disabled extension must not start its host');

  await manager.enable();
  assert.equal(created, 1);
  assert.equal((await manager.rendererAssets()).script, 'renderer-1.1.7');
  const stateFile = path.join(userData, 'extensions', 'state.json');
  const persisted = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
  assert.equal(persisted.wallpaper.enabled, true);
  const restarted = new WallpaperExtensionManager({ userData, coreVersion: '1.1.7', hostFactory });
  await restarted.restore();
  assert.equal((await restarted.getState()).enabled, true);
  assert.equal(created, 2);
  restarted.close();

  failVersion = '1.1.8';
  const packageV2 = path.join(temp, 'package-v2'); writePackage(packageV2, '1.1.8');
  await assert.rejects(() => restarted.installFromDirectory(packageV2, { grantedPermissions: grants }), /健康检查失败/);
  const afterRollback = await restarted.getState();
  assert.equal(afterRollback.enabled, true);
  assert.equal(afterRollback.activeVersion, '1.1.7');
  assert.equal(fs.existsSync(path.join(userData, 'extensions', 'programs', EXTENSION_ID, '1.1.7')), true);

  await restarted.disable();
  assert.equal((await restarted.getState()).enabled, false);
  assert.ok(closed >= 2, 'disable and failed candidate release their hosts');
  await restarted.uninstall();
  assert.equal((await restarted.getState()).installed, false);
  assert.equal(fs.readFileSync(preserved, 'utf8'), '{"fixture":true}', 'user data must survive uninstall');
  assert.equal(fs.existsSync(path.join(userData, 'extensions', 'programs', EXTENSION_ID, '1.1.7')), false,
    'uninstall removes executable program resources');

  for (const [name, options, expected] of [
    ['unknown-permission', { permission: 'filesystem.all' }, /清单无效/],
    ['traversal', { entry: '../outside.js' }, /入口路径无效|清单无效/],
    ['bad-hash', { badHash: true }, /校验失败/],
    ['wrong-protocol', { protocol: 2 }, /协议版本不兼容/],
    ['future-core', { minCore: '9.0.0' }, /需要更新版本的 Vigil/],
  ]) {
    const dir = path.join(temp, name); writePackage(dir, '1.1.7', options);
    await assert.rejects(() => manager.installFromDirectory(dir, { grantedPermissions: grants }), expected);
  }

  manager.close(); restarted.close(); fs.rmSync(temp, { recursive: true, force: true });
  console.log('PASS Wallpaper extension install/state/health/disable/restart/rollback/data retention and fail-closed manifest checks');
}

run().catch(e => { console.error(e); process.exitCode = 1; });
