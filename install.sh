#!/usr/bin/env bash
# Install Avo as a user-global standalone CLI (zero Python required)
# or via uv tool for source/package installs.
#
# Examples:
#   curl -fsSL https://avo.faqihhakim.tech/install.sh | bash
#   bash install.sh --dry-run
#   bash install.sh --version 0.7.4
#   bash install.sh --from-source --package '.[providers]'
#
# Environment:
#   AVO_VERSION               Version to install (default: latest)
#   AVO_INSTALL_DIR           Target binary directory (default: ~/.local/bin)
#   AVO_INSTALL_MODE          Install mode: standalone (default) or source
#   AVO_PACKAGE               Package spec for source mode (default: avo[all])
#   AVO_PYTHON_VERSION        Python version for source mode (default: 3.13)
#   AVO_UPDATE_SHELL=0        Skip updating shell PATH

set -euo pipefail

install_mode="${AVO_INSTALL_MODE:-standalone}"
version="${AVO_VERSION:-latest}"
bin_dir="${AVO_INSTALL_DIR:-$HOME/.local/bin}"
package_spec="${AVO_PACKAGE:-avo[all]}"
python_version="${AVO_PYTHON_VERSION:-3.13}"
dry_run=0
update_shell=1

case "${AVO_UPDATE_SHELL:-1}" in
  0|false|no|off) update_shell=0 ;;
esac

usage() {
  cat <<'EOF'
Usage: install.sh [options]

Install Avo as a user-global CLI. By default, installs a self-contained
standalone binary that does NOT require Python or any package manager.

Options:
  --dry-run                 Print the plan without network or filesystem changes
  --version VERSION         Install specific version (default: latest)
  --bin-dir DIR             Install binary to DIR (default: ~/.local/bin)
  --from-source             Install via uv tool using Python package instead of binary
  --package SPEC            Package spec for source mode (default: avo[all])
  --python VERSION          Python version for source mode (default: 3.13)
  --no-update-shell         Do not add bin directory to shell startup files
  -h, --help                Show this help

Environment:
  AVO_VERSION               Version override (for example: 0.7.3)
  AVO_INSTALL_DIR           Target directory override (default: ~/.local/bin)
  AVO_INSTALL_MODE          Mode: standalone (default) or source
  AVO_PACKAGE               Package spec override (for example: .[providers])
  AVO_PYTHON_VERSION        Python version override for source mode
  AVO_UPDATE_SHELL=0        Skip updating shell profile
EOF
}

die() {
  printf 'Avo installer error: %s\n' "$*" >&2
  exit 1
}

while (($# > 0)); do
  case "$1" in
    --dry-run) dry_run=1 ;;
    --version)
      (($# >= 2)) || die "--version requires a value"
      version="$2"
      shift
      ;;
    --bin-dir)
      (($# >= 2)) || die "--bin-dir requires a value"
      bin_dir="$2"
      shift
      ;;
    --from-source)
      install_mode="source"
      ;;
    --package)
      (($# >= 2)) || die "--package requires a value"
      package_spec="$2"
      install_mode="source"
      shift
      ;;
    --python)
      (($# >= 2)) || die "--python requires a value"
      python_version="$2"
      install_mode="source"
      shift
      ;;
    --no-update-shell) update_shell=0 ;;
    -h|--help)
      usage
      exit 0
      ;;
    *) die "unknown option: $1 (use --help)" ;;
  esac
  shift
done

# Detect Platform and Architecture
raw_os="$(uname -s)"
case "$raw_os" in
  Linux*) platform="linux" ;;
  Darwin*) platform="macos" ;;
  MINGW*|MSYS*|CYGWIN*) platform="windows" ;;
  *) die "unsupported operating system: $raw_os (use install.ps1 on native Windows)" ;;
esac

raw_arch="$(uname -m)"
case "$raw_arch" in
  x86_64|amd64) arch="x86_64" ;;
  aarch64|arm64)
    if [ "$platform" = "macos" ]; then
      arch="aarch64"
    else
      arch="aarch64"
    fi
    ;;
  *) die "unsupported architecture: $raw_arch" ;;
esac

target_triple="${platform}-${arch}"

# Format version tag
if [ "$version" != "latest" ] && [[ "$version" != v* ]]; then
  tag_name="v${version}"
  clean_version="${version}"
else
  tag_name="${version}"
  clean_version="${version#v}"
fi

printf 'Avo installer\n'
printf '  platform: %s (%s)\n' "$platform" "$arch"
printf '  mode: %s\n' "$install_mode"

# -----------------------------------------------------------------------------
# Mode 1: Standalone Binary (Zero Python Required)
# -----------------------------------------------------------------------------
if [ "$install_mode" = "standalone" ]; then
  archive_name="avo-${clean_version}-${target_triple}.tar.gz"
  if [ "$tag_name" = "latest" ]; then
    download_url="https://github.com/Fqih/avo/releases/latest/download/${archive_name}"
    checksum_url="https://github.com/Fqih/avo/releases/latest/download/${archive_name}.sha256"
  else
    download_url="https://github.com/Fqih/avo/releases/download/${tag_name}/${archive_name}"
    checksum_url="https://github.com/Fqih/avo/releases/download/${tag_name}/${archive_name}.sha256"
  fi

  printf '  target: standalone binary (no Python runtime required)\n'
  printf '  destination: %s/avo\n' "$bin_dir"
  printf '  download: %s\n' "$download_url"

  if ((dry_run)); then
    printf '  mode: dry-run (standalone binary, zero-python)\n'
    printf '  scope: user-global (no root / no python runtime required)\n'
    printf '  would ensure directory: %s\n' "$bin_dir"
    printf '  would download: %s\n' "$download_url"
    printf '  would extract: avo -> %s/avo\n' "$bin_dir"
    printf '  would chmod: 0755 %s/avo\n' "$bin_dir"
    if ((update_shell)); then
      printf '  would update shell profile if %s not in PATH\n' "$bin_dir"
    fi
    exit 0
  fi

  # Create install directory
  mkdir -p "$bin_dir"

  # Create temporary working directory
  tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/avo-install.XXXXXX")"
  trap 'rm -rf "$tmp_dir"' EXIT

  archive_file="$tmp_dir/$archive_name"

  printf '→ Downloading standalone binary archive from GitHub Releases ...\n'
  if command -v curl >/dev/null 2>&1; then
    if ! curl -fsSL "$download_url" -o "$archive_file"; then
      printf 'Notice: Release binary for %s not found on GitHub. Falling back to source mode via uv ...\n' "$target_triple"
      install_mode="source"
    fi
  elif command -v wget >/dev/null 2>&1; then
    if ! wget -qO "$archive_file" "$download_url"; then
      printf 'Notice: Release binary for %s not found on GitHub. Falling back to source mode via uv ...\n' "$target_triple"
      install_mode="source"
    fi
  else
    die "curl or wget is required to download the installer archive"
  fi

  if [ "$install_mode" = "standalone" ]; then
    checksum_file="$archive_file.sha256"
    printf '→ Verifying SHA-256 checksum ...\n'
    if command -v curl >/dev/null 2>&1; then
      if ! curl -fsSL "$checksum_url" -o "$checksum_file" 2>/dev/null; then
        die "Failed to download SHA-256 checksum file from $checksum_url. Aborting installation."
      fi
    elif command -v wget >/dev/null 2>&1; then
      if ! wget -qO "$checksum_file" "$checksum_url" 2>/dev/null; then
        die "Failed to download SHA-256 checksum file from $checksum_url. Aborting installation."
      fi
    fi

    if [ ! -f "$checksum_file" ] || [ ! -s "$checksum_file" ]; then
      die "Checksum file is missing or empty. Aborting installation."
    fi

    expected_hash="$(awk '{print $1}' "$checksum_file")"
    if [ -z "$expected_hash" ]; then
      die "Could not parse expected SHA-256 hash from $checksum_file. Aborting installation."
    fi

    actual_hash=""
    if command -v sha256sum >/dev/null 2>&1; then
      actual_hash="$(sha256sum "$archive_file" | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
      actual_hash="$(shasum -a 256 "$archive_file" | awk '{print $1}')"
    else
      die "Neither sha256sum nor shasum is available to verify archive integrity. Aborting installation."
    fi

    if [ -z "$actual_hash" ]; then
      die "Failed to calculate SHA-256 hash for $archive_file. Aborting installation."
    fi

    if [ "$actual_hash" != "$expected_hash" ]; then
      die "Checksum verification failed! Expected ${expected_hash}, got ${actual_hash}. Aborting installation."
    fi
    printf '✓ SHA-256 checksum verified (%s)\n' "$actual_hash"

    printf '→ Extracting %s ...\n' "$archive_name"
    tar -xzf "$archive_file" -C "$tmp_dir"

    if [ ! -f "$tmp_dir/avo" ]; then
      die "Archive did not contain the 'avo' binary"
    fi

    cp "$tmp_dir/avo" "$bin_dir/avo"
    chmod 0755 "$bin_dir/avo"

    # Shell PATH update
    if ((update_shell)) && [[ ":$PATH:" != *":$bin_dir:"* ]]; then
      printf '→ Adding %s to shell profile ...\n' "$bin_dir"
      shell_profile=""
      case "${SHELL:-}" in
        */zsh) shell_profile="$HOME/.zshrc" ;;
        */bash)
          if [ -f "$HOME/.bashrc" ]; then
            shell_profile="$HOME/.bashrc"
          elif [ -f "$HOME/.bash_profile" ]; then
            shell_profile="$HOME/.bash_profile"
          fi
          ;;
        *) shell_profile="$HOME/.profile" ;;
      esac

      if [ -n "$shell_profile" ] && [ -w "$shell_profile" ]; then
        if ! grep -qs 'export PATH=".*'"$bin_dir"'.*"' "$shell_profile"; then
          printf '\n# Added by Avo standalone installer\nexport PATH="%s:$PATH"\n' "$bin_dir" >> "$shell_profile"
          printf '✓ Added PATH entry to %s\n' "$shell_profile"
        fi
      fi
    fi

    printf '\n✓ Avo standalone CLI installed successfully!\n'
    printf 'Binary location: %s/avo\n' "$bin_dir"
    printf 'Run: avo --version\n'
    if [[ ":$PATH:" != *":$bin_dir:"* ]]; then
      printf '\nNotice: %s is not in your current PATH.\n' "$bin_dir"
      printf 'Restart your shell or run: export PATH="%s:$PATH"\n' "$bin_dir"
    fi
    exit 0
  fi
fi

# -----------------------------------------------------------------------------
# Mode 2: Source / Python Package via uv (Fallback or Explicit)
# -----------------------------------------------------------------------------
printf '  package: %s\n' "$package_spec"
printf '  python: %s\n' "$python_version"

if ((dry_run)); then
  printf '  mode: dry-run (source / uv tool)\n'
  printf '  scope: user-global (no root access)\n'
  printf '  would ensure: uv\n'
  printf '  would run: uv python install %s\n' "$python_version"
  printf '  would run: uv tool install --force --python %s %s\n' "$python_version" "$package_spec"
  if ((update_shell)); then
    printf '  would run: uv tool update-shell\n'
  fi
  exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' '→ uv not found; installing managed uv tool for this user ...'
  uv_installer="$(mktemp "${TMPDIR:-/tmp}/avo-uv-install.XXXXXX")"
  trap 'rm -f "$uv_installer"' EXIT
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL https://astral.sh/uv/install.sh -o "$uv_installer"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$uv_installer" https://astral.sh/uv/install.sh
  else
    die "curl or wget is required to install uv"
  fi
  sh "$uv_installer"
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

command -v uv >/dev/null 2>&1 || die "uv was installed but is not on PATH; restart the shell and retry"

printf '%s\n' "→ Ensuring Python $python_version is available ..."
uv python install "$python_version"
printf '%s\n' "→ Installing Avo user-global CLI ..."
uv tool install --force --python "$python_version" "$package_spec"

if ((update_shell)); then
  printf '%s\n' '→ Updating shell PATH for uv tools ...'
  uv tool update-shell
fi

printf '\nAvo installed successfully as a user-global command.\n'
printf 'Run: avo --version\n'
if ((update_shell)); then
  printf 'If `avo` is not found in this shell, restart it or source your shell profile.\n'
fi
