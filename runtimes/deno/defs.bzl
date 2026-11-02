"""Builds Deno from patched source in one action with the pinned tools."""

_PY_TOOLCHAIN = "@rules_python//python:toolchain_type"

def _resources(_os_name, _inputs):
    return {"cpu": 6, "memory": 16000}

def _deno_build_impl(ctx):
    runtime = ctx.toolchains[_PY_TOOLCHAIN].py3_runtime
    binary = ctx.actions.declare_file(ctx.label.name + "/deno")

    args = ctx.actions.args()
    args.add("-I")
    args.add(ctx.file._driver)
    args.add("--source-lock", ctx.file.source_lock)
    args.add("--crates-index", ctx.file.crates_index)
    args.add("--cargo", ctx.file.cargo)
    args.add("--clang", ctx.file.clang)
    args.add("--cmake", ctx.file.cmake)
    args.add("--rusty-v8", ctx.file.rusty_v8)
    args.add("--rusty-v8-binding", ctx.file.rusty_v8_binding)
    args.add("--sysroot-info", ctx.file.sysroot_info)
    args.add("--jobs", ctx.attr.jobs)
    args.add("--out", binary)

    singles = [
        ctx.file._driver,
        ctx.file.source_lock,
        ctx.file.crates_index,
        ctx.file.cargo,
        ctx.file.clang,
        ctx.file.cmake,
        ctx.file.rusty_v8,
        ctx.file.rusty_v8_binding,
        ctx.file.sysroot_info,
    ]
    many = [
        ctx.files.source,
        ctx.files.crates,
        ctx.files.rust,
        ctx.files.llvm,
        ctx.files.libclang,
        ctx.files.cmake_files,
        ctx.files.sysroot_files,
    ]
    ctx.actions.run(
        executable = runtime.interpreter,
        arguments = [args],
        inputs = depset(singles, transitive = [depset(files) for files in many]),
        tools = runtime.files,
        outputs = [binary],
        mnemonic = "DenoBuild",
        progress_message = "Building Deno %{label}",
        resource_set = _resources,
        use_default_shell_env = False,
    )
    return [DefaultInfo(files = depset([binary]), runfiles = ctx.runfiles([binary]))]

deno_build = rule(
    implementation = _deno_build_impl,
    attrs = {
        "source": attr.label(mandatory = True, allow_files = True),
        "source_lock": attr.label(mandatory = True, allow_single_file = True),
        "crates": attr.label(mandatory = True, allow_files = True),
        "crates_index": attr.label(mandatory = True, allow_single_file = True),
        "rust": attr.label(mandatory = True, allow_files = True),
        "cargo": attr.label(mandatory = True, allow_single_file = True),
        "llvm": attr.label(mandatory = True, allow_files = True),
        "libclang": attr.label(mandatory = True, allow_files = True),
        "clang": attr.label(mandatory = True, allow_single_file = True),
        "cmake": attr.label(mandatory = True, allow_single_file = True),
        "cmake_files": attr.label(mandatory = True, allow_files = True),
        "rusty_v8": attr.label(mandatory = True, allow_single_file = True),
        "rusty_v8_binding": attr.label(mandatory = True, allow_single_file = True),
        "sysroot_info": attr.label(mandatory = True, allow_single_file = True),
        "sysroot_files": attr.label(mandatory = True, allow_files = True),
        "jobs": attr.int(default = 6),
        "_driver": attr.label(default = "//runtimes/deno:build_deno.py", allow_single_file = True),
    },
    toolchains = [_PY_TOOLCHAIN],
)
