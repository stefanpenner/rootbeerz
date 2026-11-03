"""Builds one JDK from pinned source with an empty cacerts. Bazel runs this file as one action.

The action unpacks the source, builds make, bash, perl, m4, and autoconf from pinned source,
runs the JDK configure with an empty cacerts directory, runs make, and copies the image.
Each tool on PATH is a pinned tool or a row in HOST_TOOLS. runtimes/jdk/README.md lists each input.

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
import re
import subprocess
import sys
import tarfile

# The build finds a host tool only through a link in the host directory. Each row has one reason.
# The build finds every other tool in a pinned archive. tools_test.py checks the rows.
_POSIX = "POSIX utility that configure and make call. macOS ships it on the sealed system volume."
_MACOS = "macOS system tool with no pinned build. The JDK build calls it on macOS."
HOST_TOOLS = {
    **{name: _POSIX for name in [
        "awk", "basename", "cat", "chmod", "cmp", "comm", "cp", "cut", "date", "df", "diff",
        "dirname", "echo", "egrep", "env", "expr", "find", "grep", "head", "install", "ln",
        "locale", "ls", "mkdir", "mktemp", "mv", "nice", "printf", "readlink", "realpath", "rm",
        "rmdir", "sed", "sh", "sleep", "sort", "split", "tail", "tee", "test", "time", "touch",
        "tr", "true", "ulimit", "uname", "uniq", "wc", "which", "xargs",
    ]},
    "arch": _MACOS + " configure reads the CPU name.",
    "sw_vers": _MACOS + " configure reads the macOS version.",
    "codesign": _MACOS + " Each binary gets an ad hoc signature. LLVM has no codesign.",
    "mig": _MACOS + " It generates the Mach interface stubs for the debugger agent.",
    "dtrace": _MACOS + " It generates the DTrace probe header for HotSpot.",
    "xattr": _MACOS + " make clears the extended attributes of each copied file.",
    "SetFile": _MACOS + " configure records it on macOS. The image build calls it.",
    "file": _MACOS + " configure records it to read the type of a binary.",
    "tar": _MACOS + " configure records it. The image build calls it. The driver unpacks each source with tarfile.",
    "gzip": _MACOS + " configure records it. The image build calls it.",
    "gunzip": _MACOS + " configure requires it.",
    "zip": _MACOS + " configure records it. The image build calls it.",
    "unzip": _MACOS + " configure records it. The image build can call it.",
}
HOST_SEARCH = ["/usr/bin", "/bin", "/usr/sbin"]
# A tool path in spec.gmk under one of these directories did not come from PATH.
HOST_PREFIXES = ("/usr/", "/bin/", "/sbin/", "/opt/", "/Library/", "/Applications/")
# Each tool builds from a pinned source, in this order. autoconf records the perl that it finds.
TOOL_ORDER = ["make", "bash", "perl", "m4", "autoconf"]
TOOL_FLAGS = {"bash": ["--without-bash-malloc", "--disable-nls"]}
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
    parser.add_argument("--bash-src", required=True)
    parser.add_argument("--perl-src", required=True)
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
        # The macOS sandbox blocks the javac server bind.
        "--disable-javac-server",
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


def build_env(wrap_bin, tools_bin, host_bin, sysroot, scratch):
    return {
        "PATH": ":".join([wrap_bin, tools_bin, host_bin]),
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


def _strip_top(member, path):
    """Drops the top directory of a member. A member that leaves the tree stops the build."""
    _, _, rest = member.name.partition("/")
    if not rest:
        return None
    changes = {"name": rest}
    if member.islnk():
        changes["linkname"] = member.linkname.partition("/")[2]
    return tarfile.data_filter(member.replace(**changes, deep=False), path)


def unpack(archive, tree):
    """Unpacks one source archive into a new directory with no top directory. A kept scratch keeps the old result."""
    if os.path.isdir(tree):
        return
    temp = tree + ".unpacking"
    shutil.rmtree(temp, ignore_errors=True)
    os.makedirs(temp)
    try:
        with tarfile.open(archive) as tar:
            tar.extractall(temp, filter=_strip_top)
    except tarfile.FilterError as error:
        sys.exit(f"{archive}: {error}")
    os.rename(temp, tree)


def link_host_tools(host_bin, tools, search):
    """Links each host row into one directory. PATH holds that directory, not the host directories."""
    shutil.rmtree(host_bin, ignore_errors=True)
    os.makedirs(host_bin)
    for name in sorted(tools):
        found = [os.path.join(d, name) for d in search if os.access(os.path.join(d, name), os.X_OK)]
        if not found:
            sys.exit(f"The host tool {name} is not in {':'.join(search)}. HOST_TOOLS needs it: {tools[name]}")
        os.symlink(found[0], os.path.join(host_bin, name))
    return host_bin


def host_tool_leaks(spec, allowed, sysroot):
    """Returns each spec.gmk tool line whose program is on a host path and is not a host row.

    The sysroot is a recorded input, so a value under it is not a leak.
    """
    leaks = []
    for line in spec.splitlines():
        match = re.match(r"\s*(\w+)\s*:=\s*(/\S+)", line)
        if not match:
            continue
        path = match.group(2)
        if path == sysroot or path.startswith(sysroot + "/"):
            continue
        if path.startswith(HOST_PREFIXES) and os.path.basename(path) not in allowed:
            leaks.append(line.strip())
    return leaks


# Each plain name that the build tools ask for, and the pinned LLVM tool that answers it.
LLVM_WRAPPERS = {
    "cc": "clang",
    "c++": "clang++",
    "ld": "ld64.lld",
    "ar": "llvm-ar",
    "ranlib": "llvm-ranlib",
    "nm": "llvm-nm",
    "strip": "llvm-strip",
    "lipo": "llvm-lipo",
    "otool": "llvm-otool",
    "install_name_tool": "llvm-install-name-tool",
    "dsymutil": "dsymutil",
    "c++filt": "llvm-cxxfilt",
    "objdump": "llvm-objdump",
}


def write_wrappers(scratch, llvm):
    """Writes one shim for each LLVM_WRAPPERS row. A target that is not an action input stops the build."""
    missing = [t for t in LLVM_WRAPPERS.values() if not os.access(os.path.join(llvm, "bin", t), os.X_OK)]
    if missing:
        sys.exit(f"The pinned LLVM in {llvm} has no {', '.join(missing)}. Add each to a filegroup in toolchain/llvm.BUILD.")
    wrap_bin = os.path.join(scratch, "bin")
    os.makedirs(wrap_bin, exist_ok=True)
    for name, target in LLVM_WRAPPERS.items():
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

    The Metal tools need an Xcode component that a host may not have. runtimes/jdk/README.md states this limit.
    """
    library = os.path.join(boot, "lib", "shaders.metallib")
    actions = {"metal": ': > "$out"', "metallib": f'cp "{library}" "$out" && chmod 644 "$out"'}
    for name, action in actions.items():
        path = os.path.join(wrap_bin, name)
        with open(path, "w") as stream:
            stream.write(_METAL_SHIM.format(name=name, action=action))
        os.chmod(path, 0o755)


def perl_configure(prefix, sysroot):
    """The perl Configure options. perl does not read CFLAGS or LDFLAGS."""
    return [
        "-des",
        f"-Dprefix={prefix}",
        "-Dcc=cc",
        f"-Accflags=-isysroot {sysroot}",
        f"-Aldflags=-isysroot {sysroot} -fuse-ld=lld",
        "-Dman1dir=none",
        "-Dman3dir=none",
    ]


def build_tool(source_archive, name, prefix, scratch, env, jobs, sysroot):
    """Builds one tool from its pinned source. Later tools and the JDK read it from prefix/bin.

    make and bash configure with the host /bin/sh. Each later tool configures with the pinned bash.
    """
    done = os.path.join(prefix, f".{name}.done")
    if os.path.exists(done):
        return
    tree = os.path.join(scratch, "src", name)
    unpack(source_archive, tree)
    shell = "/bin/sh" if name in ("make", "bash") else os.path.join(prefix, "bin", "bash")
    env = dict(env, CONFIG_SHELL=shell)
    if name == "perl":
        run([shell, "./Configure", *perl_configure(prefix, sysroot)], tree, env)
    else:
        flags = ["--disable-dependency-tracking", *TOOL_FLAGS.get(name, [])]
        run([shell, "./configure", f"--prefix={prefix}", *flags], tree, env)
    if name == "make":
        run([shell, "./build.sh"], tree, env)
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
    tools_bin = os.path.join(tools, "bin")
    wrap_bin = write_wrappers(scratch, llvm)
    host_bin = link_host_tools(os.path.join(scratch, "host"), HOST_TOOLS, HOST_SEARCH)
    env = build_env(wrap_bin, tools_bin, host_bin, sysroot, scratch)
    os.makedirs(env["TMPDIR"], exist_ok=True)

    sources = {
        "make": args.make_src,
        "bash": args.bash_src,
        "perl": args.perl_src,
        "m4": args.m4_src,
        "autoconf": args.autoconf_src,
    }
    tool_env = dict(env, CC="cc", CFLAGS=f"-isysroot {sysroot} -O1", LDFLAGS=f"-isysroot {sysroot} -fuse-ld=lld")
    for name in TOOL_ORDER:
        build_tool(os.path.abspath(sources[name]), name, tools, scratch, tool_env, args.jobs, sysroot)

    write_autoconf_shim(wrap_bin, tools_bin)

    boot = copy_boot_jdk(args.boot_java, scratch)
    write_metal_shims(wrap_bin, boot)
    tree = os.path.join(scratch, "src", "jdk")
    unpack(os.path.abspath(args.source), tree)

    empty = os.path.join(scratch, "empty-cacerts")
    os.makedirs(empty, exist_ok=True)
    out = os.path.join(scratch, "out")
    os.makedirs(out, exist_ok=True)
    flags = configure_flags(boot, sysroot, empty, args.jobs, args.version, scratch, llvm)
    bash = os.path.join(tools_bin, "bash")
    if not os.path.exists(os.path.join(out, "Makefile")):
        run([bash, os.path.join(tree, "configure"), *flags], out, dict(env, CONFIG_SHELL=bash))
    with open(os.path.join(out, "spec.gmk")) as stream:
        leaks = host_tool_leaks(stream.read(), set(HOST_TOOLS), sysroot)
    if leaks:
        sys.exit("spec.gmk names a host tool that is not a HOST_TOOLS row:\n" + "\n".join(leaks))
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
