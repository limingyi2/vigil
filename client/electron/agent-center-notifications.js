// Native notifications only. No approval response, credentials or arbitrary IPC.
const {pathToFileURL} = require('url');
function register({ipcMain, Notification, getWindow, indexPath, onClick = () => {}}) {
  const seen = new Set(), active = new Set();
  const bodies = {approval:'Codex 正在等待审批。当前仅支持查看，审批答复尚未开放。',
    input:'Codex 需要补充输入。当前仅支持查看，答复尚未开放。',
    failure:'Codex 任务出现明确失败，请查看 Agent Center 的事件记录。'};
  const trusted = pathToFileURL(indexPath).href;
  ipcMain.handle('agent-center:notify', (event, payload) => {
    const win = getWindow();
    if (!win || win.isDestroyed() || event.sender !== win.webContents ||
        event.senderFrame !== win.webContents.mainFrame || event.senderFrame.url !== trusted)
      return {ok:false,err:'untrusted_notification_sender'};
    if (!payload || !Object.prototype.hasOwnProperty.call(bodies,payload.kind) ||
        typeof payload.key !== 'string' || !/^[a-zA-Z0-9:_-]{1,240}$/.test(payload.key))
      return {ok:false,err:'invalid_notification'};
    if (seen.has(payload.key)) return {ok:true,duplicate:true};
    if (!Notification.isSupported()) return {ok:false,err:'notifications_unavailable'};
    try {
      const notification = new Notification({title:'Vigil · Agent Center', body:bodies[payload.kind]});
      notification.on('click', () => { onClick(); if (!win.isDestroyed()) win.webContents.send('agent-center:open'); });
      notification.on('close', () => active.delete(notification));
      notification.on('failed', () => active.delete(notification));
      notification.show(); seen.add(payload.key); active.add(notification);
      return {ok:true};
    } catch (_) { return {ok:false,err:'notification_failed'}; }
  });
}
module.exports = {register};
