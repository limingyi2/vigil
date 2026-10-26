'use strict';
// Versioned application overlays: never rewrite a running app.asar or bundled Brain.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const vm = require('vm');
const hash = data => crypto.createHash('sha256').update(data).digest('hex');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
const fileHash = file => hash(fs.readFileSync(file));

function create(options) {
  const baseRoot = path.resolve(options.baseRoot);
  const root = path.join(path.resolve(options.userData), 'local-patches');
  const stateFile = path.join(root, 'state.json');
  const baseId = hash(fs.readFileSync(path.join(baseRoot, 'package.json')) + '\n'
    + (fs.existsSync(path.join(baseRoot, 'build-info.json'))
      ? fs.readFileSync(path.join(baseRoot, 'build-info.json')) : 'development'));
  const state = () => {
    if (!fs.existsSync(stateFile)) return { schema: 1, baseId, active: null, previous: null, pending: null };
    const value = read(stateFile);
    if (value.schema !== 1) throw Error('不支持的本地补丁状态格式');
    return value.baseId === baseId ? value : { schema: 1, baseId, active: null, previous: null, pending: null };
  };
  const save = value => {
    fs.mkdirSync(root, { recursive: true });
    fs.writeFileSync(stateFile + '.tmp', JSON.stringify(value, null, 2));
    fs.renameSync(stateFile + '.tmp', stateFile);
  };
  const validId = id => typeof id === 'string' && /^[a-f0-9]{24}$/.test(id);
  const version = id => {
    if (!validId(id)) throw Error('无效补丁 ID');
    return path.join(root, 'versions', id);
  };
  const appRoot = id => id ? path.join(version(id), 'app') : baseRoot;
  const inventory = dir => {
    const files = {};
    function visit(at, rel) {
      for (const item of fs.readdirSync(at, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
        const next = rel ? rel + '/' + item.name : item.name;
        const file = path.join(at, item.name);
        if (item.isSymbolicLink()) throw Error('补丁目录不能包含符号链接');
        if (item.isDirectory()) visit(file, next);
        else if (item.isFile()) files[next] = fileHash(file);
        else throw Error('补丁目录包含非普通文件');
      }
    }
    visit(dir, '');
    return files;
  };
  const verify = id => {
    const manifest = read(path.join(version(id), 'manifest.json'));
    if (manifest.baseId !== baseId || manifest.id !== id ||
        JSON.stringify(manifest.files) !== JSON.stringify(inventory(appRoot(id))))
      throw Error('本地补丁完整性校验失败');
  };
  const mutable = rel => typeof rel === 'string' && !rel.includes('\\')
    && !rel.split('/').some(p => !p || p === '.' || p === '..' || p.includes(':'))
    && /^(electron|src)\/.+\.(js|css|html|json)$/.test(rel)
    && !['electron/bootstrap.js', 'electron/local-patches.js'].includes(rel);
  const copy = (from, to) => {
    fs.mkdirSync(to, { recursive: true });
    for (const item of fs.readdirSync(from, { withFileTypes: true })) {
      if (item.isSymbolicLink()) throw Error('应用目录不能包含符号链接');
      const src = path.join(from, item.name), dst = path.join(to, item.name);
      if (item.isDirectory()) copy(src, dst);
      else if (item.isFile()) fs.writeFileSync(dst, fs.readFileSync(src));
      else throw Error('应用目录包含非普通文件');
    }
  };

  function status() {
    const value = state();
    return { ok: true, ...value, base_id: baseId, base_root: baseRoot,
      active_root: appRoot(value.active), restart_required: !!value.pending,
      format: { schema: 1, base_id: baseId, files: [{ path: 'electron/example.js',
        before_sha256: '现有文件 SHA-256；新文件填 null', sha256: '新文件 SHA-256' }] },
      detail: '补丁目录含 patch.json 和 files/<相对路径>；仅修改 electron/src 的 JS/CSS/HTML/JSON。提交后完全退出并重启；Brain、依赖和版本元数据需整包更新。' };
  }

  function stage(source) {
    const dir = path.resolve(source);
    const manifest = read(path.join(dir, 'patch.json'));
    if (manifest.schema !== 1 || manifest.base_id !== baseId || !Array.isArray(manifest.files)
        || !manifest.files.length || manifest.files.length > 128) throw Error('补丁格式或基线不匹配');
    const value = state();
    if (value.pending || value.phase === 'loading') throw Error('已有待重启或未确认健康的补丁');
    const current = appRoot(value.active);
    if (value.active) verify(value.active);
    const seen = new Set(), payloads = [];
    for (const entry of manifest.files) {
      if (!mutable(entry.path) || seen.has(entry.path)) throw Error('不允许的补丁路径：' + entry.path);
      seen.add(entry.path);
      const payload = path.join(dir, 'files', entry.path);
      const real = fs.realpathSync(payload), inside = path.relative(fs.realpathSync(dir), real);
      if (inside.startsWith('..') || path.isAbsolute(inside) || !fs.statSync(real).isFile())
        throw Error('补丁文件越界');
      const bytes = fs.readFileSync(real);
      if (bytes.length > 4 * 1024 * 1024 || fileHash(real) !== entry.sha256) throw Error('补丁文件校验失败');
      const target = path.join(current, entry.path);
      const before = fs.existsSync(target) ? fileHash(target) : null;
      if (before !== entry.before_sha256) throw Error('目标文件已变化：' + entry.path);
      if (entry.path.endsWith('.js')) new vm.Script(bytes.toString('utf8'), { filename: entry.path });
      if (entry.path.endsWith('.json')) JSON.parse(bytes.toString('utf8'));
      payloads.push([entry.path, bytes]);
    }
    const id = crypto.randomBytes(12).toString('hex');
    const destination = appRoot(id);
    copy(current, destination);
    for (const [rel, bytes] of payloads) {
      fs.mkdirSync(path.dirname(path.join(destination, rel)), { recursive: true });
      fs.writeFileSync(path.join(destination, rel), bytes);
    }
    fs.writeFileSync(path.join(version(id), 'manifest.json'), JSON.stringify({ id, baseId, files: inventory(destination) }));
    verify(id);
    value.pending = { id, kind: 'patch' };
    save(value);
    return { ok: true, id, restart_required: true, files: [...seen], detail: '补丁已暂存，完全退出并重启客户端后生效。' };
  }

  function rollback() {
    const value = state();
    if (!value.active && !value.pending) return { ok: false, error: '没有可回退的补丁' };
    value.pending = { id: value.previous || null, kind: 'rollback' };
    save(value);
    return { ok: true, restart_required: true };
  }

  function select() {
    const value = state();
    // A second launch must not mistake a still-running first process for a crash.
    if (value.phase === 'loading' && value.pid) {
      let alive = false;
      try { process.kill(value.pid, 0); alive = true; } catch (_) {}
      if (alive) return appRoot(value.active);
    }
    if (value.phase === 'loading') {
      value.last_error = '上次补丁启动未确认健康，已恢复上一版';
      value.active = value.previous || null;
      value.previous = null;
    }
    if (value.pending) {
      try {
        if (value.pending.id) verify(value.pending.id);
        const next = value.pending.id;
        value.previous = value.active;
        value.active = next;
      } catch (error) { value.last_error = error.message; }
      value.pending = null;
    }
    if (value.active) {
      try { verify(value.active); }
      catch (error) {
        value.last_error = error.message;
        value.active = null;
        value.previous = null;
      }
    }
    value.phase = value.active ? 'loading' : 'healthy';
    value.pid = process.pid;
    save(value);
    return appRoot(value.active);
  }
  function healthy() {
    const value = state();
    if (value.phase === 'loading' && value.pid === process.pid) {
      value.phase = 'healthy';
      save(value);
    }
  }
  return { status, stage, rollback, select, healthy };
}
module.exports = { create };
