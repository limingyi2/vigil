'use strict';
const fs = require('fs'), path = require('path'), crypto = require('crypto');
const REQUIRED = ['creator_jobs', 'agent_runs', 'models_saved', 'chat_stream'];
function read(file) { return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/,'')); }
function compatible(expected, actual) {
  if (!expected || !actual || actual.protocol !== 1 || expected.protocol !== actual.protocol ||
      !actual.commit || actual.commit !== expected.commit || actual.version !== expected.version ||
      actual.source_digest !== expected.source_digest || actual.candidate !== expected.candidate ||
      REQUIRED.some(c => !(actual.capabilities || []).includes(c))) {
    throw new Error('Brain 版本或接口不匹配；请修复或重装完整 Vigil（不会连接旧 Brain）');
  }
  return true;
}
function hash(file) { return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'); }
function verifyTree(root, manifest) {
  for (const [rel, digest] of Object.entries(manifest.files || {})) {
    const file = path.resolve(root, rel);
    if (!file.startsWith(path.resolve(root) + path.sep) || !fs.statSync(file).isFile() || hash(file) !== digest)
      throw new Error('Brain 文件缺失或校验失败：' + rel);
  }
  if (!manifest.files || !manifest.files['vigil-brain.exe']) throw new Error('缺少 Brain 文件清单');
}
function verifyNoBundledPluginPaths(files) {
  // Reject generic plugin source/install trees, including future plugin names.
  const offenders = (files || []).filter(file => {
    const normalized = String(file).replace(/\\/g, '/').replace(/^\/+/, '');
    const parts = normalized.split('/').filter(Boolean).map(part => part.toLowerCase());
    return parts.includes('installed_plugins') || parts.includes('plugins')
      || parts.at(-1) === 'zxplugin.json';
  });
  if (offenders.length) {
    throw new Error('Client package contains extension plugin payload: ' + offenders.slice(0, 8).join(', '));
  }
  return true;
}
function verifyNoBundledPluginManifests(entries) {
  const offenders = (entries || []).filter(entry => {
    try {
      const manifest = typeof entry.content === 'string' ? JSON.parse(entry.content) : entry.content;
      return manifest && manifest.schema === 'vigil.brain-plugin/v1';
    } catch (_) { return false; }
  }).map(entry => entry.path);
  if (offenders.length) {
    throw new Error('Client package contains extension plugin manifest: ' + offenders.slice(0, 8).join(', '));
  }
  return true;
}
module.exports = {read, compatible, hash, verifyTree, verifyNoBundledPluginPaths,
  verifyNoBundledPluginManifests, REQUIRED};
