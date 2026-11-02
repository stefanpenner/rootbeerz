"""Runs as a Bazel test spawn. On Linux, .bazelrc puts each spawn in linux-sandbox.

The test reads the host paths that a leak would use. Each one must be empty or closed.
"""

import os
import sys
import unittest

# .bazelrc blocks these paths with --sandbox_block_path.
BLOCKED = ["/usr/include", "/usr/local", "/opt"]


def visible(path):
    """Returns the names under path that the spawn can read."""
    try:
        return os.listdir(path)
    except (FileNotFoundError, PermissionError, NotADirectoryError):
        return []


@unittest.skipUnless(sys.platform.startswith("linux"), "linux-sandbox exists on Linux only")
class HostTrees(unittest.TestCase):
    def test_each_blocked_tree_shows_nothing(self):
        for path in BLOCKED:
            self.assertEqual(visible(path), [], path)

    def test_the_host_c_header_is_hidden(self):
        self.assertFalse(os.access("/usr/include/stdio.h", os.R_OK))

    def test_tmp_is_private_to_the_spawn(self):
        # A hermetic tmp is empty when the spawn starts. The host /tmp of a CI runner is not.
        self.assertEqual([name for name in visible("/tmp") if not name.startswith("bazel")], [])


if __name__ == "__main__":
    unittest.main()
