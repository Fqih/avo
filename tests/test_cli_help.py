from __future__ import annotations

import pytest

from avo.cli import main


def test_help_flags_are_accepted_for_discoverable_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for command in ("setup", "login", "models", "doctor", "runs"):
        with pytest.raises(SystemExit) as exc:
            main([command, "--help"])
        assert exc.value.code == 0
        assert command in capsys.readouterr().out.lower()
