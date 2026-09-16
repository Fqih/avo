"""Import credentials from official vendor CLIs."""

from __future__ import annotations

from avo.oauth.store import Credential


def find_importable(store_key: str) -> Credential | None:
    """Find importable credentials from official vendor CLIs if present."""

    return None
