"""Tests that need no network. Bazel can cache these tests."""

import crypto_bundle
import os
import re
import subprocess
import sys
import tempfile
import unittest

from trustlib import evaluate, node_env, path_from_env, run_node, store_env

ROOTBEER = path_from_env("ROOTBEER_NODE")
STOCK = path_from_env("STOCK_NODE")
VERSION = os.environ["NODE_VERSION"]
MAJOR = int(VERSION.split(".")[0])
LOOPBACK = path_from_env("LOOPBACK_JS")
CA = path_from_env("TEST_CA")
LEAF = path_from_env("TEST_LEAF")
LEAF_KEY = path_from_env("TEST_LEAF_KEY")
EMPTY = path_from_env("EMPTY_STORE")
READOBJ = path_from_env("READOBJ")
ALLOWED_LIBRARIES = re.compile(
    r"^(/usr/lib/libSystem\.B\.dylib"
    r"|/usr/lib/libobjc\.A\.dylib"
    r"|/System/Library/Frameworks/[^ ]+"
    r"|(ld-linux-x86-64|libc|libdl|libgcc_s|libm|libpthread|librt)\.so\.[0-9]+)$"
)

# The first body line of any DER certificate starts with MII in base64. The marker does not
# come from the stock binary, so a root that stock never shipped is found too.
PEM_CERTIFICATE = re.compile(rb"-----BEGIN CERTIFICATE-----\r?\nMI[A-Za-z0-9+/]")
# The oldest glibc that the Linux sysroot ships, and the floor that the README states.
GLIBC_FLOOR = (2, 31)

ROOT_COUNT = "require('tls').rootCertificates.length"
BUNDLED_FLAG = "--use-bundled-ca"


def loopback(node, *flags, env):
    return evaluate_loopback(run_node(node, *flags, LOOPBACK, LEAF, LEAF_KEY, env=env))


def evaluate_loopback(result):
    if result.returncode != 0:
        raise AssertionError(f"loopback exited {result.returncode}: {result.stderr}")
    return crypto_bundle.parse_line(result.stdout)


class ProcessStarts(unittest.TestCase):
    def test_version_matches_pin(self):
        result = run_node(ROOTBEER, "-v")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"v{VERSION}")

    def test_process_versions(self):
        versions = evaluate(ROOTBEER, "process.versions")
        self.assertEqual(versions["node"], VERSION)
        self.assertIn("openssl", versions)
        self.assertIn("v8", versions)

    def test_runs_javascript(self):
        self.assertEqual(evaluate(ROOTBEER, "[1, 2].map((n) => n * 2)"), [2, 4])


class StockControl(unittest.TestCase):
    """These tests prove that the assertions on rootbeer can fail."""

    def test_stock_has_bundled_roots(self):
        self.assertGreater(evaluate(STOCK, ROOT_COUNT), 100)

    def test_stock_ignores_ssl_cert_file(self):
        env = store_env(CA, tempfile.mkdtemp())
        self.assertFalse(loopback(STOCK, env=env)["trusted"])

    def test_stock_with_bundled_flag_ignores_ssl_cert_file(self):
        env = store_env(CA, tempfile.mkdtemp())
        self.assertFalse(loopback(STOCK, BUNDLED_FLAG, env=env)["trusted"])

    def test_stock_rejects_both_flags(self):
        result = run_node(STOCK, "--use-openssl-ca", BUNDLED_FLAG, "-v")
        self.assertNotEqual(result.returncode, 0)


class NoBundledRoots(unittest.TestCase):
    def test_root_certificates_is_empty(self):
        self.assertEqual(evaluate(ROOTBEER, ROOT_COUNT), 0)

    def test_bundled_flag_does_not_restore_roots(self):
        self.assertEqual(evaluate(ROOTBEER, ROOT_COUNT, BUNDLED_FLAG), 0)

    def test_node_options_does_not_restore_roots(self):
        env = node_env(NODE_OPTIONS=BUNDLED_FLAG)
        self.assertEqual(evaluate(ROOTBEER, ROOT_COUNT, env=env), 0)

    def test_both_flags_are_accepted(self):
        result = run_node(ROOTBEER, "--use-openssl-ca", BUNDLED_FLAG, "-v")
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipIf(MAJOR < 22, "getCACertificates exists on Node 22 and newer")
    def test_bundled_ca_list_is_empty(self):
        self.assertEqual(evaluate(ROOTBEER, "require('tls').getCACertificates('bundled').length"), 0)

    @unittest.skipIf(MAJOR < 22, "getCACertificates exists on Node 22 and newer")
    def test_default_ca_list_holds_no_stock_roots(self):
        env = store_env(EMPTY, tempfile.mkdtemp())
        self.assertEqual(
            evaluate(ROOTBEER, "require('tls').getCACertificates('default').length", env=env), 0)


@unittest.skipIf(MAJOR < 24, "this check is for the current patch family")
class TrustApi(unittest.TestCase):
    """Node 24 and 26 have a CA list API. docs/HERMETICITY.md states these results."""

    def setUp(self):
        self.env = store_env(CA, tempfile.mkdtemp())

    def test_default_list_does_not_show_the_local_store(self):
        # The connection trusts the store. The list holds only bundled and extra roots.
        self.assertEqual(
            evaluate(ROOTBEER, "require('tls').getCACertificates('default').length", env=self.env), 0)

    def test_system_flag_keeps_the_local_store(self):
        self.assertTrue(loopback(ROOTBEER, "--use-system-ca", env=self.env)["trusted"])


class BinaryScan(unittest.TestCase):
    """The stock binary holds each bundled root as PEM text. rootbeerz must hold none."""

    @classmethod
    def setUpClass(cls):
        roots = evaluate(STOCK, "require('tls').rootCertificates")
        cls.markers = sorted({crypto_bundle.body_marker(pem) for pem in roots})

    def scan(self, path):
        with open(path, "rb") as binary:
            data = binary.read()
        return [marker for marker in self.markers if marker in data]

    def test_markers_cover_the_stock_roots(self):
        self.assertGreater(len(self.markers), 100)

    def test_stock_binary_holds_every_marker(self):
        self.assertEqual(len(self.scan(STOCK)), len(self.markers))

    def test_rootbeer_binary_holds_no_marker(self):
        self.assertEqual(self.scan(ROOTBEER), [])

    def test_stock_binary_holds_pem_certificates(self):
        # The control. The generic scan must be able to find the bundled roots.
        with open(STOCK, "rb") as binary:
            self.assertGreater(len(PEM_CERTIFICATE.findall(binary.read())), 100)

    def test_rootbeer_binary_holds_no_pem_certificate(self):
        with open(ROOTBEER, "rb") as binary:
            self.assertEqual(PEM_CERTIFICATE.findall(binary.read()), [])

    def test_default_openssl_dir_is_etc_ssl(self):
        # OPENSSLDIR names the default store. macOS Node points it at /System/Library/OpenSSL.
        with open(ROOTBEER, "rb") as binary:
            data = binary.read()
        # assertTrue, not assertIn: a failed assertIn prints the whole binary (300 MB) into the log.
        self.assertTrue(b'OPENSSLDIR: "/etc/ssl"' in data, "OPENSSLDIR is not /etc/ssl")
        self.assertFalse(b"/System/Library/OpenSSL" in data, "OPENSSLDIR names the macOS OpenSSL path")


class BuildPaths(unittest.TestCase):
    """The binary must not hold the path of the machine that built it."""

    BUILD_PATH = re.compile(rb"/execroot/|bazel-out/|/sandbox/(processwrapper|darwin|linux)-sandbox")
    FIXED_MODULES_DIR = b'MODULESDIR: "/nonexistent/ossl-modules"'

    def test_pattern_finds_a_build_path(self):
        # The control. The pattern must match the paths that Bazel builds leave in a binary.
        for sample in (b"/cache/x/sandbox/processwrapper-sandbox/1/execroot/_main/a",
                       b"/Users/x/sandbox/darwin-sandbox/3/execroot/_main/bazel-out/b"):
            self.assertTrue(self.BUILD_PATH.search(sample), sample)

    def test_rootbeer_binary_holds_no_build_path(self):
        with open(ROOTBEER, "rb") as binary:
            found = sorted({m.group(0) for m in self.BUILD_PATH.finditer(binary.read())})
        self.assertEqual(found, [])

    def test_openssl_modules_dir_is_fixed(self):
        # OpenSSL 3 (Node 18 and newer) loads provider modules from MODULESDIR.
        if MAJOR < 18:
            self.skipTest("OpenSSL 1.1.1 has no MODULESDIR")
        with open(ROOTBEER, "rb") as binary:
            self.assertTrue(self.FIXED_MODULES_DIR in binary.read(), "MODULESDIR is not fixed")


class LinkCheck(unittest.TestCase):
    """The binary must not load a C++ library or any other library from the host toolchain."""

    def test_only_system_libraries(self):
        output = subprocess.run(
            [READOBJ, "--needed-libs", ROOTBEER], capture_output=True, text=True, check=True
        ).stdout
        block = output.split("NeededLibraries [", 1)[1].split("]", 1)[0]
        libraries = [line.strip() for line in block.splitlines() if line.strip()]
        self.assertTrue(libraries, output)
        unexpected = [name for name in libraries if not ALLOWED_LIBRARIES.match(name)]
        self.assertEqual(unexpected, [])


@unittest.skipUnless(sys.platform.startswith("linux"), "glibc symbols exist on Linux only")
class GlibcFloor(unittest.TestCase):
    def test_binary_needs_no_newer_glibc_than_the_documented_floor(self):
        output = subprocess.run(
            [READOBJ, "--dyn-syms", ROOTBEER], capture_output=True, text=True, check=True
        ).stdout
        versions = {
            tuple(int(part) for part in match.groups())
            for match in re.finditer(r"@GLIBC_(\d+)\.(\d+)", output)
        }
        self.assertTrue(versions, output[:2000])
        self.assertLessEqual(max(versions), GLIBC_FLOOR)


class LocalStoreOnly(unittest.TestCase):
    def setUp(self):
        self.empty_dir = tempfile.mkdtemp()

    def test_ssl_cert_file_supplies_trust(self):
        env = store_env(CA, self.empty_dir)
        self.assertTrue(loopback(ROOTBEER, env=env)["trusted"])

    def test_bundled_flag_keeps_the_local_store(self):
        env = store_env(CA, self.empty_dir)
        self.assertTrue(loopback(ROOTBEER, BUNDLED_FLAG, env=env)["trusted"])

    def test_negated_store_flag_keeps_the_local_store(self):
        env = store_env(CA, self.empty_dir)
        self.assertTrue(loopback(ROOTBEER, "--no-use-openssl-ca", env=env)["trusted"])

    def test_ssl_cert_dir_supplies_trust(self):
        hashed_dir = crypto_bundle.hashed_dir(CA)
        env = store_env(EMPTY, hashed_dir)
        self.assertTrue(loopback(ROOTBEER, env=env)["trusted"])

    def test_empty_store_rejects(self):
        result = loopback(ROOTBEER, env=store_env(EMPTY, self.empty_dir))
        self.assertFalse(result["trusted"])
        self.assertEqual(result["code"], "UNABLE_TO_VERIFY_LEAF_SIGNATURE")

    def test_node_extra_ca_certs_supplies_trust(self):
        env = store_env(EMPTY, self.empty_dir)
        env["NODE_EXTRA_CA_CERTS"] = CA
        self.assertTrue(loopback(ROOTBEER, env=env)["trusted"])


class PinnedBundles(unittest.TestCase):
    def subjects(self, env_name):
        return crypto_bundle.subjects(STOCK, path_from_env(env_name))

    def test_pinned_bundle_has_the_recorded_digest(self):
        self.assertEqual(
            crypto_bundle.sha256(path_from_env("PINNED_STORE")),
            os.environ["PINNED_STORE_SHA256"])

    def test_pinned_bundle_holds_no_emudhra_subject(self):
        subjects = self.subjects("PINNED_STORE")
        self.assertGreater(len(subjects), 100)
        self.assertEqual([s for s in subjects if re.search("emsign|emudhra", s, re.I)], [])

    def test_emsign_bundle_holds_only_emudhra_subjects(self):
        subjects = self.subjects("EMSIGN_STORE")
        self.assertEqual(len(subjects), 4)
        for subject in subjects:
            self.assertRegex(subject, "(?i)emudhra")


if __name__ == "__main__":
    unittest.main()
