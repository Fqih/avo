"""Safe terminal attachment handling for chat turns.

Attachments are explicit (``@path``, ``file://path`` or ``@clipboard``),
workspace-bound, size-limited, and converted to the provider-neutral content
shape already understood by Avo's multimodal providers.
"""

from __future__ import annotations

import mimetypes
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from pydantic import JsonValue

_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_TEXT_EXTENSIONS = {
    ".c",
    ".cfg",
    ".cpp",
    ".css",
    ".csv",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".md",
    ".py",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


class AttachmentError(ValueError):
    """Raised before inference when an attachment is unsafe or unsupported."""


@dataclass(frozen=True, slots=True)
class AttachmentPolicy:
    """Resource limits applied before reading attachment bytes."""

    max_file_bytes: int = 2 * 1024 * 1024
    max_image_bytes: int = 10 * 1024 * 1024
    max_total_bytes: int = 20 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PreparedPrompt:
    """Clean prompt plus canonical content blocks for a model request."""

    text: str
    content: list[dict[str, JsonValue]]
    attachments: tuple[str, ...] = ()


ClipboardReader = Callable[[], tuple[bytes, str] | None]


def _clipboard_image() -> tuple[bytes, str] | None:
    """Read an image from Wayland or X11 without invoking a shell."""

    commands = (
        ("wl-paste", ("--list-types",)),
        ("xclip", ("-selection", "clipboard", "-t", "TARGETS", "-o")),
    )
    for executable, arguments in commands:
        try:
            listed = subprocess.run(
                [executable, *arguments],
                capture_output=True,
                check=False,
                timeout=3,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
        types = listed.stdout.decode("utf-8", errors="replace").splitlines()
        media_type = next(
            (item.strip() for item in types if item.strip() in _IMAGE_TYPES.values()), None
        )
        if media_type is None:
            continue
        read_args = (
            ("--type", media_type, "--no-newline")
            if executable == "wl-paste"
            else ("-selection", "clipboard", "-t", media_type, "-o")
        )
        try:
            image = subprocess.run(
                [executable, *read_args],
                capture_output=True,
                check=False,
                timeout=3,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
        if image.returncode == 0 and image.stdout:
            return image.stdout, media_type
    return None


def _path_from_token(token: str, workspace_root: Path) -> Path | None:
    raw = token[1:] if token.startswith("@") else token
    if raw.startswith("file://"):
        parsed = urlparse(raw)
        raw = unquote(parsed.path)
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    if not candidate.exists():
        if token.startswith("@") or token.startswith("file://"):
            raise AttachmentError(f"attachment not found: {raw}")
        return None
    return candidate


def _safe_path(path: Path, workspace_root: Path) -> tuple[Path, str]:
    root = workspace_root.expanduser().resolve()
    resolved = path.resolve()
    try:
        display = resolved.relative_to(root)
    except ValueError as exc:
        raise AttachmentError(f"attachment must stay inside the workspace: {path}") from exc
    if not resolved.is_file():
        raise AttachmentError(f"attachment is not a regular file: {path}")
    return resolved, display.as_posix()


def _image_content(data: bytes, media_type: str) -> dict[str, JsonValue]:
    import base64

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(data).decode("ascii"),
        },
    }


def prepare_prompt(
    task: str,
    *,
    workspace_root: Path,
    policy: AttachmentPolicy | None = None,
    clipboard_reader: ClipboardReader | None = None,
) -> PreparedPrompt:
    """Extract explicit file/image attachments from a chat prompt.

    Drag-and-drop paths are accepted when the terminal inserts an existing
    workspace path. ``@`` makes attachment intent explicit and works with
    paths containing spaces when pasted as a shell-quoted token. Clipboard
    images use ``@clipboard`` and never touch disk.
    """

    limits = policy or AttachmentPolicy()
    try:
        tokens = shlex.split(task)
    except ValueError as exc:
        raise AttachmentError(f"could not parse attachment path: {exc}") from exc

    remaining: list[str] = []
    content: list[dict[str, JsonValue]] = []
    labels: list[str] = []
    total_bytes = 0
    for token in tokens:
        if token == "@clipboard":
            reader = clipboard_reader or _clipboard_image
            clipboard = reader()
            if clipboard is None:
                raise AttachmentError("clipboard does not contain a supported image")
            data, media_type = clipboard
            if media_type not in _IMAGE_TYPES.values():
                raise AttachmentError(f"unsupported clipboard media type: {media_type}")
            if len(data) > limits.max_image_bytes:
                raise AttachmentError("clipboard image exceeds the image size limit")
            total_bytes += len(data)
            if total_bytes > limits.max_total_bytes:
                raise AttachmentError("attachments exceed the total size limit")
            labels.append("clipboard")
            content.append(_image_content(data, media_type))
            continue

        path = _path_from_token(token, workspace_root)
        if path is None:
            remaining.append(token)
            continue
        safe_path, label = _safe_path(path, workspace_root)
        size = safe_path.stat().st_size
        suffix = safe_path.suffix.lower()
        if suffix in _IMAGE_TYPES:
            if size > limits.max_image_bytes:
                raise AttachmentError(f"image {label} exceeds the image size limit")
            content.append(_image_content(safe_path.read_bytes(), _IMAGE_TYPES[suffix]))
        elif suffix in _TEXT_EXTENSIONS or mimetypes.guess_type(safe_path.name)[0] == "text/plain":
            if size > limits.max_file_bytes:
                raise AttachmentError(f"file {label} exceeds the file size limit")
            try:
                text = safe_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise AttachmentError(f"binary file is not supported: {label}") from exc
            content.append({"type": "text", "text": f"--- {label} ---\n{text}"})
        else:
            raise AttachmentError(f"unsupported binary attachment: {label}")
        total_bytes += size
        if total_bytes > limits.max_total_bytes:
            raise AttachmentError("attachments exceed the total size limit")
        labels.append(label)

    clean_text = " ".join(remaining).strip() or "Please inspect the attached file(s)."
    content.insert(0, {"type": "text", "text": clean_text})
    return PreparedPrompt(text=clean_text, content=content, attachments=tuple(labels))


__all__ = ["AttachmentError", "AttachmentPolicy", "PreparedPrompt", "prepare_prompt"]
