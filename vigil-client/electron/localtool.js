// -*- coding: utf-8 -*-
/**
 * 本地工具：她要用这台电脑做事时，真正动手的地方。
 *
 * 服务端拦不了本机操作 —— 所有防线都在这个文件里，三条：
 *
 * 1. **路径白名单**：只允许在配置的目录里读写（默认 家/桌面/下载/文档）。
 *    解析绝对路径 + 展开 ~ + 规范化之后逐段比对，不是字符串 startsWith
 *    （`C:\Users\me\DesktopX` 用 startsWith 会被 `Desktop` 放过去）。
 * 2. **命令白名单**：local_exec 只放行只读命令（看状态那些），其余一律拒绝。
 *    黑名单永远漏，白名单不会。
 * 3. **危险操作要人点**：write/delete/exec 标了 danger，执行前必须弹确认。
 *    确认那一环在主进程（main.js）里，这里只负责"做"和"拒绝"。
 *
 * 原则：**失败就说清楚为什么**，不要把异常吞掉返回空字符串 ——
 * 那会让她以为"执行完了，什么都没有"，然后编一个答案。
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const { shell, desktopCapturer, app } = require('electron');

const MAX_READ = 200 * 1024;        // 单次读文件上限 200KB
const MAX_LIST = 200;               // 列目录最多返回多少条
const MAX_FIND = 50;                // 找文件最多返回多少条

// ── 路径白名单 ────────────────────────────────────────────────
function defaultRoots() {
  const out = [];
  for (const fn of ['getPath']) {
    try { out.push(app[fn]('home')); } catch (_) {}
  }
  for (const name of ['desktop', 'downloads', 'documents']) {
    try { out.push(app.getPath(name)); } catch (_) {}
  }
  return [...new Set(out.filter(Boolean))];
}

function expand(p) {
  let s = String(p || '').trim().replace(/^"|"$/g, '');
  if (!s) return '';
  if (s === '~') return os.homedir();
  if (s.startsWith('~/') || s.startsWith('~\\')) s = path.join(os.homedir(), s.slice(2));
  return path.resolve(s);
}

/** 目标在不在允许的根里。**逐段比对**，别用 startsWith。 */
function inside(target, roots) {
  const t = path.resolve(target);
  for (const r of roots) {
    const root = path.resolve(r);
    if (t === root) return root;
    const rel = path.relative(root, t);
    // relative 结果不以 .. 开头、也不是绝对路径 → 确实在 root 里面
    if (rel && !rel.startsWith('..') && !path.isAbsolute(rel)) return root;
  }
  return null;
}

/**
 * 这个路径能不能碰。
 *
 * 两种模式，由设置里的 `allowAllPaths` 决定：
 *
 * - **false（默认，保守）**：只允许 roots 里那几个目录。适合"先试试"。
 * - **true（干活的模式）**：整台机器都能读写。
 *
 * 为什么要有 true：你要的是"帮我在桌面搭个脚本、帮我整理文件"这种活，
 * 它天然要跨越很多目录（下载目录、文档、桌面、项目文件夹…）。卡在白名单里
 * 的结果是每换一个目录就被拒一次，最后什么也干不成。
 *
 * 那安全靠什么？靠**三道**，不是靠路径白名单：
 *   1. 系统目录仍然硬拦（Windows / Program Files / 引导盘根目录）
 *   2. 每个危险操作**都要你在确认框上点一下**（写、删、移动、执行命令）
 *   3. 改动都有备份，操作日志里能逐条撤销
 * 也就是说：权限放开，但"做什么"仍然由你一次次点头。
 */
const SYSTEM_DIRS = [
  'c:\\windows', 'c:\\program files', 'c:\\program files (x86)',
  'c:\\programdata', 'c:\\$recycle.bin', 'c:\\system volume information',
  'c:\\recovery', 'c:\\perflogs',
];

function isSystemPath(abs) {
  const low = path.resolve(abs).toLowerCase().replace(/\\+$/, '');
  for (const d of SYSTEM_DIRS) {
    if (low === d || low.startsWith(d + '\\')) return d;
  }
  // 整块盘符的根（C:\ 本身）也不给动
  if (/^[a-z]:\\?$/.test(low)) return low;
  return null;
}

function guard(p, cfg) {
  const abs = expand(p);
  if (!abs) return { err: '没给路径' };
  const sys = isSystemPath(abs);
  if (sys) {
    return { err: '系统目录不动：' + abs + '\n（这个限制写死在代码里，改设置也放不开）' };
  }
  if (cfg.allowAllPaths) return { abs, root: path.parse(abs).root };
  const roots = (cfg.roots && cfg.roots.length) ? cfg.roots : defaultRoots();
  const root = inside(abs, roots);
  if (!root) {
    return { err: '这个路径不在允许的目录里：' + abs +
      '\n（允许的目录：' + roots.join('、') +
      '；要放开整台机器，在 设置 → 本地操控 里把「允许整台机器」打开）' };
  }
  return { abs, root };
}

// ── 各个工具 ──────────────────────────────────────────────────
function sizeText(n) {
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  return (n / 1024 / 1024 / 1024).toFixed(1) + ' GB';
}

async function tool_system_info() {
  const cpus = os.cpus() || [];
  const lines = [
    '系统：' + os.type() + ' ' + os.release() + '（' + os.arch() + '）',
    '主机名：' + os.hostname(),
    '当前用户：' + (os.userInfo().username || ''),
    'CPU：' + (cpus[0] ? cpus[0].model.trim() + ' × ' + cpus.length : '未知'),
    '内存：' + sizeText(os.totalmem() - os.freemem()) + ' 已用 / ' + sizeText(os.totalmem()) + ' 总共',
    '开机时长：' + (os.uptime() / 3600).toFixed(1) + ' 小时',
    '现在时间：' + new Date().toLocaleString('zh-CN'),
  ];
  // 磁盘：统计允许目录所在卷的剩余空间
  const roots = defaultRoots();
  if (roots.length && fs.statfs) {
    try {
      const s = await fs.promises.statfs(roots[0]);
      const free = s.bsize * s.bavail, total = s.bsize * s.blocks;
      lines.push('磁盘（' + path.parse(roots[0]).root + '）：剩 ' + sizeText(free) + ' / 共 ' + sizeText(total));
    } catch (_) {}
  }
  return lines.join('\n');
}

async function tool_file_list(args, cfg) {
  const g = guard(args.path || '', cfg);
  if (g.err) return g.err;
  let st;
  try { st = await fs.promises.stat(g.abs); } catch (e) { return '读不到这个目录：' + e.message; }
  if (!st.isDirectory()) return g.abs + ' 不是目录（要读文件用 local_file_read）';
  const names = await fs.promises.readdir(g.abs);
  const rows = [];
  for (const n of names.slice(0, MAX_LIST)) {
    try {
      const s = await fs.promises.stat(path.join(g.abs, n));
      rows.push((s.isDirectory() ? '[目录] ' : '       ') + n +
        (s.isDirectory() ? '' : '  ' + sizeText(s.size) + '  ' + s.mtime.toISOString().slice(0, 10)));
    } catch (_) { rows.push('       ' + n); }
  }
  const more = names.length > MAX_LIST ? '\n…（还有 ' + (names.length - MAX_LIST) + ' 项没列）' : '';
  return g.abs + ' 里有 ' + names.length + ' 项：\n' + rows.join('\n') + more;
}

async function tool_file_read(args, cfg) {
  const g = guard(args.path || '', cfg);
  if (g.err) return g.err;
  let st;
  try { st = await fs.promises.stat(g.abs); } catch (e) { return '读不到这个文件：' + e.message; }
  if (st.isDirectory()) return g.abs + ' 是目录（要列目录用 local_file_list）';
  if (st.size > MAX_READ) {
    const fd = await fs.promises.open(g.abs, 'r');
    try {
      const buf = Buffer.alloc(MAX_READ);
      await fd.read(buf, 0, MAX_READ, 0);
      return '（文件 ' + sizeText(st.size) + '，只读了前 200KB）\n' + buf.toString('utf8');
    } finally { await fd.close(); }
  }
  const buf = await fs.promises.readFile(g.abs);
  // 二进制文件别硬当文本塞给她：出现大量 NUL 就直说是二进制
  if (buf.slice(0, 4096).includes(0)) return g.abs + ' 像是二进制文件（' + sizeText(st.size) + '），读出来没法看。';
  return buf.toString('utf8');
}

async function tool_file_find(args, cfg) {
  const q = String(args.name || '').trim().toLowerCase();
  if (!q) return '没给要找的文件名';
  const roots = (cfg.roots && cfg.roots.length) ? cfg.roots : defaultRoots();
  const hits = [];
  const skip = new Set(['node_modules', '.git', 'AppData', '$Recycle.Bin', 'Windows', 'Program Files', 'Program Files (x86)']);
  async function walk(dir, depth) {
    if (hits.length >= MAX_FIND || depth > 5) return;
    let names;
    try { names = await fs.promises.readdir(dir); } catch (_) { return; }
    for (const n of names) {
      if (hits.length >= MAX_FIND) return;
      if (skip.has(n)) continue;
      const full = path.join(dir, n);
      if (n.toLowerCase().includes(q)) hits.push(full);
      let st;
      try { st = await fs.promises.stat(full); } catch (_) { continue; }
      if (st.isDirectory()) await walk(full, depth + 1);
    }
  }
  for (const r of roots) await walk(r, 0);
  if (!hits.length) return '在允许的目录里没找到名字含「' + args.name + '」的文件。';
  return '找到 ' + hits.length + ' 个：\n' + hits.join('\n') + (hits.length >= MAX_FIND ? '\n…（只列了前 50 个）' : '');
}

/**
 * 在文件内容里搜关键词或正则。
 *
 * 为什么加它：模型想找"config.json 里 api_key 在哪"，以前只能 local_file_read
 * 把整个文件读进上下文再自己扫——文件大了烧 Token。grep 直接返回匹配行，
 * Token 消耗几乎为零。这是 AI 写代码工具的标配（Glob → Grep → Read 三层里的第二层）。
 */
async function tool_file_grep(args, cfg) {
  const p = String(args.path || '').trim();
  const pat = String(args.pattern || '').trim();
  if (!p) return '没给要搜的路径';
  if (!pat) return '没给要搜的关键词';
  const abs = g.abs(p, cfg);
  if (!abs) return '路径不在允许的目录里：' + p;

  let regex;
  try { regex = new RegExp(pat, 'i'); } catch (_) { regex = null; }

  const globPat = String(args.glob || '').trim();
  const maxResults = Math.max(1, Math.min(500, Number(args.max_results) || 50));
  const skip = new Set(['node_modules', '.git', 'AppData', '$Recycle.Bin', 'Windows',
    'Program Files', 'Program Files (x86)', '.cache', 'dist', 'build', '__pycache__']);
  const TEXT_EXT = new Set(['.txt', '.md', '.json', '.js', '.ts', '.tsx', '.jsx', '.py',
    '.java', '.c', '.cpp', '.h', '.hpp', '.cs', '.go', '.rs', '.rb', '.php', '.sh',
    '.bat', '.ps1', '.yml', '.yaml', '.toml', '.ini', '.cfg', '.conf', '.html', '.css',
    '.scss', '.vue', '.sql', '.xml', '.csv', '.log', '.env', '.gitignore']);
  const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB 以上不搜

  const results = [];
  let filesSearched = 0;

  function matchLine(line) {
    if (regex) return regex.test(line);
    return line.toLowerCase().includes(pat.toLowerCase());
  }

  function globMatch(name) {
    if (!globPat) return true;
    // 简单通配符：*.py → 以 .py 结尾
    if (globPat.startsWith('*.')) {
      return name.toLowerCase().endsWith(globPat.slice(1).toLowerCase());
    }
    return name.toLowerCase().includes(globPat.toLowerCase());
  }

  async function searchFile(filePath) {
    if (results.length >= maxResults) return;
    let st;
    try { st = await fs.promises.stat(filePath); } catch (_) { return; }
    if (st.size > MAX_FILE_SIZE) return;
    if (!globMatch(path.basename(filePath))) return;
    // 无扩展名且不在 TEXT_EXT 里的，按前 2KB 判断是不是文本
    const ext = path.extname(filePath).toLowerCase();
    if (ext && !TEXT_EXT.has(ext)) return;
    let content;
    try { content = await fs.promises.readFile(filePath, 'utf-8'); } catch (_) { return; }
    filesSearched++;
    const lines = content.split('\n');
    for (let i = 0; i < lines.length && results.length < maxResults; i++) {
      if (matchLine(lines[i])) {
        results.push(filePath + ':' + (i + 1) + ': ' + lines[i].trim().slice(0, 200));
      }
    }
  }

  async function walk(dir, depth) {
    if (results.length >= maxResults || depth > 6) return;
    let names;
    try { names = await fs.promises.readdir(dir); } catch (_) { return; }
    for (const n of names) {
      if (results.length >= maxResults) return;
      if (skip.has(n) || (n.startsWith('.') && n !== '..')) continue;
      const full = path.join(dir, n);
      let st;
      try { st = await fs.promises.stat(full); } catch (_) { continue; }
      if (st.isDirectory()) await walk(full, depth + 1);
      else await searchFile(full);
    }
  }

  let st;
  try { st = await fs.promises.stat(abs); } catch (_) { return '路径不存在：' + p; }
  if (st.isDirectory()) {
    await walk(abs, 0);
  } else {
    await searchFile(abs);
  }

  if (!results.length) return '在 ' + p + ' 里没找到含「' + pat + '」的内容（搜了 ' + filesSearched + ' 个文件）。';
  return '找到 ' + results.length + ' 处匹配（搜了 ' + filesSearched + ' 个文件）：\n' +
    results.join('\n') + (results.length >= maxResults ? '\n…（只列了前 ' + maxResults + ' 条）' : '');
}

/**
 * 最近改过的文件。
 *
 * 为什么加它：`local_file_find` 只能按**名字**找，而绝大多数时候
 * 想找的是"我刚存的那个东西" —— 名字记不住，时间记得住。
 */
async function tool_file_recent(args, cfg) {
  const roots = (cfg.roots && cfg.roots.length) ? cfg.roots : defaultRoots();
  const hours = Math.max(1, Math.min(24 * 30, Number(args.hours) || 24));
  const since = Date.now() - hours * 3600 * 1000;
  const found = [];
  // 🔴 这里**不跳 `tmp` / `temp`**：Windows 的临时目录就叫 `...\Temp\`，
  // 一跳就会把"允许目录本身在临时目录下"的情况整个跳过 —— e2e 逮到过这个
  // （沙箱在 %TEMP% 下，刚写完文件却说"没有改动过的文件"）。
  // 只跳真正跟找文件无关的重目录。
  const skip = new Set(['node_modules', '.git', 'AppData', '$Recycle.Bin', 'Windows',
    'Program Files', 'Program Files (x86)', '.cache']);
  async function walk(dir, depth) {
    if (found.length >= MAX_FIND * 3 || depth > 4) return;
    let names;
    try { names = await fs.promises.readdir(dir); } catch (_) { return; }
    for (const n of names) {
      // `..` 也以点开头，但它是父目录 —— 跳过隐藏目录时别把它捎上
      if (skip.has(n) || (n.startsWith('.') && n !== '..')) continue;
      const full = path.join(dir, n);
      let st;
      try { st = await fs.promises.stat(full); } catch (_) { continue; }
      if (st.isDirectory()) { await walk(full, depth + 1); continue; }
      if (st.mtimeMs >= since) found.push({ full, mtime: st.mtimeMs, size: st.size });
    }
  }
  for (const r of roots) await walk(r, 0);
  if (!found.length) return '最近 ' + hours + ' 小时里，允许的目录下没有改动过的文件。';
  found.sort((a, b) => b.mtime - a.mtime);
  const top = found.slice(0, MAX_FIND);
  return '最近 ' + hours + ' 小时改过的文件（共 ' + found.length + ' 个，按时间倒序）：\n'
    + top.map((f) => '  ' + new Date(f.mtime).toLocaleString('zh-CN')
        + '  ' + sizeText(f.size).padStart(9) + '  ' + f.full).join('\n')
    + (found.length > MAX_FIND ? '\n…（只列了前 ' + MAX_FIND + ' 个）' : '');
}

async function tool_open_url(args) {
  const url = String(args.url || '').trim();
  if (!/^https?:\/\//i.test(url)) return '只能打开 http/https 网址：' + url;
  await shell.openExternal(url);
  return '已经用默认浏览器打开：' + url;
}

async function tool_screenshot() {
  const srcs = await desktopCapturer.getSources({ types: ['screen'], thumbnailSize: { width: 1600, height: 1000 } });
  if (!srcs.length) return '抓不到屏幕（可能是权限问题）';
  const dir = path.join(app.getPath('userData'), 'screenshots');
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, 'shot_' + Date.now() + '.png');
  fs.writeFileSync(file, srcs[0].thumbnail.toPNG());
  return '截了张屏幕图，存在：' + file + '\n（图不进对话，要看得你自己去打开）';
}

async function tool_file_write(args, cfg) {
  const g = guard(args.path || '', cfg);
  if (g.err) return g.err;
  const existed = fs.existsSync(g.abs);
  let bk;
  if (existed) {
    bk = await backupFile(g.abs);
    if (bk && bk.file) bk.existed = true;
  } else {
    // 新文件：没有"原文件"可备份，但**仍然要留一条可撤销的记录** ——
    // 撤销的动作就是把它删掉。返回 null 的话界面会写"不可撤销"，
    // 等于你让她新建了东西却收不回来。
    bk = { target: g.abs, existed: false };
  }
  await fs.promises.mkdir(path.dirname(g.abs), { recursive: true });
  await fs.promises.writeFile(g.abs, String(args.text == null ? '' : args.text), 'utf8');
  const canUndo = existed ? !!(bk && bk.file) : true;
  const tip = canUndo ? '（可撤销）' : (bk && bk.skipped ? '（' + bk.skipped + '）' : '');
  return { text: '已写入：' + g.abs + tip, backup: canUndo ? bk : null };
}

/**
 * 追加写。**跟覆盖写分成两个工具**，因为干多步任务时最常干的事是
 * "往这个脚本里再加一段" —— 用覆盖写做这件事，等于每加一段都要把
 * 前面全部内容重新生成一遍，既费 token 又容易越写越短。
 */
async function tool_file_append(args, cfg) {
  const g = guard(args.path || '', cfg);
  if (g.err) return g.err;
  const existed = fs.existsSync(g.abs);
  let bk = null;
  if (existed) {
    bk = await backupFile(g.abs);
    if (bk && bk.file) bk.existed = true;
  } else {
    bk = { target: g.abs, existed: false };
  }
  await fs.promises.mkdir(path.dirname(g.abs), { recursive: true });
  await fs.promises.appendFile(g.abs, String(args.text == null ? '' : args.text), 'utf8');
  const canUndo = existed ? !!(bk && bk.file) : true;
  return { text: '已追加到：' + g.abs + (canUndo ? '（可撤销）' : ''), backup: canUndo ? bk : null };
}

async function tool_mkdir(args, cfg) {
  const g = guard(args.path || '', cfg);
  if (g.err) return g.err;
  const existed = fs.existsSync(g.abs);
  await fs.promises.mkdir(g.abs, { recursive: true });
  return { text: existed ? '目录本来就在：' + g.abs : '已建目录：' + g.abs,
           backup: existed ? null : { target: g.abs, existed: false, isDir: true } };
}

/** 移动 / 重命名。撤销 = 搬回去。 */
async function tool_move(args, cfg) {
  const from = guard(args.from || '', cfg);
  if (from.err) return '来源不对：' + from.err;
  const to = guard(args.to || '', cfg);
  if (to.err) return '目标不对：' + to.err;
  if (!fs.existsSync(from.abs)) return '来源不存在：' + from.abs;
  if (fs.existsSync(to.abs) && !args.overwrite) {
    // 覆盖别人的文件**必须先说一声**。多步任务里"移动一堆文件"最容易
    // 撞上同名，静默覆盖是最难查的那类数据丢失。
    return '目标已经存在：' + to.abs + '（要覆盖就在参数里加 overwrite=true）';
  }
  await fs.promises.mkdir(path.dirname(to.abs), { recursive: true });
  await fs.promises.rename(from.abs, to.abs).catch(async (e) => {
    if (e.code !== 'EXDEV') throw e;
    // 跨盘符 rename 会 EXDEV，退回"复制 + 删源"
    await fs.promises.cp(from.abs, to.abs, { recursive: true });
    await fs.promises.rm(from.abs, { recursive: true, force: true });
  });
  return { text: '已移动：' + from.abs + '  →  ' + to.abs,
           backup: { target: from.abs, existed: false, movedTo: to.abs } };
}

async function tool_copy(args, cfg) {
  const from = guard(args.from || '', cfg);
  if (from.err) return '来源不对：' + from.err;
  const to = guard(args.to || '', cfg);
  if (to.err) return '目标不对：' + to.err;
  if (!fs.existsSync(from.abs)) return '来源不存在：' + from.abs;
  const existed = fs.existsSync(to.abs);
  let bk = null;
  if (existed && !args.overwrite) {
    return '目标已经存在：' + to.abs + '（要覆盖就加 overwrite=true）';
  }
  if (existed) {
    bk = await backupFile(to.abs);
    if (bk && bk.file) bk.existed = true;
  } else {
    bk = { target: to.abs, existed: false };
  }
  await fs.promises.mkdir(path.dirname(to.abs), { recursive: true });
  await fs.promises.cp(from.abs, to.abs, { recursive: true });
  return { text: '已复制：' + from.abs + '  →  ' + to.abs, backup: bk };
}

async function tool_file_delete(args, cfg) {
  const g = guard(args.path || '', cfg);
  if (g.err) return g.err;
  const bk = await backupFile(g.abs);
  await shell.trashItem(g.abs);      // 进回收站，不是真删
  const tip = bk && bk.file ? '（已备份，可撤销）' : bk && bk.skipped ? '（' + bk.skipped + '）' : '';
  return { text: '已移到回收站：' + g.abs + tip, backup: (bk && bk.file) ? bk : null };
}

// ── 备份 / 撤销 ───────────────────────────────────────────────
// 写和删之前先把原文件抄一份到 userData/backups/<时间戳>/ 下。
// 有了它，"撤销"才有意义；没有它，界面上那个按钮只是个摆设。
const MAX_BACKUP = 50 * 1024 * 1024;      // 超过 50MB 不备份（磁盘和速度都不划算）
let backupSeq = 0;

function backupDir() {
  return path.join(app.getPath('userData'), 'backups');
}

async function backupFile(abs) {
  try {
    const st = await fs.promises.stat(abs);
    if (!st.isFile()) {
      return { skipped: '目录不备份，撤不回来（' + abs + '）' };
    }
    if (st.size > MAX_BACKUP) {
      return { skipped: '文件 ' + sizeText(st.size) + ' 太大，没有备份，撤不回来' };
    }
    const dir = path.join(backupDir(), Date.now().toString(36) + '_' + (++backupSeq));
    await fs.promises.mkdir(dir, { recursive: true });
    const dst = path.join(dir, path.basename(abs));
    await fs.promises.copyFile(abs, dst);
    return { file: dst, target: abs, size: st.size };
  } catch (e) {
    return { skipped: '备份失败：' + (e && e.message || e) };
  }
}

/** 撤销：把备份抄回原位。文件本来不存在的话（新建），撤销 = 删掉它。 */
async function restore(backup) {
  if (!backup || !backup.target) return '这条没有备份，撤不回来';
  try {
    // ① 移动：把东西从 moveto 搬回 target。
    //    🔴 这条必须在 existed 判断**之前**。移动的撤销不是"删掉"——
    //    第一版把移动记成 `existed:false`，于是撤销一次移动等于把文件删了
    //    （源位置和目标位置都没了）。dryrun 逮到过这个。
    if (backup.movedTo) {
      if (!fs.existsSync(backup.movedTo)) {
        return '搬回来的时候发现它已经不在了：' + backup.movedTo;
      }
      await fs.promises.mkdir(path.dirname(backup.target), { recursive: true });
      await fs.promises.rename(backup.movedTo, backup.target).catch(async (e) => {
        if (e.code !== 'EXDEV') throw e;
        await fs.promises.cp(backup.movedTo, backup.target, { recursive: true });
        await fs.promises.rm(backup.movedTo, { recursive: true, force: true });
      });
      return '已搬回原位：' + backup.movedTo + '  →  ' + backup.target;
    }
    // ② 目录：新建的目录，撤销 = 删掉（空的才删得掉）
    if (backup.isDir) {
      try {
        await fs.promises.rmdir(backup.target);
        return '已撤销：删掉了新建的目录 ' + backup.target;
      } catch (e) {
        return '这个目录里已经有东西了，没删：' + backup.target + '（你自己看一眼再决定）';
      }
    }
    // ③ 新建的文件：撤销 = 删掉它
    if (backup.existed === false) {
      await fs.promises.rm(backup.target, { force: true, recursive: false });
      return '已撤销：删掉了新建的 ' + backup.target;
    }
    // ④ 覆盖前备份过的：把备份抄回去
    if (!backup.file) return '这条没有备份，撤不回来';
    if (!fs.existsSync(backup.file)) return '备份文件已经不在了：' + backup.file;
    await fs.promises.mkdir(path.dirname(backup.target), { recursive: true });
    await fs.promises.copyFile(backup.file, backup.target);
    return '已还原：' + backup.target;
  } catch (e) {
    return '撤销失败：' + (e && e.message || e);
  }
}

// ── 进程 ──────────────────────────────────────────────────────
// 用 `wmic` 而不是 `tasklist`：tasklist 的 CSV 在中文 Windows 上会把
// 内存写成「12,345 K」这种带千分位和单位的格式，解析出来是错的。
// wmic 给的是纯字节数。
// 🔴 wmic 在 Win11 24H2 之后被移除了 —— 所以下面留了 tasklist 兜底，
//    而且**不硬编码"哪个更好"**，哪个能跑通用哪个。
async function listProcesses() {
  const { exec } = require('child_process');
  const run = (cmd) => new Promise((res) => {
    exec(cmd, { timeout: 20000, maxBuffer: 8 * 1024 * 1024, windowsHide: true },
      (err, stdout) => res(err && !stdout ? '' : String(stdout || '')));
  });

  const out = await run('wmic process get Name,ProcessId,WorkingSetSize /format:csv');
  const rows = [];
  for (const line of out.split(/\r?\n/)) {
    const p = line.split(',').map((s) => s.trim());
    // csv 表头形如 Node,Name,ProcessId,WorkingSetSize
    if (p.length < 4 || !/^\d+$/.test(p[p.length - 2])) continue;
    const mem = Number(p[p.length - 1]) || 0;
    rows.push({ name: p[1] || p[0], pid: Number(p[p.length - 2]), mem });
  }

  if (!rows.length) {
    // 兜底：tasklist 的 csv（引号里可能还有逗号，但进程名和内存够用了）
    const t = await run('tasklist /fo csv /nh');
    for (const line of t.split(/\r?\n/)) {
      const m = line.match(/^"([^"]+)","(\d+)"[\s\S]*?"([\d,]+) K"$/);
      if (m) rows.push({ name: m[1], pid: Number(m[2]), mem: Number(m[3].replace(/,/g, '')) * 1024 });
    }
  }
  return rows;
}

async function tool_process_list(args) {
  const rows = await listProcesses();
  if (!rows.length) return '列不出进程（wmic 和 tasklist 都没跑通）';
  const f = String(args.filter || '').trim().toLowerCase();
  let hit = f ? rows.filter((r) => r.name.toLowerCase().includes(f)) : rows;
  hit = hit.sort((a, b) => b.mem - a.mem);
  const total = hit.reduce((s, r) => s + r.mem, 0);
  const top = hit.slice(0, 40);
  return (f ? '名字含「' + args.filter + '」的进程 ' + hit.length + ' 个'
            : '当前 ' + rows.length + ' 个进程')
    + '，按内存排序' + (hit.length > 40 ? '（只列前 40）' : '') + '：\n'
    + top.map((r) => '  ' + String(r.pid).padStart(7) + '  ' + sizeText(r.mem).padStart(9) + '  ' + r.name).join('\n')
    + '\n合计内存：' + sizeText(total);
}

async function tool_process_kill(args) {
  const target = String(args.name_or_pid || '').trim();
  if (!target) return '没给进程名或 PID';
  const rows = await listProcesses();

  // 系统关键进程**先查**，再谈匹配到几个。顺序反过来的话，
  // 像 explorer 这种开着一堆窗口的，会先在"匹配到多个、请指定 PID"那儿返回，
  // 根本走不到保护检查（e2e 逮到过这个）。
  const PROTECTED = new Set(['system', 'registry', 'smss', 'csrss', 'wininit', 'winlogon',
    'services', 'lsass', 'svchost', 'dwm', 'explorer', 'fontdrvhost', 'sihost',
    'taskhostw', 'ctfmon', 'audiodg', 'conhost', 'memcompression']);
  const isProtected = (name) => PROTECTED.has(String(name).replace(/\.exe$/i, '').toLowerCase());

  if (!/^\d+$/.test(target) && isProtected(target)) {
    return '「' + target + '」是系统进程，不能结束（结束了这台电脑会出问题）';
  }

  let victims;
  if (/^\d+$/.test(target)) {
    victims = rows.filter((r) => r.pid === Number(target));
    if (!victims.length) return '没有 PID 为 ' + target + ' 的进程';
  } else {
    victims = rows.filter((r) => r.name.toLowerCase() === target.toLowerCase());
    if (!victims.length) victims = rows.filter((r) => r.name.toLowerCase().includes(target.toLowerCase()));
    if (victims.length > 1) {
      // 同名多个进程：**不猜**。杀错进程比拒绝执行糟得多。
      return '「' + target + '」匹配到 ' + victims.length + ' 个进程（PID '
        + victims.slice(0, 8).map((v) => v.pid).join('、')
        + (victims.length > 8 ? '…' : '') + '），没敢动。请指定 PID。';
    }
  }
  if (!victims.length) return '没有匹配「' + target + '」的进程';

  const v = victims[0];
  if (isProtected(v.name)) {
    return '「' + v.name + '」是系统进程，不能结束（结束了这台电脑会出问题）';
  }

  const { exec } = require('child_process');
  const out = await new Promise((res) => {
    exec('taskkill /PID ' + v.pid + ' /F', { timeout: 15000, windowsHide: true },
      (err, stdout, stderr) => res(String(stdout || '') + String(stderr || '')));
  });
  const ok = /成功|SUCCESS/i.test(out);
  return (ok ? '已结束进程：' : '结束失败：') + v.name + '（PID ' + v.pid + '）'
    + (ok ? '' : '\n' + out.trim().slice(0, 300));
}

// ── 当前窗口 ──────────────────────────────────────────────────
// 纯 JS 碰不到 Win32 API。为了这一个功能引一个原生模块不划算，所以借 PowerShell。
//
// 🔴 脚本**写成临时 .ps1 再执行**，不要用 `powershell -Command "..."` 内联：
//    内联字符串要穿透 JS 转义 + cmd 转义 + PowerShell 解析三层，
//    里面的 here-string（@" ... "@）转义完就废了 —— e2e 逮到过
//    "Command failed: powershell -NoProfile -Command Add-Type @" 这种。
//
// 用 `powershell`（Windows PowerShell 5.1）而不是 `pwsh`：5.1 每台 Windows 都有，
// pwsh 要另装，而这点活 5.1 完全够。
const PS_ACTIVE_WINDOW = [
  'Add-Type @"',
  'using System;',
  'using System.Runtime.InteropServices;',
  'using System.Text;',
  'public class FgWin {',
  '  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();',
  '  [DllImport("user32.dll", CharSet=CharSet.Unicode)]',
  '  public static extern int GetWindowTextW(IntPtr h, StringBuilder t, int n);',
  '  public static string Title() {',
  '    IntPtr h = GetForegroundWindow();',
  '    if (h == IntPtr.Zero) return "";',
  '    StringBuilder sb = new StringBuilder(512);',
  '    GetWindowTextW(h, sb, 512);',
  '    return sb.ToString();',
  '  }',
  '}',
  '"@',
  // 输出走 base64：PowerShell 5.1 的 stdout 默认是控制台代码页（中文机器=GBK），
  // node execFile 按 UTF-8 解码必然乱码（2026-10-09 她看到的"????-¼..."就是这来的）。
  // base64 不受代码页影响，node 侧解码 100% 可靠。
  '$b=[System.Text.Encoding]::UTF8.GetBytes([FgWin]::Title()); [Convert]::ToBase64String($b)',
].join('\r\n');

async function tool_active_window() {
  if (process.platform !== 'win32') return '只有 Windows 上能读前台窗口';
  const tmp = path.join(app.getPath('temp'), 'vigil_fgwin.ps1');
  try {
    fs.writeFileSync(tmp, '\ufeff' + PS_ACTIVE_WINDOW, 'utf8');   // BOM：免得中文标题乱码
    const { execFile } = require('child_process');
    const out = await new Promise((res) => {
      execFile('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', tmp],
        { timeout: 20000, windowsHide: true },
        (err, stdout, stderr) => res({ err, stdout: String(stdout || ''), stderr: String(stderr || '') }));
    });
    // PS 输出的是 UTF8 字节的 base64；解码失败（老版本残留/异常输出）就退回原文
    let title = out.stdout.trim();
    try {
      const decoded = Buffer.from(title, 'base64').toString('utf8');
      // 合法 base64 且解出非空可读文本才采用；避免把普通文本误当 base64
      if (decoded && /^[A-Za-z0-9+/=]+$/.test(title) && title.length % 4 === 0) title = decoded;
    } catch (_) { /* 保持原样 */ }
    if (title) return '当前最前面的窗口是：' + title;
    if (out.err) return '读不到前台窗口：' + (out.stderr.trim().slice(0, 200) || out.err.message);
    return '读不到前台窗口标题（可能所有窗口都最小化了）';
  } catch (e) {
    return '读不到前台窗口：' + (e && e.message || e);
  } finally {
    try { fs.unlinkSync(tmp); } catch (_) {}
  }
}

// ── 剪贴板 ────────────────────────────────────────────────────
async function tool_clipboard_read() {
  const { clipboard } = require('electron');
  const t = clipboard.readText();
  if (!t) return '剪贴板里没有文字（可能是空的，或者只有图片/文件）';
  return '剪贴板里 ' + t.length + ' 个字：\n' + t.slice(0, 20000);
}

async function tool_clipboard_write(args) {
  const { clipboard } = require('electron');
  const t = String(args.text == null ? '' : args.text);
  clipboard.writeText(t);
  return '已放进剪贴板：' + (t.length > 60 ? t.slice(0, 60) + '…' : t) + '（' + t.length + ' 字）';
}

// ── 命令执行 ──────────────────────────────────────────────────
// 只读白名单：**默认放行的那些**。属于"看一眼状态"，没什么可改的。
const CMD_ALLOW = [
  'ipconfig', 'systeminfo', 'tasklist', 'ver', 'whoami', 'hostname', 'date', 'time',
  'dir', 'tree', 'type', 'find', 'findstr', 'where', 'wmic', 'netstat', 'ping', 'tracert',
  'nslookup', 'chcp', 'echo', 'git status', 'git log', 'git diff', 'node -v', 'npm -v',
  'python --version', 'pip list',
];

// 不管开不开"允许整台机器"，这些**永远不执行**。
// 它们不是"危险"，是"一执行就可能不可逆地毁掉这台机器"：
// 格盘、覆盖引导记录、把整个盘删空。
const CMD_BLOCK = [
  /format\s+[a-z]:/i, /diskpart/i, /\bmkfs\b/i, /\bdd\s+if=/i,
  /\brm\s+-rf\s+\/(\s|$)/i, /remove-item\s+.*-recurse.*[a-z]:\\?\s*$/i,
  /shutdown\s+\/[frs]/i, /\bbcdedit\b/i, /vssadmin\s+delete/i,
  /cipher\s+\/w/i, /takeown\s+\/f\s+[a-z]:\\windows/i,
];

/** 返回 null 表示放行；返回字符串表示拒绝的理由。 */
function execVerdict(cmd, cfg) {
  const c = String(cmd || '').trim();
  if (!c) return '没给命令';
  for (const re of CMD_BLOCK) {
    if (re.test(c)) return '这条命令会不可逆地破坏系统，永远不执行：' + c.slice(0, 80);
  }
  const low = c.toLowerCase();
  const hit = CMD_ALLOW.find((x) => low === x || low.startsWith(x + ' '));
  if (hit) return null;                       // 只读命令：直接放行
  if (cfg && cfg.allowAllCmds) return null;   // 开了"允许整台机器"：放行，但客户端仍会弹确认
  return '这条命令不在只读白名单里，没执行：' + c.slice(0, 80) +
    '\n（只放行看状态的命令，比如 ' + CMD_ALLOW.slice(0, 6).join('、') + ' …；' +
    '要让它能跑别的命令，在 设置 → 本地操控 里把「允许执行命令」打开）';
}

async function tool_exec(args, cfg) {
  const cmd = String(args.cmd || '').trim();
  const bad = execVerdict(cmd, cfg);
  if (bad) return bad;
  const { exec } = require('child_process');
  return await new Promise((resolve) => {
    exec(cmd, { timeout: 60000, maxBuffer: 2 * 1024 * 1024, windowsHide: true,
                cwd: (cfg && cfg.workdir) || undefined },
      (err, stdout, stderr) => {
        const out = (stdout || '') + (stderr ? '\n[stderr]\n' + stderr : '');
        const text = out.trim().slice(0, 12000);
        if (err && !text) resolve('命令执行失败：' + err.message);
        else resolve(text || '（命令没有输出）');
      });
  });
}

const RUN = {
  local_system_info: () => tool_system_info(),
  local_file_list: tool_file_list,
  local_file_read: tool_file_read,
  local_file_find: tool_file_find,
  local_file_grep: tool_file_grep,
  local_file_recent: tool_file_recent,
  open_url: tool_open_url,
  local_screenshot: () => tool_screenshot(),
  local_process_list: tool_process_list,
  local_active_window: tool_active_window,
  local_clipboard_read: tool_clipboard_read,
  local_file_write: tool_file_write,
  local_file_append: tool_file_append,
  local_file_delete: tool_file_delete,
  local_mkdir: tool_mkdir,
  local_move: tool_move,
  local_copy: tool_copy,
  local_process_kill: tool_process_kill,
  local_clipboard_write: tool_clipboard_write,
  local_exec: tool_exec,
};

// ── 工具目录 ──────────────────────────────────────────────────
// **这份目录是客户端自己的**，不依赖 brain（没网、brain 没跑，工具页照样能用）。
// 代价是描述文字跟 brain/localtools.py 里的有一份重复 —— 用 e2e 里的
// "工具名单对账"盯着两边别漂移（名字对不上会当场报出来）。
const CATALOG = [
  { name: 'local_system_info', group: 'system', danger: false,
    desc: '看这台电脑的基本情况：系统版本、CPU、内存、磁盘剩余、当前时间',
    params: [] },
  { name: 'local_process_list', group: 'system', danger: false,
    desc: '列出正在运行的进程（按内存排序，可只挑名字含某个词的）',
    params: [{ k: 'filter', label: '只要名字含（可留空）', ph: 'chrome', req: false }] },
  { name: 'local_active_window', group: 'system', danger: false,
    desc: '看现在最前面的是哪个窗口（标题）',
    params: [] },

  { name: 'local_file_list', group: 'files', danger: false,
    desc: '列出一个目录里有什么（名字、大小、修改日期）',
    params: [{ k: 'path', label: '目录路径', ph: '~/Desktop 或 D:\\某目录', req: true }] },
  { name: 'local_file_read', group: 'files', danger: false,
    desc: '读一个文本文件的内容（只读，单次最多 200KB）',
    params: [{ k: 'path', label: '文件路径', ph: '~/文档/笔记.md', req: true }] },
  { name: 'local_file_find', group: 'files', danger: false,
    desc: '按文件名在允许的目录里递归找（最多 50 个、5 层）',
    params: [{ k: 'name', label: '文件名或其中一段', ph: '发票', req: true }] },
  { name: 'local_file_grep', group: 'files', danger: false,
    desc: '在文件内容里搜关键词或正则，返回文件名+行号+内容（比读全文省 Token）',
    params: [
      { k: 'path', label: '目录或文件路径', ph: 'F:/Vigil/brain', req: true },
      { k: 'pattern', label: '关键词或正则', ph: 'api_key', req: true },
      { k: 'glob', label: '只搜某类文件（可留空）', ph: '*.py', req: false },
      { k: 'max_results', label: '最多返回条数（默认 50）', ph: '50', req: false },
    ] },
  { name: 'local_file_recent', group: 'files', danger: false,
    desc: '列出最近改动过的文件 —— 记不住名字、只记得"刚存的"时候用它',
    params: [{ k: 'hours', label: '最近多少小时（默认 24）', ph: '24', req: false }] },

  { name: 'local_clipboard_read', group: 'clip', danger: false,
    desc: '读剪贴板里的文字',
    params: [] },
  { name: 'local_clipboard_write', group: 'clip', danger: true,
    desc: '把一段文字放进剪贴板（会覆盖你现在复制的东西）',
    params: [{ k: 'text', label: '要放进剪贴板的文字', ph: '', req: true, multi: true }] },

  { name: 'open_url', group: 'net', danger: false,
    desc: '用默认浏览器打开一个网址（只认 http/https）',
    params: [{ k: 'url', label: '网址', ph: 'https://…', req: true }] },
  { name: 'local_screenshot', group: 'net', danger: false,
    desc: '截一张当前屏幕的图，存在本地（不进对话）',
    params: [] },

  { name: 'local_file_write', group: 'do', danger: true,
    desc: '往文件里写内容（新建或覆盖）。写之前自动备份，可撤销',
    params: [{ k: 'path', label: '文件路径', ph: '~/Desktop/note.txt', req: true },
             { k: 'text', label: '要写入的内容', ph: '', req: true, multi: true }] },
  { name: 'local_file_append', group: 'do', danger: true,
    desc: '往文件末尾追加内容（写脚本时一段段加，别整篇重写）',
    params: [{ k: 'path', label: '文件路径', ph: '~/Desktop/run.bat', req: true },
             { k: 'text', label: '要追加的内容', ph: '', req: true, multi: true }] },
  { name: 'local_mkdir', group: 'do', danger: true,
    desc: '新建目录（含中间的父目录），已存在就当没事',
    params: [{ k: 'path', label: '目录路径', ph: '~/Desktop/整理好的', req: true }] },
  { name: 'local_move', group: 'do', danger: true,
    desc: '移动或重命名（文件、目录都行）。目标已存在时不会偷偷覆盖',
    params: [{ k: 'from', label: '从哪', ph: '~/Downloads/a.pdf', req: true },
             { k: 'to', label: '到哪', ph: '~/Desktop/文档/a.pdf', req: true },
             { k: 'overwrite', label: '目标存在时覆盖（填 true）', ph: '', req: false }] },
  { name: 'local_copy', group: 'do', danger: true,
    desc: '复制文件或目录',
    params: [{ k: 'from', label: '从哪', ph: '', req: true },
             { k: 'to', label: '到哪', ph: '', req: true },
             { k: 'overwrite', label: '目标存在时覆盖（填 true）', ph: '', req: false }] },
  { name: 'local_file_delete', group: 'do', danger: true,
    desc: '把文件/目录移到回收站（先备份，可撤销）',
    params: [{ k: 'path', label: '要删的路径', ph: '', req: true }] },
  { name: 'local_process_kill', group: 'do', danger: true,
    desc: '结束一个进程（按 PID）。系统进程和同名多开的会被拒绝执行',
    params: [{ k: 'name_or_pid', label: '进程 PID（更安全）或名字', ph: '12345', req: true }] },
  { name: 'local_exec', group: 'do', danger: true,
    desc: '执行一条命令行。只读命令直接放行，其他要先在设置里打开并每次确认',
    params: [{ k: 'cmd', label: '命令', ph: 'ipconfig', req: true }] },
];

const GROUP_NAME = { system: '系统', files: '文件', clip: '剪贴板', net: '网络', do: '会改动东西' };

const DANGER = new Set(CATALOG.filter((t) => t.danger).map((t) => t.name));

// "会改动东西"的那一批默认**关着**，要用自己开（设置 → 本地操控）。
// 读的那批（看状态、读文件、剪贴板、截图…）默认开着。
const DEFAULT_OFF = ['local_file_write', 'local_file_append', 'local_file_delete',
  'local_mkdir', 'local_move', 'local_copy',
  'local_process_kill', 'local_clipboard_write', 'local_exec'];


/** 这台设备现在能干什么（跟着设置里的开关走）。 */
function capabilities(cfg) {
  const c = cfg || {};
  // 没显式配过 localOff 就按默认来：读的能用，"会改动东西"的先关着。
  // 配过就完全以配置为准（包括"我就是要全关"这种）。
  const off = Array.isArray(c.localOff) ? c.localOff : DEFAULT_OFF;
  const all = CATALOG.map((t) => t.name).filter((n) => !off.includes(n));
  // 危险的那几个还要单独打开（默认关）
  const risky = (c.localRisky && c.localRisky.length) ? c.localRisky : [];
  return all.filter((n) => !DANGER.has(n) || risky.includes(n));
}

/**
 * 跑一个本地工具。
 *
 * 返回 `{text, backup}`：text 是给她/给你看的那句话（跟以前一样是纯文本），
 * backup 有值才代表"这次操作能撤销"。调用方（main.js）拿它写操作日志。
 */
async function run(name, args, cfg) {
  const fn = RUN[name];
  if (!fn) return { text: '这台电脑没有这个能力：' + name, backup: null };
  try {
    const out = await fn(args || {}, cfg || {});
    if (out && typeof out === 'object' && typeof out.text === 'string') {
      return { text: out.text, backup: out.backup || null };
    }
    return { text: String(out), backup: null };
  } catch (e) {
    return { text: '本机执行出错：' + (e && e.message || e), backup: null };
  }
}

module.exports = {
  run, capabilities, defaultRoots, restore,
  CATALOG, GROUP_NAME, DANGER, CMD_ALLOW, CMD_BLOCK, execVerdict,
  inside, expand, isSystemPath, backupFile,
};
