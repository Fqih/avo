"""Pluggable credential storage with a permission-protected fallback."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from avo.oauth.store import Credential


class CredentialBackend(Protocol):
    """Storage contract used by the OAuth compatibility facade."""

    def get(self, provider: str) -> Credential | None: ...

    def put(self, credential: Credential) -> Path: ...

    def delete(self, provider: str) -> bool: ...

    def list(self) -> tuple[Credential, ...]: ...

    def describe(self) -> str: ...


def _credential_class() -> type[Credential]:
    from avo.oauth.store import Credential

    return Credential


def _credential_from_raw(provider: str, value: object) -> Credential:
    credential_type = _credential_class()
    if isinstance(value, str):
        return credential_type(provider=provider, kind="api_key", access_token=value)
    if isinstance(value, dict):
        data = dict(value)
        data.setdefault("provider", provider)
        try:
            return credential_type.model_validate(data)
        except Exception:
            return credential_type(provider=provider, kind="api_key", access_token="")
    return credential_type(provider=provider, kind="api_key", access_token="")


def _credential_to_raw(credential: Credential) -> object:
    if credential.kind == "api_key":
        return credential.access_token or ""
    return credential.model_dump(mode="json", exclude_none=True) | {"kind": "oauth"}


class FileCredentialBackend:
    """Store credentials in JSON with restrictive permissions and atomic writes."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()

    def _load_raw(self) -> dict[str, object]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def list(self) -> tuple[Credential, ...]:
        records = [
            _credential_from_raw(str(provider).lower(), value)
            for provider, value in self._load_raw().items()
        ]
        return tuple(sorted(records, key=lambda item: item.provider))

    def get(self, provider: str) -> Credential | None:
        target = provider.strip().lower()
        return next((item for item in self.list() if item.provider == target), None)

    def put(self, credential: Credential) -> Path:
        current = {item.provider: item for item in self.list()}
        current[credential.provider.lower()] = credential
        self._write(current)
        return self.path

    def delete(self, provider: str) -> bool:
        target = provider.strip().lower()
        current = {item.provider: item for item in self.list()}
        if target not in current:
            return False
        del current[target]
        if current:
            self._write(current)
        else:
            with suppress(FileNotFoundError):
                self.path.unlink()
        return True

    def _write(self, credentials: Mapping[str, Credential]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        payload = {
            provider: _credential_to_raw(credential)
            for provider, credential in sorted(credentials.items())
        }
        encoded = json.dumps(payload, indent=2, ensure_ascii=True) + "\n"
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(encoded)
            Path(temp_name).replace(self.path)
            os.chmod(self.path, 0o600)
        except BaseException:
            with suppress(OSError):
                os.unlink(temp_name)
            raise

    def describe(self) -> str:
        return "file-permissions"


class EncryptedFileCredentialBackend:
    """Store credentials in a Fernet-encrypted, permission-protected file."""

    def __init__(self, path: Path, *, key: str | bytes | None = None) -> None:
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise ValueError(
                "encrypted credential storage requires the optional dependency; "
                "install `avo[security]` or use AVO_CREDENTIAL_BACKEND=keyring"
            ) from exc
        raw_key = key if key is not None else os.environ.get("AVO_CREDENTIAL_ENCRYPTION_KEY", "")
        if isinstance(raw_key, str):
            raw_key = raw_key.encode("ascii")
        if not raw_key:
            raise ValueError(
                "AVO_CREDENTIAL_ENCRYPTION_KEY is required for encrypted credential storage"
            )
        try:
            self._fernet: Any = Fernet(raw_key)
        except (TypeError, ValueError) as exc:
            raise ValueError("AVO_CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key") from exc
        self.path = Path(path).expanduser()

    def _load_raw(self) -> dict[str, object]:
        if not self.path.is_file():
            return {}
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
            token = envelope["ciphertext"]
            plaintext = self._fernet.decrypt(str(token).encode("ascii"))
            raw = json.loads(plaintext.decode("utf-8"))
        except (OSError, TypeError, ValueError, KeyError):
            return {}
        except Exception:
            return {}
        return raw if isinstance(raw, dict) else {}

    def list(self) -> tuple[Credential, ...]:
        records = [
            _credential_from_raw(str(provider).lower(), value)
            for provider, value in self._load_raw().items()
        ]
        return tuple(sorted(records, key=lambda item: item.provider))

    def get(self, provider: str) -> Credential | None:
        target = provider.strip().lower()
        return next((item for item in self.list() if item.provider == target), None)

    def put(self, credential: Credential) -> Path:
        current = {item.provider: item for item in self.list()}
        current[credential.provider.lower()] = credential
        self._write(current)
        return self.path

    def delete(self, provider: str) -> bool:
        target = provider.strip().lower()
        current = {item.provider: item for item in self.list()}
        if target not in current:
            return False
        del current[target]
        if current:
            self._write(current)
        else:
            with suppress(FileNotFoundError):
                self.path.unlink()
        return True

    def _write(self, credentials: Mapping[str, Credential]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        payload = {
            provider: _credential_to_raw(credential)
            for provider, credential in sorted(credentials.items())
        }
        plaintext = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        encoded = json.dumps(
            {"version": 1, "ciphertext": self._fernet.encrypt(plaintext).decode("ascii")},
            separators=(",", ":"),
        )
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
            Path(temp_name).replace(self.path)
            os.chmod(self.path, 0o600)
        except BaseException:
            with suppress(OSError):
                os.unlink(temp_name)
            raise

    def describe(self) -> str:
        return "encrypted-file"


class KeyringCredentialBackend:
    """Store credential records in an OS keyring-compatible module."""

    _INDEX = "__avo_provider_index__"

    def __init__(
        self,
        *,
        service: str = "avo",
        module: Any | None = None,
    ) -> None:
        if module is None:
            try:
                import keyring as keyring_module
            except ImportError as exc:
                raise ValueError(
                    "keyring backend requested but optional dependency is missing; "
                    "install `avo[keyring]` or use AVO_CREDENTIAL_BACKEND=file"
                ) from exc
            module = keyring_module
        self.service = service
        self.module: Any = module

    def _providers(self) -> set[str]:
        raw = self.module.get_password(self.service, self._INDEX)
        if not raw:
            return set()
        try:
            values = json.loads(raw)
        except (TypeError, ValueError):
            return set()
        return {str(value).lower() for value in values} if isinstance(values, list) else set()

    def _save_providers(self, providers: set[str]) -> None:
        if providers:
            self.module.set_password(
                self.service,
                self._INDEX,
                json.dumps(sorted(providers), separators=(",", ":")),
            )
        else:
            with suppress(Exception):
                self.module.delete_password(self.service, self._INDEX)

    def list(self) -> tuple[Credential, ...]:
        records: list[Credential] = []
        for provider in sorted(self._providers()):
            value = self.module.get_password(self.service, provider)
            if not value:
                continue
            try:
                raw = json.loads(value)
                records.append(_credential_from_raw(provider, raw))
            except (TypeError, ValueError):
                continue
        return tuple(records)

    def get(self, provider: str) -> Credential | None:
        target = provider.strip().lower()
        return next((item for item in self.list() if item.provider == target), None)

    def put(self, credential: Credential) -> Path:
        provider = credential.provider.lower()
        value = json.dumps(_credential_to_raw(credential), separators=(",", ":"))
        self.module.set_password(self.service, provider, value)
        providers = self._providers()
        providers.add(provider)
        self._save_providers(providers)
        return Path("[os-keyring]")

    def delete(self, provider: str) -> bool:
        target = provider.strip().lower()
        if target not in self._providers():
            return False
        self.module.delete_password(self.service, target)
        providers = self._providers()
        providers.discard(target)
        self._save_providers(providers)
        return True

    def describe(self) -> str:
        return "os-keyring"


def _auth_path(environ: Mapping[str, str]) -> Path:
    explicit = environ.get("AVO_AUTH_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    config = environ.get("AVO_CONFIG_DIR", "").strip()
    if config:
        return Path(config).expanduser().resolve() / "auth.json"
    xdg = environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return (base / "avo" / "auth.json").resolve()


def resolve_credential_backend(
    environ: Mapping[str, str] | None = None,
) -> CredentialBackend:
    """Resolve the credential backend without exposing any secret value."""

    env = os.environ if environ is None else environ
    choice = env.get("AVO_CREDENTIAL_BACKEND", "file").strip().lower() or "file"
    if choice == "file":
        return FileCredentialBackend(_auth_path(env))
    if choice == "keyring":
        return KeyringCredentialBackend()
    if choice in {"encrypted", "encrypted-file"}:
        return EncryptedFileCredentialBackend(
            _auth_path(env), key=env.get("AVO_CREDENTIAL_ENCRYPTION_KEY")
        )
    if choice == "auto":
        try:
            return KeyringCredentialBackend()
        except ValueError:
            return FileCredentialBackend(_auth_path(env))
    raise ValueError(
        "invalid AVO_CREDENTIAL_BACKEND; choose file, encrypted-file, keyring, or auto"
    )


__all__ = [
    "CredentialBackend",
    "EncryptedFileCredentialBackend",
    "FileCredentialBackend",
    "KeyringCredentialBackend",
    "resolve_credential_backend",
]
