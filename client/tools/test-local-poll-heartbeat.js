// Exercise the production pollOnce/pollBrain bodies with a mocked authenticated
// transport. No Electron window, real client, or backend is started.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, '..', 'electron', 'main.js'), 'utf8');
function between(startNeedle, endNeedle) {
  const start = source.indexOf(startNeedle);
  const end = source.indexOf(endNeedle, start + startNeedle.length);
  assert.notEqual(start, -1, `missing production function ${startNeedle}`);
  assert.notEqual(end, -1, `missing production boundary ${endNeedle}`);
  return source.slice(start, end);
}

const productionPollFunctions = [
  between('async function pollBrain(', 'async function pollOnce()'),
  between('async function pollOnce()', 'function startPolling()'),
].join('\n');

async function runPoll(caps, localAuto = false) {
  const requests = [];
  const cfg = { device_id: 'device-fixture', localAuto };
  const sandbox = {
    pollBusy: false,
    asking: null,
    readCfg: () => cfg,
    connectionBase: () => 'https://brain.fixture',
    localtool: { capabilities: () => caps },
    getToken: async () => 'fixture-auth-token',
    request: async (...args) => {
      requests.push(args);
      return { json: { call: null } };
    },
    encodeURIComponent,
    setTimeout,
    console,
  };
  vm.runInNewContext(
    `${productionPollFunctions}\nglobalThis.__pollOnce = pollOnce;`, sandbox,
    { filename: 'client/electron/main.js' });
  await sandbox.__pollOnce();
  return { requests, sandbox };
}

async function main() {
  const empty = await runPoll([]);
  assert.equal(empty.requests.length, 1, 'all-disabled client still sends a heartbeat');
  assert.deepEqual(empty.requests[0], [
    'GET',
    'https://brain.fixture/api/plugin/remote/poll?caps=&device=device-fixture&approval_mode=confirm_risky',
    null,
    'fixture-auth-token',
    8000,
  ]);
  assert.equal(empty.sandbox.pollBusy, false, 'poll lock is released after heartbeat');
  console.log('PASS empty capabilities send an authenticated status heartbeat');

  const partial = await runPoll(['local_file_list', 'local_file_read'], true);
  assert.equal(partial.requests.length, 1);
  assert.equal(partial.requests[0][0], 'GET');
  assert.equal(partial.requests[0][1],
    'https://brain.fixture/api/plugin/remote/poll?caps=local_file_list%2Clocal_file_read&device=device-fixture&approval_mode=auto');
  assert.equal(partial.requests[0][3], 'fixture-auth-token');
  assert.equal(partial.sandbox.pollBusy, false);
  console.log('PASS partial capability polling preserves advertised tools and auth');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
