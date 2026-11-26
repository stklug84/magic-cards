# title: Linux x86_64 release build image
# authors:
#   - Steffen Klug <45033201+stklug84@users.noreply.github.com>
# date: 2026-11-26
# keywords: [infrastructure, release, rust]
# comment: |
#   The image rust/release/build-linux.sh compiles the x86_64 simulator
#   binaries in: PyPA's manylinux_2_28 (AlmaLinux 8, glibc 2.28), so the
#   binaries run on any Linux with glibc 2.28 or newer. The script reads
#   the FROM line and runs the image as it is; this file is never built.
#   It exists so Dependabot (the docker ecosystem in dependabot.yml) keeps
#   the tag and its digest current.
FROM quay.io/pypa/manylinux_2_28_x86_64:2026.11.21-1@sha256:55a36963bc0504cdf004011001c1072306402f0b8b83be5a5eba7068f93eec53
