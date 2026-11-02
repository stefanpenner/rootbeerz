// Prints one JSON line that describes the outcome of an HTTPS request.
// Usage: node probe.js <url>
// The caller classifies the outcome. This script only reports what Node saw.
'use strict';
const https = require('https');

const TIMEOUT_MS = 20000;

function report(result) {
  console.log(JSON.stringify(result));
  process.exit(0);
}

const request = https.get(process.argv[2], { agent: false }, (response) => {
  report({ trusted: true, status: response.statusCode });
});
request.setTimeout(TIMEOUT_MS, () => {
  request.destroy(Object.assign(new Error('timeout'), { code: 'ETIMEDOUT' }));
});
request.on('error', (error) => {
  report({ trusted: false, code: error.code || 'UNKNOWN', message: error.message });
});
