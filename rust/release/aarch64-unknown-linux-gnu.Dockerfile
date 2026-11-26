# title: Linux arm64 release build image
# authors:
#   - Steffen Klug <45033201+stklug84@users.noreply.github.com>
# date: 2026-11-26
# keywords: [infrastructure, release, rust]
# comment: |
#   The image rust/release/build-linux.sh compiles the arm64 simulator
#   binaries in: PyPA's manylinux_2_28 (AlmaLinux 8, glibc 2.28), so the
#   binaries run on any Linux with glibc 2.28 or newer. The script reads
#   the FROM line and runs the image as it is; this file is never built.
#   It exists so Dependabot (the docker ecosystem in dependabot.yml) keeps
#   the tag and its digest current.
FROM quay.io/pypa/manylinux_2_28_aarch64:2026.11.21-1@sha256:126d9eea9d6b1717f3e17b2b3ac51da04ef159a5f935dac36fe92d621dc8f246
