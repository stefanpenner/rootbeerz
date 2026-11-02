"""Checks that runtimes/node/image/Dockerfile has no unpinned input. Bazel can cache this test."""

import os
import re
import unittest
from pathlib import Path

DOCKERFILE = Path(os.environ["DOCKERFILE"])
CONTEXT = Path(os.environ["BUILD_CONTEXT"])

SHA256 = r"[0-9a-f]{64}"
SNAPSHOT_URL = re.compile(
    r"^https://snapshot\.debian\.org/archive/debian/[0-9]{8}T[0-9]{6}Z/pool/main/.+_amd64\.deb$"
)
# These programs fetch or resolve versions at build time.
FORBIDDEN_RUN = re.compile(
    r"\b(apt|apt-get|aptitude|apk|yum|dnf|pip|pip3|npm|npx|gem|cargo|go|curl|wget|git)\b"
)
REQUIRED_PACKAGES = {
    "libc6",
    "libgcc-s1",
    "libstdc++6",
    "zlib1g",
    "libzstd1",
    "libxml2",
    "libicu72",
    "liblzma5",
    "busybox-static",
    # BusyBox sh runs its own applets before it looks at PATH. That hides the tool wrappers.
    "dash",
    # Bazel test scripts start with `#!/usr/bin/env bash` and use `set -o pipefail`.
    "bash",
    "libtinfo6",
}


def instructions():
    """Returns (keyword, argument) pairs. Joins continued lines and drops comments."""
    lines = []
    pending = ""
    for raw in DOCKERFILE.read_text().splitlines():
        stripped = raw.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        lines.append(pending + stripped)
        pending = ""
    if pending:
        lines.append(pending)
    return [(line.split(None, 1) + [""])[:2] for line in lines]


def of(keyword):
    return [arg for key, arg in instructions() if key.upper() == keyword]


def flags_and_rest(argument):
    flags = {}
    words = argument.split()
    while words and words[0].startswith("--"):
        name, _, value = words.pop(0)[2:].partition("=")
        flags[name] = value
    return flags, words


class EveryImageIsPinned(unittest.TestCase):
    def test_from_has_digest(self):
        stages = of("FROM")
        self.assertTrue(stages, "no FROM instruction")
        for argument in stages:
            _, words = flags_and_rest(argument)
            image = words[0]
            if image == "scratch":
                continue
            self.assertRegex(image, rf"@sha256:{SHA256}$", f"FROM {image} has no digest")

    def test_final_stage_is_scratch(self):
        _, words = flags_and_rest(of("FROM")[-1])
        self.assertEqual(words[0], "scratch")

    def test_copy_from_names_a_stage_or_digest(self):
        names = {w[2] for _, w in (flags_and_rest(a) for a in of("FROM")) if len(w) > 2}
        for argument in of("COPY"):
            flags, _ = flags_and_rest(argument)
            if "from" in flags:
                self.assertIn(flags["from"], names)


class EveryDownloadIsPinned(unittest.TestCase):
    def test_add_url_has_checksum(self):
        adds = of("ADD")
        self.assertTrue(adds, "no ADD instruction")
        for argument in adds:
            flags, words = flags_and_rest(argument)
            source = words[0]
            self.assertRegex(source, r"^https://", f"ADD source {source} is not an https URL")
            self.assertRegex(
                flags.get("checksum", ""), rf"^sha256:{SHA256}$", f"ADD {source} has no sha256"
            )

    def test_deb_urls_use_the_snapshot_archive(self):
        debs = [flags_and_rest(a)[1][0] for a in of("ADD") if ".deb" in a]
        self.assertGreaterEqual(len(debs), len(REQUIRED_PACKAGES))
        for url in debs:
            self.assertRegex(url, SNAPSHOT_URL.pattern)

    def test_no_download_or_resolver_in_run(self):
        for argument in of("RUN"):
            match = FORBIDDEN_RUN.search(argument)
            self.assertIsNone(match, f"RUN uses {match and match.group(0)}: {argument}")

    def test_no_arg_instruction(self):
        self.assertEqual(of("ARG"), [], "ARG can change an input")


class ImageHasTheRequiredContent(unittest.TestCase):
    def test_required_packages_are_present(self):
        text = DOCKERFILE.read_text()
        for package in REQUIRED_PACKAGES:
            self.assertRegex(text, rf"/{re.escape(package)}_", f"{package} is missing")

    def test_ca_bundle_comes_from_the_pinned_trust_file(self):
        copies = [flags_and_rest(a)[1] for a in of("COPY")]
        sources = [w[:-1] for w in copies]
        self.assertIn(["trust/mozilla-no-emsign.pem"], sources)

    def test_local_copy_sources_exist(self):
        for argument in of("COPY"):
            flags, words = flags_and_rest(argument)
            if "from" in flags:
                continue
            for source in words[:-1]:
                self.assertTrue((CONTEXT / source).exists(), f"{source} is not in the context")

    def test_dockerignore_allows_only_needed_paths(self):
        ignore = Path(str(DOCKERFILE) + ".dockerignore").read_text().split()
        self.assertEqual(ignore[0], "*")
        self.assertIn("!trust/mozilla-no-emsign.pem", ignore)

    def test_final_stage_only_copies_files(self):
        # The final stage starts at scratch, so only COPY can add files to it.
        final = []
        for key, argument in instructions():
            if key.upper() == "FROM":
                final = []
            else:
                final.append(key.upper())
        self.assertTrue(final)
        self.assertLessEqual(set(final), {"COPY", "USER", "WORKDIR", "ENV", "ENTRYPOINT", "CMD"})


class DocsMatch(unittest.TestCase):
    def test_docs_do_not_claim_unpinned_packages(self):
        text = (CONTEXT / "docs/HERMETICITY.md").read_text()
        self.assertNotIn("`apt-get` packages in that image are not pinned", text)
        self.assertIn("snapshot.debian.org", text)

    def test_readme_builds_with_the_repository_context(self):
        text = (CONTEXT / "README.md").read_text()
        self.assertIn("-f runtimes/node/image/Dockerfile", text)


if __name__ == "__main__":
    unittest.main()
