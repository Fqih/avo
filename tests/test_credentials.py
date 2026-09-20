from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest


def _credential(provider: str = "openai"):
    from avo.oauth.store import Credential

    return Credential(
        provider=provider,
        kind="api_key",
        access_token="secret-token",
        obtained_at=datetime.now(UTC),
    )


def test_file_backend_round_trip_is_private_and_atomic(tmp_path: Path) -> None:
    from avo.credentials import FileCredentialBackend

    path = tmp_path / "auth.json"
    backend = FileCredentialBackend(path)

    backend.put(_credential())

    assert backend.get("openai").secret() == "secret-token"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0
    assert backend.describe() == "file-permissions"


def test_file_backend_migrates_legacy_string_records(tmp_path: Path) -> None:
    from avo.credentials import FileCredentialBackend

    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"openrouter": "sk-old"}), encoding="utf-8")
    backend = FileCredentialBackend(path)

    loaded = backend.get("openrouter")

    assert loaded is not None
    assert loaded.kind == "api_key"
    assert loaded.secret() == "sk-old"


def test_keyring_backend_round_trip_and_index() -> None:
    from avo.credentials import KeyringCredentialBackend

    class FakeKeyring:
        values: ClassVar[dict[tuple[str, str], str]] = {}

        @classmethod
        def get_password(cls, service: str, username: str) -> str | None:
            return cls.values.get((service, username))

        @classmethod
        def set_password(cls, service: str, username: str, password: str) -> None:
            cls.values[(service, username)] = password

        @classmethod
        def delete_password(cls, service: str, username: str) -> None:
            cls.values.pop((service, username), None)

    backend = KeyringCredentialBackend(module=FakeKeyring)
    backend.put(_credential("codex"))

    loaded = backend.get("codex")

    assert loaded is not None
    assert loaded.secret() == "secret-token"
    assert [item.provider for item in backend.list()] == ["codex"]
    assert backend.describe() == "os-keyring"
    assert backend.delete("codex") is True
    assert backend.get("codex") is None


def test_encrypted_file_backend_round_trip_does_not_store_plaintext(tmp_path: Path) -> None:
    from cryptography.fernet import Fernet

    from avo.credentials import EncryptedFileCredentialBackend

    path = tmp_path / "auth.enc"
    backend = EncryptedFileCredentialBackend(path, key=Fernet.generate_key())

    backend.put(_credential("anthropic"))

    assert backend.get("anthropic").secret() == "secret-token"
    assert "secret-token" not in path.read_text(encoding="utf-8")
    assert backend.describe() == "encrypted-file"


def test_resolver_supports_encrypted_file_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    from avo.credentials import EncryptedFileCredentialBackend, resolve_credential_backend

    monkeypatch.setenv("AVO_CREDENTIAL_BACKEND", "encrypted-file")
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AVO_CREDENTIAL_ENCRYPTION_KEY", key)
    backend = resolve_credential_backend(
        {
            "AVO_CREDENTIAL_BACKEND": "encrypted-file",
            "AVO_CREDENTIAL_ENCRYPTION_KEY": key,
            "AVO_AUTH_FILE": str(tmp_path / "auth.enc"),
        }
    )

    assert isinstance(backend, EncryptedFileCredentialBackend)


def test_resolver_rejects_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    from avo.credentials import resolve_credential_backend

    monkeypatch.setenv("AVO_CREDENTIAL_BACKEND", "vault-that-does-not-exist")

    with pytest.raises(ValueError, match="AVO_CREDENTIAL_BACKEND"):
        resolve_credential_backend()


def test_resolver_defaults_to_file_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    from avo.credentials import FileCredentialBackend, resolve_credential_backend

    monkeypatch.delenv("AVO_CREDENTIAL_BACKEND", raising=False)

    assert isinstance(resolve_credential_backend(), FileCredentialBackend)
