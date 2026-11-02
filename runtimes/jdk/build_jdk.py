"""Builds one JDK from pinned source with an empty cacerts. Bazel runs this file as one action.

The action unpacks the source, builds make, m4, and autoconf from pinned source,
runs the JDK configure with an empty cacerts directory, runs make, and copies the image.
docs/JDK.md lists each input and each host read.

Set ROOTBEER_SCRATCH to keep the scratch directory between runs. Use this for
development only. Bazel does not set it.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile

# The host POSIX tools, codesign, and the Xcode tools. docs/JDK.md states why.
BASE_PATH = "/usr/bin:/bin:/usr/sbin"
# GenerateCacerts writes this exact PKCS12 store when the source directory is empty.
# The store has no entry, no salt, and no MAC, so the bytes are the same in each build.
EMPTY_PKCS12 = bytes.fromhex("30160201033011" "06092a864886f70d010701" "a00404023000")
SOURCE_EPOCH = 0


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--boot-java", required=True)
    parser.add_argument("--make-src", required=True)
    parser.add_argument("--m4-src", required=True)
    parser.add_argument("--autoconf-src", required=True)
    parser.add_argument("--clang", required=True)
    parser.add_argument("--sysroot-info", required=True)
    parser.add_argument("--jobs", type=int, required=True)
    parser.add_argument("--out-home", required=True)
    parser.add_argument("--out-tarball", required=True)
    parser.add_argument("--out-sha256", required=True)
    return parser.parse_args()


def require_empty_cacerts(data):
    """Stops the build unless the bytes are the PKCS12 store with no entry."""
    if data != EMPTY_PKCS12:
        sys.exit(f"cacerts: the file is not the empty PKCS12 store: {len(data)} bytes, {data[:16].hex()}")


def configure_flags(boot_jdk, sysroot, empty_cacerts, jobs, version, scratch, llvm):
    build = version.split("+")[1]
    # lld records no object path with -S. docs/HERMETICITY.md states why.
    prefix = f"-ffile-prefix-map={scratch}=. -ffile-prefix-map={llvm}=llvm"
    cxx_headers = f"-nostdinc++ -isystem {sysroot}/usr/include/c++/v1"
    return [
        f"--with-boot-jdk={boot_jdk}",
        f"--with-sysroot={sysroot}",
        f"--with-toolchain-path={llvm}/bin",
        "--with-toolchain-type=clang",
        f"--with-cacerts-src={empty_cacerts}",
        f"--with-jobs={jobs}",
        f"--with-num-cores={jobs}",
        "--with-memory-size=16000",
        "--with-debug-level=release",
        "--with-jvm-variants=server",
        "--with-native-debug-symbols=none",
        "--disable-warnings-as-errors",
        "--with-version-pre=",
        f"--with-version-build={build}",
        "--with-version-opt=rootbeerz",
        "--with-vendor-name=rootbeerz",
        "--with-source-date=version",
        "--with-build-user=rootbeerz",
        f"--with-extra-cflags={prefix}",
        f"--with-extra-cxxflags={prefix} {cxx_headers}",
        "--with-extra-ldflags=-fuse-ld=lld -Wl,-S",
    ]


def build_env(wrap_bin, tools_bin, sysroot, scratch):
    return {
        "PATH": ":".join([wrap_bin, tools_bin, BASE_PATH]),
        "HOME": scratch,
        "TMPDIR": os.path.join(scratch, "tmp"),
        "LC_ALL": "C",
        "LANG": "C",
        "SOURCE_DATE_EPOCH": str(SOURCE_EPOCH),
        "ZERO_AR_DATE": "1",
        "SDKROOT": sysroot,
    }


def run(command, cwd, env):
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def unpack(archive, tree, env):
    """Unpacks one source archive into a new directory. A kept scratch keeps the old result."""
    if os.path.isdir(tree):
        return
    temp = tree + ".unpacking"
    shutil.rmtree(temp, ignore_errors=True)
    os.makedirs(temp)
    run(["/usr/bin/tar", "-xf", archive, "--strip-components=1", "-C", temp], "/", env)
    os.rename(temp, tree)


def write_wrappers(scratch, llvm):
    """Selects the pinned LLVM tools by the plain name that the build tools ask for."""
    wrap_bin = os.path.join(scratch, "bin")
    os.makedirs(wrap_bin, exist_ok=True)
    for name, target in [
        ("cc", "clang"),
        ("c++", "clang++"),
        ("ar", "llvm-ar"),
        ("ranlib", "llvm-ranlib"),
        ("nm", "llvm-nm"),
        ("strip", "llvm-strip"),
    ]:
        path = os.path.join(wrap_bin, name)
        with open(path, "w") as stream:
            stream.write(f'#!/bin/sh\nexec "{llvm}/bin/{target}" "$@"\n')
        os.chmod(path, 0o755)
    return wrap_bin


def autoconf_shim(tools_bin):
    """Returns a script that calls autom4te. On Darwin 27 the exec in the autoconf script does not return."""
    return (
        "#!/bin/sh\n"
        'case "$1" in\n'
        f'--version) exec "{tools_bin}/autoconf" --version ;;\n'
        "esac\n"
        f'exec "{tools_bin}/autom4te" --language=autoconf --output=- "$@"\n'
    )


def write_autoconf_shim(wrap_bin, tools_bin):
    path = os.path.join(wrap_bin, "autoconf")
    with open(path, "w") as stream:
        stream.write(autoconf_shim(tools_bin))
    os.chmod(path, 0o755)


_METAL_SHIM = """#!/bin/sh
out=
while [ $# -gt 0 ]; do
  case "$1" in
    --version) echo "rootbeerz {name} shim"; exit 0 ;;
    -o) shift; out="$1" ;;
  esac
  shift
done
{action}
"""


def write_metal_shims(wrap_bin, boot):
    """Replaces the Xcode Metal tools. The shader library comes from the pinned boot JDK of the same version.

    The Metal tools need an Xcode component that a host may not have. docs/JDK.md states this limit.
    """
    library = os.path.join(boot, "lib", "shaders.metallib")
    actions = {"metal": ': > "$out"', "metallib": f'cp "{library}" "$out" && chmod 644 "$out"'}
    for name, action in actions.items():
        path = os.path.join(wrap_bin, name)
        with open(path, "w") as stream:
            stream.write(_METAL_SHIM.format(name=name, action=action))
        os.chmod(path, 0o755)


def build_tool(source_archive, name, prefix, scratch, env, jobs, bootstrap=False):
    """Builds one GNU tool from its pinned source. Later tools and the JDK read it from prefix/bin."""
    done = os.path.join(prefix, f".{name}.done")
    if os.path.exists(done):
        return
    tree = os.path.join(scratch, "src", name)
    unpack(source_archive, tree, env)
    run(["/bin/sh", "./configure", f"--prefix={prefix}", "--disable-dependency-tracking"], tree, env)
    if bootstrap:
        run(["/bin/sh", "./build.sh"], tree, env)
        os.makedirs(os.path.join(prefix, "bin"), exist_ok=True)
        shutil.copy2(os.path.join(tree, "make"), os.path.join(prefix, "bin", "make"))
    else:
        run(["make", f"-j{jobs}"], tree, env)
        run(["make", "install"], tree, env)
    with open(done, "w"):
        pass


def copy_boot_jdk(boot_java, scratch):
    """Copies the boot JDK with real files. Bazel gives a tree of symlinks to read-only files."""
    home = os.path.dirname(os.path.dirname(os.path.abspath(boot_java)))
    target = os.path.join(scratch, "boot")
    if not os.path.isdir(target):
        temp = target + ".copying"
        shutil.rmtree(temp, ignore_errors=True)
        shutil.copytree(home, temp, symlinks=False)
        os.rename(temp, target)
    return target


def copy_home(image, out_home):
    shutil.copytree(image, out_home, symlinks=True, dirs_exist_ok=True)


def pack(home, stem, out_tarball, out_sha256):
    """Writes the image as a tar.gz with sorted names, fixed times, and no owner."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        members = []
        for root, dirs, files in os.walk(home):
            dirs.sort()
            members += [os.path.join(root, name) for name in dirs + files]
        for path in sorted(members):
            name = os.path.join(stem, os.path.relpath(path, home))
            info = archive.gettarinfo(path, arcname=name)
            info.mtime = SOURCE_EPOCH
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if info.mode & stat.S_IXUSR or info.isdir() else 0o644
            if info.isfile():
                with open(path, "rb") as stream:
                    archive.addfile(info, stream)
            else:
                archive.addfile(info)
    with open(out_tarball, "wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=6) as stream:
            stream.write(buffer.getvalue())
    with open(out_tarball, "rb") as stream:
        digest = hashlib.sha256(stream.read()).hexdigest()
    with open(out_sha256, "w") as stream:
        stream.write(f"{digest}  {os.path.basename(out_tarball)}\n")


def main():
    args = parse_args()
    clang = os.path.abspath(args.clang)
    llvm = os.path.dirname(os.path.dirname(clang))
    with open(args.sysroot_info) as stream:
        sysroot = json.load(stream)["path"]
    if not os.path.isabs(sysroot):
        sysroot = os.path.join(os.path.dirname(os.path.abspath(args.sysroot_info)), sysroot)

    scratch = os.environ.get("ROOTBEER_SCRATCH") or os.path.abspath(args.out_tarball + ".scratch")
    os.makedirs(scratch, exist_ok=True)
    scratch = os.path.realpath(scratch)
    tools = os.path.join(scratch, "tools")
    wrap_bin = write_wrappers(scratch, llvm)
    env = build_env(wrap_bin, os.path.join(tools, "bin"), sysroot, scratch)
    os.makedirs(env["TMPDIR"], exist_ok=True)

    tool_env = dict(env, CC="cc", CFLAGS=f"-isysroot {sysroot} -O1", LDFLAGS=f"-isysroot {sysroot} -fuse-ld=lld")
    build_tool(os.path.abspath(args.make_src), "make", tools, scratch, tool_env, args.jobs, bootstrap=True)
    build_tool(os.path.abspath(args.m4_src), "m4", tools, scratch, tool_env, args.jobs)
    build_tool(os.path.abspath(args.autoconf_src), "autoconf", tools, scratch, tool_env, args.jobs)

    write_autoconf_shim(wrap_bin, os.path.join(tools, "bin"))

    boot = copy_boot_jdk(args.boot_java, scratch)
    write_metal_shims(wrap_bin, boot)
    tree = os.path.join(scratch, "src", "jdk")
    unpack(os.path.abspath(args.source), tree, env)

    empty = os.path.join(scratch, "empty-cacerts")
    os.makedirs(empty, exist_ok=True)
    out = os.path.join(scratch, "out")
    os.makedirs(out, exist_ok=True)
    flags = configure_flags(boot, sysroot, empty, args.jobs, args.version, scratch, llvm)
    if not os.path.exists(os.path.join(out, "Makefile")):
        run(["/bin/bash", os.path.join(tree, "configure"), *flags], out, env)
    run(["make", "images", f"JOBS={args.jobs}", "LOG=warn"], out, env)

    image = os.path.join(out, "images", "jdk")
    with open(os.path.join(image, "lib", "security", "cacerts"), "rb") as stream:
        require_empty_cacerts(stream.read())
    copy_home(image, args.out_home)
    stem = f"jdk-{args.version}-darwin-arm64"
    pack(args.out_home, stem, args.out_tarball, args.out_sha256)
    if "ROOTBEER_SCRATCH" not in os.environ:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    main()
