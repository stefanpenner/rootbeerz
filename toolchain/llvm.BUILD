package(default_visibility = ["//visibility:public"])

exports_files([
    "bin/clang",
    "bin/clang++",
    "bin/llvm-ar",
    "bin/llvm-readobj",
    "bin/llvm-strip",
    "lib/libc++.a",
    "lib/libc++abi.a",
    "lib/x86_64-unknown-linux-gnu/libc++.a",
    "lib/x86_64-unknown-linux-gnu/libc++abi.a",
])

filegroup(
    name = "toolchain",
    srcs = glob(
        [
            "bin/clang",
            "bin/clang++",
            "bin/clang-*",
            "bin/ld64.lld",
            "bin/ld.lld",
            "bin/lld",
            "bin/llvm-ar",
            "bin/llvm-libtool-darwin",
            "bin/llvm-nm",
            "bin/llvm-objcopy",
            "bin/llvm-ranlib",
            "bin/llvm-strip",
            "include/c++/**",
            "include/*linux*/c++/**",
            "lib/clang/**",
            "lib/libc++.a",
            "lib/libc++abi.a",
            "lib/*linux*/libc++.a",
            "lib/*linux*/libc++abi.a",
        ],
        allow_empty = True,
        exclude = [
            "bin/clang-apply-replacements",
            "bin/clang-change-namespace",
            "bin/clang-check",
            "bin/clang-doc",
            "bin/clang-extdef-mapping",
            "bin/clang-format",
            "bin/clang-include-cleaner",
            "bin/clang-include-fixer",
            "bin/clang-installapi",
            "bin/clang-linker-wrapper",
            "bin/clang-move",
            "bin/clang-nvlink-wrapper",
            "bin/clang-offload-bundler",
            "bin/clang-offload-packager",
            "bin/clang-pseudo",
            "bin/clang-query",
            "bin/clang-refactor",
            "bin/clang-rename",
            "bin/clang-reorder-fields",
            "bin/clang-repl",
            "bin/clang-scan-deps",
            "bin/clang-sycl-linker",
            "bin/clang-tblgen",
            "bin/clang-tidy",
        ],
    ),
)

# Bindgen loads libclang at build time. This group stays apart from the toolchain group, because a
# change to the toolchain group changes the inputs of each Node build.
filegroup(
    name = "libclang",
    srcs = glob(["lib/libclang.dylib"], allow_empty = True),
)
