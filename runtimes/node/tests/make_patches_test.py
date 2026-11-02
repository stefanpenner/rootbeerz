"""Tests tools/make_patches.py and the configure flags on small fixtures. No network."""

import importlib.util
import os
import subprocess
import tempfile
import unittest


def load(name, env):
    spec = importlib.util.spec_from_file_location(name, os.environ[env])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load("make_patches", "MAKE_PATCHES")
driver = load("build_node", "BUILD_NODE")

# The two shapes of the certificate loops, copied from the real sources.
NODE_24 = ("for (X509* cert : {0}) {{", "cert")
NODE_26 = ("for (const auto& cert : {0}) {{", "cert.get()")


def context(shape):
    loop, value = shape
    return f"""\
static const char* const root_certs[] = {{
#include "node_root_certs.h"  // NOLINT(build/include_order)
}};

static std::vector<X509*> InitializeBundledRootCertificates() {{
  // Read the bundled certificates in node_root_certs.h into
  // bundled_root_certs_vector.
  std::vector<X509*> bundled_root_certs;
  return bundled_root_certs;
}}

// TODO(joyeecheung): keep this comment.
X509_STORE* NewRootCertStore(Environment* env) {{
  if (ssl_openssl_cert_store) {{
    CHECK_EQ(1, X509_STORE_set_default_paths(store));
  }} else {{
    {loop.format("GetBundledRootCertificates()")}
      CHECK_EQ(1, X509_STORE_add_cert(store, {value}));
    }}
    if (use_system_ca) {{
      {loop.format("GetSystemStoreCACertificates()")}
        CHECK_EQ(1, X509_STORE_add_cert(store, {value}));
      }}
    }}
  }}

  return store;
}}

void GetBundledRootCertificates(const FunctionCallbackInfo<Value>& args) {{
  Environment* env = Environment::GetCurrent(args);
  Local<Value> result[arraysize(root_certs)];
  args.GetReturnValue().Set(
      Array::New(env->isolate(), result, arraysize(root_certs)));
}}
"""


class CurrentFamily(unittest.TestCase):
    def check(self, shape):
        loop, value = shape
        result = tool.current_context(context(shape), "fixture")
        self.assertNotIn("root_certs[", result)
        self.assertNotIn("node_root_certs.h\"", result)
        self.assertNotIn("for (", result.split("X509_STORE_set_default_paths")[0])
        self.assertNotIn("GetBundledRootCertificates())", result)
        self.assertNotIn("if (ssl_openssl_cert_store)", result)
        self.assertIn("  CHECK_EQ(1, X509_STORE_set_default_paths(store));\n  if (use_system_ca) {\n", result)
        self.assertIn(
            f"    {loop.format('GetSystemStoreCACertificates()')}\n"
            f"      CHECK_EQ(1, X509_STORE_add_cert(store, {value}));\n"
            "    }\n  }\n\n  return store;",
            result)
        self.assertIn("return {};", result)

    def test_node_24_shape(self):
        self.check(NODE_24)

    def test_node_26_shape(self):
        self.check(NODE_26)

    def test_a_moved_trust_block_stops_the_tool(self):
        moved = context(NODE_24).replace("if (ssl_openssl_cert_store) {", "if (other) {")
        with self.assertRaises(SystemExit):
            tool.current_context(moved, "fixture")

    def test_new_lines_use_this_family(self):
        self.assertEqual(tool.FAMILIES["24"], "current")
        self.assertEqual(tool.FAMILIES["26"], "current")
        self.assertIs(tool.edits_for("24")["src/crypto/crypto_context.cc"], tool.current_context)


class UnknownLine(unittest.TestCase):
    def test_no_patch_file_stays_behind(self):
        with tempfile.TemporaryDirectory() as directory:
            module = os.path.join(directory, "MODULE.bazel")
            with open(module, "w", encoding="utf-8") as stream:
                stream.write('node.version(\n    major = "99",\n    version = "99.0.0",\n)\n')
            sources = os.path.join(directory, "node-v99.0.0", "src")
            os.makedirs(sources)
            with open(os.path.join(sources, "node_root_certs.h"), "w", encoding="utf-8") as stream:
                stream.write("cert\n")
            patches = os.path.join(directory, "patches")
            with self.assertRaises(SystemExit):
                tool.generate(directory, patches, module, None)
            self.assertFalse(os.path.exists(patches))


class ConfigureFlags(unittest.TestCase):
    def flags(self, text):
        with tempfile.TemporaryDirectory() as tree:
            with open(os.path.join(tree, "configure.py"), "w", encoding="utf-8") as stream:
                stream.write(text)
            return driver.configure_flags(tree)

    def test_temporal_is_off_when_the_release_has_the_flag(self):
        # configure looks for cargo on PATH. The build declares no Rust toolchain.
        self.assertIn("--v8-disable-temporal-support", self.flags("'--v8-disable-temporal-support'"))

    def test_older_releases_do_not_get_the_flag(self):
        self.assertNotIn("--v8-disable-temporal-support", self.flags(""))

    def test_corepack_flag_needs_the_option(self):
        # Node 26 has no corepack by default. configure gives an unknown flag to gyp, which fails.
        self.assertNotIn("--without-corepack", self.flags("'--with-corepack'"))
        self.assertIn("--without-corepack", self.flags("'--without-corepack'"))


class LibtoolWrapper(unittest.TestCase):
    def arguments(self, *args):
        with tempfile.TemporaryDirectory() as directory:
            fake = os.path.join(directory, "fake")
            driver.write_tool(directory, "fake", '#!/bin/sh\nfor a in "$@"; do echo "$a"; done\n')
            driver.write_tool(directory, "libtool", driver.LIBTOOL_WRAPPER.format(libtool=fake))
            output = subprocess.check_output([os.path.join(directory, "libtool"), *args], text=True)
            return output.splitlines()

    def test_framework_flags_do_not_reach_the_tool(self):
        # abseil.gyp in Node 24 and 26 gives -framework to libtool. llvm-libtool-darwin stops on it.
        self.assertEqual(
            self.arguments("-framework", "CoreFoundation", "-static", "-o", "a.a", "b.o"),
            ["-static", "-o", "a.a", "b.o"])

    def test_a_flag_as_one_argument_does_not_reach_the_tool(self):
        self.assertEqual(self.arguments("-framework CoreFoundation", "-static"), ["-static"])

    def test_other_arguments_keep_their_order_and_spaces(self):
        self.assertEqual(self.arguments("-o", "a b.a", "c.o", "d.o"), ["-o", "a b.a", "c.o", "d.o"])


if __name__ == "__main__":
    unittest.main()
