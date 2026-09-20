"""Workspace root resolution and ``validate_path`` for application tools.

Every file tool resolves the path the model asked for against a fixed
``Workspace`` before doing any I/O. Resolution is strict: any traversal that
would escape the workspace root is rejected with
:class:`WorkspacePathError`, regardless of whether the escape succeeds on
the local filesystem.

The check runs *before* the operation, not after — the goal is to fail
closed when the model sends something suspicious, not to clean up damage.

Note on TOCTOU: ``validate_path`` resolves symlinks at call time. The
window between validation and the actual ``open()`` is not closed by this
module; the file_tools layer is responsible for using ``O_NOFOLLOW`` or
re-checking containment on platforms that support it.
"""

from __future__ import annotations

import os
from pathlib import Path

from avo.exceptions import AvoError

PathLike = str | Path


class WorkspacePathError(AvoError):
    """Raised when a path operation would escape the workspace root."""


class Workspace:
    """A fixed root directory for application tool I/O.

    The root is resolved at construction so the workspace behaves correctly
    even when the operator passes a relative path, a path containing
    symlinks, or a symlink root.
    """

    def __init__(self, root: PathLike, *, create: bool = False) -> None:
        root_path = Path(root)
        if create:
            root_path.mkdir(parents=True, exist_ok=True)
        if not root_path.exists():
            raise WorkspacePathError(
                f"workspace root {root_path!s} does not exist; pass create=True to make it"
            )
        if not root_path.is_dir():
            raise WorkspacePathError(f"workspace root {root_path!s} is not a directory")
        self._root = root_path.resolve(strict=True)

    @property
    def root(self) -> Path:
        """Return the absolute, resolved workspace root."""

        return self._root

    def _candidate_to_resolved(
        self,
        candidate_str: str,
        *,
        strict: bool,
    ) -> Path:
        """Resolve ``candidate_str`` against the workspace without raising containment errors.

        Resolution always happens with ``strict=False`` so that lexical
        ``..`` segments and existing-link symlinks are normalized before
        the containment check. The caller then performs an explicit
        ``exists()`` test when ``strict=True`` was requested.
        """

        if "\x00" in candidate_str:
            raise WorkspacePathError("path contains a null byte")
        if candidate_str == "":
            raise WorkspacePathError("path is empty")

        candidate = Path(candidate_str)
        base = self._root if not candidate.is_absolute() else None
        try:
            if base is not None:
                resolved = (base / candidate).resolve(strict=False)
            else:
                resolved = candidate.resolve(strict=False)
        except OSError as exc:
            raise WorkspacePathError(f"path could not be resolved: {exc}") from exc

        # Containment is authoritative — apply it before any existence check
        # so that an escape vector like ``../file.txt`` is rejected as an
        # escape even if the leaf happens to be missing on disk.
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise WorkspacePathError(
                f"path escapes workspace root {self._root!s}: {candidate_str}"
            ) from exc

        del strict  # existence is enforced by the caller
        return resolved

    def validate_path(self, requested: PathLike, *, must_exist: bool = True) -> Path:
        """Resolve ``requested`` against the workspace and reject escapes.

        Args:
            requested: A path the model asked for. May be absolute or
                relative to the workspace root.
            must_exist: When True (default) the resolved path must already
                exist. When False the path may be a brand-new file, but the
                parent directory must still exist inside the workspace.

        Raises:
            WorkspacePathError: If the path contains a null byte, resolves
                to a location outside the workspace root, or violates the
                ``must_exist`` contract.

        """

        candidate_str = os.fspath(requested)
        resolved = self._candidate_to_resolved(candidate_str, strict=must_exist)

        if must_exist:
            if not resolved.exists():
                raise WorkspacePathError(f"path does not exist: {candidate_str}")
            return resolved

        # must_exist=False — the leaf may be a new file, but the parent must
        # exist as a directory inside the workspace.
        parent = resolved.parent
        if not parent.exists():
            raise WorkspacePathError(f"parent directory does not exist: {parent!s}")
        if not parent.is_dir():
            raise WorkspacePathError(f"parent path is not a directory: {parent!s}")
        try:
            parent.relative_to(self._root)
        except ValueError as exc:
            raise WorkspacePathError(
                f"parent path escapes workspace root {self._root!s}: {parent!s}"
            ) from exc
        return resolved

    def validate_for_write(self, requested: PathLike) -> Path:
        """Resolve a path that the tool intends to create or overwrite.

        Equivalent to ``validate_path(requested, must_exist=False)`` — the
        parent directory must exist inside the workspace; the leaf may not.

        If the requested leaf is itself a symlink (or any parent
        component of it is one), the write is rejected. Following a
        symlink at write time defeats the point of the workspace check,
        because the link target could live anywhere on the filesystem
        even if the link itself is inside the workspace.
        """

        candidate_str = os.fspath(requested)
        if "\x00" in candidate_str:
            raise WorkspacePathError("path contains a null byte")
        if candidate_str == "":
            raise WorkspacePathError("path is empty")

        candidate = Path(candidate_str)
        base = self._root if not candidate.is_absolute() else None
        # Refuse any symlink in the chain. We check the unresolved path
        # (the literal the model sent) for symlinks because the leaf
        # might be a symlink even after a resolved parent dir.
        literal = (base / candidate) if base is not None else candidate
        try:
            if literal.is_symlink():
                raise WorkspacePathError(
                    f"refusing to follow symlink at write target: {candidate_str}"
                )
            # Walk up looking for a symlink at any level.
            for ancestor in literal.parents:
                if ancestor == self._root:
                    break
                if ancestor.is_symlink():
                    raise WorkspacePathError(f"refusing to follow symlink in path: {ancestor!s}")
        except WorkspacePathError:
            raise
        except OSError as exc:
            raise WorkspacePathError(f"path could not be inspected: {exc}") from exc

        resolved = self.validate_path(requested, must_exist=False)
        try:
            rel = resolved.relative_to(self._root)
            if rel.parts and rel.parts[0] == ".git":
                raise WorkspacePathError(
                    f"refusing to modify files inside .git directory: {candidate_str}"
                )
        except ValueError:
            pass
        return resolved


def assert_workspace_target(
    root: PathLike,
    target: PathLike,
    *,
    follow_symlinks: bool = False,
) -> Path:
    """Validate a final file target immediately before a filesystem mutation."""

    workspace = Workspace(root)
    candidate = Path(target)
    if not candidate.is_absolute():
        candidate = workspace.root / candidate
    if not follow_symlinks:
        try:
            if candidate.is_symlink():
                raise WorkspacePathError(f"refusing to follow symlink at write target: {target}")
            current = candidate.parent
            while current != workspace.root and current != current.parent:
                if current.is_symlink():
                    raise WorkspacePathError(f"refusing to follow symlink in path: {current}")
                current = current.parent
        except OSError as exc:
            raise WorkspacePathError(f"path could not be inspected: {exc}") from exc
    resolved = candidate.resolve(strict=False)
    try:
        rel = resolved.relative_to(workspace.root)
        if rel.parts and rel.parts[0] == ".git":
            raise WorkspacePathError(f"refusing to modify files inside .git directory: {target}")
    except ValueError as exc:
        raise WorkspacePathError(f"path escapes workspace root {workspace.root}: {target}") from exc
    return resolved


def validate_path(root: PathLike, requested: PathLike, *, must_exist: bool = True) -> Path:
    """Module-level convenience around :meth:`Workspace.validate_path`."""

    return Workspace(root).validate_path(requested, must_exist=must_exist)


__all__ = ["Workspace", "WorkspacePathError", "assert_workspace_target", "validate_path"]
