// -*- coding: utf-8 -*-
/**
 * 开发用的假桥：没有 brain 也能把界面点开看。
 *
 * 用法：把 index.html 另存成 _harness.html（或直接改一下 script 引用），
 * 在 js/ui.js **之前**加一行 `<script src="js/_devstub.js"></script>`，
 * 然后 `#kb`、`#ability` 这样带 hash 打开，它会自动切到那一页。
 *
 * 里面的返回结构照抄真实接口（见 00-客户端开发方案 里那张接口表），
 * 所以界面对不上时，要么是这里过时了，要么是真接口变了 —— 两种都值得看一眼。
 */
(function () {
  'use strict';
  const J = (json, status) => Promise.resolve({ ok: true, status: status || 200, json: json });
  const now = Math.floor(Date.now() / 1000);
  const PREVIEW_SESSIONS = [];
  const PREVIEW_HISTORY = new Map();
  const PREVIEW_MARKET = [
    { name: 'weather', desc: '查天气', author: 'Vigil', version: '1.2.0', market_version: '1.2.0',
      installed: true, enabled: true, update_state: 'current', runtime_state: 'loaded', tags: ['工具'] },
    { name: 'memo', desc: '记账', author: 'Vigil', version: '0.9.0', market_version: '1.0.0',
      installed: true, enabled: false, update_state: 'update_available', runtime_state: 'disabled', tags: ['数据'] },
    { name: 'translate', desc: '翻译', author: 'Vigil', version: '2.0.0', market_version: '2.0.0',
      installed: false, enabled: false, update_state: 'not_installed', runtime_state: 'not_loaded', tags: ['工具'] },
  ];

  const STATS = {
    turns: 128, tok_in: 31200, tok_out: 10900, fallback: 2, avg_ms: 2900,
    mem_hit_turns: 79, mem_hits: 213, model: 'mimo-v2.6-pro', voice: '', name: '林知夏',
    agenda: { total: 12, pending: 2, done: 9, missed: 1 }, last_err: '',
  };

  const HISTORY = [
    { role: 'her', text: '我在阳台晒被子呢，你那边忙完了没？', t: now - 900 },
    { role: 'me', text: '今天想吃什么？', t: now - 800 },
    { role: 'her', text: '刚看了下天气，下午有雨哦，出门记得带伞～', t: now - 700 },
  ];

  const ROUTES = [
    [/^\/api\/stats/, () => J(STATS)],
    [/^\/api\/history/, () => J({ items: HISTORY })],
    [/^\/api\/chat$/, (b) => J({ reply: '（假数据）收到：' + (b && b.text || '') })],
    [/^\/api\/persona\/full/, () => J({ fields: {
      name: '林知夏', nicknames: ['夏夏'], relation: '女朋友',
      personality: '说话轻快，爱用颜文字，会主动关心他在做什么。', notes: '（人设正文）' } })],
    [/^\/api\/persona/, () => J({ name: '林知夏', key: 'default', current: 'default', used: 3, max: 5,
      templates: [
        { key: 'default', name: '林知夏', desc: '元气少女', current: true },
        { key: 'maimai', name: '麦麦', desc: '说话简短，会开玩笑' },
        { key: 'yui', name: '结衣', desc: '温柔学姐' },
      ] })],
    [/^\/api\/reload_persona/, () => J({ ok: true })],
    [/^\/api\/features/, () => J({ features: {
      hotboard: true, weather: true, weather_alert: false, package: true,
      meme: true, moment_image: false, tools: true } })],
    [/^\/api\/plugins/, () => J({ manage: { channels: ['pc'], items: [
      { name: 'weather', desc: '查天气', err: '', channels: ['app'], tools: ['weather'],
        meta: { version: '1.2', author: 'me', installed_at: '2026-10-02' } },
      { name: 'memo', desc: '记账', err: '', channels: ['pc'], tools: ['memo_add', 'memo_list'],
        meta: { version: '0.9' } },
      { name: 'translate', desc: '翻译', err: 'ImportError: no module named x', channels: [], tools: [] },
    ] } })],
    [/^\/api\/skill\/status/, () => J({ alive: true, host: '127.0.0.1:8790', node: 'v20.11.1',
      dir: 'brain/skill_host/skills', ext: ['js'], channels: ['pc'],
      skills: [ { id: 'qq-music', name: 'qq-music', version: '1.0.0', tools: [{ name: 'search_song' }, { name: 'play' }], channels: ['app'] } ] })],
    [/^\/api\/diary\/list/, () => J({ days: ['2026-10-06', '2026-10-05', '2026-10-04'] })],
    [/^\/api\/diary\/read/, () => J({ body: '下雨前抢着把花搬进来了，有一盆的叶子被风吹折了，有点心疼。' })],
    [/^\/api\/moments/, () => J({ items: [ { id: 1, text: '雨天的窗台最好看了', time: now - 3600,
      likes: ['我'], comments: [{ who: '我', text: '这张好看' }], imgs: [] } ] })],
    [/^\/api\/packages/, () => J({ items: [ { number: 'SF1234567890', carrier: '顺丰速运', status: '运输中',
      note: '耳机', latest: '已到达杭州转运中心' } ] })],
    [/^\/api\/models/, () => J({ current: { provider: 'xiaomi', provider_name: '小米 MiMo',
      api_base: 'https://api.xiaomimimo.com/v1', api_key_set: true, chat: 'mimo-v2.6-pro', image: '', audio: '' },
      providers: [ { key: 'xiaomi', name: '小米 MiMo' }, { key: 'siliconflow', name: '硅基流动' }, { key: 'custom', name: '自定义' } ],
      groups: { chat: ['mimo-v2.6-pro', 'mimo-v2.6'], image: [], audio: [] },
      source: 'online', count: 12 })],
    [/^\/api\/models\/apply/, () => J({ ok: true })],
    [/^\/api\/trace/, (b, url) => {
      // 造三天的量：够「按天」出 3 个点、「按轮次」出一条完整的折线。
      // 幅度公式写死（不是随机），这样 harness 截图每次都一样，好对照。
      const last = Number((url || '').match(/last=(\d+)/) ? RegExp.$1 : 60);
      const srcs = ['pc'];
      const items = [];
      const days = [3, 2, 1, 0];                       // 往前数几天
      const counts = [14, 21, 26, 18];                 // 每天的轮数
      days.forEach((d, di) => {
        const day0 = new Date(); day0.setHours(0, 0, 0, 0);
        const base = Math.floor(+day0 / 1000) - d * 86400;
        const n = counts[di];
        for (let i = 0; i < n && items.length < last; i++) {
          const t = base + 8 * 3600 + Math.floor(i * (14 * 3600) / n);
          items.push({
            t,
            src: srcs[(di + i) % 3],
            in_len: 40 + (i * 37) % 320,
            tok_in: 900 + i * 42 + (di * 130) + (i % 5) * 180,
            tok_out: 140 + (i * 61) % 700 + (i % 3) * 90,
            ms: 1600 + (i * 313) % 3400,
            mem_hits: i % 4,
            model: 'mimo-v2.6-pro',
          });
        }
      });
      return J({ items });
    }],
    [/^\/api\/logs/, () => J({ items: [
      { t: now - 30, level: 'INFO', msg: 'turn start · persona=default · mem=3' },
      { t: now - 25, level: 'WARN', msg: 'stream 重试 1 次（连接超时）' },
      { t: now - 20, level: 'INFO', msg: '图片输入交给当前对话模型处理' } ] })],
    [/^\/api\/plugin\/remote\/tools/, () => J({ ok: true, items: [
      { name: 'local_system_info', desc: '看这台电脑的基本情况：系统版本、CPU、内存、磁盘剩余', danger: false, group: 'system', params: [] },
      { name: 'local_file_list', desc: '列出一个目录里有什么', danger: false, group: 'files', params: ['path'] },
      { name: 'local_file_read', desc: '读一个文本文件的内容（只读）', danger: false, group: 'files', params: ['path'] },
      { name: 'local_file_find', desc: '按文件名找文件', danger: false, group: 'files', params: ['name'] },
      { name: 'open_url', desc: '用默认浏览器打开网址', danger: false, group: 'net', params: ['url'] },
      { name: 'local_screenshot', desc: '截一张当前屏幕的图', danger: false, group: 'system', params: [] },
      { name: 'local_file_write', desc: '往文件里写内容（会改动他的文件）', danger: true, group: 'files', params: ['path', 'text'] },
      { name: 'local_file_delete', desc: '删文件或目录（进回收站）', danger: true, group: 'files', params: ['path'] },
      { name: 'local_exec', desc: '执行一条命令行（只允许白名单里的只读命令）', danger: true, group: 'system', params: ['cmd'] },
    ] })],
    [/^\/api\/plugin\/kb\/stats/, () => J({ ok: true, enabled: true, inject: true, scope: 'default',
      dir: 'F:\\Vigil\\brain\\data\\kb\\default', count: 486, docs: 12, dim: 1024, model: 'BAAI/bge-m3',
      backend: 'api', top_k: 3, min_score: 0.45, updated_at: '2026-10-06 20:14',
      config: { chunk_size: 800, chunk_overlap: 80, backend: 'api', model: 'BAAI/bge-m3', inject: true, top_k: 3, min_score: 0.45 } })],
    [/^\/api\/plugin\/kb\/docs/, () => J({ items: [
      { name: '部署说明.md', chunks: 42, size: 18432, added_at: '2026-10-06 20:12' },
      { name: '接口手册.pdf', chunks: 186, size: 2202009, added_at: '2026-10-06 20:14' } ] })],
    [/^\/api\/plugin\/kb\/search/, () => J({ items: [
      { score: 0.595, source: '部署说明.md', chunk: 1, heading: '反向代理', text: 'Nginx 里把 8788 转发到后端，WebSocket 走 upgrade 头。' },
      { score: 0.36, source: '接口手册.pdf', chunk: 42, heading: '', text: '连接建立后先发 token，服务端校验失败直接断连。' } ] })],
  ];

  window.vigil = {
    // 夹具不拉起 EgoAgent 进程：预览里问答服务就是不可用，不假装能用。
    ego: {
      ensure: () => Promise.resolve({ ok: false, error: '预览环境不启动分析服务' }),
      status: () => Promise.resolve({ ok: false }),
    },
    // 预览里没有主进程，弹不出系统对话框，也不该假装选了文件。
    video: {
      pick: () => Promise.resolve({ ok: false, err: '预览环境不能选本地文件，用桌面客户端可以选任意视频' }),
      share: () => Promise.resolve({ ok: false, err: '预览环境不能读本地文件' }),
    },
    api: (method, path, body) => {
      const url = new URL(path, 'http://preview');
      if (url.pathname === '/api/conversations' && method === 'GET') return J({ok:true,items:PREVIEW_SESSIONS});
      if (url.pathname === '/api/conversations' && method === 'POST') {
        const item = {id:crypto.randomUUID(),title:'新对话',created_at:Date.now()/1000,updated_at:Date.now()/1000,legacy:false,has_content:true};
        PREVIEW_SESSIONS.unshift(item); PREVIEW_HISTORY.set(item.id,[]); return J({ok:true,item},201);
      }
      if (url.pathname.startsWith('/api/conversations/') && method === 'POST') {
        const bits = url.pathname.split('/').filter(Boolean), id = bits[2];
        if (bits[3] === 'delete') { const i=PREVIEW_SESSIONS.findIndex(x=>x.id===id); if(i>=0) PREVIEW_SESSIONS.splice(i,1); PREVIEW_HISTORY.delete(id); return J({ok:true}); }
        const item=PREVIEW_SESSIONS.find(x=>x.id===id); if(item){item.title=body.title;return J({ok:true,item});}
      }
      if (url.pathname === '/api/history' && method === 'GET') return J({ok:true,items:PREVIEW_HISTORY.get(url.searchParams.get('conversation_id'))||[],display_turns:[]});
      if (url.pathname === '/api/creator/jobs' && method === 'GET') return J({ok:true,items:[]});
      if (url.pathname === '/api/chat' && method === 'POST') {
        const id=body.conversation_id, history=PREVIEW_HISTORY.get(id)||[];
        history.push({role:'user',text:body.text||'',t:new Date().toISOString()},{role:'assistant',text:'（网页验收模拟）已收到：'+(body.text||''),t:new Date().toISOString()});
        PREVIEW_HISTORY.set(id,history); const item=PREVIEW_SESSIONS.find(x=>x.id===id); if(item){item.has_content=true;item.title=(body.text||'新对话').slice(0,48);item.updated_at=Date.now()/1000;}
        return J({ok:true,reply:'（网页验收模拟）已收到：'+(body.text||'')});
      }
      if (url.pathname === '/api/market/list' && method === 'GET') {
        return J({ ok: true, state: 'ready', state_message: '插件市场已就绪', source: 'preview',
          market_version: 'preview', updated: '2026-10-23', installed_count: PREVIEW_MARKET.filter(x => x.installed).length,
          plugins: PREVIEW_MARKET });
      }
      if (url.pathname === '/api/market/refresh' && method === 'POST') return J({ ok: true, state: 'ready' });
      if (url.pathname === '/api/market/lifecycle' && method === 'POST') {
        const item = PREVIEW_MARKET.find(x => x.name === (body && body.name));
        if (!item) return J({ ok: false, err: '插件不存在' }, 404);
        const action = body.action;
        if (action === 'enable') { item.enabled = true; item.runtime_state = 'loaded'; }
        if (action === 'disable') { item.enabled = false; item.runtime_state = 'disabled'; }
        if (action === 'reload') item.runtime_state = 'loaded';
        return J({ ok: true, name: item.name, state: item.runtime_state, enabled: item.enabled });
      }
      if (url.pathname === '/api/market/install' && method === 'POST') {
        const item = PREVIEW_MARKET.find(x => x.name === (body && body.name));
        if (!item) return J({ ok: false, err: '市场里找不到插件' }, 404);
        item.installed = true; item.enabled = true; item.runtime_state = 'loaded'; item.update_state = 'current'; item.version = item.market_version;
        return J({ ok: true, name: item.name, state: 'installed', msg: '插件已安装' });
      }
      if (url.pathname === '/api/market/update' && method === 'POST') {
        const item = PREVIEW_MARKET.find(x => x.name === (body && body.name));
        if (!item || !item.installed) return J({ ok: false, err: '插件尚未安装' }, 404);
        item.version = item.market_version; item.update_state = 'current'; item.runtime_state = 'loaded';
        return J({ ok: true, name: item.name, state: 'updated', stage: 'loaded' });
      }
      if (url.pathname === '/api/market/uninstall' && method === 'POST') {
        const item = PREVIEW_MARKET.find(x => x.name === (body && body.name));
        if (!item || !item.installed) return J({ ok: false, err: '插件不存在' }, 404);
        item.installed = false; item.enabled = false; item.runtime_state = 'not_loaded'; item.update_state = 'not_installed';
        return J({ ok: true, name: item.name, state: 'uninstalled', user_data: 'preserved' });
      }
      for (const [re, fn] of ROUTES) {
        if (re.test(path)) return fn(body, path);
      }
      return Promise.resolve({ ok: false, status: 404, err: '假桥没这个路由：' + path });
    },
    cfg: { get: () => Promise.resolve({ base: 'http://127.0.0.1:41234', src: 'pc' }),
           set: (p) => Promise.resolve({ base: 'http://127.0.0.1:41234', src: 'pc', ...p }) },
    token: { has: () => Promise.resolve('本地 Brain'),
             test: () => Promise.resolve({ ok: true, status: 200, name: '林知夏' }) },
    // 假 sidecar：预览里也能看到"本地大脑"卡片（url 带 nofind 就演失败态）
    sidecar: {
      status: () => Promise.resolve(/nofind/.test(location.hash)
        ? { status: 'failed', restarts: 3, port: 0, logTail: ['[sidecar] 随包 Brain 不存在'],
            diagnosis: '随包 Brain 不存在：…\resources\vigil-brain（安装不完整，请重新安装 Vigil）' }
        : { status: 'ready', restarts: 0, port: 41234, logTail: ['VIGIL_BRAIN_READY port=41234'], diagnosis: '' }),
      restart: () => Promise.resolve({ ok: true }),
      onState: () => {},
    },
    local: {
      defaults: () => Promise.resolve({
        roots: ['C:\\Users\\limingyi', 'C:\\Users\\limingyi\\Desktop', 'C:\\Users\\limingyi\\Downloads', 'C:\\Users\\limingyi\\Documents'],
        commands: ['ipconfig', 'systeminfo', 'tasklist', 'ver', 'whoami', 'hostname', 'dir', 'type', 'netstat', 'ping', 'git status', 'node -v'],
        danger: ['local_file_write', 'local_file_delete', 'local_exec'],
      }),
      caps: () => Promise.resolve(['local_system_info', 'local_file_list', 'local_file_read', 'local_file_find', 'open_url', 'local_screenshot']),
      tools: () => Promise.resolve({
        groups: { system: '系统', files: '文件', clip: '剪贴板', net: '网络', do: '会改动东西' },
        items: [
          { name: 'local_system_info', group: 'system', danger: false, enabled: true, params: [], desc: '看这台电脑的基本情况：系统版本、CPU、内存、磁盘剩余、当前时间' },
          { name: 'local_process_list', group: 'system', danger: false, enabled: true, desc: '列出正在运行的进程（按内存排序）', params: [{ k: 'filter', label: '只要名字含（可留空）', ph: 'chrome', req: false }] },
          { name: 'local_active_window', group: 'system', danger: false, enabled: true, desc: '看现在最前面的是哪个窗口（标题）', params: [] },
          { name: 'local_file_list', group: 'files', danger: false, enabled: true, desc: '列出一个目录里有什么（名字、大小、修改日期）', params: [{ k: 'path', label: '目录路径', ph: '~/Desktop', req: true }] },
          { name: 'local_file_recent', group: 'files', danger: false, enabled: true, desc: '列出最近改动过的文件', params: [{ k: 'hours', label: '最近多少小时', ph: '24', req: false }] },
          { name: 'local_clipboard_read', group: 'clip', danger: false, enabled: true, desc: '读剪贴板里的文字', params: [] },
          { name: 'local_screenshot', group: 'net', danger: false, enabled: true, desc: '截一张当前屏幕的图，存在本地（不进对话）', params: [] },
          { name: 'local_file_delete', group: 'do', danger: true, enabled: false, desc: '把文件/目录移到回收站（先备份，可撤销）', params: [{ k: 'path', label: '要删的路径', ph: '', req: true }] },
          { name: 'local_process_kill', group: 'do', danger: true, enabled: false, desc: '结束一个进程（按 PID）', params: [{ k: 'name_or_pid', label: '进程 PID 或名字', ph: '12345', req: true }] },
        ],
      }),
      run: (name) => Promise.resolve({ ok: true, text: '（假数据）' + name + ' 的执行结果\n第二行', backup: null }),
      log: () => Promise.resolve([
        { id: 'op_1', t: '2026-10-07 04:50:12', tool: 'local_file_list', args: { path: '~/Desktop' }, source: 'me', ok: true, text: '桌面里有 12 项', backup: null, undone: false },
        { id: 'op_2', t: '2026-10-07 04:48:03', tool: 'local_file_write', args: { path: '~/Desktop/note.txt' }, source: 'her', ok: true, text: '已写入（原文件已备份，可撤销）', backup: { file: 'C:/x/bak/note.txt', target: 'C:/Users/x/Desktop/note.txt' }, undone: false },
        { id: 'op_3', t: '2026-10-07 04:41:55', tool: 'local_exec', args: { cmd: 'ipconfig' }, source: 'her', ok: true, text: 'Windows IP 配置…', backup: null, undone: false },
      ]),
      undo: () => Promise.resolve({ ok: true, text: '已还原：C:/Users/x/Desktop/note.txt' }),
      reveal: () => Promise.resolve({ ok: true }),
      onConfirm: () => {}, answer: () => {}, onStep: () => {}, onDone: () => {},
    },
    win: { minimize() {}, maximize() {}, close() {}, onState() {} },
    // 拉起本地 Brain。URL 里带 nofind 就演一次失败路径，用来核对设置页的
    // 诊断文案。（真实现见 electron/main.js 的 autoConnect）
    autoConnect: () => {
      if (/nofind/.test(location.hash)) {
        return Promise.resolve({ ok: false, err: '随包 Brain 不存在（安装不完整，请重新安装 Vigil）' });
      }
      return Promise.resolve({ ok: true, base: 'http://127.0.0.1:41234', kind: 'local' });
    },
    openExternal: () => Promise.resolve(),
    platform: 'win32',
  };

  // 带 hash 打开时自动切页：#ability，或 #settings/device 这样带二级
  window.addEventListener('load', () => {
    const raw = (location.hash || '').replace('#', '');
    if (!raw) return;
    const [id, sub] = raw.split('/');
    const id2 = sub ? id : raw;
    const sub2 = sub || '';
    setTimeout(() => {
      const b = document.querySelector('.rbtn[data-page="' + id2 + '"]');
      if (!b) return;
      b.click();
      if (sub2) setTimeout(() => {
        const it = document.querySelector('.panel [data-k="' + sub2 + '"]');
        if (it) it.click();
      }, 250);
    }, 200);
  });
})();
