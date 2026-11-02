"""Fetches every Deno build input with a pinned sha256. Version and source sha256 come from runtimes/deno/manifest.json."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive", "http_file")
load("//runtimes/deno:pins.bzl", "CMAKE", "LINES")

_CRATE_URL = "https://static.crates.io/crates/{name}/{name}-{version}.crate"
_CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"
_RUST_URL = "https://static.rust-lang.org/dist/{date}/{package}-{version}-{triple}.tar.xz"
_V8_URL = "https://github.com/denoland/rusty_v8/releases/download/v{version}/{name}"

# The tests/ tree holds 15000 data files that the deno binary does not read. Cargo reads the
# manifests and the Rust files to load the workspace. Some data names are not valid Bazel labels.
_DROP_TEST_DATA = "find tests \\( -type f -o -type l \\) ! -name '*.toml' ! -name '*.rs' -delete"
_DROP_BUILD_FILES = "find . -mindepth 2 \\( -name BUILD -o -name BUILD.bazel -o -name WORKSPACE \\) -exec rm -f {} +"

_SOURCE_BUILD = """
package(default_visibility = ["//visibility:public"])

exports_files([
    "Cargo.lock",
    "Cargo.toml",
    "cli/lib/args.rs",
    "ext/node/Cargo.toml",
    "ext/node/ops/tls.rs",
    "ext/tls/Cargo.toml",
    "ext/tls/lib.rs",
    "libs/eszip/Cargo.toml",
])

filegroup(
    name = "all",
    srcs = glob(["**"]),
)
"""

_CRATES_BUILD = """
package(default_visibility = ["//visibility:public"])

exports_files(["index.json"])

filegroup(
    name = "all",
    srcs = glob(["crates/*.crate"]),
)
"""

_RUST_BUILD = """
package(default_visibility = ["//visibility:public"])

exports_files(["bin/cargo"])

filegroup(
    name = "all",
    srcs = glob(["bin/**", "lib/**"]),
)
"""

_STOCK_BUILD = """
package(default_visibility = ["//visibility:public"])

exports_files(["deno"])
"""

_CMAKE_BUILD = """
package(default_visibility = ["//visibility:public"])

exports_files(["CMake.app/Contents/bin/cmake"])

filegroup(
    name = "all",
    srcs = glob(["CMake.app/Contents/bin/**", "CMake.app/Contents/share/**"]),
)
"""

def _registry_packages(lock_text):
    """Returns (name, version, sha256) of each registry package in a Cargo.lock."""
    packages = []
    for block in lock_text.split("[[package]]")[1:]:
        fields = {}
        for line in block.split("\n"):
            if " = \"" in line and not line.startswith(" "):
                key, value = line.split(" = \"", 1)
                fields[key] = value.rstrip("\"")
        source = fields.get("source")
        if source == None:
            continue
        if source != _CRATES_IO:
            fail("Cargo.lock names a source that has no pin: %s" % source)
        packages.append((fields["name"], fields["version"], fields["checksum"]))
    return packages

def _crates_impl(rctx):
    packages = _registry_packages(rctx.read(rctx.attr.lock))
    index = []
    pending = []
    for name, version, sha256 in packages:
        file_name = "crates/%s-%s.crate" % (name, version)
        pending.append(rctx.download(
            _CRATE_URL.format(name = name, version = version),
            output = file_name,
            sha256 = sha256,
            block = False,
        ))
        index.append({"name": name, "version": version, "sha256": sha256, "file": file_name})
    for download in pending:
        download.wait()
    rctx.file("index.json", json.encode_indent(index, indent = " ") + "\n")
    rctx.file("BUILD.bazel", _CRATES_BUILD)

_crates = repository_rule(
    implementation = _crates_impl,
    attrs = {"lock": attr.label(mandatory = True, allow_single_file = True)},
)

def _rust_impl(rctx):
    triple = rctx.attr.triple
    version = rctx.attr.version
    for package, sha256, inner in [
        ("rustc", rctx.attr.rustc_sha256, "rustc"),
        ("cargo", rctx.attr.cargo_sha256, "cargo"),
        ("rust-std", rctx.attr.rust_std_sha256, "rust-std-" + triple),
    ]:
        rctx.download_and_extract(
            _RUST_URL.format(date = rctx.attr.date, package = package, version = version, triple = triple),
            sha256 = sha256,
            strip_prefix = "%s-%s-%s/%s" % (package, version, triple, inner),
        )
    rctx.file("BUILD.bazel", _RUST_BUILD)

_rust = repository_rule(
    implementation = _rust_impl,
    attrs = {
        "version": attr.string(mandatory = True),
        "date": attr.string(mandatory = True),
        "triple": attr.string(mandatory = True),
        "rustc_sha256": attr.string(mandatory = True),
        "cargo_sha256": attr.string(mandatory = True),
        "rust_std_sha256": attr.string(mandatory = True),
    },
)

def _line_repos(entry):
    line = entry["line"]
    version = entry["version"]
    pins = LINES[line]
    darwin = pins["darwin_arm64"]
    triple = darwin["triple"]
    v8 = pins["rusty_v8_version"]
    prefix = "deno_%s_" % line

    http_archive(
        name = prefix + "src",
        url = "https://github.com/denoland/deno/releases/download/v%s/deno_src.tar.gz" % version,
        sha256 = entry["sha256"],
        strip_prefix = "deno",
        build_file_content = _SOURCE_BUILD,
        patches = ["//runtimes/deno:patches/%s/0001-remove-bundled-roots.patch" % line],
        patch_args = ["-p1"],
        patch_cmds = [_DROP_TEST_DATA, _DROP_BUILD_FILES],
    )
    _crates(name = prefix + "crates", lock = "@%ssrc//:Cargo.lock" % prefix)
    _rust(
        name = prefix + "rust",
        version = pins["rust_version"],
        date = pins["rust_date"],
        triple = triple,
        rustc_sha256 = darwin["rustc"],
        cargo_sha256 = darwin["cargo"],
        rust_std_sha256 = darwin["rust_std"],
    )
    http_file(
        name = prefix + "rusty_v8",
        url = _V8_URL.format(version = v8, name = "librusty_v8_simdutf_release_%s.a.gz" % triple),
        sha256 = darwin["rusty_v8"],
        downloaded_file_path = "librusty_v8.a.gz",
    )
    http_file(
        name = prefix + "rusty_v8_binding",
        url = _V8_URL.format(version = v8, name = "src_binding_simdutf_release_%s.rs" % triple),
        sha256 = darwin["rusty_v8_binding"],
        downloaded_file_path = "src_binding.rs",
    )
    http_archive(
        name = prefix + "stock",
        url = "https://github.com/denoland/deno/releases/download/v%s/deno-%s.zip" % (version, triple),
        sha256 = darwin["stock"],
        build_file_content = _STOCK_BUILD,
    )

def _deno_impl(mctx):
    manifest = json.decode(mctx.read(Label("//runtimes/deno:manifest.json")))
    for entry in manifest["versions"]:
        _line_repos(entry)
    http_archive(
        name = "deno_cmake",
        url = "https://github.com/Kitware/CMake/releases/download/v{v}/cmake-{v}-macos-universal.tar.gz".format(v = CMAKE["version"]),
        sha256 = CMAKE["macos_sha256"],
        strip_prefix = "cmake-%s-macos-universal" % CMAKE["version"],
        build_file_content = _CMAKE_BUILD,
    )
    return mctx.extension_metadata(reproducible = True)

deno = module_extension(implementation = _deno_impl)
