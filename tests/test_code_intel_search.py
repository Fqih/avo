"""Tests for code search and semantic ranking in avo.code_intel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from avo.app_tools.file_tools import bind_workspace
from avo.app_tools.workspace import Workspace
from avo.code_intel.search import CodeSearchEngine, code_search_tool

SERVICE_CODE = '''
class AuthenticationManager:
    """Handles user authentication, token refresh, and credential validation."""

    def login(self, username: str, password_hash: str) -> bool:
        """Validate credentials against secure store and issue session token."""
        return True

    def refresh_token(self, token: str) -> str:
        """Refresh expired JWT auth token with renewed TTL."""
        return "new_jwt_token"


class OrderProcessor:
    """Processes customer checkout cart, calculates taxes and shipping costs."""

    def calculate_tax(self, total: float, rate: float) -> float:
        """Calculate state sales tax for invoice."""
        return total * rate
'''


def test_code_search_engine_bm25_scoring(tmp_path: Path) -> None:
    test_file = tmp_path / "auth_service.py"
    test_file.write_text(SERVICE_CODE, encoding="utf-8")

    engine = CodeSearchEngine(root=tmp_path)
    engine.index()

    # Search for auth refresh logic
    results = engine.search("refresh expired auth token", limit=3)
    assert len(results) > 0
    top = results[0]
    assert top.name == "refresh_token"
    assert top.container_name == "AuthenticationManager"
    assert "JWT" in (top.docstring or "")

    # Search for taxes
    tax_results = engine.search("calculate sales tax", limit=3)
    assert len(tax_results) > 0
    assert tax_results[0].name == "calculate_tax"


async def test_code_search_tool(tmp_path: Path) -> None:
    test_file = tmp_path / "service.py"
    test_file.write_text(SERVICE_CODE, encoding="utf-8")

    ws = Workspace(root=tmp_path)
    tool = code_search_tool()

    with bind_workspace(ws):
        res: Any = await tool.invoke({"query": "token validation", "limit": 2})
        assert res["query"] == "token validation"
        assert res["count"] > 0
        names = [item["name"] for item in res["results"]]
        assert any(n in ("AuthenticationManager", "login", "refresh_token") for n in names)
