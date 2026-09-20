#!/usr/bin/env bash
# Mirrors root install.sh for canonical standalone installation.
exec "$(dirname "$0")/../install.sh" "$@"
