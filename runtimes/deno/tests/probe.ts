// Fetches one URL and prints one JSON line. The tests read this line.
// Deno gives a rustls error text, so this file maps it to the codes in tests/trustlib.py.

const CERTIFICATE_CODES: [RegExp, string][] = [
  [/UnknownIssuer/, "UNABLE_TO_GET_ISSUER_CERT_LOCALLY"],
  [/Expired/, "CERT_HAS_EXPIRED"],
  [/NotValidForName/, "ERR_TLS_CERT_ALTNAME_INVALID"],
];

function textOf(error: unknown): string {
  let text = String(error);
  for (let cause = (error as Error)?.cause; cause; cause = (cause as Error)?.cause) {
    text += ` ${cause}`;
  }
  return text;
}

function codeOf(text: string): string {
  for (const [pattern, code] of CERTIFICATE_CODES) {
    if (pattern.test(text)) return code;
  }
  return "NO_DECISION";
}

try {
  const response = await fetch(Deno.args[0], { signal: AbortSignal.timeout(30000) });
  await response.body?.cancel();
  console.log(JSON.stringify({ trusted: true, code: null }));
} catch (error) {
  const detail = textOf(error);
  console.log(JSON.stringify({ trusted: false, code: codeOf(detail), detail }));
}
