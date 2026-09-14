"""Import-contract guard for the split chat REPL modules.

``avo.chat`` re-exports everything that moved into the leaf modules
(``chat_render``, ``chat_workspace_commands``, ``chat_commands``,
``chat_turn``), so the leaves must never import ``avo.chat`` at
runtime — :class:`avo.chat.ChatContext` references stay behind
``TYPE_CHECKING``. These tests pin that contract: every name in
``avo.chat.__all__`` resolves, each module imports standalone in a
fresh interpreter, and importing a leaf does not drag ``avo.chat`` in.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import avo.chat
from avo.chat_commands import _run_slash
from avo.chat_render import SLASH_COMMANDS, _print_header
from avo.chat_turn import _run_turn
from avo.chat_workspace_commands import _run_repl_shell

SRC_AVO = Path(avo.chat.__file__).resolve().parent

CHAT_MODULES = [
    "avo.chat",
    "avo.chat_render",
    "avo.chat_workspace_commands",
    "avo.chat_commands",
    "avo.chat_turn",
]

LEAF_MODULES = {
    "avo.chat_render": "chat_render.py",
    "avo.chat_workspace_commands": "chat_workspace_commands.py",
    "avo.chat_commands": "chat_commands.py",
    "avo.chat_turn": "chat_turn.py",
}


def _run_import(dotted: str) -> subprocess.CompletedProcess[str]:
    """Import ``dotted`` in a fresh interpreter and report sys.modules state."""

    env = {**os.environ, "PYTHONPATH": str(SRC_AVO.parent)}
    code = f"import {dotted}, sys; print('avo.chat' in sys.modules)"
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_new_chat_modules_exist() -> None:
    for fname in LEAF_MODULES.values():
        assert (SRC_AVO / fname).is_file(), f"missing {fname}"


def test_all_names_resolve() -> None:
    missing = [name for name in avo.chat.__all__ if not hasattr(avo.chat, name)]
    assert missing == []


def test_re_exports_are_the_same_objects() -> None:
    assert avo.chat._run_slash is _run_slash
    assert avo.chat._run_turn is _run_turn
    assert avo.chat._print_header is _print_header
    assert avo.chat._run_repl_shell is _run_repl_shell
    assert avo.chat.SLASH_COMMANDS is SLASH_COMMANDS


@pytest.mark.parametrize("dotted", CHAT_MODULES)
def test_module_imports_standalone(dotted: str) -> None:
    result = _run_import(dotted)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("dotted", sorted(LEAF_MODULES))
def test_leaf_import_does_not_pull_avo_chat(dotted: str) -> None:
    """A leaf loading ``avo.chat`` at runtime means a real import cycle."""

    result = _run_import(dotted)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", f"{dotted} imports avo.chat at runtime"


@pytest.mark.parametrize("dotted", sorted(LEAF_MODULES))
def test_avo_chat_imports_are_type_checking_only(dotted: str) -> None:
    """Static half of the cycle guard: ``from avo.chat import`` in a leaf
    may only appear inside an ``if TYPE_CHECKING:`` block."""

    module = __import__(dotted, fromlist=["_"])
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    offenders: list[int] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [
                node.module or "" if isinstance(node, ast.ImportFrom) else alias.name
                for alias in node.names
            ]
            if any(n == "avo.chat" for n in names):
                offenders.append(node.lineno)
        elif isinstance(node, ast.If) and not (
            isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
        ):
            # Any non-TYPE_CHECKING top-level if whose body imports avo.chat
            # at runtime would also be a cycle.
            for child in ast.walk(node):
                if isinstance(child, ast.ImportFrom) and child.module == "avo.chat":
                    offenders.append(child.lineno)
    assert offenders == [], f"{dotted} imports avo.chat at runtime (lines {offenders})"
