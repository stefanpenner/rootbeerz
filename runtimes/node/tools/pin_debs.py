"""Prints pinned ADD lines for Debian packages from one snapshot.debian.org timestamp.

Usage: python3 runtimes/node/tools/pin_debs.py 20260301T000000Z libc6 libxml2 ...

This tool is the only step that reads an unpinned index. Its output holds the
sha256 of each .deb, and the Dockerfile keeps those lines. A reviewer can run the
tool again and compare. The build never runs this tool.
"""

import io
import lzma
import re
import sys
import urllib.request

SNAPSHOT = "https://snapshot.debian.org/archive/debian/{ts}"
SUITE = "bookworm"


def read_index(timestamp):
    url = SNAPSHOT.format(ts=timestamp) + f"/dists/{SUITE}/main/binary-amd64/Packages.xz"
    with urllib.request.urlopen(url, timeout=120) as response:
        return lzma.decompress(response.read()).decode()


def parse(index):
    packages = {}
    for block in index.split("\n\n"):
        fields = dict(re.findall(r"^([A-Za-z0-9-]+): (.*)$", block, re.MULTILINE))
        if "Package" in fields:
            packages[fields["Package"]] = fields
    return packages


def add_line(timestamp, fields):
    url = SNAPSHOT.format(ts=timestamp) + "/" + fields["Filename"]
    name = fields["Package"]
    return (
        f"ADD --checksum=sha256:{fields['SHA256']} \\\n"
        f"    {url} \\\n"
        f"    /debs/{name}.deb"
    )


def main(argv):
    if len(argv) < 3 or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", argv[1]):
        sys.exit(__doc__)
    timestamp, names = argv[1], argv[2:]
    packages = parse(read_index(timestamp))
    for name in names:
        if name not in packages:
            sys.exit(f"{name} is not in the {SUITE} index at {timestamp}")
        print(f"# {name} {packages[name]['Version']}")
        print(add_line(timestamp, packages[name]))


if __name__ == "__main__":
    main(sys.argv)
