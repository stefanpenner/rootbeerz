"""Runs the hermetic tests with the stock Node in place of rootbeer. The trust tests must fail."""

import sys
import unittest

import hermetic_test
import teethlib

MUST_FAIL = {
    "test_root_certificates_is_empty",
    "test_bundled_flag_does_not_restore_roots",
    "test_node_options_does_not_restore_roots",
    "test_both_flags_are_accepted",
    "test_rootbeer_binary_holds_no_marker",
    "test_rootbeer_binary_holds_no_pem_certificate",
    "test_default_openssl_dir_is_etc_ssl",
    "test_ssl_cert_file_supplies_trust",
    "test_bundled_flag_keeps_the_local_store",
    "test_negated_store_flag_keeps_the_local_store",
    "test_ssl_cert_dir_supplies_trust",
}
if sys.platform != "darwin":
    # Stock Linux Node also sets OPENSSLDIR to /etc/ssl. Only macOS stock names another path.
    MUST_FAIL.discard("test_default_openssl_dir_is_etc_ssl")


class StockBinary(unittest.TestCase):
    def test_every_trust_test_fails_on_stock(self):
        failed = teethlib.failed_tests(
            hermetic_test.NoBundledRoots, hermetic_test.BinaryScan, hermetic_test.LocalStoreOnly)
        self.assertEqual(sorted(MUST_FAIL - failed), [], "these tests pass on the stock binary")


if __name__ == "__main__":
    unittest.main()
