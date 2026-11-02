"""Pins for the Deno build inputs. Deno version and source sha256 stay in runtimes/deno/manifest.json.

Each digest comes from the upstream checksum list or release digest. A Deno bump must update the
Rust and rusty_v8 entries. The source tests and the build fail when the entries do not match.
"""

CMAKE = {
    "version": "4.4.5",
    "macos_sha256": "679e77a96b9ef9484cc6c1445255fc4f84206b48a65420efbedf16bdab55fbff",
}

# The key is the line in runtimes/deno/manifest.json.
LINES = {
    "2": {
        "rust_version": "1.95.0",
        "rust_date": "2026-04-16",
        "rusty_v8_version": "150.4.0",
        "darwin_arm64": {
            "triple": "aarch64-apple-darwin",
            "rustc": "149e85a285b6eba58eb6c8bdf7deb1b93763890598e62cb635a712e3a8454f04",
            "cargo": "6c2ffed8e1ac9cf4dc9e80f282a869a6b237a153e7c55cca039d33de29d80aaf",
            "rust_std": "9b30089b0f767cb91b2190ffec55a9beeb2a21a1405d8da0f664d7e09d08e6d8",
            # Deno enables the simdutf feature, so the build reads the simdutf archive.
            "rusty_v8": "5aeffd8d5a0c1b79ac1d70af83d5b19099655fd9c645a794dc43f101f779838c",
            "rusty_v8_binding": "ca5adf0cf89c9a70ad460ae73648b2fe89b74aa113b3cb7f757b6a02b758394f",
            "stock": "9f0bf660393da2eaff044cdc7444cb08c73f235849b61d224d825a194ce7c98e",
        },
    },
}
