"""Typed credential records (auth.json v2) with v1 string compatibility."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from avo.auth import AuthError, auth_file_path


class Credential(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    kind: Literal["api_key", "oauth"]
    access_token: str | None = Field(default=None, repr=False)
    refresh_token: str | None = Field(default=None, repr=False)
    expires_at: datetime | None = None
    obtained_at: datetime | None = None
    last_refresh_at: datetime | None = None
    account: str | None = None
    scope: str | None = None
    subscription: bool = False

    def secret(self) -> str:
        token = self.access_token
        if not token:
            raise AuthError(f"credential for {self.provider!r} has no access token")
        return token


def _from_raw(provider: str, value: object) -> Credential:
    if isinstance(value, str):
        return Credential(provider=provider, kind="api_key", access_token=value)
    if isinstance(value, dict):
        data = dict(value)
        data.setdefault("provider", provider)
        try:
            return Credential.model_validate(data)
        except Exception:
            return Credential(provider=provider, kind="api_key", access_token="")
    return Credential(provider=provider, kind="api_key", access_token="")


def _to_raw(cred: Credential) -> object:
    if cred.kind == "api_key":
        return cred.access_token or ""
    return cred.model_dump(mode="json", exclude_none=True) | {"kind": "oauth"}


def load_all_credentials() -> dict[str, Credential]:
    path = auth_file_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).lower(): _from_raw(str(k).lower(), v) for k, v in data.items()}


def get_credential(provider: str) -> Credential | None:
    return load_all_credentials().get(provider.lower())


def _write_raw(data: dict[str, object]) -> Path:
    target = auth_file_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    raw = json.dumps(
        {k: _to_raw(v) if isinstance(v, Credential) else v for k, v in data.items()},
        indent=2,
    ).encode("utf-8")
    fd = os.open(target, flags, 0o600)
    try:
        with open(fd, "wb", closefd=False) as fh:
            fh.write(raw)
    finally:
        os.close(fd)
    os.chmod(target, 0o600)
    return target


def store_credential(cred: Credential) -> Path:
    current = load_all_credentials()
    current[cred.provider.lower()] = cred
    return _write_raw(dict(current))


def remove_credential(provider: str) -> bool:
    current = load_all_credentials()
    if provider.lower() not in current:
        return False
    del current[provider.lower()]
    if not current:
        target = auth_file_path()
        if target.is_file():
            target.unlink()
        return True
    _write_raw(dict(current))
    return True
