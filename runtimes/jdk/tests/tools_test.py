"""Tests that the JDK driver takes each build tool from a pin, or from a host row with a reason."""

import io
import os
import re
import shutil
import stat
import tarfile
import tempfile
import unittest

import build_jdk

# Each of these names comes from a pinned archive. A host row must not name one.
PINNED = {
    "make", "m4", "autoconf", "autom4te", "perl", "bash",
    "cc", "c++", "ar", "ranlib", "nm", "strip", "lipo", "otool", "install_name_tool", "dsymutil",
    "c++filt", "objdump",
    "metal", "metallib",
}
HOST_DIRS = ["/usr/bin", "/bin", "/usr/sbin", "/sbin", "/usr/local/bin", "/opt/homebrew/bin"]


class Temp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def touch(self, path, mode=0o755):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as stream:
            stream.write("#!/bin/sh\n")
        os.chmod(path, mode)


class HostRows(unittest.TestCase):
    def test_each_row_has_a_reason(self):
        for name, reason in build_jdk.HOST_TOOLS.items():
            self.assertTrue(reason.strip(), name)

    def test_path_holds_each_tool_that_configure_requires_on_macos(self):
        # The UTIL_REQUIRE_PROGS names in make/autoconf/*.m4 of jdk-25.0.5-ga, for macOS.
        # configure stops when one of these is not on PATH.
        required = [
            "awk", "bash", "cat", "chmod", "cp", "cut", "date", "diff", "dsymutil", "echo", "expr",
            "find", "gunzip", "gzip", "head", "install_name_tool", "ln", "ls", "mig", "mkdir",
            "mktemp", "mv", "otool", "printf", "rm", "rmdir", "SetFile", "sh", "sort", "tail",
            "tar", "tee", "touch", "tr", "ulimit", "uname", "unzip", "wc", "xargs", "xattr", "zip",
            "c++filt", "grep", "egrep", "sed", "file",
        ]
        on_path = set(build_jdk.HOST_TOOLS) | set(build_jdk.LLVM_WRAPPERS) | set(build_jdk.TOOL_ORDER)
        self.assertEqual([n for n in required if n not in on_path], [])

    def test_no_row_names_a_pinned_tool(self):
        self.assertEqual(set(build_jdk.HOST_TOOLS) & PINNED, set())


class HostBin(Temp):
    def setUp(self):
        super().setUp()
        for name in ["a", "b", "c"]:
            self.touch(self.path("first", name))
        self.touch(self.path("second", "a"))
        self.touch(self.path("second", "d"))
        self.search = [self.path("first"), self.path("second")]

    def test_links_only_the_listed_tools(self):
        host = build_jdk.link_host_tools(self.path("host"), {"a": "why", "d": "why"}, self.search)
        self.assertEqual(sorted(os.listdir(host)), ["a", "d"])
        self.assertEqual(os.readlink(os.path.join(host, "d")), self.path("second", "d"))

    def test_first_directory_wins(self):
        host = build_jdk.link_host_tools(self.path("host"), {"a": "why"}, self.search)
        self.assertEqual(os.readlink(os.path.join(host, "a")), self.path("first", "a"))

    def test_a_missing_tool_stops_the_build(self):
        with self.assertRaises(SystemExit):
            build_jdk.link_host_tools(self.path("host"), {"absent": "why"}, self.search)


class Path(unittest.TestCase):
    def setUp(self):
        self.env = build_jdk.build_env("/wrap", "/tools", "/host", "/sdk", "/scratch")

    def test_path_is_the_wrappers_then_the_pins_then_the_host_rows(self):
        self.assertEqual(self.env["PATH"], "/wrap:/tools:/host")

    def test_path_has_no_host_directory(self):
        for part in self.env["PATH"].split(":"):
            self.assertNotIn(part, HOST_DIRS)


class Wrappers(Temp):
    def llvm(self, names):
        for name in names:
            self.touch(self.path("llvm", "bin", name))
        return self.path("llvm")

    def test_mach_o_tools_come_from_the_pinned_llvm(self):
        llvm = self.llvm(build_jdk.LLVM_WRAPPERS.values())
        wrap = build_jdk.write_wrappers(self.path("scratch"), llvm)
        for name, target in [
            ("lipo", "llvm-lipo"),
            ("otool", "llvm-otool"),
            ("install_name_tool", "llvm-install-name-tool"),
            ("dsymutil", "dsymutil"),
            ("c++filt", "llvm-cxxfilt"),
            ("objdump", "llvm-objdump"),
            ("ld", "ld64.lld"),
        ]:
            with open(os.path.join(wrap, name)) as stream:
                self.assertIn(f'exec "{llvm}/bin/{target}" "$@"', stream.read(), name)

    def test_a_missing_llvm_tool_stops_the_build(self):
        llvm = self.llvm([t for t in build_jdk.LLVM_WRAPPERS.values() if t != "llvm-lipo"])
        with self.assertRaises(SystemExit):
            build_jdk.write_wrappers(self.path("scratch"), llvm)


class ActionInputs(unittest.TestCase):
    """The JDK action sees only the LLVM files that a filegroup lists."""

    def test_each_wrapper_target_is_an_action_input(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "..", "..", "toolchain", "llvm.BUILD")) as stream:
            build = stream.read()
        groups = {}
        for block in build.split("filegroup(")[1:]:
            name = re.search(r'name = "(\w+)"', block).group(1)
            groups[name] = block[:block.index("\n)")]
        inputs = groups["toolchain"] + groups["macho_tools"]
        for target in set(build_jdk.LLVM_WRAPPERS.values()):
            self.assertIn(f'"bin/{target}"', inputs, target)


class Unpack(Temp):
    def archive(self, members, mode="w:gz", name="src.tar.gz"):
        path = self.path(name)
        with tarfile.open(path, mode) as tar:
            for info, data in members:
                tar.addfile(info, io.BytesIO(data) if data is not None else None)
        return path

    def file(self, name, data=b"x", mode=0o644):
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = mode
        return info, data

    def test_strips_the_top_directory(self):
        archive = self.archive([self.file("top/a/b.txt", b"hello")])
        build_jdk.unpack(archive, self.path("tree"))
        with open(self.path("tree", "a", "b.txt"), "rb") as stream:
            self.assertEqual(stream.read(), b"hello")

    def test_keeps_the_exec_bit(self):
        archive = self.archive([self.file("top/configure", b"#!/bin/sh\n", 0o755)])
        build_jdk.unpack(archive, self.path("tree"))
        self.assertTrue(os.stat(self.path("tree", "configure")).st_mode & stat.S_IXUSR)

    def test_reads_xz(self):
        archive = self.archive([self.file("top/m4.c")], mode="w:xz", name="src.tar.xz")
        build_jdk.unpack(archive, self.path("tree"))
        self.assertTrue(os.path.exists(self.path("tree", "m4.c")))

    def test_keeps_a_relative_symlink(self):
        link = tarfile.TarInfo("top/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "a.txt"
        archive = self.archive([self.file("top/a.txt"), (link, None)])
        build_jdk.unpack(archive, self.path("tree"))
        self.assertEqual(os.readlink(self.path("tree", "link")), "a.txt")

    def test_refuses_a_member_outside_the_tree(self):
        archive = self.archive([self.file("top/../../evil")])
        with self.assertRaises(SystemExit):
            build_jdk.unpack(archive, self.path("tree"))
        self.assertFalse(os.path.exists(self.path("evil")))

    def test_refuses_a_symlink_outside_the_tree(self):
        link = tarfile.TarInfo("top/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        archive = self.archive([(link, None)])
        with self.assertRaises(SystemExit):
            build_jdk.unpack(archive, self.path("tree"))


class ToolOrder(unittest.TestCase):
    def test_make_comes_first(self):
        self.assertEqual(build_jdk.TOOL_ORDER[0], "make")

    def test_autoconf_records_the_pinned_perl(self):
        # autoconf writes the perl path into autom4te when its configure runs.
        self.assertLess(build_jdk.TOOL_ORDER.index("perl"), build_jdk.TOOL_ORDER.index("autoconf"))

    def test_each_pinned_source_is_built(self):
        self.assertEqual(sorted(build_jdk.TOOL_ORDER), ["autoconf", "bash", "m4", "make", "perl"])


class Perl(unittest.TestCase):
    def setUp(self):
        self.argv = build_jdk.perl_configure("/tools", "/sdk")

    def test_runs_with_no_questions(self):
        self.assertIn("-des", self.argv)

    def test_installs_into_the_tool_prefix(self):
        self.assertIn("-Dprefix=/tools", self.argv)

    def test_compiles_against_the_pinned_sysroot(self):
        self.assertIn("-Dcc=cc", self.argv)
        self.assertIn("-Accflags=-isysroot /sdk", self.argv)
        self.assertIn("-Aldflags=-isysroot /sdk -fuse-ld=lld", self.argv)


class Leaks(unittest.TestCase):
    SDK = "/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk"

    def leaks(self, spec, allowed=frozenset()):
        return build_jdk.host_tool_leaks(spec, allowed, self.SDK)

    def test_finds_a_tool_variable_on_a_host_path(self):
        spec = "BASH := /bin/bash\nMAKE := /s/tools/bin/make\nLIPO := /usr/bin/lipo\n"
        self.assertEqual(self.leaks(spec), ["BASH := /bin/bash", "LIPO := /usr/bin/lipo"])

    def test_a_host_row_is_not_a_leak(self):
        self.assertEqual(self.leaks("CODESIGN := /usr/bin/codesign\n", {"codesign"}), [])

    def test_ignores_a_path_in_a_flag(self):
        self.assertEqual(self.leaks("CFLAGS := -I/usr/include\n"), [])

    def test_the_recorded_sysroot_is_not_a_leak(self):
        self.assertEqual(self.leaks(f"SYSROOT := {self.SDK}\n"), [])

    def test_a_tool_next_to_the_sysroot_is_a_leak(self):
        spec = "XCRUN := /Library/Developer/CommandLineTools/usr/bin/xcrun\n"
        self.assertEqual(self.leaks(spec), [spec.strip()])


if __name__ == "__main__":
    unittest.main()
