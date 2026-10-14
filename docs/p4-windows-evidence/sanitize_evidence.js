// 证据脱敏 + 旧轮元数据补录（透明、可复验）。
// 脱敏范围沿用 REPORT.md §七 惯例：只替换本机用户名与临时目录前缀
// （→ %TEMP% / %USERPROFILE%）；端口、回环地址、包名、断言文本原样保留。
// 用法：node sanitize_evidence.js [--write]
const fs = require('fs');
const path = require('path');

const EV = __dirname;
const WRITE = process.argv.includes('--write');

// 原始文件文本里的转义形态（§七：1/2/4 层都处理）：
//   4 层 = detail 字段里嵌了第二层 JSON（harness 把 JSON.stringify 的结果
//          再塞进 detail，外层又转义一次）→ 文件里每段 4 个反斜杠
//   2 层 = 普通 JSON 字符串里的路径 → 文件里每段 2 个反斜杠
//   1 层 = 裸文本（理论上不进 JSON，防御性列出）
const REPLACEMENTS = [
    ['C:\\\\\\\\Users\\\\\\\\limingyi\\\\\\\\AppData\\\\\\\\Local\\\\\\\\Temp', '%TEMP%'],
    ['C:\\\\Users\\\\limingyi\\\\AppData\\\\Local\\\\Temp', '%TEMP%'],
    ['C:\\Users\\limingyi\\AppData\\Local\\Temp', '%TEMP%'],
    ['C:\\\\\\\\Users\\\\\\\\limingyi', '%USERPROFILE%'],
    ['C:\\\\Users\\\\limingyi', '%USERPROFILE%'],
    ['C:\\Users\\limingyi', '%USERPROFILE%'],
];

const FILES = [
    '298e7c6/results.json',
    '298e7c6-c123/results-c123.json',
    'c0346e5/results.json',
    'c0346e5-c123/results-c123.json',
];

// 旧轮（c0346e5-c123）的 harness 不记 commit/python，按会话时间线补录并注明
const SUPPLEMENT = {
    'c0346e5-c123/results-c123.json': {
        commit: 'c0346e53ad0a8053358d1538d46b2470f021a00f',
        python: 'Python 3.13.15',
        note: 'commit/python 两字段为验收后补录：该轮（26/35，修复前）所用 harness 版本的元数据采集尚不成熟（worktree 分支 ref 未经 commondir 回退 + 裸 spawnSync python 撞 EBUSY）而留空；commit 由 .git 指针文件经 commondir 解析主仓库 ref 只读获得（c0346e53ad0a8053358d1538d46b2470f021a00f，即该轮实际 HEAD），python 为 harness 启动时 PATH 首选的系统 Python 3.13.15。35 条 results、截图、其余元数据未改动。该轮的失败项（C3-4/5a/8a/8c/8d 技能 scope 假失败、C3-7c 重启失败虚假清除、C2-4 卸载谎称线程、C2-1/C2-3 DOM 陈旧）的根因与修复见 REPORT-c0346e5.md。',
    },
};

for (const rel of FILES) {
    const fp = path.join(EV, rel);
    if (!fs.existsSync(fp)) { console.log('SKIP (不存在): ' + rel); continue; }
    let t = fs.readFileSync(fp, 'utf8');
    const before = t;
    let n = 0;
    for (const [from, to] of REPLACEMENTS) {
        const parts = t.split(from);
        n += parts.length - 1;
        t = parts.join(to);
    }
    let j;
    try { j = JSON.parse(t); } catch (e) {
        console.log('ERROR JSON 解析失败: ' + rel + ' -> ' + e.message);
        continue;
    }
    const changed = t !== before;
    const sup = SUPPLEMENT[rel];
    if (sup) {
        if (!j.commit) j.commit = sup.commit;
        if (!j.python) j.python = sup.python;
        j.metadata_note = sup.note;
    }
    if (changed) {
        j.sanitize_note = '本文件的 detail 字段经脱敏后处理：仅将本机用户名与临时目录前缀替换为 %TEMP%/%USERPROFILE%（沿用 REPORT.md §七 惯例，两种反斜杠转义形态均已处理），端口/回环地址/包名/断言文本与全部 results 未改动；写回后仍是合法 JSON（已复验）。';
    }
    const ok = j.results.filter((r) => r.ok).length + '/' + j.results.length;
    console.log((changed ? 'CHANGED' : 'clean  ') + ' ' + rel + '  replacements=' + n + '  results=' + ok +
        (sup ? '  [补录 commit/python]' : '') + (changed ? '  [加 sanitize_note]' : ''));
    if (WRITE && (changed || sup)) {
        fs.writeFileSync(fp, JSON.stringify(j, null, 2));
        // 复验：写回后可解析且 results 条数不变
        const j2 = JSON.parse(fs.readFileSync(fp, 'utf8'));
        if (j2.results.length !== j.results.length) throw new Error('results 条数被改动: ' + rel);
        if (fs.readFileSync(fp, 'utf8').includes('limingyi')) throw new Error('仍含用户名: ' + rel);
    }
}
if (!WRITE) console.log('\n（dry-run；加 --write 落盘）');
