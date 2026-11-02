#!/usr/bin/env python3
"""The one CLI that each GitHub Actions step calls. Each step is one subcommand.

It uses the Python standard library only. install-bazel runs before Bazel exists,
and python3 is on each GitHub-hosted runner. gha_test.py tests each subcommand.
"""

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

BAZEL_VERSION = "9.1.1"

# (RUNNER_OS, RUNNER_ARCH) -> (release suffix, sha256 of the binary).
BAZEL_PINS = {
    ("macOS", "ARM64"): ("darwin-arm64", "2db883718453f0437a7bcb408e889dbf8539cdc4d61c8ebc3807a1a88d02ff08"),
    ("Linux", "X64"): ("linux-x86_64", "857bed5d2756b4d998d3caebf2d941d13d434c4eda4b1d6d7dda205736c25a93"),
}

# Fit Bazel to the runner, so a large build does not run out of memory.
FIT = ["--jobs=HOST_CPUS", "--local_resources=cpu=HOST_CPUS", "--local_resources=memory=HOST_RAM*.8"]

HOSTED = re.compile(r"(ubuntu|macos)-(latest|[0-9][a-z0-9.-]*)")

NOTES = (
    "Runtimes with no bundled root certificates. Each one uses the system store.\n\n"
    "Check a download: sha256sum -c SHA256SUMS --ignore-missing\n"
)


class Refused(Exception):
    """A step that must stop the job. main prints the message and exits 1."""


def call(argv, cwd=None):
    """Runs argv, shows its stdout, and returns its stdout. A nonzero exit is Refused."""
    proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, text=True)
    lines = []
    for line in proc.stdout:
        sys.stdout.write(line)
        lines.append(line)
    if proc.wait():
        raise Refused(f"{argv[0]} exited {proc.returncode}: {' '.join(argv)}")
    return "".join(lines)


def download(url):
    with urllib.request.urlopen(url) as response:
        return response.read()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def runner():
    return os.environ["RUNNER_OS"], os.environ["RUNNER_ARCH"]


def append(env_file, line):
    with open(os.environ[env_file], "a") as f:
        f.write(line + "\n")


# install-bazel


def install_bazel(_args):
    system = runner()
    if system not in BAZEL_PINS:
        raise Refused(f"No Bazel pin for {'-'.join(system)}.")
    suffix, want = BAZEL_PINS[system]
    url = f"https://github.com/bazelbuild/bazel/releases/download/{BAZEL_VERSION}/bazel-{BAZEL_VERSION}-{suffix}"
    body = download(url)
    if sha256(body) != want:
        raise Refused(f"The sha256 of {url} is {sha256(body)}, not the pin {want}.")
    bin_dir = Path(os.environ["RUNNER_TEMP"]) / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    bazel = bin_dir / "bazel"
    bazel.write_bytes(body)
    bazel.chmod(bazel.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    append("GITHUB_PATH", str(bin_dir))


# matrix


def number(version):
    return [int(n) for n in re.findall(r"[0-9]+", version)]


def picked(versions, scope):
    if scope != "pr":
        return versions
    oldest = min(versions, key=lambda v: number(v["version"]))
    newest = max(reversed(versions), key=lambda v: number(v["version"]))
    unique = {v["line"]: v for v in (newest, oldest)}
    return [unique[line] for line in sorted(unique)]


def hosted(system):
    if not HOSTED.fullmatch(system):
        raise Refused(f"not a GitHub-hosted runner: {system!r}")
    return system


def legs(manifests, scope):
    return [
        {
            "runtime": manifest["runtime"],
            **{key: version[key] for key in ("line", "version", "target", "test", "system_ca_test", "artifact")},
            "os": hosted(system),
        }
        for manifest in manifests
        for version in picked(manifest["versions"], scope)
        for system in manifest["os"]
    ]


def matrix(args):
    paths = args.manifests or sorted(glob.glob("runtimes/*/manifest.json"))
    manifests = [json.loads(Path(p).read_text()) for p in paths]
    append("GITHUB_OUTPUT", "legs=" + json.dumps({"include": legs(manifests, args.scope)}, separators=(",", ":")))


# build and test


def build(args):
    call(["bazel", "build", *FIT, args.target, "--output_groups=+dist"])


def test(args):
    call(["bazel", "test", *FIT, *args.targets])


# collect


def write_sha256(path):
    sidecar = Path(f"{path}.sha256")
    if not sidecar.exists():
        sidecar.write_text(f"{sha256(Path(path).read_bytes())}  {Path(path).name}\n")


def collect(args):
    matches = sorted(glob.glob(args.artifact))
    if not matches:
        raise Refused(f"Nothing matches the artifact {args.artifact}.")
    out = Path("out")
    out.mkdir(exist_ok=True)
    if all(re.search(r"\.tar\.gz(\.sha256)?$", m) for m in matches):
        for match in matches:
            shutil.copyfile(match, out / Path(match).name)
        for tarball in out.glob("*.tar.gz"):
            write_sha256(tarball)
        return
    name = f"{args.runtime}-{args.version}-{os.environ['RUNNER_OS']}-{os.environ['RUNNER_ARCH']}"
    tarball = out / f"{name}.tar.gz"
    with tarfile.open(tarball, "w:gz", dereference=True) as tar:
        for match in matches:
            tar.add(match, arcname=f"{name}/{Path(match).name}")
    write_sha256(tarball)


# release


def release(args):
    run = call([
        "gh", "run", "list", "-R", args.repo, "--workflow", "ci.yml", "--commit", args.sha,
        "--event", "push", "--status", "success", "--limit", "1",
        "--json", "databaseId", "--jq", ".[0].databaseId // empty",
    ]).strip()
    if not run:
        raise Refused(f"No green CI push run for {args.sha}. Tag a commit on main after its CI run passes.")
    dist = Path("dist")
    dist.mkdir(exist_ok=True)
    call(["gh", "run", "download", run, "-R", args.repo, "--dir", str(dist)])

    tarballs = {}
    for path in sorted(dist.rglob("*.tar.gz")):
        if path.name in tarballs:
            raise Refused(f"Two artifacts give {path.name}: {tarballs[path.name]} and {path}.")
        tarballs[path.name] = path
    if not tarballs:
        raise Refused(f"CI run {run} has no .tar.gz artifact.")

    out = Path("release")
    out.mkdir(exist_ok=True)
    sums = []
    for name in sorted(tarballs):
        shutil.copyfile(tarballs[name], out / name)
        sums.append(f"{sha256((out / name).read_bytes())}  {name}\n")
    (out / "SHA256SUMS").write_text("".join(sums))
    (out / "notes.md").write_text(NOTES)
    call([
        "gh", "release", "create", args.tag, "-R", args.repo, "--title", args.tag, "--notes-file", "notes.md",
        "--", *sorted(tarballs), "SHA256SUMS",
    ], cwd=str(out))


def parser():
    p = argparse.ArgumentParser(prog="gha.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("install-bazel", help="Install the pinned Bazel on the PATH.").set_defaults(run=install_bazel)

    m = sub.add_parser("matrix", help="Write the CI matrix to GITHUB_OUTPUT as legs.")
    m.add_argument("--scope", choices=["all", "pr"], required=True)
    m.add_argument("manifests", nargs="*", help="Default: runtimes/*/manifest.json.")
    m.set_defaults(run=matrix)

    b = sub.add_parser("build", help="Build a target and its dist output.")
    b.add_argument("--target", required=True)
    b.set_defaults(run=build)

    t = sub.add_parser("test", help="Test the targets in one Bazel call.")
    t.add_argument("targets", nargs="+")
    t.set_defaults(run=test)

    c = sub.add_parser("collect", help="Put the artifact and its sha256 into out/.")
    c.add_argument("--runtime", required=True)
    c.add_argument("--version", required=True)
    c.add_argument("--artifact", required=True, help="A path or a glob.")
    c.set_defaults(run=collect)

    r = sub.add_parser("release", help="Publish the artifacts of the green CI run of a commit.")
    r.add_argument("--repo", required=True)
    r.add_argument("--sha", required=True)
    r.add_argument("--tag", required=True)
    r.set_defaults(run=release)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        args.run(args)
    except Refused as e:
        print(f"gha.py {args.command}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
