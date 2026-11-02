// Starts a TLS server on loopback, connects to it with the default CA store,
// and prints one JSON line. No test here needs the network.
// Usage: node loopback.js <leaf.pem> <leaf.key>
'use strict';
const fs = require('fs');
const tls = require('tls');

const [certFile, keyFile] = process.argv.slice(2);

function report(result) {
  console.log(JSON.stringify(result));
  process.exit(0);
}

const server = tls.createServer({
  cert: fs.readFileSync(certFile),
  key: fs.readFileSync(keyFile),
}, (socket) => socket.on('error', () => {}));
server.on('tlsClientError', () => {});

server.listen(0, '127.0.0.1', () => {
  const client = tls.connect({
    host: '127.0.0.1',
    port: server.address().port,
    servername: 'localhost',
  });
  client.on('secureConnect', () => report({ trusted: client.authorized }));
  client.on('error', (error) => report({ trusted: false, code: error.code || 'UNKNOWN' }));
});
