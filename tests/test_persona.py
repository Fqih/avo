"""Tests for the persona and workspace instructions system."""

from __future__ import annotations

from pathlib import Path

import pytest

from avo.persona import BUILTIN_PERSONAS, PersonaManager


def test_builtin_personas_defined() -> None:
    assert "coder" in BUILTIN_PERSONAS
    assert "reviewer" in BUILTIN_PERSONAS
    assert "architect" in BUILTIN_PERSONAS
    assert "terse" in BUILTIN_PERSONAS


def test_persona_manager_defaults() -> None:
    mgr = PersonaManager()
    assert mgr.active_persona is None
    assert mgr.custom_instructions is None
    assert mgr.render_system_prompt() is None


def test_persona_manager_set_valid_persona() -> None:
    mgr = PersonaManager()
    mgr.set_persona("coder")
    assert mgr.active_persona == "coder"
    rendered = mgr.render_system_prompt()
    assert rendered is not None
    assert "Expert Software Engineer" in rendered


def test_persona_manager_set_invalid_persona_raises() -> None:
    mgr = PersonaManager()
    with pytest.raises(ValueError, match="Unknown persona 'hacker'"):
        mgr.set_persona("hacker")


def test_persona_manager_load_workspace_instructions(tmp_path: Path) -> None:
    avo_dir = tmp_path / ".avo"
    avo_dir.mkdir()
    instr_file = avo_dir / "instructions.md"
    instr_file.write_text("Always write Python 3.12 type hints.", encoding="utf-8")

    mgr = PersonaManager(workspace_root=tmp_path)
    assert mgr.custom_instructions == "Always write Python 3.12 type hints."

    mgr.set_persona("reviewer")
    rendered = mgr.render_system_prompt()
    assert rendered is not None
    assert "Senior Code Reviewer" in rendered
    assert "Workspace Instructions:" in rendered
    assert "Always write Python 3.12 type hints." in rendered


def test_persona_manager_env_var_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVO_SYSTEM_PROMPT", "Enforce strict zero-warning policy.")
    mgr = PersonaManager()
    assert mgr.custom_instructions == "Enforce strict zero-warning policy."
    rendered = mgr.render_system_prompt()
    assert rendered is not None
    assert "Enforce strict zero-warning policy." in rendered


def test_register_custom_persona_and_activate() -> None:
    mgr = PersonaManager()
    mgr.register_persona(
        "devsecops",
        "Role: DevSecOps Engineer.\nFocus: SAST and container security.",
    )
    assert "devsecops" in mgr.available_personas()
    mgr.set_persona("devsecops")
    assert mgr.active_persona == "devsecops"
    rendered = mgr.render_system_prompt()
    assert rendered is not None
    assert "DevSecOps Engineer" in rendered


def test_register_custom_persona_persists_to_workspace(tmp_path: Path) -> None:
    mgr = PersonaManager(workspace_root=tmp_path)
    mgr.register_persona("qa", "Role: QA Automation Engineer.", persist=True)
    target_file = tmp_path / ".avo" / "personas" / "qa.md"
    assert target_file.is_file()
    assert target_file.read_text(encoding="utf-8") == "Role: QA Automation Engineer."

    # New manager instance loads it automatically from workspace
    new_mgr = PersonaManager(workspace_root=tmp_path)
    assert "qa" in new_mgr.available_personas()


def test_register_custom_persona_invalid_name_raises() -> None:
    mgr = PersonaManager()
    with pytest.raises(ValueError, match="Invalid persona name"):
        mgr.register_persona("bad name with spaces!", "Prompt")
    with pytest.raises(ValueError, match="Persona prompt cannot be empty"):
        mgr.register_persona("valid_name", "   ")
