"""Tests that use the public network. Sources for the hosts are in docs/EMUDHRA.md.

A host that gives no trust decision raises Inconclusive. That is a test error.
It is never a pass.
"""

import time
import unittest

from probelib import env_path, probe
from trustlib import NETWORK_ATTEMPTS, Inconclusive

SHIM = env_path("CERTIFI_SHIM_WHEEL")
STOCK = env_path("CERTIFI_STOCK_WHEEL")
EMSIGN = env_path("EMSIGN_STORE")
PINNED = env_path("PINNED_STORE")

EMUDHRA_G1 = ("testevg1.emsign.com", 443)
VALID_SOURCE = ("example.com", 443)

# X509_V_ERR codes that prove that the store did not hold an issuer for the chain.
STORE_REJECTED = {
    2,  # UNABLE_TO_GET_ISSUER_CERT
    18,  # DEPTH_ZERO_SELF_SIGNED_CERT
    19,  # SELF_SIGNED_CERT_IN_CHAIN
    20,  # UNABLE_TO_GET_ISSUER_CERT_LOCALLY
    21,  # UNABLE_TO_VERIFY_LEAF_SIGNATURE
    27,  # CERT_UNTRUSTED
}


def decide(wheel, cert_file, server):
    """Returns a result that holds a trust decision, or raises Inconclusive."""
    for attempt in range(NETWORK_ATTEMPTS):
        got = probe(wheel, cert_file=cert_file, server=server)
        if got["tls"] != "no-decision":
            return got
        if attempt + 1 < NETWORK_ATTEMPTS:
            time.sleep(2)
    raise Inconclusive(f"{server}: no trust decision: {got['tls_detail']}")


class EmudhraTest(unittest.TestCase):
    def assert_store_rejected(self, got):
        self.assertNotEqual(got["tls"], "trusted", got)
        if got.get("verify_code") not in STORE_REJECTED:
            raise Inconclusive(f"the rejection does not depend on the store: {got}")

    def test_shim_with_pinned_store_rejects_emudhra(self):
        self.assert_store_rejected(decide(SHIM, PINNED, EMUDHRA_G1))

    def test_control_stock_with_pinned_store_trusts_emudhra(self):
        self.assertEqual(decide(STOCK, PINNED, EMUDHRA_G1)["tls"], "trusted")

    def test_control_shim_with_emsign_store_trusts_emudhra(self):
        # This control proves that the host answers, and that the store decides.
        self.assertEqual(decide(SHIM, EMSIGN, EMUDHRA_G1)["tls"], "trusted")

    def test_shim_with_pinned_store_trusts_a_valid_source(self):
        self.assertEqual(decide(SHIM, PINNED, VALID_SOURCE)["tls"], "trusted")


if __name__ == "__main__":
    unittest.main()
