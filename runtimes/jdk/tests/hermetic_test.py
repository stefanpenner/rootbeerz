"""Hermetic proof for the JDK with an empty cacerts. These tests use no network.

The stock control shows that each assertion can fail. teeth_test.py proves this.
"""

import hashlib
import os
import socket
import ssl
import sys
import threading
import unittest

from jdklib import KEYCHAIN_ROOT_FLAGS, Jdk, pem_fingerprints

OURS = Jdk(os.environ["ROOTBEERZ_JDK"])
STOCK = Jdk(os.environ["STOCK_JDK"])
PROBE = os.path.abspath(os.environ["PROBE_JAVA"])
TEST_CA = os.path.abspath(os.environ["TEST_CA"])
TEST_LEAF = os.path.abspath(os.environ["TEST_LEAF"])
TEST_LEAF_KEY = os.path.abspath(os.environ["TEST_LEAF_KEY"])
EMSIGN = os.path.abspath(os.environ["EMSIGN_STORE"])
PINNED = os.path.abspath(os.environ["PINNED_STORE"])

# emSign Root CA - G1. testevg1.emsign.com chains to this root.
EMSIGN_G1 = "40f6af0346a99aa1cd1d555a4e9cce62c7f9634603ee406615833dc8c8d00367"
ZERO_ENTRIES = "Your keystore contains 0 entries"


class LoopbackServer:
    """A TLS server on 127.0.0.1 that uses the testonly leaf."""

    def __init__(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(TEST_LEAF, TEST_LEAF_KEY)
        self.context = context
        self.socket = socket.create_server(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                raw, _ = self.socket.accept()
            except OSError:
                return
            try:
                with self.context.wrap_socket(raw, server_side=True) as tls:
                    tls.recv(4096)
                    tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except (ssl.SSLError, OSError):
                raw.close()

    def close(self):
        self.socket.close()


class Shaders(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "macOS holds the Metal shader library")
    def test_ours_shaders_are_the_pinned_stock_bytes(self):
        def digest(jdk):
            with open(os.path.join(jdk.home, "lib", "shaders.metallib"), "rb") as file:
                return hashlib.sha256(file.read()).hexdigest()

        self.assertEqual(digest(OURS), digest(STOCK))


class Keystore(unittest.TestCase):
    def test_ours_cacerts_is_valid_and_empty(self):
        result = OURS.list_cacerts()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(ZERO_ENTRIES, result.stdout)

    def test_ours_has_no_other_default_store(self):
        security = os.path.join(OURS.home, "lib", "security")
        self.assertTrue(os.path.isfile(os.path.join(security, "cacerts")))
        self.assertFalse(os.path.exists(os.path.join(security, "jssecacerts")))

    def test_ours_image_holds_no_certificate_file(self):
        found = [
            name
            for _, _, names in os.walk(OURS.home)
            for name in names
            if name.endswith((".pem", ".crt", ".cer", ".der", ".p12"))
        ]
        self.assertEqual(found, [])

    def test_ours_default_trust_manager_has_no_anchor(self):
        self.assertEqual(OURS.anchors(PROBE), set())

    def test_control_stock_cacerts_holds_emsign(self):
        result = STOCK.list_cacerts()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(ZERO_ENTRIES, result.stdout)
        self.assertIn("emsign", result.stdout.lower())

    def test_control_stock_default_trust_manager_holds_emsign_root(self):
        anchors = STOCK.anchors(PROBE)
        self.assertIn(EMSIGN_G1, anchors)
        self.assertIn(EMSIGN_G1, pem_fingerprints(EMSIGN))


class Loopback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = LoopbackServer()
        cls.url = f"https://localhost:{cls.server.port}/"
        cls.ca_store = OURS.make_store(PROBE, TEST_CA, "testonly.p12")
        cls.pinned_store = OURS.make_store(PROBE, PINNED, "pinned.p12")

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def assert_rejected(self, jdk, code, *flags, env=None):
        result = jdk.connect(PROBE, self.url, *flags, env=env)
        self.assertFalse(result["trusted"], result)
        self.assertEqual(result["code"], code, result)

    def assert_trusted(self, jdk, *flags):
        result = jdk.connect(PROBE, self.url, *flags)
        self.assertTrue(result["trusted"], result)

    def test_ours_rejects_with_no_flag(self):
        self.assert_rejected(OURS, "NO_TRUST_ANCHORS")

    def test_ours_trusts_with_explicit_store(self):
        self.assert_trusted(OURS, *self.ca_store)

    def test_ours_rejects_with_a_store_that_lacks_the_ca(self):
        self.assert_rejected(OURS, "PATH_NOT_TRUSTED", *self.pinned_store)

    def test_ours_ignores_ssl_cert_file(self):
        env = {"SSL_CERT_FILE": TEST_CA, "SSL_CERT_DIR": os.path.dirname(TEST_CA)}
        self.assert_rejected(OURS, "NO_TRUST_ANCHORS", env=env)

    def test_control_stock_rejects_because_the_ca_is_unknown(self):
        self.assert_rejected(STOCK, "PATH_NOT_TRUSTED")

    def test_control_stock_trusts_with_explicit_store(self):
        self.assert_trusted(STOCK, *self.ca_store)


@unittest.skipUnless(sys.platform == "darwin", "the keychain exists on macOS only")
class Keychain(unittest.TestCase):
    """The keychain types read the host. The test decides on the anchor count, not on a host root."""

    def test_ours_reads_the_system_roots_with_keychain_root_type(self):
        anchors = OURS.anchors(PROBE, *KEYCHAIN_ROOT_FLAGS)
        self.assertGreater(len(anchors), 50)


if __name__ == "__main__":
    unittest.main()
