from __future__ import annotations

import base64
from pathlib import Path

import pytest


def test_prepare_prompt_accepts_workspace_file_and_image(tmp_path: Path) -> None:
    from avo.attachments import prepare_prompt

    text_file = tmp_path / "main.py"
    text_file.write_text("print('ok')\n", encoding="utf-8")
    image_file = tmp_path / "screen.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\nimage")

    prepared = prepare_prompt(
        "review @main.py and @screen.png",
        workspace_root=tmp_path,
    )

    assert prepared.text == "review and"
    assert prepared.attachments == ("main.py", "screen.png")
    assert prepared.content[0] == {"type": "text", "text": "review and"}
    assert prepared.content[1]["text"].startswith("--- main.py ---")
    assert prepared.content[2]["type"] == "image"
    assert prepared.content[2]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.b64encode(image_file.read_bytes()).decode("ascii"),
    }


def test_prepare_prompt_accepts_dragged_file_url_and_relative_path(tmp_path: Path) -> None:
    from avo.attachments import prepare_prompt

    document = tmp_path / "notes.md"
    document.write_text("hello", encoding="utf-8")

    prepared = prepare_prompt(
        f"summarize file://{document} {document.relative_to(tmp_path)}",
        workspace_root=tmp_path,
    )

    assert prepared.attachments == ("notes.md", "notes.md")
    assert prepared.content[-1]["text"].endswith("hello")


def test_prepare_prompt_rejects_workspace_escape_and_binary(tmp_path: Path) -> None:
    from avo.attachments import AttachmentError, prepare_prompt

    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    binary = tmp_path / "archive.bin"
    binary.write_bytes(b"\x00\x01")

    with pytest.raises(AttachmentError, match="workspace"):
        prepare_prompt(f"@{outside}", workspace_root=tmp_path)
    with pytest.raises(AttachmentError, match="binary"):
        prepare_prompt("@archive.bin", workspace_root=tmp_path)


def test_prepare_prompt_supports_pasted_clipboard_image(tmp_path: Path) -> None:
    from avo.attachments import prepare_prompt

    prepared = prepare_prompt(
        "describe @clipboard",
        workspace_root=tmp_path,
        clipboard_reader=lambda: (b"clipboard-image", "image/jpeg"),
    )

    assert prepared.attachments == ("clipboard",)
    assert prepared.content[0]["text"] == "describe"
    assert prepared.content[1]["source"]["media_type"] == "image/jpeg"


def test_prepare_prompt_enforces_total_limit(tmp_path: Path) -> None:
    from avo.attachments import AttachmentError, AttachmentPolicy, prepare_prompt

    large = tmp_path / "large.txt"
    large.write_text("123456", encoding="utf-8")

    with pytest.raises(AttachmentError, match="total"):
        prepare_prompt(
            "@large.txt",
            workspace_root=tmp_path,
            policy=AttachmentPolicy(max_total_bytes=5),
        )
