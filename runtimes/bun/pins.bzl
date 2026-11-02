"""Pins for Bun inputs that runtimes/bun/manifest.json does not hold. Each value is a sha256."""

# The stock release binaries. The tests use them as the control. Nothing links them.
STOCK = {
    "1": {
        "darwin_arm64": "c240d7f0ccb90c263a849fcdb09b7ee62630d921348ed6aafda86b9d577bc510",
        "linux_x64": "bf8ccbfb2b0b80159e0f9a6d15c55317add8077534dbc6130a3edb52fccb395c",
    },
}
