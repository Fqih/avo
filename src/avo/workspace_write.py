"""Contained atomic persistence shared by workspace edits and personas."""

from __future__ import annotations

import contextlib
import ntpath
import os
import secrets
import stat as stat_module
from collections.abc import Iterator
from pathlib import Path, PureWindowsPath
from typing import Any

from avo.app_tools.workspace import WorkspacePathError


@contextlib.contextmanager
def _windows_directory_handle(path: Path) -> Iterator[None]:
    """Pin a directory against rename, deletion, and reparse-point changes."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    info = kernel32.GetFileInformationByHandleEx
    info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    info.restype = wintypes.BOOL
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL

    class AttributeTagInfo(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    # FILE_READ_ATTRIBUTES, FILE_SHARE_READ, OPEN_EXISTING,
    # FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT.
    # In particular, do not share DELETE or WRITE access to these directories.
    handle = create(str(path), 0x80, 0x1, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = AttributeTagInfo()
        if not info(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)):
            raise ctypes.WinError(ctypes.get_last_error())
        if attributes.attributes & 0x400 or not attributes.attributes & 0x10:
            raise WorkspacePathError(f"unsafe workspace directory: {path}")
        yield
    finally:
        close(handle)


def _windows_write(workspace_root: Path, relative: Path, content: str) -> os.stat_result:
    """Windows lacks dir_fd/O_NOFOLLOW: hold no-reparse parent handles instead."""
    # Reject alternate streams, device names and Win32 path normalization aliases.
    for part in relative.parts:
        reserved = (
            ntpath.isreserved(part)
            if hasattr(ntpath, "isreserved")
            else PureWindowsPath(part).is_reserved()
        )
        if (
            part.endswith((".", " "))
            or reserved
            or any(c in '<>:"|?*' or ord(c) < 32 for c in part)
        ):
            raise WorkspacePathError(f"invalid Windows write path: {relative}")

    root = workspace_root.absolute()
    target = root / relative
    with contextlib.ExitStack() as guards:
        # Pin ancestors too: an ancestor rename must not redirect absolute paths.
        for directory in (*reversed(root.parents), root):
            guards.enter_context(_windows_directory_handle(directory))
        parent = root
        for part in relative.parts[:-1]:
            parent /= part
            parent.mkdir(exist_ok=True)
            guards.enter_context(_windows_directory_handle(parent))

        try:
            existing = target.lstat()
        except FileNotFoundError:
            pass
        else:
            if (
                not stat_module.S_ISREG(existing.st_mode)
                or getattr(existing, "st_file_attributes", 0) & 0x400
            ):
                raise WorkspacePathError(f"not a regular workspace file: {relative}")

        temporary = parent / f".avo-write-{secrets.token_hex(16)}"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            return target.stat()
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)


def save_workspace_file(workspace_root: Path, file_path_str: str, content: str) -> dict[str, Any]:
    """Save text content to a file inside the workspace safely."""
    if "\x00" in file_path_str:
        raise WorkspacePathError("path contains a null byte")
    if not file_path_str.strip():
        raise WorkspacePathError("path is empty")

    candidate_path = Path(file_path_str)
    try:
        relative = (
            candidate_path.relative_to(workspace_root)
            if candidate_path.is_absolute()
            else candidate_path
        )
    except ValueError as exc:
        raise WorkspacePathError(f"path escapes workspace root: {file_path_str}") from exc
    if not relative.parts or ".." in relative.parts:
        raise WorkspacePathError(f"invalid workspace write path: {file_path_str}")

    if os.name == "nt":
        stat = _windows_write(workspace_root, relative, content)
    else:
        stat = _posix_write(workspace_root, relative, content)
    rel_path = relative.as_posix()
    return {
        "ok": True,
        "path": rel_path,
        "filename": relative.name,
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
        "line_count": len(content.splitlines()),
        "message": f"Saved {rel_path}",
    }


def _posix_write(workspace_root: Path, relative: Path, content: str) -> os.stat_result:
    # Pin every parent directory. No path component may be followed through a
    # symlink, even if another thread swaps it between validation and the write.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(workspace_root, flags)
    temporary = f".avo-write-{secrets.token_hex(16)}"
    try:
        for part in relative.parts[:-1]:
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, dir_fd=directory_fd)
            try:
                child_fd = os.open(part, flags, dir_fd=directory_fd)
            except OSError as exc:
                raise WorkspacePathError(f"unsafe workspace directory: {part}") from exc
            os.close(directory_fd)
            directory_fd = child_fd

        mode = 0o600
        try:
            existing = os.stat(relative.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not stat_module.S_ISREG(existing.st_mode):
                raise WorkspacePathError(f"not a regular workspace file: {relative}")
            mode = stat_module.S_IMODE(existing.st_mode)

        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode, dir_fd=directory_fd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, relative.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            stat = os.stat(relative.name, dir_fd=directory_fd, follow_symlinks=False)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory_fd)
    finally:
        os.close(directory_fd)
    return stat
