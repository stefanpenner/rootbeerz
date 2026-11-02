"""Tests the host-leak guard of the Bun build driver. It needs no Bun build."""

import importlib.util
import os
import pathlib
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("build_bun", os.environ["BUILD_BUN"])
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


class CargoConfigGuard(unittest.TestCase):
    def test_finds_a_config_in_a_parent_directory(self):
        with tempfile.TemporaryDirectory() as root:
            top = pathlib.Path(root).resolve()
            (top / ".cargo").mkdir()
            (top / ".cargo/config.toml").write_text("[build]\nrustc-wrapper = 'x'\n")
            self.assertEqual(DRIVER.parent_cargo_configs(top / "a/b"), [top / ".cargo/config.toml"])

    def test_finds_the_legacy_name(self):
        with tempfile.TemporaryDirectory() as root:
            top = pathlib.Path(root).resolve()
            (top / ".cargo").mkdir()
            (top / ".cargo/config").write_text("")
            self.assertEqual(DRIVER.parent_cargo_configs(top / "a"), [top / ".cargo/config"])

    def test_clean_tree_has_no_config(self):
        with tempfile.TemporaryDirectory() as root:
            top = pathlib.Path(root).resolve()
            self.assertEqual([p for p in DRIVER.parent_cargo_configs(top / "a") if top in p.parents], [])


class NpmCacheFolder(unittest.TestCase):
    def test_release_version(self):
        self.assertEqual(DRIVER.npm_cache_folder("@types/node", "26.2.0"), "@types/node@26.2.0@@@1")

    def test_recorded_prerelease(self):
        self.assertEqual(
            DRIVER.npm_cache_folder("querystring-es3", "1.0.0-0"), "querystring-es3@1.0.0-b8f8325b21a8a1e5@@@1"
        )

    def test_unknown_prerelease_is_refused(self):
        with self.assertRaises(SystemExit):
            DRIVER.npm_cache_folder("other", "1.0.0-beta")


if __name__ == "__main__":
    unittest.main()
