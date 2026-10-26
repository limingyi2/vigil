const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createUpdateManager } = require('../electron/update-manager');

// 一次"下载"就是往临时目录写几段字节，并按真实实现的节奏回调进度。
function makeSource(bytes, manifest) {
  const calls = { latest: 0, download: 0 };
  return {
    calls,
    latest: async () => { calls.latest += 1; return { ok: true, json: manifest }; },
    download: async (url, dest, onProgress) => {
      calls.download += 1;
      const chunk = Math.max(1, Math.floor(bytes.length / 3));
      let got = 0;
      fs.writeFileSync(dest + '.part', Buffer.alloc(0));
      for (let i = 0; i < bytes.length; i += chunk) {
        const piece = bytes.subarray(i, Math.min(i + chunk, bytes.length));
        fs.appendFileSync(dest + '.part', piece);
        got += piece.length;
        if (onProgress) onProgress(got, bytes.length);
      }
      fs.renameSync(dest + '.part', dest);
      return { ok: true, size: got };
    },
  };
}

function fixture(code, bytes) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-upd-'));
  const manifest = {
    code,
    ver: '9.9.9-test',
    notes: 'n',
    size: bytes.length,
    sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
    setup_url: 'https://github.com/limingyi2/vigil/releases/download/x/y.exe',
  };
  return { dir, manifest, dest: path.join(dir, 'setup.exe') };
}

function build(t, opts) {
  const states = [];
  const log = { spawned: [], quit: 0 };
  const mgr = createUpdateManager(Object.assign({
    readCode: () => 42,
    downloadPath: t.dest,
    emit: (s) => states.push(s),
    spawnInstaller: (file) => log.spawned.push(file),
    quit: () => { log.quit += 1; },
    emitMs: 0,          // 测试里每个进度都要看到，不做节流
    quitDelay: 0,
  }, opts));
  return { mgr, states, log };
}

test('有新版本只报告，不自动下载', async (t) => {
  const bytes = Buffer.from('x'.repeat(300));
  const f = fixture(43, bytes);
  t.after(() => fs.rmSync(f.dir, { recursive: true, force: true }));
  const src = makeSource(bytes, f.manifest);
  const { mgr, states } = build(t, { source: src, downloadPath: f.dest });
  const r = await mgr.check('status');
  assert.equal(r.ok, true);
  assert.equal(r.update, true);
  assert.equal(r.latest, 43);
  assert.equal(r.phase, 'available');
  assert.equal(src.calls.download, 0, '只查状态时不该下载');
  assert.equal(mgr.snapshot().phase, 'available');
  assert.equal(fs.existsSync(f.dest), false);
});

test('已是最新时状态回到 idle（圆圈隐藏）', async (t) => {
  const bytes = Buffer.from('y'.repeat(10));
  const f = fixture(42, bytes);
  t.after(() => fs.rmSync(f.dir, { recursive: true, force: true }));
  const { mgr } = build(t, { source: makeSource(bytes, f.manifest), downloadPath: f.dest });
  const r = await mgr.check('status');
  assert.equal(r.update, false);
  assert.equal(mgr.snapshot().phase, 'idle');
});

test('下载有进度、校验通过后进入 ready', async (t) => {
  const bytes = Buffer.from('z'.repeat(300));
  const f = fixture(43, bytes);
  t.after(() => fs.rmSync(f.dir, { recursive: true, force: true }));
  const src = makeSource(bytes, f.manifest);
  const { mgr, states } = build(t, { source: src, downloadPath: f.dest });
  await mgr.check('status');
  const from = states.length;   // 只断言下载这一段，前面 check 的 available 不算
  const r = await mgr.download();
  assert.equal(r.ok, true);
  assert.equal(src.calls.download, 1);

  const phases = states.slice(from).map((s) => s.phase);
  assert.equal(phases[phases.length - 1], 'ready');
  assert.ok(phases.includes('downloading'), '要先进入 downloading');
  assert.ok(phases.includes('verifying'), '校验阶段要单独报一次');
  assert.equal(phases.includes('available'), false, '下载过程不该退回 available');

  const percents = states.slice(from).filter((s) => s.phase === 'downloading').map((s) => s.percent);
  assert.ok(percents.length >= 2, '至少要报两次进度');
  for (let i = 1; i < percents.length; i += 1) {
    assert.ok(percents[i] >= percents[i - 1], '进度不能倒退');
  }
  assert.ok(Math.max(...percents) <= 99, '下载阶段最多 99%，给校验留一格');
  assert.equal(mgr.snapshot().percent, 100);
  assert.equal(fs.existsSync(f.dest), true);
});

test('sha 对不上就删包并报 error，不让人装坏包', async (t) => {
  const bytes = Buffer.from('a'.repeat(200));
  const f = fixture(43, bytes);
  t.after(() => fs.rmSync(f.dir, { recursive: true, force: true }));
  const bad = Object.assign({}, f.manifest, { sha256: 'f'.repeat(64) });
  const { mgr } = build(t, { source: makeSource(bytes, bad), downloadPath: f.dest });
  const r = await mgr.download();
  assert.equal(r.ok, false);
  assert.match(r.err, /sha256/);
  assert.equal(mgr.snapshot().phase, 'error');
  assert.equal(fs.existsSync(f.dest), false, '校验失败要把包删掉');
});

test('临时目录里已有同 sha 的包就不再下载', async (t) => {
  const bytes = Buffer.from('b'.repeat(120));
  const f = fixture(43, bytes);
  t.after(() => fs.rmSync(f.dir, { recursive: true, force: true }));
  fs.writeFileSync(f.dest, bytes);
  const src = makeSource(bytes, f.manifest);
  const { mgr } = build(t, { source: src, downloadPath: f.dest });
  const r = await mgr.check('status');
  assert.equal(r.phase, 'ready');
  assert.equal(src.calls.download, 0);
});

test('about 页的 ask：查到就顺手开始下，不再二次询问', async (t) => {
  const bytes = Buffer.from('c'.repeat(90));
  const f = fixture(43, bytes);
  t.after(() => fs.rmSync(f.dir, { recursive: true, force: true }));
  const src = makeSource(bytes, f.manifest);
  const { mgr } = build(t, { source: src, downloadPath: f.dest });
  const r = await mgr.check('ask');
  assert.equal(r.update, true);
  assert.equal(src.calls.download, 1, 'ask 模式应当已经触发下载');
  // 下载是后台跑的，等它把状态推到 ready
  const deadline = Date.now() + 3000;
  while (mgr.snapshot().phase !== 'ready' && Date.now() < deadline) {
    await new Promise((res) => setTimeout(res, 10));
  }
  assert.equal(mgr.snapshot().phase, 'ready');
});

test('ready 之后点一下才装，装完退出；没下好不给装', async (t) => {
  const bytes = Buffer.from('d'.repeat(90));
  const f = fixture(43, bytes);
  t.after(() => fs.rmSync(f.dir, { recursive: true, force: true }));
  const { mgr, log } = build(t, { source: makeSource(bytes, f.manifest), downloadPath: f.dest });

  const early = mgr.install();
  assert.equal(early.ok, false, '还没下载完不能装');

  await mgr.download();
  const ok = mgr.install();
  assert.equal(ok.ok, true);
  assert.deepEqual(log.spawned, [f.dest]);
  assert.equal(mgr.snapshot().phase, 'installing');
  await new Promise((res) => setTimeout(res, 20));
  assert.equal(log.quit, 1);
});

test('更新源连不上：报 ok:false 但不在界面上留红点', async (t) => {
  const bytes = Buffer.from('e'.repeat(10));
  const f = fixture(43, bytes);
  t.after(() => fs.rmSync(f.dir, { recursive: true, force: true }));
  const { mgr } = build(t, {
    source: { latest: async () => ({ ok: false, status: 0, err: '更新连接超时' }), download: async () => ({ ok: false }) },
    downloadPath: f.dest,
  });
  const r = await mgr.check('status');
  assert.equal(r.ok, false);
  assert.equal(r.update, false);
  assert.equal(mgr.snapshot().phase, 'idle', '查询失败不该变成 error 状态');
});

test('清单缺 sha 或大小就不许下', async (t) => {
  const bytes = Buffer.from('f'.repeat(10));
  const f = fixture(43, bytes);
  t.after(() => fs.rmSync(f.dir, { recursive: true, force: true }));
  const bad = Object.assign({}, f.manifest, { sha256: '' });
  const { mgr } = build(t, { source: makeSource(bytes, bad), downloadPath: f.dest });
  const r = await mgr.download();
  assert.equal(r.ok, false);
  assert.equal(mgr.snapshot().phase, 'error');
});
