const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('p15Host', {
  pickExe: () => ipcRenderer.invoke('poc:pick-exe'),
  start: config => ipcRenderer.invoke('poc:start', config),
  setBounds: bounds => ipcRenderer.send('poc:bounds', bounds),
  setVisible: visible => ipcRenderer.send('poc:visible', !!visible),
  action: action => ipcRenderer.send('poc:action', action),
  onAutoStart: fn => ipcRenderer.on('poc:auto-start', (_event, value) => fn(value)),
  onEvent: fn => {
    if (typeof fn !== 'function') throw new TypeError('Event handler required');
    const listener = (_event, value) => fn(value);
    ipcRenderer.on('poc:event', listener);
    return () => ipcRenderer.removeListener('poc:event', listener);
  },
});
