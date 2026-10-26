'use strict';
const { app } = require('electron');
const path = require('path');
if (process.env.VIGIL_USER_DATA) app.setPath('userData', path.resolve(process.env.VIGIL_USER_DATA));
// Take the lock before touching the overlay health marker. A second instance
// must not change the first instance's activation/rollback state.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
const patches = require('./local-patches').create({ baseRoot: path.join(__dirname, '..'),
  userData: app.getPath('userData') });
// Shared with local tools even when main.js comes from a versioned overlay.
global.__vigilLocalPatches = patches;
let selected;
try { selected = patches.select(); }
catch (error) {
  console.error('[local-patch]', error.message);
  selected = path.join(__dirname, '..');
}
app.setAppPath(selected);
try { require(path.join(selected, 'electron/main.js')); }
catch (error) {
  console.error('[local-patch] startup failed:', error.message);
  app.exit(1); // Next startup recovers the previous healthy version.
}
}
