"""Proves that this Bun trusts the system store of the host.

The hermetic test proves that this Bun bundles no root. So a trusted public
host with no store env proves that the trust comes from the system store.
The empty-store control proves that the store makes the decision.
"""

import os
import unittest

from trustlib import STORE_REJECTED, node_env, path_from_env, probe

BUN = path_from_env("ROOTBEER_BUN")
PROBES = (path_from_env("PROBE_HTTPS"), path_from_env("PROBE_FETCH"))
EMPTY = path_from_env("EMPTY_STORE")
VALID = "https://example.com/"


class SystemStore(unittest.TestCase):
    def test_no_store_env_trusts_a_valid_host(self):
        for script in PROBES:
            result = probe(BUN, script, VALID, env=node_env())
            self.assertTrue(result["trusted"], (script, result))

    def test_control_empty_store_rejects_the_same_host(self):
        env = node_env(SSL_CERT_FILE=EMPTY, SSL_CERT_DIR=os.environ["TEST_TMPDIR"])
        for script in PROBES:
            result = probe(BUN, script, VALID, env=env)
            self.assertFalse(result["trusted"], (script, result))
            self.assertIn(result["code"], STORE_REJECTED, (script, result))


if __name__ == "__main__":
    unittest.main()
