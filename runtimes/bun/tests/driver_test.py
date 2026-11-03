"""Tests the host-leak guard of the Bun build driver. It needs no Bun build."""

import importlib.util
import os
import pathlib
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("build_bun", os.environ["BUILD_BUN"])
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


class CargoConfigGuard(unittest.TestCase):
    def test_finds_a_config_in_a_parent_directory(self):
        with tempfile.TemporaryDirectory() as root:
            top = pathlib.Path(root).resolve()
            (top / ".cargo").mkdir()
            (top / ".cargo/config.toml").write_text("[build]\nrustc-wrapper = 'x'\n")
            self.assertEqual(DRIVER.parent_cargo_configs(top / "a/b"), [top / ".cargo/config.toml"])

    def test_finds_the_legacy_name(self):
        with tempfile.TemporaryDirectory() as root:
            top = pathlib.Path(root).resolve()
            (top / ".cargo").mkdir()
            (top / ".cargo/config").write_text("")
            self.assertEqual(DRIVER.parent_cargo_configs(top / "a"), [top / ".cargo/config"])

    def test_clean_tree_has_no_config(self):
        with tempfile.TemporaryDirectory() as root:
            top = pathlib.Path(root).resolve()
            self.assertEqual([p for p in DRIVER.parent_cargo_configs(top / "a") if top in p.parents], [])


class NpmCacheFolder(unittest.TestCase):
    def test_release_version(self):
        self.assertEqual(DRIVER.npm_cache_folder("@types/node", "26.2.0"), "@types/node@26.2.0@@@1")

    def test_recorded_prerelease(self):
        self.assertEqual(
            DRIVER.npm_cache_folder("querystring-es3", "1.0.0-0"), "querystring-es3@1.0.0-b8f8325b21a8a1e5@@@1"
        )

    def test_unknown_prerelease_is_refused(self):
        with self.assertRaises(SystemExit):
            DRIVER.npm_cache_folder("other", "1.0.0-beta")


class SdkPath(unittest.TestCase):
    """The build uses the SDK that @macos_sdk selects, not the xcrun default."""

    def test_absolute_path(self):
        with tempfile.TemporaryDirectory() as root:
            info = pathlib.Path(root) / "info.json"
            info.write_text('{"path": "/sdks/MacOSX26.5.sdk"}')
            self.assertEqual(DRIVER.sdk_path(info), pathlib.Path("/sdks/MacOSX26.5.sdk"))

    def test_relative_path_is_next_to_the_info_file(self):
        with tempfile.TemporaryDirectory() as root:
            info = pathlib.Path(root).resolve() / "info.json"
            info.write_text('{"path": "sdk"}')
            self.assertEqual(DRIVER.sdk_path(info), info.parent / "sdk")

    def test_driver_does_not_ask_xcrun(self):
        self.assertNotIn("xcrun", pathlib.Path(os.environ["BUILD_BUN"]).read_text())

    def test_input_files_is_the_sibling_files_dir(self):
        with tempfile.TemporaryDirectory() as root:
            top = pathlib.Path(root).resolve()
            (top / "files").mkdir()
            info = top / "inputs_1.json"
            info.write_text("{}")
            self.assertEqual(DRIVER.input_files(info), top / "files")

    def test_ninja_tag_is_the_release_segment(self):
        inputs = {"archives": [{"name": "ninja", "url": "https://example/v1.12.1/ninja.tar.gz"}]}
        self.assertEqual(DRIVER.ninja_tag(inputs), "v1.12.1")

    def test_build_env_puts_the_tools_dir_first(self):
        env = DRIVER.build_env(pathlib.Path("/scratch"), pathlib.Path("/tools"), pathlib.Path("/cache"), "/sdk")
        self.assertTrue(env["PATH"].startswith("/tools" + os.pathsep), env["PATH"])
        self.assertEqual(env["SDKROOT"], "/sdk")
        self.assertEqual(env["BUN_TOOLCHAIN_LLVM"], "/scratch/llvm")
        self.assertEqual(env["SOURCE_DATE_EPOCH"], "0")


class LdShim(unittest.TestCase):
    """Cargo puts pinned library folders in DYLD_* paths. The host ld must not load the pinned libc++."""

    def run_shim(self, env):
        with tempfile.TemporaryDirectory() as root:
            top = pathlib.Path(root)
            host_ld = top / "host-ld"
            host_ld.write_text('#!/bin/sh\necho "host $@ [${DYLD_LIBRARY_PATH}${DYLD_FALLBACK_LIBRARY_PATH}${KEEP}]"\n')
            host_ld.chmod(0o755)
            DRIVER.write_ld_shim(top, host_ld)
            return subprocess.run([top / "ld", "-ld_new"], env=env, check=True, capture_output=True, text=True).stdout

    def test_ld_runs_the_host_ld_with_the_same_arguments(self):
        self.assertEqual(self.run_shim({"KEEP": "k"}), "host -ld_new [k]\n")

    def test_ld_drops_the_dyld_library_paths(self):
        env = {"DYLD_LIBRARY_PATH": "/pinned/llvm/lib", "DYLD_FALLBACK_LIBRARY_PATH": "/pinned/llvm/lib", "KEEP": "k"}
        self.assertEqual(self.run_shim(env), "host -ld_new [k]\n")

    def test_clang_link_uses_the_ld_shim(self):
        # llvm/bin/clang++ is an absolute path. A PATH entry named ld does not run.
        with tempfile.TemporaryDirectory() as root:
            top = pathlib.Path(root)
            llvm = top / "llvm-bin"
            llvm.mkdir()
            for name in ("clang", "clang++"):
                compiler = llvm / name
                compiler.write_text('#!/bin/sh\necho "$@"\n')
                compiler.chmod(0o755)
            host = top / "host-ld"
            host.write_text("#!/bin/sh\necho host\n")
            host.chmod(0o755)
            DRIVER.write_ld_shim(top, host)
            DRIVER.wrap_clang_linkers(llvm, top / "ld")
            DRIVER.wrap_clang_linkers(llvm, top / "ld")
            link = subprocess.run(
                [llvm / "clang++", "a.o", "-o", "a"], check=True, capture_output=True, text=True).stdout
            compiled = subprocess.run(
                [llvm / "clang++", "-c", "a.c"], check=True, capture_output=True, text=True).stdout
        self.assertEqual(link.split(), [f"--ld-path={top / 'ld'}", "a.o", "-o", "a"])
        self.assertEqual(compiled.split(), ["-c", "a.c"])

    def test_clang_symlink_keeps_one_real_compiler(self):
        with tempfile.TemporaryDirectory() as root:
            top = pathlib.Path(root)
            llvm = top / "llvm-bin"
            llvm.mkdir()
            compiler = llvm / "clang"
            compiler.write_text('#!/bin/sh\necho "$@"\n')
            compiler.chmod(0o755)
            (llvm / "clang++").symlink_to("clang")
            host = top / "host-ld"
            host.write_text("#!/bin/sh\necho host\n")
            host.chmod(0o755)
            DRIVER.write_ld_shim(top, host)
            DRIVER.wrap_clang_linkers(llvm, top / "ld")
            link = subprocess.run(
                [llvm / "clang++", "a.o", "-o", "a"], check=True, capture_output=True, text=True).stdout
        self.assertEqual(link.split(), [f"--ld-path={top / 'ld'}", "a.o", "-o", "a"])

    def test_missing_host_ld_stops_the_build(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(SystemExit):
                DRIVER.write_ld_shim(pathlib.Path(root), pathlib.Path(root) / "no-ld")


if __name__ == "__main__":
    unittest.main()
