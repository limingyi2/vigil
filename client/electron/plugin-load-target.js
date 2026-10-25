'use strict';

function findWindowForContents(sender, windows) {
  if (!sender || !Array.isArray(windows)) return null;
  return windows.find((window) => window && !window.isDestroyed() && window.webContents === sender) || null;
}

module.exports = { findWindowForContents };

