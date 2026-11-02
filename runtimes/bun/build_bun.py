"""Builds one Bun binary from patched source with the pinned inputs.

Bazel checks each input by checksum before this driver runs. The driver lays the inputs out for
scripts/build.ts of Bun, then runs that script with no network. Each layout step leaves a stamp, so a
run with ROOTBEER_SCRATCH continues the work of an earlier run.
"""

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile

# The revision of the bun-v1.4.4 tag. The source archive holds no git data.
REVISION = "38afee6c84fcf48c0edb2466aa4b27d287a59c30"
RUST_COMPONENTS = ("rust-rustc", "rust-cargo", "rust-rust-std", "rust-rust-src")
TAR = "/usr/bin/tar"
SKIPPED = ("bench", "test")
HOST_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
# Stock Bun 1.4.4 wrote this folder name in its install cache.
PRERELEASE_FOLDERS = {"querystring-es3@1.0.0-0": "querystring-es3@1.0.0-b8f8325b21a8a1e5@@@1"}


def parent_cargo_configs(path):
    """Lists each config that cargo reads from a parent directory. CARGO_HOME does not stop this walk."""
    found = []
    for directory in [path, *path.parents]:
        for name in ("config", "config.toml"):
            config = directory / ".cargo" / name
            if config.is_file():
                found.append(config)
    return found


def once(scratch, name, identity, step):
    """Runs one layout step, unless a stamp with the same identity exists."""
    stamp = scratch / "stamps" / name
    if stamp.is_file() and stamp.read_text() == identity:
        print(f"keep {name}", flush=True)
        return
    stamp.parent.mkdir(parents=True, exist_ok=True)
    print(f"lay out {name}", flush=True)
    step()
    stamp.write_text(identity)


def reset(path):
    if path.exists():
        subprocess.run(["/bin/chmod", "-R", "u+w", path], check=True)
        shutil.rmtree(path)
    path.mkdir(parents=True)


def untar(archive, into, strip=1):
    subprocess.run([TAR, "-xf", archive, "-C", into, f"--strip-components={strip}"], check=True)


def one_file(directory):
    files = sorted(path for path in directory.iterdir() if path.is_file())
    if len(files) != 1:
        raise SystemExit(f"{directory} must hold one file, not {len(files)}")
    return files[0]


def copy_source(root, into):
    reset(into)
    for directory, children, names in os.walk(root, followlinks=False):
        relative = pathlib.Path(directory).relative_to(root)
        if directory == str(root):
            children[:] = [child for child in children if child not in SKIPPED]
        (into / relative).mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copy(pathlib.Path(directory) / name, into / relative / name, follow_symlinks=True)
            target = into / relative / name
            target.chmod(target.stat().st_mode | stat.S_IWUSR)


def lay_out_rust(archives, into):
    reset(into)
    for name in RUST_COMPONENTS:
        unpacked = into.parent / "rust-part"
        reset(unpacked)
        untar(one_file(archives / name), unpacked)
        for component in (path for path in unpacked.iterdir() if path.is_dir()):
            shutil.copytree(component, into, dirs_exist_ok=True, symlinks=True)
        shutil.rmtree(unpacked)


def lay_out_ninja(archive, cache, tag):
    target = cache / "ninja" / tag
    reset(target)
    subprocess.run(["/usr/bin/unzip", "-q", "-o", archive, "ninja", "-d", target], check=True)
    (target / "ninja").chmod(0o755)


def lay_out_prefetch(deps, inputs, into):
    reset(into / "by-url")
    for item in inputs["deps"]:
        key = hashlib.sha256(item["url"].encode()).hexdigest()[:32]
        shutil.copy(deps / f"{item['name']}.tar.gz", into / "by-url" / key)


def lay_out_crates(crates, inputs, vendor, cargo_home):
    reset(vendor)
    for item in inputs["crates"]:
        name = f"{item['name']}-{item['version']}"
        subprocess.run([TAR, "-xzf", crates / f"{name}.crate", "-C", vendor], check=True)
        (vendor / name / ".cargo-checksum.json").write_text(json.dumps({"files": {}, "package": item["sha256"]}))
    reset(cargo_home)
    (cargo_home / "config.toml").write_text(
        "[source.crates-io]\n"
        'replace-with = "pinned"\n'
        "[source.pinned]\n"
        f'directory = "{vendor}"\n'
        "[net]\n"
        "offline = true\n"
    )


def npm_cache_folder(name, version):
    """Names the install cache folder of one package. Bun hashes a prerelease tag in this name."""
    if "-" not in version and "+" not in version:
        return f"{name}@{version}@@@1"
    folder = PRERELEASE_FOLDERS.get(f"{name}@{version}")
    if folder is None:
        raise SystemExit(f"{name}@{version}: run Bun 1.4.4 once and record its cache folder name")
    return folder


def lay_out_npm(npm, inputs, cache):
    reset(cache)
    for item in inputs["npm"]:
        target = cache / npm_cache_folder(item["name"], item["version"])
        target.mkdir(parents=True)
        untar(npm / f"{item['name'].replace('/', '__')}@{item['version']}.tgz", target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-marker", type=pathlib.Path, required=True)
    parser.add_argument("--inputs-json", type=pathlib.Path, required=True)
    parser.add_argument("--stock-bun", type=pathlib.Path, required=True)
    parser.add_argument("--identity", type=pathlib.Path, required=True)
    parser.add_argument("--jobs", type=int, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--no-build", action="store_true", help="Stop after the layout steps.")
    args = parser.parse_args()

    keep = os.environ.get("ROOTBEER_SCRATCH")
    # The default is outside the home directory, because cargo walks each parent directory.
    scratch = pathlib.Path(keep) if keep else pathlib.Path(tempfile.mkdtemp(prefix="rootbeerz-bun-", dir="/private/tmp"))
    scratch.mkdir(parents=True, exist_ok=True)
    scratch = scratch.resolve()
    leaks = parent_cargo_configs(scratch)
    if leaks:
        raise SystemExit(f"host cargo configuration would change this build: {leaks}")
    pins = args.inputs_json.read_bytes()
    inputs = json.loads(pins)
    files = args.inputs_json.resolve().parent / "files"
    if not files.is_dir():
        files = pathlib.Path(os.path.dirname(args.inputs_json)) / "files"
    identity = hashlib.sha256(pins + args.identity.read_bytes()).hexdigest()

    src = scratch / "src"
    cache = scratch / "build-cache"
    archives = files / "archives"
    tools = scratch / "tools"
    tag = next(i["url"].split("/")[-2] for i in inputs["archives"] if i["name"] == "ninja")

    once(scratch, "source", identity, lambda: copy_source(args.source_marker.parent, src))
    once(scratch, "llvm", identity, lambda: (reset(scratch / "llvm"), untar(one_file(archives / "llvm"), scratch / "llvm")))
    once(scratch, "cmake", identity, lambda: (reset(scratch / "cmake"), untar(one_file(archives / "cmake"), scratch / "cmake")))
    once(scratch, "rust", identity, lambda: lay_out_rust(archives, scratch / "rust"))
    once(scratch, "ninja", identity, lambda: lay_out_ninja(one_file(archives / "ninja"), cache, tag))
    once(scratch, "prefetch", identity, lambda: lay_out_prefetch(files / "deps", inputs, scratch / "prefetch"))
    once(scratch, "crates", identity, lambda: lay_out_crates(files / "crates", inputs, scratch / "vendor", scratch / "cargo-home"))
    once(scratch, "npm", identity, lambda: lay_out_npm(files / "npm", inputs, scratch / "npm-cache"))

    def lay_out_tools():
        reset(tools)
        shutil.copy(args.stock_bun, tools / "bun")
        (tools / "bun").chmod(0o755)

    once(scratch, "tools", identity, lay_out_tools)
    if args.no_build:
        return 0

    env = {
        "PATH": os.pathsep.join([str(tools), str(scratch / "cmake/CMake.app/Contents/bin"), HOST_PATH]),
        "HOME": str(scratch / "home"),
        "TMPDIR": str(scratch / "tmp"),
        "BUN_BUILD_CACHE_DIR": str(cache),
        "BUN_BUILD_PREFETCH_DIR": str(scratch / "prefetch"),
        "BUN_INSTALL": str(scratch / "bun-install"),
        "BUN_INSTALL_CACHE_DIR": str(scratch / "npm-cache"),
        "BUN_TOOLCHAIN_LLVM": str(scratch / "llvm"),
        "BUN_TOOLCHAIN_RUST": str(scratch / "rust"),
        "CARGO_HOME": str(scratch / "cargo-home"),
        "GIT_SHA": REVISION,
        "NO_COLOR": "1",
        # Rung 6: the host SDK. The tinycc tool step calls clang with no -isysroot.
        "SDKROOT": subprocess.run(
            ["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "SOURCE_DATE_EPOCH": "0",
        "ZERO_AR_DATE": "1",
    }
    for name in ("HOME", "TMPDIR"):
        pathlib.Path(env[name]).mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [tools / "bun", "scripts/build.ts", "--profile=release", "--lto=off", "--canary=off", f"-j{args.jobs}"],
        cwd=src,
        env=env,
        check=True,
    )
    shutil.copy(src / "build/release/bun", args.out)
    args.out.chmod(0o755)
    if not keep:
        subprocess.run(["/bin/chmod", "-R", "u+w", scratch], check=True)
        shutil.rmtree(scratch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
