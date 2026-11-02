"""Proves that this Deno trusts the system store of the host.

The hermetic test proves that this Deno bundles no root. So a trusted public
host with no store env proves that the trust comes from the system store.
The empty-store control proves that the store makes the decision.
"""

import tempfile
import unittest

from trustlib import STORE_REJECTED, node_env, path_from_env, probe

DENO = path_from_env("ROOTBEER_DENO")
PROBE = path_from_env("PROBE_TS")
EMPTY = path_from_env("EMPTY_STORE")
RUN_FLAGS = ("run", "--allow-all", "--no-config", "--no-lock")
VALID = "https://example.com/"


class SystemStore(unittest.TestCase):
    def setUp(self):
        self.deno_dir = tempfile.mkdtemp()

    def fetch(self, **store):
        env = node_env(DENO_DIR=self.deno_dir, DENO_NO_UPDATE_CHECK="1", **store)
        return probe(DENO, PROBE, VALID, *RUN_FLAGS, env=env)

    def test_no_store_env_trusts_a_valid_host(self):
        result = self.fetch()
        self.assertTrue(result["trusted"], result)

    def test_control_empty_store_rejects_the_same_host(self):
        result = self.fetch(SSL_CERT_FILE=EMPTY, SSL_CERT_DIR=self.deno_dir)
        self.assertFalse(result["trusted"], result)
        self.assertIn(result["code"], STORE_REJECTED, result)


if __name__ == "__main__":
    unittest.main()
