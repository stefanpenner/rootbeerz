"""Tests with no network. They compare the rootbeerz Deno with the stock Deno.

The stock_teeth target runs StockControl only, so it needs no Deno build.
A loopback server gives the testonly certificate. Only the store path can make that certificate
trusted.
"""

import base64
import http.server
import json
import os
import re
import socketserver
import ssl
import tempfile
import threading
import unittest

from trustlib import STORE_REJECTED, node_env, path_from_env, probe, run_node

STOCK = path_from_env("STOCK_DENO")
PROBE = path_from_env("PROBE_TS")
ROOT_COUNT = path_from_env("ROOT_COUNT_TS")
EMPTY = path_from_env("EMPTY_STORE")
EMSIGN = path_from_env("EMSIGN_STORE")
PINNED = path_from_env("PINNED_STORE")
TEST_CA = path_from_env("TEST_CA")
RUN_FLAGS = ("run", "--allow-all", "--no-config", "--no-lock")
PEM_BLOCK = re.compile(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.S)
MIN_STOCK_ROOTS = 100
MIN_STOCK_ANCHORS_FOUND = 100


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


def der_read(data, offset):
    """Returns (start of the content, end of the content) of the DER value at offset."""
    length = data[offset + 1]
    start = offset + 2
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[start : start + count], "big")
        start += count
    return start, start + length


def anchor_parts(der):
    """Returns the subject and the SPKI as a trust anchor stores them, with no outer tag."""
    tbs_start, _ = der_read(der, 0)
    position, _ = der_read(der, tbs_start)
    if der[position] == 0xA0:
        position = der_read(der, position)[1]
    for _field in ("serial", "signature", "issuer", "validity"):
        position = der_read(der, position)[1]
    subject_start, subject_end = der_read(der, position)
    spki_start, spki_end = der_read(der, subject_end)
    return der[subject_start:subject_end], der[spki_start:spki_end]


def certificates(path):
    """Returns (PEM base64 body, DER) of each certificate in a PEM file."""
    with open(path) as stream:
        text = stream.read()
    bodies = ["".join(block.split()) for block in PEM_BLOCK.findall(text)]
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

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.deno_dir = tempfile.mkdtemp()

    def env(self, **extra):
        return node_env(DENO_DIR=self.deno_dir, DENO_NO_UPDATE_CHECK="1", NO_COLOR="1", **extra)

    def fetch(self, deno, **extra):
        env = self.env(SSL_CERT_DIR=self.deno_dir, **extra)
        return probe(deno, PROBE, self.url, *RUN_FLAGS, env=env)

    def assert_trusted(self, deno, **extra):
        result = self.fetch(deno, **extra)
        self.assertTrue(result["trusted"], result)

    def assert_store_rejected(self, deno, **extra):
        result = self.fetch(deno, **extra)
        self.assertFalse(result["trusted"], result)
        self.assertIn(result["code"], STORE_REJECTED, result)

    def root_count(self, deno, **extra):
        env = self.env(SSL_CERT_DIR=self.deno_dir, **extra)
        result = run_node(deno, *RUN_FLAGS, ROOT_COUNT, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return int(result.stdout)


class StockControl(Base):
    """These tests prove that the probes can see the difference."""

    def test_stock_default_ignores_the_store_file(self):
        self.assert_store_rejected(STOCK, SSL_CERT_FILE=TEST_CA)

    def test_stock_system_value_reads_the_store_file(self):
        self.assert_trusted(STOCK, SSL_CERT_FILE=TEST_CA, DENO_TLS_CA_STORE="system")

    def test_stock_system_value_with_empty_store_rejects(self):
        self.assert_store_rejected(STOCK, SSL_CERT_FILE=EMPTY, DENO_TLS_CA_STORE="system")

    def test_stock_node_tls_reports_bundled_roots(self):
        self.assertGreaterEqual(self.root_count(STOCK, SSL_CERT_FILE=EMPTY), MIN_STOCK_ROOTS)

    def test_stock_holds_emsign_roots_as_trust_anchors(self):
        # Mozilla removed emSign ECC Root CA - G3, so the stock binary holds 3 of the 4 roots.
        binary = read_binary(STOCK)
        roots = certificates(EMSIGN)
        self.assertEqual(len(roots), 4)
        names = {anchor_parts(der)[0]: anchor_parts(der)[1] in binary for _body, der in roots}
        self.assertEqual(sum(names.values()), 3, names)
        self.assertTrue(names[next(n for n in names if b"emSign Root CA - G1" in n)])

    def test_stock_holds_many_pinned_store_anchors(self):
        binary = read_binary(STOCK)
        found = sum(anchor_parts(der)[1] in binary for _body, der in certificates(PINNED))
        self.assertGreaterEqual(found, MIN_STOCK_ANCHORS_FOUND)


class Rootbeerz(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.deno = path_from_env("ROOTBEER_DENO")
        cls.binary = read_binary(cls.deno)

    def assert_absent(self, *needles):
        # assertNotIn prints the whole binary when it fails.
        for needle in needles:
            self.assertFalse(needle in self.binary, needle[:32].hex())

    def test_version_matches_the_manifest(self):
        with open(path_from_env("RUNTIME_MANIFEST")) as stream:
            manifest = json.load(stream)
        entry = next(e for e in manifest["versions"] if e["line"] == os.environ["DENO_LINE"])
        result = run_node(self.deno, "--version", env=self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"deno {entry['version']}", result.stdout)

    def test_default_trusts_the_store_file(self):
        self.assert_trusted(self.deno, SSL_CERT_FILE=TEST_CA)

    def test_mozilla_value_trusts_the_store_file(self):
        self.assert_trusted(self.deno, SSL_CERT_FILE=TEST_CA, DENO_TLS_CA_STORE="mozilla")

    def test_default_with_empty_store_rejects(self):
        self.assert_store_rejected(self.deno, SSL_CERT_FILE=EMPTY)

    def test_mozilla_value_with_empty_store_rejects(self):
        self.assert_store_rejected(self.deno, SSL_CERT_FILE=EMPTY, DENO_TLS_CA_STORE="mozilla")

    def test_node_tls_reports_no_bundled_roots(self):
        self.assertEqual(self.root_count(self.deno, SSL_CERT_FILE=EMPTY), 0)

    def test_binary_holds_no_emsign_root(self):
        roots = certificates(EMSIGN)
        self.assertEqual(len(roots), 4)
        for body, der in roots:
            subject, spki = anchor_parts(der)
            with self.subTest(subject=subject.hex()):
                self.assert_absent(der, body.encode(), body[:64].encode(), spki)

    def test_binary_holds_no_pinned_store_anchor(self):
        for body, der in certificates(PINNED):
            subject, spki = anchor_parts(der)
            with self.subTest(subject=subject.hex()):
                self.assert_absent(der, body[:64].encode(), spki)


if __name__ == "__main__":
    unittest.main()
