"""Tests verifying site/ structure, standalone installer exposure, and CI configuration."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_docs_ci_workflow_removed() -> None:
    """CI docs workflow must not exist so GitHub Actions never overwrites custom site/."""
    docs_workflow = ROOT / ".github" / "workflows" / "docs.yml"
    assert not docs_workflow.exists(), "docs.yml must be removed to prevent CI from overwriting site/"


def test_netlify_configuration_publishes_site_directly() -> None:
    """Netlify must publish site/ directly without running build command."""
    netlify_toml = (ROOT / "netlify.toml").read_text(encoding="utf-8")
    assert 'publish = "site/"' in netlify_toml
    # Build command must be empty to publish site/ directly
    assert 'command = ""' in netlify_toml
    assert "build_site.sh" not in netlify_toml


def test_site_installers_match_root() -> None:
    """install.sh and install.ps1 in site/ must exist and support standalone zero-python mode."""
    site_sh = ROOT / "site" / "install.sh"
    site_ps1 = ROOT / "site" / "install.ps1"

    assert site_sh.exists()
    assert site_ps1.exists()

    sh_content = site_sh.read_text(encoding="utf-8")
    ps1_content = site_ps1.read_text(encoding="utf-8")

    assert "standalone binary" in sh_content
    assert "standalone (zero Python runtime required)" in ps1_content


def test_site_index_showcases_standalone_and_new_features() -> None:
    """site/index.html must highlight zero-Python standalone installer and key capabilities."""
    index_html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")

    # Zero-Python & standalone indicators
    assert "Zero-Python" in index_html or "standalone" in index_html
    assert "install.sh" in index_html
    assert "install.ps1" in index_html
    assert "Git Worktree" in index_html
    assert "Durable" in index_html or "Approval" in index_html
