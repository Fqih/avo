"""Lazy loader for the optional :mod:`avo_native` extension.

The Rust extension provides a streaming SHA-256 implementation of the
cache-key digest used by :func:`avo.providers.prompt_cache.cache_key_for_request`.
Falling back to the pure-Python implementation when the extension is
absent is mandatory — the runtime must remain operational on platforms
where the binary wheel is not yet available.

Importing this module is safe in any environment. The first call to
:func:`cache_key_hash_native` either resolves to a real function or
raises so callers can take the Python fallback.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_cache_key_hash: Callable[..., str] | None = None
_ext_version: str | None = None
_import_error: BaseException | None = None


def _try_load() -> None:
    """Load the native module into the module-level slots."""

    global _cache_key_hash, _ext_version, _import_error
    try:
        import avo_native as _ext
    except ImportError as exc:
        _import_error = exc
        return
    _cache_key_hash = _ext.cache_key_hash
    _ext_version = _ext.version


_try_load()


def is_available() -> bool:
    """Return ``True`` when the :mod:`avo_native` extension loaded."""

    return _cache_key_hash is not None


def version() -> str | None:
    """Return the bundled extension version, or ``None`` if not loaded."""

    return _ext_version


def import_error() -> BaseException | None:
    """Return the :class:`ImportError` raised at load time, if any."""

    return _import_error


def cache_key_hash_native(run_id: str, step: int, messages_json: list[str]) -> str:
    """Dispatch to the native hash when available, else raise.

    Callers should check :func:`is_available` first and fall back to
    :func:`avo.providers.prompt_cache.cache_key_for_request` when the
    extension did not load.
    """

    if _cache_key_hash is None:  # pragma: no cover - defensive
        raise RuntimeError("avo_native extension is not loaded")
    return _cache_key_hash(run_id, step, messages_json)


def __getattr__(name: str) -> Any:
    """Re-export ``avo_native`` symbols on demand for test introspection."""

    if name in {"cache_key_hash", "version"}:
        import avo_native as _ext

        return getattr(_ext, name)
    raise AttributeError(name)


__all__: list[str] = [
    "cache_key_hash_native",
    "import_error",
    "is_available",
    "version",
]
