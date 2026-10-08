// -*- coding: utf-8 -*-
/**
 * M1 验收 e2e：全新 sidecar 家（config 里没有 api_key）→ 客户端应自动把人
 * 领到"模型与服务商"向导 → 填 key 热加载 → 真实对话出回复。
 *
 * 这就是待办清单 1.5 的那条："关掉云端一切，客户端填 key 即聊"。
 *
 * 隔离：
 * - sidecar 家整体拷到临时目录（exe + _internal + 空 config），真实 dist 不动
 * - 临时 config = 真实 config 去掉 api_key / api_keys / embed_key
 *   （人设、渠道映射都留着 —— 她还得是她）
 * - 客户端用临时 --user-data-dir + VIGIL_BRAIN_EXE 指向临时家的 exe
 * 跑法：node tools/e2e-sidecar-wizard.js
 */
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const WebSocket = require('ws');

const ROOT = path.join(__dirname, '..', '..');
const CLIENT = path.join(__dirname, '..');
const DIST = path.join(ROOT, 'dist', 'vigil-brain');
const ELECTRON = path.join(CLIENT, 'node_modules', 'electron', 'dist', 'electron.exe');
const CDP_PORT = 9335;
const SHOT = path.join(CLIENT, '_shots', 'e2e-sidecar-wizard.png');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const get = (url) => new Promise((resolve, reject) => {
  http.get(url, (res) => { let d = ''; res.on('data', (c) => d += c); res.on('end', () => resolve(d)); })
    .on('error', reject);
});

function guiEnv(extra) {
  const e = { ...process.env, ...(extra || {}) };
  delete e.ELECTRON_RUN_AS_NODE;
  return e;
}

function cdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const waiters = new Map();
  ws.on('message', (raw) => {
    let m; try { m = JSON.parse(raw); } catch (_) { return; }
    if (waiters.has(m.id)) { waiters.get(m.id)(m); waiters.delete(m.id); }
  });
  const send = (method, params) => new Promise((res) => {
    const i = ++id;
    waiters.set(i, res);
    ws.send(JSON.stringify({ id: i, method, params: params || {} }));
    setTimeout(() => {
      if (waiters.has(i)) {
        waiters.delete(i);
        res({ result: { result: { value: undefined, description: 'CDP 超时：' + method } } });
      }
    }, 30000);
  });
  return new Promise((res, rej) => {
    ws.on('open', () => res({ send, close: () => ws.close() }));
    ws.on('error', rej);
  });
}

(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil_wizard_e2e_'));
  const results = [];
  const ok = (n, pass, extra) => {
    results.push({ n, pass });
    console.log((pass ? '  OK   ' : '  FAIL ') + n + (extra ? '   ' + extra : ''));
  };
  let app = null, c = null;
  try {
    // ── 0. 布置"没填过 key"的 sidecar 家 ──
    console.log('\n[0] 布置全新 sidecar 家');
    const home = path.join(tmp, 'sidecar-home');
    fs.mkdirSync(home, { recursive: true });
    fs.copyFileSync(path.join(DIST, 'vigil-brain.exe'), path.join(home, 'vigil-brain.exe'));
    fs.cpSync(path.join(DIST, '_internal'), path.join(home, '_internal'), { recursive: true });
    // config：真实配置挖掉所有 key，人设与世界设定保留
    const cfg = JSON.parse(fs.readFileSync(path.join(ROOT, 'brain', 'config', 'config.json'), 'utf8'));
    delete cfg.api_key; delete cfg.api_keys; delete cfg.embed_key;
    const homeCfg = path.join(home, 'config');
    fs.mkdirSync(homeCfg, { recursive: true });
    fs.writeFileSync(path.join(homeCfg, 'config.json'), JSON.stringify(cfg), 'utf8');
    fs.copyFileSync(path.join(ROOT, 'brain', 'config', 'world.json'), path.join(homeCfg, 'world.json'));
    fs.cpSync(path.join(ROOT, 'brain', 'config', 'personas'), path.join(homeCfg, 'personas'), { recursive: true });
    // 向导要填的真 key 从本机真实配置里拿（只进内存，不落日志）；
    // 服务商也跟着配置走 —— key 和接口是配对的，选错家就是 401
    const realCfg = JSON.parse(
      fs.readFileSync(path.join(ROOT, 'brain', 'config', 'config.json'), 'utf8'));
    const realKey = realCfg.api_key || '';
    const realProv = realCfg.provider || '';
    ok('sidecar 家就绪（config 无 key）', !!(realKey && realProv),
       (realKey ? '' : '真实配置里没有 api_key ') + (realProv ? '' : '没有 provider'));

    console.log('\n[1] 起客户端（VIGIL_BRAIN_EXE 指向临时家）');
    const profile = path.join(tmp, 'profile');
    fs.mkdirSync(profile, { recursive: true });
    const elog = fs.openSync(path.join(tmp, 'electron.log'), 'a');
    app = spawn(ELECTRON, ['.', '--user-data-dir=' + profile,
      '--remote-debugging-port=' + CDP_PORT, '--disable-gpu', '--no-sandbox'], {
      cwd: CLIENT, stdio: ['ignore', elog, elog],
      env: guiEnv({ VIGIL_BRAIN_EXE: path.join(home, 'vigil-brain.exe') }),
    });
    let target = null;
    for (let i = 0; i < 60 && !target; i++) {
      try {
        const l = JSON.parse(await get('http://127.0.0.1:' + CDP_PORT + '/json'));
        target = l.find((t) => t.type === 'page' && /index\.html/.test(t.url || '')) || l.find((t) => t.type === 'page');
      } catch (_) { await sleep(500); }
    }
    ok('客户端窗口起来了', !!target);
    if (!target) throw new Error('连不上调试端口，看 ' + path.join(tmp, 'electron.log'));

    c = await cdp(target.webSocketDebuggerUrl);
    await c.send('Page.enable');
    const ev = async (expr) => {
      const r = await c.send('Runtime.evaluate', { expression: expr, returnByValue: true });
      return r.result && r.result.result && r.result.result.value;
    };
    const ev2 = async (expr) => {
      const r = await c.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
      return r.result && r.result.result && r.result.result.value;
    };

    console.log('\n[2] 等 sidecar 就绪（90s），再看向导有没有把人领到填 key 页');
    let sc = null;
    for (let i = 0; i < 90; i++) {
      await sleep(1000);
      sc = await ev2("(window.vigil && window.vigil.sidecar) ? window.vigil.sidecar.status() : null");
      if (sc && sc.status === 'ready' && sc.port) break;
    }
    ok('sidecar 就绪', !!sc && sc.status === 'ready' && !!sc.port,
       sc ? 'port=' + sc.port : 'null');
    if (!sc || sc.status !== 'ready') throw new Error('sidecar 没就绪');

    // 向导：maybeWizard 检测到没 key 应该把页面切到 设置→模型与服务商
    // 先逐步 Dump 条件，别让 try/catch 把问题吞了
    {
      const d0 = await ev2("(function(){try{return JSON.stringify({vigil:typeof window.vigil, keys:Object.keys(window.vigil||{}), sidecar:typeof (window.vigil||{}).sidecar});}catch(e){return 'ERR:'+e.message}})()");
      const d1 = await ev2("JSON.stringify(API.state.conn)");
      const d2 = await ev2("(function(){try{return window.vigil.cfg.get().then(function(r){return 'OK:'+JSON.stringify(r).slice(0,120)}).catch(function(e){return 'REJECT:'+ (e&&e.message||e)});}catch(e){return 'SYNCERR:'+e.message}})()");
      const d3 = await ev2("(async()=>{const r=await window.vigil.api('GET','/api/models');" +
        "return JSON.stringify({ok:r.ok,key_set:r.json&&r.json.current&&r.json.current.api_key_set});})()");
      const d4 = await ev2("API.state.page");
      console.log('   [诊断] bridge=' + d0);
      console.log('   [诊断] conn=' + d1);
      console.log('   [诊断] cfg=' + d2);
      console.log('   [诊断] models=' + d3);
      console.log('   [诊断] page=' + d4);
    }
    let onModel = '';
    for (let i = 0; i < 20; i++) {
      await sleep(1000);
      onModel = await ev("(document.querySelector('.topbar h1')||{}).textContent || ''");
      if (/模型与服务商/.test(onModel || '')) break;
    }
    ok('首启自动进了填 key 向导（模型与服务商）', /模型与服务商/.test(onModel || ''), 'topbar=' + onModel);

    console.log('\n[3] 填 key 并保存（热加载）');
    // 服务商下拉应该已经由 /api/models 渲染出来；选中 siliconflow，填 key，保存
    const provSet = await ev2("(function(){const s=document.getElementById('mProv');if(!s)return 'no-select';" +
      "const want=" + JSON.stringify(realProv) + ";" +
      "const has=[...s.options].some(function(o){return o.value===want});" +
      "if(has)s.value=want;s.dispatchEvent(new Event('change'));return 'ok:'+s.value;})()");
    ok('服务商下拉可用并选中 ' + realProv, new RegExp('^ok:' + realProv + '$').test(provSet || ''), String(provSet));
    await ev2("(function(){var i=document.getElementById('mKey');" +
      "if(!i)return 'no-input';i.value=" + JSON.stringify(realKey) + ";return 'ok';})()");
    await ev("document.getElementById('mSave').click()");
    let applyRes = '';
    for (let i = 0; i < 10; i++) {
      await sleep(1000);
      applyRes = await ev("(document.getElementById('mRes')||{}).textContent || ''");
      if (applyRes) break;
    }
    ok('保存并生效', /已生效/.test(applyRes || ''), applyRes);

    console.log('\n[4] 回聊天页，发一句真话（这就是"填 key 即聊"）');
    await ev("(document.querySelector('[data-page=\"chat\"]')||{click(){}}).click()");
    await sleep(1000);
    const turns0 = Number(await ev("document.querySelectorAll('.turn').length")) || 0;
    await ev2("(function(){var ta=document.getElementById('ta');" +
      "if(!ta)return 'no-input';" +
      "var set=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value');" +
      "set.set.call(ta,'打包验收：用一句话报一下你现在的状态');ta.dispatchEvent(new Event('input',{bubbles:true}));return 'ok';})()");
    await ev("document.getElementById('sendBtn').click()");
    let reply = '';
    for (let i = 0; i < 150; i++) {
      await sleep(1000);
      const turns1 = Number(await ev("document.querySelectorAll('.turn').length")) || 0;
      if (turns1 <= turns0) continue;
      const t = String(await ev("[...document.querySelectorAll('.turn')].pop().innerText.replace(/\\s+/g,' ')"));
      if (/出错了|失败/.test(t)) { reply = t; break; }
      // 等正文生成完（"正在生成…"占位消失）再收工
      if (/正在生成|思考中/.test(t)) { reply = t; continue; }
      reply = t;
      break;
    }
    ok('她回话了（真实 LLM 调用）', !!reply && !/出错了|失败|正在生成/.test(reply), reply.slice(0, 120));

    console.log('\n[5] 截图');
    const shot = await c.send('Page.captureScreenshot', { format: 'png' });
    if (shot.result && shot.result.data) {
      fs.mkdirSync(path.dirname(SHOT), { recursive: true });
      fs.writeFileSync(SHOT, Buffer.from(shot.result.data, 'base64'));
      ok('截图已保存', true, SHOT);
    } else ok('截图已保存', false, JSON.stringify(shot).slice(0, 80));
  } catch (e) {
    console.log('出错了：' + e.message);
    results.push({ n: '整体流程', pass: false });
  } finally {
    if (c) c.close();
    if (app) app.kill();
  }
  const pass = results.filter((r) => r.pass).length;
  console.log('\n结果：' + pass + ' / ' + results.length + ' 通过');
  const failed = results.filter((r) => !r.pass).map((r) => r.n);
  if (failed.length) console.log('没过的：' + failed.join('、'));
  console.log('临时目录：' + tmp);
  process.exit(pass === results.length ? 0 : 1);
})();
