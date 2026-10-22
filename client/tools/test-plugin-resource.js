const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('http');
const { createPluginResources, shouldCancelLoopbackRequest } = require('../electron/plugin-resource');

test('generic resource URL attaches auth, streams ranges and serves web children', async () => {
  const seen = [];
  const upstream = http.createServer((req, res) => {
    seen.push({ url: req.url, auth: req.headers.authorization, range: req.headers.range });
    const u = new URL(req.url, 'http://localhost');
    if (req.headers.authorization !== 'Bearer test-secret') { res.writeHead(401); return res.end(); }
    if (u.searchParams.get('file') === 'index.html') {
      res.writeHead(200, { 'Content-Type': 'text/html' }); return res.end('<img src="assets/a.png">');
    }
    if (req.headers.range) {
      res.writeHead(206, { 'Content-Type': 'video/mp4', 'Content-Range': 'bytes 2-5/10', 'Content-Length': 4 });
      return res.end('2345');
    }
    res.writeHead(200, { 'Content-Type': 'image/png' }); res.end('fixture-image');
  });
  await new Promise(r => upstream.listen(0, '127.0.0.1', r));
  const resources = createPluginResources({ getBase: () => 'http://127.0.0.1:' + upstream.address().port,
    getToken: async () => 'test-secret' });
  try {
    const url = await resources.url('/api/plugin/wallpaper/resource?id=123&file=video.mp4');
    assert(!url.includes('test-secret'));
    const response = await fetch(url, { headers: { Range: 'bytes=2-5' } });
    assert.equal(response.status, 206);
    assert.equal(response.headers.get('content-range'), 'bytes 2-5/10');
    assert.equal(await response.text(), '2345');
    assert.equal(seen[0].auth, 'Bearer test-secret');
    assert.equal(seen[0].range, 'bytes=2-5');
    const web = await resources.url('/api/plugin/wallpaper/resource?id=123&file=index.html');
    const webResponse = await fetch(web);
    assert.equal(await (await fetch(new URL('assets/a.png', web))).text(), 'fixture-image');
    assert.match(seen.at(-1).url, /file=assets%2Fa.png/);
    assert.equal((await fetch(url.replace(/\/r\/[a-f0-9]+\//, '/r/' + '0'.repeat(48) + '/'))).status, 404);
    for (const invalid of ['/api/persona', '//evil.example/api/plugin/wallpaper/resource',
      '/api/plugin/wallpaper/resource?id=../&file=x',
      '/api/plugin/wallpaper/resource?id=123&file=../../x']) {
      await assert.rejects(resources.url(invalid));
    }
  } finally {
    resources.close();
    await new Promise(r => upstream.close(r));
  }
});


test('resource tickets expire when idle and revoke on unload', async () => {
  const upstream = http.createServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'image/png' }); res.end('ok');
  });
  await new Promise(r => upstream.listen(0, '127.0.0.1', r));
  const resources = createPluginResources({
    getBase: () => 'http://127.0.0.1:' + upstream.address().port,
    getToken: async () => 'test-secret', ticketIdleMs: 120,
  });
  try {
    const resource = '/api/plugin/wallpaper/resource?id=123&file=scene.json';
    const first = await resources.url(resource);
    assert.equal(resources.isApprovedUrl(first), true);
    assert.equal(resources.isApprovedUrl('http://127.0.0.1:43210/'), false);
    await new Promise(r => setTimeout(r, 200));
    assert.equal(resources.isApprovedUrl(first), false);
    assert.equal((await fetch(first)).status, 410);
    const second = await resources.url(resource);
    assert.notEqual(first, second);
    assert.equal((await fetch(second)).status, 200);
    resources.releaseResources();
    assert.equal(resources.isApprovedUrl(second), false);
    assert.equal((await fetch(second)).status, 404);
  } finally {
    resources.close();
    await new Promise(r => upstream.close(r));
  }
});

test('loopback policy covers frames, images, media, workers and other webContents', () => {
  const approved = url => url.includes('/r/') && url.includes('a'.repeat(48));
  for (const resourceType of ['xhr','fetch','image','media','subFrame','script','worker','stylesheet']) {
    assert.equal(shouldCancelLoopbackRequest({ url: 'http://127.0.0.1:4321/r/' + 'a'.repeat(48) + '/scene.json',
      webContentsId: 7, resourceType }, 7, approved), false, resourceType + ' approved media capability');
    assert.equal(shouldCancelLoopbackRequest({ url: 'http://127.0.0.1:4321/private', webContentsId: 7, resourceType },
      7, approved), true, resourceType + ' arbitrary loopback denied');
  }
  assert.equal(shouldCancelLoopbackRequest({url:'http://127.0.0.1:4321/r/'+'a'.repeat(48)+'/scene.json',webContentsId:8,resourceType:'image'},7,approved),false,
    'capability URLs can be consumed by the sandboxed plugin frame');
  assert.equal(shouldCancelLoopbackRequest({url:'https://example.com',webContentsId:7,resourceType:'image'},7,approved),false,
    'remote non-loopback requests are unaffected');
});
