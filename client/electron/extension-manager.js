'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const Ajv2020 = require('ajv/dist/2020');
const addFormats = require('ajv-formats');

const EXTENSION_ID = 'com.vigil.wallpaper';
const PROTOCOL = { name: 'vigil-desktop-extension', major: 1, minor: 0 };
const MANIFEST_SCHEMA = require('./extension-manifest.schema.json');
const ajv = new Ajv2020({ allErrors: true, strict: true });
addFormats(ajv);
const validateManifest = ajv.compile(MANIFEST_SCHEMA);

function semverTuple(value) {
  const match = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.exec(value || '');
  if (!match) throw new Error('扩展版本格式无效');
  return match.slice(1, 4).map(Number);
}
function semverGte(a, b) {
  const aa = semverTuple(a), bb = semverTuple(b);
  for (let i = 0; i < 3; i++) if (aa[i] !== bb[i]) return aa[i] > bb[i];
  return true;
}
function safeRelative(value) {
  if (typeof value !== 'string' || !value || value.includes('\\') || path.posix.isAbsolute(value)) return false;
  const parts = value.split('/');
  return parts.every(p => p && p !== '.' && p !== '..');
}
function sha256(file) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    const stream = fs.createReadStream(file);
    stream.on('error', reject); stream.on('data', b => hash.update(b));
    stream.on('end', () => resolve(hash.digest('hex')));
  });
}

class WallpaperExtensionManager {
  constructor({ userData, coreVersion, hostFactory }) {
    this.userData = userData;
    this.coreVersion = coreVersion;
    this.root = path.join(userData, 'extensions');
    this.programs = path.join(this.root, 'programs');
    this.statePath = path.join(this.root, 'state.json');
    this.hostFactory = hostFactory || ((versionDir, manifest) => {
      const hostFile = path.resolve(versionDir, manifest.entry);
      const { createWallpaperHost } = require(hostFile);
      return createWallpaperHost({
        dataDir: path.join(this.userData, 'wallpaper-engine'),
        vendorDir: path.join(versionDir, 'vendor', 'dsh-wallpaper-engine'),
      });
    });
    this.host = null;
    this.manifest = null;
    this.state = { schema: 1, wallpaper: { installed: false, enabled: false, activeVersion: null, grantedPermissions: [] } };
    this.stateError = '';
    try {
      const parsed = JSON.parse(fs.readFileSync(this.statePath, 'utf8'));
      if (parsed?.schema === 1 && this._validState(parsed.wallpaper)) this.state = parsed;
      else this.stateError = '扩展状态文件版本无效，壁纸保持关闭';
    } catch (e) {
      if (e.code !== 'ENOENT') this.stateError = '扩展状态文件不可读取，壁纸保持关闭';
    }
    if (this.stateError) this.state.wallpaper.enabled = false;
  }

  async getState() {
    return { ok: true, ...this.state.wallpaper, error: this.stateError || undefined };
  }

  _validState(value) {
    const allowed = new Set(['installed', 'enabled', 'activeVersion', 'grantedPermissions']);
    if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).some(key => !allowed.has(key))
      || typeof value.installed !== 'boolean' || typeof value.enabled !== 'boolean'
      || !Array.isArray(value.grantedPermissions)
      || value.grantedPermissions.some(p => !['desktop.wallpaper.control', 'desktop.ui.contribute'].includes(p))) return false;
    if (value.activeVersion !== null) {
      try { semverTuple(value.activeVersion); } catch (_) { return false; }
    }
    return value.installed === (value.activeVersion !== null) && (!value.enabled || value.installed);
  }

  _versionDir(version) { return path.join(this.programs, EXTENSION_ID, version); }
  _assertManifest(manifest) {
    if (!validateManifest(manifest)) {
      const detail = (validateManifest.errors || []).map(e => e.instancePath + ' ' + e.message).join('; ');
      throw new Error('扩展清单无效：' + detail.slice(0, 300));
    }
    if (manifest.id !== EXTENSION_ID || manifest.kind !== 'desktop-extension') throw new Error('扩展类型或 ID 与 Wallpaper 不匹配');
    if (!semverGte(this.coreVersion, manifest.min_core)) throw new Error('此扩展需要更新版本的 Vigil');
    if (manifest.protocol.name !== PROTOCOL.name || manifest.protocol.major !== PROTOCOL.major
      || PROTOCOL.minor < manifest.protocol.min_minor) throw new Error('扩展通信协议版本不兼容');
    if (!manifest.permissions.includes('desktop.wallpaper.control') || !manifest.permissions.includes('desktop.ui.contribute')) {
      throw new Error('Wallpaper 必需权限未声明');
    }
    for (const field of [manifest.entry, manifest.renderer.script, manifest.renderer.style]) {
      if (!safeRelative(field)) throw new Error('扩展入口路径无效');
    }
    const paths = manifest.files.map(x => x.path);
    if (new Set(paths).size !== paths.length || paths.some(p => !safeRelative(p))) throw new Error('扩展文件清单包含重复或无效路径');
    for (const field of [manifest.entry, manifest.renderer.script, manifest.renderer.style]) {
      if (!paths.includes(field)) throw new Error('入口资源未包含在完整性清单中');
    }
  }

  async _listFiles(root, dir = root, output = []) {
    const entries = await fs.promises.readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (dir === root && entry.name === 'manifest.json') continue;
      const absolute = path.join(dir, entry.name);
      const stat = await fs.promises.lstat(absolute);
      if (stat.isSymbolicLink()) throw new Error('扩展包不允许符号链接');
      if (stat.isDirectory()) await this._listFiles(root, absolute, output);
      else if (stat.isFile()) output.push(path.relative(root, absolute).split(path.sep).join('/'));
      else throw new Error('扩展包包含不支持的文件类型');
    }
    return output;
  }

  async _verifyDirectory(source, manifest) {
    this._assertManifest(manifest);
    const listed = manifest.files.map(f => f.path).sort();
    const actual = (await this._listFiles(source)).sort();
    if (JSON.stringify(listed) !== JSON.stringify(actual)) throw new Error('扩展文件与 Manifest 清单不一致');
    for (const item of manifest.files) {
      const actualHash = await sha256(path.join(source, ...item.path.split('/')));
      if (actualHash !== item.sha256) throw new Error('扩展文件校验失败：' + item.path);
    }
  }

  async _readPackage(source) {
    const manifest = JSON.parse(await fs.promises.readFile(path.join(source, 'manifest.json'), 'utf8'));
    await this._verifyDirectory(source, manifest);
    return manifest;
  }

  async installFromDirectory(source, { grantedPermissions = [] } = {}) {
    const absSource = path.resolve(source);
    const manifest = await this._readPackage(absSource);
    if (!Array.isArray(grantedPermissions) || manifest.permissions.some(p => !grantedPermissions.includes(p))) {
      throw new Error('扩展权限尚未由用户授予');
    }
    const versionDir = this._versionDir(manifest.version);
    await fs.promises.mkdir(path.dirname(versionDir), { recursive: true });
    const staging = path.join(this.root, '.staging-' + crypto.randomBytes(8).toString('hex'));
    try {
      await fs.promises.cp(absSource, staging, { recursive: true, errorOnExist: true, force: false, dereference: false });
      await this._readPackage(staging);
      try { await fs.promises.rename(staging, versionDir); }
      catch (e) {
        if (e.code !== 'EEXIST' && e.code !== 'EPERM') throw e;
        const installed = await this._readPackage(versionDir);
        if (installed.version !== manifest.version || JSON.stringify(installed.files) !== JSON.stringify(manifest.files)) throw new Error('该版本目录已存在且内容不同');
        await fs.promises.rm(staging, { recursive: true, force: true });
      }
    } catch (e) {
      await fs.promises.rm(staging, { recursive: true, force: true }).catch(() => {});
      throw e;
    }

    const previous = { ...this.state.wallpaper };
    if (!previous.installed || !previous.enabled) {
      this.state.wallpaper = { installed: true, enabled: previous.enabled, activeVersion: manifest.version, grantedPermissions: [...grantedPermissions] };
      await this._saveState();
      return this.getState();
    }
    // Upgrade an enabled extension by staging and health-checking a parallel host first.
    const candidate = await this._healthyHost(versionDir, manifest);
    const oldHost = this.host;
    this.host = candidate; this.manifest = manifest;
    this.state.wallpaper = { installed: true, enabled: true, activeVersion: manifest.version, grantedPermissions: [...grantedPermissions] };
    try { await this._saveState(); }
    catch (e) {
      this.host = oldHost; this.state.wallpaper = previous;
      candidate.close();
      throw e;
    }
    if (oldHost) oldHost.close();
    return this.getState();
  }

  async _healthyHost(versionDir, manifest) {
    const host = this.hostFactory(versionDir, manifest);
    try {
      await host.request('GET', '/settings');
      return host;
    } catch (e) {
      host.close();
      throw new Error('Wallpaper 启动健康检查失败：' + String(e.message || e).slice(0, 160));
    }
  }

  async enable() {
    if (!this.state.wallpaper.installed || !this.state.wallpaper.activeVersion) throw new Error('请先安装 Wallpaper 扩展');
    if (this.state.wallpaper.enabled && this.host) return this.getState();
    const dir = this._versionDir(this.state.wallpaper.activeVersion);
    const manifest = await this._readPackage(dir);
    if (manifest.permissions.some(p => !this.state.wallpaper.grantedPermissions.includes(p))) throw new Error('扩展权限未授予，请重新安装并确认权限');
    const host = await this._healthyHost(dir, manifest);
    this.host = host; this.manifest = manifest;
    this.state.wallpaper.enabled = true;
    try { await this._saveState(); }
    catch (e) { this.state.wallpaper.enabled = false; host.close(); this.host = null; throw e; }
    return this.getState();
  }

  async disable() {
    if (this.host) this.host.close();
    this.host = null; this.manifest = null;
    this.state.wallpaper.enabled = false;
    await this._saveState();
    return this.getState();
  }

  async uninstall() {
    await this.disable();
    this.state.wallpaper.installed = false;
    this.state.wallpaper.activeVersion = null;
    this.state.wallpaper.grantedPermissions = [];
    // Keep version directories for rollback/reinstall. User assets are in userData/wallpaper-engine.
    await this._saveState();
    return this.getState();
  }

  async rendererAssets() {
    if (!this.state.wallpaper.enabled || !this.manifest) throw new Error('Wallpaper 扩展未启用');
    const root = this._versionDir(this.manifest.version);
    await this._verifyDirectory(root, this.manifest);
    const [script, style] = await Promise.all([
      fs.promises.readFile(path.join(root, ...this.manifest.renderer.script.split('/')), 'utf8'),
      fs.promises.readFile(path.join(root, ...this.manifest.renderer.style.split('/')), 'utf8'),
    ]);
    return { ok: true, script, style, version: this.manifest.version };
  }

  async request(method, resource, body) {
    if (!this.state.wallpaper.enabled || !this.host) throw new Error('Wallpaper 扩展未启用');
    if (!this.manifest.permissions.includes('desktop.wallpaper.control')) throw new Error('Wallpaper 权限未授予');
    return this.host.request(method, resource, body);
  }
  async importFile(file) {
    if (!this.state.wallpaper.enabled || !this.host) throw new Error('Wallpaper 扩展未启用');
    if (!this.manifest.permissions.includes('desktop.wallpaper.control')) throw new Error('Wallpaper 权限未授予');
    return this.host.importFile(file);
  }

  async restore() {
    if (!this.state.wallpaper.enabled) return this.getState();
    try { return await this.enable(); }
    catch (e) {
      this.state.wallpaper.enabled = false;
      this.stateError = 'Wallpaper 恢复失败，扩展已停用：' + String(e.message || e).slice(0, 160);
      await this._saveState();
      return this.getState();
    }
  }

  async _saveState() {
    await fs.promises.mkdir(this.root, { recursive: true });
    const temp = this.statePath + '.tmp';
    await fs.promises.writeFile(temp, JSON.stringify(this.state, null, 2), { mode: 0o600 });
    await fs.promises.rename(temp, this.statePath);
  }
  close() { if (this.host) this.host.close(); this.host = null; }
}

module.exports = { WallpaperExtensionManager, semverGte, safeRelative, EXTENSION_ID };
