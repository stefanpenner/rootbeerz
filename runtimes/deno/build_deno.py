"""Builds Deno from patched source with the pinned tools only. Bazel runs this file as one action.

The action copies the source and the toolchain to a scratch directory, unpacks the pinned crates,
writes tool wrappers, and runs cargo with --locked and --offline. PATH holds the wrappers, then
/usr/bin and /bin for POSIX tools. Those POSIX tools and the macOS SDK come from the host.

Set ROOTBEER_SCRATCH to keep the scratch directory between runs. Set --fast-profile to skip LTO.
Use both for development only. Bazel sets neither.
"""

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile

BASE_PATH = "/usr/bin:/bin"
TARGET_LINKER_FLAGS = [
    # A static libc++ with chained fixups crashes at startup on Darwin 27. See docs/HERMETICITY.md.
    "-Wl,-no_fixup_chains",
    # Without -S, lld records the path of every object file in the symbol table.
    "-Wl,-S",
    "-Wl,--icf=safe",
    "-weak_framework", "Metal",
    "-weak_framework", "MetalPerformanceShaders",
    "-weak_framework", "QuartzCore",
    "-weak_framework", "CoreGraphics",
]
XCRUN_WRAPPER = """#!/bin/sh
while [ $# -gt 0 ]; do
  case "$1" in
    --sdk) shift 2 ;;
    --show-sdk-path) echo "{sdk}"; exit 0 ;;
    --show-sdk-version) echo "{version}"; exit 0 ;;
    --show-sdk-platform-path) echo "{sdk}"; exit 0 ;;
    -f|-find|--find) command -v "$2"; exit $? ;;
    *) exec "$@" ;;
  esac
done
"""


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-lock", required=True)
    parser.add_argument("--crates-index", required=True)
    parser.add_argument("--cargo", required=True)
    parser.add_argument("--clang", required=True)
    parser.add_argument("--cmake", required=True)
    parser.add_argument("--rusty-v8", required=True)
    parser.add_argument("--rusty-v8-binding", required=True)
    parser.add_argument("--sysroot-info", required=True)
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--fast-profile", action="store_true")
    parser.add_argument("--out", required=True)
    return parser.parse_args()


def write_tool(bin_dir, name, text):
    path = os.path.join(bin_dir, name)
    with open(path, "w") as stream:
        stream.write(text)
    os.chmod(path, 0o755)


def copy_tree(source, target):
    """Copies a tree and resolves the links, because the sandbox holds links to the inputs."""
    shutil.copytree(source, target, symlinks=False, dirs_exist_ok=True)
    for root, directories, files in os.walk(target):
        for name in directories + files:
            path = os.path.join(root, name)
            os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)


def unpack_crates(index_path, vendor):
    """Unpacks each pinned crate. Cargo compares the crate digest with the entry in Cargo.lock."""
    with open(index_path) as stream:
        index = json.load(stream)
    repo = os.path.dirname(index_path)
    names = {entry["name"] for entry in index}
    forbidden = sorted(name for name in names if name.startswith("webpki-root"))
    if forbidden:
        sys.exit(f"The crate set holds a root crate: {forbidden}")
    os.makedirs(vendor, exist_ok=True)
    for entry in index:
        stem = f"{entry['name']}-{entry['version']}"
        with tarfile.open(os.path.join(repo, entry["file"]), "r:gz") as archive:
            archive.extractall(vendor, filter="data")
        with open(os.path.join(vendor, stem, ".cargo-checksum.json"), "w") as stream:
            json.dump({"files": {}, "package": entry["sha256"]}, stream)


def write_cargo_config(cargo_home, vendor):
    os.makedirs(cargo_home, exist_ok=True)
    with open(os.path.join(cargo_home, "config.toml"), "w") as stream:
        stream.write(
            '[source.crates-io]\nreplace-with = "pinned"\n\n'
            f'[source.pinned]\ndirectory = "{vendor}"\n\n'
            "[net]\noffline = true\n"
        )


def write_tools(bin_dir, llvm_bin, scratch, llvm, sdk, sdk_version):
    os.makedirs(bin_dir, exist_ok=True)
    flags = " ".join([
        f"-ffile-prefix-map={scratch}=.",
        f"-ffile-prefix-map={llvm}=llvm",
        f"-ffile-prefix-map={sdk}=/sdk",
        "-Wno-unused-command-line-argument",
        "-fuse-ld=lld",
        "-isysroot", sdk,
    ])
    for name, tool in [("cc", "clang"), ("clang", "clang"), ("c++", "clang++"), ("clang++", "clang++")]:
        target = os.path.join(llvm_bin, tool)
        write_tool(bin_dir, name, f'#!/bin/sh\nexec "{target}" {flags} "$@"\n')
    for name, tool in [("ar", "llvm-ar"), ("ranlib", "llvm-ranlib"), ("nm", "llvm-nm"), ("strip", "llvm-strip")]:
        target = os.path.join(llvm_bin, tool)
        write_tool(bin_dir, name, f'#!/bin/sh\nexec "{target}" "$@"\n')
    write_tool(bin_dir, "xcrun", XCRUN_WRAPPER.format(sdk=sdk, version=sdk_version))


def rust_flags(scratch, vendor, cc):
    """Returns flags for CARGO_ENCODED_RUSTFLAGS. RUSTFLAGS splits a link-args value at each space."""
    link_args = " ".join(TARGET_LINKER_FLAGS)
    return "\x1f".join([
        "--cfg", "tokio_unstable",
        "-C", f"linker={cc}",
        "-C", f"link-args=-fuse-ld=lld {link_args}",
        f"--remap-path-prefix={vendor}=/cargo",
        f"--remap-path-prefix={scratch}=/deno",
    ])


def build_env(args, scratch, rust, bin_dir, cargo_home, vendor, llvm, sdk):
    cc = os.path.join(bin_dir, "cc")
    cxx = os.path.join(bin_dir, "c++")
    cmake_bin = os.path.dirname(os.path.abspath(args.cmake))
    env = {
        "PATH": os.pathsep.join([bin_dir, os.path.join(rust, "bin"), cmake_bin, BASE_PATH]),
        "HOME": scratch,
        "TMPDIR": os.path.join(scratch, "tmp"),
        "LC_ALL": "C",
        "SOURCE_DATE_EPOCH": "0",
        "ZERO_AR_DATE": "1",
        "SDKROOT": sdk,
        "MACOSX_DEPLOYMENT_TARGET": "12.0",
        "CARGO_HOME": cargo_home,
        "CARGO_TARGET_DIR": os.path.join(scratch, "target"),
        "CARGO_NET_OFFLINE": "true",
        "CARGO_INCREMENTAL": "0",
        "RUSTC": os.path.join(rust, "bin", "rustc"),
        # Cargo reads each parent config. An empty value stops a host compiler wrapper.
        "RUSTC_WRAPPER": "",
        "CARGO_ENCODED_RUSTFLAGS": rust_flags(scratch, vendor, cc),
        # The packed debug info needs dsymutil, and debug info holds the build path.
        "CARGO_PROFILE_RELEASE_DEBUG": "false",
        "CARGO_PROFILE_RELEASE_SPLIT_DEBUGINFO": "off",
        "CC": cc,
        "CXX": cxx,
        "AR": os.path.join(bin_dir, "ar"),
        "RANLIB": os.path.join(bin_dir, "ranlib"),
        "CMAKE": os.path.abspath(args.cmake),
        "LIBCLANG_PATH": os.path.join(llvm, "lib"),
        "BINDGEN_EXTRA_CLANG_ARGS": f"-isysroot {sdk}",
        "RUSTY_V8_ARCHIVE": os.path.abspath(args.rusty_v8),
        "RUSTY_V8_SRC_BINDING_PATH": os.path.abspath(args.rusty_v8_binding),
    }
    if args.fast_profile:
        env.update({
            "CARGO_PROFILE_RELEASE_LTO": "off",
            "CARGO_PROFILE_RELEASE_CODEGEN_UNITS": "32",
            "CARGO_PROFILE_RELEASE_OPT_LEVEL": "1",
        })
    return env


def main():
    args = parse_args()
    with open(args.sysroot_info) as stream:
        sysroot = json.load(stream)
    out = os.path.abspath(args.out)
    scratch = os.environ.get("ROOTBEER_SCRATCH") or out + ".scratch"
    source = os.path.join(scratch, "source")
    rust = os.path.join(scratch, "rust")
    vendor = os.path.join(scratch, "vendor")
    cargo_home = os.path.join(scratch, "cargo-home")
    bin_dir = os.path.join(scratch, "bin")
    llvm_bin = os.path.dirname(os.path.abspath(args.clang))
    llvm = os.path.dirname(llvm_bin)
    sdk = sysroot["path"]

    os.makedirs(os.path.join(scratch, "tmp"), exist_ok=True)
    copy_tree(os.path.dirname(os.path.abspath(args.source_lock)), source)
    copy_tree(os.path.dirname(os.path.dirname(os.path.abspath(args.cargo))), rust)
    unpack_crates(os.path.abspath(args.crates_index), vendor)
    write_cargo_config(cargo_home, vendor)
    write_tools(bin_dir, llvm_bin, scratch, llvm, sdk, sysroot["version"])

    env = build_env(args, scratch, rust, bin_dir, cargo_home, vendor, llvm, sdk)
    command = [
        os.path.join(rust, "bin", "cargo"), "build", "--release", "--locked", "--offline",
        "-j", str(args.jobs), "-p", "deno", "--bin", "deno",
    ]
    subprocess.run(command, cwd=source, env=env, check=True)

    binary = os.path.join(scratch, "target", "release", "deno")
    shutil.copy(binary, out)
    if not os.environ.get("ROOTBEER_SCRATCH"):
        shutil.rmtree(scratch)


if __name__ == "__main__":
    main()
