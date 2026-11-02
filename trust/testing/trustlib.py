"""Shared helpers for the rootbeerz tests. The tests read their inputs from env."""

import json
import os
import subprocess
import time

# These codes prove that the store did not hold an issuer for the chain.
STORE_REJECTED = {
    "UNABLE_TO_GET_ISSUER_CERT_LOCALLY",
    "UNABLE_TO_GET_ISSUER_CERT",
    "UNABLE_TO_VERIFY_LEAF_SIGNATURE",
    "SELF_SIGNED_CERT_IN_CHAIN",
    "DEPTH_ZERO_SELF_SIGNED_CERT",
    "CERT_UNTRUSTED",
}

# These codes reject a certificate for a reason that does not depend on the store.
# CERT_HAS_EXPIRED comes from the host. CERT_REVOKED comes from the Node 22 SPKI block list.
STORE_INDEPENDENT = {"CERT_HAS_EXPIRED", "CERT_REVOKED", "ERR_TLS_CERT_ALTNAME_INVALID"}

NETWORK_ATTEMPTS = 3


class Inconclusive(Exception):
    """The host gave no trust decision. A test must error, and must not pass."""


def path_from_env(name):
    return os.path.abspath(os.environ[name])


def node_env(**extra):
    """Returns an env with no inherited SSL or Node settings."""
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("TEST_TMPDIR", "/tmp")}
    env.update(extra)
    return env


def run_node(node, *args, env=None):
    return subprocess.run(
        [node, *args],
        env=node_env() if env is None else env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def evaluate(node, expression, *flags, env=None):
    """Returns the JSON value of an expression that Node evaluates."""
    script = f"console.log(JSON.stringify({expression}))"
    result = run_node(node, *flags, "-e", script, env=env)
    if result.returncode != 0:
        raise AssertionError(f"node exited {result.returncode}: {result.stderr}")
    return json.loads(result.stdout)


def probe_once(node, probe_js, url, flags, env):
    result = run_node(node, *flags, probe_js, url, env=env)
    if result.returncode != 0:
        raise AssertionError(f"probe exited {result.returncode}: {result.stderr}")
    return json.loads(result.stdout)


def probe(node, probe_js, url, *flags, env=None):
    """Returns a probe result that holds a trust decision, or raises Inconclusive."""
    for attempt in range(NETWORK_ATTEMPTS):
        result = probe_once(node, probe_js, url, flags, env)
        if result["trusted"] or is_certificate_code(result["code"]):
            return result
        if attempt + 1 < NETWORK_ATTEMPTS:
            time.sleep(2)
    raise Inconclusive(f"{url}: no trust decision: {result}")


def is_certificate_code(code):
    return code in STORE_REJECTED or code in STORE_INDEPENDENT or "CERT" in code


def store_env(store_file, store_dir):
    """Returns an env that points OpenSSL at one file and one directory only."""
    return node_env(SSL_CERT_FILE=store_file, SSL_CERT_DIR=store_dir)
