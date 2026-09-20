"""Entrypoint for `python -m avo`."""

from __future__ import annotations

import sys

from avo.cli import main

if __name__ == "__main__":
    sys.exit(main())
