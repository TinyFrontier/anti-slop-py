"""The ``fastapi`` opt-in group: framework policy the core rules cannot state.

Turn it on in ``pyproject.toml``::

    [tool.anti-slop]
    groups = ["fastapi"]

Every rule here is named by its full, prefixed id -- in configuration, in
diagnostics, and in ``# anti-slop: ignore[fastapi/...]`` suppressions:

* ``fastapi/no-dict-body-parameters`` -- a route body must be a pydantic model, not
  ``dict``/``Mapping``/``Any``.
* ``fastapi/no-untyped-route-response`` -- a route must declare its response type.
* ``fastapi/no-raw-request-parsing`` -- a route must not decode the body off
  ``Request`` behind the validating boundary.
* ``fastapi/no-state-attribute-access`` -- ``app.state`` is an untyped grab bag;
  provide the value with ``Depends`` instead.

The group also **retargets one core rule**. ``no-module-mocking`` states a
framework-independent problem and then offers a framework-independent recipe
("take the collaborator as an argument"); in a FastAPI project the seam already
exists, so :data:`CORE_MESSAGE_OVERRIDES` replaces the recipe half with
``app.dependency_overrides``. The problem half is imported from the core rule rather
than restated, so the two messages cannot drift apart.

Enabling the group is a claim about the repository, not about a file: it is meant for
a project with a *direct* dependency on FastAPI. A framework that appears only
transitively in a lockfile is not a reason to turn these rules on.
"""

from __future__ import annotations

from collections.abc import Mapping

from anti_slop.contrib.fastapi.rules.no_dict_body_parameters import (
    RULE as NO_DICT_BODY_PARAMETERS,
)
from anti_slop.contrib.fastapi.rules.no_raw_request_parsing import (
    RULE as NO_RAW_REQUEST_PARSING,
)
from anti_slop.contrib.fastapi.rules.no_state_attribute_access import (
    RULE as NO_STATE_ATTRIBUTE_ACCESS,
)
from anti_slop.contrib.fastapi.rules.no_untyped_route_response import (
    RULE as NO_UNTYPED_ROUTE_RESPONSE,
)
from anti_slop.engine.rule import Rule
from anti_slop.rules.no_module_mocking import MESSAGE_PROBLEM
from anti_slop.rules.no_module_mocking import RULE_ID as NO_MODULE_MOCKING_ID

__all__ = [
    "CORE_MESSAGE_OVERRIDES",
    "GROUP_NAME",
    "GROUP_RULES",
    "MOCKING_RECIPE",
]

GROUP_NAME = "fastapi"

GROUP_RULES: tuple[Rule, ...] = (
    NO_DICT_BODY_PARAMETERS,
    NO_RAW_REQUEST_PARSING,
    NO_STATE_ATTRIBUTE_ACCESS,
    NO_UNTYPED_ROUTE_RESPONSE,
)

# The FastAPI half of `no-module-mocking`'s diagnostic: same problem statement,
# framework-native recipe.
MOCKING_RECIPE = (
    "In a FastAPI project that seam already exists. Declare the collaborator as a"
    " dependency of the route -- `thing: Thing = Depends(get_thing)`, or the"
    " `Annotated[Thing, Depends(get_thing)]` spelling -- and replace it in the test"
    " with `app.dependency_overrides[get_thing] = lambda: fake`, clearing the entry"
    " afterwards (`app.dependency_overrides.clear()`, or a fixture that restores the"
    " previous mapping). The override is typed, it is the framework's own hook rather"
    " than a reach into someone else's module, and it survives every rename and"
    " re-export that would silently turn a patch into a no-op. If the dependency is"
    " buried too deep to inject today, lift it into a provider function first; that"
    " refactoring is what the patch was standing in for."
)

CORE_MESSAGE_OVERRIDES: Mapping[str, Mapping[str, str]] = {
    NO_MODULE_MOCKING_ID: {"module-mock": f"{MESSAGE_PROBLEM} {MOCKING_RECIPE}"},
}
