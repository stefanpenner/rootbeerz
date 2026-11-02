"""Helpers for the mutation tests. A test that cannot fail proves nothing."""

import re
import unittest


def failed_tests(*cases):
    """Runs the cases, and returns the method names of the tests that did not pass."""
    suite = unittest.TestSuite(
        unittest.defaultTestLoader.loadTestsFromTestCase(case) for case in cases)
    result = unittest.TestResult()
    suite.run(result)
    return {test.id().rsplit(".", 1)[-1] for test, _ in result.failures + result.errors}


def reverse_patch(text, diff):
    """Returns the text before one file's unified diff. A context mismatch raises ValueError."""
    lines = text.split("\n")
    hunks = re.split(r"(?m)^(?=@@ )", diff)[1:]
    for hunk in reversed(hunks):
        header, *body = hunk.split("\n")
        match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", header)
        start, count = int(match.group(1)), int(match.group(2) or 1)
        body = [line for line in body if line and not line.startswith("\\")]
        new = [line[1:] for line in body if line[0] in " +"]
        old = [line[1:] for line in body if line[0] in " -"]
        first = start - 1 if count else start
        if lines[first:first + count] != new:
            raise ValueError(f"the patch does not match the source at line {start}")
        lines[first:first + count] = old
    return "\n".join(lines)


def split_diff(patch):
    """Returns {path: diff} for each file in a git patch."""
    parts = re.split(r"(?m)^diff --git a/(\S+) b/\S+\n", patch)
    return dict(zip(parts[1::2], parts[2::2]))
