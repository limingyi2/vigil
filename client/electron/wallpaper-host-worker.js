'use strict';

// This utility process is the only process that loads an installed Wallpaper
// host entry. It exposes a small request protocol to Core and never touches a
// BrowserWindow, Core preload, or renderer DOM.
const path = require('path');

const args = process.argv.slice(2);
const hostFile = path.resolve(args[0] || '');
const dataDir = path.resolve(args[1] || '');
const vendorDir = path.resolve(args[2] || '');
let host = null;
let busy = Promise.resolve();
let closing = false;

function reply(id, value, error) {
  try { process.parentPort.postMessage({ id, ok: !error, value, err: error ? String(error.message || error).slice(0, 300) : undefined }); }
  catch (_) {}
}

async function dispatch(message) {
  const id = message && message.id;
  try {
    if (!message || !Number.isSafeInteger(id) || typeof message.op !== 'string') throw new Error('Invalid Wallpaper host protocol message');
    if (message.op === 'close') {
      closing = true;
      if (host) host.close();
      host = null;
      reply(id, true);
      setImmediate(() => process.exit(0));
      return;
    }
    if (closing) throw new Error('Wallpaper host is closed');
    if (!host) {
      const resolved = path.resolve(hostFile);
      if (!resolved || !resolved.endsWith(path.sep + 'wallpaper-host.js')) throw new Error('Wallpaper host entry is invalid');
      const { createWallpaperHost } = require(resolved);
      host = createWallpaperHost({ dataDir, vendorDir });
    }
    if (message.op === 'request') {
      const method = String(message.method || '').toUpperCase();
      const resource = String(message.resource || '');
      if (!['GET', 'PUT', 'POST'].includes(method) || resource.length > 512) throw new Error('Wallpaper request rejected');
      reply(id, await host.request(method, resource, message.body));
    } else if (message.op === 'importFile') {
      const file = path.resolve(String(message.file || ''));
      if (!file || !path.isAbsolute(file)) throw new Error('Wallpaper import path is invalid');
      reply(id, await host.importFile(file));
    } else throw new Error('Unknown Wallpaper host operation');
  } catch (error) { reply(id, null, error); }
}

process.parentPort.on('message', event => {
  const message = event && event.data;
  busy = busy.then(() => dispatch(message), () => dispatch(message));
});
