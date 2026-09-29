#!/bin/sh
# Install a neovain release binary on Linux or macOS:
#
#   curl -fsSL https://raw.githubusercontent.com/kbrock84/neovain/main/install.sh | sh
#
# NEOVAIN_VERSION      release to install, e.g. v0.1.0 (default: the latest release)
# NEOVAIN_INSTALL_DIR  where to put the binary (default: ~/.local/bin)
set -eu

repo="kbrock84/neovain"
dir="${NEOVAIN_INSTALL_DIR:-$HOME/.local/bin}"

fail() {
  echo "neovain install: $1" >&2
  exit 1
}

case "$(uname -s)" in
  Linux) os="unknown-linux-musl" ;;
  Darwin) os="apple-darwin" ;;
  *) fail "unsupported system $(uname -s); see https://github.com/$repo/releases" ;;
esac
case "$(uname -m)" in
  x86_64 | amd64) arch="x86_64" ;;
  arm64 | aarch64) arch="aarch64" ;;
  *) fail "unsupported machine $(uname -m); see https://github.com/$repo/releases" ;;
esac

version="${NEOVAIN_VERSION:-}"
if [ -z "$version" ]; then
  latest="$(curl -fsSL -o /dev/null -w '%{url_effective}' "https://github.com/$repo/releases/latest")"
  version="${latest##*/tag/}"
fi
case "$version" in
  v[0-9]*) ;;
  *) fail "could not find a release (got '$version')" ;;
esac

name="neovain-$version-$arch-$os"
url="https://github.com/$repo/releases/download/$version"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

curl -fsSL "$url/$name.tar.gz" -o "$tmp/$name.tar.gz" || fail "download failed: $url/$name.tar.gz"
curl -fsSL "$url/SHA256SUMS" -o "$tmp/SHA256SUMS" || fail "download failed: $url/SHA256SUMS"

if command -v sha256sum > /dev/null 2>&1; then
  sum="sha256sum"
else
  sum="shasum -a 256"
fi
grep "$name.tar.gz" "$tmp/SHA256SUMS" > "$tmp/expected" || fail "no checksum listed for $name.tar.gz"
(cd "$tmp" && $sum -c expected > /dev/null) || fail "checksum mismatch for $name.tar.gz"

tar xzf "$tmp/$name.tar.gz" -C "$tmp"
mkdir -p "$dir"
cp "$tmp/$name/neovain" "$dir/neovain"
chmod 755 "$dir/neovain"

echo "installed neovain $version to $dir/neovain"
command -v nvim > /dev/null 2>&1 || echo "note: neovain needs Neovim 0.9 or newer (nvim) on your PATH"
case ":$PATH:" in
  *":$dir:"*) ;;
  *) echo "note: $dir is not on your PATH" ;;
esac
