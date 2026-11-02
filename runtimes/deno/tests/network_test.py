"""Tests that use the public network. Sources for the hosts are in docs/EMUDHRA.md.

A host that gives no trust decision raises Inconclusive. That is a test error. It is never a pass.
"""

import tempfile
import unittest

from trustlib import Inconclusive, STORE_INDEPENDENT, STORE_REJECTED, node_env, path_from_env, probe

ROOTBEER = path_from_env("ROOTBEER_DENO")
STOCK = path_from_env("STOCK_DENO")
PROBE = path_from_env("PROBE_TS")
EMPTY = path_from_env("EMPTY_STORE")
PINNED = path_from_env("PINNED_STORE")
EMSIGN = path_from_env("EMSIGN_STORE")
RUN_FLAGS = ("run", "--allow-all", "--no-config", "--no-lock")
EMUDHRA = "https://testevg1.emsign.com/"
VALID = "https://example.com/"


class Network(unittest.TestCase):
    def setUp(self):
        self.deno_dir = tempfile.mkdtemp()

    def fetch(self, deno, url, store):
        env = node_env(
            DENO_DIR=self.deno_dir,
            DENO_NO_UPDATE_CHECK="1",
            SSL_CERT_FILE=store,
            SSL_CERT_DIR=self.deno_dir,
        )
        return probe(deno, PROBE, url, *RUN_FLAGS, env=env)

    def assert_store_rejected(self, deno, url, store):
        result = self.fetch(deno, url, store)
        self.assertFalse(result["trusted"], result)
        if result["code"] in STORE_INDEPENDENT:
            raise Inconclusive(f"{url}: {result['code']} does not depend on the store")
        self.assertIn(result["code"], STORE_REJECTED, result)

    def test_control_stock_trusts_emudhra_with_empty_store(self):
        self.assertTrue(self.fetch(STOCK, EMUDHRA, EMPTY)["trusted"])

    def test_control_rootbeerz_trusts_emudhra_with_emsign_store(self):
        self.assertTrue(self.fetch(ROOTBEER, EMUDHRA, EMSIGN)["trusted"])

    def test_control_rootbeerz_trusts_valid_host_with_pinned_store(self):
        self.assertTrue(self.fetch(ROOTBEER, VALID, PINNED)["trusted"])

    def test_rootbeerz_rejects_emudhra_with_pinned_store(self):
        self.assert_store_rejected(ROOTBEER, EMUDHRA, PINNED)

    def test_rootbeerz_rejects_emudhra_with_empty_store(self):
        self.assert_store_rejected(ROOTBEER, EMUDHRA, EMPTY)


if __name__ == "__main__":
    unittest.main()
