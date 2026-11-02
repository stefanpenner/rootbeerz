"""Proves that the hermetic test fails when it reads the wrong JDK. A test that cannot fail proves nothing."""

import importlib
import os
import unittest

from teethlib import failed_tests

OURS = os.environ["ROOTBEERZ_JDK"]
STOCK = os.environ["STOCK_JDK"]


def failures_with(ours, stock):
    os.environ["ROOTBEERZ_JDK"] = ours
    os.environ["STOCK_JDK"] = stock
    import hermetic_test

    module = importlib.reload(hermetic_test)
    return failed_tests(module.Keystore, module.Loopback)


class Teeth(unittest.TestCase):
    def test_stock_in_place_of_ours_fails_each_ours_test(self):
        failures = failures_with(STOCK, STOCK)
        self.assertEqual(
            failures,
            {
                "test_ours_cacerts_is_valid_and_empty",
                "test_ours_default_trust_manager_has_no_anchor",
                "test_ours_rejects_with_no_flag",
                "test_ours_ignores_ssl_cert_file",
            },
        )

    def test_ours_in_place_of_stock_fails_each_control(self):
        failures = failures_with(OURS, OURS)
        self.assertEqual(
            failures,
            {
                "test_control_stock_cacerts_holds_emsign",
                "test_control_stock_default_trust_manager_holds_emsign_root",
                "test_control_stock_rejects_because_the_ca_is_unknown",
            },
        )


if __name__ == "__main__":
    unittest.main()
