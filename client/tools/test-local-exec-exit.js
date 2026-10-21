const assert = require('assert');
const Module = require('module');
const load = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === 'electron') return { shell: {}, desktopCapturer: {}, app: { getPath: () => require('os').tmpdir() } };
  return load.call(this, request, parent, isMain);
};
const { run } = require('../electron/localtool');

(async () => {
  const ok = await run('local_exec', { cmd: 'node -e "process.exit(0)"' }, { allowAllCmds: true });
  assert.match(ok.text, /\[VIGIL_EXEC_EXIT_CODE=0\]$/);
  const failed = await run('local_exec', { cmd: 'node -e "process.exit(7)"' }, { allowAllCmds: true });
  assert.match(failed.text, /\[VIGIL_EXEC_EXIT_CODE=7\]$/);
  process.stdout.write('local_exec exit status evidence passed\n');
})().catch((err) => { console.error(err); process.exitCode = 1; });
