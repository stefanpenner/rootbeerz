"""The certifi API over the system store. This package bundles no certificate.

certifi returns one file, so where() cannot use a store that is only a directory.
"""

import os
import ssl

# The file list of Go crypto/x509. A file is the only store that this API can return.
KNOWN_STORE_PATHS = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/ca-bundle.pem",
    "/etc/pki/tls/cacert.pem",
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/etc/ssl/cert.pem",
)


def where():
    """Returns the path of the system store. Raises FileNotFoundError if none exists."""
    override = os.environ.get("SSL_CERT_FILE")
    if override:
        if os.path.isfile(override):
            return override
        raise FileNotFoundError(f"SSL_CERT_FILE is not a file: {override}")

    cafile = ssl.get_default_verify_paths().cafile
    if cafile and os.path.isfile(cafile):
        return cafile

    for path in KNOWN_STORE_PATHS:
        if os.path.isfile(path):
            return path

    raise FileNotFoundError(
        "no system certificate store: SSL_CERT_FILE is not set, "
        f"OpenSSL has no default file, and these files do not exist: {KNOWN_STORE_PATHS}"
    )


def contents():
    """Returns the text of the store that where() names."""
    with open(where(), encoding="utf-8", errors="replace") as store:
        return store.read()
