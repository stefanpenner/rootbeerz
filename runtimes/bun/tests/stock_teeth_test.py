"""Runs the rootbeerz tests with the stock Bun as the subject. The no-roots tests must fail."""

import os
import unittest

import teethlib

os.environ["ROOTBEER_BUN"] = os.environ["STOCK_BUN"]

import hermetic_test  # noqa: E402

MUST_FAIL = {
    "test_node_tls_reports_no_bundled_roots",
    "test_binary_holds_no_emsign_root",
    "test_binary_holds_no_pinned_store_root",
}


class StockBinary(unittest.TestCase):
    def test_every_no_roots_test_fails_on_stock(self):
        failed = teethlib.failed_tests(hermetic_test.Rootbeerz)
        self.assertEqual(sorted(MUST_FAIL - failed), [], "these tests pass on the stock binary")


if __name__ == "__main__":
    unittest.main()
