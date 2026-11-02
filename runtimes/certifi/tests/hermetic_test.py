"""Hermetic proof for the certifi shim. This test uses no public network.

The test runs the shipped wheel and the pinned stock wheel in the same way.
The stock control must show the difference, or the test proves nothing.
"""

import hashlib
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from unittest import mock

from probelib import env_path, fingerprints, probe

SHIM = env_path("CERTIFI_SHIM_WHEEL")
STOCK = env_path("CERTIFI_STOCK_WHEEL")
MANIFEST = env_path("CERTIFI_MANIFEST")
EMSIGN = env_path("EMSIGN_STORE")
EMPTY = env_path("EMPTY_STORE")
PINNED = env_path("PINNED_STORE")
TEST_CA = env_path("TEST_CA")
TEST_LEAF = env_path("TEST_LEAF")
TEST_LEAF_KEY = env_path("TEST_LEAF_KEY")


def read(path):
    with open(path, encoding="ascii", errors="replace") as handle:
        return handle.read()


def pinned_entry():
    return json.loads(read(MANIFEST))["versions"][0]


class LoopbackServer:
    """Serves the testonly leaf on 127.0.0.1 until the block ends."""

    def __enter__(self):
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(TEST_LEAF, TEST_LEAF_KEY)
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.addr = self.sock.getsockname()
        threading.Thread(target=self._serve, daemon=True).start()
        return self

    def __exit__(self, *_):
        self.sock.close()

    def _serve(self):
        while True:
            try:
                raw, _ = self.sock.accept()
            except OSError:
                return
            try:
                with self.ctx.wrap_socket(raw, server_side=True):
                    pass
            except OSError:  # ssl.SSLError is an OSError. A client may reject us.
                pass


class BundleTest(unittest.TestCase):
    def test_stock_bundle_holds_emsign_root(self):
        stock = fingerprints(probe(STOCK)["contents"])
        self.assertTrue(fingerprints(read(EMSIGN)) & stock)

    def test_shim_with_pinned_store_holds_no_emsign_root(self):
        shim = fingerprints(probe(SHIM, cert_file=PINNED)["contents"])
        self.assertTrue(shim, "the pinned store holds no root")
        self.assertFalse(fingerprints(read(EMSIGN)) & shim)

    def test_stock_wheel_bundles_a_pem(self):
        with zipfile.ZipFile(STOCK) as whl:
            self.assertIn("certifi/cacert.pem", whl.namelist())

    def test_shim_wheel_bundles_no_pem(self):
        with zipfile.ZipFile(SHIM) as whl:
            names = whl.namelist()
            self.assertIn("certifi/__init__.py", names)
            for name in names:
                self.assertFalse(name.endswith((".pem", ".crt", ".cer")), name)
                self.assertNotIn(b"BEGIN CERTIFICATE", whl.read(name), name)

    def test_shim_wheel_has_no_build_time(self):
        with zipfile.ZipFile(SHIM) as whl:
            for info in whl.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0), info.filename)


class ManifestTest(unittest.TestCase):
    def test_stock_wheel_matches_the_manifest_hash(self):
        with open(STOCK, "rb") as handle:
            got = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(got, pinned_entry()["sha256"])

    def test_versions_come_from_the_manifest(self):
        pinned = pinned_entry()["version"]
        self.assertEqual(probe(SHIM, cert_file=PINNED)["version"], pinned)
        self.assertEqual(probe(STOCK)["version"], pinned)

    def test_wheel_name_follows_the_manifest(self):
        pinned = pinned_entry()["version"]
        self.assertEqual(
            os.path.basename(SHIM), f"certifi-{pinned}-py3-none-any.whl"
        )
        self.assertEqual(
            os.path.basename(os.path.dirname(SHIM)),
            os.path.basename(pinned_entry()["artifact"]),
        )


class ApiTest(unittest.TestCase):
    def run_python(self, code, wheel=SHIM):
        return subprocess.run(
            [sys.executable, "-I", "-c", code, wheel],
            env={"SSL_CERT_FILE": PINNED},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def test_public_names_match_stock(self):
        code = (
            "import sys, json; sys.path.insert(0, sys.argv[1]); import certifi; "
            "print(json.dumps([sorted(n for n in dir(certifi) "
            "if not n.startswith('_')), sorted(certifi.__all__)]))"
        )
        self.assertEqual(
            json.loads(self.run_python(code, SHIM)),
            json.loads(self.run_python(code, STOCK)),
        )

    def test_core_module_matches_stock(self):
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from certifi.core import where, contents; contents(); print(where())"
        )
        self.assertEqual(self.run_python(code), PINNED)

    def test_module_entry_prints_where(self):
        done = subprocess.run(
            [sys.executable, "-m", "certifi"],
            env={"SSL_CERT_FILE": PINNED, "PYTHONPATH": SHIM},
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(done.stdout.strip(), PINNED)

    def test_pip_installs_the_wheel_and_it_works(self):
        with tempfile.TemporaryDirectory() as target:
            subprocess.run(
                [
                    sys.executable, "-I", "-m", "pip", "install",
                    "--no-index", "--no-deps", "--quiet",
                    "--disable-pip-version-check", "--target", target, SHIM,
                ],
                env={"PATH": "/usr/bin:/bin", "HOME": target},
                capture_output=True,
                check=True,
            )
            got = probe(target, cert_file=PINNED)
        self.assertEqual(got["where"], PINNED)
        self.assertIn(target, got["file"])


class LoopbackTrustTest(unittest.TestCase):
    def test_shim_trusts_through_ssl_cert_file(self):
        with LoopbackServer() as srv:
            got = probe(SHIM, cert_file=TEST_CA, server=srv.addr)
        self.assertEqual(got["where"], TEST_CA)
        self.assertEqual(got["tls"], "trusted", got)

    def test_shim_rejects_with_an_empty_store(self):
        with LoopbackServer() as srv:
            got = probe(SHIM, cert_file=EMPTY, server=srv.addr)
        self.assertEqual(got["where"], EMPTY)
        # OpenSSL refuses a CA file with no certificate. No connection starts.
        self.assertEqual(got["tls"], "rejected-load", got)

    def test_shim_rejects_with_a_store_that_lacks_the_ca(self):
        with LoopbackServer() as srv:
            got = probe(SHIM, cert_file=PINNED, server=srv.addr)
        self.assertEqual(got["tls"], "rejected-verify", got)

    def test_control_stock_ignores_ssl_cert_file(self):
        with LoopbackServer() as srv:
            got = probe(STOCK, cert_file=TEST_CA, server=srv.addr)
        self.assertNotEqual(got["where"], TEST_CA)
        self.assertEqual(got["tls"], "rejected-verify", got)


class ResolveOrderTest(unittest.TestCase):
    """Runs the shim in this process, so the OS view of a store can change."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, SHIM)
        import certifi.core

        cls.core = certifi.core

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(SHIM)

    def resolve(self, env=None, cafile=None, known=()):
        default = types.SimpleNamespace(cafile=cafile)
        with (
            mock.patch.dict(os.environ, env or {}, clear=True),
            mock.patch.object(ssl, "get_default_verify_paths", lambda: default),
            mock.patch.object(self.core, "KNOWN_STORE_PATHS", known),
        ):
            return self.core.where()

    def test_ssl_cert_file_wins(self):
        got = self.resolve({"SSL_CERT_FILE": TEST_CA}, cafile=EMPTY, known=(PINNED,))
        self.assertEqual(got, TEST_CA)

    def test_empty_ssl_cert_file_means_unset(self):
        self.assertEqual(self.resolve({"SSL_CERT_FILE": ""}, cafile=EMPTY), EMPTY)

    def test_default_verify_paths_win_over_known_paths(self):
        self.assertEqual(self.resolve(cafile=EMPTY, known=(PINNED,)), EMPTY)

    def test_first_known_path_that_exists_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            absent = os.path.join(tmp, "absent.pem")
            self.assertEqual(self.resolve(known=(absent, PINNED, EMPTY)), PINNED)

    def test_missing_ssl_cert_file_fails_and_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            absent = os.path.join(tmp, "absent.pem")
            with self.assertRaisesRegex(FileNotFoundError, "SSL_CERT_FILE"):
                self.resolve({"SSL_CERT_FILE": absent}, cafile=EMPTY, known=(PINNED,))

    def test_directory_as_ssl_cert_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(FileNotFoundError, "SSL_CERT_FILE"):
                self.resolve({"SSL_CERT_FILE": tmp})

    def test_no_store_fails_and_names_each_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            absent = os.path.join(tmp, "absent.pem")
            with self.assertRaises(FileNotFoundError) as caught:
                self.resolve(known=(absent,))
        self.assertIn(absent, str(caught.exception))
        self.assertIn("SSL_CERT_FILE", str(caught.exception))

    def test_contents_fails_with_no_store(self):
        default = types.SimpleNamespace(cafile=None)
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(ssl, "get_default_verify_paths", lambda: default),
            mock.patch.object(self.core, "KNOWN_STORE_PATHS", ()),
        ):
            with self.assertRaises(FileNotFoundError):
                self.core.contents()


if __name__ == "__main__":
    unittest.main()
