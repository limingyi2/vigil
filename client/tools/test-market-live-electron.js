// Real Electron renderer -> preload/IPC -> Brain market API -> live GitHub Raw.
// All app paths/config and installed-plugin reads are redirected to a temp directory.
const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const assert = require('node:assert/strict');

const client = path.resolve(__dirname, '..');
const repo = path.resolve(client, '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil-live-market-electron-'));
app.setPath('userData', path.join(tmp, 'electron-data'));
let brain, win;

async function readLine(stream, child) {
  return new Promise((resolve, reject) => {
    let text = '';
    let errorText = '';
    child.stderr.on('data', chunk => { errorText += chunk.toString(); });
    const onData = chunk => {
      text += chunk.toString();
      const at = text.indexOf('\n');
      if (at >= 0) { stream.off('data', onData); resolve(text.slice(0, at)); }
    };
    stream.on('data', onData);
    child.once('exit', code => reject(new Error('fixture Brain exited before ready: ' + code + ' ' + errorText)));
  });
}

async function main() {
  await app.whenReady();
  const brainScript = path.join(tmp, 'brain-market.py');
  fs.writeFileSync(brainScript, String.raw`
import json, os, sys, tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
root = sys.argv[1]
sys.path.insert(0, os.path.join(root, 'brain'))
sys.path.insert(0, os.path.join(root, 'brain', 'plugins'))
import paths
from plugins import plugin_market
isolated = tempfile.mkdtemp(prefix='vigil-market-brain-data-')
paths.CONFIG_DIR = isolated
plugin_market._repo_path = lambda: ''
plugin_market._plugins_dir = lambda: os.path.join(isolated, 'installed_plugins')
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get('Authorization') != 'Bearer p14-electron-fixture':
            self.send_error(401); return
        if self.path != '/api/market/list':
            self.send_error(404); return
        body = json.dumps(plugin_market.api_list({}, {}), ensure_ascii=False).encode('utf-8')
        self.send_response(200); self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *_): pass
server = HTTPServer(('127.0.0.1', 0), Handler)
print(server.server_port, flush=True)
server.serve_forever()
`, 'utf8');
  brain = spawn(process.env.PYTHON || 'python', [brainScript, repo], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let stderr = '';
  brain.stderr.on('data', chunk => { stderr += chunk.toString(); });
  const port = Number(await readLine(brain.stdout, brain));
  if (!Number.isInteger(port) || port <= 0) throw new Error('invalid fixture Brain port: ' + stderr);

  ipcMain.handle('api', (_event, request) => new Promise(resolve => {
    const req = http.request({ hostname: '127.0.0.1', port, path: request.path,
      method: request.method || 'GET', headers: { Authorization: 'Bearer p14-electron-fixture' }, timeout: 30000 }, res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => {
        let json = null;
        try { json = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch (_) {}
        resolve({ ok: res.statusCode >= 200 && res.statusCode < 300, status: res.statusCode, json });
      });
    });
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', error => resolve({ ok: false, status: 0, err: error.message }));
    req.end();
  }));

  const html = path.join(tmp, 'renderer.html');
  fs.writeFileSync(html, `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; connect-src 'none'; object-src 'none'"><pre id="result">loading</pre><script src="renderer.js"></script>`);
  fs.writeFileSync(path.join(tmp, 'renderer.js'), `window.vigil.api('GET','/api/market/list').then(r=>{document.body.dataset.done='1';document.getElementById('result').textContent=JSON.stringify(r);}).catch(e=>{document.body.dataset.done='1';document.getElementById('result').textContent=String(e);});`);
  win = new BrowserWindow({ show: false, webPreferences: { preload: path.join(client, 'electron/preload.js'),
    contextIsolation: true, sandbox: true, nodeIntegration: false } });
  await win.loadFile(html);
  const until = async expression => {
    const end = Date.now() + 45000;
    while (Date.now() < end) {
      if (await win.webContents.executeJavaScript(expression)) return;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('timeout waiting for ' + expression);
  };
  await until("document.body.dataset.done === '1'");
  const response = await win.webContents.executeJavaScript("JSON.parse(document.getElementById('result').textContent)");
  assert.equal(response.ok, true, response.err || 'Electron API request failed');
  assert.equal(response.json.state, 'ready', response.json.state_message);
  assert.equal(response.json.source, 'github');
  assert.ok(response.json.plugins.some(item => item.name === 'wallpaper'));
  console.log('PASS real Electron renderer/preload/IPC → Brain /api/market/list → GitHub Raw');
  console.log(JSON.stringify({ source: response.json.source, state: response.json.state,
    market_version: response.json.market_version, updated: response.json.updated,
    plugin_count: response.json.plugins.length }));
}

main().then(() => cleanup(0), error => { console.error(error.stack || error); cleanup(1); });
function cleanup(code) {
  if (win && !win.isDestroyed()) win.destroy();
  if (brain && brain.exitCode === null) brain.kill();
  app.exit(code);
}
