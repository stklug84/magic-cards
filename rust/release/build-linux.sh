#!/usr/bin/env bash
#
# Build the simulator binaries (mtg-simulate, mtg-selfplay, mtg-cr) for a
# Linux target inside PyPA's manylinux_2_28 image (AlmaLinux 8, glibc 2.28),
# then check that none of them needs a newer glibc than 2.28.
#
# A binary linked on a current distribution asks for that distribution's
# glibc: v0.4.0, linked on Ubuntu 24.04, needed glibc 2.34 and, on arm64,
# 2.39. Linked against glibc 2.28, the binaries run on every Linux with
# glibc 2.28 or newer (RHEL 8, Debian 11, Ubuntu 20.04 and later).
#
#   rust/release/build-linux.sh x86_64-unknown-linux-gnu
#   rust/release/build-linux.sh aarch64-unknown-linux-gnu
#
# Needs Docker. The image of each target is the FROM line of
# rust/release/<target>.Dockerfile, pinned by digest. The build runs as the
# calling user, with rustup installed from a pinned, checksum-verified
# rustup-init and the toolchain rust/rust-toolchain.toml pins. The binaries
# land in rust/target/<target>/release/. A target of another architecture
# than the machine's runs emulated and slowly; release.yml builds each one
# on its own runner. Used by release.yml and validate.yml.

set -euo pipefail

GLIBC_FLOOR="2.28"
RUSTUP_VERSION="1.29.3"
BINARIES="mtg-simulate mtg-selfplay mtg-cr"

usage() {
	sed -n '2,21p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

if [ $# -ne 1 ]; then
	usage >&2
	exit 2
fi
target="$1"

# the published SHA-256 of rustup-init RUSTUP_VERSION
# (https://static.rust-lang.org/rustup/archive/<version>/<target>/rustup-init.sha256)
case "${target}" in
x86_64-unknown-linux-gnu)
	platform="linux/amd64"
	rustup_sha256="b1c8cb14e30889e7e2999e6b69e62d9e9b9ffc0f4df8180564d306d9680e6c55"
	;;
aarch64-unknown-linux-gnu)
	platform="linux/arm64"
	rustup_sha256="9f608c142e0e38a78957d0661ba1dfaf0b6fb37184cfaa4b9a0aa57af6184282"
	;;
-h | --help)
	usage
	exit 0
	;;
*)
	echo "error: no Linux release build for target '${target}'" >&2
	usage >&2
	exit 2
	;;
esac

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "${here}/../.." && pwd)"
image="$(sed -n 's/^FROM //p' "${here}/${target}.Dockerfile")"
if [ -z "${image}" ]; then
	echo "error: ${here}/${target}.Dockerfile names no image" >&2
	exit 1
fi

echo "==> building ${BINARIES} for ${target} in ${image%@*}"
# The quoted script runs in the container's bash; its variables come from
# the -e options, so it is not expanded here.
# shellcheck disable=SC2016
docker run --rm \
	--platform "${platform}" \
	--user "$(id -u):$(id -g)" \
	--volume "${root}:/work" \
	--workdir /work/rust \
	--env HOME=/tmp/home \
	--env RUSTUP_HOME=/tmp/rustup \
	--env CARGO_HOME=/tmp/cargo \
	--env TARGET="${target}" \
	--env BINARIES="${BINARIES}" \
	--env GLIBC_FLOOR="${GLIBC_FLOOR}" \
	--env RUSTUP_VERSION="${RUSTUP_VERSION}" \
	--env RUSTUP_SHA256="${rustup_sha256}" \
	"${image}" \
	bash -euo pipefail -c '
		mkdir -p "$HOME"
		curl --proto "=https" --tlsv1.2 -fsSL -o /tmp/rustup-init \
			"https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/${TARGET}/rustup-init"
		echo "${RUSTUP_SHA256}  /tmp/rustup-init" | sha256sum -c --quiet -
		chmod +x /tmp/rustup-init
		/tmp/rustup-init -y --quiet --no-modify-path --profile minimal --default-toolchain none
		export PATH="${CARGO_HOME}/bin:${PATH}"
		# installs the channel and components rust-toolchain.toml pins
		rustup toolchain install
		rustc --version
		echo "linking against $(ldd --version | head -n 1)"
		cargo build --release --locked -p mtgsim --target "${TARGET}"

		# readelf lists every glibc symbol version a binary needs; a weak
		# need (a symbol looked up at run time) does not stop it loading
		status=0
		for name in ${BINARIES}; do
			binary="target/${TARGET}/release/${name}"
			needed="$(readelf --version-info --wide "${binary}" |
				sed -n "s/.*Name: GLIBC_\([0-9.]*\) *Flags: none.*/\1/p" | sort -V | tail -n 1)"
			newest="$(printf "%s\n%s\n" "${needed}" "${GLIBC_FLOOR}" | sort -V | tail -n 1)"
			if [ -z "${needed}" ] || [ "${newest}" != "${GLIBC_FLOOR}" ]; then
				echo "error: ${name} needs glibc ${needed:-?}, more than ${GLIBC_FLOOR}" >&2
				status=1
			else
				echo "${name}: needs glibc ${needed} or newer"
			fi
		done
		exit "${status}"
	'
echo "==> ${root}/rust/target/${target}/release/{${BINARIES// /,}}"
