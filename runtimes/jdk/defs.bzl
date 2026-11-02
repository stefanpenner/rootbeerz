"""Builds a JDK from source with an empty cacerts, in one action, with no network."""

_PY_TOOLCHAIN = "@rules_python//python:toolchain_type"

# Other agents and the Node build share this machine. The driver runs at most 6 compile jobs.
def _jdk_build_resources(_os_name, _inputs):
    return {"cpu": 6, "memory": 16000}

def _jdk_build_impl(ctx):
    runtime = ctx.toolchains[_PY_TOOLCHAIN].py3_runtime
    version = ctx.attr.version
    stem = "jdk-%s-%s-%s" % (version, ctx.attr.os_name, ctx.attr.arch)
    home = ctx.actions.declare_directory(ctx.label.name)
    tarball = ctx.actions.declare_file(stem + ".tar.gz")
    sha256 = ctx.actions.declare_file(stem + ".tar.gz.sha256")

    args = ctx.actions.args()
    args.add("-I")
    args.add(ctx.file._driver)
    args.add("--version", version)
    args.add("--source", ctx.file.source)
    args.add("--boot-java", ctx.file.boot_java)
    args.add("--make-src", ctx.file.make)
    args.add("--bash-src", ctx.file.bash)
    args.add("--perl-src", ctx.file.perl)
    args.add("--m4-src", ctx.file.m4)
    args.add("--autoconf-src", ctx.file.autoconf)
    args.add("--clang", ctx.file.clang)
    args.add("--sysroot-info", ctx.file.sysroot_info)
    args.add("--jobs", ctx.attr.jobs)
    args.add("--out-home", home.path)
    args.add("--out-tarball", tarball)
    args.add("--out-sha256", sha256)

    ctx.actions.run(
        executable = runtime.interpreter,
        arguments = [args],
        inputs = depset(
            [
                ctx.file._driver,
                ctx.file.source,
                ctx.file.boot_java,
                ctx.file.make,
                ctx.file.bash,
                ctx.file.perl,
                ctx.file.m4,
                ctx.file.autoconf,
                ctx.file.clang,
                ctx.file.sysroot_info,
            ],
            transitive = [
                depset(ctx.files.boot_files),
                depset(ctx.files.llvm),
                depset(ctx.files.macho_tools),
                depset(ctx.files.sysroot_files),
            ],
        ),
        tools = runtime.files,
        outputs = [home, tarball, sha256],
        mnemonic = "JdkBuild",
        progress_message = "Building JDK %s" % version,
        resource_set = _jdk_build_resources,
        use_default_shell_env = False,
    )

    return [
        DefaultInfo(files = depset([home]), runfiles = ctx.runfiles([home])),
        OutputGroupInfo(dist = depset([tarball, sha256])),
    ]

jdk_build = rule(
    implementation = _jdk_build_impl,
    attrs = {
        "version": attr.string(mandatory = True),
        "os_name": attr.string(mandatory = True),
        "arch": attr.string(mandatory = True),
        "source": attr.label(mandatory = True, allow_single_file = True),
        "boot_java": attr.label(mandatory = True, allow_single_file = True),
        "boot_files": attr.label(mandatory = True, allow_files = True),
        "make": attr.label(mandatory = True, allow_single_file = True),
        "bash": attr.label(mandatory = True, allow_single_file = True),
        "perl": attr.label(mandatory = True, allow_single_file = True),
        "m4": attr.label(mandatory = True, allow_single_file = True),
        "autoconf": attr.label(mandatory = True, allow_single_file = True),
        "llvm": attr.label(mandatory = True, allow_files = True),
        "macho_tools": attr.label(mandatory = True, allow_files = True),
        "clang": attr.label(mandatory = True, allow_single_file = True),
        "sysroot_info": attr.label(mandatory = True, allow_single_file = True),
        "sysroot_files": attr.label_list(allow_files = True),
        "jobs": attr.int(default = 6),
        "_driver": attr.label(default = "//runtimes/jdk:build_jdk.py", allow_single_file = True),
    },
    toolchains = [_PY_TOOLCHAIN],
)

def jdk_line(line, version):
    """Defines the stock control and the rootbeerz JDK for one line.

    Args:
      line: The feature release, for example "25".
      version: The pinned version, for example "25.0.5+8".
    """
    hub = "@jdk_versions//:"
    stock = "%sstock_%s_" % (hub, line)
    files = select({
        "//toolchain:macos_arm64": stock + "darwin_arm64_files",
        "//toolchain:linux_x64": stock + "linux_x64_files",
    })
    java = select({
        "//toolchain:macos_arm64": stock + "darwin_arm64_java",
        "//toolchain:linux_x64": stock + "linux_x64_java",
    })
    native.alias(name = "stock_%s_files" % line, actual = files)
    native.alias(name = "stock_%s_java" % line, actual = java)

    jdk_build(
        name = "jdk_%s" % line,
        version = version,
        os_name = "darwin",
        arch = "arm64",
        source = "%ssource_%s" % (hub, line),
        boot_java = ":stock_%s_java" % line,
        boot_files = ":stock_%s_files" % line,
        make = hub + "tool_make",
        bash = hub + "tool_bash",
        perl = hub + "tool_perl",
        m4 = hub + "tool_m4",
        autoconf = hub + "tool_autoconf",
        llvm = "//toolchain:llvm",
        macho_tools = "//toolchain:macho_tools",
        clang = "//toolchain:clang",
        sysroot_info = "//toolchain:sysroot_info",
        sysroot_files = ["//toolchain:sysroot_files"],
        # The macOS arm64 path is the only one that this repository has built.
        target_compatible_with = [
            "@platforms//os:macos",
            "@platforms//cpu:arm64",
        ],
    )

    native.filegroup(
        name = "dist_%s" % line,
        srcs = [":jdk_%s" % line],
        output_group = "dist",
    )
