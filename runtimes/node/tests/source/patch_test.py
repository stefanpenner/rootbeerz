"""Tests the patched source. These tests prove the patch without a compile."""

import importlib.util
import os
import re
import shutil
import tempfile
import unittest

from trustlib import path_from_env


class SourceCase(unittest.TestCase):
    def assertAbsent(self, needle, text):
        # assertNotIn prints the whole file on failure.
        self.assertFalse(needle in text, f"found in source: {needle!r}")


def read(env_name):
    with open(path_from_env(env_name), encoding="utf-8") as stream:
        return stream.read()


class RootCertificateHeader(SourceCase):
    def test_header_holds_no_certificate(self):
        self.assertAbsent("-----BEGIN", read("ROOT_CERTS_H"))


class CryptoContext(SourceCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read("CRYPTO_CONTEXT_CC")

    def test_header_is_not_included(self):
        self.assertAbsent('#include "node_root_certs.h"', self.text)

    def test_array_is_gone(self):
        self.assertAbsent("root_certs[", self.text)

    def test_store_load_has_no_condition(self):
        # Node 24 and 26 read the flag into a local first, so match any form of the condition.
        conditional = re.compile(r"if \([^)]*ssl_openssl_cert_store\) \{\n\s*(CHECK_EQ\(1, )?X509_STORE_set_default_paths")
        self.assertIsNone(conditional.search(self.text), "the local store load has a condition")
        self.assertIn("X509_STORE_set_default_paths(store)", self.text)

    def test_bundled_roots_do_not_enter_the_store(self):
        self.assertIsNone(
            re.search(r"GetBundledRootCertificates\(\)\) \{\n\s*CHECK_EQ\(1, X509_STORE_add_cert", self.text),
            "bundled roots enter the store")

    def test_bundled_initializer_is_empty(self):
        # The header is empty too, and this check holds if a later release fills it in another way.
        if "InitializeBundledRootCertificates" not in self.text:
            self.skipTest("this line has no initializer")
        body = self.text.split("InitializeBundledRootCertificates() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("return {};", body)
        self.assertAbsent("PEM_read_bio_X509", body)


class Snapshot(SourceCase):
    """The startup snapshot is part of the binary. V8 must not take a random seed or a
    schedule from the machine, or two builds of one source make two different binaries."""

    @classmethod
    def setUpClass(cls):
        cls.text = read("NODE_MKSNAPSHOT_CC")

    def test_generation_is_predictable(self):
        self.assertTrue("--predictable" in self.text or "kGeneratePredictableSnapshot" in self.text,
                        "node_mksnapshot does not ask V8 for a predictable snapshot")

    def test_generation_has_a_fixed_seed(self):
        # Node 20 and 22 set the seed in node.cc, for the flag above.
        self.assertTrue("--random_seed=" in self.text or "kGeneratePredictableSnapshot" in self.text,
                        "node_mksnapshot does not fix the random seed")


class Options(SourceCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read("NODE_OPTIONS_CC")

    def test_flags_do_not_conflict(self):
        self.assertAbsent("either --use-openssl-ca or --use-bundled-ca", self.text)

    def test_bundled_flag_has_no_effect_on_the_store(self):
        self.assertAbsent('ImpliesNot("--use-bundled-ca"', self.text)


class ConfigureFlags(SourceCase):
    """configure gives an unknown flag to gyp, and only a real build then shows the failure."""

    def test_this_release_accepts_every_flag(self):
        spec = importlib.util.spec_from_file_location("build_node", path_from_env("BUILD_NODE"))
        driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)
        text = read("CONFIGURE_PY")
        with tempfile.TemporaryDirectory() as tree:
            shutil.copyfile(path_from_env("CONFIGURE_PY"), os.path.join(tree, "configure.py"))
            flags = driver.configure_flags(tree)
        self.assertEqual([flag for flag in flags if f"'{flag}'" not in text], [])


class OpenSslDirectory(SourceCase):
    def test_no_directory_uses_the_macos_path(self):
        self.assertAbsent("/System/Library/OpenSSL", read("OPENSSL_COMMON_GYPI"))


if __name__ == "__main__":
    unittest.main()
