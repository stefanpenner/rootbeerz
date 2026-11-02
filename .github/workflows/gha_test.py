"""Tests each subcommand of gha.py. A fake replaces each external command and each download."""

import hashlib
import io
import json
import os
import stat
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import gha

BAZELVERSION = Path(os.environ["BAZELVERSION"]).read_text().strip()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Workspace(unittest.TestCase):
    """Gives each test an empty folder and the runner variables of a macOS arm64 job."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=os.environ.get("TEST_TMPDIR")))
        self.output = self.dir / "github_output"
        self.path_file = self.dir / "github_path"
        self.env = {
            "RUNNER_OS": "macOS",
            "RUNNER_ARCH": "ARM64",
            "RUNNER_TEMP": str(self.dir / "runner_temp"),
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_PATH": str(self.path_file),
        }
        self.calls = []
        patches = [
            mock.patch.dict(os.environ, self.env),
            mock.patch.object(gha, "call", self.fake_call),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.answers = {}

    def fake_call(self, argv, cwd=None):
        self.calls.append((list(argv), cwd))
        return self.answers.get(argv[1] if argv[0] == "gh" else argv[0], "")

    def main(self, *argv):
        err = io.StringIO()
        with redirect_stderr(err):
            code = gha.main(list(argv))
        return code, err.getvalue()


class InstallBazel(Workspace):
    def setUp(self):
        super().setUp()
        self.downloads = []
        patch = mock.patch.object(gha, "download", self.fake_download)
        patch.start()
        self.addCleanup(patch.stop)
        self.body = b"bazel"

    def fake_download(self, url):
        self.downloads.append(url)
        return self.body

    def pin(self, sha):
        return mock.patch.dict(gha.BAZEL_PINS, {("macOS", "ARM64"): ("darwin-arm64", sha)})

    def test_the_pinned_version_is_the_bazelversion(self):
        self.assertEqual(gha.BAZEL_VERSION, BAZELVERSION)

    def test_each_hosted_system_has_a_pin(self):
        self.assertEqual(set(gha.BAZEL_PINS), {("macOS", "ARM64"), ("Linux", "X64")})
        for suffix, sha in gha.BAZEL_PINS.values():
            self.assertRegex(sha, r"^[0-9a-f]{64}$")
            self.assertIn(suffix, {"darwin-arm64", "linux-x86_64"})

    def test_a_matching_download_installs_bazel_on_the_path(self):
        with self.pin(sha256(self.body)):
            self.assertEqual(self.main("install-bazel"), (0, ""))
        self.assertEqual(
            self.downloads,
            [f"https://github.com/bazelbuild/bazel/releases/download/{BAZELVERSION}/bazel-{BAZELVERSION}-darwin-arm64"],
        )
        installed = Path(self.env["RUNNER_TEMP"]) / "bin" / "bazel"
        self.assertEqual(installed.read_bytes(), self.body)
        self.assertTrue(installed.stat().st_mode & stat.S_IXUSR)
        self.assertEqual(self.path_file.read_text(), f"{installed.parent}\n")

    def test_a_changed_download_installs_nothing(self):
        with self.pin("0" * 64):
            code, err = self.main("install-bazel")
        self.assertEqual(code, 1)
        self.assertIn("sha256", err)
        self.assertFalse((Path(self.env["RUNNER_TEMP"]) / "bin" / "bazel").exists())
        self.assertFalse(self.path_file.exists())

    def test_a_system_with_no_pin_is_refused(self):
        with mock.patch.dict(os.environ, {"RUNNER_OS": "Windows", "RUNNER_ARCH": "X64"}):
            code, err = self.main("install-bazel")
        self.assertEqual(code, 1)
        self.assertIn("Windows-X64", err)
        self.assertEqual(self.downloads, [])


class LinuxSandbox(Workspace):
    """.bazelrc forces linux-sandbox on Linux. install-bazel makes sure that the runner can start one."""

    PROBE = "unshare"

    def setUp(self):
        super().setUp()
        self.env_patch({"RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "RUNNER_ENVIRONMENT": "github-hosted"})
        self.namespaces = False

    def env_patch(self, values):
        patch = mock.patch.dict(os.environ, values)
        patch.start()
        self.addCleanup(patch.stop)

    def fake_call(self, argv, cwd=None):
        self.calls.append((list(argv), cwd))
        if argv[0] == "sudo":
            self.namespaces = True
        if argv[0] == self.PROBE and not self.namespaces:
            raise gha.Refused("unshare exited 1")
        return ""

    def commands(self):
        return [argv[0] for argv, _ in self.calls]

    def test_a_runner_with_namespaces_changes_nothing(self):
        self.namespaces = True
        gha.allow_sandbox()
        self.assertEqual(self.commands(), ["unshare"])

    def test_a_hosted_runner_lifts_the_apparmor_limit_and_probes_again(self):
        gha.allow_sandbox()
        self.assertEqual(self.commands(), ["unshare", "sudo", "unshare"])
        self.assertEqual(self.calls[1][0], ["sudo", "sysctl", "-w", "kernel.apparmor_restrict_unprivileged_userns=0"])

    def test_a_self_hosted_runner_is_not_changed_and_is_refused(self):
        self.env_patch({"RUNNER_ENVIRONMENT": "self-hosted"})
        with self.assertRaises(gha.Refused) as caught:
            gha.allow_sandbox()
        self.assertIn("user namespaces", str(caught.exception))
        self.assertEqual(self.commands(), ["unshare"])

    def test_a_runner_that_still_has_no_namespaces_is_refused(self):
        def never(argv, cwd=None):
            self.calls.append((list(argv), cwd))
            if argv[0] == "unshare":
                raise gha.Refused("unshare exited 1")
            return ""
        with mock.patch.object(gha, "call", never), self.assertRaises(gha.Refused):
            gha.allow_sandbox()

    def test_macos_needs_no_probe(self):
        self.env_patch({"RUNNER_OS": "macOS", "RUNNER_ARCH": "ARM64"})
        gha.allow_sandbox()
        self.assertEqual(self.calls, [])

    def test_install_bazel_checks_the_sandbox(self):
        self.namespaces = True
        with mock.patch.object(gha, "download", lambda url: b"bazel"), \
                mock.patch.dict(gha.BAZEL_PINS, {("Linux", "X64"): ("linux-x86_64", sha256(b"bazel"))}):
            self.assertEqual(self.main("install-bazel"), (0, ""))
        self.assertEqual(self.commands(), ["unshare"])


class BuildAndTest(Workspace):
    def test_build_asks_for_the_dist_output_and_fits_the_machine(self):
        self.assertEqual(self.main("build", "--target", "//runtimes/node:dist_22"), (0, ""))
        self.assertEqual(self.calls, [([
            "bazel", "build", "--jobs=HOST_CPUS", "--local_resources=cpu=HOST_CPUS",
            "--local_resources=memory=HOST_RAM*.8", "//runtimes/node:dist_22", "--output_groups=+dist",
        ], None)])

    def test_test_runs_each_target_in_one_bazel_call(self):
        self.assertEqual(self.main("test", "//a:tests", "//a:system_ca_test"), (0, ""))
        self.assertEqual(self.calls, [([
            "bazel", "test", "--jobs=HOST_CPUS", "--local_resources=cpu=HOST_CPUS",
            "--local_resources=memory=HOST_RAM*.8", "//a:tests", "//a:system_ca_test",
        ], None)])

    def test_a_failed_command_fails_the_step(self):
        def fail(argv, cwd=None):
            raise gha.Refused("bazel test failed")
        with mock.patch.object(gha, "call", fail):
            self.assertEqual(self.main("test", "//a:t")[0], 1)


class Matrix(Workspace):
    def manifest(self, runtime, versions, systems=("macos-14", "ubuntu-latest")):
        path = self.dir / runtime / "manifest.json"
        path.parent.mkdir()
        path.write_text(json.dumps({
            "runtime": runtime,
            "os": list(systems),
            "versions": [{
                "line": line, "version": version, "sha256": "0" * 64,
                "target": f"//{runtime}:{line}", "test": f"//{runtime}:t{line}",
                "system_ca_test": f"//{runtime}:s{line}", "artifact": f"bazel-bin/{runtime}/{line}",
            } for line, version in versions],
        }))
        return str(path)

    def legs(self, scope, *paths):
        self.assertEqual(self.main("matrix", "--scope", scope, *paths), (0, ""))
        lines = self.output.read_text().splitlines()
        self.assertEqual([line.split("=", 1)[0] for line in lines], ["legs", "count"])
        include = json.loads(lines[0].split("=", 1)[1])["include"]
        self.assertEqual(lines[1], f"count={len(include)}")
        return include

    def test_a_leg_carries_what_the_build_needs(self):
        path = self.manifest("deno", [("2", "2.1.0")], ["ubuntu-latest"])
        self.assertEqual(self.legs("all", path), [{
            "runtime": "deno", "line": "2", "version": "2.1.0", "target": "//deno:2",
            "test": "//deno:t2", "system_ca_test": "//deno:s2", "artifact": "bazel-bin/deno/2",
            "os": "ubuntu-latest",
        }])

    def test_pr_scope_keeps_the_oldest_and_the_newest_by_number(self):
        path = self.manifest("node", [("9", "9.0.0"), ("10", "10.0.0"), ("16", "16.0.0")], ["macos-14"])
        self.assertEqual([l["line"] for l in self.legs("pr", path)], ["16", "9"])
        self.output.unlink()
        self.assertEqual([l["line"] for l in self.legs("all", path)], ["9", "10", "16"])

    def test_pr_scope_with_one_version_gives_one_leg_per_system(self):
        path = self.manifest("x", [("1", "1.0.0")])
        self.assertEqual(len(self.legs("pr", path)), 2)

    def test_a_system_that_is_not_hosted_is_refused(self):
        for system in ("self-hosted", "ser9", "ubuntu-ser9", "macos-self-hosted", "", "ubuntu-latest "):
            with self.subTest(system=system):
                path = self.manifest(f"x{len(system)}{system.strip() or 'e'}", [("1", "1.0.0")], [system])
                code, err = self.main("matrix", "--scope", "all", path)
                self.assertEqual(code, 1)
                self.assertIn("not a GitHub-hosted runner", err)

    def test_an_unknown_scope_is_refused(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            gha.main(["matrix", "--scope", "some"])


def manifest(runtime, versions, systems=("macos-14",)):
    return {
        "runtime": runtime,
        "os": list(systems),
        "versions": [{
            "line": line, "version": version, "target": f"//{runtime}:{line}", "test": f"//{runtime}:t{line}",
            "system_ca_test": f"//{runtime}:s{line}", "artifact": f"bazel-bin/{runtime}/{line}",
        } for line, version in versions],
    }


NODE = manifest("node", [("18", "18.0.0"), ("20", "20.0.0"), ("22", "22.0.0")])
DENO = manifest("deno", [("2", "2.1.0")])
MODULE = 'bazel_dep(name = "x")\nnode.version(\n    major = "22",\n    sha256 = "a",\n)\nuse_repo(node, "n")\n'


class Affected(unittest.TestCase):
    """A pull request builds only the legs that its changed files can change."""

    def chosen(self, changed, base=(NODE, DENO), head=(NODE, DENO), tags_only=False):
        picked = gha.affected(changed, gha.legs(list(base), "all"), list(head), tags_only)
        return [(leg["runtime"], leg["line"]) for leg in picked]

    def test_a_doc_change_builds_nothing(self):
        self.assertEqual(self.chosen(["README.md", "docs/a.md", "runtimes/jdk/README.md", "tests/ci_test.py"]), [])

    def test_a_file_in_a_runtime_builds_the_pr_legs_of_that_runtime(self):
        self.assertEqual(self.chosen(["runtimes/deno/patches/0001.patch"]), [("deno", "2")])
        self.assertEqual(self.chosen(["runtimes/node/build_node.py"]), [("node", "18"), ("node", "22")])

    def test_a_new_version_builds_only_that_leg(self):
        head = manifest("node", [("18", "18.0.0"), ("20", "20.0.0"), ("22", "22.0.0"), ("24", "24.0.0")])
        self.assertEqual(self.chosen(["runtimes/node/manifest.json"], head=(head, DENO)), [("node", "24")])

    def test_a_changed_version_builds_only_that_leg(self):
        head = manifest("node", [("18", "18.0.0"), ("20", "20.0.1"), ("22", "22.0.0")])
        self.assertEqual(self.chosen(["runtimes/node/manifest.json"], head=(head, DENO)), [("node", "20")])

    def test_a_new_runtime_builds_each_of_its_legs(self):
        bun = manifest("bun", [("1", "1.0.0")], ["macos-14", "ubuntu-latest"])
        self.assertEqual(self.chosen(["runtimes/bun/manifest.json"], head=(NODE, DENO, bun)), [("bun", "1"), ("bun", "1")])

    def test_a_file_outside_the_runtimes_builds_the_pr_legs_of_each_runtime(self):
        for path in ("MODULE.bazel", "MODULE.bazel.lock", ".bazelrc", "toolchain/cc/BUILD.bazel",
                     ".github/workflows/ci.yml", ".github/workflows/gha.py", ".github/actions/build/action.yml", "x"):
            with self.subTest(path=path):
                self.assertEqual(self.chosen([path]), [("node", "18"), ("node", "22"), ("deno", "2")])

    def test_node_tags_in_module_bazel_follow_the_manifest(self):
        head = manifest("node", [("18", "18.0.0"), ("20", "20.0.0"), ("22", "22.0.0"), ("24", "24.0.0")])
        changed = ["MODULE.bazel", "MODULE.bazel.lock", "runtimes/node/manifest.json"]
        self.assertEqual(self.chosen(changed, head=(head, DENO), tags_only=True), [("node", "24")])

    def test_a_lock_change_with_no_module_change_builds_the_pr_legs(self):
        self.assertEqual(self.chosen(["MODULE.bazel.lock"], tags_only=True), [("node", "18"), ("node", "22"), ("deno", "2")])

    def test_only_node_tags_differ(self):
        bumped = MODULE.replace('major = "22"', 'major = "24"')
        added = MODULE.replace("use_repo", 'node.version(\n    major = "26",\n    old_v8 = True,\n)\nuse_repo')
        self.assertTrue(gha.node_tags_only(MODULE, bumped))
        self.assertTrue(gha.node_tags_only(MODULE, added))
        self.assertFalse(gha.node_tags_only(MODULE, MODULE.replace('"x"', '"y"')))


class AffectedMatrix(Workspace):
    """matrix --base asks git for the changed files and the base manifests."""

    def fake_call(self, argv, cwd=None):
        self.calls.append((list(argv), cwd))
        files = {
            "base:runtimes/node/manifest.json": json.dumps(NODE),
            "base:runtimes/deno/manifest.json": json.dumps(DENO),
            "base:MODULE.bazel": MODULE,
        }
        if argv[:2] == ["git", "diff"]:
            return "runtimes/deno/build_deno.py\n"
        if argv[:2] == ["git", "ls-tree"]:
            return "runtimes/node/manifest.json\nruntimes/node/BUILD.bazel\nruntimes/deno/manifest.json\n"
        if argv[:2] == ["git", "show"]:
            return files[argv[2]]
        raise AssertionError(argv)

    def test_base_selects_the_affected_legs_and_counts_them(self):
        for runtime in (NODE, DENO):
            path = self.dir / "runtimes" / runtime["runtime"] / "manifest.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(runtime))
        (self.dir / "MODULE.bazel").write_text(MODULE)
        event = self.dir / "event.json"
        event.write_text(json.dumps({"pull_request": {"base": {"sha": "base"}}}))
        patch = mock.patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event)})
        patch.start()
        self.addCleanup(patch.stop)
        cwd = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, cwd)
        self.assertEqual(self.main("matrix", "--scope", "pr", "--affected"), (0, ""))
        lines = dict(line.split("=", 1) for line in self.output.read_text().splitlines())
        self.assertEqual([leg["runtime"] for leg in json.loads(lines["legs"])["include"]], ["deno"])
        self.assertEqual(lines["count"], "1")
        self.assertIn(["git", "diff", "--no-renames", "--name-only", "base", "HEAD"], [argv for argv, _ in self.calls])

    def test_affected_needs_the_pr_scope(self):
        self.assertEqual(self.main("matrix", "--scope", "all", "--affected")[0], 1)


class Collect(Workspace):
    def setUp(self):
        super().setUp()
        os.chdir(self.dir)

    def test_a_tarball_and_its_sha256_are_copied_as_they_are(self):
        Path("bin").mkdir()
        Path("bin/node-v1-darwin-arm64.tar.gz").write_bytes(b"t")
        Path("bin/node-v1-darwin-arm64.tar.gz.sha256").write_text("kept\n")
        self.assertEqual(self.main("collect", "--runtime", "node", "--version", "1",
                                   "--artifact", "bin/node-v1-*.tar.gz*"), (0, ""))
        self.assertEqual(sorted(os.listdir("out")), ["node-v1-darwin-arm64.tar.gz", "node-v1-darwin-arm64.tar.gz.sha256"])
        self.assertEqual(Path("out/node-v1-darwin-arm64.tar.gz.sha256").read_text(), "kept\n")

    def test_another_artifact_becomes_a_named_tarball_with_a_sha256(self):
        Path("bin/jdk_25/bin").mkdir(parents=True)
        Path("bin/jdk_25/bin/java").write_text("java")
        self.assertEqual(self.main("collect", "--runtime", "jdk", "--version", "25.0.5+8",
                                   "--artifact", "bin/jdk_25"), (0, ""))
        name = "jdk-25.0.5+8-macOS-ARM64.tar.gz"
        self.assertEqual(sorted(os.listdir("out")), [name, name + ".sha256"])
        data = Path("out", name).read_bytes()
        self.assertEqual(Path("out", name + ".sha256").read_text(), f"{sha256(data)}  {name}\n")
        with tarfile.open(Path("out", name)) as tar:
            self.assertIn("jdk-25.0.5+8-macOS-ARM64/jdk_25/bin/java", tar.getnames())

    def test_a_symlinked_artifact_is_followed(self):
        Path("real").write_text("deno")
        Path("bin").mkdir()
        os.symlink(Path("real").resolve(), "bin/deno_2")
        self.main("collect", "--runtime", "deno", "--version", "2.9.8", "--artifact", "bin/deno_2")
        with tarfile.open("out/deno-2.9.8-macOS-ARM64.tar.gz") as tar:
            member = tar.getmember("deno-2.9.8-macOS-ARM64/deno_2")
            self.assertTrue(member.isfile())

    def test_no_match_is_refused(self):
        code, err = self.main("collect", "--runtime", "x", "--version", "1", "--artifact", "bin/none*")
        self.assertEqual(code, 1)
        self.assertIn("bin/none*", err)


class Release(Workspace):
    def setUp(self):
        super().setUp()
        os.chdir(self.dir)
        self.answers["run"] = ""

    def download_into(self, files):
        real = self.fake_call

        def fake(argv, cwd=None):
            if argv[:3] == ["gh", "run", "download"]:
                dist = Path(argv[argv.index("--dir") + 1])
                for name, data in files.items():
                    (dist / name).parent.mkdir(parents=True, exist_ok=True)
                    (dist / name).write_bytes(data)
            return real(argv, cwd)
        return mock.patch.object(gha, "call", fake)

    def release(self):
        return self.main("release", "--repo", "o/r", "--sha", "abc", "--tag", "v1")

    def test_no_green_ci_run_publishes_nothing(self):
        code, err = self.release()
        self.assertEqual(code, 1)
        self.assertIn("No green CI push run for abc", err)
        self.assertEqual([c[0][:3] for c in self.calls], [["gh", "run", "list"]])

    def test_the_files_come_from_the_green_ci_push_run_of_the_commit(self):
        self.answers["run"] = "42\n"
        with self.download_into({"a-macOS/a.tar.gz": b"a", "b-Linux/b.tar.gz": b"b", "b-Linux/b.tar.gz.sha256": b"x"}):
            self.assertEqual(self.release(), (0, ""))
        argvs = [c[0] for c in self.calls]
        self.assertEqual(argvs[0], [
            "gh", "run", "list", "-R", "o/r", "--workflow", "ci.yml", "--commit", "abc",
            "--event", "push", "--status", "success", "--limit", "1",
            "--json", "databaseId", "--jq", ".[0].databaseId // empty",
        ])
        self.assertEqual(argvs[1][:6], ["gh", "run", "download", "42", "-R", "o/r"])
        create, cwd = self.calls[2]
        self.assertEqual(create, [
            "gh", "release", "create", "v1", "-R", "o/r", "--title", "v1", "--notes-file", "notes.md",
            "--", "a.tar.gz", "b.tar.gz", "SHA256SUMS",
        ])
        self.assertEqual(Path(cwd, "SHA256SUMS").read_text(),
                         f"{sha256(b'a')}  a.tar.gz\n{sha256(b'b')}  b.tar.gz\n")
        self.assertIn("sha256sum -c SHA256SUMS", Path(cwd, "notes.md").read_text())

    def test_a_run_with_no_tarball_publishes_nothing(self):
        self.answers["run"] = "42"
        with self.download_into({"x/readme.txt": b"r"}):
            code, err = self.release()
        self.assertEqual(code, 1)
        self.assertIn("no .tar.gz", err)
        self.assertNotIn("release", [c[0][1] for c in self.calls])

    def test_two_tarballs_with_one_name_publish_nothing(self):
        self.answers["run"] = "42"
        with self.download_into({"x/a.tar.gz": b"1", "y/a.tar.gz": b"2"}):
            code, err = self.release()
        self.assertEqual(code, 1)
        self.assertIn("a.tar.gz", err)


if __name__ == "__main__":
    unittest.main()
