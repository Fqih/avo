from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_public_docs_use_current_cli_and_provider_language() -> None:
    public_docs = [
        ROOT / "README.md",
        ROOT / "docs" / "index.md",
        ROOT / "docs" / "cli.md",
        ROOT / "docs" / "guides" / "quickstart.md",
        ROOT / "docs" / "guides" / "subscription-auth.md",
        ROOT / "docs" / "guides" / "combo-routing.md",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in public_docs)

    assert "avo combo auth" in text
    assert "avo models ollama" in text
    assert "avo.faqihhakim.tech" in text
    assert "subscription required" not in text.lower()
    assert "Ollama Cloud" in text
