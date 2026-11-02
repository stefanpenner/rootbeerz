"""Builds Bun from patched source in one action with the pinned inputs."""

_PY_TOOLCHAIN = "@rules_python//python:toolchain_type"

def _resources(_os_name, _inputs):
    return {"cpu": 6, "memory": 16000}

def _bun_build_impl(ctx):
    runtime = ctx.toolchains[_PY_TOOLCHAIN].py3_runtime
    binary = ctx.actions.declare_file(ctx.label.name + "/bun")

    args = ctx.actions.args()
    args.add("-I")
    args.add(ctx.file._driver)
    args.add("--source-marker", ctx.file.source_marker)
    args.add("--inputs-json", ctx.file.inputs_json)
    args.add("--stock-bun", ctx.file.stock_bun)
    args.add("--identity", ctx.file.patch)
    args.add("--jobs", ctx.attr.jobs)
    args.add("--out", binary)
    args.add("--sysroot-info", ctx.file.sysroot_info)

    # This value keeps the work directory across runs. The README states the limit.
    env = {}
    scratch = ctx.configuration.default_shell_env.get("ROOTBEER_SCRATCH")
    if scratch:
        env["ROOTBEER_SCRATCH"] = scratch

    singles = [ctx.file._driver, ctx.file.source_marker, ctx.file.inputs_json, ctx.file.stock_bun, ctx.file.patch, ctx.file.sysroot_info]
    ctx.actions.run(
        executable = runtime.interpreter,
        arguments = [args],
        inputs = depset(singles, transitive = [depset(ctx.files.source), depset(ctx.files.inputs), depset(ctx.files.sysroot_files)]),
        tools = runtime.files,
        outputs = [binary],
        env = env,
        mnemonic = "BunBuild",
        progress_message = "Building Bun %{label}",
        resource_set = _resources,
        use_default_shell_env = False,
    )
    return [DefaultInfo(files = depset([binary]), runfiles = ctx.runfiles([binary]))]

bun_build = rule(
    implementation = _bun_build_impl,
    attrs = {
        "source": attr.label(mandatory = True, allow_files = True),
        "source_marker": attr.label(mandatory = True, allow_single_file = True),
        "inputs": attr.label(mandatory = True, allow_files = True),
        "inputs_json": attr.label(mandatory = True, allow_single_file = True),
        "stock_bun": attr.label(mandatory = True, allow_single_file = True),
        "patch": attr.label(mandatory = True, allow_single_file = True),
        "jobs": attr.int(default = 6),
        # The SDK that @macos_sdk selects. ld64.lld can read it. The xcrun default can be too new.
        "sysroot_info": attr.label(default = Label("//toolchain:sysroot_info"), allow_single_file = True),
        "sysroot_files": attr.label_list(default = [Label("//toolchain:sysroot_files")], allow_files = True),
        "_driver": attr.label(default = Label("//runtimes/bun:build_bun.py"), allow_single_file = True),
    },
    toolchains = [_PY_TOOLCHAIN],
)
