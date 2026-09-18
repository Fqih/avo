"""Ollama discovery and explicitly-confirmed local model downloads."""

from __future__ import annotations

import inspect
import json
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from avo.hardware import detect_hardware
from avo.model_catalog import recommend_ollama_models


@dataclass(frozen=True, slots=True)
class OllamaModel:
    name: str
    size_bytes: int | None = None
    modified_at: str | None = None
    digest: str | None = None


@dataclass(frozen=True, slots=True)
class OllamaHealth:
    available: bool
    version: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class PullPlan:
    model: str
    download_bytes: int | None
    already_installed: bool


@dataclass(frozen=True, slots=True)
class PullProgress:
    model: str
    status: str
    completed_bytes: int | None = None
    total_bytes: int | None = None
    detail: str | None = None


class OllamaPullRefused(RuntimeError):
    """Raised when the operator declines a model download."""


def _sync_request(method: str, url: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    hostname = urllib.parse.urlparse(url).hostname
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        response_context = opener.open(request, timeout=5)
    else:
        response_context = urllib.request.urlopen(request, timeout=5)  # nosec B310
    with response_context as response:
        if method == "GET":
            return json.loads(response.read().decode("utf-8"))
        return [line.decode("utf-8", errors="replace") for line in response]


class OllamaManager:
    """Own discovery and pull operations, separate from inference."""

    def __init__(self, base_url: str = "http://localhost:11434", *, client: Any = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        if self._client is None:
            # The manager is also used by the synchronous CLI.  Keep the
            # dependency-free fallback direct and bounded by urllib's socket
            # timeout; injected async clients remain fully non-blocking.
            return _sync_request(method.upper(), url, kwargs.get("json"))
        call = getattr(self._client, method.lower())
        result = call(url, **kwargs)
        return await result if inspect.isawaitable(result) else result

    @staticmethod
    async def _response_json(response: Any) -> Any:
        if isinstance(response, (dict, list)):
            return response
        status = int(getattr(response, "status_code", 200))
        if status >= 400:
            raise RuntimeError(f"Ollama returned HTTP {status}")
        value = response.json()
        return await value if inspect.isawaitable(value) else value

    async def list_models(self) -> tuple[OllamaModel, ...]:
        response = await self._call("get", "/api/tags")
        payload = await self._response_json(response)
        raw_models = payload.get("models", []) if isinstance(payload, dict) else []
        models: list[OllamaModel] = []
        for raw in raw_models:
            if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
                continue
            models.append(
                OllamaModel(
                    name=str(raw["name"]),
                    size_bytes=(
                        int(raw["size"]) if isinstance(raw.get("size"), (int, float)) else None
                    ),
                    modified_at=str(raw["modified_at"]) if raw.get("modified_at") else None,
                    digest=str(raw["digest"]) if raw.get("digest") else None,
                )
            )
        return tuple(models)

    async def check_health(self) -> OllamaHealth:
        try:
            response = await self._call("get", "/api/version")
            payload = await self._response_json(response)
            version = payload.get("version") if isinstance(payload, dict) else None
            return OllamaHealth(True, str(version) if version else None)
        except Exception as exc:
            return OllamaHealth(False, detail=str(exc))

    async def pull(
        self,
        model: str,
        *,
        confirm: Callable[[PullPlan], Awaitable[bool] | bool],
        output: Callable[[PullProgress], object] | None = None,
    ) -> OllamaModel:
        """Ask for confirmation before POSTing to Ollama's pull endpoint."""

        installed_models = await self.list_models()
        installed_names = {item.name for item in installed_models}
        profile = detect_hardware()
        recommendation = next(
            (item for item in recommend_ollama_models(profile) if item.name == model), None
        )
        plan = PullPlan(
            model=model,
            download_bytes=recommendation.download_bytes if recommendation else None,
            already_installed=model in installed_names,
        )
        decision = confirm(plan)
        if inspect.isawaitable(decision):
            decision = await decision
        if not decision:
            raise OllamaPullRefused(f"download declined for {model}")

        response = await self._call("post", "/api/pull", json={"model": model, "stream": True})
        for progress in await self._pull_progress(response, model):
            if output is not None:
                result = output(progress)
                if inspect.isawaitable(result):
                    await result

        refreshed = await self.list_models()
        for item in refreshed:
            if item.name == model:
                return item
        return OllamaModel(name=model, size_bytes=plan.download_bytes)

    async def _pull_progress(self, response: Any, model: str) -> list[PullProgress]:
        status = (
            int(getattr(response, "status_code", 200)) if not isinstance(response, list) else 200
        )
        if status >= 400:
            raise RuntimeError(f"Ollama returned HTTP {status} during pull")
        if isinstance(response, list):
            lines: Sequence[Any] = response
        elif hasattr(response, "aiter_lines"):
            lines = [line async for line in response.aiter_lines()]
        elif hasattr(response, "iter_lines"):
            lines = list(response.iter_lines())
        else:
            lines = []

        parsed: list[PullProgress] = []
        for raw_line in lines:
            if isinstance(raw_line, bytes):
                raw_line = raw_line.decode("utf-8", errors="replace")
            try:
                payload = json.loads(str(raw_line))
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            parsed.append(
                PullProgress(
                    model=model,
                    status=str(payload.get("status", "")),
                    completed_bytes=(
                        int(payload["completed"])
                        if isinstance(payload.get("completed"), (int, float))
                        else None
                    ),
                    total_bytes=(
                        int(payload["total"])
                        if isinstance(payload.get("total"), (int, float))
                        else None
                    ),
                    detail=str(payload.get("error")) if payload.get("error") else None,
                )
            )
        return parsed


def build_ollama_cloud_config(
    *,
    model: str,
    api_key: str,
    base_url: str = "https://ollama.com",
) -> dict[str, str]:
    """Build a redacted-by-default remote Ollama config; never pulls locally."""

    return {
        "AVO_PROVIDER": "ollama-cloud",
        "AVO_MODEL": model,
        "AVO_OLLAMA_BASE_URL": base_url.rstrip("/"),
        "AVO_OLLAMA_API_KEY": api_key,
    }


__all__ = [
    "OllamaHealth",
    "OllamaManager",
    "OllamaModel",
    "OllamaPullRefused",
    "PullPlan",
    "PullProgress",
    "build_ollama_cloud_config",
]
