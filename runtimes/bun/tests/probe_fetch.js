// Prints one JSON line that describes the outcome of a fetch. Usage: bun probe_fetch.js <url>
// Bun runs fetch and node:https on different code paths, so the tests probe both.
const TIMEOUT_MS = 20000;

try {
  const response = await fetch(process.argv[2], { signal: AbortSignal.timeout(TIMEOUT_MS) });
  console.log(JSON.stringify({ trusted: true, status: response.status }));
} catch (error) {
  console.log(JSON.stringify({ trusted: false, code: error.code || 'UNKNOWN', message: error.message }));
}
process.exit(0);
