from __future__ import annotations

from avo.hardware import HardwareProfile, detect_hardware


def test_detect_hardware_reads_linux_memory_and_survives_missing_gpu(tmp_path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16384000 kB\n", encoding="utf-8")

    def runner(*args, **kwargs):
        raise FileNotFoundError(args[0])

    profile = detect_hardware(
        proc_meminfo=meminfo,
        runner=runner,
        statvfs=lambda _: type("Stats", (), {"f_bavail": 100, "f_frsize": 1024**3})(),
    )

    assert profile.ram_bytes == 16384000 * 1024
    assert profile.cpu_cores >= 1
    assert profile.gpu_vram_bytes is None
    assert profile.disk_free_bytes == 100 * 1024**3


def test_hardware_profile_is_immutable_and_serializable() -> None:
    profile = HardwareProfile(
        platform="Linux",
        cpu_cores=8,
        ram_bytes=16 * 1024**3,
        gpu_vram_bytes=8 * 1024**3,
        disk_free_bytes=100 * 1024**3,
    )
    assert profile.ram_gb == 16.0
    assert profile.vram_gb == 8.0
