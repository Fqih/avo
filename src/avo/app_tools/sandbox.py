"""Sandbox executor wrapping docker-py for ephemeral command execution.

The executor never invokes ``subprocess`` on the host directly. Every
``run`` call creates a fresh container with ``remove=True``,
``network_mode="none"`` (configurable), and a bounded ``mem_limit``.
The container's working directory is fixed to ``/workspace`` so the
agent cannot reach the host filesystem outside what the operator
explicitly mounts.

The docker client is injectable: production code passes
``docker.from_env()``; tests pass a fake client and assert the
container configuration that was sent to it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from avo.exceptions import ToolExecutionError

SandboxError = ToolExecutionError

_DEFAULT_IMAGE = "python:3.12-slim"
_DEFAULT_MEM_LIMIT = "256m"
_DEFAULT_CPU_QUOTA = 50000  # 0.5 CPU
_DEFAULT_TIMEOUT_SECONDS = 30.0
_IN_CONTAINER_WORKDIR = "/workspace"

# Multi-language image registry. Pin minor versions for reproducibility.
# Slim/alpine base keep pull size + attack surface small.
_LANGUAGE_IMAGES: dict[str, str] = {
    "python": "python:3.12-slim",
    "node": "node:20-alpine",
    "typescript": "node:20-alpine",  # tsc + node runtime in same image
    "go": "golang:1.22-alpine",
    "rust": "rust:1.80-slim",
    "ruby": "ruby:3.3-slim",
    "java": "eclipse-temurin:21-jre-alpine",
    "bash": "alpine:3.20",
    "sh": "alpine:3.20",
    "generic": "alpine:3.20",
}

# Extensions auto-detected by `language_from_path()`.
_PATH_LANGUAGE_HINTS: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "node",
    ".mjs": "node",
    ".cjs": "node",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".java": "java",
    ".sh": "bash",
    ".bash": "bash",
}


def language_from_path(path: str | Path) -> str | None:
    """Infer the language for a file path. Returns ``None`` if unknown."""

    suffix = Path(path).suffix.lower()
    return _PATH_LANGUAGE_HINTS.get(suffix)


def resolve_image(language: str | None = None, *, explicit_image: str | None = None) -> str:
    """Resolve the docker image for ``language`` or fall back to ``explicit_image``.

    Args:
        language: One of the keys in ``_LANGUAGE_IMAGES`` (``"python"``,
            ``"node"``, ``"go"``, ``"rust"``, ``"ruby"``, ``"java"``,
            ``"bash"``, ``"generic"``). Case-insensitive.
        explicit_image: When ``language`` is None, use this image directly.

    Raises:
        SandboxError: when ``language`` is unknown.
    """

    if language is None:
        if explicit_image is None:
            return _DEFAULT_IMAGE
        return explicit_image

    key = language.lower()
    image = _LANGUAGE_IMAGES.get(key)
    if image is None:
        raise SandboxError(
            f"unknown language {language!r}; supported: {', '.join(sorted(_LANGUAGE_IMAGES))}"
        )
    return image


class _Container(Protocol):
    """Subset of ``docker.models.containers.Container`` we depend on."""

    id: str
    short_id: str

    def wait(self) -> dict[str, Any]: ...
    def logs(self, *, stdout: bool = ..., stderr: bool = ...) -> bytes: ...
    def remove(self, *, force: bool = ...) -> None: ...


class _ContainersAPI(Protocol):
    """Subset of ``docker.DockerClient.containers`` we depend on."""

    def create(self, **kwargs: Any) -> _Container: ...


class _DockerClient(Protocol):
    """Subset of ``docker.DockerClient`` we depend on."""

    def containers(self) -> _ContainersAPI: ...


@dataclass(frozen=True)
class SandboxResult:
    """The structured outcome of one sandboxed command invocation."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    image: str
    network_mode: str
    mem_limit: str


def _coerce_log(value: Any) -> str:
    """Convert the docker client's ``logs()`` return value into a string."""

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return str(value)


class SandboxExecutor:
    """Run a shell command inside an ephemeral docker container.

    Args:
        client: docker client (defaults to ``docker.from_env()``). Tests
            inject a fake client that records the container config.
        image: Docker image used for the container. Overrides
            ``language`` if both are supplied.
        language: Shortcut for selecting a base image by language name
            (e.g. ``"python"``, ``"node"``, ``"go"``, ``"rust"``,
            ``"ruby"``, ``"java"``, ``"bash"``, ``"generic"``).
            Resolved via ``resolve_image``. Ignored when ``image`` is
            also given.
        mem_limit: Memory limit passed to docker (a string like ``"256m"``).
        cpu_quota: CPU quota (1.0 CPU = 100000). Default ``50000`` = 0.5 CPU.
        network_mode: Docker network mode. ``"none"`` keeps the container
            fully offline (default for safe agent execution).
        timeout_seconds: Hard wall-clock cap applied via ``wait()`` polling.

    The constructor does **not** create any container. ``run()`` creates
    one container per invocation and removes it before returning.
    """

    def __init__(
        self,
        *,
        client: _DockerClient | None = None,
        image: str | None = None,
        language: str | None = None,
        mem_limit: str = _DEFAULT_MEM_LIMIT,
        cpu_quota: int = _DEFAULT_CPU_QUOTA,
        network_mode: str = "none",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._explicit_client = client is not None
        self._client: _DockerClient | None = client
        # When both are passed the explicit ``image`` wins; when only one
        # is passed we resolve the other to keep backward compatibility.
        if image is None:
            image = resolve_image(language, explicit_image=None)
        self.image = image
        self.language = (language or "").lower() or None
        self.mem_limit = mem_limit
        self.cpu_quota = cpu_quota
        self.network_mode = network_mode
        self.timeout_seconds = timeout_seconds

    @classmethod
    def for_language(
        cls,
        language: str,
        *,
        client: _DockerClient | None = None,
        **kwargs: Any,
    ) -> SandboxExecutor:
        """Build an executor pre-configured for ``language``.

        Equivalent to ``SandboxExecutor(language=language, ...)`` but
        reads as a clearer call site for tool wiring.
        """

        return cls(client=client, language=language, **kwargs)

    def _resolve_client(self) -> _DockerClient:
        if self._client is not None:
            return self._client
        try:
            import docker as _docker  # type: ignore[import-untyped]
        except ModuleNotFoundError as exc:
            raise SandboxError(
                "SandboxExecutor requires the `docker` package; install avo"
                " with the [sandbox] extra."
            ) from exc
        client: _DockerClient = _docker.from_env()
        self._client = client
        return client

    async def run(
        self,
        command: str,
        *,
        workspace_dir: Path,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> SandboxResult:
        """Run ``command`` inside an ephemeral container.

        ``workspace_dir`` is reserved for future mounting support; today
        the executor only records it so the caller can scope shell I/O
        to a known directory. Network is off by default, so the
        container cannot reach the host or the internet.

        Raises:
            SandboxError: when the docker client is unavailable, the
                container cannot be created, or the wait times out.
        """

        effective_timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        del workspace_dir  # reserved for future bind-mount support

        client = self._resolve_client()

        try:
            container = await asyncio.to_thread(self._create_container, client, command, env or {})
        except SandboxError:
            raise
        except Exception as exc:  # pragma: no cover - docker errors vary
            raise SandboxError(f"failed to create sandbox container: {exc}") from exc

        try:
            started = asyncio.get_event_loop().time()
            try:
                info = await asyncio.wait_for(
                    asyncio.to_thread(container.wait),
                    timeout=effective_timeout,
                )
            except TimeoutError as exc:
                raise SandboxError(
                    f"sandbox command {command!r} exceeded the configured timeout of "
                    f"{effective_timeout} seconds."
                ) from exc
            duration_ms = max(0.0, (asyncio.get_event_loop().time() - started) * 1000)
            exit_code = int(info.get("StatusCode", 0)) if isinstance(info, dict) else 0
            logs = await asyncio.to_thread(container.logs, stdout=True, stderr=True)
            text = _coerce_log(logs)
            stdout, stderr = self._split_logs(text)
            return SandboxResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                image=self.image,
                network_mode=self.network_mode,
                mem_limit=self.mem_limit,
            )
        finally:
            with contextlib.suppress(Exception):  # pragma: no cover - best-effort cleanup
                await asyncio.to_thread(container.remove, force=True)

    def _create_container(
        self,
        client: _DockerClient,
        command: str,
        env: Mapping[str, str],
    ) -> _Container:
        """Create the ephemeral container; called in a worker thread."""

        containers = client.containers()
        return containers.create(
            image=self.image,
            command=["sh", "-c", command],
            environment=dict(env),
            network_mode=self.network_mode,
            mem_limit=self.mem_limit,
            cpu_quota=self.cpu_quota,
            working_dir=_IN_CONTAINER_WORKDIR,
            remove=True,
            detach=True,
            stdout=True,
            stderr=True,
        )

    @staticmethod
    def _split_logs(value: str) -> tuple[str, str]:
        """Split ``logs()`` output. Default: everything on stdout."""

        # Real docker-py returns a tuple ``(stdout_bytes, stderr_bytes)``
        # for ``exec_run``; ``logs(stdout=True, stderr=True)`` returns
        # interleaved bytes. Tests inject strings via the fake client
        # so this split is a no-op.
        return value, ""


__all__ = [
    "SandboxError",
    "SandboxExecutor",
    "SandboxResult",
    "language_from_path",
    "resolve_image",
]
