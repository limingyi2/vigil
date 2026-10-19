'use strict';

// The isolated plugin UI host is launched as a separate Electron process.
// The normal app keeps the original Core main process implementation.
if (process.argv.includes('--vigil-plugin-ui-host')) {
  require('./plugin-ui-child-host');
} else {
  require('./core-main');
}
