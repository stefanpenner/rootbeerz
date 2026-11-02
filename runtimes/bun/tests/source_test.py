"""Source tests. They read the pinned upstream tree and the patched tree. They need no Bun build."""

import hashlib
import json
import os
import pathlib
import tarfile
import tempfile
import unittest

import sourcelib

MANIFEST = pathlib.Path(os.environ["RUNTIME_MANIFEST"])
TARBALL = pathlib.Path(os.environ["BUN_TARBALL"])
PATCHED_MARKER = pathlib.Path(os.environ["BUN_PATCHED_MARKER"])


def manifest_entry():
    with MANIFEST.open() as stream:
        return json.load(stream)["versions"][0]


class Upstream(unittest.TestCase):
    """Each check must fail on the upstream source. Else the check proves nothing."""

    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory()
        with tarfile.open(TARBALL) as archive:
            members = [
                member
                for member in archive.getmembers()
                if member.name.split("/", 1)[-1]
                in {
                    sourcelib.BUNDLED_RS,
                    sourcelib.ROOT_CERTS_CPP,
                    sourcelib.TLS_TS,
                    sourcelib.ROOT_DER,
                    sourcelib.ROOT_LIST,
                    sourcelib.ROOT_DATA,
                }
            ]
            archive.extractall(cls.scratch.name, members=members, filter="data")
        cls.root = next(pathlib.Path(cls.scratch.name).iterdir())

    @classmethod
    def tearDownClass(cls):
        cls.scratch.cleanup()

    def test_tarball_matches_the_manifest(self):
        self.assertEqual(hashlib.sha256(TARBALL.read_bytes()).hexdigest(), manifest_entry()["sha256"])

    def test_every_check_fails_on_upstream(self):
        self.assertEqual(sourcelib.failures(self.root), [check.__name__ for check in sourcelib.CHECKS])


class Patched(unittest.TestCase):
    def test_patched_tree_passes_every_check(self):
        self.assertEqual(sourcelib.failures(PATCHED_MARKER.parent), [])


if __name__ == "__main__":
    unittest.main()
