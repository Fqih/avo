"""Plaintext JSON credential records protected by restrictive file permissions."""

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
    from avo.credentials import resolve_credential_backend

    return {credential.provider: credential for credential in resolve_credential_backend().list()}


def get_credential(provider: str) -> Credential | None:
    from avo.credentials import resolve_credential_backend

    return resolve_credential_backend().get(provider)


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
    from avo.credentials import resolve_credential_backend

    return resolve_credential_backend().put(cred)


def remove_credential(provider: str) -> bool:
    from avo.credentials import resolve_credential_backend

    return resolve_credential_backend().delete(provider)
