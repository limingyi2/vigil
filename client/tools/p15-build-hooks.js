'use strict';
// Legacy P15 preview configuration is still used for diagnostics. Keep its
// packaged app and extraResources subject to the same zero-plugin rule.
const fs = require('fs');
const path = require('path');
const contract = require('../electron/build-contract');

exports.afterPack = async context => {
  const resources = path.join(context.appOutDir, 'resources');
  const archive = path.join(resources, 'app.asar');
  const asar = require('@electron/asar');
  const entries = asar.listPackage(archive).map(file => file.replace(/\\/g, '/'));
  contract.verifyNoBundledPluginPaths(entries);
  contract.verifyNoBundledPluginManifests(entries.filter(file => /\/manifest\.json$/i.test(file)).map(file => ({
    path: file,
    content: asar.extractFile(archive, file.replace(/^\//, '')).toString('utf8'),
  })));

  const resourcesFiles = [];
  const walk = dir => {
    for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, item.name);
      const relative = path.relative(resources, full).replace(/\\/g, '/');
      if (item.isDirectory()) walk(full);
      else resourcesFiles.push({ path: relative, full });
    }
  };
  walk(resources);
  contract.verifyNoBundledPluginPaths(resourcesFiles.map(item => item.path));
  contract.verifyNoBundledPluginManifests(resourcesFiles
    .filter(item => /\/manifest\.json$/i.test(item.path))
    .map(item => ({ path: item.path, content: fs.readFileSync(item.full, 'utf8') })));
};
