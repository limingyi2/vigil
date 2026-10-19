'use strict';

// Deliberately tiny bridge for an untrusted plugin renderer. Plugin identity is
// never accepted here; the main process binds each WebContents to its identity.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('vigilPlugin', Object.freeze({
  registerPage: (page) => {
    if (!page || typeof page !== 'object' || typeof page.render !== 'function') return false;
    return ipcRenderer.invoke('plugin-ui:register-page', {
      id: String(page.id || '').slice(0, 64),
      name: String(page.name || '').slice(0, 80),
      icon: String(page.icon || 'puzzle').slice(0, 32),
    });
  },
  get: (path) => ipcRenderer.invoke('plugin-ui:request', { method: 'GET', path, body: null }),
  post: (path, body) => ipcRenderer.invoke('plugin-ui:request', { method: 'POST', path, body }),
  resourceUrl: (path) => ipcRenderer.invoke('plugin-ui:request', { operation: 'resource-url', path }),
}));
contextBridge.exposeInMainWorld('vigil', Object.freeze({
  plugins: Object.freeze({ resourceUrl: (path) => ipcRenderer.invoke('plugin-ui:request', { operation: 'resource-url', path }) }),
  wallpaper: Object.freeze({
    request: (method, resource, body) => ipcRenderer.invoke('plugin-ui:request', { operation: 'wallpaper.request', method, resource, body }),
    importFile: () => ipcRenderer.invoke('plugin-ui:request', { operation: 'wallpaper.import' }),
    directory: () => ipcRenderer.invoke('plugin-ui:request', { operation: 'wallpaper.directory' }),
  }),
  openExternal: (url) => ipcRenderer.invoke('plugin-ui:request', { operation: 'open-external', url }),
}));
