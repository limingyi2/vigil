const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {createWallpaperHost} = require('../electron/wallpaper-host');

test('native wallpaper host isolates settings, gates media, rewrites renderer URLs and imports files', async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-host-test-'));
  const vendor = path.join(tmp, 'vendor');
  fs.cpSync(path.resolve(__dirname, '../vendor/dsh-wallpaper-engine'), vendor, {recursive:true});
  const source = path.join(vendor, 'lib/index.js');
  fs.writeFileSync(source, fs.readFileSync(source,'utf8').replace(
    'return [...env, ...(reg ? [reg] : []), ...STEAM_PROBE_DIRS, ...wsl];', 'return [...env];'));
  process.env.DSH_WE_STEAM_ROOT = path.join(tmp,'no-steam');
  const host = createWallpaperHost({dataDir:path.join(tmp,'state'),vendorDir:vendor,logger:{info(){},warn(){},error(){}}});
  try {
    const base = await host.start();
    const origin = new URL(base).origin;
    assert.equal((await fetch(origin+'/wallpaper-engine/settings')).status,403);
    assert.equal((await fetch(base+'/wallpaper-engine/settings')).status,403);
    assert.equal((await fetch(base+'/wallpaper-engine/settings',{method:'PUT',body:'{}'})).status,403);
    assert.equal((await fetch(origin+'/w/wrong/wallpaper-engine/scene-live/index.html')).status,403);
    const html = await (await fetch(base+'/wallpaper-engine/scene-live/index.html')).text();
    assert(html.includes(new URL(base).pathname+'/wallpaper-engine/scene-live/assets/'));
    assert(html.includes('vigil-control.js'));
    const jsFile=/src="([^"]+renderer-[^"]+\.js)"/.exec(html)[1];
    assert.equal((await fetch(origin+jsFile)).status,200);
    assert.equal((await fetch(base+'/wallpaper-engine/scene-files/unknown/secret.txt')).status,404);
    await assert.rejects(host.request('GET','/../../settings'));
    await host.request('PUT','/settings',{settingsVersion:6,id:'sample-id',glassAlpha:51,rotationGroups:[{id:'vigil-library',name:'test',interval:70,wallpaperIds:[],order:'sequence'}]});
    const saved = (await host.request('GET','/settings')).data.settings;
    assert.equal(saved.id,'sample-id'); assert.equal(saved.glassAlpha,51);
    assert.equal(saved.rotationGroups[0].interval,70);
    assert.equal(JSON.parse(fs.readFileSync(path.join(tmp,'state/config.json'),'utf8')).settings.id,'sample-id');
    const png = path.join(tmp,'sample.png');
    fs.writeFileSync(png,Buffer.from('89504e470d0a1a0a','hex'));
    await host.importFile(png);
    const inv = (await host.request('GET','/inventory')).data;
    const image = inv.wallpapers.find(w=>w.type==='image'); assert(image);
    const r = await fetch(image.media,{headers:{Range:'bytes=1-3'}});
    assert.equal(r.status,206); assert.deepEqual(Buffer.from(await r.arrayBuffer()),fs.readFileSync(png).subarray(1,4));
    await assert.rejects(host.importFile(path.join(tmp,'bad.exe')));
  } finally {host.close();}
});
