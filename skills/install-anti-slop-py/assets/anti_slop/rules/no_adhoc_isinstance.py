"""``no-adhoc-isinstance`` -- reject ``isinstance``/``issubclass`` branching outside a
type-guard function (port of ``no-runtime-typeof``).

Only ``isinstance``/``issubclass`` calls that sit *outside* a function whose contract
names the narrowing it performs are flagged. In Python ``isinstance`` is the blessed
narrowing mechanism -- ``TypeGuard`` and ``TypeIs`` are themselves defined in terms of
a function whose body calls it -- so a rule that banned it everywhere would ban the
language's own type system.

**Default is a reversal of the original.** ``allow-in-type-guards`` (default
``True``) exempts calls inside a function whose return annotation is
``TypeGuard[...]``/``TypeIs[...]`` -- the bare name, the ``typing.TypeGuard`` /
``typing_extensions.TypeIs`` attribute form, and the quoted-string spelling of
either. Setting the option to ``False`` switches to a strict mode for
pydantic/msgspec-first codebases: every ``isinstance``/``issubclass`` call is
flagged, guard function or not.

**"Inside a type-guard function" is resolved structurally, not lexically nearest in
scope.** Climb ``context.ancestors(node)`` to the *nearest* ``def``/``async def``
ancestor -- that function's own return annotation decides, and nothing else does. A
function nested inside a guard, with no ``TypeGuard``/``TypeIs`` annotation of its
own, is therefore flagged even though its enclosing function is a guard: the nested
function is a separate contract that happens to be defined in the same place, and its
own signature says nothing about narrowing.

Callee detection matches only the bare builtin names ``isinstance``/``issubclass`` --
not an attribute access such as ``some_module.isinstance`` -- consistent with this
rule looking for the actual builtins, not anything merely named alike.

``match``/``case`` class patterns (``case Point(x=x, y=y):``) are structural pattern
matching and are not walked by this rule at all, so they never need a guard function
to stay unflagged.
"""

from __future__ import annotations

import ast

from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import (
    CONFIDENCE_POLICY,
    FIX_NONE,
    TIER_ARCHITECTURAL,
    BoolOption,
    Rule,
    RuleMetadata,
    on,
)

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "no-adhoc-isinstance"

# The two builtins this rule looks for, as bare names only.
_CHECK_NAMES = frozenset({"isinstance", "issubclass"})

# `TypeGuard`/`TypeIs`, matched as a bare name or the trailing attribute of a
# qualified name (`typing.TypeGuard`, `typing_extensions.TypeIs`).
_TYPE_GUARD_NAMES = frozenset({"TypeGuard", "TypeIs"})

_ALLOW_IN_TYPE_GUARDS = BoolOption(
    name="allow-in-type-guards",
    default=True,
    description=(
        "Exempt isinstance()/issubclass() calls inside a function whose return"
        " annotation is TypeGuard[...]/TypeIs[...]. Set to false for a strict mode"
        " that flags every call regardless of context."
    ),
)

_MESSAGE = (
    "`{name}(...)` branches on representation instead of a decoded contract: every"
    " caller that happens to pass the checked type type-checks the same as one that"
    " does not, and nothing downstream is proven about the value. Decode the input"
    " into a domain type at the I/O boundary (a pydantic/msgspec model or a"
    " dataclass) and branch on that domain value instead. If this narrowing is"
    " genuinely reusable, extract it into its own function annotated"
    " `-> TypeIs[...]` so the type checker, not just this call site, understands the"
    " narrowing."
)


def _check_call(context: RuleContext, node: ast.Call) -> None:
    name = _check_name(node.func)
    if name is None:
        return
    if context.bool_option(_ALLOW_IN_TYPE_GUARDS.name) and _inside_type_guard(
        context, node
    ):
        return
    context.report(node, "adhoc_check", name=name)


def _check_name(func: ast.expr) -> str | None:
    """A bare ``isinstance``/``issubclass`` name; never an attribute access."""
    match func:
        case ast.Name(id=name) if name in _CHECK_NAMES:
            return name
        case _:
            return None


def _inside_type_guard(context: RuleContext, node: ast.AST) -> bool:
    enclosing = _nearest_def(context, node)
    if enclosing is None or enclosing.returns is None:
        return False
    return _is_type_guard_annotation(enclosing.returns)


def _nearest_def(
    context: RuleContext, node: ast.AST
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The nearest enclosing ``def``/``async def``, skipping every other ancestor kind."""
    for ancestor in context.ancestors(node):
        match ancestor:
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                return ancestor
            case _:
                continue
    return None


def _is_type_guard_annotation(annotation: ast.expr) -> bool:
    """True for ``TypeGuard[...]``/``TypeIs[...]``, bare, attribute, or quoted."""
    match annotation:
        case ast.Subscript(value=value):
            return _is_type_guard_name(value)
        case ast.Constant(value=str() as text):
            prefix = text.strip().split("[", 1)[0].strip()
            return prefix.rsplit(".", 1)[-1] in _TYPE_GUARD_NAMES
        case _:
            return _is_type_guard_name(annotation)


def _is_type_guard_name(value: ast.expr) -> bool:
    match value:
        case ast.Name(id=name) if name in _TYPE_GUARD_NAMES:
            return True
        case ast.Attribute(attr=attr) if attr in _TYPE_GUARD_NAMES:
            return True
        case _:
            return False


_METADATA = RuleMetadata(
    tier=TIER_ARCHITECTURAL,
    confidence=CONFIDENCE_POLICY,
    fix=FIX_NONE,
    tags=("typing", "narrowing"),
    problem=(
        "An inline `isinstance` branch re-derives a claim about a value's shape at"
        " the point of use instead of deciding it once at a boundary. Every caller"
        " that happens to pass the checked type type-checks the same as one that does"
        " not, nothing downstream is proven, and the same check tends to reappear at"
        " every site that touches the value. This is the port of the original's"
        " `no-runtime-typeof`, and it is a policy about where narrowing belongs, not"
        " a claim that `isinstance` is wrong."
    ),
    recipe=(
        "Decode the input into a domain type at the I/O boundary -- a"
        " pydantic/msgspec model or a dataclass -- and branch on that domain value,"
        " so the narrowing happens once where the data arrives. Where the narrowing"
        " is genuinely reusable, extract it into its own function annotated"
        " `-> TypeIs[...]`: the guard then becomes a named, checkable contract that"
        " the type checker understands at every call site."
    ),
    when_to_disable=(
        "This is the bluntest rule in the set, and its default is intentionally"
        " stricter than idiomatic Python: a plain `if isinstance(value, Foo)` outside"
        " a guard function is flagged even with `allow-in-type-guards = true`."
        " Suggested postures: `error` or `warn` in a business backend, `off` or"
        " `warn` in a framework or library, `warn` in ML/scientific code. Turn it off"
        " outright where the domain objects are themselves classes to branch on -- an"
        " AST analyzer, a visitor over a node hierarchy -- because branching on them"
        " is exactly what this rule's own recipe prescribes; anti-slop's own"
        " repository does this. `allow-in-type-guards = false` is the opposite move,"
        " for pydantic/msgspec-first codebases that want every call flagged."
    ),
    fp_caveats=(
        "The builtins are matched by bare name with no scope resolution, so a local"
        " variable or parameter shadowing `isinstance`/`issubclass` is still flagged."
        " `Inside a type-guard function` is resolved structurally: the *nearest*"
        " enclosing `def` decides, so a helper nested inside a guard is judged on its"
        " own signature. Legitimate patterns that live outside any guard --"
        " `functools.singledispatch` implementations, `__eq__` bodies returning"
        " `NotImplemented` for an unrecognized type -- need a per-line suppression."
    ),
)

RULE = Rule(
    id=RULE_ID,
    description=(
        "isinstance()/issubclass() must live inside a TypeGuard/TypeIs function, not"
        " branch ad hoc."
    ),
    messages={"adhoc_check": _MESSAGE},
    handlers=(on(ast.Call, _check_call),),
    metadata=_METADATA,
    options=(_ALLOW_IN_TYPE_GUARDS,),
)
