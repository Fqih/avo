"""``batch_replace`` tool: atomic multi-file search and replace refactoring.

Applies a batch of surgical text replacements across one or more files in
the workspace. If any replacement fails (e.g. ``old_string`` not found or
ambiguous), the entire transaction aborts without mutating any files on disk.
Supports ``dry_run=True`` to preview unified diffs before applying changes.
"""

from __future__ import annotations

import contextlib
import difflib
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from avo import FunctionTool as PublicFunctionTool

from .edit_file import EditFileError, _apply_edit, _current_edit_workspace


class BatchReplaceError(EditFileError):
    """Raised when atomic batch replacement cannot be satisfied."""


class FilePatch(BaseModel):
    """Specification for replacing text in a single workspace file."""

    path: str = Field(min_length=1, description="Workspace-relative or absolute file path")
    old_string: str = Field(min_length=1, description="Literal text to replace")
    new_string: str = Field(description="Replacement text")
    replace_all: bool = Field(
        default=False,
        description="Replace every occurrence; if False, requires exactly one match",
    )

    @model_validator(mode="after")
    def _require_difference(self) -> FilePatch:
        if self.old_string == self.new_string:
            raise ValueError("old_string and new_string must differ")
        return self


class BatchReplaceArguments(BaseModel):
    """Arguments for the ``batch_replace`` tool."""

    patches: list[FilePatch] = Field(
        min_length=1,
        description="List of file patches to apply in an atomic transaction",
    )
    dry_run: bool = Field(
        default=False,
        description="If True, preview the replacements and diff without modifying files",
    )


def _write_atomic(path: Path, content: str, encoding: str = "utf-8") -> None:
    encoded = content.encode(encoding)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o644)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)


async def _batch_replace(arguments: BatchReplaceArguments) -> dict[str, Any]:
    workspace = _current_edit_workspace()

    # Step 1: Pre-resolve paths and group patches by file
    resolved_paths: dict[str, Path] = {}
    patches_by_path: dict[Path, list[FilePatch]] = {}

    for patch in arguments.patches:
        resolved = workspace.validate_for_write(patch.path)
        resolved_paths[patch.path] = resolved
        patches_by_path.setdefault(resolved, []).append(patch)

    # Step 2: Read originals and apply transformations in-memory
    original_contents: dict[Path, str] = {}
    updated_contents: dict[Path, str] = {}
    replacements_by_path: dict[Path, int] = {}
    diffs: list[str] = []

    for resolved, patches in patches_by_path.items():
        if not resolved.is_file():
            raise BatchReplaceError(f"Target file does not exist: {resolved}")

        original_text = resolved.read_text(encoding="utf-8")
        original_contents[resolved] = original_text

        current_text = original_text
        file_replaces = 0

        for p in patches:
            try:
                count = current_text.count(p.old_string)
                current_text = _apply_edit(current_text, p.old_string, p.new_string, p.replace_all)
                file_replaces += count
            except EditFileError as exc:
                raise BatchReplaceError(f"Failed applying patch to '{p.path}': {exc}") from exc

        updated_contents[resolved] = current_text
        replacements_by_path[resolved] = file_replaces

        # Generate unified diff
        diff_lines = list(
            difflib.unified_diff(
                original_text.splitlines(keepends=True),
                current_text.splitlines(keepends=True),
                fromfile=f"a/{resolved.name}",
                tofile=f"b/{resolved.name}",
            )
        )
        if diff_lines:
            diffs.append("".join(diff_lines))

    # Step 3: If dry_run, return computed preview immediately
    total_replaces = sum(replacements_by_path.values())
    files_changed = sum(
        1
        for orig, upd in updated_contents.items()
        if orig in original_contents and original_contents[orig] != upd
    )

    if arguments.dry_run:
        return {
            "status": "dry_run",
            "files_changed": files_changed,
            "total_replacements": total_replaces,
            "patches_count": len(arguments.patches),
            "diff": "\n".join(diffs),
            "files": [
                {
                    "path": str(p),
                    "replacements": replacements_by_path[p],
                }
                for p in patches_by_path
            ],
        }

    # Step 4: Write updates with rollback protection
    written_files: list[Path] = []
    try:
        for resolved, new_text in updated_contents.items():
            if original_contents[resolved] != new_text:
                _write_atomic(resolved, new_text)
                written_files.append(resolved)
    except Exception as exc:
        # Rollback all already-written files to original content
        for rolled in written_files:
            with contextlib.suppress(Exception):
                _write_atomic(rolled, original_contents[rolled])
        err_msg = f"IO error during batch replace; rolled back changes: {exc}"
        raise BatchReplaceError(err_msg) from exc

    return {
        "status": "applied",
        "files_changed": files_changed,
        "total_replacements": total_replaces,
        "patches_count": len(arguments.patches),
        "diff": "\n".join(diffs),
        "files": [
            {
                "path": str(p),
                "replacements": replacements_by_path[p],
            }
            for p in patches_by_path
        ],
    }


def batch_replace_tool() -> PublicFunctionTool[BatchReplaceArguments]:
    """Return a :class:`FunctionTool` for atomic multi-file text replacements."""
    return PublicFunctionTool(
        name="batch_replace",
        description=(
            "Atomically apply text replacements across one or multiple files in the workspace. "
            "If any replacement fails (string not found or ambiguous), the entire operation "
            "aborts without changing any file. Use dry_run=True to preview unified diffs."
        ),
        arguments_model=BatchReplaceArguments,
        function=_batch_replace,
    )


__all__ = [
    "BatchReplaceArguments",
    "BatchReplaceError",
    "FilePatch",
    "batch_replace_tool",
]
