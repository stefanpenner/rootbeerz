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
        key, value = self.output.read_text().rstrip("\n").split("=", 1)
        self.assertEqual(key, "legs")
        self.assertNotIn("\n", value)
        return json.loads(value)["include"]

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
