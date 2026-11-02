"""Source facts that make a Bun tree free of compiled-in roots.

Each check returns True when the tree is good. An unpatched Bun tree fails each check.
"""

import pathlib
import re

BUNDLED_RS = "src/runtime/socket/bundled_root_certs.rs"
ROOT_CERTS_CPP = "packages/bun-usockets/src/crypto/root_certs.cpp"
TLS_TS = "src/js/node/tls.ts"
ROOT_DER = "packages/bun-usockets/root_certs.der"
ROOT_LIST = "packages/bun-usockets/root_certs.txt"
ROOT_DATA = "packages/bun-usockets/certdata.txt"


def _text(root, name):
    return (pathlib.Path(root) / name).read_text(encoding="utf-8")


def _function(source, signature):
    """Returns the body of one C++ function. A closing brace in column 0 ends it."""
    start = source.index(signature)
    return source[start : source.index("\n}\n", start)]


def no_der_file(root):
    return not (pathlib.Path(root) / ROOT_DER).exists()


def no_root_list(root):
    return not (pathlib.Path(root) / ROOT_LIST).exists()


def no_root_data(root):
    return not (pathlib.Path(root) / ROOT_DATA).exists()


def no_include_bytes(root):
    return "include_bytes!" not in _text(root, BUNDLED_RS)


def store_skips_bundled_set(root):
    body = _function(_text(root, ROOT_CERTS_CPP), "extern \"C\" X509_STORE *us_get_default_ca_store()")
    return "us_get_bundled_root_cert_set" not in body


def store_always_adds_system(root):
    body = _function(_text(root, ROOT_CERTS_CPP), "extern \"C\" X509_STORE *us_get_default_ca_store()")
    return "if (us_should_use_system_ca())" not in body and "us_get_root_system_certs()" in body


def keychain_yields_to_env(root):
    """On macOS, SSL_CERT_FILE or SSL_CERT_DIR replaces the keychain."""
    body = _function(_text(root, ROOT_CERTS_CPP), "const us_system_certs_t &us_get_root_system_certs()")
    return re.search(r'getenv\("SSL_CERT_FILE"\)', body) is not None and re.search(
        r'getenv\("SSL_CERT_DIR"\)', body
    ) is not None


def tls_default_list_has_system(root):
    return 'getUseSystemCA() || process.env.NODE_USE_SYSTEM_CA === "1"' not in _text(root, TLS_TS)


CHECKS = (
    no_der_file,
    no_root_list,
    no_root_data,
    no_include_bytes,
    store_skips_bundled_set,
    store_always_adds_system,
    keychain_yields_to_env,
    tls_default_list_has_system,
)


def failures(root):
    return [check.__name__ for check in CHECKS if not check(root)]
