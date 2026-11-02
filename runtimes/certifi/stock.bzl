"""The pinned stock certifi wheel. runtimes/certifi/manifest.json owns the version and the sha256."""

_URL = "https://files.pythonhosted.org/packages/py3/c/certifi/certifi-{version}-py3-none-any.whl"

def _certifi_stock_impl(rctx):
    manifest = json.decode(rctx.read(Label("//runtimes/certifi:manifest.json")))
    if len(manifest["versions"]) != 1:
        fail("runtimes/certifi/manifest.json must hold one version")
    entry = manifest["versions"][0]
    rctx.download(
        url = _URL.format(version = entry["version"]),
        output = "certifi.whl",
        sha256 = entry["sha256"],
    )
    rctx.file("version.bzl", 'CERTIFI_VERSION = "%s"\n' % entry["version"])
    rctx.file("BUILD.bazel", 'exports_files(["certifi.whl", "version.bzl"], visibility = ["//visibility:public"])\n')

certifi_stock = repository_rule(implementation = _certifi_stock_impl)
