"""Proves that this Node trusts the system store of the host.

The hermetic test proves that this Node bundles no root. So a trusted public
host with no store env proves that the trust comes from the system store.
The empty-store control proves that the store makes the decision.
"""

import tempfile
import unittest

from trustlib import STORE_REJECTED, node_env, path_from_env, probe, store_env

NODE = path_from_env("ROOTBEER_NODE")
PROBE = path_from_env("PROBE_JS")
EMPTY = path_from_env("EMPTY_STORE")
VALID = "https://example.com/"


class SystemStore(unittest.TestCase):
    def test_no_store_env_trusts_a_valid_host(self):
        result = probe(NODE, PROBE, VALID, env=node_env())
        self.assertTrue(result["trusted"], result)

    def test_control_empty_store_rejects_the_same_host(self):
        result = probe(NODE, PROBE, VALID, env=store_env(EMPTY, tempfile.mkdtemp()))
        self.assertFalse(result["trusted"], result)
        self.assertIn(result["code"], STORE_REJECTED, result)


if __name__ == "__main__":
    unittest.main()
