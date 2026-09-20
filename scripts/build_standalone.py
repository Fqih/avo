#!/usr/bin/env python3
"""Build a standalone, zero-Python-dependency binary for Avo CLI using PyInstaller."""

from __future__ import annotations

import argparse
import hashlib
import platform
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def get_version() -> str:
    """Extract Avo version from src/avo/__init__.py."""
    init_path = ROOT / "src" / "avo" / "__init__.py"
    for line in init_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=")[1].strip().strip('"').strip("'")
    return "0.0.0"


def resolve_platform_arch() -> tuple[str, str]:
    """Resolve normalized platform and architecture strings."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system.startswith("linux"):
        os_name = "linux"
    elif system.startswith("darwin"):
        os_name = "macos"
    elif system.startswith("win"):
        os_name = "windows"
    else:
        os_name = system

    if machine in ("x86_64", "amd64"):
        arch_name = "x86_64"
    elif machine in ("aarch64", "arm64"):
        arch_name = "aarch64"
    else:
        arch_name = machine

    return os_name, arch_name


def calculate_sha256(file_path: Path) -> str:
    """Calculate SHA256 digest of a file."""
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def build_binary(output_dir: Path, check_only: bool = False) -> Path:
    """Invoke PyInstaller to build a standalone single-file binary."""
    os_name, _ = resolve_platform_arch()
    exe_suffix = ".exe" if os_name == "windows" else ""
    target_bin = output_dir / f"avo{exe_suffix}"

    if check_only:
        print(f"[dry-run] Would build standalone binary: {target_bin}")
        return target_bin

    pyinstaller_cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=avo",
        "--onefile",
        "--clean",
        "--noconfirm",
        f"--distpath={output_dir}",
        f"--workpath={output_dir / 'build'}",
        f"--specpath={output_dir}",
        f"--paths={ROOT / 'src'}",
        "--collect-submodules=avo",
        "--collect-data=avo",
        "--hidden-import=pydantic",
        "--hidden-import=pydantic_core",
        "--hidden-import=prompt_toolkit",
        "--hidden-import=sqlite3",
        str(ROOT / "src" / "avo" / "cli.py"),
    ]

    print(f"→ Running PyInstaller to build standalone binary in {output_dir}...")
    subprocess.run(pyinstaller_cmd, cwd=ROOT, check=True)

    if not target_bin.exists():
        raise FileNotFoundError(f"Expected standalone binary not found at {target_bin}")

    # Set executable permissions on non-Windows
    if os_name != "windows":
        target_bin.chmod(0o755)

    size = target_bin.stat().st_size
    print(f"✓ Standalone binary successfully built: {target_bin} ({size} bytes)")
    return target_bin


def package_binary(binary_path: Path, output_dir: Path, version: str) -> Path:
    """Package binary into a release archive (.tar.gz or .zip) with sha256."""
    os_name, arch_name = resolve_platform_arch()
    archive_base = f"avo-{version}-{os_name}-{arch_name}"

    if os_name == "windows":
        archive_path = output_dir / f"{archive_base}.zip"
        print(f"→ Creating zip archive: {archive_path}...")
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(binary_path, arcname="avo.exe")
            if (ROOT / "LICENSE").exists():
                zf.write(ROOT / "LICENSE", arcname="LICENSE")
            if (ROOT / "README.md").exists():
                zf.write(ROOT / "README.md", arcname="README.md")
    else:
        archive_path = output_dir / f"{archive_base}.tar.gz"
        print(f"→ Creating tar.gz archive: {archive_path}...")
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(binary_path, arcname="avo")
            if (ROOT / "LICENSE").exists():
                tf.add(ROOT / "LICENSE", arcname="LICENSE")
            if (ROOT / "README.md").exists():
                tf.add(ROOT / "README.md", arcname="README.md")

    # Generate SHA256 checksum file
    sha256 = calculate_sha256(archive_path)
    sha_file = archive_path.with_suffix(archive_path.suffix + ".sha256")
    sha_file.write_text(f"{sha256}  {archive_path.name}\n", encoding="utf-8")
    print(f"✓ Created checksum: {sha_file.name} ({sha256})")

    return archive_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build standalone Avo binary without Python dependencies."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist" / "standalone",
        help="Target output directory",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Print build plan without running PyInstaller",
    )
    parser.add_argument(
        "--package",
        action="store_true",
        help="Package binary into tar.gz/zip with sha256 checksum",
    )
    parser.add_argument("--version", type=str, default=None, help="Override version string")

    args = parser.parse_args()
    version = args.version or get_version()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    os_target, arch_target = resolve_platform_arch()
    print("Avo Standalone Binary Builder")
    print(f"  Version:  {version}")
    print(f"  Platform: {os_target}-{arch_target}")
    print(f"  Output:   {output_dir}")

    binary_path = build_binary(output_dir, check_only=args.check_only)

    if args.package and not args.check_only:
        archive_path = package_binary(binary_path, output_dir, version)
        print(f"\n✓ Package ready: {archive_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
