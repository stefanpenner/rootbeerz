"""Shared helpers for the certifi tests. The tests read their inputs from env.

A probe puts one wheel on sys.path of a new Python process. The process then
uses the call shape of requests: ssl context with cafile=certifi.where().
"""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys

PEM_BLOCK = re.compile(
    r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.S
)

_PROBE = """
import json, socket, ssl, sys
sys.path.insert(0, sys.argv[1])
import certifi
out = {"version": certifi.__version__, "file": certifi.__file__}
try:
    out["where"] = certifi.where()
    out["contents"] = certifi.contents()
except Exception as err:
    out["where_error"] = type(err).__name__ + ": " + str(err)
    print(json.dumps(out))
    sys.exit(0)
if len(sys.argv) > 2:
    host, port = sys.argv[2], int(sys.argv[3])
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ssl.SSLError as err:
        out["tls"] = "rejected-load"
        out["tls_detail"] = str(err)
    else:
        try:
            with socket.create_connection((host, port), timeout=15) as raw:
                with ctx.wrap_socket(raw, server_hostname=host):
                    out["tls"] = "trusted"
        except ssl.SSLCertVerificationError as err:
            out["tls"] = "rejected-verify"
            out["verify_code"] = err.verify_code
            out["tls_detail"] = str(err)
        except OSError as err:
            out["tls"] = "no-decision"
            out["tls_detail"] = str(err)
print(json.dumps(out))
"""


def fingerprints(pem_text):
    """Returns the sha256 of the DER bytes of each certificate in a PEM text."""
    return {
        hashlib.sha256(base64.b64decode("".join(body.split()))).hexdigest()
        for body in PEM_BLOCK.findall(pem_text)
    }


def probe(wheel, cert_file=None, server=None):
    """Runs one wheel in a new process. Returns the JSON result of the process."""
    env = {"PATH": "/usr/bin:/bin"}
    if cert_file is not None:
        env["SSL_CERT_FILE"] = str(cert_file)
    args = [sys.executable, "-I", "-c", _PROBE, str(wheel)]
    if server is not None:
        args += [server[0], str(server[1])]
    done = subprocess.run(args, env=env, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise AssertionError(f"probe exited {done.returncode}: {done.stderr}")
    return json.loads(done.stdout)


def env_path(name):
    return os.path.abspath(os.environ[name])
