const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('http');
const { createPluginMedia } = require('../electron/plugin-media');

test('opaque media URL attaches auth, streams ranges and serves web children', async () => {
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
  const media = createPluginMedia({ getBase: () => 'http://127.0.0.1:' + upstream.address().port,
    getToken: async () => 'test-secret' });
  try {
    const url = await media.url('/api/plugin/wallpaper/we/media?id=123&file=video.mp4');
    assert(!url.includes('test-secret'));
    const response = await fetch(url, { headers: { Range: 'bytes=2-5' } });
    assert.equal(response.status, 206);
    assert.equal(response.headers.get('content-range'), 'bytes 2-5/10');
    assert.equal(await response.text(), '2345');
    assert.equal(seen[0].auth, 'Bearer test-secret');
    assert.equal(seen[0].range, 'bytes=2-5');
    const web = await media.url('/api/plugin/wallpaper/we/media?id=123&file=index.html');
    const webResponse = await fetch(web);
    assert.match(webResponse.headers.get('content-security-policy'), /object-src 'none'/);
    assert.equal(await (await fetch(new URL('assets/a.png', web))).text(), 'fixture-image');
    assert.match(seen.at(-1).url, /file=assets%2Fa.png/);
    assert.equal((await fetch(url.replace(/\/r\/[a-f0-9]+\//, '/r/' + '0'.repeat(48) + '/'))).status, 404);
    for (const invalid of ['/api/persona', '//evil.example/api/plugin/wallpaper/we/media',
      '/api/plugin/wallpaper/we/media?id=../&file=x',
      '/api/plugin/wallpaper/we/media?id=123&file=../../x']) {
      await assert.rejects(media.url(invalid));
    }
  } finally {
    media.close();
    await new Promise(r => upstream.close(r));
  }
});
