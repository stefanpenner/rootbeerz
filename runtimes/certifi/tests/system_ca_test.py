"""Proves that the certifi shim names the system store of the host.

The hermetic test proves that the shim bundles no root. So a trusted public
host through where() with no store env proves that the trust comes from the
system store. The empty-store control proves that the store makes the decision.
"""

import unittest

from network_test import VALID_SOURCE, decide
from probelib import env_path

SHIM = env_path("CERTIFI_SHIM_WHEEL")
EMPTY = env_path("EMPTY_STORE")


class SystemStore(unittest.TestCase):
    def test_no_store_env_names_an_existing_store(self):
        got = decide(SHIM, None, VALID_SOURCE)
        self.assertNotIn("where_error", got)
        self.assertNotIn("site-packages", got["where"])

    def test_no_store_env_trusts_a_valid_host(self):
        got = decide(SHIM, None, VALID_SOURCE)
        self.assertEqual(got["tls"], "trusted", got)

    def test_control_empty_store_rejects_the_same_host(self):
        # Python refuses to load a store with no certificate. So the empty
        # store fails at load, before a handshake can give a verify code.
        got = decide(SHIM, EMPTY, VALID_SOURCE)
        self.assertEqual(got["tls"], "rejected-load", got)
        self.assertIn("NO_CERTIFICATE", got["tls_detail"], got)


if __name__ == "__main__":
    unittest.main()
