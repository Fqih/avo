"""Tests for the optional :mod:`avo_native` extension integration."""

from __future__ import annotations

import importlib

import pytest

from avo import _native
from avo.providers.prompt_cache import cache_key_for_request


def test_native_loader_succeeds_when_wheel_installed() -> None:
    # Smoke test: if the wheel is installed, the extension must load
    # cleanly. Otherwise the loader exposes ``is_available() == False``
    # so callers can fall back.
    if _native.is_available():
        assert _native.version()  # non-empty
        assert _native.import_error() is None
    else:
        assert _native.import_error() is not None


@pytest.mark.parametrize(
    "messages",
    [
        [{"role": "user", "content": "hi"}],
        [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "follow up"},
        ],
    ],
)
def test_cache_key_matches_between_python_and_native(messages: list[dict[str, str]]) -> None:
    """Python fallback and native hash must agree on identical input."""

    from avo.providers.prompt_cache import _native_cache_key_hash, _serialise_repr

    native = _native_cache_key_hash()
    if native is None:  # pragma: no cover - wheel missing in CI
        pytest.skip("avo_native wheel not installed")
    fragments = [_serialise_repr(message) for message in messages]
    native_key = native("run-1", 1, fragments)
    py_key = cache_key_for_request(run_id="run-1", step=1, messages=messages)
    assert native_key == py_key


def test_cache_key_changes_when_input_changes() -> None:
    base = cache_key_for_request(run_id="r", step=1, messages=[{"role": "user", "content": "a"}])
    other = cache_key_for_request(run_id="r", step=1, messages=[{"role": "user", "content": "b"}])
    assert base != other


def test_native_module_can_be_reloaded() -> None:
    """Force a reload to exercise the lazy-import path twice."""

    importlib.reload(_native)
    # The reload may or may not re-raise the original ImportError; either
    # way, the loader stays consistent.
    assert isinstance(_native.is_available(), bool)
