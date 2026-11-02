#!/usr/bin/env python3
"""Adds or bumps a Node release in the repository.

Usage: add_node_version.py <X.Y.Z> [--old-v8] [--shasums FILE] [--no-patches]

It edits these files:
  MODULE.bazel                one node.version tag, replacing the tag of the same line
  runtimes/node/manifest.json          the entry of the line. The CI matrix comes from this file.
  README.md                   the version table and the prose that lists the lines
  STATUS.md                   the row of the line in the Result table

The three sha256 values come from the release SHASUMS256.txt on nodejs.org.
Use --shasums for a local copy. For a new line, it then downloads the source tarball,
checks its sha256, and runs make_patches.py for the line. That fails with a message when
the line needs a new family. See docs/ADDING_A_VERSION.md.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
DIST = "https://nodejs.org/dist/v{0}/"


def die(message):
    raise SystemExit(f"add_node_version: {message}")


def hashes(version, shasums):
    found = {}
    for line in shasums.splitlines():
        digest, _, name = line.partition("  ")
        found[name.strip()] = digest.strip()
    wanted = {
        "sha256": f"node-v{version}.tar.gz",
        "stock_darwin_arm64_sha256": f"node-v{version}-darwin-arm64.tar.gz",
        "stock_linux_x64_sha256": f"node-v{version}-linux-x64.tar.gz",
    }
    result = {}
    for key, name in wanted.items():
        if not re.fullmatch(r"[0-9a-f]{64}", found.get(name, "")):
            die(f"SHASUMS256.txt has no sha256 for {name}")
        result[key] = found[name]
    return result


def tag_text(major, version, digests, old_v8):
    lines = [
        "node.version(",
        f'    major = "{major}",',
        f'    version = "{version}",',
    ] + [f'    {key} = "{value}",' for key, value in digests.items()]
    if old_v8:
        lines.append("    old_v8 = True,")
    return "\n".join(lines) + "\n)\n"


def prose(majors):
    ordered = sorted(majors, key=int)
    if len(ordered) == 1:
        return ordered[0]
    return ", ".join(ordered[:-1]) + ", and " + ordered[-1]


def set_manifest(text, major, version, sha256):
    """Replaces or adds the entry of the line. The lines stay newest first."""
    manifest = json.loads(text)
    entry = {
        "line": major,
        "version": version,
        "sha256": sha256,
        "target": f"//runtimes/node:dist_{major}",
        "test": f"//runtimes/node/tests:node_{major}_tests",
        "system_ca_test": f"//runtimes/node/tests:system_ca_{major}_test",
        "artifact": f"bazel-bin/runtimes/node/node-v{version}-*.tar.gz*",
    }
    others = [v for v in manifest["versions"] if v["line"] != major]
    manifest["versions"] = sorted(others + [entry], key=lambda v: int(v["line"]), reverse=True)
    return json.dumps(manifest, indent=2) + "\n"


def update(module, manifest, readme, version, shasums, old_v8):
    """Returns the new text of MODULE.bazel, runtimes/node/manifest.json, and README.md."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        die(f"{version!r} is not X.Y.Z")
    major = version.split(".")[0]
    digests = hashes(version, shasums)
    tag = tag_text(major, version, digests, old_v8)

    existing = None
    for found in re.finditer(r"^node\.version\(\n(?:    .*\n)*\)\n", module, re.MULTILINE):
        if f'    major = "{major}",' in found.group(0):
            existing = found
    if existing:
        # Keep the old_v8 choice of an existing line unless the caller sets it.
        old_v8 = old_v8 or "old_v8 = True" in existing.group(0)
        tag = tag_text(major, version, digests, old_v8)
        module = module[:existing.start()] + tag + module[existing.end():]
    else:
        if not re.search(r"^node\.version\(\n", module, re.MULTILINE):
            die("MODULE.bazel has no node.version tag")
        older = [m for m in re.finditer(r'^node\.version\(\n    major = "(\d+)"', module, re.MULTILINE)
                 if int(m.group(1)) < int(major)]
        if older:
            at = older[0].start()
            module = module[:at] + tag + "\n" + module[at:]
        else:
            at = module.index("use_repo(node,")
            module = module[:at].rstrip("\n") + "\n\n" + tag + "\n" + module[at:]

    manifest = set_manifest(manifest, major, version, digests["sha256"])
    majors = re.findall(r'^node\.version\(\n    major = "(\d+)"', module, re.MULTILINE)
    text = prose(majors)

    readme = re.sub(r"Node\.js [0-9, and]+ with no bundled root certificates",
                    f"Node.js {text} with no bundled root certificates", readme)
    readme = re.sub(r"builds Node [0-9, and]+ on macOS 14", f"builds Node {text} on macOS 14", readme)
    row = f"| {major} | {version} | `{digests['sha256']}` | untested | untested |"
    row_pattern = re.compile(r"^\| %s \| [0-9.]+ \| `[0-9a-f]{64}` \|.*$" % major, re.MULTILINE)
    if row_pattern.search(readme):
        readme = row_pattern.sub(lambda _: row, readme, count=1)
    else:
        rows = list(re.finditer(r"^\| (\d+) \| [0-9.]+ \| `[0-9a-f]{64}` \|.*$", readme, re.MULTILINE))
        higher_than_new = [r for r in rows if int(r.group(1)) < int(major)]
        if higher_than_new:
            at = higher_than_new[0].start()
            readme = readme[:at] + row + "\n" + readme[at:]
        else:
            at = rows[-1].end()
            readme = readme[:at] + "\n" + row + readme[at:]
    return module, manifest, readme


def update_status(status, version):
    """Returns STATUS.md with an untested row for the version. Only the Result table changes."""
    head, marker, tail = status.partition("## Result\n")
    rows = list(re.finditer(r"^\| (\d+)\.\d+\.\d+ \|.*$", tail.split("\n## ")[0], re.MULTILINE))
    if not rows:
        die("STATUS.md has no Result table")
    major = int(version.split(".")[0])
    row = f"| {version} | untested | untested | None. CI has not run this version. |"
    same = [r for r in rows if int(r.group(1)) == major]
    if same:
        return head + marker + tail[:same[0].start()] + row + tail[same[0].end():]
    lower = [r for r in rows if int(r.group(1)) < major]
    if lower:
        at = lower[0].start()
        return head + marker + tail[:at] + row + "\n" + tail[at:]
    at = rows[-1].end()
    return head + marker + tail[:at] + "\n" + row + tail[at:]


def fetch(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def make_patches(version, tarball_sha256, tarball):
    major = version.split(".")[0]
    if hashlib.sha256(tarball).hexdigest() != tarball_sha256:
        die("the source tarball does not match SHASUMS256.txt")
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "source.tar.gz")
        with open(path, "wb") as stream:
            stream.write(tarball)
        with tarfile.open(path) as archive:
            archive.extractall(directory, filter="data")
        subprocess.run(
            [sys.executable, os.path.join(ROOT, "runtimes", "node", "tools", "make_patches.py"), "--only", major,
             directory, os.path.join(ROOT, "runtimes", "node", "patches")],
            check=True)


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    flags = [a for a in argv if a.startswith("--")]
    shasums_file = None
    if "--shasums" in argv:
        shasums_file = argv[argv.index("--shasums") + 1]
        args.remove(shasums_file)
    if len(args) != 1:
        die(__doc__)
    version = args[0]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        die(f"{version!r} is not X.Y.Z")
    if shasums_file:
        with open(shasums_file) as stream:
            shasums = stream.read()
    else:
        shasums = fetch(DIST.format(version) + "SHASUMS256.txt").decode()
    paths = [os.path.join(ROOT, p) for p in ("MODULE.bazel", "runtimes/node/manifest.json", "README.md", "STATUS.md")]
    old = [open(p, encoding="utf-8").read() for p in paths]
    new = [*update(*old[:3], version, shasums, old_v8="--old-v8" in flags), update_status(old[3], version)]
    for path, text in zip(paths, new):
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(text)
    major = version.split(".")[0]
    if "--no-patches" not in flags:
        digest = hashes(version, shasums)["sha256"]
        tarball = fetch(DIST.format(version) + f"node-v{version}.tar.gz")
        try:
            make_patches(version, digest, tarball)
        except subprocess.CalledProcessError:
            # Leave no half edit. The message above came from make_patches.py.
            for path, text in zip(paths, old):
                with open(path, "w", encoding="utf-8") as stream:
                    stream.write(text)
            die(f"no patches for Node {major}. MODULE.bazel, node.json, README.md and STATUS.md are restored.")
    print(f"Updated MODULE.bazel, node.json, README.md and STATUS.md for Node {version}.")
    print(f"Next: bazel test //runtimes/node/tests:source_{major}_test //runtimes/node/tests:versions_test, then")
    print(f"  bazel build //runtimes/node:node_{major} && bazel test //runtimes/node/tests:hermetic_{major}_test //runtimes/node/tests:network_{major}_test")


if __name__ == "__main__":
    main(sys.argv[1:])
