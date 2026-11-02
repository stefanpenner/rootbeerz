package(default_visibility = ["//visibility:public"])

exports_files(["info.json"])

filegroup(
    name = "files",
    srcs = glob(
        ["**"],
        exclude = ["BUILD.bazel", "REPO.bazel", "WORKSPACE*"],
    ),
)
