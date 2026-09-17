#!/usr/bin/env bash

set -euo pipefail

# Build the public documentation and expose the canonical installers at the
# site root. Netlify runs this script; it also makes manual site/ uploads work.
mkdocs build --strict --clean "$@"
cp install.sh site/install.sh
cp install.ps1 site/install.ps1
chmod 0755 site/install.sh
chmod 0644 site/install.ps1
