from __future__ import annotations

from avo.hardware import HardwareProfile
from avo.model_catalog import recommend_ollama_models


def test_recommendations_prioritize_installed_models_and_explain_fit() -> None:
    profile = HardwareProfile(
        platform="Linux",
        cpu_cores=8,
        ram_bytes=16 * 1024**3,
        gpu_vram_bytes=None,
        disk_free_bytes=100 * 1024**3,
    )

    recommendations = recommend_ollama_models(profile, installed=("qwen2.5-coder:7b",))

    assert recommendations
    first = recommendations[0]
    assert first.name == "qwen2.5-coder:7b"
    assert first.fit == "fits"
    assert "installed" in first.reason.lower()
    assert any(item.tool_calling for item in recommendations)


def test_low_memory_laptop_gets_small_model_as_fit() -> None:
    profile = HardwareProfile(
        platform="Linux",
        cpu_cores=4,
        ram_bytes=4 * 1024**3,
        gpu_vram_bytes=None,
        disk_free_bytes=20 * 1024**3,
    )

    recommendations = recommend_ollama_models(profile)

    assert recommendations[0].name in {"qwen2.5:3b", "llama3.2:3b"}
    assert recommendations[0].fit == "fits"
    assert any(item.fit == "too large" for item in recommendations)
