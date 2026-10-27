// -*- coding: utf-8 -*-
/**
 * 验证"像 agent 一样连续干活"：一串工具调用能不能真的把活干完。
 *
 * 不用 LLM —— 那要花钱、还得等模型。这里**直接在进程内按顺序调工具**，
 * 模拟模型会发出的那一串调用：建目录 → 写脚本 → 追加 → 移动 → 列出来核对。
 * 测的是**工具链本身够不够用**（能不能连续干完一件事、撤销链对不对）。
 *
 * 全程关在临时沙箱里，不碰真实目录。
 *
 *   node_modules\electron\dist\electron.exe tools/agent-dryrun.js
 */
const { app } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');

app.whenReady().then(async () => {
  const localtool = require(path.join(__dirname, '..', 'electron', 'localtool.js'));
  const oplog = require(path.join(__dirname, '..', 'electron', 'oplog.js'));

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'vigil_agent_'));
  app.setPath('userData', path.join(tmp, 'profile'));
  fs.mkdirSync(path.join(tmp, 'profile'), { recursive: true });
  oplog.init(path.join(tmp, 'profile'));
  process.stdout.write('[setup] done ' + tmp + '\n');

  // 模拟场景：桌面上堆了一堆混在一起的下载文件
  const desk = path.join(tmp, 'Desktop');
  fs.mkdirSync(desk, { recursive: true });
  for (const f of ['报告.pdf', 'photo.jpg', 'note.txt', 'setup.exe', 'data.xlsx', 'old.mp4']) {
    fs.writeFileSync(path.join(desk, f), 'x');
  }

  // 这就是"允许整台机器 + 允许执行命令"打开后的配置
  const cfg = { allowAllPaths: true, allowAllCmds: true, roots: [tmp] };

  let pass = 0, fail = 0;
  const check = (name, ok, extra) => {
    console.log((ok ? '  OK   ' : '  FAIL ') + name + (extra ? '   ' + extra : ''));
    if (ok) { pass += 1; } else { fail += 1; }
  };
  // 自己记一份操作台账。
  //  不要指望 oplog.jsonl 有东西：往日志里写的是 **main.js**（进程里那条
  // IPC 路径），这个脚本绕过了它直接调 localtool.run，所以日志是空的。
  // 第一版就踩了这个，然后拿空日志去读 .backup → 抛异常 → 因为是 async
  // 回调里未捕获的 rejection，Electron 不退出，整个脚本永久挂住。
  const journal = [];
  const run = async (tool, args) => {
    const r = await localtool.run(tool, args, cfg);
    journal.push({ tool, args, text: r.text, backup: r.backup });
    return r.text;
  };

  console.log('\n沙箱：' + tmp);
  console.log('\n=== 模拟她接到的指令：「把桌面的文件按类型分好类」 ===\n');

  // 第 1 步：看看桌面上有什么
  const listed = await run('local_file_list', { path: desk });
  check('1. 列目录', listed.includes('报告.pdf') && listed.includes('photo.jpg'),
        listed.split('\n')[0]);

  // 第 2 步：建分类目录
  const cats = ['文档', '图片', '程序', '表格', '视频'];
  const mk = [];
  for (const c of cats) mk.push(await run('local_mkdir', { path: path.join(desk, c) }));
  check('2. 建 5 个分类目录', mk.every((t) => /已建目录|本来就在/.test(t)), mk[0].split('\n')[0]);

  // 第 3 步：写一个整理脚本（新建 + 追加，模拟"一段段加"）
  const script = path.join(desk, '整理.bat');
  const w1 = await run('local_file_write', { path: script, text: '@echo off\r\nrem 整理桌面文件\r\n' });
  const w2 = await run('local_file_append', { path: script, text: 'echo done\r\n' });
  check('3. 写脚本 + 追加', /已写入/.test(w1) && /已追加/.test(w2), path.basename(script));

  // 第 4 步：按类型把文件挪进对应目录
  const plan = [
    ['报告.pdf', '文档'], ['note.txt', '文档'],
    ['photo.jpg', '图片'],
    ['setup.exe', '程序'],
    ['data.xlsx', '表格'],
    ['old.mp4', '视频'],
  ];
  let moved = 0, refused = 0;
  for (const [f, c] of plan) {
    const t = await run('local_move', { from: path.join(desk, f), to: path.join(desk, c, f) });
    if (/已移动/.test(t)) moved++;
    else { refused++; console.log('     （没搬成）' + f + ' → ' + c + '：' + t.split('\n')[0]); }
  }
  check('4. 按类型移动 6 个文件', moved === 6, moved + ' 个成功' + (refused ? '，' + refused + ' 个失败' : ''));

  // 第 5 步：核对结果
  const after = await run('local_file_list', { path: desk });
  const inDoc = await run('local_file_list', { path: path.join(desk, '文档') });
  check('5. 桌面只剩分类目录+脚本', !after.includes('photo.jpg') && after.includes('文档'),
        '文档目录里：' + inDoc.split('\n').slice(1, 3).map((s) => s.trim()).join(' / '));

  // ── 多步任务的三个关键性质 ──
  console.log('\n=== 多步任务的三个关键性质 ===');

  const log = journal;
  check('A. 每一步都留了痕（工具链每步都有返回）', log.length >= 15,
        log.length + ' 次工具调用');
  // 13 是实际数出来的：5 次 mkdir + 写+追加（2）+ 6 次 move = 13。
  // 剩下的 3 次是列目录（读操作，本来就不该有备份）。
  const undoable = log.filter((r) => r.backup);
  const writers = log.filter((r) => /write|append|mkdir|move|copy|delete/.test(r.tool));
  check('B. 每个改动都有撤销依据', undoable.length === writers.length,
        writers.length + ' 次改动 / ' + undoable.length + ' 次可撤销');

  // 撤销一步移动：文件该**回到原位**，不是被删掉。
  // （第一版这里错了：移动的撤销记成了"新建"，撤销的动作变成删文件，
  //   搬过去的那份和原来的位置一起没了。）
  const lastMove = log.filter((r) => r.tool === 'local_move' && r.backup).pop();
  if (!lastMove) {
    check('C. 撤销一次移动 = 搬回原位', false, '没找到可撤销的移动记录');
  } else {
    const movedName = path.basename(lastMove.backup.target);
    const back = await localtool.restore(lastMove.backup);
    const atSource = fs.existsSync(path.join(desk, movedName));
    check('C. 撤销一次移动 = 搬回原位（不是删掉）', atSource && /已搬回原位/.test(back),
          movedName + '：' + back);
  }

  // 覆盖保护：往已存在的位置搬，必须拒绝而不是偷偷覆盖
  const guard = await run('local_move', { from: script, to: path.join(desk, '文档', '报告.pdf') });
  check('D. 同名覆盖被拦下（不偷偷覆盖）', /目标已经存在/.test(guard), guard.split('\n')[0]);

  // 系统目录硬拦
  const sys = await run('local_file_write', { path: 'C:\\Windows\\evil.txt', text: 'x' });
  check('E. 系统目录硬拦（放开整台机器也拦）', /系统目录不动/.test(sys), sys.split('\n')[0]);

  // 危险命令永远不执行
  const bad = await run('local_exec', { cmd: 'format C: /y' });
  check('F. 破坏性命令永远拒绝', /不可逆地破坏系统/.test(bad), bad.split('\n')[0]);

  console.log('\n结果：' + pass + ' / ' + (pass + fail) + ' 通过');
  console.log('沙箱：' + desk);
  app.exit(fail ? 1 : 0);
}).catch((e) => {
  //  少了这个 catch，async 回调里抛的异常会变成"未捕获的 rejection"，
  // Electron 不退出、脚本永久挂住（第一版就是这样，查了半天）。
  console.log('!! 脚本异常：' + (e && e.stack || e));
  app.exit(2);
});
