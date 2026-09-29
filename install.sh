#!/bin/sh
# Install neovain on Linux or macOS:
#
#   curl -fsSL https://raw.githubusercontent.com/kbrock84/neovain/main/install.sh | sh
#
# neovain drives Neovim, so the script also checks for Neovim 0.9 or newer. If it is missing or
# too old, the script asks before installing it. Nothing here needs sudo.
#
# NEOVAIN_VERSION       release to install, e.g. v0.1.0 (default: the latest release)
# NEOVAIN_INSTALL_DIR   where to put the binaries (default: ~/.local/bin)
# NEOVAIN_NVIM_DIR      where to unpack Neovim (default: ~/.local/opt)
# NEOVAIN_INSTALL_NVIM  yes or no: answer the Neovim question ahead of time (for scripts and agents)
set -eu

repo="kbrock84/neovain"
dir="${NEOVAIN_INSTALL_DIR:-$HOME/.local/bin}"
opt="${NEOVAIN_NVIM_DIR:-$HOME/.local/opt}"

fail() {
  echo "neovain install: $1" >&2
  exit 1
}

case "$(uname -s)" in
  Linux) os="unknown-linux-musl" nvim_os="linux" ;;
  Darwin) os="apple-darwin" nvim_os="macos" ;;
  *) fail "unsupported system $(uname -s); see https://github.com/$repo/releases" ;;
esac
case "$(uname -m)" in
  x86_64 | amd64) arch="x86_64" nvim_arch="x86_64" ;;
  arm64 | aarch64) arch="aarch64" nvim_arch="arm64" ;;
  *) fail "unsupported machine $(uname -m); see https://github.com/$repo/releases" ;;
esac

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# ---- neovain ----

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
echo "Installed neovain $version to $dir/neovain"

# ---- Neovim ----

# Prints the version of the nvim on PATH, e.g. 0.11.3, or nothing if there is none.
nvim_version() {
  command -v nvim > /dev/null 2>&1 || return 0
  nvim --version 2> /dev/null | head -n 1 | sed 's/^NVIM v//'
}

# True if the version in $1 is 0.9 or newer.
new_enough() {
  major="${1%%.*}"
  rest="${1#*.}"
  minor="${rest%%.*}"
  case "$major$minor" in
    "" | *[!0-9]*) return 1 ;;
  esac
  [ "$major" -gt 0 ] || [ "$minor" -ge 9 ]
}

# Asks a yes/no question on the terminal. The script usually runs as `curl ... | sh`, so stdin is
# the script itself and the answer has to come from /dev/tty. With no terminal, the answer is no.
ask() {
  case "${NEOVAIN_INSTALL_NVIM:-}" in
    yes | y | 1 | true) return 0 ;;
    no | n | 0 | false) return 1 ;;
  esac
  if ! (exec < /dev/tty) 2> /dev/null; then
    echo "No terminal to ask on, so skipping. Set NEOVAIN_INSTALL_NVIM=yes to install it."
    return 1
  fi
  printf '%s [y/N] ' "$1" > /dev/tty
  read -r answer < /dev/tty || return 1
  case "$answer" in
    y | Y | yes | Yes | YES) return 0 ;;
    *) return 1 ;;
  esac
}

install_nvim() {
  # Neovim's Linux builds are linked against glibc, so they don't run on musl systems like Alpine.
  if [ "$nvim_os" = "linux" ] && ldd --version 2>&1 | grep -qi musl; then
    echo "Neovim's prebuilt binaries need glibc. Install Neovim with your package manager"
    echo "instead (Alpine: apk add neovim)."
    return 1
  fi
  if [ -e "$dir/nvim" ] && [ ! -L "$dir/nvim" ]; then
    echo "$dir/nvim already exists and is not a link, so it was left alone."
    return 1
  fi
  nvim_name="nvim-$nvim_os-$nvim_arch"
  nvim_url="https://github.com/neovim/neovim/releases/latest/download/$nvim_name.tar.gz"
  echo "Downloading $nvim_url"
  curl -fsSL "$nvim_url" -o "$tmp/$nvim_name.tar.gz" || {
    echo "Download failed."
    return 1
  }
  mkdir -p "$opt"
  rm -rf "${opt:?}/$nvim_name"
  tar xzf "$tmp/$nvim_name.tar.gz" -C "$opt"
  ln -sf "$opt/$nvim_name/bin/nvim" "$dir/nvim"
  echo "Installed $("$dir/nvim" --version | head -n 1) to $opt/$nvim_name"
}

manual_hint() {
  echo "Install Neovim 0.9 or newer yourself (https://neovim.io/doc/install/), or point"
  echo "NEOVAIN_NVIM at an nvim binary you already have."
}

found="$(nvim_version)"
if new_enough "$found"; then
  echo "Found Neovim $found"
else
  if [ -n "$found" ]; then
    echo "Found Neovim $found, but neovain needs 0.9 or newer."
  else
    echo "Neovim was not found. neovain needs it (0.9 or newer)."
  fi
  if ask "Install the latest Neovim to $opt? It needs no sudo."; then
    install_nvim || manual_hint
  else
    manual_hint
  fi
fi

case ":$PATH:" in
  *":$dir:"*) ;;
  *)
    echo "$dir is not on your PATH. Add this line to your shell profile:"
    echo "  export PATH=\"$dir:\$PATH\""
    ;;
esac
