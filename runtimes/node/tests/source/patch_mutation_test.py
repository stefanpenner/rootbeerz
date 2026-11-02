"""Runs the source tests on the source without the patches. The tests must fail there."""

import os
import tempfile
import unittest

import patch_test
import teethlib
from trustlib import path_from_env

MAJOR = int(os.environ["NODE_MAJOR"])
SOURCE_FILES = {
    "src/node_root_certs.h": "ROOT_CERTS_H",
    "src/crypto/crypto_context.cc": "CRYPTO_CONTEXT_CC",
    "src/node_options.cc": "NODE_OPTIONS_CC",
    "tools/snapshot/node_mksnapshot.cc": "NODE_MKSNAPSHOT_CC",
    "deps/openssl/openssl_common.gypi": "OPENSSL_COMMON_GYPI",
}
PATCH_TESTS = {
    "test_header_holds_no_certificate",
    "test_header_is_not_included",
    "test_array_is_gone",
    "test_store_load_has_no_condition",
    "test_flags_do_not_conflict",
    "test_bundled_flag_has_no_effect_on_the_store",
    "test_no_directory_uses_the_macos_path",
}
# Node 16 and 18 get the predictable flag from a patch. The fixed seed is upstream code.
OLD_V8_TESTS = {"test_generation_is_predictable"}
# Node 24 and 26 load each bundled root in a loop in NewRootCertStore.
CURRENT_TESTS = {"test_bundled_roots_do_not_enter_the_store", "test_bundled_initializer_is_empty"}


def line_patches():
    paths = os.environ["PATCHES"].split()
    return sorted(path for path in paths if f"patches/{MAJOR}/" in path)


class SourceWithoutPatches(unittest.TestCase):
    def test_every_source_test_for_a_patch_fails(self):
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
            failed = teethlib.failed_tests(
                patch_test.RootCertificateHeader, patch_test.CryptoContext, patch_test.Snapshot,
                patch_test.Options, patch_test.OpenSslDirectory)
        expected = PATCH_TESTS | (OLD_V8_TESTS if MAJOR < 20 else set())
        expected |= CURRENT_TESTS if MAJOR >= 24 else set()
        self.assertEqual(sorted(expected - failed), [], "these tests pass on the unpatched source")


def read(env):
    with open(path_from_env(env), encoding="utf-8") as stream:
        return stream.read()


if __name__ == "__main__":
    unittest.main()
