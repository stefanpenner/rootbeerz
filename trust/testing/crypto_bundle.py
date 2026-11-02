"""Helpers that read PEM bundles and build OpenSSL hashed directories."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile

from trustlib import node_env


def parse_line(stdout):
    return json.loads(stdout)


def sha256(path):
    with open(path, "rb") as stream:
        return hashlib.sha256(stream.read()).hexdigest()


def body_marker(pem):
    """Returns the second body line of a PEM. The first line repeats across roots."""
    return pem.splitlines()[2].encode()


def subjects(node, bundle):
    script = (
        "const fs = require('fs');"
        "const { X509Certificate } = require('crypto');"
        f"const text = fs.readFileSync({json.dumps(bundle)}, 'latin1');"
        "const blocks = text.match(/-----BEGIN CERTIFICATE-----[\\s\\S]+?-----END CERTIFICATE-----/g);"
        "console.log(JSON.stringify(blocks.map((pem) => new X509Certificate(pem).subject)));"
    )
    result = subprocess.run(
        [node, "-e", script], env=node_env(), capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def hashed_dir(ca_file):
    """Returns a directory that OpenSSL can search. It holds one CA under its subject hash.

    OpenSSL finds a file in SSL_CERT_DIR by the hash of the subject. The test CA
    never changes, so the test uses the name that the build recorded in env.
    """
    directory = tempfile.mkdtemp()
    shutil.copy(ca_file, os.path.join(directory, os.environ["TEST_CA_SUBJECT_HASH"] + ".0"))
    return directory
