// P16 P1-E fixture: drive the real localtool handlers with a stubbed electron.
// Reads {root, cases:[...]} from stdin, prints one JSON object with each case's
// {ok, denied, text} so the Python test can assert on real tool output.
const Module = require('module');
const path = require('path');
const os = require('os');
const _load = Module._load;
Module._load = function (request) {
  if (request === 'electron') {
    return { shell: {}, desktopCapturer: {}, app: { getPath: () => os.tmpdir() } };
  }
  return _load.apply(this, arguments);
};

const lt = require(path.join(__dirname, '..', '..', 'client', 'electron', 'localtool.js'));

const DENY = /不在允许|允许的目录|系统目录|拒绝|不能|没开/;

function readStdin() {
  return new Promise((resolve) => {
    let buf = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => { buf += chunk; });
    process.stdin.on('end', () => resolve(buf));
  });
}

(async () => {
  const input = JSON.parse(await readStdin());
  const root = input.root;
  const cfg = { roots: [root] };
  const cases = {
    list: () => lt.run('local_file_list', { path: root }, cfg),
    find: () => lt.run('local_file_find', { name: 'todo' }, cfg),
    grep: () => lt.run('local_file_grep', { path: path.join(root, 'brain'), pattern: 'failure_brief' }, cfg),
    read: () => lt.run('local_file_read', { path: path.join(root, 'notes', 'todo.md') }, cfg),
    // `..` must not walk out of the authorised root.
    escape: () => lt.run('local_file_list', { path: path.join(root, '..') }, cfg),
    // A real, existing sibling directory next to the authorised root.
    sibling: () => lt.run('local_file_list', { path: path.join(root, '..', 'p16_sibling_escape') }, cfg),
    system: () => lt.run('local_file_list', { path: process.platform === 'win32' ? 'C:/Windows' : '/etc' }, cfg),
  };
  const out = {};
  for (const name of input.cases) {
    const result = await cases[name]();
    const text = String((result && result.text) || '');
    out[name] = { text: text, denied: DENY.test(text), ok: !!text && !DENY.test(text) };
  }
  process.stdout.write(JSON.stringify(out));
})().catch((error) => {
  process.stderr.write(String((error && error.stack) || error));
  process.exit(1);
});
