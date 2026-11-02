"""Proves that the pinned build inputs agree with the pinned Bun source. It needs no Bun build."""

import json
import os
import pathlib
import re
import unittest

INPUTS = json.loads(pathlib.Path(os.environ["BUN_INPUTS"]).read_text())
MANIFEST = json.loads(pathlib.Path(os.environ["RUNTIME_MANIFEST"]).read_text())
CARGO_LOCK = pathlib.Path(os.environ["BUN_CARGO_LOCK"]).read_text()


def locked_crates():
    crates = {}
    for block in CARGO_LOCK.split("[[package]]")[1:]:
        if 'source = "registry+' not in block:
            continue
        name = re.search(r'^name = "(.+)"$', block, re.M).group(1)
        version = re.search(r'^version = "(.+)"$', block, re.M).group(1)
        crates[f"{name}-{version}"] = re.search(r'^checksum = "(.+)"$', block, re.M).group(1)
    return crates


class Inputs(unittest.TestCase):
    def test_reference_matches_the_manifest(self):
        self.assertEqual(INPUTS["bun_ref"], "bun-v" + MANIFEST["versions"][0]["version"])

    def test_every_locked_crate_is_pinned_with_its_checksum(self):
        pinned = {f"{crate['name']}-{crate['version']}": crate["sha256"] for crate in INPUTS["crates"]}
        locked = locked_crates()
        self.assertGreater(len(locked), 0)
        self.assertEqual({name: pinned.get(name) for name in locked}, locked)

    def test_each_input_has_a_checksum(self):
        for kind in ("archives", "deps", "crates"):
            for item in INPUTS[kind]:
                self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$", item)
        for item in INPUTS["npm"]:
            self.assertTrue(item["integrity"].startswith("sha512-"), item)

    def test_no_input_holds_the_root_data(self):
        # Bun takes its roots from its own tree, not from a dependency.
        self.assertNotIn("webpki-roots", {crate["name"] for crate in INPUTS["crates"]})


if __name__ == "__main__":
    unittest.main()
