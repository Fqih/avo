"""Best-effort local hardware detection for model recommendations.

Only local operating-system metadata is inspected. Missing GPU utilities are
normal and never make the detector fail.
"""

from __future__ import annotations

import os
import platform
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    """Conservative capabilities used to rank local model suggestions."""

    platform: str
    cpu_cores: int
    ram_bytes: int
    gpu_name: str | None = None
    gpu_vram_bytes: int | None = None
    disk_free_bytes: int | None = None

    @property
    def system(self) -> str:
        """Backward-compatible alias for the platform name."""

        return self.platform

    @property
    def vram_bytes(self) -> int | None:
        """Backward-compatible alias for GPU memory."""

        return self.gpu_vram_bytes

    @property
    def ram_gb(self) -> float:
        return round(self.ram_bytes / 1024**3, 1)

    @property
    def vram_gb(self) -> float | None:
        return None if self.gpu_vram_bytes is None else round(self.gpu_vram_bytes / 1024**3, 1)


def _default_runner(args: list[str], **kwargs: Any) -> Any:
    return subprocess.run(args, capture_output=True, text=True, timeout=3, check=False, **kwargs)


def _run_optional(runner: Callable[..., Any], args: list[str]) -> Any | None:
    try:
        return runner(args, timeout=3, check=False)
    except TypeError:
        try:
            return runner(args)
        except (OSError, subprocess.SubprocessError):
            return None
    except (OSError, subprocess.SubprocessError):
        return None


def _read_ram_bytes(path: Path) -> int:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                parts = line.split()
                return int(parts[1]) * 1024 if len(parts) >= 2 else 0
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def _gpu_memory_bytes(runner: Callable[..., Any]) -> int | None:
    commands = (
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
        ["rocm-smi", "--showmeminfo", "vram", "--csv"],
    )
    for command in commands:
        result = _run_optional(runner, command)
        if result is None:
            continue
        output = str(getattr(result, "stdout", result) or "")
        for token in output.replace(",", " ").split():
            try:
                value = float(token)
            except ValueError:
                continue
            if value > 0:
                # nvidia-smi reports MiB; ROCm's CSV commonly reports bytes or
                # MiB depending on version. A conservative heuristic handles
                # both without making GPU detection a hard dependency.
                return int(value * 1024**2) if value < 1_000_000 else int(value)
    return None


def detect_hardware(
    *,
    runner: Callable[..., Any] | None = None,
    statvfs: Callable[[str], Any] | None = None,
    proc_meminfo: Path | None = None,
) -> HardwareProfile:
    """Detect CPU/RAM/GPU/disk with safe fallbacks on every platform."""

    command_runner = runner or _default_runner
    memory_path = proc_meminfo or Path("/proc/meminfo")
    ram_bytes = _read_ram_bytes(memory_path)
    if not ram_bytes:
        try:
            ram_bytes = int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
        except (AttributeError, OSError, ValueError):
            ram_bytes = 0

    disk_free_bytes: int | None = None
    try:
        fs = statvfs or os.statvfs
        stats = fs("/")
        disk_free_bytes = int(stats.f_bavail) * int(stats.f_frsize)
    except (OSError, AttributeError, TypeError, ValueError):
        pass

    return HardwareProfile(
        platform=platform.system() or "Unknown",
        cpu_cores=max(1, int(os.cpu_count() or 1)),
        ram_bytes=ram_bytes,
        gpu_vram_bytes=_gpu_memory_bytes(command_runner),
        disk_free_bytes=disk_free_bytes,
    )


__all__ = ["HardwareProfile", "detect_hardware"]
