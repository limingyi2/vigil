// Run with node. Exercises the actual packaged bootstrap across separate Electron launches.
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { spawnSync } = require('child_process');
const asar = require('@electron/asar');
const sha = data => crypto.createHash('sha256').update(data).digest('hex');

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-patch-electron-'));
  try {
    const source = path.join(dir, 'source'), archive = path.join(dir, 'app.asar');
    const home = path.join(dir, 'profile'), marker = path.join(dir, 'result.json');
    fs.mkdirSync(path.join(source, 'electron'), { recursive: true });
    fs.writeFileSync(path.join(source, 'package.json'), JSON.stringify({ name: 'vigil-patch-fixture',
      version: '1.0.0', main: 'electron/bootstrap.js' }));
    for (const name of ['bootstrap.js', 'local-patches.js'])
      fs.copyFileSync(path.join(__dirname, '../electron', name), path.join(source, 'electron', name));
    const main = `const {app,BrowserWindow}=require('electron');
      const fs=require('fs'); const version=1;
      app.whenReady().then(()=>{const w=new BrowserWindow({show:false});
        const p=global.__vigilLocalPatches;
        const result=process.env.PATCH_SOURCE?p.stage(process.env.PATCH_SOURCE):p.status();
        fs.writeFileSync(process.env.PATCH_MARKER,JSON.stringify({version,result}));
        if(process.env.PATCH_MODE!=='crash')p.healthy();
        w.destroy();app.exit(0);});`;
    fs.writeFileSync(path.join(source, 'electron/main.js'), main);
    await asar.createPackage(source, archive);
    const env = { ...process.env, VIGIL_USER_DATA: home, PATCH_MARKER: marker };
    delete env.ELECTRON_RUN_AS_NODE;
    const run = extra => {
      const result = spawnSync(require('electron'), [archive], {
        env: { ...env, ...extra }, timeout: 20000, encoding: 'utf8', windowsHide: true });
      assert.equal(result.status, 0, result.stderr || String(result.error));
      return JSON.parse(fs.readFileSync(marker, 'utf8'));
    };
    const initial = run({});
    assert.equal(initial.version, 1);
    const payload = path.join(dir, 'payload');
    fs.mkdirSync(path.join(payload, 'files/electron'), { recursive: true });
    const updated = main.replace('const version=1', 'const version=2');
    fs.writeFileSync(path.join(payload, 'files/electron/main.js'), updated);
    fs.writeFileSync(path.join(payload, 'patch.json'), JSON.stringify({ schema: 1,
      base_id: initial.result.base_id, files: [{ path: 'electron/main.js',
        before_sha256: sha(main), sha256: sha(updated) }] }));
    assert.equal(run({ PATCH_SOURCE: payload }).result.restart_required, true);
    assert.equal(run({ PATCH_MODE: 'crash' }).version, 2);
    assert.equal(run({}).version, 1, 'An unhealthy overlay must roll back across real processes');
    assert.equal(run({ PATCH_SOURCE: payload }).result.restart_required, true);
    assert.equal(run({}).version, 2);
    assert.equal(run({}).version, 2, 'Healthy overlay must survive further restarts');
    console.log('PASS real app.asar staging, restart activation, crash recovery and persistence');
  } finally {
    assert.equal(path.dirname(fs.realpathSync(dir)), fs.realpathSync(os.tmpdir()));
    fs.rmSync(dir, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
