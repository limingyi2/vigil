// Runs using the OLD installed Electron's Node runtime, before NSIS uninstalls it.
process.noAsar=true; // Back up the archive as bytes, not Electron's virtual directory.
const path=require('path');
const tx=require('./update-transaction');
try {
  const home=process.env.VIGIL_USER_DATA || path.join(process.env.APPDATA,'vigil-client');
  tx.checkpoint(process.argv[2],home,JSON.parse(require('fs').readFileSync(process.argv[3],'utf8')),path.basename(process.execPath));
} catch (_) { console.error('Previous Vigil backup could not be verified; update cancelled.');process.exit(1); }
