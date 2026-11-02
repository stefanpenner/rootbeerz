"""Tests that read the patched Deno source. The tests do not run the binary."""

import re
import unittest

from trustlib import path_from_env


def read(env):
    with open(path_from_env(env), encoding="utf-8") as stream:
        return stream.read()


def body_of(source, signature):
    """Returns the text from the signature to the first line that closes the function."""
    start = source.index(signature)
    end = source.index("\n}\n", start)
    return source[start:end]


class Lockfile(unittest.TestCase):
    def test_lockfile_has_no_webpki_roots_package(self):
        self.assertNotRegex(read("CARGO_LOCK"), r'name = "webpki-roots?"')

    def test_lockfile_has_no_webpki_root_certs_package(self):
        self.assertNotRegex(read("CARGO_LOCK"), r'name = "webpki-root-certs"')

    def test_lockfile_has_no_edge_to_a_root_crate(self):
        self.assertNotRegex(read("CARGO_LOCK"), r'"webpki-root')


class Manifests(unittest.TestCase):
    def test_workspace_does_not_declare_a_root_crate(self):
        self.assertNotRegex(read("CARGO_TOML"), r"(?m)^webpki-root")

    def test_workspace_reqwest_has_no_bundled_roots(self):
        self.assertNotRegex(read("CARGO_TOML"), r'(?m)^reqwest = .*"rustls-tls"')

    def test_eszip_reqwest_has_no_bundled_roots(self):
        self.assertNotIn('"rustls-tls"', read("ESZIP_CARGO_TOML"))

    def test_tls_crate_does_not_depend_on_a_root_crate(self):
        self.assertNotIn("webpki-root", read("TLS_CARGO_TOML"))

    def test_node_crate_does_not_depend_on_a_root_crate(self):
        self.assertNotIn("webpki-root", read("NODE_CARGO_TOML"))


class CliStore(unittest.TestCase):
    def setUp(self):
        self.source = read("CLI_ARGS_RS")

    def test_cli_does_not_use_a_root_crate(self):
        self.assertNotIn("webpki_root", self.source)

    def test_default_store_is_the_system_store(self):
        self.assertIn('vec!["system".to_string()]', self.source)
        self.assertNotIn('vec!["mozilla".to_string()]', self.source)

    def test_mozilla_value_selects_the_system_store(self):
        self.assertRegex(self.source, r'"mozilla"\s*\|\s*"system"\s*=>')


class TlsCrate(unittest.TestCase):
    def setUp(self):
        self.source = read("TLS_LIB_RS")

    def test_tls_crate_does_not_use_a_root_crate(self):
        self.assertNotIn("webpki_root", self.source)

    def test_default_store_loads_the_system_store(self):
        body = body_of(self.source, "pub fn create_default_root_cert_store")
        self.assertIn("load_native_certs()", body)


class NodeCompatibility(unittest.TestCase):
    def setUp(self):
        self.source = read("NODE_TLS_RS")

    def test_node_layer_does_not_use_a_root_crate(self):
        self.assertNotIn("webpki_root", self.source)

    def test_node_default_is_the_system_store(self):
        self.assertIn('.unwrap_or_else(|| "system".to_string())', self.source)

    def test_node_mozilla_value_selects_the_system_store(self):
        self.assertNotIn('stores.contains(&"mozilla")\n', self.source)
        self.assertRegex(
            self.source,
            re.compile(r'stores\.contains\(&"mozilla"\)\s*\|\|\s*stores\.contains\(&"system"\)'),
        )

    def test_node_root_certificates_come_from_the_system_store(self):
        body = body_of(self.source, "pub fn op_get_root_certificates")
        self.assertIn("load_system_ca_certificates()", body)


if __name__ == "__main__":
    unittest.main()
