"""Writes the certifi shim wheel. The same inputs give the same bytes."""

import argparse
import base64
import hashlib
import os
import zipfile

METADATA = """\
Metadata-Version: 2.1
Name: certifi
Version: {version}
Summary: rootbeerz shim of certifi. where() returns the system store. It bundles no roots.
Home-page: https://github.com/stefanpenner/rootbeer-node
License: MIT
Requires-Python: >=3.7
"""

WHEEL = """\
Wheel-Version: 1.0
Generator: rootbeerz
Root-Is-Purelib: true
Tag: py3-none-any
"""


def record_line(name, data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return f"{name},sha256={digest.decode()},{len(data)}\n"


def wheel_members(version, sources):
    """Returns (name, bytes) for each member, sorted. RECORD is the last member."""
    info = f"certifi-{version}.dist-info"
    members = {f"certifi/{os.path.basename(path)}": read(path) for path in sources}
    members["certifi/_version.py"] = f'__version__ = "{version}"\n'.encode()
    members[f"{info}/METADATA"] = METADATA.format(version=version).encode()
    members[f"{info}/WHEEL"] = WHEEL.encode()
    ordered = sorted(members.items())
    record = "".join(record_line(name, data) for name, data in ordered)
    record += f"{info}/RECORD,,\n"
    return ordered + [(f"{info}/RECORD", record.encode())]


def read(path):
    with open(path, "rb") as handle:
        return handle.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("sources", nargs="+")
    args = parser.parse_args()
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as whl:
        for name, data in wheel_members(args.version, args.sources):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            whl.writestr(info, data)


if __name__ == "__main__":
    main()
