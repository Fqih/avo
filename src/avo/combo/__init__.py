"""Combo routing: multi-tier model orchestration and failover."""

from __future__ import annotations

from avo.combo.cli import ComboCliError
from avo.combo.detector import classify_failover_reason, is_quota_or_rate_limit_error
from avo.combo.models import ComboProfile, ComboTier
from avo.combo.provider import ComboRouterProvider
from avo.combo.store import (
    combos_file_path,
    delete_combo,
    get_combo,
    load_combos,
    save_combo,
)

__all__ = [
    "ComboCliError",
    "ComboProfile",
    "ComboRouterProvider",
    "ComboTier",
    "classify_failover_reason",
    "combos_file_path",
    "delete_combo",
    "get_combo",
    "is_quota_or_rate_limit_error",
    "load_combos",
    "save_combo",
]
