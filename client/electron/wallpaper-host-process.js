'use strict';

const path = require('path');

function createWallpaperHostProcess({ versionDir, manifest, userData }) {
  const { utilityProcess } = require('electron');
  const hostFile = path.resolve(versionDir, ...manifest.entry.split('/'));
  const vendorDir = path.resolve(versionDir, 'vendor', 'dsh-wallpaper-engine');
  const child = utilityProcess.fork(path.join(__dirname, 'wallpaper-host-worker.js'),
    [hostFile, path.join(userData, 'wallpaper-engine'), vendorDir], {
      cwd: versionDir,
      serviceName: 'Vigil Wallpaper Host',
      stdio: 'pipe',
    });
  let nextId = 0;
  let closed = false;
  const pending = new Map();
  child.on('message', message => {
    const item = pending.get(message && message.id);
    if (!item) return;
    pending.delete(message.id);
    clearTimeout(item.timer);
    if (message.ok) item.resolve(message.value);
    else item.reject(new Error(message.err || 'Wallpaper host request failed'));
  });
  child.on('exit', code => {
    closed = true;
    for (const item of pending.values()) { clearTimeout(item.timer); item.reject(new Error('Wallpaper host process exited (' + code + ')')); }
    pending.clear();
  });
  function call(op, values = {}, timeout = 65000) {
    if (closed) return Promise.reject(new Error('Wallpaper host process is closed'));
    const id = ++nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('Wallpaper host request timed out')); }, timeout);
      pending.set(id, { resolve, reject, timer });
      try { child.postMessage({ id, op, ...values }); }
      catch (error) { pending.delete(id); clearTimeout(timer); reject(error); }
    });
  }
  return {
    request: (method, resource, body) => call('request', { method, resource, body }),
    importFile: file => call('importFile', { file }, 130000),
    close() {
      if (closed) return;
      call('close', {}, 2500).catch(() => {}).finally(() => { if (!closed) child.kill(); });
    },
  };
}

module.exports = { createWallpaperHostProcess };
