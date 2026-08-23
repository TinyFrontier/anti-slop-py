"""anti-slop-py -- an opinionated linter for low-evidence Python patterns.

This module is the registry of core rules. Opt-in groups (PLAN.md section 3.5) live
under ``anti_slop.contrib`` and are never registered here.
"""

from __future__ import annotations

from collections.abc import Mapping

from anti_slop.engine.rule import Rule
from anti_slop.rules.no_object_parameters import RULE as NO_OBJECT_PARAMETERS

__all__ = ["CORE_RULES", "RULES_BY_ID", "__version__"]

__version__ = "0.1.0"

CORE_RULES: tuple[Rule, ...] = (NO_OBJECT_PARAMETERS,)

RULES_BY_ID: Mapping[str, Rule] = {rule.id: rule for rule in CORE_RULES}
