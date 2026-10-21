const { app } = require('electron');
require('../../../electron/main');
app.whenReady().then(() => {
  const timeout = Math.max(20000, Number(process.env.P15_ELECTRON_TEST_QUIT_MS) || 25000);
  setTimeout(() => app.quit(), timeout).unref();
});
