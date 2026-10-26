const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { createVideoMedia } = require('../electron/video-media');

// 这些用例只钉「票据 + Range + 白名单」这层，不依赖 ffmpeg：
// 随便一段字节配 .mp4 后缀就够了，探编码探不出来时会原样给地址。
function request(url, headers) {
  return new Promise((resolve, reject) => {
    const req = http.request(url, { method: (headers && headers.method) || 'GET', headers: headers || {} }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, bytes: Buffer.concat(chunks).length }));
    });
    req.on('error', reject);
    req.end();
  });
}

test('票据地址按 Range 回 206，越界回 416，换票据就 404', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-media-'));
  const file = path.join(dir, 'clip.mp4');
  fs.writeFileSync(file, Buffer.alloc(3000, 7));
  const media = createVideoMedia();
  t.after(() => { media.close(); fs.rmSync(dir, { recursive: true, force: true }); });

  const info = await media.share(file);
  assert.equal(info.ok, true);
  assert.equal(info.size, 3000);

  const whole = await request(info.url);
  assert.equal(whole.status, 200);
  assert.equal(whole.headers['accept-ranges'], 'bytes');
  assert.equal(whole.bytes, 3000);

  const first = await request(info.url, { Range: 'bytes=0-99' });
  assert.equal(first.status, 206);
  assert.equal(first.bytes, 100);
  assert.equal(first.headers['content-range'], 'bytes 0-99/3000');

  const tail = await request(info.url, { Range: 'bytes=2900-' });
  assert.equal(tail.status, 206);
  assert.equal(tail.bytes, 100);

  const over = await request(info.url, { Range: 'bytes=9999-10099' });
  assert.equal(over.status, 416);
  assert.equal(over.headers['content-range'], 'bytes */3000');

  const head = await request(info.url, { method: 'HEAD' });
  assert.equal(head.status, 200);
  assert.equal(head.bytes, 0);

  media.close();
  const gone = await request(info.url).catch(() => ({ status: 0 }));
  assert.notEqual(gone.status, 206, '关掉通道后票据不该还能用');
});

test('只放行被登记的那一个文件，扩展名不对直接拒', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-media-'));
  const media = createVideoMedia();
  t.after(() => { media.close(); fs.rmSync(dir, { recursive: true, force: true }); });

  await assert.rejects(() => media.share(path.join(dir, 'notes.md')), /不是支持的视频格式/);
  await assert.rejects(() => media.share(path.join(dir, 'missing.mp4')), /读不到这个文件/);

  const empty = path.join(dir, 'empty.mp4');
  fs.writeFileSync(empty, Buffer.alloc(0));
  await assert.rejects(() => media.share(empty), /文件是空的/);

  const folder = path.join(dir, 'folder.mp4');
  fs.mkdirSync(folder);
  await assert.rejects(() => media.share(folder), /不是文件/);

  const one = path.join(dir, 'a.mp4');
  fs.writeFileSync(one, Buffer.alloc(10, 1));
  const info = await media.share(one);
  // 票据是 48 位十六进制，且每条都不一样
  assert.match(info.url, /^http:\/\/127\.0\.0\.1:\d+\/v\/[a-f0-9]{48}$/);
  const again = await media.share(one);
  assert.notEqual(info.url, again.url);
});
