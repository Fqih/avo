"""Tests for native Python AST code intelligence analyzer."""

from __future__ import annotations

from avo.code_intel.ast_analyzer import (
    extract_symbols,
    find_symbol_definitions,
    find_symbol_references,
)
from avo.code_intel.models import SymbolKind

SAMPLE_CODE = '''
import os
from math import sqrt

GLOBAL_FLAG = True

class CalculationService:
    """Service handling mathematical ops."""

    def __init__(self, offset: int = 0) -> None:
        self.offset = offset

    def compute(self, x: int) -> float:
        val = sqrt(x) + self.offset
        return val

def run_calculation() -> None:
    svc = CalculationService(offset=5)
    res = svc.compute(16)
    print(res)
'''


def test_extract_symbols_finds_classes_functions_and_methods() -> None:
    symbols = extract_symbols(SAMPLE_CODE, path="service.py")
    names = {s.name: s for s in symbols}

    assert "CalculationService" in names
    assert names["CalculationService"].kind == SymbolKind.CLASS
    assert names["CalculationService"].line == 7

    assert "compute" in names
    assert names["compute"].kind == SymbolKind.METHOD
    assert names["compute"].container_name == "CalculationService"

    assert "run_calculation" in names
    assert names["run_calculation"].kind == SymbolKind.FUNCTION
    assert names["run_calculation"].line == 17


def test_find_symbol_definitions() -> None:
    defs = find_symbol_definitions(SAMPLE_CODE, "CalculationService", path="service.py")
    assert len(defs) == 1
    assert defs[0].name == "CalculationService"
    assert defs[0].line == 7
    assert defs[0].kind == SymbolKind.CLASS

    method_defs = find_symbol_definitions(SAMPLE_CODE, "compute", path="service.py")
    assert len(method_defs) == 1
    assert method_defs[0].line == 13


def test_find_symbol_references() -> None:
    refs = find_symbol_references(SAMPLE_CODE, "CalculationService", path="service.py")
    # Defined on line 7, used on line 18
    assert len(refs) >= 2
    lines = [r.line for r in refs]
    assert 7 in lines
    assert 18 in lines
