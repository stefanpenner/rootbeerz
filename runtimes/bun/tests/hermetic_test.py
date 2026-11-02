"""Tests with no network. They compare the rootbeerz Bun with the stock Bun.

The stock_control target runs StockControl only, so it needs no Bun build.
A loopback server gives the testonly certificate. Only the store path can make that certificate
trusted. Stock Bun also reads SSL_CERT_FILE, so a loopback result alone cannot tell the binaries
apart. The compiled-in roots tell them apart: stock holds them, and rootbeerz does not.
"""

import base64
import http.server
import json
import os
import re
import socketserver
import ssl
import threading
import unittest

from trustlib import STORE_REJECTED, node_env, path_from_env, probe, run_node

STOCK = path_from_env("STOCK_BUN")
PROBES = (path_from_env("PROBE_HTTPS"), path_from_env("PROBE_FETCH"))
ROOT_COUNT = path_from_env("ROOT_COUNT_JS")
EMPTY = path_from_env("EMPTY_STORE")
EMSIGN = path_from_env("EMSIGN_STORE")
PINNED = path_from_env("PINNED_STORE")
TEST_CA = path_from_env("TEST_CA")
PEM_BLOCK = re.compile(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.S)
EMSIGN_G1 = b"emSign Root CA - G1"
MIN_STOCK_ROOTS = 100
MIN_STOCK_DER_FOUND = 100


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def handle_error(self, *_args):
        # A client that rejects the certificate closes the socket. The tests expect that.
        pass


def certificates(path):
    """Returns (PEM base64 body, DER) of each certificate in a PEM file."""
    with open(path) as stream:
        bodies = ["".join(block.split()) for block in PEM_BLOCK.findall(stream.read())]
    return [(body, base64.b64decode(body)) for body in bodies]


def read_binary(path):
    with open(path, "rb") as stream:
        return stream.read()


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(path_from_env("TEST_LEAF"), path_from_env("TEST_LEAF_KEY"))
        cls.server = Server(("127.0.0.1", 0), Handler)
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"https://localhost:{cls.server.server_address[1]}/"
        cls.store_dir = os.environ["TEST_TMPDIR"]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def env(self, store):
        """Returns an env where one file and one empty directory are the whole system store."""
        return node_env(SSL_CERT_FILE=store, SSL_CERT_DIR=self.store_dir, NO_COLOR="1")

    def assert_trusted(self, bun, store):
        for script in PROBES:
            result = probe(bun, script, self.url, env=self.env(store))
            self.assertTrue(result["trusted"], (script, result))

    def assert_store_rejected(self, bun, store):
        for script in PROBES:
            result = probe(bun, script, self.url, env=self.env(store))
            self.assertFalse(result["trusted"], (script, result))
            self.assertIn(result["code"], STORE_REJECTED, (script, result))

    def root_count(self, bun):
        result = run_node(bun, ROOT_COUNT, env=self.env(EMPTY))
        self.assertEqual(result.returncode, 0, result.stderr)
        return int(result.stdout)


class StockControl(Base):
    """These tests prove that the probes and the scans can see the difference."""

    def test_stock_with_empty_store_rejects(self):
        self.assert_store_rejected(STOCK, EMPTY)

    def test_stock_store_file_supplies_trust(self):
        self.assert_trusted(STOCK, TEST_CA)

    def test_stock_node_tls_reports_bundled_roots(self):
        self.assertGreaterEqual(self.root_count(STOCK), MIN_STOCK_ROOTS)

    def test_stock_holds_the_emsign_root(self):
        # Mozilla removed some emSign roots, so the test names one root that stock holds.
        binary = read_binary(STOCK)
        held = [der for _body, der in certificates(EMSIGN) if der in binary]
        self.assertTrue(any(EMSIGN_G1 in der for der in held), len(held))

    def test_stock_holds_many_pinned_store_roots(self):
        binary = read_binary(STOCK)
        found = sum(der in binary for _body, der in certificates(PINNED))
        self.assertGreaterEqual(found, MIN_STOCK_DER_FOUND)


class Rootbeerz(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bun = path_from_env("ROOTBEER_BUN")
        cls.binary = read_binary(cls.bun)

    def held(self, path):
        """Returns the first bytes of each root in the PEM file that the binary holds."""
        return [der[:16].hex() for body, der in certificates(path) if der in self.binary or body[:64].encode() in self.binary]

    def test_version_matches_the_manifest(self):
        with open(path_from_env("RUNTIME_MANIFEST")) as stream:
            entry = json.load(stream)["versions"][0]
        result = run_node(self.bun, "--version", env=self.env(EMPTY))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), entry["version"])

    def test_revision_is_the_stock_revision(self):
        """A source build is a canary build by default. Ours must state the release revision."""
        ours = run_node(self.bun, "--revision", env=self.env(EMPTY))
        stock = run_node(STOCK, "--revision", env=self.env(EMPTY))
        self.assertEqual(ours.stdout.strip(), stock.stdout.strip())

    def test_store_file_supplies_trust(self):
        self.assert_trusted(self.bun, TEST_CA)

    def test_empty_store_rejects(self):
        self.assert_store_rejected(self.bun, EMPTY)

    def test_node_tls_reports_no_bundled_roots(self):
        self.assertEqual(self.root_count(self.bun), 0)

    def test_binary_holds_no_emsign_root(self):
        self.assertEqual(self.held(EMSIGN), [])

    def test_binary_holds_no_pinned_store_root(self):
        self.assertEqual(self.held(PINNED), [])

    def test_binary_holds_no_root_list_file_name(self):
        self.assertNotIn(b"root_certs.der", self.binary)


if __name__ == "__main__":
    unittest.main()
