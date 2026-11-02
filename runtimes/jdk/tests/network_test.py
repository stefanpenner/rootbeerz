"""Network proof for the JDK with an empty cacerts. docs/EMUDHRA.md lists the host.

A host that gives no trust decision raises Inconclusive. That is a test error.
It is never a pass.
"""

import os
import unittest

from jdklib import Jdk, connect_decided

OURS = Jdk(os.environ["ROOTBEERZ_JDK"])
STOCK = Jdk(os.environ["STOCK_JDK"])
PROBE = os.path.abspath(os.environ["PROBE_JAVA"])
PINNED = os.path.abspath(os.environ["PINNED_STORE"])

EMUDHRA = "https://testevg1.emsign.com/"
VALID = "https://www.google.com/"


class Network(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pinned = OURS.make_store(PROBE, PINNED, "pinned.p12")

    def test_control_stock_trusts_emudhra(self):
        result = connect_decided(STOCK, PROBE, EMUDHRA)
        self.assertTrue(result["trusted"], result)

    def test_ours_rejects_emudhra_with_no_flag(self):
        result = connect_decided(OURS, PROBE, EMUDHRA)
        self.assertEqual(result["code"], "NO_TRUST_ANCHORS", result)

    def test_ours_rejects_emudhra_with_pinned_store(self):
        result = connect_decided(OURS, PROBE, EMUDHRA, *self.pinned)
        self.assertEqual(result["code"], "PATH_NOT_TRUSTED", result)

    def test_ours_trusts_a_valid_host_with_pinned_store(self):
        result = connect_decided(OURS, PROBE, VALID, *self.pinned)
        self.assertTrue(result["trusted"], result)


if __name__ == "__main__":
    unittest.main()
