#!/usr/bin/env bash
# ==============================================================================
# Avo One-Line Installer
# Usage: curl -fsSL https://avo.run/install.sh | bash
# ==============================================================================

set -euo pipefail

AVO_VERSION="${AVO_VERSION:-latest}"
INSTALL_DIR="${HOME}/.local/bin"
DATA_DIR="${HOME}/.local/share/avo"
VENV_DIR="${DATA_DIR}/venv"

COLOR_RESET="\033[0m"
COLOR_GREEN="\033[1;32m"
COLOR_CYAN="\033[1;36m"
COLOR_RED="\033[1;31m"
COLOR_YELLOW="\033[1;33m"

echo -e "${COLOR_CYAN}🥑 Installing Avo Autonomous Engineering Agent...${COLOR_RESET}"

# Check architecture and OS
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
    Linux*)     PLATFORM="linux";;
    Darwin*)    PLATFORM="macos";;
    *)          echo -e "${COLOR_RED}Unsupported OS: $OS${COLOR_RESET}"; exit 1;;
esac

mkdir -p "$INSTALL_DIR"
mkdir -p "$DATA_DIR"

# 1. Prefer uv if installed
if command -v uv >/dev/null 2>&1; then
    echo -e "${COLOR_GREEN}✓ Found uv tool manager. Installing Avo via uv tool...${COLOR_RESET}"
    if [ "$AVO_VERSION" = "latest" ]; then
        uv tool install --force --upgrade avo-ai
    else
        uv tool install --force --upgrade "avo-ai==$AVO_VERSION"
    fi
# 2. Fall back to pipx if installed
elif command -v pipx >/dev/null 2>&1; then
    echo -e "${COLOR_GREEN}✓ Found pipx. Installing Avo via pipx...${COLOR_RESET}"
    if [ "$AVO_VERSION" = "latest" ]; then
        pipx install --force avo-ai
    else
        pipx install --force "avo-ai==$AVO_VERSION"
    fi
# 3. Fall back to isolated Python 3.12+ virtualenv
elif command -v python3 >/dev/null 2>&1; then
    PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

    if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
        echo -e "${COLOR_RED}Python 3.11+ required. Detected Python $PY_VER.${COLOR_RESET}"
        exit 1
    fi

    echo -e "${COLOR_GREEN}✓ Creating isolated virtual environment in ${VENV_DIR}...${COLOR_RESET}"
    rm -rf "$VENV_DIR"
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    if [ "$AVO_VERSION" = "latest" ]; then
        "$VENV_DIR/bin/pip" install --quiet --upgrade avo-ai
    else
        "$VENV_DIR/bin/pip" install --quiet --upgrade "avo-ai==$AVO_VERSION"
    fi

    # Symlink to ~/.local/bin/avo
    ln -sf "$VENV_DIR/bin/avo" "$INSTALL_DIR/avo"
else
    echo -e "${COLOR_RED}No Python 3, uv, or pipx detected. Please install Python 3.12+ or uv first.${COLOR_RESET}"
    exit 1
fi

# Ensure ~/.local/bin is in PATH
if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    echo -e "\n${COLOR_YELLOW}Notice: $INSTALL_DIR is not in your current PATH.${COLOR_RESET}"
    echo -e "Add this line to your ~/.bashrc, ~/.zshrc, or profile:"
    echo -e "  ${COLOR_CYAN}export PATH=\"\$HOME/.local/bin:\$PATH\"${COLOR_RESET}\n"
fi

if command -v "$INSTALL_DIR/avo" >/dev/null 2>&1; then
    INSTALLED_VER=$("$INSTALL_DIR/avo" --version 2>/dev/null || echo "installed")
    echo -e "${COLOR_GREEN}✓ Avo successfully installed: ${INSTALLED_VER}${COLOR_RESET}"
    echo -e "Run ${COLOR_CYAN}avo${COLOR_RESET} to start coding!"
else
    echo -e "${COLOR_GREEN}✓ Avo installed to $INSTALL_DIR/avo${COLOR_RESET}"
fi
