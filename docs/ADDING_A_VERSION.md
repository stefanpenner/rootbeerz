# Adding a Node version

A version is one `node.version` tag in `MODULE.bazel`. The module extension in
`runtimes/node/node_versions.bzl` turns each tag into the patched source, the stock control
binaries, the `//runtimes/node:node_<major>` target, and the test targets.

## A new release of a line that is already pinned (for example 22.23.4 to 22.24.0)

```
python3 runtimes/node/tools/add_node_version.py 22.24.0
bazel test //runtimes/node/tests:source_22_test //runtimes/node/tests:versions_test
bazel build //runtimes/node:node_22 //runtimes/node:dist_22 --output_groups=+dist
bazel test //runtimes/node/tests:hermetic_22_test //runtimes/node/tests:network_22_test
```

The tool reads `SHASUMS256.txt` from nodejs.org. It rewrites the tag, the README row, and the
`STATUS.md` row. The new `STATUS.md` row says `untested`, because CI has not run the version.
The patches in `runtimes/node/patches/22/` must still apply. `source_22_test` fails when they do not.
In that case run `python3 runtimes/node/tools/make_patches.py <dir with node-vX.Y.Z trees> runtimes/node/patches`.
It stops when a replacement does not match exactly once, so it never writes a partial patch.

## A new line (for example 28)

```
python3 runtimes/node/tools/add_node_version.py 28.0.0
```

The tool edits `MODULE.bazel`, `runtimes/node/manifest.json`, the README, and `STATUS.md`. It then runs
`runtimes/node/tools/make_patches.py --only 28`. That stops with a message when the line has no entry in
`FAMILIES`. A family is the shape of the trust code in `src/crypto/crypto_context.cc`.
Node 16, 18, and 20 are `legacy`. Node 22 is `modern`. Node 24 and 26 are `current`.

If the line has a new shape:

1. Write a context function in `runtimes/node/tools/make_patches.py`, and add it to `FAMILIES`.
   Keep the semantics of the other families. The bundled list stays empty, and
   `X509_STORE_set_default_paths` runs on every path.
2. Run `python3 runtimes/node/tools/add_node_version.py 28.0.0` again.
3. Run the test chain above. The hermetic tests, including the PEM scan, prove that the binary
   holds no roots, and the network tests prove that the eMudhra hosts fail with the pinned store.

A new line can also fail in the build, after the patches apply. Node 24 and 26 showed three such failures:

- `configure.py` lost `--without-corepack`. `ConfigureFlags` in `runtimes/node/tests/source/patch_test.py` now finds this in the source.
- `tools/v8_gypfiles/abseil.gyp` gives `-framework` to `llvm-libtool-darwin`. The wrapper in `runtimes/node/build_node.py` drops it.
- `configure.py` looks for `cargo` on `PATH`. The driver passes `--v8-disable-temporal-support`.

Pass `--old-v8` when the V8 of the line needs the Clang warning flags and the libc++ 19 patches
(Node 16 and 18 today). Add `OLD_ZLIB_HEADERS` entries in `make_patches.py` for such a line.

## What the tests check

`tests/versions_test.py` fails when the README table, the patch files, or `make_patches.py`
disagree with the tags. `tests/runtimes_test.py` fails when `runtimes/node/manifest.json` disagrees
with the tags. CI reads its matrix from that file. `tests/add_node_version_test.py` tests the tool.

After a build, record the result in `STATUS.md` and in the README table. `versions_test` fails when these two differ.

## The daily version check

`.github/workflows/version-bumps.yml` runs `//tools/bump` each day. The tool reads the Node lines from
`MODULE.bazel` and the other runtimes from `runtimes/*.json`. It opens one pull request for each new release.
The branch name is `bump/<runtime>-<line>-<version>`.

To see the updates and write nothing, run:

```
bazel run //tools/bump -- --dry-run
```

The tool obeys these rules:

- A pull request for the branch exists, open or closed: the tool skips the release. To refuse a release, close its pull request.
- The branch exists with no pull request: the tool opens the pull request, and makes no second commit.
- The patches fail, or the release starts a new line: the tool opens a draft with the title prefix `[human work]`. The body lists the work.
- One failure does not stop the other updates. The job then ends with an error.

The token is `GITHUB_TOKEN`, and these limits apply:

- The repository setting "Allow GitHub Actions to create and approve pull requests" must be on. When it is off, the tool makes the branch, and GitHub refuses the pull request. The next run after you turn on the setting opens the pull request.
- GitHub does not start the checks of such a pull request until a person with write access selects "Approve workflows to run". The `pr-approval` environment then asks for its own approval. The job does not dispatch `ci.yml`, because the non-PR jobs have no `pr-approval` gate.
- `GITHUB_TOKEN` cannot write a file in `.github/workflows/`. If a tool changes such a file, the pull request omits the change, and the body tells you to make it. `tests/versions_test.py` can fail until you do.
- The tool makes each commit through the REST API with no author, so GitHub signs it. The tool stops if GitHub reports the commit as not verified.
