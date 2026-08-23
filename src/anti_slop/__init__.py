"""anti-slop-py -- an opinionated linter for low-evidence Python patterns.

This module is the registry of core rules. Opt-in groups (PLAN.md section 3.5) live
under ``anti_slop.contrib`` and are never registered here.
"""

from __future__ import annotations

from collections.abc import Mapping

from anti_slop.engine.rule import Rule
from anti_slop.rules.no_any_parameters import RULE as NO_ANY_PARAMETERS
from anti_slop.rules.no_any_returns import RULE as NO_ANY_RETURNS
from anti_slop.rules.no_chained_casts import RULE as NO_CHAINED_CASTS
from anti_slop.rules.no_conditional_empty_dict_spread import (
    RULE as NO_CONDITIONAL_EMPTY_DICT_SPREAD,
)
from anti_slop.rules.no_known_value_widening import RULE as NO_KNOWN_VALUE_WIDENING
from anti_slop.rules.no_object_parameters import RULE as NO_OBJECT_PARAMETERS
from anti_slop.rules.no_shape_in_symbol_names import (
    # anti-slop: ignore[no-shape-in-symbol-names]
    RULE as NO_SHAPE_IN_SYMBOL_NAMES,
)
from anti_slop.rules.no_unsafe_dict_values import RULE as NO_UNSAFE_DICT_VALUES

__all__ = ["CORE_RULES", "RULES_BY_ID", "__version__"]

__version__ = "0.1.0"

CORE_RULES: tuple[Rule, ...] = (
    NO_ANY_PARAMETERS,
    NO_ANY_RETURNS,
    NO_CHAINED_CASTS,
    NO_CONDITIONAL_EMPTY_DICT_SPREAD,
    NO_KNOWN_VALUE_WIDENING,
    NO_OBJECT_PARAMETERS,
    NO_SHAPE_IN_SYMBOL_NAMES,
    NO_UNSAFE_DICT_VALUES,
)

RULES_BY_ID: Mapping[str, Rule] = {rule.id: rule for rule in CORE_RULES}
