"""Finds the macOS SDK. The SDK license does not allow a public copy.

Never publish an SDK tarball. Do not commit it, upload it, or add it to an artifact,
a shared cache, or a public URL.

The default SDK is a host input. The repository records its path, version, and
settings digest in info.json. The repository also links the SDK directory as `sdk/`,
and `:files` lists every file in it. The Node build declares these files as inputs,
so a change to any SDK file makes a new cache key. docs/HERMETICITY.md describes
the remaining gap: the action still reads the SDK at its host path.

`--config=pinned-sdk` sets ROOTBEER_PINNED_SDK=1. The repository then fetches a private
tarball from ROOTBEER_PINNED_SDK_URL and checks it against ROOTBEER_PINNED_SDK_SHA256.
The user supplies both values. The action reads the declared `sdk/` files. info.json
goes into the published BUILDINFO.json, so it never records the address.
"""

_SDK_ROOTS = [
    "/Library/Developer/CommandLineTools/SDKs",
    "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs",
]

# ld64.lld from LLVM 19 cannot read a .tbd file that lists this architecture.
_UNREADABLE_ARCH = "arm64e.x1"

def _version_of(name):
    return name[len("MacOSX"):-len(".sdk")]

def _version_key(name):
    return [int(part) for part in _version_of(name).split(".")]

def _candidates(rctx):
    found = []
    for root in _SDK_ROOTS:
        root_path = rctx.path(root)
        if not root_path.exists:
            continue
        for entry in root_path.readdir():
            name = entry.basename
            if name.startswith("MacOSX") and name.endswith(".sdk") and name != "MacOSX.sdk":
                found.append(entry)
    return sorted(found, key = lambda entry: _version_key(entry.basename), reverse = True)

def _readable_by_lld(rctx, sdk):
    tbd = sdk.get_child("usr").get_child("lib").get_child("libSystem.B.tbd")
    return tbd.exists and _UNREADABLE_ARCH not in rctx.read(tbd)

# Bazel cannot declare these as inputs, and the Node build does not read them.
# - Ruby.framework holds a symlink to its own directory (Headers/ruby/ruby -> .). Bazel cannot glob a loop.
# - usr/share/man has file names with ':' (APR::Base64.3pm). A label cannot hold ':'.
# A new SDK that has another such path fails the glob with a clear error.
_SKIP = [
    "System/Library/Frameworks/Ruby.framework",
    "usr/share/man",
]

def _link_sdk(rctx, real):
    """Links the SDK as sdk/. Directories on the way to a skipped path are real, the rest are links."""
    skip_parents = {}
    for skip in _SKIP:
        parts = skip.split("/")
        for i in range(1, len(parts)):
            skip_parents["/".join(parts[:i])] = True
    pending = [""]
    for _ in range(len(skip_parents) + 1):
        next_pending = []
        for rel in pending:
            parent = real.get_child(*rel.split("/")) if rel else real
            for entry in parent.readdir():
                child = rel + "/" + entry.basename if rel else entry.basename
                if child in _SKIP:
                    continue
                if child in skip_parents:
                    next_pending.append(child)
                else:
                    rctx.symlink(entry, "sdk/" + child)
        pending = next_pending

def _select_sdk(rctx):
    override = rctx.os.environ.get("ROOTBEER_MACOS_SDK")
    if override:
        return rctx.path(override)
    for sdk in _candidates(rctx):
        if _readable_by_lld(rctx, sdk):
            return sdk
    fail("No macOS SDK that ld64.lld can read. Install Command Line Tools, or set ROOTBEER_MACOS_SDK.")

def _sha256(rctx, path):
    result = rctx.execute(["/usr/bin/shasum", "-a", "256", str(path)])
    if result.return_code != 0:
        fail("Cannot hash %s: %s" % (path, result.stderr))
    return result.stdout.split(" ")[0]

def _pinned_address(rctx):
    url = rctx.os.environ.get("ROOTBEER_PINNED_SDK_URL", "")
    sha256 = rctx.os.environ.get("ROOTBEER_PINNED_SDK_SHA256", "")
    if not url or not sha256:
        fail("--config=pinned-sdk needs ROOTBEER_PINNED_SDK_URL and ROOTBEER_PINNED_SDK_SHA256. " +
             "Give both with --repo_env. Remove the config to use the host SDK. docs/HERMETICITY.md has the procedure.")
    if len(sha256) != 64 or [c for c in sha256.elems() if c not in "0123456789abcdef"]:
        fail("ROOTBEER_PINNED_SDK_SHA256 must be 64 hex characters in lower case.")
    if rctx.os.environ.get("ROOTBEER_MACOS_SDK"):
        fail("ROOTBEER_MACOS_SDK selects a host SDK, and --config=pinned-sdk selects a tarball. Remove one.")
    return url, sha256

def _pinned_info(rctx):
    """Fetches the tarball, checks it, and links the SDK as sdk/. The address stays out of info.json."""
    url, sha256 = _pinned_address(rctx)
    fetched = rctx.download_and_extract(url = url, sha256 = sha256, output = "pinned", allow_fail = True)
    if not fetched.success:
        fail("Cannot fetch or verify the pinned SDK. Check the tarball at ROOTBEER_PINNED_SDK_URL " +
             "and the value of ROOTBEER_PINNED_SDK_SHA256.")
    sdks = [e for e in rctx.path("pinned").readdir() if e.basename.endswith(".sdk")]
    if len(sdks) != 1:
        fail("The pinned SDK tarball must hold exactly one *.sdk directory. tools/pack_macos_sdk.py writes this shape.")
    sdk = sdks[0]
    settings = sdk.get_child("SDKSettings.json")
    if not settings.exists:
        fail("The pinned SDK tarball has no SDKSettings.json.")
    if not _readable_by_lld(rctx, sdk):
        fail("ld64.lld cannot read the pinned SDK. Its libSystem.B.tbd is absent or lists %s." % _UNREADABLE_ARCH)
    _link_sdk(rctx, sdk)
    return {
        "path": "sdk",
        "sdk_dir": "sdk",
        "source": "pinned",
        "version": json.decode(rctx.read(settings))["Version"],
        "settings_sha256": _sha256(rctx, settings),
        "tarball_sha256": sha256,
    }

def _macos_sdk_impl(rctx):
    if rctx.os.name != "mac os x":
        info = {"path": "", "version": "", "settings_sha256": ""}
    elif rctx.os.environ.get("ROOTBEER_PINNED_SDK") == "1":
        info = _pinned_info(rctx)
    else:
        sdk = _select_sdk(rctx)
        real = sdk.realpath
        settings = real.get_child("SDKSettings.json")
        if not settings.exists:
            fail("%s has no SDKSettings.json" % real)
        settings_data = json.decode(rctx.read(settings))
        info = {
            "path": str(real),
            "sdk_dir": "sdk",
            "version": settings_data["Version"],
            "settings_sha256": _sha256(rctx, settings),
        }
        _link_sdk(rctx, real)
    rctx.file("info.json", json.encode_indent(info, indent = "  ") + "\n")
    rctx.file("BUILD.bazel", """package(default_visibility = ["//visibility:public"])

exports_files(["info.json", "sdk/SDKSettings.json"])

# Empty on Linux, where there is no SDK.
filegroup(
    name = "files",
    srcs = glob(["sdk/**"], allow_empty = True),
)

# The small part of the SDK that the C++ toolchain reads. Each compile action takes these inputs.
filegroup(
    name = "cc_files",
    srcs = glob(
        ["sdk/SDKSettings.json", "sdk/usr/include/**", "sdk/usr/lib/*.tbd", "sdk/usr/lib/system/**"],
        allow_empty = True,
    ),
)
""")

macos_sdk = repository_rule(
    implementation = _macos_sdk_impl,
    environ = [
        "ROOTBEER_MACOS_SDK",
        "ROOTBEER_PINNED_SDK",
        "ROOTBEER_PINNED_SDK_URL",
        "ROOTBEER_PINNED_SDK_SHA256",
    ],
    local = True,
)
