"""Checks the content of the built Linux image. It needs Docker and a built image.

Run it with:
  bazel test //runtimes/node/tests:image_test --test_env=IMAGE=rootbeerz-linux --test_env=DOCKER_HOST
"""

import hashlib
import os
import subprocess
import unittest

IMAGE = os.environ["IMAGE"]
TRUST = os.environ["TRUST_BUNDLE"]
MAX_UNPACKED_BYTES = 150 * 1000 * 1000


def run(*command, user=None):
    args = ["docker", "run", "--rm", "--platform", "linux/amd64", "--entrypoint", command[0]]
    if user:
        args += ["--user", user]
    result = subprocess.run(args + [IMAGE] + list(command[1:]), capture_output=True, text=True)
    return result


def sh(script):
    return run("/bin/sh", "-c", script)


class ImageContent(unittest.TestCase):
    def test_bazel_starts(self):
        result = run("bazel", "--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "bazel 9.1.1")

    def test_no_package_manager(self):
        script = "for p in /var/lib/dpkg /usr/bin/apt /usr/bin/apt-get /etc/apt; do [ -e $p ] && echo $p; done; true"
        self.assertEqual(sh(script).stdout.strip(), "")

    def test_no_compiler(self):
        result = sh("command -v gcc cc clang c++ make || echo none")
        self.assertEqual(result.stdout.strip(), "none")

    def test_sh_is_not_busybox(self):
        # BusyBox sh runs its own applets before it looks at PATH.
        result = sh("readlink /bin/sh")
        self.assertEqual(result.stdout.strip(), "dash")

    def test_a_wrapper_in_path_beats_a_busybox_applet(self):
        script = (
            "mkdir -p /tmp/wrap && printf '#!/bin/sh\\necho WRAPPER\\n' > /tmp/wrap/ar "
            "&& chmod +x /tmp/wrap/ar && PATH=/tmp/wrap:$PATH sh -c ar"
        )
        self.assertEqual(sh(script).stdout.strip(), "WRAPPER")

    def test_posix_tools_exist(self):
        for tool in ["sed", "cp", "mkdir", "env", "cat", "rm", "ln", "find", "awk", "tar", "bash"]:
            self.assertEqual(sh(f"command -v {tool}").returncode, 0, tool)

    def test_bash_runs_bazel_test_scripts(self):
        # Bazel test scripts use `#!/usr/bin/env bash` and `set -o pipefail`.
        script = (
            "printf '#!/usr/bin/env bash\\nset -euo pipefail\\necho \\\"${BASH_VERSION%%%%.*}\\\"\\n' "
            "> /tmp/t.sh && chmod +x /tmp/t.sh && /tmp/t.sh"
        )
        result = sh(script)
        self.assertEqual(result.stdout.strip().strip('"'), "5", result.stderr)

    def test_runtime_libraries_load(self):
        # Each library that the LLVM tools need must be present with a SONAME link.
        for lib in ["libc.so.6", "libm.so.6", "libgcc_s.so.1", "libstdc++.so.6", "libz.so.1",
                    "libzstd.so.1", "libxml2.so.2", "liblzma.so.5", "libicuuc.so.72"]:
            result = sh(f"ls /lib/x86_64-linux-gnu/{lib} /usr/lib/x86_64-linux-gnu/{lib} 2>/dev/null")
            self.assertTrue(result.stdout.strip(), f"{lib} is missing")

    def test_ca_bundle_is_the_pinned_trust_file(self):
        with open(TRUST, "rb") as stream:
            expected = hashlib.sha256(stream.read()).hexdigest()
        for path in ["/etc/ssl/certs/ca-certificates.crt", "/etc/ssl/cert.pem"]:
            result = sh(f"sha256sum {path}")
            self.assertEqual(result.stdout.split()[0], expected, path)

    def test_runs_as_builder(self):
        self.assertEqual(sh("id -u").stdout.strip(), "1000")

    def test_image_is_small(self):
        result = subprocess.run(
            ["docker", "history", "--no-trunc", "--format", "{{.Size}}", IMAGE],
            capture_output=True, text=True, check=True)
        units = {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3}
        total = 0
        for line in result.stdout.split():
            number = line.rstrip("kMGB")
            unit = line[len(number):]
            total += float(number) * units[unit]
        self.assertLess(total, MAX_UNPACKED_BYTES)


if __name__ == "__main__":
    unittest.main()
