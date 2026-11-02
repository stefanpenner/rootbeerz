# Linux x86_64 host for Bazel. The build itself uses the pinned toolchain from
# MODULE.bazel. This image supplies Bazel, POSIX tools, runtime libraries, and a CA store.
#
# Every input has a sha256. The final stage starts at scratch.
# Build from the repository root:
#   docker build --platform linux/amd64 -f runtimes/node/image/Dockerfile -t rootbeerz-linux .
#
# To change a package, run tools/pin_debs.py and paste its output here.

# This stage only unpacks the pinned files. Nothing from it reaches the final stage
# except the unpacked tree.
FROM debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3 AS rootfs

ADD --checksum=sha256:857bed5d2756b4d998d3caebf2d941d13d434c4eda4b1d6d7dda205736c25a93 \
    --chmod=755 \
    https://github.com/bazelbuild/bazel/releases/download/9.1.1/bazel-9.1.1-linux-x86_64 \
    /rootfs/usr/local/bin/bazel

# Debian bookworm packages from snapshot.debian.org, pinned by sha256.
# libc6 2.36-9+deb12u13
ADD --checksum=sha256:3d8072c73b017e907bbf44b7db870687888a991961d74f1ecbba6b9458f32a2c \
    https://snapshot.debian.org/archive/debian/20260301T000000Z/pool/main/g/glibc/libc6_2.36-9+deb12u13_amd64.deb \
    /debs/libc6.deb
# gcc-12-base 12.2.0-14+deb12u1
ADD --checksum=sha256:1896a2aacf4ad681ff5eacc24a5b0ca4d5d9c9b9c9e4b6de5197bc1e116ea619 \
    https://snapshot.debian.org/archive/debian/20260301T000000Z/pool/main/g/gcc-12/gcc-12-base_12.2.0-14+deb12u1_amd64.deb \
    /debs/gcc-12-base.deb
# libgcc-s1 12.2.0-14+deb12u1
ADD --checksum=sha256:3016e62cb4b7cd8038822870601f5ed131befe942774d0f745622cc77d8a88f7 \
    https://snapshot.debian.org/archive/debian/20260301T000000Z/pool/main/g/gcc-12/libgcc-s1_12.2.0-14+deb12u1_amd64.deb \
    /debs/libgcc-s1.deb
# libstdc++6 12.2.0-14+deb12u1
ADD --checksum=sha256:5cd3171216d4ab0fc911cfe9c35509bf2dd8f47761c43b7f6a4296701551a24d \
    https://snapshot.debian.org/archive/debian/20260301T000000Z/pool/main/g/gcc-12/libstdc++6_12.2.0-14+deb12u1_amd64.deb \
    /debs/libstdc++6.deb
# zlib1g 1:1.2.13.dfsg-1
ADD --checksum=sha256:d7dd1d1411fedf27f5e27650a6eff20ef294077b568f4c8c5e51466dc7c08ce4 \
    https://snapshot.debian.org/archive/debian/20260301T000000Z/pool/main/z/zlib/zlib1g_1.2.13.dfsg-1_amd64.deb \
    /debs/zlib1g.deb
# libzstd1 1.5.4+dfsg2-5
ADD --checksum=sha256:6315b5ac38b724a710fb96bf1042019398cb656718b1522279a5185ed39318fa \
    https://snapshot.debian.org/archive/debian/20260301T000000Z/pool/main/libz/libzstd/libzstd1_1.5.4+dfsg2-5_amd64.deb \
    /debs/libzstd1.deb
# liblzma5 5.4.1-1
ADD --checksum=sha256:d321b9502b16aac534e1c691afbe3dc5e125e5091aa35bea026c59b25ebe82e7 \
    https://snapshot.debian.org/archive/debian/20260301T000000Z/pool/main/x/xz-utils/liblzma5_5.4.1-1_amd64.deb \
    /debs/liblzma5.deb
# libicu72 72.1-3+deb12u1
ADD --checksum=sha256:f7f6f99c6d7b025914df2447fc93e11d22c44c0c8bdd8b6f36691c9e7ddcef88 \
    https://snapshot.debian.org/archive/debian/20260301T000000Z/pool/main/i/icu/libicu72_72.1-3+deb12u1_amd64.deb \
    /debs/libicu72.deb
# libxml2 2.9.14+dfsg-1.3~deb12u5
ADD --checksum=sha256:261ad120adf02d9bdef3f47c486e016bfd410cdb1ada9730a8f9a6add0857bd6 \
    https://snapshot.debian.org/archive/debian/20260301T000000Z/pool/main/libx/libxml2/libxml2_2.9.14+dfsg-1.3~deb12u5_amd64.deb \
    /debs/libxml2.deb
# busybox-static 1:1.35.0-4+b7
ADD --checksum=sha256:614744fe5088e932c6521fa3cca7d507b1992f6944cc8d8c25768decdd9bfaf8 \
    https://snapshot.debian.org/archive/debian/20260301T000000Z/pool/main/b/busybox/busybox-static_1.35.0-4+b7_amd64.deb \
    /debs/busybox-static.deb
# dash 0.5.12-2
ADD --checksum=sha256:33ea40061da2f1a861ec46212b2b6a34f0776a049b1a3f0abce2fb8cb994258f \
    https://snapshot.debian.org/archive/debian/20260301T000000Z/pool/main/d/dash/dash_0.5.12-2_amd64.deb \
    /debs/dash.deb
# bash 5.2.15-2+b10
ADD --checksum=sha256:be3ab2fd057cb9a29e733d0d8c2b867468731d99af2ce62a63ec6a6c7928572f \
    https://snapshot.debian.org/archive/debian/20260301T000000Z/pool/main/b/bash/bash_5.2.15-2+b10_amd64.deb \
    /debs/bash.deb
# libtinfo6 6.4-4
ADD --checksum=sha256:072d908f38f51090ca28ca5afa3b46b2957dc61fe35094c0b851426859a49a51 \
    https://snapshot.debian.org/archive/debian/20260301T000000Z/pool/main/n/ncurses/libtinfo6_6.4-4_amd64.deb \
    /debs/libtinfo6.deb

# Unpack each package, then link every BusyBox applet.
# /bin/bash is GNU bash, because Bazel test scripts need it.
# /bin/sh is dash. BusyBox sh runs its own applets before it looks at PATH,
# so it would run its own `ar` and not the pinned llvm-ar wrapper.
# The image has no `ar` link for the same reason. The build uses llvm-ar.
RUN set -eu; \
    mkdir -p /rootfs/tmp /rootfs/home/builder /rootfs/cache /rootfs/src /rootfs/usr/bin; \
    for deb in /debs/*.deb; do dpkg-deb -x "$deb" /rootfs; done; \
    ln -sf dash /rootfs/bin/sh; \
    for applet in $(/rootfs/bin/busybox --list); do \
        [ "$applet" = ar ] && continue; \
        [ -e "/rootfs/bin/$applet" ] || ln -s busybox "/rootfs/bin/$applet"; \
    done; \
    ln -s ../../bin/busybox /rootfs/usr/bin/env; \
    rm -rf /rootfs/usr/share /rootfs/var /rootfs/etc/ld.so.conf.d; \
    chmod 1777 /rootfs/tmp; \
    chown -R 1000:1000 /rootfs/home/builder /rootfs/cache /rootfs/src

FROM scratch

COPY --from=rootfs /rootfs/ /
COPY runtimes/node/image/etc/passwd runtimes/node/image/etc/group runtimes/node/image/etc/nsswitch.conf /etc/
COPY trust/mozilla-no-emsign.pem /etc/ssl/certs/ca-certificates.crt
COPY trust/mozilla-no-emsign.pem /etc/ssl/cert.pem

ENV PATH=/usr/local/bin:/usr/bin:/bin \
    HOME=/home/builder \
    SSL_CERT_FILE=/etc/ssl/cert.pem

# rules_python refuses to run its interpreter setup as root.
USER builder
WORKDIR /src
