"""Tests for the plain logic of the JDK build driver. The build itself runs in //runtimes/jdk:jdk_25."""

import os
import shutil
import struct
import subprocess
import tempfile
import unittest

import build_jdk


# Bytes from the JDK 25.0.5 build. keytool reads this file as a PKCS12 store with 0 entries.
EMPTY_PKCS12 = bytes.fromhex("30160201033011" "06092a864886f70d010701" "a00404023000")


class Cacerts(unittest.TestCase):
    def test_empty_keystore_passes(self):
        build_jdk.require_empty_cacerts(EMPTY_PKCS12)

    def test_keystore_with_more_bytes_fails(self):
        with self.assertRaises(SystemExit):
            build_jdk.require_empty_cacerts(EMPTY_PKCS12 + b"\0")

    def test_empty_file_fails(self):
        with self.assertRaises(SystemExit):
            build_jdk.require_empty_cacerts(b"")

    def test_file_of_another_type_fails(self):
        with self.assertRaises(SystemExit):
            build_jdk.require_empty_cacerts(struct.pack(">III", 0xFEEDFEED, 2, 0))


class Configure(unittest.TestCase):
    def setUp(self):
        self.flags = build_jdk.configure_flags(
            boot_jdk="/boot",
            sysroot="/sdk",
            empty_cacerts="/scratch/empty",
            jobs=6,
            version="25.0.5+8",
            scratch="/scratch",
            llvm="/llvm",
        )

    def test_selects_the_empty_cacerts_source(self):
        self.assertIn("--with-cacerts-src=/scratch/empty", self.flags)

    def test_job_limit(self):
        self.assertIn("--with-jobs=6", self.flags)

    def test_selects_pinned_boot_jdk_and_sysroot(self):
        self.assertIn("--with-boot-jdk=/boot", self.flags)
        self.assertIn("--with-sysroot=/sdk", self.flags)

    def test_version_comes_from_the_manifest_value(self):
        self.assertIn("--with-version-build=8", self.flags)
        self.assertIn("--with-version-pre=", self.flags)


class AutoconfShim(unittest.TestCase):
    def test_calls_autom4te_with_no_autoconf_exec(self):
        text = build_jdk.autoconf_shim("/tools/bin")
        self.assertIn('exec "/tools/bin/autom4te" --language=autoconf --output=- "$@"', text)

    def test_version_comes_from_the_pinned_autoconf(self):
        text = build_jdk.autoconf_shim("/tools/bin")
        self.assertIn('--version) exec "/tools/bin/autoconf" --version', text)


class MetalShim(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.boot = os.path.join(self.dir, "boot")
        os.makedirs(os.path.join(self.boot, "lib"))
        with open(os.path.join(self.boot, "lib", "shaders.metallib"), "wb") as stream:
            stream.write(b"pinned shaders")
        build_jdk.write_metal_shims(self.dir, self.boot)

    def run_shim(self, *args):
        return subprocess.run([os.path.join(self.dir, args[0]), *args[1:]], check=True, capture_output=True)

    def test_version_exits_with_zero(self):
        self.run_shim("metal", "--version")
        self.run_shim("metallib", "--version")

    def test_metal_writes_a_placeholder(self):
        out = os.path.join(self.dir, "a.air")
        self.run_shim("metal", "-c", "-std=osx-metal2.0", "-o", out, "a.metal")
        self.assertTrue(os.path.exists(out))

    def test_metallib_copies_the_pinned_library(self):
        out = os.path.join(self.dir, "shaders.metallib")
        self.run_shim("metallib", "-o", out, "a.air")
        with open(out, "rb") as stream:
            self.assertEqual(stream.read(), b"pinned shaders")


if __name__ == "__main__":
    unittest.main()
