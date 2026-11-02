"""Checks the CI workflow. A pull request must wait for approval and use a hosted runner.

The checks read the YAML as text, because the build has no YAML parser.
Bazel can cache this test.
"""

import os
import re
import unittest
from pathlib import Path

WORKFLOW = Path(os.environ["WORKFLOW"]).read_text()
ACTION = Path(os.environ["BUILD_ACTION"]).read_text()
SETUP = Path(os.environ["SETUP_BAZEL_ACTION"]).read_text()
BAZEL_VERSION = Path(os.environ["BAZELVERSION"]).read_text().strip()
HOSTED = re.compile(r"^(ubuntu|macos|windows)-[a-z0-9.]+$")
NOT_PR = "github.event_name != 'pull_request'"
ONLY_PR = "github.event_name == 'pull_request'"
LINUX_USES_THE_VARIABLE = (
    "${{ startsWith(matrix.os, 'ubuntu-') && "
    "fromJSON(vars.CI_RUNS_ON || '\"ubuntu-latest\"') || matrix.os }}"
)
# These jobs turn runtimes/*/manifest.json into the build matrix. They run jq and no build.
MATRIX_JOBS = {"matrix": "all", "matrix-pr": "pr"}


def triggers():
    block = re.search(r"^on:\n((?:[ ]+.*\n|\n)+)", WORKFLOW, re.MULTILINE).group(1)
    return set(re.findall(r"^  ([a-z_]+):", block, re.MULTILINE))


def jobs():
    """Returns {name: text} for each job under the top-level jobs key."""
    body = WORKFLOW.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([a-z0-9_-]+):\n", body, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


def field(text, name):
    match = re.search(rf"^    {name}: (.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


class Triggers(unittest.TestCase):
    def test_only_the_allowed_triggers(self):
        self.assertEqual(triggers(), {"push", "workflow_dispatch", "pull_request"})

    def test_push_runs_on_main_only(self):
        self.assertRegex(WORKFLOW, r"push:\n\s+branches: \[main\]")

    def test_no_pull_request_target(self):
        self.assertNotIn("pull_request_target", WORKFLOW)

    def test_token_is_read_only(self):
        self.assertRegex(WORKFLOW, r"(?m)^permissions:\n  contents: read\n")


class PullRequestJobs(unittest.TestCase):
    def test_there_is_a_pull_request_job(self):
        self.assertTrue([n for n, t in jobs().items() if field(t, "if") == ONLY_PR])

    def test_pull_request_jobs_wait_for_approval(self):
        for name, text in jobs().items():
            if field(text, "if") == ONLY_PR:
                self.assertEqual(field(text, "environment"), "pr-approval", name)

    def test_pull_request_jobs_use_a_hosted_runner(self):
        for name, text in jobs().items():
            if field(text, "if") == ONLY_PR:
                runs_on = field(text, "runs-on")
                if runs_on == "${{ matrix.os }}":
                    # The manifests name the systems. runtimes_test proves that the
                    # filter refuses each system that is not hosted.
                    self.assertIn("fromJSON(needs.matrix-pr.outputs.legs)", text, name)
                else:
                    self.assertRegex(runs_on, HOSTED, name)

    def test_pull_request_jobs_never_read_the_runner_variable(self):
        for name, text in jobs().items():
            if field(text, "if") == ONLY_PR:
                self.assertNotIn("vars.", text, name)
                self.assertNotIn("self-hosted", text, name)


class PushJobs(unittest.TestCase):
    def test_every_job_is_gated_on_the_event(self):
        for name, text in jobs().items():
            self.assertIn(field(text, "if"), {ONLY_PR, NOT_PR}, f"{name} runs on every event")

    def test_only_push_jobs_read_the_runner_variable(self):
        for name, text in jobs().items():
            if "vars." in text or "self-hosted" in text:
                self.assertEqual(field(text, "if"), NOT_PR, name)
                self.assertNotIn("environment", text, name)

    def test_push_jobs_use_the_runner_variable_with_a_hosted_fallback(self):
        selected = {n: t for n, t in jobs().items() if field(t, "if") == NOT_PR}
        self.assertTrue(selected)
        for name, text in selected.items():
            runs_on = field(text, "runs-on")
            if name in MATRIX_JOBS:
                # jq is on the hosted image. A self-hosted runner may not have it.
                self.assertEqual(runs_on, "ubuntu-latest", name)
            elif "matrix:" in text:
                # Only Linux uses the variable. macOS stays on GitHub.
                self.assertEqual(runs_on, LINUX_USES_THE_VARIABLE, name)
            else:
                self.assertEqual(runs_on, "${{ fromJSON(vars.CI_RUNS_ON || '\"ubuntu-latest\"') }}", name)


class MatrixJobs(unittest.TestCase):
    def test_each_event_has_one_matrix_job(self):
        self.assertEqual(field(jobs()["matrix"], "if"), NOT_PR)
        self.assertEqual(field(jobs()["matrix-pr"], "if"), ONLY_PR)

    def test_each_matrix_job_reads_every_manifest_with_its_scope(self):
        for name, scope in MATRIX_JOBS.items():
            text = jobs()[name]
            self.assertIn(f"          legs=$(jq -c -s --arg scope {scope} -f .github/workflows/runtime-matrix.jq runtimes/*/manifest.json)\n", text)

    def test_matrix_jobs_only_run_jq(self):
        for name in MATRIX_JOBS:
            text = jobs()[name]
            self.assertNotIn("bazel", text, name)
            self.assertNotIn("secrets.", text, name)

    def test_source_tests_run_the_manifest_checks(self):
        for name in ("source-tests", "source-tests-pr"):
            self.assertIn("//tests:runtimes_test", jobs()[name], name)

    def test_no_runtime_list_in_the_workflow(self):
        self.assertNotRegex(WORKFLOW, r"(?m)^\s+(node|runtime|version|line|os): \[")


class Build(unittest.TestCase):
    def build_jobs(self):
        return {n: t for n, t in jobs().items() if "strategy:" in t}

    def test_every_job_installs_the_pinned_bazel(self):
        for name, text in jobs().items():
            if name in MATRIX_JOBS:
                continue
            self.assertIn("uses: ./.github/actions/", text, name)
            self.assertTrue(
                "./.github/actions/setup-bazel" in text or "./.github/actions/build" in text, name
            )

    def test_both_events_have_a_build_matrix(self):
        events = {field(t, "if") for t in self.build_jobs().values()}
        self.assertEqual(events, {ONLY_PR, NOT_PR})

    def test_each_build_job_reads_the_matrix_of_its_event(self):
        for name, text in self.build_jobs().items():
            source = "matrix-pr" if field(text, "if") == ONLY_PR else "matrix"
            self.assertIn(f"      matrix: ${{{{ fromJSON(needs.{source}.outputs.legs) }}}}\n", text, name)

    def test_build_jobs_use_the_build_action(self):
        for name, text in self.build_jobs().items():
            self.assertIn("uses: ./.github/actions/build", text, name)
            for key in ("runtime", "line", "version", "target", "test", "system_ca_test", "artifact"):
                self.assertIn(f"{key}: ${{{{ matrix.{key} }}}}", text, name)

    def test_build_jobs_wait_only_for_their_matrix_job(self):
        # One approval must release every leg. A leg never waits for another leg.
        # A pull request has two approvals: first the matrix job, then the legs.
        for name, text in self.build_jobs().items():
            source = "matrix-pr" if field(text, "if") == ONLY_PR else "matrix"
            self.assertEqual(field(text, "needs"), source, name)

    def test_build_jobs_keep_going_when_one_leg_fails(self):
        for name, text in self.build_jobs().items():
            self.assertIn("fail-fast: false", text, name)

    def test_build_jobs_have_a_long_timeout(self):
        for name, text in self.build_jobs().items():
            self.assertGreaterEqual(int(field(text, "timeout-minutes")), 90, name)


class SetupBazelAction(unittest.TestCase):
    def test_bazel_comes_from_a_pinned_release(self):
        self.assertIn(f"/releases/download/{BAZEL_VERSION}/bazel-{BAZEL_VERSION}-", SETUP)
        self.assertIn("darwin-arm64", SETUP)
        self.assertIn("linux-x86_64", SETUP)

    def test_each_bazel_download_has_a_sha256(self):
        checksums = re.findall(r"\b[0-9a-f]{64}\b", SETUP)
        self.assertGreaterEqual(len(set(checksums)), 2)
        self.assertRegex(SETUP, r"shasum -a 256 -c")

    def test_no_bazelisk_and_no_unpinned_download(self):
        self.assertNotIn("bazelisk", SETUP.lower())
        self.assertNotIn("/latest", SETUP)

class BuildAction(unittest.TestCase):
    def test_build_uses_the_pinned_bazel(self):
        self.assertIn("uses: ./.github/actions/setup-bazel", ACTION)

    def test_build_runs_the_tests_and_uploads_the_artifact(self):
        self.assertIn("bazel build", ACTION)
        self.assertIn("--output_groups=+dist", ACTION)
        self.assertIn('"$TARGET"', ACTION)
        self.assertIn('bazel test', ACTION)
        self.assertIn('"$TEST" "$SYSTEM_CA_TEST"', ACTION)
        self.assertIn("upload-artifact@", ACTION)
        self.assertIn("name: ${{ inputs.runtime }}-${{ inputs.line }}-${{ runner.os }}-${{ runner.arch }}", ACTION)
        self.assertIn("if-no-files-found: error", ACTION)

    def test_each_upload_is_a_tarball_with_a_sha256(self):
        # A release publishes these files as they are, so each one must be a download.
        self.assertIn('tar -C stage -czf "out/$NAME.tar.gz" "$NAME"', ACTION)
        self.assertIn('shasum -a 256 "$f" > "$f.sha256"', ACTION)
        self.assertIn("NAME: ${{ inputs.runtime }}-${{ inputs.version }}-${{ runner.os }}-${{ runner.arch }}", ACTION)

    def test_inputs_reach_the_shell_only_through_env(self):
        for block in re.findall(r"run: \|\n((?:        .*\n)+)", ACTION):
            self.assertNotIn("${{", block)

    def test_build_fits_the_machine(self):
        # .bazelrc asks for 10 CPUs. A runner has 2 to 4.
        self.assertIn("--local_resources=cpu=HOST_CPUS", ACTION)
        self.assertIn("--jobs=HOST_CPUS", ACTION)


class PinnedActions(unittest.TestCase):
    def test_every_action_has_a_commit_sha(self):
        for text in (WORKFLOW, ACTION, SETUP):
            for use in re.findall(r"^\s+(?:- )?uses: (\S+)", text, re.MULTILINE):
                if use.startswith("./"):
                    continue
                self.assertRegex(use, r"@[0-9a-f]{40}$", use)


    def test_no_job_hardcodes_self_hosted(self):
        self.assertNotRegex(WORKFLOW, r"runs-on: \[?\s*self-hosted")


if __name__ == "__main__":
    unittest.main()
