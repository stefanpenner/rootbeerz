"""Runs the Deno source tests on the source without the patches. The tests must fail there."""

import os
import tempfile
import unittest

import source_test
import teethlib
from trustlib import path_from_env

SOURCE_FILES = {
    "Cargo.lock": "CARGO_LOCK",
    "Cargo.toml": "CARGO_TOML",
    "cli/lib/args.rs": "CLI_ARGS_RS",
    "ext/node/Cargo.toml": "NODE_CARGO_TOML",
    "ext/node/ops/tls.rs": "NODE_TLS_RS",
    "ext/tls/Cargo.toml": "TLS_CARGO_TOML",
    "ext/tls/lib.rs": "TLS_LIB_RS",
    "libs/eszip/Cargo.toml": "ESZIP_CARGO_TOML",
}
CASES = (
    source_test.Lockfile,
    source_test.Manifests,
    source_test.CliStore,
    source_test.TlsCrate,
    source_test.NodeCompatibility,
)


def test_names():
    return {
        name
        for case in CASES
        for name in unittest.defaultTestLoader.getTestCaseNames(case)
    }


def read(env):
    with open(path_from_env(env), encoding="utf-8") as stream:
        return stream.read()


def line_patches():
    return sorted(path for path in os.environ["DENO_PATCHES"].split() if path.endswith(".patch"))


class SourceWithoutPatches(unittest.TestCase):
    def test_each_source_test_fails_on_the_upstream_source(self):
        contents = {path: read(env) for path, env in SOURCE_FILES.items()}
        for name in reversed(line_patches()):
            with open(name, encoding="utf-8") as stream:
                for path, diff in teethlib.split_diff(stream.read()).items():
                    if path in contents:
                        contents[path] = teethlib.reverse_patch(contents[path], diff)
        with tempfile.TemporaryDirectory() as directory:
            for path, env in SOURCE_FILES.items():
                target = os.path.join(directory, env)
                with open(target, "w", encoding="utf-8") as stream:
                    stream.write(contents[path])
                os.environ[env] = target
            failed = teethlib.failed_tests(*CASES)
        self.assertEqual(
            sorted(test_names() - failed), [], "these tests pass on the unpatched source")


class PatchedSource(unittest.TestCase):
    def test_each_source_test_passes_on_the_patched_source(self):
        self.assertEqual(sorted(teethlib.failed_tests(*CASES)), [])


if __name__ == "__main__":
    unittest.main()
