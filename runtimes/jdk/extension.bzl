"""Reads runtimes/jdk/manifest.json and creates the repositories of each JDK line. MODULE.bazel holds no JDK pin."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive", "http_file")

_STOCK_BUILD = """
package(default_visibility = ["//visibility:public"])

exports_files(["bin/java"])

filegroup(
    name = "files",
    srcs = glob(["**"], exclude = ["BUILD.bazel", "REPO.bazel", "WORKSPACE"]),
)
"""

def _hub_impl(rctx):
    versions = json.decode(rctx.attr.versions)
    rctx.file("versions.bzl", "JDK_VERSIONS = %s\n" % json.encode(versions))
    build = ['package(default_visibility = ["//visibility:public"])\n']
    for name, actual in json.decode(rctx.attr.aliases).items():
        build.append('alias(name = "%s", actual = "%s")\n' % (name, actual))
    rctx.file("BUILD.bazel", "\n".join(build))

_hub = repository_rule(
    implementation = _hub_impl,
    attrs = {
        "versions": attr.string(mandatory = True),
        "aliases": attr.string(mandatory = True),
    },
)

def _jdk_impl(module_ctx):
    manifest = json.decode(module_ctx.read(Label("//runtimes/jdk:manifest.json")))
    versions = {}
    aliases = {}

    for name, tool in manifest["build_tools"].items():
        repo = "jdk_tool_" + name
        http_file(
            name = repo,
            downloaded_file_path = tool["url"].rsplit("/", 1)[1],
            sha256 = tool["sha256"],
            urls = [tool["url"]],
        )
        aliases["tool_" + name] = "@%s//file" % repo

    for entry in manifest["versions"]:
        line = entry["line"]
        versions[line] = entry["version"]
        source = "jdk_source_" + line
        http_file(
            name = source,
            downloaded_file_path = "source.tar.gz",
            sha256 = entry["sha256"],
            urls = [entry["source_url"]],
        )
        aliases["source_" + line] = "@%s//file" % source
        for os_name, stock in entry["stock"].items():
            repo = "jdk_stock_%s_%s" % (line, os_name)
            http_archive(
                name = repo,
                build_file_content = _STOCK_BUILD,
                sha256 = stock["sha256"],
                strip_prefix = stock["strip_prefix"],
                urls = [stock["url"]],
            )
            aliases["stock_%s_%s_files" % (line, os_name)] = "@%s//:files" % repo
            aliases["stock_%s_%s_java" % (line, os_name)] = "@%s//:bin/java" % repo

    _hub(name = "jdk_versions", versions = json.encode(versions), aliases = json.encode(aliases))
    # Each download has a sha256, so the lockfile need not record this extension.
    return module_ctx.extension_metadata(reproducible = True)

jdk = module_extension(implementation = _jdk_impl)
