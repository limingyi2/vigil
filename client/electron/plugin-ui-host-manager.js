'use strict';

const net = require('net');
const crypto = require('crypto');
const { spawn } = require('child_process');

function createPluginUiHostManager({ app, onBrokerRequest, onExit = () => {} }) {
  let server = null, socket = null, child = null, started = null, token = null, debugPort = 0;
  let input = '', nextId = 0;
  const pending = new Map();
  function rejectPending(message) {
    for (const item of pending.values()) { clearTimeout(item.timer); item.reject(new Error(message)); }
    pending.clear();
  }
  function write(message) {
    if (!socket || socket.destroyed) throw new Error('Isolated plugin host is unavailable');
    socket.write(JSON.stringify(message) + '\n');
  }
  function call(op, plugin, timeout = 12000) {
    return ensure().then(() => new Promise((resolve, reject) => {
      const id = ++nextId;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('Isolated plugin host timed out')); }, timeout);
      pending.set(id, { resolve, reject, timer });
      try { write({ type: 'command', id, op, pluginId: plugin && plugin.id, plugin }); }
      catch (error) { pending.delete(id); clearTimeout(timer); reject(error); }
    }));
  }
  function handle(message) {
    if (message.type === 'renderer-crashed') {
      console.warn('[plugin-ui-host] plugin renderer crashed; recycling isolated Electron host:', message.pluginId);
      // A renderer crash can leave Electron's helper process in an unhealthy
      // state. Retire the entire isolated Electron instance; Core stays alive
      // and a subsequent open starts a clean instance.
      rejectPending('Plugin renderer crashed; isolated host is restarting');
      socket?.destroy();
      if (child && child.exitCode == null) child.kill();
      return;
    }
    if (message.type === 'response') {
      const item = pending.get(message.id); if (!item) return;
      pending.delete(message.id); clearTimeout(item.timer);
      if (message.ok) item.resolve(message.value); else item.reject(new Error(message.error || 'Plugin host failed'));
      return;
    }
    if (message.type === 'broker') {
      Promise.resolve().then(() => onBrokerRequest(message.pluginId, message.request)).then(value => {
        write({ type: 'response', id: message.id, ok: true, value });
      }, error => {
        try { write({ type: 'response', id: message.id, ok: false, error: String(error.message || error).slice(0, 300) }); } catch (_) {}
      });
    }
  }
  let startedResolve, startedReject;
  function ensure() {
    if (socket && !socket.destroyed && child && child.exitCode == null) return started || Promise.resolve();
    if (started) return started;
    started = new Promise((resolve, reject) => { startedResolve = resolve; startedReject = reject; });
    token = crypto.randomBytes(32).toString('hex'); input = '';
    server = net.createServer(client => {
      if (socket) { client.destroy(); return; }
      socket = client; socket.setEncoding('utf8');
      let authenticated = false;
      socket.on('data', chunk => {
        input += chunk;
        if (input.length > 2 * 1024 * 1024) { socket.destroy(new Error('Plugin host frame too large')); return; }
        let pos; while ((pos = input.indexOf('\n')) >= 0) {
          const line = input.slice(0, pos); input = input.slice(pos + 1);
          try {
            const message = JSON.parse(line);
            if (!authenticated) {
              const received = Buffer.from(String(message?.token || ''));
              const expected = Buffer.from(token);
              if (message?.type !== 'hello' || received.length !== expected.length || !crypto.timingSafeEqual(received, expected)) {
                socket.destroy(new Error('Plugin host authentication failed')); return;
              }
              authenticated = true; startedResolve(); continue;
            }
            if (message?.type === 'hello') { socket.destroy(new Error('Duplicate plugin host authentication')); return; }
            handle(message);
          } catch (error) { console.error('[plugin-ui-host] invalid IPC frame:', error.message); }
        }
      });
      socket.on('close', () => {
        console.warn('[plugin-ui-host] broker socket closed');
        socket = null; rejectPending('Isolated plugin host disconnected'); started = null; debugPort = 0;
        if (child && child.exitCode == null) child.kill();
      });
      socket.on('error', error => console.warn('[plugin-ui-host] IPC error:', error.message));
    });
    server.once('error', error => { startedReject?.(error); started = null; });
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const appPath = app.getAppPath();
      const launch = async () => {
        const args = [];
        if (process.env.VIGIL_TEST_MODE === '1') {
          const probe = net.createServer();
          await new Promise((resolve, reject) => { probe.once('error', reject); probe.listen(0, '127.0.0.1', resolve); });
          debugPort = probe.address().port;
          await new Promise(resolve => probe.close(resolve));
          args.push('--remote-debugging-port=' + debugPort, '--remote-allow-origins=*');
        }
        args.push(appPath, '--vigil-plugin-ui-host', String(address.port), token);
      console.info('[plugin-ui-host] spawn:', process.execPath, args.join(' '));
      child = spawn(process.execPath, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env } });
      child.stdout.on('data', data => console.log('[plugin-ui-child]', String(data).trimEnd()));
      child.stderr.on('data', data => console.warn('[plugin-ui-child]', String(data).trimEnd()));
      child.once('error', error => { startedReject?.(error); rejectPending(error.message); onExit(error); });
      child.once('exit', (code, signal) => {
        console.warn('[plugin-ui-host] child exited:', code, signal);
        socket?.destroy(); socket = null; rejectPending('Isolated plugin host exited (' + code + ')');
        if (server) { server.close(); server = null; }
        child = null; started = null; onExit(new Error('Plugin host exited (' + code + ', ' + signal + ')'));
      });
      const timeout = setTimeout(() => { startedReject?.(new Error('Isolated plugin host startup timed out')); child?.kill(); }, 15000);
      started.finally(() => clearTimeout(timeout)).catch(() => {});
      };
      launch().catch(error => { startedReject?.(error); started = null; if (server) { server.close(); server = null; } });
    });
    return started;
  }
  return {
    open: plugin => call('open', plugin, 12000),
    close: id => (!socket || socket.destroyed || !child || child.exitCode != null)
      ? Promise.resolve({ ok: true, alreadyClosed: true })
      : call('close', { id }, 5000).catch(error => ({ ok: false, err: error.message })),
    crash: id => call('crash', { id }, 5000),
    getState: () => socket && !socket.destroyed ? call('state', null, 5000) : Promise.resolve([]),
    getDebugPort: () => debugPort,
    closeAll() {
      rejectPending('Plugin host shutting down');
      if (socket && !socket.destroyed) socket.end();
      if (child && child.exitCode == null) child.kill();
      child = null; socket = null; started = null;
      if (server) { server.close(); server = null; }
    },
  };
}

module.exports = { createPluginUiHostManager };
