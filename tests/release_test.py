"""Checks the release workflow. A tag publishes the files of a green CI run on main.

The release builds nothing. It downloads the artifacts of the CI run for the
tagged commit, so the published files are the files that the tests checked.
"""

import os
import re
import unittest
from pathlib import Path

RELEASE = Path(os.environ["RELEASE_WORKFLOW"]).read_text()


def run_blocks():
    return re.findall(r"run: \|\n((?:          .*\n|\n)+)", RELEASE)


class Trigger(unittest.TestCase):
    def test_only_a_version_tag_starts_a_release(self):
        self.assertRegex(RELEASE, r"(?m)^on:\n  push:\n    tags: \['v\*'\]\n")
        self.assertNotIn("pull_request", RELEASE)
        self.assertNotIn("workflow_run", RELEASE)

    def test_the_token_can_write_releases_and_read_runs_only(self):
        self.assertRegex(RELEASE, r"(?m)^permissions:\n  contents: write\n  actions: read\n(?!  )")

    def test_the_job_uses_a_hosted_runner(self):
        self.assertEqual(re.findall(r"runs-on: (.*)", RELEASE), ["ubuntu-latest"])
        self.assertNotIn("vars.", RELEASE)


class Source(unittest.TestCase):
    def test_the_files_come_from_a_green_ci_push_run_of_the_tagged_commit(self):
        self.assertIn(
            'gh run list -R "$GITHUB_REPOSITORY" --workflow ci.yml --commit "$SHA" '
            "--event push --status success",
            RELEASE,
        )
        self.assertIn('gh run download "$run" -R "$GITHUB_REPOSITORY" --dir dist', RELEASE)

    def test_no_build_happens_in_the_release(self):
        self.assertNotIn("bazel", RELEASE)
        self.assertNotIn("setup-bazel", RELEASE)

    def test_the_release_lists_a_sha256_for_each_tarball(self):
        self.assertIn("sha256sum -- *.tar.gz > SHA256SUMS", RELEASE)
        self.assertIn('gh release create "$TAG"', RELEASE)

    def test_inputs_reach_the_shell_only_through_env(self):
        blocks = run_blocks()
        self.assertTrue(blocks)
        for block in blocks:
            self.assertNotIn("${{", block)

    def test_no_third_party_action(self):
        self.assertNotRegex(RELEASE, r"uses: (?!\./)")


if __name__ == "__main__":
    unittest.main()
