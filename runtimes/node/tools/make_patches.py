#!/usr/bin/env python3
"""Regenerates patches/ from verified Node source trees.

Usage: make_patches.py <directory that holds node-vX.Y.Z trees> <patches directory> [MODULE.bazel]
       make_patches.py --only <major> <sources> <patches> [MODULE.bazel]
       make_patches.py --list [MODULE.bazel]

The versions come from the node.version tags in MODULE.bazel. A new line needs a family
below. A new release of an existing line needs nothing here.

Every replacement must match exactly once. A new Node release that moves the
trust code stops this tool instead of producing a partial patch.
"""

import difflib
import os
import re
import sys

# The shape of the trust code in crypto_context.cc.
FAMILIES = {
    "16": "legacy",
    "18": "legacy",
    "20": "legacy",
    "22": "modern",
    "24": "current",
    "26": "current",
}
DEFAULT_MODULE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "MODULE.bazel")


def read_versions(module_bazel):
    """Returns {major: (version, old_v8)} from the node.version tags, in file order."""
    with open(module_bazel, encoding="utf-8") as stream:
        text = stream.read()
    versions = {}
    for tag in re.findall(r"^node\.version\(\n(.*?)^\)", text, re.MULTILINE | re.DOTALL):
        fields = dict(re.findall(r'^    (\w+) = "([^"]*)",$', tag, re.MULTILINE))
        old_v8 = re.search(r"^    old_v8 = True,$", tag, re.MULTILINE) is not None
        versions[fields["major"]] = (fields["version"], old_v8)
    if not versions:
        raise SystemExit(f"no node.version tags in {module_bazel}")
    return versions

EMPTY_HEADER = """\
// rootbeer: This file holds no certificates. Trust comes from the local store.
"""

STORE_COMMENT = """\
  // rootbeer: The binary holds no roots. The OpenSSL default paths are the only
  // default trust source. They honor SSL_CERT_FILE and SSL_CERT_DIR. No flag
  // changes this choice.
"""


CHAR_TRAITS_PATH = "deps/v8/src/inspector/uint16-char-traits.h"
CHAR_TRAITS_USERS = (
    "deps/v8/src/inspector/string-16.h",
    "deps/v8/src/inspector/v8-string-conversions.h",
)

# libc++ 19 removed the generic char_traits. The V8 in Node 16 and 18 needs it for uint16_t.
CHAR_TRAITS_HEADER = """\
// rootbeer: libc++ 19 has no generic std::char_traits. This file restores the
// behavior of that template for the uint16_t strings in this V8 version.
#ifndef V8_INSPECTOR_UINT16_CHAR_TRAITS_H_
#define V8_INSPECTOR_UINT16_CHAR_TRAITS_H_

#include <stdint.h>

#include <algorithm>
#include <cstddef>
#include <cstring>
#include <cwchar>
#include <ios>
#include <string>

namespace std {

template <>
struct char_traits<uint16_t> {
  using char_type = uint16_t;
  using int_type = uint32_t;
  using off_type = streamoff;
  using pos_type = streampos;
  using state_type = mbstate_t;

  static constexpr void assign(char_type& to, const char_type& from) noexcept { to = from; }
  static constexpr bool eq(char_type a, char_type b) noexcept { return a == b; }
  static constexpr bool lt(char_type a, char_type b) noexcept { return a < b; }

  static constexpr int compare(const char_type* a, const char_type* b, size_t count) {
    for (size_t i = 0; i < count; ++i) {
      if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1;
    }
    return 0;
  }

  static constexpr size_t length(const char_type* text) {
    size_t count = 0;
    while (text[count] != 0) ++count;
    return count;
  }

  static constexpr const char_type* find(const char_type* text, size_t count,
                                         const char_type& value) {
    for (size_t i = 0; i < count; ++i) {
      if (text[i] == value) return text + i;
    }
    return nullptr;
  }

  static char_type* move(char_type* to, const char_type* from, size_t count) {
    if (count != 0) memmove(to, from, count * sizeof(char_type));
    return to;
  }

  static char_type* copy(char_type* to, const char_type* from, size_t count) {
    if (count != 0) memcpy(to, from, count * sizeof(char_type));
    return to;
  }

  static char_type* assign(char_type* to, size_t count, char_type value) {
    fill_n(to, count, value);
    return to;
  }

  static constexpr int_type not_eof(int_type value) noexcept {
    return eq_int_type(value, eof()) ? ~eof() : value;
  }
  static constexpr char_type to_char_type(int_type value) noexcept {
    return static_cast<char_type>(value);
  }
  static constexpr int_type to_int_type(char_type value) noexcept { return value; }
  static constexpr bool eq_int_type(int_type a, int_type b) noexcept { return a == b; }
  static constexpr int_type eof() noexcept { return 0xFFFFFFFFu; }
};

}  // namespace std

#endif  // V8_INSPECTOR_UINT16_CHAR_TRAITS_H_
"""

# Apple SDK headers define TARGET_OS_MAC, and an old zlib then treats macOS as
# classic Mac OS. Later zlib versions test MACOS only.
OLD_ZLIB_HEADERS = {
    "18": ("deps/v8/third_party/zlib/zutil.h",),
    "16": ("deps/v8/third_party/zlib/zutil.h", "deps/zlib/zutil.h"),
}


def substitute(pattern, replacement, text, name):
    # A callable replacement gets the match. A string replacement is literal text.
    replace = replacement if callable(replacement) else lambda _: replacement
    result, count = re.subn(pattern, replace, text, flags=re.S)
    if count != 1:
        raise SystemExit(f"{name}: expected one match, found {count}: {pattern}")
    return result


def remove_literal(literal, text, name):
    return substitute(re.escape(literal), "", text, name)


def legacy_context(text, name):
    """Node 16, 18, and 20 share one shape of the trust code."""
    text = substitute(
        r"static const char\* const root_certs\[\] = \{\n"
        r'#include "node_root_certs.h"  // NOLINT\(build/include_order\)\n'
        r"\};\n\n",
        "", text, name)
    text = substitute(
        r"  static std::vector<X509\*> root_certs_vector;.*?"
        r"(  X509_STORE\* store = X509_STORE_new\(\);)",
        "  X509_STORE* store = X509_STORE_new();", text, name)
    text = substitute(
        r"  if \(per_process::cli_options->ssl_openssl_cert_store\) \{\n"
        r"    X509_STORE_set_default_paths\(store\);\n"
        r"  \} else \{.*?\n  \}\n\n  return store;",
        STORE_COMMENT + "  X509_STORE_set_default_paths(store);\n\n  return store;",
        text, name)
    text = substitute(
        r"  Local<Value> result\[arraysize\(root_certs\)\];.*?"
        r"Array::New\(env->isolate\(\), result, arraysize\(root_certs\)\)\);",
        "  args.GetReturnValue().Set(Array::New(env->isolate()));", text, name)
    return text


def modern_context(text, name):
    """Node 22 loads bundled roots through a cached vector."""
    text = substitute(
        r"static const char\* const root_certs\[\] = \{\n"
        r'#include "node_root_certs.h"  // NOLINT\(build/include_order\)\n'
        r"\};\n\n",
        "", text, name)
    text = substitute(
        r"  // Read the bundled certificates in node_root_certs.h into\n.*?"
        r"  return bundled_root_certs;\n\}\n\n// TODO\(joyeecheung\)",
        "  // rootbeer: The binary holds no roots.\n  return {};\n}\n\n// TODO(joyeecheung)",
        text, name)
    text = substitute(
        r"  if \(per_process::cli_options->ssl_openssl_cert_store\) \{\n"
        r"    CHECK_EQ\(1, X509_STORE_set_default_paths\(store\)\);\n"
        r"  \} else \{\n"
        r"    for \(X509\* cert : GetBundledRootCertificates\(\)\) \{\n"
        r"      CHECK_EQ\(1, X509_STORE_add_cert\(store, cert\)\);\n"
        r"    \}\n"
        r"    if \(per_process::cli_options->use_system_ca\) \{\n"
        r"      for \(X509\* cert : GetSystemStoreCACertificates\(\)\) \{\n"
        r"        CHECK_EQ\(1, X509_STORE_add_cert\(store, cert\)\);\n"
        r"      \}\n"
        r"    \}\n"
        r"  \}\n",
        STORE_COMMENT
        + "  CHECK_EQ(1, X509_STORE_set_default_paths(store));\n"
        + "  if (per_process::cli_options->use_system_ca) {\n"
        + "    for (X509* cert : GetSystemStoreCACertificates()) {\n"
        + "      CHECK_EQ(1, X509_STORE_add_cert(store, cert));\n"
        + "    }\n"
        + "  }\n",
        text, name)
    text = substitute(
        r"  Local<Value> result\[arraysize\(root_certs\)\];.*?"
        r"Array::New\(env->isolate\(\), result, arraysize\(root_certs\)\)\);",
        "  args.GetReturnValue().Set(Array::New(env->isolate()));", text, name)
    return text


def current_context(text, name):
    """Node 24 and 26 read use_system_ca from the Environment. Node 26 keeps smart pointers."""
    text = substitute(
        r"static const char\* const root_certs\[\] = \{\n"
        r'#include "node_root_certs.h"  // NOLINT\(build/include_order\)\n'
        r"\};\n\n",
        "", text, name)
    text = substitute(
        r"  // Read the bundled certificates in node_root_certs.h into\n.*?"
        r"  return bundled_root_certs;\n\}\n\n// TODO\(joyeecheung\)",
        "  // rootbeer: The binary holds no roots.\n  return {};\n}\n\n// TODO(joyeecheung)",
        text, name)
    loop = r"(?:X509\* cert|const auto& cert)"
    value = r"cert(?:\.get\(\))?"
    text = substitute(
        r"  if \(ssl_openssl_cert_store\) \{\n"
        r"    CHECK_EQ\(1, X509_STORE_set_default_paths\(store\)\);\n"
        r"  \} else \{\n"
        rf"    for \({loop} : GetBundledRootCertificates\(\)\) \{{\n"
        rf"      CHECK_EQ\(1, X509_STORE_add_cert\(store, {value}\)\);\n"
        r"    \}\n"
        r"    if \(use_system_ca\) \{\n"
        rf"      (for \({loop} : GetSystemStoreCACertificates\(\)\) \{{)\n"
        rf"        (CHECK_EQ\(1, X509_STORE_add_cert\(store, {value}\)\);)\n"
        r"      \}\n"
        r"    \}\n"
        r"  \}\n",
        lambda match: (
            STORE_COMMENT
            + "  CHECK_EQ(1, X509_STORE_set_default_paths(store));\n"
            + "  if (use_system_ca) {\n"
            + f"    {match[1]}\n"
            + f"      {match[2]}\n"
            + "    }\n"
            + "  }\n"),
        text, name)
    text = substitute(
        r"  Local<Value> result\[arraysize\(root_certs\)\];.*?"
        r"Array::New\(env->isolate\(\), result, arraysize\(root_certs\)\)\);",
        "  args.GetReturnValue().Set(Array::New(env->isolate()));", text, name)
    return text


def options(text, name):
    text = substitute(
        r'  if \(use_openssl_ca && use_bundled_ca\) \{\n'
        r'    errors->push_back\("either --use-openssl-ca or --use-bundled-ca can be "\n'
        r'                      "used, not both"\);\n'
        r"  \}\n\n",
        "", text, name)
    return remove_literal(
        '  ImpliesNot("--use-bundled-ca", "[ssl_openssl_cert_store]");\n', text, name)


def openssl_dir(text, name):
    return substitute(
        r'OPENSSLDIR="/System/Library/OpenSSL/"', 'OPENSSLDIR="/etc/ssl"', text, name)


def modules_dir(text, name):
    # Node sets MODULESDIR to the build directory, so the binary holds the path of the builder.
    return substitute(
        r"'MODULESDIR=\"<\(modules_dir\)\"'", "'MODULESDIR=\"/nonexistent/ossl-modules\"'", text, name)


def include_char_traits(text, name):
    return substitute(
        r"#include <string>\n",
        '#include <string>\n\n#include "src/inspector/uint16-char-traits.h"\n',
        text, name)


def zlib_macos_test(text, name):
    return substitute(
        r"#if defined\(MACOS\) \|\| defined\(TARGET_OS_MAC\)\n", "#if defined(MACOS)\n", text, name)


# Node 16 and 18 seed V8 in node_mksnapshot, but they do not ask for a predictable schedule.
# Without it, the startup snapshot differs from build to build. Node 20 and 22 ask for it themselves.
SNAPSHOT_PATH = "tools/snapshot/node_mksnapshot.cc"


def predictable_snapshot(text, name):
    seed = '  v8::V8::SetFlagsFromString("--random_seed=42");\n'
    return substitute(
        re.escape(seed) + r'(?=  v8::V8::SetFlagsFromString\("--harmony-import-assertions"\);\n  return BuildSnapshot)',
        seed + '  v8::V8::SetFlagsFromString("--predictable");\n', text, name)


def edits_for(major):
    if major not in FAMILIES:
        raise SystemExit(
            f"Node {major} has no family in FAMILIES. Compare its src/crypto/crypto_context.cc "
            "with a known family, then add a context function for it. See docs/ADDING_A_VERSION.md.")
    context = {
        "current": current_context,
        "modern": modern_context,
        "legacy": legacy_context,
    }[FAMILIES[major]]
    edits = {
        "src/crypto/crypto_context.cc": context,
        "src/node_options.cc": options,
        "deps/openssl/openssl_common.gypi": openssl_dir,
    }
    if major == "16":
        edits["deps/openssl/openssl.gypi"] = openssl_dir
    else:
        edits["deps/openssl/openssl.gyp"] = modules_dir
    return edits


def unified(path, before, after):
    # The Bazel patch reader needs the git header to split a multi-file patch.
    return f"diff --git a/{path} b/{path}\n" + "".join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True), f"a/{path}", f"b/{path}"))


def new_file(path, text):
    lines = text.splitlines(True)
    return (f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
            f"@@ -0,0 +1,{len(lines)} @@\n" + "".join("+" + line for line in lines))


def read(root, path):
    with open(os.path.join(root, path), encoding="utf-8", newline="") as stream:
        return stream.read()


def write(patches, major, name, text):
    directory = os.path.join(patches, major)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, name), "w", encoding="utf-8", newline="") as stream:
        stream.write(text)


def main():
    if sys.argv[1] == "--list":
        for major, (version, old_v8) in read_versions(sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODULE).items():
            print(major, version, str(old_v8).lower())
        return
    only = None
    if sys.argv[1] == "--only":
        only = sys.argv[2]
        del sys.argv[1:3]
    sources, patches, *rest = sys.argv[1:]
    generate(sources, patches, rest[0] if rest else DEFAULT_MODULE, only)


def generate(sources, patches, module_bazel, only):
    for major, (version, old_v8) in read_versions(module_bazel).items():
        if only and major != only:
            continue
        # A line with no family stops here, before any patch file exists.
        edits = edits_for(major)
        root = os.path.join(sources, f"node-v{version}")
        header = "src/node_root_certs.h"
        write(patches, major, "0001-empty-root-certs.patch",
              unified(header, read(root, header), EMPTY_HEADER))
        forced = ""
        for path, edit in edits.items():
            before = read(root, path)
            forced += unified(path, before, edit(before, f"{major}:{path}"))
        write(patches, major, "0002-force-local-store.patch", forced)
        if old_v8:
            traits = new_file(CHAR_TRAITS_PATH, CHAR_TRAITS_HEADER)
            for path in CHAR_TRAITS_USERS:
                before = read(root, path)
                traits += unified(path, before, include_char_traits(before, f"{major}:{path}"))
            write(patches, major, "0003-libcxx-19-char-traits.patch", traits)
            zlib = ""
            if major not in OLD_ZLIB_HEADERS:
                raise SystemExit(f"Node {major} is old_v8 but OLD_ZLIB_HEADERS has no entry for it")
            for path in OLD_ZLIB_HEADERS[major]:
                before = read(root, path)
                zlib += unified(path, before, zlib_macos_test(before, f"{major}:{path}"))
            write(patches, major, "0004-zlib-macos-test.patch", zlib)
            before = read(root, SNAPSHOT_PATH)
            write(patches, major, "0005-predictable-snapshot.patch",
                  unified(SNAPSHOT_PATH, before, predictable_snapshot(before, f"{major}:{SNAPSHOT_PATH}")))


if __name__ == "__main__":
    main()
