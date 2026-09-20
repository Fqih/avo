"""``edit_file`` tool: surgical string replacement inside a file.

Like ``write_file``, the path is resolved through the active
:class:`Workspace` and the write itself opens the file with
``O_NOFOLLOW`` to refuse symlink targets. Unlike ``write_file``, this
tool only changes the matching region and is safe for the model to use
on partial edits without rewriting the whole file.

Behaviour:

* ``replace_all=False`` (default) requires exactly one match of
  ``old_string``. Zero matches and multiple matches both raise
  :class:`EditFileError` with a precise reason.
* ``replace_all=True`` replaces every non-overlapping match. Zero
  matches still raises — a no-op edit should not silently succeed.
* ``old_string`` and ``new_string`` must differ.
"""

from __future__ import annotations

import difflib
import os
from typing import Any

from pydantic import BaseModel, Field, model_validator

from avo import FunctionTool as PublicFunctionTool

from .workspace import Workspace


class EditFileError(RuntimeError):
    """Raised when ``edit_file`` cannot satisfy the requested edit."""


class EditFileArguments(BaseModel):
    """Arguments for the ``edit_file`` tool."""

    path: str = Field(min_length=1, description="Workspace-relative or absolute path")
    old_string: str = Field(min_length=1, description="Literal text to replace")
    new_string: str = Field(description="Replacement text")
    replace_all: bool = Field(
        default=False,
        description="Replace every match; otherwise the match must be unique",
    )

    @model_validator(mode="after")
    def _require_difference(self) -> EditFileArguments:
        """Reject edits where replacement equals the original text."""

        if self.old_string == self.new_string:
            raise ValueError("old_string and new_string must differ")
        return self


def _current_edit_workspace() -> Workspace:
    from avo.app_tools.file_tools import current_workspace

    try:
        return current_workspace()
    except Exception as exc:
        raise EditFileError(
            "edit_file invoked without an active workspace; wrap the run in "
            "avo.app_tools.file_tools.bind_workspace(...)"
        ) from exc


def _apply_edit(text: str, old_string: str, new_string: str, replace_all: bool) -> str:
    """Return ``text`` with ``old_string`` replaced by ``new_string``.

    Raises:
        EditFileError: when ``old_string`` does not appear, or appears
            more than once and ``replace_all=False``.
    """

    occurrences = text.count(old_string)
    if occurrences == 0:
        raise EditFileError("old_string not found in file")
    if occurrences > 1 and not replace_all:
        raise EditFileError(
            f"old_string matches {occurrences} locations; pass replace_all=True "
            "to replace all matches, or narrow old_string to a single location"
        )
    return text.replace(old_string, new_string)


async def _edit_file(arguments: EditFileArguments) -> dict[str, Any]:
    workspace = _current_edit_workspace()
    resolved = workspace.validate_for_write(arguments.path)
    encoding = "utf-8"
    original_text = resolved.read_text(encoding=encoding)

    updated_text = _apply_edit(
        original_text, arguments.old_string, arguments.new_string, arguments.replace_all
    )

    encoded = updated_text.encode(encoding)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(resolved, flags, 0o644)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)
    diff_lines = list(
        difflib.unified_diff(
            original_text.splitlines(keepends=True),
            updated_text.splitlines(keepends=True),
            fromfile=f"a/{arguments.path}",
            tofile=f"b/{arguments.path}",
            n=3,
        )
    )
    diff = "".join(diff_lines)

    return {
        "path": str(resolved),
        "size": len(encoded),
        "matches_replaced": original_text.count(arguments.old_string),
        "diff": diff,
    }


def edit_file_tool() -> PublicFunctionTool[EditFileArguments]:
    """Return a :class:`FunctionTool` that performs a surgical file edit."""

    return PublicFunctionTool(
        name="edit_file",
        description=(
            "Replace a literal string in a file inside the workspace. By "
            "default the match must be unique; pass replace_all=True to "
            "replace every occurrence. The path is validated against the "
            "workspace and the write refuses to follow symlinks."
        ),
        arguments_model=EditFileArguments,
        function=_edit_file,
    )


__all__ = [
    "EditFileArguments",
    "EditFileError",
    "edit_file_tool",
]
