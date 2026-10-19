'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { WallpaperExtensionManager } = require('../electron/extension-manager');

async function run(packageDir) {
  assert.ok(packageDir, 'Provide a built Wallpaper extension directory');
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-wallpaper-package-'));
  const userData = path.join(temp, 'profile');
  const dataDir = path.join(userData, 'wallpaper-engine');
  fs.mkdirSync(dataDir, { recursive: true });
  fs.writeFileSync(path.join(dataDir, 'preserve.fixture'), 'keep');
  let started = 0, closed = 0;
  const manager = new WallpaperExtensionManager({ userData, coreVersion: '1.1.7', hostFactory: () => {
    started++;
    return { request: async () => ({ data: { ok: true } }), close: () => { closed++; } };
  } });
  try {
    await manager.installFromDirectory(packageDir, { grantedPermissions: [
      'desktop.wallpaper.control', 'desktop.ui.contribute',
    ] });
    assert.equal(started, 0, 'installing disabled extension must not start Wallpaper');
    await manager.enable();
    assert.equal(started, 1);
    const assets = await manager.rendererAssets();
    assert.ok(assets.script.includes('VigilWallpaper'));
    assert.ok(assets.style.includes('vwp-'));
    await manager.disable();
    assert.equal(closed, 1);
    const restarted = new WallpaperExtensionManager({ userData, coreVersion: '1.1.7', hostFactory: () => {
      started++;
      return { request: async () => ({ data: { ok: true } }), close: () => { closed++; } };
    } });
    assert.equal((await restarted.restore()).enabled, false, 'disabled state must remain disabled after restart');
    assert.equal(started, 1, 'restart of disabled extension must not start Wallpaper');
    assert.equal(fs.readFileSync(path.join(dataDir, 'preserve.fixture'), 'utf8'), 'keep');
    restarted.close();
    console.log('PASS built Wallpaper package manifest/hash/install/enable/assets/disable/restart/user-data preservation');
  } finally {
    manager.close();
    fs.rmSync(temp, { recursive: true, force: true });
  }
}

run(process.argv[2]).catch(error => { console.error(error); process.exitCode = 1; });
