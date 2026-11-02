"""The manifests obey the contract, the Node manifest has no drift, and the CI matrix follows them."""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

KINDS = {"github-release", "pypi", "adoptium", "nodejs-dist"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")

MANIFESTS = [Path(path) for path in os.environ["RUNTIME_MANIFESTS"].split()]
MODULE = Path(os.environ["MODULE_BAZEL"]).read_text()
JQ = os.environ["JQ"]
FILTER = os.environ["MATRIX_FILTER"]


def manifests():
    return {path.parent.name: json.loads(path.read_text()) for path in MANIFESTS}


def module_node_versions():
    """Returns {major: (version, sha256)} from the node.version tags."""
    tags = re.findall(r"^node\.version\((.*?)^\)", MODULE, re.MULTILINE | re.DOTALL)
    found = {}
    for tag in tags:
        fields = dict(re.findall(r'^\s+(\w+) = "([^"]*)"', tag, re.MULTILINE))
        found[fields["major"]] = (fields["version"], fields["sha256"])
    return found


def matrix(scope, paths):
    """Runs the filter that the CI matrix step runs."""
    command = [JQ, "-c", "-s", "--arg", "scope", scope, "-f", FILTER, *map(str, paths)]
    return json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)


def legs(scope, paths):
    return {(l["runtime"], l["line"], l["os"]) for l in matrix(scope, paths)["include"]}


def manifest_file(folder, runtime, lines, systems=("macos-14", "ubuntu-latest")):
    path = Path(folder) / runtime / "manifest.json"
    path.parent.mkdir(exist_ok=True)
    versions = [
        {
            "line": line,
            "version": version,
            "sha256": "0" * 64,
            "target": f"//runtimes/{runtime}:{runtime}_{line}",
            "test": f"//runtimes/{runtime}/tests:{runtime}_{line}_tests",
            "system_ca_test": f"//runtimes/{runtime}/tests:system_ca_{line}_test",
            "artifact": f"bazel-bin/runtimes/{runtime}/{runtime}_{line}",
        }
        for line, version in lines
    ]
    path.write_text(json.dumps({"runtime": runtime, "os": list(systems), "versions": versions}))
    return path


class ManifestContract(unittest.TestCase):
    def test_node_has_a_manifest(self):
        self.assertIn("node", manifests())

    def test_each_manifest_obeys_the_contract(self):
        for path in MANIFESTS:
            manifest = json.loads(path.read_text())
            with self.subTest(path=str(path)):
                self.assertEqual(manifest["runtime"], path.parent.name)
                self.assertIn(manifest["upstream"]["kind"], KINDS)
                self.assertTrue(manifest["os"])
                self.assertTrue(manifest["versions"])
                for version in manifest["versions"]:
                    self.assertRegex(version["sha256"], SHA256)
                    for name in ("line", "version", "target", "test", "system_ca_test", "artifact"):
                        self.assertTrue(version[name], name)


class Layout(unittest.TestCase):
    """Each runtime owns runtimes/<runtime>/. Targets and artifacts keep the runtime name."""

    def versions(self):
        for runtime, manifest in manifests().items():
            for version in manifest["versions"]:
                yield runtime, version

    def test_each_manifest_lives_in_its_runtime_folder(self):
        for path in MANIFESTS:
            self.assertEqual(path.name, "manifest.json")
            self.assertEqual(path.parent.parent.name, "runtimes")

    def test_labels_and_artifacts_live_in_the_runtime_folder(self):
        for runtime, version in self.versions():
            with self.subTest(runtime=runtime, line=version["line"]):
                self.assertTrue(version["target"].startswith(f"//runtimes/{runtime}:"))
                self.assertTrue(version["test"].startswith(f"//runtimes/{runtime}/tests:"))
                self.assertTrue(version["artifact"].startswith(f"bazel-bin/runtimes/{runtime}/"))

    def test_no_target_or_artifact_carries_the_repo_name(self):
        for runtime, version in self.versions():
            for field in ("target", "test", "system_ca_test", "artifact"):
                with self.subTest(runtime=runtime, line=version["line"], field=field):
                    self.assertNotIn("rootbeer", version[field])

    def test_each_version_names_its_system_store_test(self):
        for runtime, version in self.versions():
            with self.subTest(runtime=runtime, line=version["line"]):
                self.assertEqual(version["system_ca_test"],
                                 f"//runtimes/{runtime}/tests:system_ca_{version['line']}_test")

    def test_module_labels_name_the_toolchain_or_a_runtime(self):
        labels = re.findall(r'"(//[^"]*)"', MODULE)
        self.assertTrue(labels)
        for label in labels:
            self.assertRegex(label, r"^//(toolchain|runtimes/[a-z]+)[/:]")


class NodeDrift(unittest.TestCase):
    def test_the_tag_parser_reads_every_tag(self):
        self.assertEqual(len(module_node_versions()), MODULE.count("\nnode.version("))
        self.assertTrue(module_node_versions())

    def test_node_manifest_equals_the_module_tags(self):
        listed = {v["line"]: (v["version"], v["sha256"]) for v in manifests()["node"]["versions"]}
        self.assertEqual(listed, module_node_versions())


class Matrix(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name

    def test_push_builds_every_version_on_every_system(self):
        path = manifest_file(self.folder, "deno", [("1", "1.46.3"), ("2", "2.1.0")])
        self.assertEqual(
            legs("all", [path]),
            {(r, l, o) for r in ["deno"] for l in ["1", "2"] for o in ["macos-14", "ubuntu-latest"]},
        )

    def test_pull_request_builds_the_oldest_and_the_newest_by_version_number(self):
        # The file order is wrong on purpose, and 9 sorts before 10 only as a number.
        lines = [("10", "10.0.0"), ("8", "8.1.0"), ("9", "9.0.0")]
        path = manifest_file(self.folder, "x", lines, ["ubuntu-latest"])
        self.assertEqual(legs("pr", [path]), {("x", "8", "ubuntu-latest"), ("x", "10", "ubuntu-latest")})

    def test_pull_request_handles_a_version_with_build_metadata(self):
        lines = [("17", "17.0.13+11"), ("21", "21.0.5+11")]
        path = manifest_file(self.folder, "jdk", lines, ["ubuntu-latest"])
        self.assertEqual(len(legs("pr", [path])), 2)

    def test_a_single_version_gives_one_leg_per_system(self):
        path = manifest_file(self.folder, "x", [("1", "1.0.0")])
        self.assertEqual(len(legs("pr", [path])), 2)

    def test_a_new_manifest_adds_legs_with_no_other_change(self):
        node = manifest_file(self.folder, "node", [("26", "26.0.0")], ["ubuntu-latest"])
        deno = manifest_file(self.folder, "deno", [("2", "2.0.0")], ["ubuntu-latest"])
        self.assertEqual({l[0] for l in legs("pr", [node])}, {"node"})
        self.assertEqual({l[0] for l in legs("pr", [node, deno])}, {"node", "deno"})

    def test_each_leg_carries_what_the_build_needs(self):
        path = manifest_file(self.folder, "deno", [("2", "2.1.0")], ["ubuntu-latest"])
        self.assertEqual(
            matrix("all", [path])["include"],
            [
                {
                    "runtime": "deno",
                    "line": "2",
                    "version": "2.1.0",
                    "target": "//runtimes/deno:deno_2",
                    "test": "//runtimes/deno/tests:deno_2_tests",
                    "system_ca_test": "//runtimes/deno/tests:system_ca_2_test",
                    "artifact": "bazel-bin/runtimes/deno/deno_2",
                    "os": "ubuntu-latest",
                }
            ],
        )

    def test_the_filter_refuses_a_system_that_is_not_hosted(self):
        # A manifest must not carry a pull request to a self-hosted runner.
        for system in ("self-hosted", "ser9", "ubuntu-ser9", "macos-self-hosted", "", "ubuntu-latest "):
            for scope in ("pr", "all"):
                with self.subTest(system=system, scope=scope):
                    path = manifest_file(self.folder, "x", [("1", "1.0.0")], [system])
                    with self.assertRaises(subprocess.CalledProcessError):
                        matrix(scope, [path])

    def test_the_filter_accepts_each_hosted_system(self):
        for system in ("ubuntu-latest", "ubuntu-24.04", "macos-14", "macos-latest"):
            path = manifest_file(self.folder, "x", [("1", "1.0.0")], [system])
            self.assertEqual(len(legs("pr", [path])), 1, system)

    def test_node_builds_on_macos_and_linux(self):
        self.assertEqual(manifests()["node"]["os"], ["macos-14", "ubuntu-latest"])

    def test_the_real_manifests_keep_todays_node_legs(self):
        self.assertEqual(
            {l[1] for l in legs("pr", MANIFESTS) if l[0] == "node"},
            {min(module_node_versions(), key=int), max(module_node_versions(), key=int)},
        )
        self.assertEqual(
            {l[1] for l in legs("all", MANIFESTS) if l[0] == "node"},
            set(module_node_versions()),
        )


if __name__ == "__main__":
    unittest.main()
