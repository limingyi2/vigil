// -*- coding: utf-8 -*-
/**
 * 生成 src/_harness.html：把 index.html 多加一行假桥引用，
 * 这样不开 Electron、不起 brain，用浏览器就能看界面。
 *
 *   node tools/make-harness.js
 *
 * 假数据在 src/js/_devstub.js 里（照真实接口的返回结构写的）。
 * 带 hash 可以直接进某一页：_harness.html#kb  /  #ability  /  #settings/device
 */
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const idx = path.join(root, 'src', 'index.html');
const out = path.join(root, 'src', '_harness.html');
const NEEDLE = '<script src="js/ui.js"></script>';

const src = fs.readFileSync(idx, 'utf8');
if (!src.includes(NEEDLE)) {
  console.error('index.html 里的脚本引用变了，这里要跟着改：' + NEEDLE);
  process.exit(1);
}
fs.writeFileSync(out, src.replace(NEEDLE, '<script src="js/_devstub.js"></script>\n' + NEEDLE), 'utf8');
console.log('已生成：' + out);
console.log('浏览器打开它即可（带 hash 直接进某一页）：');
console.log('  file:///' + out.replace(/\\/g, '/'));
console.log('  file:///' + out.replace(/\\/g, '/') + '#kb');
