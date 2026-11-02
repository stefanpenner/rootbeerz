"""Tests that use the public network. Sources for the hosts are in docs/EMUDHRA.md.

A host that gives no trust decision raises Inconclusive. That is a test error. It is never a pass.
"""

import os
import unittest

from trustlib import Inconclusive, STORE_INDEPENDENT, STORE_REJECTED, node_env, path_from_env, probe

ROOTBEER = path_from_env("ROOTBEER_BUN")
STOCK = path_from_env("STOCK_BUN")
PROBES = (path_from_env("PROBE_HTTPS"), path_from_env("PROBE_FETCH"))
EMPTY = path_from_env("EMPTY_STORE")
PINNED = path_from_env("PINNED_STORE")
EMSIGN = path_from_env("EMSIGN_STORE")
EMUDHRA = "https://testevg1.emsign.com/"
VALID = "https://example.com/"


class Network(unittest.TestCase):
    def fetch(self, bun, script, url, store):
        env = node_env(SSL_CERT_FILE=store, SSL_CERT_DIR=os.environ["TEST_TMPDIR"])
        return probe(bun, script, url, env=env)

    def assert_trusted(self, bun, url, store):
        for script in PROBES:
            result = self.fetch(bun, script, url, store)
            self.assertTrue(result["trusted"], (script, result))

    def assert_store_rejected(self, bun, url, store):
        for script in PROBES:
            result = self.fetch(bun, script, url, store)
            self.assertFalse(result["trusted"], (script, result))
            if result["code"] in STORE_INDEPENDENT:
                raise Inconclusive(f"{url}: {result['code']} does not depend on the store")
            self.assertIn(result["code"], STORE_REJECTED, (script, result))

    def test_control_stock_trusts_emudhra_with_empty_store(self):
        self.assert_trusted(STOCK, EMUDHRA, EMPTY)

    def test_control_rootbeerz_trusts_emudhra_with_emsign_store(self):
        self.assert_trusted(ROOTBEER, EMUDHRA, EMSIGN)

    def test_control_rootbeerz_trusts_valid_host_with_pinned_store(self):
        self.assert_trusted(ROOTBEER, VALID, PINNED)


    def test_rootbeerz_rejects_emudhra_with_pinned_store(self):
        self.assert_store_rejected(ROOTBEER, EMUDHRA, PINNED)

    def test_rootbeerz_rejects_emudhra_with_empty_store(self):
        self.assert_store_rejected(ROOTBEER, EMUDHRA, EMPTY)


if __name__ == "__main__":
    unittest.main()
