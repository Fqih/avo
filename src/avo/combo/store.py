"""JSON file persistence for combo profiles (combos.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from avo.auth import default_auth_dir
from avo.combo.models import ComboCatalog, ComboProfile, ComboTier
from avo.models import utc_now

COMBOS_FILE_ENV = "AVO_COMBOS_FILE"

BUILTIN_COMBOS: dict[str, ComboProfile] = {
    "default": ComboProfile(
        name="default",
        description="Subscription Claude -> OpenRouter Llama -> Local Ollama",
        tiers=[
            ComboTier(name="subscription", provider="claude", model="claude-sonnet-5"),
            ComboTier(
                name="cheap",
                provider="openrouter",
                model="meta-llama/llama-3.3-70b-instruct",
            ),
            ComboTier(name="free", provider="ollama", model="llama3.2"),
        ],
    ),
    "coder": ComboProfile(
        name="coder",
        description="Claude Sonnet -> OpenRouter Llama -> Local Qwen Coder",
        tiers=[
            ComboTier(name="subscription", provider="claude", model="claude-sonnet-5"),
            ComboTier(
                name="cheap",
                provider="openrouter",
                model="meta-llama/llama-3.3-70b-instruct",
            ),
            ComboTier(name="free", provider="ollama", model="qwen2.5-coder:7b"),
        ],
    ),
    "budget": ComboProfile(
        name="budget",
        description="OpenRouter Llama -> Local Ollama",
        tiers=[
            ComboTier(
                name="cheap",
                provider="openrouter",
                model="meta-llama/llama-3.3-70b-instruct",
            ),
            ComboTier(name="free", provider="ollama", model="llama3.2"),
        ],
    ),
}


def combos_file_path() -> Path:
    """Return the resolved path to ~/.config/avo/combos.json."""

    explicit = os.environ.get(COMBOS_FILE_ENV)
    if explicit:
        return Path(explicit).expanduser().resolve()
    return default_auth_dir() / "combos.json"


def _read_catalog() -> ComboCatalog:
    path = combos_file_path()
    if not path.is_file():
        return ComboCatalog()
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        return ComboCatalog.model_validate(data)
    except Exception:
        return ComboCatalog()


def _write_catalog(catalog: ComboCatalog) -> None:
    path = combos_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    data = catalog.model_dump(mode="json")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)
    path.chmod(0o600)


def load_combos() -> dict[str, ComboProfile]:
    """Return all available combos (built-ins merged with user-defined)."""

    combos = dict(BUILTIN_COMBOS)
    catalog = _read_catalog()
    combos.update(catalog.combos)
    return combos


def get_combo(name: str) -> ComboProfile | None:
    """Look up a combo profile by name."""

    combos = load_combos()
    return combos.get(name)


def save_combo(profile: ComboProfile) -> None:
    """Save or update a custom combo profile in combos.json."""

    catalog = _read_catalog()
    now = utc_now()
    updated = profile.model_copy(update={"updated_at": now})
    catalog.combos[profile.name] = updated
    _write_catalog(catalog)


def delete_combo(name: str) -> bool:
    """Delete a custom combo profile. Returns True if removed, False otherwise."""

    catalog = _read_catalog()
    if name in catalog.combos:
        del catalog.combos[name]
        _write_catalog(catalog)
        return True
    return False
