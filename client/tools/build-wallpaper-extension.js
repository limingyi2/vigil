'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const client = path.resolve(__dirname, '..');
const repo = path.resolve(client, '..');
const version = require(path.join(client, 'package.json')).version;
const output = path.resolve(process.argv[2] || path.join(client, 'dist-extension', `wallpaper-${version}`));
if (fs.existsSync(output)) throw new Error(`Refusing to overwrite existing extension output: ${output}`);
fs.mkdirSync(output, { recursive: true });

function copy(source, target) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.cpSync(source, target, { recursive: true, errorOnExist: true, force: false, dereference: false });
}
copy(path.join(client, 'electron/wallpaper-host.js'), path.join(output, 'wallpaper-host.js'));
copy(path.join(client, 'src/plugins/wallpaper.js'), path.join(output, 'wallpaper.js'));
copy(path.join(client, 'src/plugins/wallpaper.css'), path.join(output, 'wallpaper.css'));
copy(path.join(client, 'vendor/dsh-wallpaper-engine'), path.join(output, 'vendor/dsh-wallpaper-engine'));

function listFiles(dir, base = dir, result = []) {
  for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
    const absolute = path.join(dir, item.name);
    const stat = fs.lstatSync(absolute);
    if (stat.isSymbolicLink()) throw new Error('Wallpaper package cannot contain symbolic links');
    if (stat.isDirectory()) listFiles(absolute, base, result);
    else if (stat.isFile()) result.push(path.relative(base, absolute).split(path.sep).join('/'));
    else throw new Error('Wallpaper package contains an unsupported file');
  }
  return result;
}

const files = listFiles(output).sort().map(file => ({
  path: file,
  sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(output, ...file.split('/')))).digest('hex'),
}));
const manifest = {
  schema: 'vigil.extension/v1',
  id: 'com.vigil.wallpaper',
  version,
  kind: 'desktop-extension',
  entry: 'wallpaper-host.js',
  renderer: { script: 'wallpaper.js', style: 'wallpaper.css' },
  min_core: version,
  protocol: { name: 'vigil-desktop-extension', major: 1, min_minor: 0 },
  artifacts: [{
    platform: 'windows-x64',
    url: 'https://example.invalid/wallpaper-extension-local-build.zip',
    sha256: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  }],
  permissions: ['desktop.wallpaper.control', 'desktop.ui.contribute'],
  files,
  data_migrations: [{
    id: 'wallpaper-data-v1',
    from: 'builtin-wallpaper-v1',
    to: 'desktop-extension-v1',
    strategy: 'copy-verify-commit',
  }],
};
fs.writeFileSync(path.join(output, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log(JSON.stringify({ output, version, files: files.length, dataDir: 'userData/wallpaper-engine' }, null, 2));
