"""Tests that use the public network. Sources for the hosts are in docs/EMUDHRA.md.

A host that gives no trust decision raises Inconclusive. That is a test error.
It is never a pass.
"""

import os
import tempfile
import unittest

from trustlib import (
    Inconclusive,
    STORE_INDEPENDENT,
    STORE_REJECTED,
    path_from_env,
    probe,
    store_env,
)

ROOTBEER = path_from_env("ROOTBEER_NODE")
STOCK = path_from_env("STOCK_NODE")
MAJOR = int(os.environ["NODE_VERSION"].split(".")[0])
PROBE = path_from_env("PROBE_JS")
EMPTY = path_from_env("EMPTY_STORE")
PINNED = path_from_env("PINNED_STORE")
EMSIGN = path_from_env("EMSIGN_STORE")

VALID_SOURCES = ["https://www.google.com/", "https://example.com/", "https://nodejs.org/"]

# eMudhra test pages that chain to emSign Root CA - G1.
EMUDHRA_G1 = [
    "https://testevg1.emsign.com/",
    "https://testovg1.emsign.com/",
    "https://testevg1r.emsign.com/",
]
# eMudhra test pages that chain to emSign ECC Root CA - G3. Stock 22 dropped this root.
EMUDHRA_G3 = ["https://testevg3.emsign.com/", "https://testovg3.emsign.com/"]
EMUDHRA = EMUDHRA_G1 + EMUDHRA_G3
# Third-party demo pages for the compromised intermediates. Stock 22 blocks these by SPKI.
EMUDHRA_DEMO = [
    "https://dv-g3b-emudhra.we-have-a-problem.com/",
    "https://ov-g3c-emudhra.we-have-a-problem.com/",
]


class Base(unittest.TestCase):
    def setUp(self):
        self.empty_dir = tempfile.mkdtemp()
        self.empty_store = store_env(EMPTY, self.empty_dir)
        self.pinned_store = store_env(PINNED, self.empty_dir)
        self.emsign_store = store_env(EMSIGN, self.empty_dir)

    def assert_trusted(self, node, url, env):
        result = probe(node, PROBE, url, env=env)
        self.assertTrue(result["trusted"], f"{url}: {result}")

    def assert_store_rejected(self, node, url, env):
        result = probe(node, PROBE, url, env=env)
        self.assertFalse(result["trusted"], f"{url} was trusted")
        if result["code"] in STORE_INDEPENDENT:
            raise Inconclusive(f"{url}: {result['code']} does not depend on the store")
        self.assertIn(result["code"], STORE_REJECTED, f"{url}: {result}")


class ValidSources(Base):
    def test_pinned_store_trusts_valid_sources(self):
        for url in VALID_SOURCES:
            with self.subTest(url=url):
                self.assert_trusted(ROOTBEER, url, self.pinned_store)

    def test_empty_store_rejects_valid_sources(self):
        for url in VALID_SOURCES:
            with self.subTest(url=url):
                self.assert_store_rejected(ROOTBEER, url, self.empty_store)

    def test_control_stock_trusts_valid_sources_with_empty_store(self):
        for url in VALID_SOURCES:
            with self.subTest(url=url):
                self.assert_trusted(STOCK, url, self.empty_store)


class EmudhraDomains(Base):
    def test_empty_store_rejects_emudhra(self):
        for url in EMUDHRA:
            with self.subTest(url=url):
                self.assert_store_rejected(ROOTBEER, url, self.empty_store)

    def test_pinned_store_rejects_emudhra(self):
        for url in EMUDHRA:
            with self.subTest(url=url):
                self.assert_store_rejected(ROOTBEER, url, self.pinned_store)

    def test_pinned_store_rejects_compromised_intermediate_demo_hosts(self):
        # Stock 22 and this build reject these by SPKI, so CERT_REVOKED is the expected code.
        for url in EMUDHRA_DEMO:
            with self.subTest(url=url):
                result = probe(ROOTBEER, PROBE, url, env=self.pinned_store)
                self.assertFalse(result["trusted"], f"{url} was trusted")
                allowed = STORE_REJECTED | {"CERT_REVOKED"}
                self.assertIn(result["code"], allowed, f"{url}: {result}")

    def test_control_emsign_store_trusts_emudhra(self):
        # This control proves that the hosts answer, and that the store makes the decision.
        for url in EMUDHRA:
            with self.subTest(url=url):
                self.assert_trusted(ROOTBEER, url, self.emsign_store)

    def test_control_stock_trusts_emudhra_with_empty_store(self):
        urls = EMUDHRA_G1 if MAJOR >= 22 else EMUDHRA
        for url in urls:
            with self.subTest(url=url):
                self.assert_trusted(STOCK, url, self.empty_store)


if __name__ == "__main__":
    unittest.main()
