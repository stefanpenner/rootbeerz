"""Builds one Node line twice and checks that the archives have the same bytes.

  bazel run //tools:repro_check -- 22

The two builds differ in three ways: the path of the workspace copy, the Bazel
output base (so the path of the sandbox and the execroot), and the length of
both paths. Neither build uses a cache. A binary that holds a path of the build
machine, or any other input that changes from run to run, makes the archives differ.

  bazel run //tools:repro_check -- --compare a.tar.gz b.tar.gz

compares two archives that exist already, for example one from CI and one from your machine.
The exit code is 0 when the archives are the same and 1 when they are not.
"""

import argparse
import dataclasses
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

# A path or an identifier, not a run of random bytes that happen to be printable.
PRINTABLE = re.compile(rb"[A-Za-z0-9_./:=+ -]{8,}")
LIMIT = 40

# No cache may answer a build, and no remote service may take part.
NO_CACHE = [
    "--remote_cache=",
    "--remote_executor=",
    "--disk_cache=",
    "--noremote_accept_cached",
    "--noremote_upload_local_results",
]


@dataclasses.dataclass
class Result:
    same: bool
    only_a: list
    only_b: list
    first_difference: int
    size_a: int
    size_b: int
    differing_bytes: int = 0


def readable(data):
    """The strings that look like a path or an identifier. Random bytes seldom hold a / or a _ or a dot in a run."""
    return {s.decode() for s in PRINTABLE.findall(data) if re.search(rb"[/_.]", s)}


def compare(a, b):
    """Compares two byte strings. Lists the printable strings that only one side holds."""
    first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    if a == b:
        return Result(True, [], [], 0, len(a), len(b), 0)
    strings_a = readable(a)
    strings_b = readable(b)
    return Result(False, sorted(strings_a - strings_b)[:LIMIT], sorted(strings_b - strings_a)[:LIMIT],
                  first, len(a), len(b), sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b)))


def members(path):
    with tarfile.open(path, "r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}


def node_binary(path):
    for name, data in members(path).items():
        if name.endswith("/bin/node"):
            return data
    raise SystemExit(f"{path} has no bin/node")


def differing_members(a, b):
    left, right = members(a), members(b)
    return sorted(name for name in set(left) | set(right) if left.get(name) != right.get(name))


def workspace_copy(source, target):
    """Copies the files of a Git workspace that Git tracks or would track. It skips .git and ignored files."""
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=source, check=True, capture_output=True).stdout
    for name in filter(None, listing.split(b"\0")):
        relative = os.fsdecode(name)
        origin = Path(source) / relative
        if not origin.exists() and not origin.is_symlink():
            continue
        destination = Path(target) / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if origin.is_symlink():
            destination.symlink_to(os.readlink(origin))
        else:
            shutil.copy2(origin, destination)


def build(bazel, workspace, output_base, major, extra):
    flags = [bazel, f"--output_base={output_base}"]
    targets = [f"//runtimes/node:node_{major}", f"//runtimes/node:dist_{major}"]
    subprocess.run(flags + ["build"] + NO_CACHE + extra + targets + ["--output_groups=+dist"],
                   cwd=workspace, check=True)
    info = subprocess.run(flags + ["info", "bazel-bin"], cwd=workspace, check=True,
                          capture_output=True, text=True).stdout.strip()
    found = sorted(Path(info, "node").glob("node-v*-*.tar.gz"))
    if len(found) != 1:
        raise SystemExit(f"expected one archive in {info}/node, found {found}")
    return found[0]


def report(a, b):
    digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    print(f"A  {digest(a)}  {a}")
    print(f"B  {digest(b)}  {b}")
    if digest(a) == digest(b):
        print("SAME: the archives have the same bytes.")
        return 0
    print("DIFFERENT. Members that differ:", ", ".join(differing_members(a, b)) or "(none, only the container)")
    result = compare(node_binary(a), node_binary(b))
    print(f"bin/node: {result.size_a} and {result.size_b} bytes, first difference at {result.first_difference}, "
          f"{result.differing_bytes} bytes differ")
    for label, strings in (("only in A", result.only_a), ("only in B", result.only_b)):
        for text in strings:
            print(f"  {label}: {text[:200]}")
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("major", nargs="?", help="Node line, for example 22")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"), help="compare two archives and stop")
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument("--workdir", help="directory for the two copies (default: a new temporary directory)")
    parser.add_argument("--keep", action="store_true", help="keep the two copies and output bases")
    parser.add_argument("bazel_args", nargs="*", help="more arguments for `bazel build`, after --")
    args = parser.parse_args()
    if args.compare:
        return report(*args.compare)
    if not args.major:
        parser.error("give a Node line, or --compare A B")

    source = Path(os.environ.get("BUILD_WORKSPACE_DIRECTORY", os.getcwd()))
    root = Path(args.workdir or tempfile.mkdtemp(prefix="rootbeer-repro-"))
    # The two roots differ in length, so a leaked path cannot match by luck.
    runs = [root / "a" / "w", root / "longer-name-for-the-second-build" / "workspace"]
    archives = []
    try:
        for index, workspace in enumerate(runs):
            workspace.mkdir(parents=True, exist_ok=True)
            workspace_copy(source, workspace)
            output_base = workspace.parent / "output-base"
            print(f"== build {'AB'[index]} in {workspace}, output base {output_base}", flush=True)
            archives.append(build(args.bazel, workspace, output_base, args.major, args.bazel_args))
        # Copy the archives out before the output bases go away.
        kept = []
        for index, archive in enumerate(archives):
            target = root / f"{'ab'[index]}-{archive.name}"
            shutil.copyfile(archive, target)
            kept.append(target)
        return report(*kept)
    finally:
        if not args.keep:
            for workspace in runs:
                subprocess.run([args.bazel, f"--output_base={workspace.parent / 'output-base'}", "shutdown"],
                               cwd=workspace, capture_output=True)
            for sub in list(root.glob("*")):
                if sub.is_dir():
                    shutil.rmtree(sub, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
