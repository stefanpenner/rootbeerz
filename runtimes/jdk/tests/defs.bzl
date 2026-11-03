"""Test suites for one JDK line."""

load("@rules_python//python:py_test.bzl", "py_test")

def jdk_tests(line):
    """Defines the hermetic, teeth, and network tests for one JDK line.

    Args:
      line: The feature release, for example "25".
    """
    ours = "//runtimes/jdk:jdk_%s" % line
    stock_files = "//runtimes/jdk:stock_%s_files" % line
    stock_java = "//runtimes/jdk:stock_%s_java" % line
    data = [
        ours,
        stock_files,
        stock_java,
        "Probe.java",
        "//trust/testing:fixtures/emsign-roots.pem",
        "//trust/testing:fixtures/testonly-ca.pem",
        "//trust/testing:fixtures/testonly-leaf.pem",
        "//trust/testing:fixtures/testonly-leaf.key",
        "//trust:mozilla-no-emsign.pem",
    ]
    env = {
        "ROOTBEERZ_JDK": "$(rootpath %s)" % ours,
        "STOCK_JDK": "$(rootpath %s)" % stock_java,
        "PROBE_JAVA": "$(rootpath Probe.java)",
        "EMSIGN_STORE": "$(rootpath //trust/testing:fixtures/emsign-roots.pem)",
        "PINNED_STORE": "$(rootpath //trust:mozilla-no-emsign.pem)",
        "TEST_CA": "$(rootpath //trust/testing:fixtures/testonly-ca.pem)",
        "TEST_LEAF": "$(rootpath //trust/testing:fixtures/testonly-leaf.pem)",
        "TEST_LEAF_KEY": "$(rootpath //trust/testing:fixtures/testonly-leaf.key)",
    }

    py_test(
        name = "hermetic_%s_test" % line,
        srcs = ["hermetic_test.py"],
        main = "hermetic_test.py",
        deps = [":jdklib"],
        data = data,
        env = env,
        # macOS returns EPERM for a Java connect to loopback when the network is blocked.
        tags = ["requires-network"],
        size = "medium",
    )

    py_test(
        name = "teeth_%s_test" % line,
        srcs = ["teeth_test.py", "hermetic_test.py"],
        main = "teeth_test.py",
        deps = [":jdklib", "//trust/testing:trustlib"],
        data = data,
        env = env,
        # Same loopback connect as hermetic. macOS denies it when the network is blocked.
        tags = ["requires-network"],
        size = "medium",
    )

    py_test(
        name = "network_%s_test" % line,
        srcs = ["network_test.py"],
        main = "network_test.py",
        deps = [":jdklib"],
        data = data,
        env = env,
        size = "large",
        tags = ["requires-network", "no-remote", "no-cache", "external"],
    )

    py_test(
        name = "system_ca_%s_test" % line,
        srcs = ["system_ca_test.py"],
        main = "system_ca_test.py",
        deps = [":jdklib"],
        data = [ours, "Probe.java"],
        env = {
            "ROOTBEERZ_JDK": "$(rootpath %s)" % ours,
            "PROBE_JAVA": "$(rootpath Probe.java)",
        },
        size = "large",
        tags = ["requires-network", "no-remote", "no-cache", "external"],
        target_compatible_with = ["@platforms//os:macos"],
    )
