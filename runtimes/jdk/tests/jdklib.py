"""Helpers for the JDK trust tests. Java reads no SSL_CERT_FILE, so each store is a -D flag."""

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time

from trustlib import Inconclusive

# A rejection that comes from the trust store.
STORE_REJECTED = {"NO_TRUST_ANCHORS", "PATH_NOT_TRUSTED"}
NETWORK_ATTEMPTS = 3
STORE_PASSWORD = "changeit"
# KeychainStore-ROOT reads the macOS system roots. KeychainStore reads the user keychain items.
KEYCHAIN_ROOT_FLAGS = [
    "-Djavax.net.ssl.trustStore=NONE",
    "-Djavax.net.ssl.trustStoreType=KeychainStore-ROOT",
]


class Jdk:
    """One JDK image, by the path of its home directory or of its bin/java file."""

    def __init__(self, path):
        path = os.path.abspath(path)
        self.home = os.path.dirname(os.path.dirname(path)) if os.path.isfile(path) else path
        self.java = os.path.join(self.home, "bin", "java")
        self.keytool = os.path.join(self.home, "bin", "keytool")

    def run(self, command, *args, env=None):
        full_env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("TEST_TMPDIR", "/tmp")}
        full_env.update(env or {})
        return subprocess.run(
            [command, *args], env=full_env, capture_output=True, text=True, timeout=180)

    def probe_json(self, probe_java, *flags, mode, env=None):
        result = self.run(self.java, *flags, probe_java, *mode, env=env)
        if result.returncode != 0:
            raise AssertionError(f"probe exited {result.returncode}: {result.stderr}")
        return json.loads(result.stdout.splitlines()[-1])

    def connect(self, probe_java, url, *flags, env=None):
        return self.probe_json(probe_java, *flags, mode=["connect", url], env=env)

    def anchors(self, probe_java, *flags):
        return set(self.probe_json(probe_java, *flags, mode=["anchors"])["anchors"])

    def list_cacerts(self):
        return self.run(self.keytool, "-list", "-cacerts", "-storepass", STORE_PASSWORD)

    def make_store(self, probe_java, pem, name):
        """Writes a PKCS12 file from a PEM file. Returns the -D flags that select it."""
        path = os.path.join(tempfile.mkdtemp(dir=os.environ.get("TEST_TMPDIR")), name)
        self.probe_json(probe_java, mode=["store", pem, path, STORE_PASSWORD])
        return store_flags(path)


def store_flags(path):
    return [
        f"-Djavax.net.ssl.trustStore={path}",
        "-Djavax.net.ssl.trustStoreType=PKCS12",
        f"-Djavax.net.ssl.trustStorePassword={STORE_PASSWORD}",
    ]


def connect_decided(jdk, probe_java, url, *flags):
    """Returns a result that holds a trust decision, or raises Inconclusive."""
    for attempt in range(NETWORK_ATTEMPTS):
        result = jdk.connect(probe_java, url, *flags)
        if result["trusted"] or result["code"] in STORE_REJECTED:
            return result
        if attempt + 1 < NETWORK_ATTEMPTS:
            time.sleep(2)
    raise Inconclusive(f"{url}: no trust decision: {result}")


def pem_fingerprints(pem_path):
    """Returns the SHA-256 of each certificate in a PEM file, in lower case hex."""
    with open(pem_path) as stream:
        bodies = re.findall(
            r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", stream.read(), re.S)
    return {hashlib.sha256(base64.b64decode("".join(b.split()))).hexdigest() for b in bodies}
