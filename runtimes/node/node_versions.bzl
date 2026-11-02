"""Module extension that declares every pinned Node line from `node.version` tags.

One tag in MODULE.bazel is the whole entry for a version. The extension creates:
  - node_<major>: the patched source, with the patches from patches/<major>/
  - stock_<major>_darwin_arm64 and stock_<major>_linux_x64: the upstream control binaries
  - node_versions: a hub with aliases, `versions.bzl`, and `versions.json`

Each sha256 comes from the SHASUMS256.txt file of the release.
tools/add_node_version.py prints the tag for a release.
"""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

_STOCK_BUILD = 'exports_files(["bin/node"])'

# The files that the source tests read. Each also gets an alias in the hub.
_SOURCE_FILES = {
    "configure": "configure.py",
    "crypto_context_cc": "src/crypto/crypto_context.cc",
    "node_mksnapshot_cc": "tools/snapshot/node_mksnapshot.cc",
    "node_options_cc": "src/node_options.cc",
    "node_root_certs_h": "src/node_root_certs.h",
    "openssl_common_gypi": "deps/openssl/openssl_common.gypi",
}

_TRUST_PATCHES = ["0001-empty-root-certs.patch", "0002-force-local-store.patch"]
_OLD_V8_PATCHES = [
    "0003-libcxx-19-char-traits.patch",
    "0004-zlib-macos-test.patch",
    "0005-predictable-snapshot.patch",
]

def _stock(name, version, platform, sha256):
    stem = "node-v%s-%s" % (version, platform)
    http_archive(
        name = name,
        build_file_content = _STOCK_BUILD,
        sha256 = sha256,
        strip_prefix = stem,
        urls = ["https://nodejs.org/dist/v%s/%s.tar.gz" % (version, stem)],
    )

def _hub_impl(rctx):
    lines = json.decode(rctx.attr.lines_json)
    build = ['package(default_visibility = ["//visibility:public"])', "", 'exports_files(["versions.bzl", "versions.json"])', ""]
    for line in lines:
        major = line["major"]
        build.append('alias(name = "source_%s", actual = "@node_%s//:source")' % (major, major))
        for key, path in _SOURCE_FILES.items():
            build.append('alias(name = "%s_%s", actual = "@node_%s//:%s")' % (key, major, major, path))
        for platform in ("darwin_arm64", "linux_x64"):
            build.append('alias(name = "stock_%s_%s", actual = "@stock_%s_%s//:bin/node")' % (major, platform, major, platform))
        build.append("")
    rctx.file("BUILD.bazel", "\n".join(build))
    rctx.file("versions.json", json.encode_indent(lines) + "\n")
    rctx.file("versions.bzl", "\n".join([
        '"""Generated from the node.version tags in MODULE.bazel."""',
        "",
        "NODE_VERSIONS = {",
    ] + ['    %s: "%s",' % (l["major"], l["version"]) for l in lines] + [
        "}",
        "",
        "OLD_V8 = [%s]" % ", ".join([str(l["major"]) for l in lines if l["old_v8"]]),
        "",
    ]))

_hub = repository_rule(
    implementation = _hub_impl,
    attrs = {"lines_json": attr.string(mandatory = True)},
)

def _impl(mctx):
    lines = {}
    for module in mctx.modules:
        if not module.is_root:
            continue
        for tag in module.tags.version:
            major = tag.major
            if major in lines:
                fail("node.version: major %s is declared twice" % major)
            if not tag.version.startswith(major + "."):
                fail("node.version: %s is not in line %s" % (tag.version, major))
            lines[major] = tag

    # Highest line first, so `NODE_VERSIONS` and the READMEs read newest to oldest.
    ordered = sorted(lines.values(), key = lambda tag: -int(tag.major))
    for tag in ordered:
        major = tag.major
        names = [Label("//runtimes/node/patches:%s/%s" % (major, name)) for name in _TRUST_PATCHES]
        if tag.old_v8:
            names += [Label("//runtimes/node/patches:%s/%s" % (major, name)) for name in _OLD_V8_PATCHES]
        http_archive(
            name = "node_" + major,
            build_file = Label("//runtimes/node:node_source.BUILD"),
            sha256 = tag.sha256,
            strip_prefix = "node-v" + tag.version,
            urls = ["https://nodejs.org/dist/v{0}/node-v{0}.tar.gz".format(tag.version)],
            # V8 and others ship BUILD files. These files would split the source tree into packages.
            patch_args = ["-p1"],
            patches = names,
            # Use -exec, not -delete. BusyBox find has no -delete.
            patch_cmds = ["find deps tools -type f \\( -name BUILD -o -name BUILD.bazel \\) -exec rm -f {} +"],
        )
        _stock("stock_%s_darwin_arm64" % major, tag.version, "darwin-arm64", tag.stock_darwin_arm64_sha256)
        _stock("stock_%s_linux_x64" % major, tag.version, "linux-x64", tag.stock_linux_x64_sha256)
    _hub(
        name = "node_versions",
        lines_json = json.encode([
            {
                "major": int(tag.major),
                "version": tag.version,
                "old_v8": tag.old_v8,
                "source_sha256": tag.sha256,
            }
            for tag in ordered
        ]),
    )
    return mctx.extension_metadata(reproducible = True)

_version = tag_class(
    doc = "One pinned Node release.",
    attrs = {
        "major": attr.string(mandatory = True, doc = "The line, for example 22."),
        "version": attr.string(mandatory = True, doc = "The release, for example 22.23.4."),
        "sha256": attr.string(mandatory = True, doc = "The sha256 of node-vX.Y.Z.tar.gz."),
        "stock_darwin_arm64_sha256": attr.string(mandatory = True),
        "stock_linux_x64_sha256": attr.string(mandatory = True),
        "old_v8": attr.bool(
            default = False,
            doc = "Builds with the Clang warning flags and patches that the V8 of Node 16 and 18 needs.",
        ),
    },
)

node = module_extension(
    implementation = _impl,
    tag_classes = {"version": _version},
)
