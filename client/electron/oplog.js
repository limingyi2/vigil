// -*- coding: utf-8 -*-
/**
 * 操作日志：这台电脑上被做过的每一次本地操作。
 *
 * 为什么必须有它：她能删你的文件、能改你的文件。在做这个日志之前，
 * 你既看不到"她刚才动了什么"，也没法还原 —— 一个能动手的系统，
 * 不能只有"动手"这一半。
 *
 * 记在客户端本地（userData/oplog.jsonl），不放服务端：
 * 这些是**这台机器**上发生过的事，换台机器就不该看见。
 *
 * 记录里带 `backup`：有备份的才能撤销。没备份的（大文件、目录）
 * 如实标出来，避免界面给一个点了没反应的"撤销"。
 */
const fs = require('fs');
const path = require('path');

let DIR = null;
let FILE = null;

function init(userDataDir) {
  DIR = userDataDir;
  FILE = path.join(userDataDir, 'oplog.jsonl');
}

const MAX_LINES = 3000;      // 超过就把最老的丢掉：日志是给自己看的，不是审计档案

function append(rec) {
  if (!FILE) return null;
  const row = {
    id: 'op_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    t: new Date().toISOString().replace('T', ' ').slice(0, 19),
    tool: rec.tool || '',
    args: rec.args || {},
    source: rec.source === 'her' ? 'her' : 'me',   // 谁触发的：你点的，还是她调的
    ok: !!rec.ok,
    text: String(rec.text == null ? '' : rec.text).slice(0, 4000),
    backup: rec.backup || null,                    // {file, target} 才有得撤
    undone: false,
    err: rec.err || '',
  };
  try {
    fs.mkdirSync(DIR, { recursive: true });
    fs.appendFileSync(FILE, JSON.stringify(row) + '\n', 'utf8');
    rotate();
  } catch (e) {
    console.warn('[日志] 写不进去：' + (e && e.message || e));
  }
  return row;
}

function rotate() {
  try {
    const lines = fs.readFileSync(FILE, 'utf8').split('\n').filter(Boolean);
    if (lines.length <= MAX_LINES) return;
    fs.writeFileSync(FILE, lines.slice(-MAX_LINES).join('\n') + '\n', 'utf8');
  } catch (_) {}
}

function list(limit) {
  if (!FILE) return [];
  let lines = [];
  try {
    lines = fs.readFileSync(FILE, 'utf8').split('\n').filter(Boolean);
  } catch (_) {
    return [];
  }
  const n = Math.max(1, Math.min(2000, Number(limit) || 200));
  // 新的在前
  return lines.slice(-n).reverse().map((l) => {
    try { return JSON.parse(l); } catch (_) { return null; }
  }).filter(Boolean);
}

function markUndone(id) {
  if (!FILE) return false;
  try {
    const lines = fs.readFileSync(FILE, 'utf8').split('\n').filter(Boolean);
    let hit = false;
    const out = lines.map((l) => {
      let o;
      try { o = JSON.parse(l); } catch (_) { return l; }
      if (o.id === id) { o.undone = true; hit = true; }
      return JSON.stringify(o);
    });
    if (hit) fs.writeFileSync(FILE, out.join('\n') + '\n', 'utf8');
    return hit;
  } catch (_) {
    return false;
  }
}

function get(id) {
  return list(2000).find((r) => r.id === id) || null;
}

module.exports = { init, append, list, get, markUndone, MAX_LINES };
