// -*- coding: utf-8 -*-
/**
 * 清掉本机存的那把 token（跑完 e2e / 想重置客户端时用）。
 *
 *   node_modules\electron\dist\electron.exe tools/clear-token.js
 *
 * 为什么要它：e2e 用的是一份临时 brain，发出来的设备 token 留在系统钥匙串里，
 * 下次启动真实 brain 时这把 token 根本不存在 —— 客户端会 401，
 * 然后（修好之后）靠自愈重新探测。但测试不该给别人留这种状态，所以主动清掉。
 */
const { app } = require('electron');

app.whenReady().then(async () => {
  try {
    const keytar = require('keytar');
    const had = await keytar.getPassword('vigil-client', 'brain-token');
    await keytar.deletePassword('vigil-client', 'brain-token');
    console.log(had ? '已清除 keytar 里的 token' : 'keytar 里本来就没有');
  } catch (e) {
    console.log('清不了 keytar：' + (e && e.message || e));
  }
  app.exit(0);
});
