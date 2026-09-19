from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def test_normalize_model_ids_deduplicates_and_rejects_unbounded_values() -> None:
    from avo.model_catalog_service import normalize_model_ids

    assert normalize_model_ids(
        "openai",
        [" gpt-5 ", "gpt-5", "", 42, "claude", "x" * 300],
    ) == ("claude", "gpt-5")


def test_catalog_cache_round_trips_without_secret_fields(tmp_path: Path) -> None:
    from avo.model_catalog_service import (
        CatalogSource,
        ModelCatalogCache,
        ModelCatalogEntry,
        ModelCatalogResult,
    )

    result = ModelCatalogResult(
        provider="openai",
        models=(
            ModelCatalogEntry(
                provider="openai",
                model_id="gpt-5",
                label="GPT-5",
                source=CatalogSource.LIVE,
                capabilities=("text",),
                recommended=True,
                reason="account catalog",
            ),
        ),
        source=CatalogSource.LIVE,
        fetched_at=datetime.now(UTC),
    )
    cache = ModelCatalogCache(tmp_path)

    cache.save(result)
    loaded = cache.load("openai")

    assert loaded is not None
    assert loaded.models == result.models
    assert loaded.source is CatalogSource.CACHE
    raw = (tmp_path / "openai.json").read_text(encoding="utf-8")
    assert "authorization" not in raw.lower()
    assert "api_key" not in raw.lower()
    assert "access_token" not in raw.lower()


def test_expired_cache_is_marked_stale(tmp_path: Path) -> None:
    from avo.model_catalog_service import (
        CatalogSource,
        ModelCatalogCache,
        ModelCatalogEntry,
        ModelCatalogResult,
    )

    result = ModelCatalogResult(
        provider="ollama",
        models=(
            ModelCatalogEntry(
                provider="ollama",
                model_id="qwen2.5:7b",
                label="qwen2.5:7b",
                source=CatalogSource.LIVE,
            ),
        ),
        source=CatalogSource.LIVE,
        fetched_at=datetime.now(UTC) - timedelta(hours=2),
    )
    cache = ModelCatalogCache(tmp_path, ttl_seconds=60)
    cache.save(result)

    loaded = cache.load("ollama")

    assert loaded is not None
    assert loaded.stale is True
    assert loaded.source is CatalogSource.STALE


def test_catalog_entry_preserves_capability_and_auth_metadata() -> None:
    from avo.model_catalog_service import CatalogSource, ModelCatalogEntry

    entry = ModelCatalogEntry(
        provider="codex",
        model_id="gpt-5.6-sol",
        label="GPT-5.6 Sol",
        source=CatalogSource.LIVE,
        capabilities=("text", "tools"),
        auth_requirement="oauth",
        transport="openai-compatible",
    )

    assert entry.capabilities == ("text", "tools")
    assert entry.auth_requirement == "oauth"
    assert entry.transport == "openai-compatible"


def test_cache_rejects_path_traversal_provider(tmp_path: Path) -> None:
    from avo.model_catalog_service import ModelCatalogCache

    with pytest.raises(ValueError, match="provider"):
        ModelCatalogCache(tmp_path).load("../secrets")


def test_cache_limits_models_to_configured_bound(tmp_path: Path) -> None:
    from avo.model_catalog_service import CatalogSource, ModelCatalogCache, ModelCatalogResult

    result = ModelCatalogResult(
        provider="openrouter",
        models=tuple(
            {
                "provider": "openrouter",
                "model_id": f"model-{index}",
                "label": f"Model {index}",
                "source": CatalogSource.LIVE,
            }
            for index in range(4)
        ),
        source=CatalogSource.LIVE,
    )
    cache = ModelCatalogCache(tmp_path, max_entries=2)

    with pytest.raises(ValueError, match="max_entries"):
        cache.save(result)
