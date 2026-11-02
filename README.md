# rootbeerz

> **Why the name.** Root certs are removed from the runtimes. Only the root store on your system is left. So: rootbeerz.

rootbeerz builds runtimes with their bundled root certificates removed.

Node.js 16, 18, 20, 22, 24, and 26 with no bundled root certificates.
Each binary trusts only the local (system) trust store.

Stock Node trusts its own list of roots. Ours trusts what your host trusts.

## Example

Both commands use a store that has no emSign roots.
`testevg1.emsign.com` is an eMudhra test host. Mozilla, Google, and Apple distrust its root.

```sh
export SSL_CERT_FILE=trust/mozilla-no-emsign.pem

# stock Node
node -p 'require("tls").rootCertificates.length'    # 119
node trust/testing/probe.js https://testevg1.emsign.com     # {"trusted":true,"status":302}

# rootbeerz
./node -p 'require("tls").rootCertificates.length'  # 0
./node trust/testing/probe.js https://testevg1.emsign.com   # {"trusted":false,"code":"UNABLE_TO_GET_ISSUER_CERT_LOCALLY",...}
./node trust/testing/probe.js https://example.com           # {"trusted":true,"status":200}
```

Stock Node ignores your store and trusts emSign.
Our Node uses your store, so it rejects emSign and still trusts valid hosts.

## Why

You cannot remove a root from the list in stock Node.
You must wait for a new Node release.
Here, the host store is the only source of trust.
Remove a root from the host, and the next Node process stops trusting it.

## Trust model

- `tls.rootCertificates` is empty.
- `--use-bundled-ca` has no effect.
- OpenSSL reads the store from `SSL_CERT_FILE`, `SSL_CERT_DIR`, or `/etc/ssl`.
- `NODE_EXTRA_CA_CERTS` still adds roots. This is by design, for now.
- `--use-system-ca` and `NODE_USE_SYSTEM_CA=1` add the system store of the operating system to the local store (Node 24 and 26).
- `tls.getCACertificates('default')` does not show the local store (Node 24 and 26).
  The list can be empty while `SSL_CERT_FILE` gives trust.
- `tls.setDefaultCACertificates()` replaces the store. A program that passes that empty list removes its own trust.
- Anyone who can set these variables or write these paths controls trust.
- A host with no store has no trust. Each TLS connection fails.
- This project does not remove a bad root from your host.
- OpenSSL does not check revocation.

## Versions

`MODULE.bazel` pins each version, one `node.version` tag per line.
Each checksum comes from the release `SHASUMS256.txt`.

| Line | Version | Source sha256 | macOS arm64 | Linux x86_64 |
|---|---|---|---|---|
| 26 | 26.13.0 | `986ead18ef18256831bae3e57d94aef680a7b84cfd4044f3e8471f833fbd7191` | pass | untested |
| 24 | 24.22.2 | `b2f5443a34a38d863e87c0f5f80c03630fc16917dfe16f26c1cec44c0b542959` | untested | untested |
| 22 | 22.23.4 | `4dd79de8cdc85d65cb32f48a75aee1067bf00bf94b96ecc8e4c2f82f6f0272c2` | pass | pass |
| 20 | 20.20.2 | `8cb85a81f75169eb811f7b2512cf17a646826430debbe016a7461f31e286fdef` | pass | pass |
| 18 | 18.20.8 | `ec60a6d2344ef9e1f093991ca1bb6bbe92c61c29d1762c4b99e08f87dbb91e2f` | pass | pass |
| 16 | 16.20.2 | `33188eb11b977113adb65b2e09d71bddd63f12168ba73ceadae6c27938dc9e93` | pass | pass |

"Pass" means the source, hermetic, and network tests passed on a built binary.
"Untested" means that the hermetic and network tests have not run for this pair. The patches apply and the source tests pass.
[STATUS.md](STATUS.md) has the evidence.
Node 16 and 18 are past end of life.
Node 26 has no Temporal API here, because the build declares no Rust toolchain. A stock Node 26 can have it.

To add a version, run `python3 runtimes/node/tools/add_node_version.py <version>`.
See [docs/ADDING_A_VERSION.md](docs/ADDING_A_VERSION.md).

## JDK

The JDK line 25 build has an empty `cacerts`. See [runtimes/jdk/README.md](runtimes/jdk/README.md).
Java does not read `SSL_CERT_FILE`. Give the JVM a trust store with a system property.

## Deno

`//runtimes/deno:deno_2` builds Deno 2.9.8 from source with no `webpki-roots`.
The binary uses the system store only. `DENO_TLS_CA_STORE=mozilla` also selects the system store.
macOS arm64 passes. Linux x86_64 is untested.

The binary holds build paths from the upstream JavaScript loader, so two builds can differ.
`deno compile` and `deno desktop` download stock upstream binaries, which hold the Mozilla roots.

Pins are in `runtimes/deno/manifest.json` and `runtimes/deno/pins.bzl`.

## How it works

1. Bazel builds Node from pinned source with a pinned toolchain.
2. Patches empty `src/node_root_certs.h` and force the local store.
3. Tests check that the binary starts and trusts valid hosts.
4. Tests check that it fails against the eMudhra test hosts when the local store has no emSign roots.

`runtimes/node/patches/<line>/` holds the patches.
`runtimes/node/tools/make_patches.py` makes them from pristine source.

## Build

Install Bazel. Then run:

```sh
bazel build //runtimes/node:node_22 //runtimes/node:dist_22 --output_groups=+dist
```

Change `22` to build another line.
The binary needs macOS 14 or later, or Linux glibc 2.25 or later.

On Linux, you can build in the pinned Docker image:

```sh
docker build --platform linux/amd64 -f runtimes/node/image/Dockerfile -t rootbeerz-linux .
docker run --rm --platform linux/amd64 \
  --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
  --security-opt systempaths=unconfined \
  -v "$PWD":/src:ro -v rootbeer-bazel-cache:/cache rootbeerz-linux \
  bazel --output_user_root=/cache build --symlink_prefix=/ --jobs=1 \
  //runtimes/node:node_22 //runtimes/node:dist_22 --output_groups=+dist
```

On Apple Silicon, Docker emulates x86_64. It is slow.

Each Bazel action runs in `linux-sandbox`. The sandbox needs user namespaces and a `/proc` mount.
The default Docker profile blocks both. The three `--security-opt` flags remove that block.
Without them, the build fails. It does not fall back to an unsandboxed process.

## Test

```sh
bazel test //runtimes/node/tests:source_22_test //runtimes/node/tests:hermetic_22_test //runtimes/node/tests:network_22_test
```

- `source`: checks the patched source.
- `hermetic`: runs the binary against a local TLS server.
- `network`: connects to public hosts and the eMudhra hosts. It needs the network.
- `system_ca`: proves that the binary uses the system store. With no store variable, it trusts a public host.
  A control with an empty store rejects the same host. Each runtime has this test:
  `//runtimes/<runtime>/tests:system_ca_<line>_test`. The JDK test runs on macOS only.

A network error is a test error. It is never a pass.

## Artifacts

`bazel run //:dist_22` copies one line to `./dist/`.
`bazel run //:dist` copies all lines.
Each archive is `node-v<version>-<os>-<arch>.tar.gz` with a `.sha256` file.
Reproducibility is proven for one pair only. Node 22 on macOS gave the same bytes in two builds.
Node 16 on macOS did not. [docs/HERMETICITY.md](docs/HERMETICITY.md#reproducibility) has the results.

## Download

A tag `v*` publishes a GitHub Release from the CI artifacts of the tagged commit.
The release builds nothing. Tag a commit on `main` only after its CI run passes.

Each file is `<runtime>-<version>-<os>-<arch>.tar.gz`. `SHA256SUMS` lists the checksums.

```sh
sha256sum -c SHA256SUMS --ignore-missing
```

Each CI run also keeps its artifacts. Open the run on the Actions tab to download them.

## CI

- A push to `main` or a manual run builds Node 16, 18, 20, 22, 24, and 26 on macOS 14 and Linux.
  A pull request builds only the oldest and the newest line.
  Each build job runs the tests of its line and the `system_ca` test, then uploads the archive.
- CI reads the build matrix from `runtimes/*/manifest.json`. A new manifest adds its runtime to CI.
  `tests/runtimes_test.py` checks the manifests.
- A pull request waits for the owner to approve it in the `pr-approval` environment.
- A pull request never uses a self-hosted runner. `tests/ci_test.py` checks this.
- A push to `main` uses the runner in the `CI_RUNS_ON` variable. Without it, GitHub hosts the job.
- Bazel is pinned and checked by sha256. Each action has a commit SHA.

## Python certifi and other runtimes

`//runtimes/certifi:certifi` builds a wheel named `certifi`. It replaces stock certifi and holds no certificate.
It has the same `where()`, `contents()`, and `__version__`.

`where()` returns the first file that it finds:

1. The file in `SSL_CERT_FILE`.
2. The default file from `ssl.get_default_verify_paths()`.
3. A known OS file. `runtimes/certifi/src/certifi/core.py` lists these files.

If it finds no file, `where()` raises `FileNotFoundError`. A missing `SSL_CERT_FILE` file also raises it.

Limits of the wheel:

- The API returns one file. It cannot use a store that is only a directory, a keychain, or the Windows store.
- `requests` calls `where()` when it loads. With no store, the import of `requests` fails.
- pip has its own copy of certifi. This wheel does not change pip.

Go, Ruby, and .NET use the system store by default. Rust and PHP depend on the crate or the library.
This table comes from upstream docs. This project did not test these runtimes.

| Runtime | Default | Exceptions |
| --- | --- | --- |
| Go | [`crypto/x509`](https://pkg.go.dev/crypto/x509) uses the system roots or the platform verifier if `Roots` is nil. | A program can set `Roots`. [`x509roots/fallback`](https://pkg.go.dev/golang.org/x/crypto/x509roots/fallback) embeds roots for use if no system store exists. |
| Ruby | [`Store#set_default_paths`](https://docs.ruby-lang.org/en/master/OpenSSL/X509/Store.html) uses the system certificates. | [RubyInstaller](https://github.com/oneclick/rubyinstaller2/blob/master/resources/ssl/README-SSL.md) on Windows ships a CA list from Mozilla. |
| Rust | There is no default. The TLS crate decides. [`rustls-native-certs`](https://docs.rs/rustls-native-certs) reads the platform store. [`native-tls`](https://docs.rs/native-tls) uses the platform TLS library. | [`webpki-roots`](https://docs.rs/webpki-roots) is a compiled-in copy of the Mozilla roots. `rustls` with this crate does not use the system store. |
| PHP | [`openssl.cafile`](https://www.php.net/manual/en/openssl.configuration.php) and [`curl.cainfo`](https://www.php.net/manual/en/curl.configuration.php) are empty by default. These pages do not state the fallback. We expect the OpenSSL and libcurl defaults to apply, and we did not confirm this. | [`composer/ca-bundle`](https://github.com/composer/ca-bundle) can fall back to a bundled file. |
| .NET | [OS libraries](https://learn.microsoft.com/en-us/dotnet/standard/security/cross-platform-cryptography) supply the roots: the Windows store, `SecTrustSettings` on macOS, and OpenSSL paths on Linux. | A program can set [`CustomTrustStore`](https://learn.microsoft.com/en-us/dotnet/api/system.security.cryptography.x509certificates.x509chainpolicy.customtruststore). |

## Bun

`//runtimes/bun:bun_1` builds Bun from source with no compiled-in Mozilla roots.
The build deletes the root files and reads only the system store.
Only macOS arm64 is pinned. The build has no LTO, so the binary differs from an upstream release.

- `--use-bundled-ca` has no bundled set to use.
- `--use-system-ca` has no extra effect.
- `tls.rootCertificates` is empty.
- On macOS, `SSL_CERT_FILE` or `SSL_CERT_DIR` replaces the keychain.

## Known gaps

- Not fully hermetic. On macOS, the SDK and POSIX tools come from the host.
- A host store with emSign roots makes the binary trust emSign hosts.

[docs/HERMETICITY.md](docs/HERMETICITY.md) lists what is pinned and what is not.
[docs/EMUDHRA.md](docs/EMUDHRA.md) describes the eMudhra test hosts.

## Names

The repository and the Bazel module are `rootbeerz`.
Each runtime keeps its upstream name: targets are `node_22`, `deno_2`, `bun_1`, `jdk_25`, and `certifi`.
Some internal names still use `rootbeer`: the `ROOTBEER_*` test variables and the `rootbeerz_*` C++ features.
The repository was `systemca-nodejs`, then `rootbeer-node`. GitHub redirects the old addresses.

## License

MIT. See [LICENSE](LICENSE).
