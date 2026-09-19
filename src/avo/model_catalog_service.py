"""Provider-neutral model catalog records and a bounded disk cache."""

from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CatalogSource(StrEnum):
    """Where a model catalog was obtained."""

    LIVE = "live"
    CACHE = "cache"
    STALE = "stale"
    STATIC = "static"


class _CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelCatalogEntry(_CatalogModel):
    """One normalized model exposed by a provider."""

    provider: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=256)
    source: CatalogSource
    capabilities: tuple[str, ...] = ()
    auth_requirement: str | None = Field(default=None, max_length=64)
    transport: str | None = Field(default=None, max_length=64)
    recommended: bool = False
    reason: str | None = Field(default=None, max_length=512)

    @field_validator("provider", "model_id", "label", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ModelCatalogResult(_CatalogModel):
    """A provider catalog plus freshness metadata."""

    provider: str = Field(min_length=1, max_length=64)
    models: tuple[ModelCatalogEntry, ...] = ()
    source: CatalogSource
    fetched_at: datetime | None = None
    stale: bool = False
    warning: str | None = Field(default=None, max_length=1024)

    @field_validator("provider", mode="before")
    @classmethod
    def _strip_provider(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("fetched_at")
    @classmethod
    def _normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        return value.astimezone(UTC)


_PROVIDER_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MAX_MODEL_ID_LENGTH = 256


def _validate_provider(provider: str) -> str:
    value = provider.strip().lower()
    if not _PROVIDER_KEY.fullmatch(value):
        raise ValueError(f"invalid provider catalog key: {provider!r}")
    return value


def normalize_model_ids(provider: str, raw: object) -> tuple[str, ...]:
    """Return bounded, deduplicated model IDs from a provider response."""

    _validate_provider(provider)
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return ()
    values: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        model_id = item.strip()
        if model_id and len(model_id) <= _MAX_MODEL_ID_LENGTH:
            values.add(model_id)
    return tuple(sorted(values))


class ModelCatalogCache:
    """Persist small provider catalogs without storing credentials."""

    def __init__(self, root: Path, *, ttl_seconds: int = 900, max_entries: int = 256) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.root = Path(root)
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries

    def _path(self, provider: str) -> Path:
        return self.root / f"{_validate_provider(provider)}.json"

    def load(self, provider: str) -> ModelCatalogResult | None:
        """Load a cached catalog, marking it stale when its TTL has elapsed."""

        path = self._path(provider)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            result = ModelCatalogResult.model_validate(raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if result.provider != provider.strip().lower():
            return None
        if len(result.models) > self.max_entries:
            return None
        now = datetime.now(UTC)
        stale = result.fetched_at is None or (
            (now - result.fetched_at).total_seconds() > self.ttl_seconds
        )
        warning = "cached model catalog is stale" if stale else None
        return result.model_copy(
            update={
                "source": CatalogSource.STALE if stale else CatalogSource.CACHE,
                "stale": stale,
                "warning": warning,
            }
        )

    def save(self, result: ModelCatalogResult) -> None:
        """Atomically save a catalog after enforcing its configured bound."""

        provider = _validate_provider(result.provider)
        if len(result.models) > self.max_entries:
            raise ValueError(f"model catalog exceeds max_entries={self.max_entries}")
        if any(entry.provider != provider for entry in result.models):
            raise ValueError("all catalog entries must match the result provider")

        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        target = self._path(provider)
        payload = result.model_dump(mode="json")
        payload["provider"] = provider
        payload["source"] = CatalogSource.LIVE.value
        payload["stale"] = False
        payload["warning"] = None
        if result.fetched_at is None:
            payload["fetched_at"] = datetime.now(UTC).isoformat()
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        fd, temp_name = tempfile.mkstemp(prefix=f".{provider}.", suffix=".tmp", dir=self.root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.write("\n")
            Path(temp_name).replace(target)
            os.chmod(target, 0o600)
        except BaseException:
            with suppress(OSError):
                os.unlink(temp_name)
            raise


def catalog_entries(
    provider: str,
    model_ids: tuple[str, ...],
    *,
    source: CatalogSource,
    recommended: str | None = None,
    capabilities: tuple[str, ...] = (),
    auth_requirement: str | None = None,
    transport: str | None = None,
) -> tuple[ModelCatalogEntry, ...]:
    """Build deterministic entries from normalized IDs."""

    provider_key = _validate_provider(provider)
    normalized = normalize_model_ids(provider_key, model_ids)
    return tuple(
        ModelCatalogEntry(
            provider=provider_key,
            model_id=model_id,
            label=model_id,
            source=source,
            capabilities=capabilities,
            auth_requirement=auth_requirement,
            transport=transport,
            recommended=model_id == recommended,
        )
        for model_id in normalized
    )


__all__ = [
    "CatalogSource",
    "ModelCatalogCache",
    "ModelCatalogEntry",
    "ModelCatalogResult",
    "catalog_entries",
    "normalize_model_ids",
]
