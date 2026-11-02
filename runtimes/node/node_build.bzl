"""Builds Node from patched source in one action with the pinned tools."""

_PY_TOOLCHAIN = "@rules_python//python:toolchain_type"

def _node_build_resources(_os_name, _inputs):
    return {"cpu": 10, "memory": 16000}

def _node_build_impl(ctx):
    runtime = ctx.toolchains[_PY_TOOLCHAIN].py3_runtime
    version = ctx.attr.version
    stem = "node-v%s-%s-%s" % (version, ctx.attr.os_name, ctx.attr.arch)
    binary = ctx.actions.declare_file(ctx.label.name + "_bin/node")
    tarball = ctx.actions.declare_file(stem + ".tar.gz")
    sha256 = ctx.actions.declare_file(stem + ".tar.gz.sha256")

    args = ctx.actions.args()
    args.add("-I")
    args.add(ctx.file._driver)
    args.add("--source-marker", ctx.file.configure)
    args.add("--version", version)
    args.add("--os", ctx.attr.os_name)
    args.add("--arch", ctx.attr.arch)
    args.add("--clang", ctx.file.clang)
    args.add("--ninja", ctx.file.ninja)
    args.add("--sysroot-info", ctx.file.sysroot_info)
    if ctx.attr.old_v8:
        args.add("--old-v8")
    if ctx.attr.jobs:
        args.add("--jobs", ctx.attr.jobs)
    args.add("--out-binary", binary)
    args.add("--out-tarball", tarball)
    args.add("--out-sha256", sha256)

    ctx.actions.run(
        executable = runtime.interpreter,
        arguments = [args],
        inputs = depset(
            [ctx.file._driver, ctx.file.configure, ctx.file.clang, ctx.file.ninja, ctx.file.sysroot_info],
            transitive = [
                depset(ctx.files.source),
                depset(ctx.files.llvm),
                depset(ctx.files.sysroot_files),
            ],
        ),
        tools = runtime.files,
        outputs = [binary, tarball, sha256],
        mnemonic = "NodeBuild",
        progress_message = "Building Node %s" % version,
        resource_set = _node_build_resources,
        use_default_shell_env = False,
    )

    return [
        DefaultInfo(files = depset([binary]), runfiles = ctx.runfiles([binary])),
        OutputGroupInfo(dist = depset([tarball, sha256])),
    ]

node_build = rule(
    implementation = _node_build_impl,
    attrs = {
        "version": attr.string(mandatory = True),
        "os_name": attr.string(mandatory = True),
        "arch": attr.string(mandatory = True),
        "source": attr.label(mandatory = True, allow_files = True),
        "configure": attr.label(mandatory = True, allow_single_file = True),
        "llvm": attr.label(mandatory = True, allow_files = True),
        "clang": attr.label(mandatory = True, allow_single_file = True),
        "ninja": attr.label(mandatory = True, allow_single_file = True),
        "sysroot_info": attr.label(mandatory = True, allow_single_file = True),
        "sysroot_files": attr.label_list(allow_files = True),
        "old_v8": attr.bool(doc = "Adds the Clang warning flags that the V8 of Node 16 and 18 needs."),
        "jobs": attr.int(doc = "Compile jobs. Zero keeps the driver default."),
        "_driver": attr.label(default = "//runtimes/node:build_node.py", allow_single_file = True),
    },
    toolchains = [_PY_TOOLCHAIN],
)
