# rootbeerz JDK

This file describes the JDK build and its trust model.
The build uses the OpenJDK source of the newest LTS line, with an empty `cacerts`.
The JDK has no bundled root.

## Java does not read `SSL_CERT_FILE`

This is a limit of the JDK trust model. The Node build does not have this limit.

The JVM picks one trust store in this order:

1. The system property `javax.net.ssl.trustStore`.
2. The file `lib/security/jssecacerts`.
3. The file `lib/security/cacerts`.

The rootbeerz JDK has no `jssecacerts`, and its `cacerts` holds 0 entries.
JDK 25 writes that file as a 24-byte PKCS12 store. The driver stops the build unless the file has these exact bytes.
With no flag, each TLS connection fails with this message:

```
the trustAnchors parameter must be non-empty
```

The operator must give the JVM a store. The JVM has no default path to the system store.

## How the system store reaches the JVM

**Linux, or a store file on any OS.** Give the path, the type, and the password if the store has one:

```sh
java -Djavax.net.ssl.trustStore=/path/to/store.p12 \
     -Djavax.net.ssl.trustStoreType=PKCS12 \
     -Djavax.net.ssl.trustStorePassword=changeit \
     App
```

A PEM file is not a keystore. Use `keytool -importcert` to make a store from each PEM certificate.

**macOS.** Give a Keychain store type, and set the store path to `NONE`:

```sh
java -Djavax.net.ssl.trustStore=NONE \
     -Djavax.net.ssl.trustStoreType=KeychainStore-ROOT \
     App
```

Use `KeychainStore-ROOT` to read the system roots.
`KeychainStore` reads the user keychain items only.
`getAcceptedIssuers` on stock Temurin 25.0.5, on one macOS arm64 host, gave these counts:

| Flags | Anchors |
|---|---|
| `trustStore=NONE`, `trustStoreType=KeychainStore` | 6 |
| `trustStoreType=KeychainStore`, no `trustStore` flag | 6 |
| `trustStore=NONE`, `trustStoreType=KeychainStore-ROOT` | 157 |

The count depends on the host.
The tests require more than 50 anchors with `KeychainStore-ROOT`.

## Inputs

The driver `runtimes/jdk/build_jdk.py` runs as one Bazel action with no network.
Each rung has the meaning that `docs/HERMETICITY.md` gives.

| Input | Source | Rung |
|---|---|---|
| OpenJDK source, tag `jdk-25.0.5-ga` | `runtimes/jdk/manifest.json`, sha256 computed here | 1 |
| Boot JDK and stock control, Temurin 25.0.5+8 | Adoptium, sha256 in `runtimes/jdk/manifest.json` | 0 |
| `lib/shaders.metallib` | Copied from the boot JDK. See "Metal shaders". | 0 |
| GNU make 4.4.1, bash 5.2.37, perl 5.40.2, m4 1.4.20, autoconf 2.72 | Upstream source, sha256 in `runtimes/jdk/manifest.json`, built in the action | 1 |
| Clang, lld, LLVM tools 19.1.7, with `lipo`, `otool`, `install_name_tool`, `dsymutil`, `c++filt`, `objdump`, and `ld` as `ld64.lld` | The same pin as the Node build | 1 |
| Archive unpack | Python `tarfile` with the data filter. The host `tar` is not used. | 1 |
| macOS SDK | Host, declared as inputs by content | 6 |
| `codesign`, `mig`, `dtrace`, `xattr`, `arch`, `sw_vers`, `SetFile`, `file`, `tar`, `gzip`, `gunzip`, `zip`, `unzip` | Host. Each is a `HOST_TOOLS` row with a reason. | 6 |
| `/bin/sh` and the POSIX tools that `HOST_TOOLS` lists | Host | 6 |

### Host tools

The action `PATH` has three directories, in this order:

1. The wrappers: the pinned LLVM tools, and the `metal` and `autoconf` shims.
2. The tools that the action builds from pinned source.
3. A directory of links. It has one link for each row of `HOST_TOOLS` in `build_jdk.py`.

`/usr/bin`, `/bin`, and `/usr/sbin` are not on `PATH`.
A tool that is not a row is not found, so a new host tool fails the build.
Each row has one reason. `runtimes/jdk/tests/tools_test.py` checks that no row names a pinned tool.

make and bash configure with the host `/bin/sh`. Each later step uses the pinned bash.
After configure, the driver reads `spec.gmk`. A tool path on a host directory that is not a row stops the build.

## Metal shaders

The JDK configure requires the Xcode Metal tools on macOS.
The Metal Toolchain is an optional Xcode component. The build must not download it.
The driver puts two shims on `PATH`. `metal` writes an empty placeholder. `metallib` copies `lib/shaders.metallib` from the pinned boot JDK.

So the JDK does not compile these shaders from source. The bytes come from Temurin 25.0.5+8, which has the same source tag.
`hermetic_25_test` checks that the two files have the same sha256.

## Autoconf shim

On Darwin 27 the `autoconf` script hung in its `exec` of `autom4te`. `kill -9` did not stop the hung `perl` process, and `ps` on that process also hung.
A direct call to `autom4te` works. The driver puts a shim named `autoconf` on `PATH`. The shim calls `autom4te` with the two options that the script adds.

## Results

These results are from one macOS arm64 host, Darwin 27.

```
$ keytool -list -cacerts
Keystore type: PKCS12
Your keystore contains 0 entries
```

- `hermetic_25_test` ran 14 tests and passed. It has no skip on macOS. It includes the Keychain read.
- `teeth_25_test` ran 2 tests and passed. It proves that the assertions fail on the wrong JDK.
- `network_25_test` ran 5 tests and passed. Stock trusts `testevg1.emsign.com`. Ours rejects it with `trust/mozilla-no-emsign.pem`.
- `otool -L` shows only `/usr/lib` and `/System/Library/Frameworks` outside the JDK. `libjvm.dylib` links the OS `libc++.1.dylib`.
- A scan of the image found no `/Users/stef`, `_bazel_stef`, or `darwin-sandbox` string.

These items did not run:

- Linux x86_64. The Bazel target exists for macOS arm64 only.
- macOS x86_64 and Linux arm64.
- A second build to compare bytes. Reproducibility is not measured.
- Hosted Actions.

## Update the JDK

`runtimes/jdk/manifest.json` holds each pin. `runtimes/jdk/extension.bzl` reads that file. `MODULE.bazel` holds no JDK pin.
