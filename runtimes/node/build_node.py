"""Builds one Node line from patched source with the pinned tools only.

Bazel runs this file as one action. The action copies the source to a scratch
directory, writes tool wrappers, runs configure, runs ninja, and packs a tarball.
PATH holds the wrappers, then /usr/bin and /bin for POSIX tools. Those POSIX
tools and the macOS SDK come from the host. docs/HERMETICITY.md lists each input.

Set ROOTBEER_SCRATCH to keep the scratch directory between runs. Use this for
development only. Bazel does not set it.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile

BASE_PATH = "/usr/bin:/bin"

# Clang 19 reports these as errors or noise on the older V8 and deps in Node 16 and 18.
OLD_V8_FLAGS = [
    "-Wno-enum-constexpr-conversion",
    "-Wno-missing-template-arg-list-after-template-kw",
    "-Wno-deprecated-non-prototype",
    "-Wno-incompatible-function-pointer-types",
    "-Wno-implicit-function-declaration",
    "-Wno-int-conversion",
]

COMPILER_WRAPPER = """#!/bin/sh
link=1
for arg in "$@"; do
  case "$arg" in
    -c|-E|-S|-M|-MM|-v|--version) link=0 ;;
  esac
done
if [ "$link" = 1 ]; then
  exec {compiler} {flags} "$@" {link_flags}
fi
exec {compiler} {flags} "$@"
"""

XCRUN_WRAPPER = """#!/bin/sh
while [ $# -gt 0 ]; do
  case "$1" in
    --sdk) shift 2 ;;
    --show-sdk-path) echo "{sdk}"; exit 0 ;;
    --show-sdk-version) echo "{version}"; exit 0 ;;
    --show-sdk-build-version) echo "rootbeer"; exit 0 ;;
    --show-sdk-platform-path) echo "{sdk}"; exit 0 ;;
    -f|-find|--find) command -v "$2"; exit $? ;;
    *) exec "$@" ;;
  esac
done
"""

# Gyp reads this version to select flags. It does not select a host Xcode.
XCODEBUILD_WRAPPER = """#!/bin/sh
echo "Xcode 16.0"
echo "Build version 16A242"
"""


# abseil.gyp in Node 24 and 26 gives -framework to libtool. llvm-libtool-darwin stops on that
# flag. A static archive holds no framework link, and the final link gets its own flags.
LIBTOOL_WRAPPER = """#!/bin/sh
count=$#
while [ "$count" -gt 0 ]; do
  arg=$1
  shift
  count=$((count - 1))
  case "$arg" in
    -framework)
      shift
      count=$((count - 1))
      ;;
    "-framework "*) ;;
    *) set -- "$@" "$arg" ;;
  esac
done
exec "{libtool}" "$@"
"""


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-marker", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--os", required=True, choices=["darwin", "linux"])
    parser.add_argument("--arch", required=True)
    parser.add_argument("--clang", required=True)
    parser.add_argument("--ninja", required=True)
    parser.add_argument("--sysroot-info", required=True)
    parser.add_argument("--old-v8", action="store_true")
    parser.add_argument("--jobs", type=int, default=10)
    parser.add_argument("--out-binary", required=True)
    parser.add_argument("--out-tarball", required=True)
    parser.add_argument("--out-sha256", required=True)
    return parser.parse_args()


def write_tool(bin_dir, name, text):
    path = os.path.join(bin_dir, name)
    with open(path, "w") as stream:
        stream.write(text)
    os.chmod(path, 0o755)


def exec_tool(bin_dir, name, target):
    write_tool(bin_dir, name, f'#!/bin/sh\nexec "{target}" "$@"\n')


def libcxx_paths(llvm, os_name):
    lib = os.path.join(llvm, "lib")
    if os_name == "linux":
        lib = os.path.join(lib, "x86_64-unknown-linux-gnu")
    return os.path.join(lib, "libc++.a"), os.path.join(lib, "libc++abi.a")


def libcxx_include_flags(llvm, os_name):
    flags = ["-nostdinc++", "-isystem", os.path.join(llvm, "include", "c++", "v1")]
    if os_name == "linux":
        target_dir = os.path.join(llvm, "include", "x86_64-unknown-linux-gnu", "c++", "v1")
        flags += ["-isystem", target_dir]
    return flags


def write_tools(args, llvm, ninja, python, sysroot, sysroot_version, scratch, old_v8):
    bin_dir = os.path.join(scratch, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    llvm_bin = os.path.join(llvm, "bin")
    common = [
        f"-ffile-prefix-map={scratch}=.",
        f"-ffile-prefix-map={llvm}=llvm",
        f"-ffile-prefix-map={sysroot}=/sysroot",
        "-Wno-unused-command-line-argument",
        "-fuse-ld=lld",
    ] + (OLD_V8_FLAGS if old_v8 else [])
    if args.os == "darwin":
        common += ["-isysroot", sysroot]
    else:
        common += ["--sysroot=" + sysroot, "--rtlib=libgcc", "--unwindlib=libgcc", "-fPIC"]
    cxx_flags = common + libcxx_include_flags(llvm, args.os) + ["-D_LIBCPP_DISABLE_AVAILABILITY"]
    libcxx, libcxxabi = libcxx_paths(llvm, args.os)
    link_flags = ["-nostdlib++", libcxx, libcxxabi]
    if args.os == "darwin":
        # A static libc++ with chained fixups crashes at startup on Darwin 27. See docs/HERMETICITY.md.
        link_flags += ["-Wl,-no_fixup_chains"]
        # Without -S, lld records the path of every object file in the symbol table (N_OSO), and
        # those paths are the sandbox path of this build.
        link_flags += ["-Wl,-S"]
    else:
        link_flags += ["-lpthread", "-ldl", "-lm"]

    for names, tool, flags, tail in [
        (["cc", "gcc", "clang"], "clang", common, []),
        (["c++", "g++", "clang++"], "clang++", cxx_flags, link_flags),
    ]:
        text = COMPILER_WRAPPER.format(
            compiler=os.path.join(llvm_bin, tool),
            flags=" ".join(flags),
            link_flags=" ".join(tail),
        )
        for name in names:
            write_tool(bin_dir, name, text)

    for name, target in [
        ("ar", "llvm-ar"),
        ("ranlib", "llvm-ranlib"),
        ("nm", "llvm-nm"),
        ("strip", "llvm-strip"),
        ("objcopy", "llvm-objcopy"),
    ]:
        exec_tool(bin_dir, name, os.path.join(llvm_bin, target))
    exec_tool(bin_dir, "ninja", ninja)
    exec_tool(bin_dir, "python", python)
    exec_tool(bin_dir, "python3", python)
    if args.os == "darwin":
        write_tool(bin_dir, "libtool", LIBTOOL_WRAPPER.format(libtool=os.path.join(llvm_bin, "llvm-libtool-darwin")))
        write_tool(bin_dir, "xcrun", XCRUN_WRAPPER.format(sdk=sysroot, version=sysroot_version))
        write_tool(bin_dir, "xcodebuild", XCODEBUILD_WRAPPER)
    return bin_dir


def build_env(bin_dir, scratch, sysroot, os_name):
    env = {
        "PATH": bin_dir + ":" + BASE_PATH,
        "HOME": scratch,
        "TMPDIR": os.path.join(scratch, "tmp"),
        "LC_ALL": "C",
        "SOURCE_DATE_EPOCH": "0",
        "ZERO_AR_DATE": "1",
        "CC": "cc",
        "CXX": "c++",
        # Do not set CC_host, CXX_host, or AR_host. Gyp then duplicates each target.
        "AR": "ar",
        "PYTHONDONTWRITEBYTECODE": "1",
        # Gyp otherwise asks sysctl, so the host memory size changes build.ninja.
        "GYP_LINK_CONCURRENCY": "2",
    }
    if os_name == "darwin":
        env["SDKROOT"] = sysroot
    os.makedirs(env["TMPDIR"], exist_ok=True)
    return env


def copy_source(source_root, tree):
    """Copies the source tree. Bazel gives read-only symlinks, and the build writes in place."""
    if os.path.exists(os.path.join(tree, "configure.py")):
        return
    shutil.copytree(source_root, tree, dirs_exist_ok=True, ignore_dangling_symlinks=True)
    for root, _, files in os.walk(tree):
        os.chmod(root, 0o755)
        for name in files:
            path = os.path.join(root, name)
            mode = os.stat(path).st_mode
            os.chmod(path, mode | stat.S_IWUSR)


def configure_flags(tree):
    with open(os.path.join(tree, "configure.py")) as stream:
        text = stream.read()
    flags = ["--ninja", "--openssl-use-def-ca-store", "--without-npm"]
    # configure gives an unknown flag to gyp, and gyp fails. Node 26 has no corepack by default.
    if "--without-corepack" in text:
        flags.append("--without-corepack")
    if "--without-dtrace" in text:
        flags.append("--without-dtrace")
    # Node 26 looks for cargo on PATH, and Temporal needs Rust. This build declares no Rust.
    # The flag makes the choice explicit, so a host cargo cannot change it.
    if "--v8-disable-temporal-support" in text:
        flags.append("--v8-disable-temporal-support")
    return flags


def run(command, cwd, env):
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def first_line(command, env):
    output = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    return output.stdout.splitlines()[0]


def buildinfo(args, env, sysroot_info, python):
    return {
        "node": args.version,
        "os": args.os,
        "arch": args.arch,
        "clang": first_line(["c++", "--version"], env),
        "ninja": first_line(["ninja", "--version"], env),
        "python": first_line([python, "--version"], env),
        "sysroot": sysroot_info,
        "bundled_roots": 0,
    }


def add_bytes(archive, name, data, mode):
    member = tarfile.TarInfo(name)
    member.size = len(data)
    member.mode = mode
    member.mtime = 0
    member.uname = member.gname = "root"
    archive.addfile(member, io.BytesIO(data))


def pack(args, binary, license_file, info):
    stem = f"node-v{args.version}-{args.os}-{args.arch}"
    with open(binary, "rb") as stream:
        node = stream.read()
    with open(license_file, "rb") as stream:
        license_text = stream.read()
    body = json.dumps(info, indent=2, sort_keys=True).encode() + b"\n"

    with open(args.out_tarball, "wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=6, mtime=0) as packed:
            with tarfile.open(fileobj=packed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                add_bytes(archive, f"{stem}/BUILDINFO.json", body, 0o644)
                add_bytes(archive, f"{stem}/LICENSE", license_text, 0o644)
                add_bytes(archive, f"{stem}/bin/node", node, 0o755)

    with open(args.out_tarball, "rb") as stream:
        digest = hashlib.sha256(stream.read()).hexdigest()
    with open(args.out_sha256, "w") as stream:
        stream.write(f"{digest}  {os.path.basename(args.out_tarball)}\n")


def main():
    args = parse_args()
    python = os.path.abspath(sys.executable)
    clang = os.path.abspath(args.clang)
    llvm = os.path.dirname(os.path.dirname(clang))
    ninja = os.path.abspath(args.ninja)
    source_root = os.path.dirname(os.path.abspath(args.source_marker))
    with open(args.sysroot_info) as stream:
        sysroot_info = json.load(stream)
    sysroot = sysroot_info["path"]
    if not os.path.isabs(sysroot):
        sysroot = os.path.join(os.path.dirname(os.path.abspath(args.sysroot_info)), sysroot)

    scratch = os.environ.get("ROOTBEER_SCRATCH") or os.path.abspath(args.out_binary + ".scratch")
    os.makedirs(scratch, exist_ok=True)
    scratch = os.path.realpath(scratch)
    tree = os.path.join(scratch, "node")

    bin_dir = write_tools(
        args, llvm, ninja, python, sysroot, sysroot_info["version"], scratch, args.old_v8)
    env = build_env(bin_dir, scratch, sysroot, args.os)

    copy_source(source_root, tree)
    if not os.path.exists(os.path.join(tree, "out", "Release", "build.ninja")):
        run(["python3", "configure.py"] + configure_flags(tree), tree, env)
    run(["ninja", "-C", "out/Release", f"-j{args.jobs}", "node"], tree, env)

    binary = os.path.join(tree, "out", "Release", "node")
    shutil.copyfile(binary, args.out_binary)
    os.chmod(args.out_binary, 0o755)
    pack(args, binary, os.path.join(tree, "LICENSE"), buildinfo(args, env, sysroot_info, python))
    if "ROOTBEER_SCRATCH" not in os.environ:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
