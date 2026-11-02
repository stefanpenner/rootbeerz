package(default_visibility = ["//visibility:public"])

filegroup(
    name = "source",
    srcs = glob(["**"], exclude = ["BUILD.bazel", "REPO.bazel", "WORKSPACE*"]),
)

exports_files([
    "configure.py",
    "deps/openssl/openssl_common.gypi",
    "src/crypto/crypto_context.cc",
    "src/node_options.cc",
    "src/node_root_certs.h",
])
