# eMudhra test domains

`runtimes/node/tests/network_test.py` uses these hosts. This file records where they come from and what they did.

## Why these hosts

Mozilla, Google, and Apple removed or blocked the emSign roots after a key compromise at eMudhra.
A build that trusts its own bundled roots keeps trusting emSign until the next Node release.
Most Node releases in these lines still bundle the emSign roots.

## Sources

- https://bugzilla.mozilla.org/show_bug.cgi?id=2083449 (comment 25)
- https://bugzilla.mozilla.org/show_bug.cgi?id=2018979
- https://sslboard.com/blog/emudhra-revoked-certificate.md/
- https://www.bleepingcomputer.com/news/security/mozilla-google-apple-block-compromised-emudhra-ca-certificates/
- https://www.ssl.com/article/check-your-certificates-before-october-27-chrome-is-removing-an-emudhra-root/amp/
- https://support.apple.com/en-us/149555
- https://thehackernews.com/2026/10/emudhra-to-exit-global-ssltls-business.html

## Hosts, as probed on 2026-10-30

All `emsign.com` hosts below resolved to 103.156.134.46.

| Host | Root | State |
|---|---|---|
| `testevg1.emsign.com`, `testovg1.emsign.com` | emSign G1 | valid |
| `testevg1r.emsign.com`, `testovg1r.emsign.com` | emSign G1 | revoked |
| `testevg1e.emsign.com`, `testovg1e.emsign.com` | emSign G1 | expired on 2026-10-16 |
| `testevg3.emsign.com`, `testovg3.emsign.com` | emSign G3 | valid |
| `dv-g3b-emudhra.we-have-a-problem.com`, `ov-g3c-emudhra.we-have-a-problem.com` | emSign G3 | demonstration hosts |

These names returned NXDOMAIN: `testdvg3`, `testevs`, `testovs`, `testdvs`, `testev`, `test`, `valid`,
`testsslev`, `testevsslrootca`, `testovsslrootca` on `emsign.com`, and `demo.emudhra.com`, `test.emudhra.com`.
`qa.emudhra.com` now uses a Sectigo certificate, so the tests do not use it.

## How the tests read a result

Each probe has one of these outcomes.

- **trusted**: The handshake and the certificate check succeeded.
- **store rejected**: The handshake reached certificate verification, and verification failed. This outcome is the pass for rootbeerz.
- **store independent**: The certificate fails for a reason that no store changes. An example is an expired certificate.
- **Inconclusive**: DNS or the network failed. The test reports an error. An Inconclusive result is never a pass.

eMudhra is leaving the TLS business, so these hosts may stop. The tests then report Inconclusive, and this file needs new hosts.

## What Stock Node does

| Line | Bundled roots | emSign roots | G1 hosts | G3 hosts |
|---|---|---|---|---|
| 16, 18, 20 | 143, 150, 144 | 4 | trusted | trusted |
| 22 | 116 | 1 (G1) | trusted | rejected |

Stock Node 22 also blocks the demonstration hosts by public key (`kBlockedSPKIs`), and reports `CERT_REVOKED`.

## The gap that remains

rootbeerz fails against these hosts when the local store has no emSign root.
rootbeerz connects to the G1 hosts when the local store has emSign G1.
On 2026-10-30, the curl Mozilla bundle and macOS `/etc/ssl/cert.pem` both contained emSign roots.
`trust/mozilla-no-emsign.pem` removes the emSign root for this reason.
OpenSSL does not check revocation, so the revoked hosts do not fail for their revocation.
