"""anti-slop-py -- an opinionated linter for low-evidence Python patterns.

This module is the registry of core rules. Opt-in groups live
under ``anti_slop.contrib`` and are never registered here.
"""

from __future__ import annotations

from collections.abc import Mapping

from anti_slop.engine.rule import Rule
from anti_slop.rules.no_adhoc_isinstance import RULE as NO_ADHOC_ISINSTANCE
from anti_slop.rules.no_any_parameters import RULE as NO_ANY_PARAMETERS
from anti_slop.rules.no_any_returns import RULE as NO_ANY_RETURNS
from anti_slop.rules.no_any_type_aliases import RULE as NO_ANY_TYPE_ALIASES
from anti_slop.rules.no_chained_casts import RULE as NO_CHAINED_CASTS
from anti_slop.rules.no_conditional_empty_dict_spread import (
    RULE as NO_CONDITIONAL_EMPTY_DICT_SPREAD,
)
from anti_slop.rules.no_dynamic_dispatch import RULE as NO_DYNAMIC_DISPATCH
from anti_slop.rules.no_known_value_widening import RULE as NO_KNOWN_VALUE_WIDENING
from anti_slop.rules.no_module_mocking import RULE as NO_MODULE_MOCKING
from anti_slop.rules.no_object_parameters import RULE as NO_OBJECT_PARAMETERS
from anti_slop.rules.no_shape_in_symbol_names import (
    # anti-slop: ignore[no-shape-in-symbol-names]
    RULE as NO_SHAPE_IN_SYMBOL_NAMES,
)
from anti_slop.rules.no_string_attribute_access import (
    RULE as NO_STRING_ATTRIBUTE_ACCESS,
)
from anti_slop.rules.no_unsafe_dict_values import RULE as NO_UNSAFE_DICT_VALUES
from anti_slop.rules.no_widen_then_cast import RULE as NO_WIDEN_THEN_CAST
from anti_slop.rules.require_safety_comment import RULE as REQUIRE_SAFETY_COMMENT

__all__ = ["CORE_RULES", "RULES_BY_ID", "__version__"]

__version__ = "0.1.0"

CORE_RULES: tuple[Rule, ...] = (
    NO_ADHOC_ISINSTANCE,
    NO_ANY_PARAMETERS,
    NO_ANY_RETURNS,
    NO_ANY_TYPE_ALIASES,
    NO_CHAINED_CASTS,
    NO_CONDITIONAL_EMPTY_DICT_SPREAD,
    NO_DYNAMIC_DISPATCH,
    NO_KNOWN_VALUE_WIDENING,
    NO_MODULE_MOCKING,
    NO_OBJECT_PARAMETERS,
    NO_SHAPE_IN_SYMBOL_NAMES,
    NO_STRING_ATTRIBUTE_ACCESS,
    NO_UNSAFE_DICT_VALUES,
    NO_WIDEN_THEN_CAST,
    REQUIRE_SAFETY_COMMENT,
)

RULES_BY_ID: Mapping[str, Rule] = {rule.id: rule for rule in CORE_RULES}
