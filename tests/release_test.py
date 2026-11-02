"""Checks the release workflow. A tag publishes the files of a green CI run on main.

The release builds nothing. It downloads the artifacts of the CI run for the
tagged commit, so the published files are the files that the tests checked.
"""

import os
import re
import unittest
from pathlib import Path

RELEASE = Path(os.environ["RELEASE_WORKFLOW"]).read_text()


CHECKOUT = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
RUN = 'python3 .github/workflows/gha.py release --repo "$GITHUB_REPOSITORY" --sha "$SHA" --tag "$TAG"'


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
    # gha_test proves what gha.py release runs: the green CI push run, SHA256SUMS, and the refusals.
    def test_one_step_publishes_with_the_cli(self):
        self.assertEqual(re.findall(r"run: (.*)", RELEASE), [RUN])

    def test_the_cli_gets_the_tagged_commit_and_the_tag(self):
        self.assertIn("SHA: ${{ github.sha }}", RELEASE)
        self.assertIn("TAG: ${{ github.ref_name }}", RELEASE)
        self.assertIn("GH_TOKEN: ${{ github.token }}", RELEASE)

    def test_no_build_happens_in_the_release(self):
        self.assertNotIn("bazel", RELEASE)
        self.assertNotIn("setup-bazel", RELEASE)

    def test_the_only_action_is_a_pinned_checkout_that_keeps_no_token(self):
        self.assertEqual(re.findall(r"uses: (\S+)", RELEASE), [CHECKOUT])
        self.assertRegex(RELEASE, re.escape(CHECKOUT) + r".*\n\s+with:\n\s+persist-credentials: false\n")


if __name__ == "__main__":
    unittest.main()
