"""Proves that this JDK trusts the system store of the host.

The cacerts of this JDK is empty. Java ignores SSL_CERT_FILE, so the system
store on macOS is the KeychainStore-ROOT type. The control with no flag uses
the empty cacerts and rejects the same host. Bazel runs this test on macOS only.
"""

import os
import unittest

from jdklib import KEYCHAIN_ROOT_FLAGS, Jdk, connect_decided

JDK = Jdk(os.environ["ROOTBEERZ_JDK"])
PROBE = os.path.abspath(os.environ["PROBE_JAVA"])
VALID = "https://example.com/"


class SystemStore(unittest.TestCase):
    def test_keychain_root_store_trusts_a_valid_host(self):
        result = connect_decided(JDK, PROBE, VALID, *KEYCHAIN_ROOT_FLAGS)
        self.assertTrue(result["trusted"], result)

    def test_control_empty_cacerts_rejects_the_same_host(self):
        result = connect_decided(JDK, PROBE, VALID)
        self.assertEqual(result["code"], "NO_TRUST_ANCHORS", result)


if __name__ == "__main__":
    unittest.main()
