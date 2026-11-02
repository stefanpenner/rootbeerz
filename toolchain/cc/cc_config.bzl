"""The C++ toolchain config: the pinned LLVM, plus the pinned sysroot or the host macOS SDK."""

load("@rules_cc//cc:action_names.bzl", "ACTION_NAMES")
load(
    "@rules_cc//cc:cc_toolchain_config_lib.bzl",
    "action_config",
    "feature",
    "flag_group",
    "flag_set",
    "tool",
)
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")

_C_COMPILE = [ACTION_NAMES.c_compile, ACTION_NAMES.assemble, ACTION_NAMES.preprocess_assemble]
_CXX_COMPILE = [ACTION_NAMES.cpp_compile, ACTION_NAMES.linkstamp_compile]
_LINK = [
    ACTION_NAMES.cpp_link_executable,
    ACTION_NAMES.cpp_link_dynamic_library,
    ACTION_NAMES.cpp_link_nodeps_dynamic_library,
]

_COMPILE_FLAGS = [
    # Paths stay relative to the exec root, so no sandbox path enters an output.
    "-no-canonical-prefixes",
    "-Wno-builtin-macro-redefined",
    '-D__DATE__="redacted"',
    '-D__TIMESTAMP__="redacted"',
    '-D__TIME__="redacted"',
]

def _flags(name, actions, flags):
    return feature(
        name = name,
        enabled = True,
        flag_sets = [flag_set(actions = actions, flag_groups = [flag_group(flags = flags)])],
    )

def _tools(actions, file):
    return [action_config(action_name = action, tools = [tool(tool = file)]) for action in actions]

def _rooted(path, *dirs):
    return ["%workspace%/" + path + "/" + name for name in dirs]

def _llvm_root(ctx):
    return ctx.file.clang.dirname.rsplit("/", 1)[0]

def _sysroot_flags(ctx, root):
    if ctx.attr.os == "darwin":
        return ["-isysroot", root]
    return ["--sysroot=" + root]

def _link_flags(ctx):
    flags = ["-fuse-ld=lld", "-no-canonical-prefixes", "-nostdlib++"]
    flags += [ctx.file.libcxx.path, ctx.file.libcxxabi.path]
    if ctx.attr.os == "darwin":
        # docs/HERMETICITY.md explains why a static libc++ needs -no_fixup_chains.
        return flags + ["-Wl,-no_fixup_chains", "-Wl,-S"]
    return flags + ["--rtlib=libgcc", "--unwindlib=libgcc", "-lpthread", "-ldl", "-lm"]

def _include_directories(ctx, llvm, root):
    llvm_dirs = ["include/c++/v1", "lib/clang/19/include"]
    if ctx.attr.os == "darwin":
        return _rooted(llvm, *llvm_dirs) + _rooted(root, "usr/include")
    llvm_dirs.append("include/x86_64-unknown-linux-gnu/c++/v1")
    return _rooted(llvm, *llvm_dirs) + _rooted(root, "usr/include", "usr/lib/gcc/x86_64-linux-gnu/10/include")

def _cc_config_impl(ctx):
    llvm = _llvm_root(ctx)
    root = ctx.file.sysroot_marker.dirname
    sysroot_flags = _sysroot_flags(ctx, root)
    cxx_flags = ["-std=c++17", "-stdlib=libc++", "-D_LIBCPP_DISABLE_AVAILABILITY"]
    all_compile = _C_COMPILE + _CXX_COMPILE

    return cc_common.create_cc_toolchain_config_info(
        ctx = ctx,
        toolchain_identifier = "rootbeerz-llvm-" + ctx.attr.os,
        compiler = "clang",
        target_system_name = ctx.attr.os,
        target_cpu = ctx.attr.cpu,
        target_libc = "unknown",
        abi_version = "unknown",
        abi_libc_version = "unknown",
        cxx_builtin_include_directories = _include_directories(ctx, llvm, root),
        action_configs = (
            _tools(_C_COMPILE, ctx.file.clang) +
            _tools(_CXX_COMPILE, ctx.file.clangxx) +
            _tools(_LINK, ctx.file.clangxx) +
            _tools([ACTION_NAMES.cpp_link_static_library], ctx.file.ar) +
            _tools([ACTION_NAMES.strip], ctx.file.strip)
        ),
        features = [
            feature(name = "supports_pic", enabled = True),
            _flags("rootbeerz_compile", all_compile, _COMPILE_FLAGS + sysroot_flags),
            _flags("rootbeerz_cxx", _CXX_COMPILE, cxx_flags),
            feature(
                name = "opt",
                flag_sets = [flag_set(
                    actions = all_compile,
                    flag_groups = [flag_group(flags = ["-O2", "-DNDEBUG"])],
                )],
            ),
            _flags("rootbeerz_link_sysroot", _LINK, sysroot_flags),
            _flags("rootbeerz_link", _LINK, _link_flags(ctx)),
        ],
    )

cc_config = rule(
    implementation = _cc_config_impl,
    attrs = {
        "ar": attr.label(allow_single_file = True, mandatory = True),
        "clang": attr.label(allow_single_file = True, mandatory = True),
        "clangxx": attr.label(allow_single_file = True, mandatory = True),
        "cpu": attr.string(mandatory = True),
        "libcxx": attr.label(allow_single_file = True, mandatory = True),
        "libcxxabi": attr.label(allow_single_file = True, mandatory = True),
        "os": attr.string(mandatory = True, values = ["darwin", "linux"]),
        "strip": attr.label(allow_single_file = True, mandatory = True),
        # A file directly in the sysroot directory. Its directory is the sysroot.
        "sysroot_marker": attr.label(allow_single_file = True, mandatory = True),
    },
)
