#!/usr/bin/env bash
# Install Avo as a user-global CLI through uv.
#
# Examples:
#   curl -fsSL https://raw.githubusercontent.com/Fqih/avo/main/install.sh | bash
#   bash install.sh --dry-run
#   AVO_PACKAGE='.[providers]' bash install.sh

set -euo pipefail

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

Install Avo as a user-global CLI using uv. No root access is required.

Options:
  --dry-run                 Print the plan without network or filesystem changes
  --package SPEC            Install SPEC instead of avo[all]
  --python VERSION          Use VERSION (default: 3.13)
  --no-update-shell         Do not add uv's tool bin directory to shell startup
  -h, --help                Show this help

Environment:
  AVO_PACKAGE               Package spec override (for example: .[providers])
  AVO_PYTHON_VERSION        Python version override
  AVO_UPDATE_SHELL=0        Skip uv tool update-shell
EOF
}

die() {
  printf 'Avo installer: %s\n' "$*" >&2
  exit 1
}

while (($# > 0)); do
  case "$1" in
    --dry-run) dry_run=1 ;;
    --package)
      (($# >= 2)) || die "--package requires a value"
      package_spec="$2"
      shift
      ;;
    --python)
      (($# >= 2)) || die "--python requires a value"
      python_version="$2"
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

case "$(uname -s)" in
  Linux*) platform="linux" ;;
  Darwin*) platform="macos" ;;
  MINGW*|MSYS*|CYGWIN*) platform="windows-git-bash" ;;
  *) die "unsupported operating system; use install.ps1 on native Windows" ;;
esac

printf 'Avo installer\n'
printf '  platform: %s\n' "$platform"
printf '  package: %s\n' "$package_spec"
printf '  python: %s\n' "$python_version"
if ((dry_run)); then
  printf '  mode: dry-run\n'
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
  printf '%s\n' '→ uv not found; installing the managed uv tool for this user ...'
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
