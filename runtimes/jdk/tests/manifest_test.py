"""Pins the shape of runtimes/jdk/manifest.json. MODULE.bazel reads no JDK pin of its own."""

import json
import os
import re
import unittest

with open(os.environ["MANIFEST"]) as stream:
    MANIFEST = json.load(stream)
with open(os.environ["MODULE_BAZEL"]) as stream:
    MODULE = stream.read()

SHA256 = re.compile(r"^[0-9a-f]{64}$")


class Manifest(unittest.TestCase):
    def test_contract_fields(self):
        self.assertEqual(MANIFEST["runtime"], "jdk")
        self.assertEqual(MANIFEST["upstream"]["kind"], "adoptium")
        self.assertIn("macos-14", MANIFEST["os"])

    def test_each_version_has_contract_fields(self):
        self.assertTrue(MANIFEST["versions"])
        for entry in MANIFEST["versions"]:
            line = entry["line"]
            self.assertEqual(entry["target"], f"//runtimes/jdk:jdk_{line}")
            self.assertEqual(entry["test"], f"//runtimes/jdk/tests:hermetic_{line}_test")
            self.assertEqual(entry["artifact"], f"bazel-bin/runtimes/jdk/jdk_{line}")
            self.assertRegex(entry["sha256"], SHA256)

    def test_source_and_stock_are_the_same_release(self):
        for entry in MANIFEST["versions"]:
            feature, build = entry["version"].split("+")
            self.assertTrue(feature.startswith(entry["line"] + "."))
            self.assertIn(f"/jdk-{feature}-ga.tar.gz", entry["source_url"])
            self.assertIn(f"jdk{entry['line']}u/", entry["source_url"])
            for name, stock in entry["stock"].items():
                with self.subTest(os=name):
                    self.assertIn(f"/jdk-{feature}%2B{build}/", stock["url"])
                    self.assertRegex(stock["sha256"], SHA256)

    def test_each_os_has_a_boot_jdk(self):
        for entry in MANIFEST["versions"]:
            self.assertEqual(set(entry["stock"]), {"darwin_arm64", "linux_x64"})

    def test_build_tools_are_pinned(self):
        self.assertEqual(set(MANIFEST["build_tools"]), {"make", "bash", "perl", "m4", "autoconf"})
        for tool in MANIFEST["build_tools"].values():
            self.assertRegex(tool["sha256"], SHA256)

    def test_module_holds_no_copy_of_a_pin(self):
        for entry in MANIFEST["versions"]:
            self.assertNotIn(entry["sha256"], MODULE)
            self.assertNotIn(entry["version"], MODULE)


if __name__ == "__main__":
    unittest.main()
