'use strict';
// 推送更新的状态机：只负责状态与动作，不碰窗口、不碰 Electron。
// main.js 注入四样东西 —— 本地版本号怎么读、更新源、安装器怎么拉、怎么退出。
// 抽出来是为了能直接在 node 里跑（tests/test_update_manager.py），
// 不用为了验一条"下没下完"去起一个 Electron。
//
// 状态（渲染层只认这几个）：
//   idle         没有新版，左下角圆圈隐藏
//   available    有新版，点一下开始下载
//   downloading  下载中，percent 0..99（留 1% 给校验）
//   verifying    算 sha256
//   ready        已下载校验通过，点一下安装
//   installing   安装器已拉起，客户端即将退出
//   error        出错，点一下重试
const fs = require('fs');
const crypto = require('crypto');

const EMIT_MS = 120;

function sha256File(p) {
  return new Promise((resolve) => {
    try {
      const h = crypto.createHash('sha256');
      fs.createReadStream(p).on('data', (c) => h.update(c))
        .on('end', () => resolve(h.digest('hex')))
        .on('error', () => resolve(''));
    } catch (e) { resolve(''); }
  });
}

function createUpdateManager(options) {
  const opt = options || {};
  const source = opt.source;
  const downloadPath = opt.downloadPath;
  const emit = opt.emit || (() => {});
  const spawnInstaller = opt.spawnInstaller || (() => {});
  const quit = opt.quit || (() => {});
  const readCode = opt.readCode || (() => 0);
  const now = opt.now || (() => Date.now());
  const emitMs = opt.emitMs == null ? EMIT_MS : Number(opt.emitMs);

  const upd = {
    phase: 'idle', current: Number(readCode()) || 0, latest: 0, ver: '', notes: '',
    got: 0, total: 0, percent: 0, err: '',
  };
  let checking = null;
  let downloading = null;
  let emittedAt = 0;

  function snapshot() { return Object.assign({}, upd); }
  function push() { emittedAt = now(); emit(snapshot()); }
  function set(patch) { Object.assign(upd, patch); push(); }

  // 临时目录里那份是不是就是这个版本且校验通过（同一个 sha 就不重下）
  async function cached(manifest) {
    const want = String((manifest && manifest.sha256) || '').toLowerCase();
    if (!/^[a-f0-9]{64}$/.test(want)) return false;
    try { if (fs.statSync(downloadPath).size !== Number(manifest.size)) return false; } catch (e) { return false; }
    return (await sha256File(downloadPath)) === want;
  }

  function validManifest(j) {
    const want = String((j && j.sha256) || '').toLowerCase();
    return !!(j && j.setup_url && /^[a-f0-9]{64}$/.test(want)
      && Number.isSafeInteger(j.size) && j.size > 0);
  }

  async function download(manifest) {
    if (downloading) return downloading;
    let j = manifest;
    if (!validManifest(j)) {
      const v = await source.latest();
      j = (v && v.json) || j;
    }
    if (!validManifest(j)) {
      set({ phase: 'error', err: '更新清单缺少有效的 SHA-256 或文件大小，已拒绝安装' });
      return { ok: false, err: upd.err };
    }
    const want = String(j.sha256).toLowerCase();
    set({
      phase: 'downloading', latest: Number(j.code) || upd.latest, ver: j.ver || upd.ver,
      notes: j.notes || upd.notes, got: 0, total: j.size, percent: 0, err: '',
    });
    downloading = (async () => {
      try {
        if (await cached(j)) {
          set({ phase: 'ready', got: j.size, total: j.size, percent: 100 });
          return { ok: true, cached: true };
        }
        try { fs.unlinkSync(downloadPath); } catch (e) { /* 本来就没有 */ }
        const dl = await source.download(j.setup_url, downloadPath, (got, total) => {
          const size = total || j.size || 0;
          upd.got = got;
          upd.total = size;
          // 留 1% 给校验：条满了却还没好会让人以为卡住
          upd.percent = size ? Math.min(99, Math.round((got / size) * 100)) : 0;
          if (now() - emittedAt >= emitMs) push();
        });
        if (!dl || !dl.ok) {
          set({ phase: 'error', err: String((dl && dl.err) || '网络错误') });
          return { ok: false, err: upd.err };
        }
        set({ phase: 'verifying', got: j.size, total: j.size, percent: 100 });
        const sha = await sha256File(downloadPath);
        let size = 0;
        try { size = fs.statSync(downloadPath).size; } catch (e) { size = -1; }
        if (sha !== want || size !== j.size) {
          try { fs.unlinkSync(downloadPath); } catch (e) { /* 删不掉就留着，下次会重新校验 */ }
          set({ phase: 'error', err: '安装包校验不一致，已删除，点一下重试' });
          return { ok: false, err: 'sha256 不匹配' };
        }
        set({ phase: 'ready', got: j.size, total: j.size, percent: 100, err: '' });
        return { ok: true };
      } catch (error) {
        set({ phase: 'error', err: String((error && error.message) || error) });
        return { ok: false, err: upd.err };
      } finally {
        downloading = null;
      }
    })();
    return downloading;
  }

  async function check(mode) {
    // mode='ask'    关于页按钮：查 + 有新版就直接下载（已经不弹窗了）
    // mode='status' 只查状态，下载和安装都不动
    // 其它（启动与每小时的自动检查）只「发现」，圆圈亮起来等人点
    const cur = Number(readCode()) || upd.current;
    upd.current = cur;
    const base = { current: cur, phase: upd.phase };
    if (checking) return checking;
    checking = (async () => {
      let v;
      try {
        v = await source.latest();
      } catch (error) {
        return Object.assign(base, { ok: false, update: false, err: String((error && error.message) || error) });
      }
      const j = (v && v.json) || {};
      if (!v || !v.ok || !j.code) {
        // 查不到不算「更新失败」：网络抖一下不该在界面上留个红点，
        // 已经下好的包也不该因为这一次查询失败被作废。
        const err = (j && j.err) || (v && v.err) || ('HTTP ' + ((v && v.status) || 0));
        return Object.assign(base, { ok: false, update: false, err });
      }
      const latest = Number(j.code) || 0;
      upd.latest = latest;
      upd.ver = j.ver || '';
      upd.notes = j.notes || '';
      const info = Object.assign(base, { ok: true, update: latest > cur, latest, ver: j.ver || '', notes: j.notes || '' });
      if (latest <= cur) {
        set({ phase: 'idle', got: 0, total: 0, percent: 0, err: '' });
        return Object.assign(info, { phase: upd.phase });
      }
      // 已经在下载 / 校验 / 下好了，别把状态打回 available 让人重复点
      if (upd.phase === 'downloading' || upd.phase === 'verifying' || upd.phase === 'ready') {
        return Object.assign(info, { phase: upd.phase });
      }
      if (validManifest(j) && await cached(j)) {
        set({ phase: 'ready', got: j.size, total: j.size, percent: 100, err: '' });
        return Object.assign(info, { phase: upd.phase });
      }
      set({ phase: 'available', got: 0, total: 0, percent: 0, err: '' });
      // 用户自己点的「检查更新」：顺手接着下，避免他再点一次圆圈
      if (mode === 'ask' && validManifest(j)) download(j);
      return Object.assign(info, { phase: upd.phase });
    })();
    try {
      return await checking;
    } finally {
      checking = null;
    }
  }

  function install() {
    if (upd.phase !== 'ready') return { ok: false, err: '更新还没下载完', phase: upd.phase };
    try { if (!fs.statSync(downloadPath).isFile()) throw new Error('missing'); } catch (e) {
      set({ phase: 'error', err: '安装包不见了，点一下重新下载' });
      return { ok: false, err: upd.err };
    }
    set({ phase: 'installing' });
    try {
      spawnInstaller(downloadPath);
    } catch (error) {
      set({ phase: 'error', err: '安装器启动失败：' + String((error && error.message) || error) });
      return { ok: false, err: upd.err };
    }
    setTimeout(() => { try { quit(); } catch (e) { /* 退出流程自会处理 */ } }, opt.quitDelay == null ? 400 : opt.quitDelay);
    return { ok: true, installing: true };
  }

  return { check, download: () => download(null), install, snapshot, state: upd };
}

module.exports = { createUpdateManager, sha256File };
