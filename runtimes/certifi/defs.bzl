"""Builds the certifi shim wheel."""

def _certifi_wheel_impl(ctx):
    out = ctx.actions.declare_file("dist/certifi-%s-py3-none-any.whl" % ctx.attr.version)
    args = ctx.actions.args()
    args.add("--version", ctx.attr.version)
    args.add("--out", out)
    args.add_all(ctx.files.srcs)
    ctx.actions.run(
        executable = ctx.executable._builder,
        arguments = [args],
        inputs = ctx.files.srcs,
        outputs = [out],
        mnemonic = "CertifiWheel",
        use_default_shell_env = False,
    )
    return [DefaultInfo(files = depset([out]))]

certifi_wheel = rule(
    implementation = _certifi_wheel_impl,
    attrs = {
        "srcs": attr.label_list(allow_files = True, mandatory = True),
        "version": attr.string(mandatory = True),
        "_builder": attr.label(
            default = "//runtimes/certifi:build_wheel",
            executable = True,
            cfg = "exec",
        ),
    },
)
