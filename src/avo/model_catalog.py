"""Curated, conservative Ollama model recommendations."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from avo.hardware import HardwareProfile


@dataclass(frozen=True, slots=True)
class ModelRecommendation:
    name: str
    purpose: str
    download_bytes: int
    min_ram_bytes: int
    recommended_vram_bytes: int | None
    tool_calling: bool
    context_tokens: int
    fit: str
    reason: str


@dataclass(frozen=True, slots=True)
class _ModelSpec:
    name: str
    purpose: str
    download_bytes: int
    min_ram_bytes: int
    recommended_vram_bytes: int | None
    tool_calling: bool
    context_tokens: int


_CATALOG: tuple[_ModelSpec, ...] = (
    _ModelSpec(
        "qwen2.5:3b", "small general assistant", 2_000_000_000, 4 * 1024**3, None, True, 32768
    ),
    _ModelSpec(
        "llama3.2:3b", "small general assistant", 2_000_000_000, 4 * 1024**3, None, True, 8192
    ),
    _ModelSpec(
        "qwen3-vl:4b", "vision and general tasks", 3_500_000_000, 6 * 1024**3, None, True, 32768
    ),
    _ModelSpec(
        "qwen2.5-coder:7b",
        "coding and tool use",
        4_700_000_000,
        10 * 1024**3,
        8 * 1024**3,
        True,
        32768,
    ),
    _ModelSpec(
        "qwen3.5:9b",
        "strong general and coding",
        6_600_000_000,
        12 * 1024**3,
        8 * 1024**3,
        True,
        32768,
    ),
    _ModelSpec(
        "gemma4:e4b",
        "compact general assistant",
        9_600_000_000,
        14 * 1024**3,
        8 * 1024**3,
        True,
        32768,
    ),
)


def _fit(spec: _ModelSpec, profile: HardwareProfile) -> tuple[str, str]:
    if profile.ram_bytes and profile.ram_bytes < spec.min_ram_bytes:
        return "too large", f"needs about {spec.min_ram_bytes / 1024**3:.0f} GB RAM"
    if profile.disk_free_bytes is not None and profile.disk_free_bytes < spec.download_bytes:
        return "too large", "not enough free disk space for the download"
    if spec.recommended_vram_bytes and profile.vram_bytes is not None:
        if profile.vram_bytes >= spec.recommended_vram_bytes:
            return "fits", "recommended GPU memory is available"
        return "marginal", "works on CPU or shared memory, but may be slow"
    if spec.min_ram_bytes >= 12 * 1024**3:
        return "marginal", "fits the detected RAM but may be slow without a GPU"
    return "fits", "fits the detected local resources"


def recommend_ollama_models(
    profile: HardwareProfile,
    *,
    installed: Collection[str] = (),
) -> tuple[ModelRecommendation, ...]:
    """Return deterministic recommendations; this function never downloads."""

    installed_set = set(installed)
    result: list[ModelRecommendation] = []
    for spec in _CATALOG:
        fit, reason = _fit(spec, profile)
        if spec.name in installed_set:
            reason = f"already installed; {reason}"
        result.append(
            ModelRecommendation(
                name=spec.name,
                purpose=spec.purpose,
                download_bytes=spec.download_bytes,
                min_ram_bytes=spec.min_ram_bytes,
                recommended_vram_bytes=spec.recommended_vram_bytes,
                tool_calling=spec.tool_calling,
                context_tokens=spec.context_tokens,
                fit=fit,
                reason=reason,
            )
        )

    fit_order = {"fits": 0, "marginal": 1, "too large": 2}
    result.sort(
        key=lambda item: (
            0 if item.name in installed_set else 1,
            fit_order[item.fit],
            item.download_bytes,
            item.name,
        )
    )
    return tuple(result)


__all__ = ["ModelRecommendation", "recommend_ollama_models"]
