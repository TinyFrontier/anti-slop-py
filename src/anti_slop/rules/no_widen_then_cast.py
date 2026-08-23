"""``no-widen-then-cast`` -- reject "widen the type, then cast it back".

PLAN.md section 3.4, row 14 (port of ``no-widen-then-assert``, the longest rule of the
original). The pattern is a three-step local flow inside one scope::

    u: User = load()          # 1. a binding with a narrow, proven type
    raw: Any = u              # 2. an explicit widening of that binding
    user = cast(User, raw)    # 3. the narrow type claimed back by force

Every step is legal, and together they are a round trip that ends exactly where it
started -- except that the type checker's own evidence was thrown away at step 2 and
replaced at step 3 by an assertion nobody verified. Step 3 is what gets reported,
because that is the line that fabricates the evidence; the message names the whole
chain so the fix (delete step 2) is visible from the diagnostic alone.

A direct ``cast(User, u)`` with no widening in between is *not* this rule -- it is a
plain unverified assertion, which ``require-safety-comment`` handles.

What counts as each step
------------------------

**Step 1, a narrow binding.** An ``AnnAssign`` with a value and an annotation that is
not ``Any``/``object`` (``u: User = load()``), an ``Assign`` of a constructor call
(``u = User(...)``, recognized by the callee's name starting with a capital -- a
heuristic, since a syntax-only rule cannot know what is a class), or a parameter of
the enclosing function carrying a non-``Any``/``object`` annotation. Parameters count
deliberately: ``def handle(u: User)`` is exactly as proven as ``u: User = load()`` --
it is the checker's own guarantee -- so widening a parameter and casting it back is
the same round trip and is reported.

**Step 2, an explicit widening.** An ``AnnAssign`` whose annotation *is* ``Any`` or
``object`` and whose value is a bare name from step 1 (or from a previous step 2, so
``u`` -> ``raw`` -> ``boxed`` chains are followed). A plain ``raw = u`` is not a
widening: it carries no annotation, so the checker still knows the type and nothing
was thrown away.

**Step 3, the cast back.** ``cast(T, raw)`` (or ``cast("T", raw)``) on a widened name,
where ``T`` is any type other than ``Any``/``object`` -- casting back to ``Any``
claims nothing narrow and is not this pattern. The target need not be the original
type: the round trip is the defect, not the choice of destination.

Scope and conservatism
----------------------

Analysis is straight-line and per scope: the statement lists of the module body and
of each function body, in order. It never descends into ``if``/``for``/``while``/
``with``/``try``/``match`` bodies, and never crosses a function boundary -- each
nested function is analysed on its own, with its own parameters as seeds, so a chain
started in an enclosing scope cannot be completed inside a closure. Class bodies are
not analysed either; methods inside them are, as functions.

Any statement that stores into a tracked name -- including a compound statement that
assigns to it somewhere inside its branches -- drops that name from the analysis. So

.. code-block:: python

    u: User = load()
    raw: Any = u
    if fallback:
        raw = fetch_raw()
    user = cast(User, raw)     # not reported: `raw` may no longer come from `u`

stays silent. The bias is deliberate: a missed round trip costs nothing, a false one
teaches the reader to ignore the linter. Lambdas and nested ``def``\\ s inside an
otherwise straight-line statement are skipped when looking for casts, for the same
reason -- their bodies run later, under a binding this pass cannot see.

Callee detection for ``cast`` is syntactic, with the same known limitation as
``no-chained-casts`` and ``require-safety-comment``: a bare name ``cast`` or any
attribute access ending in ``.cast``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule, on
from anti_slop.rules._annotations import (
    is_any_annotation,
    is_object_annotation,
    is_slop_annotation,
)

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "no-widen-then-cast"

# Statements whose bodies this rule does not follow: control flow, and the scopes
# that are analysed separately (or not at all).
_COMPOUND_STATEMENTS = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.TryStar,
    ast.Match,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)

# Expression scopes that run later, under bindings this straight-line pass cannot see.
_DEFERRED_SCOPES = (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

_MESSAGE = (
    "`{widened}` is `{narrow}` widened to `{annotation}` a few lines up, and this"
    " `cast` claims a narrow type back from it. The evidence was already there: this"
    " code threw it away at `{widened}: {annotation} = {narrow}` and then asserted it"
    " again by force, so the checker agrees and nothing was verified. Delete the"
    " widening step and use `{narrow}` directly, with the type it already carries. If"
    " something in between genuinely requires `{annotation}`, give that function a"
    " narrow parameter type instead of widening the value at its call site."
)


@dataclass(frozen=True, slots=True)
class _Widening:
    """A name currently holding an explicitly widened copy of a proven binding."""

    narrow: str
    annotation: str


def _check_module(context: RuleContext, node: ast.Module) -> None:
    _analyze(context, node.body, seeds=frozenset())


def _check_function(
    context: RuleContext, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> None:
    _analyze(context, node.body, seeds=_annotated_parameters(node.args))


def _analyze(
    context: RuleContext, body: Sequence[ast.stmt], seeds: frozenset[str]
) -> None:
    """Walk one statement list in order, tracking narrow bindings and their widenings."""
    narrow: set[str] = set(seeds)
    widened: dict[str, _Widening] = {}

    for statement in body:
        if not isinstance(statement, _COMPOUND_STATEMENTS):
            _report_casts(context, statement, widened)
        binding = _binding_of(statement, narrow, widened)
        for name in _stored_names(statement):
            narrow.discard(name)
            widened.pop(name, None)
        _apply(binding, narrow, widened)


def _report_casts(
    context: RuleContext, statement: ast.stmt, widened: dict[str, _Widening]
) -> None:
    for call in _iter_calls(statement):
        name = _cast_of_widened_name(call)
        if name is None:
            continue
        widening = widened.get(name)
        if widening is None:
            continue
        context.report(
            call,
            "widen_then_cast",
            widened=name,
            narrow=widening.narrow,
            annotation=widening.annotation,
        )


def _iter_calls(node: ast.AST) -> Iterator[ast.Call]:
    """Every call in ``node``, excluding those inside a lambda or a nested definition."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _DEFERRED_SCOPES):
            continue
        if isinstance(child, ast.Call):
            yield child
        yield from _iter_calls(child)


def _cast_of_widened_name(call: ast.Call) -> str | None:
    """The name cast back to a narrow type by ``call``, if that is what it is."""
    if not _is_cast_callee(call.func):
        return None
    target = _argument(call, index=0, keyword="typ")
    if target is None or is_slop_annotation(target):
        return None
    match _argument(call, index=1, keyword="val"):
        case ast.Name(id=name):
            return name
        case _:
            return None


def _binding_of(
    statement: ast.stmt, narrow: set[str], widened: dict[str, _Widening]
) -> tuple[str, _Widening | None] | None:
    """What ``statement`` binds, read *before* its own stores are invalidated.

    Returns ``(name, widening)`` for step 2, ``(name, None)`` for step 1, and ``None``
    when the statement establishes neither.
    """
    match statement:
        case ast.AnnAssign(target=ast.Name(id=name), annotation=annotation, value=value):
            if value is None:
                return None
            widening_annotation = _widening_annotation_name(annotation)
            if widening_annotation is None:
                return (name, None)
            source = _widening_source(value, narrow, widened)
            if source is None:
                return None
            return (name, _Widening(narrow=source, annotation=widening_annotation))
        case ast.Assign(targets=[ast.Name(id=name)], value=value) if (
            _is_constructor_call(value)
        ):
            return (name, None)
        case _:
            return None


def _apply(
    binding: tuple[str, _Widening | None] | None,
    narrow: set[str],
    widened: dict[str, _Widening],
) -> None:
    if binding is None:
        return
    name, widening = binding
    if widening is None:
        narrow.add(name)
    else:
        widened[name] = widening


def _widening_source(
    value: ast.expr, narrow: set[str], widened: dict[str, _Widening]
) -> str | None:
    """The proven binding behind ``value``, following an existing widening chain."""
    match value:
        case ast.Name(id=name):
            if name in narrow:
                return name
            existing = widened.get(name)
            return existing.narrow if existing is not None else None
        case _:
            return None


def _widening_annotation_name(annotation: ast.expr) -> str | None:
    """``"Any"``/``"object"`` when ``annotation`` widens, ``None`` when it narrows."""
    if is_any_annotation(annotation):
        return "Any"
    if is_object_annotation(annotation):
        return "object"
    return None


def _is_constructor_call(value: ast.expr) -> bool:
    """``User(...)`` / ``models.User(...)`` -- a call whose callee reads as a class.

    A syntax-only rule cannot know what a name refers to, so this is the usual
    capitalized-callee heuristic. Getting it wrong in either direction only changes
    whether step 1 is recognized, never whether an unrelated cast is reported.
    """
    match value:
        case ast.Call(func=ast.Name(id=name)) | ast.Call(func=ast.Attribute(attr=name)):
            return name[:1].isupper()
        case _:
            return False


def _annotated_parameters(arguments: ast.arguments) -> frozenset[str]:
    """Parameters whose annotation proves a narrow type, as step-1 seeds."""
    return frozenset(
        parameter.arg
        for parameter in _iter_parameters(arguments)
        if parameter.annotation is not None
        and not is_slop_annotation(parameter.annotation)
    )


def _iter_parameters(arguments: ast.arguments) -> Iterator[ast.arg]:
    yield from arguments.posonlyargs
    yield from arguments.args
    if arguments.vararg is not None:
        yield arguments.vararg
    yield from arguments.kwonlyargs
    if arguments.kwarg is not None:
        yield arguments.kwarg


def _stored_names(statement: ast.stmt) -> frozenset[str]:
    """Every name ``statement`` may bind or delete, however deeply nested.

    Deliberately over-broad: an assignment hidden inside an ``if`` branch, a ``with``
    target, a ``match`` capture, an import or a nested ``def`` all count, because any
    of them can make a tracked name mean something else by the time the cast runs.
    Parameters of nested functions are excluded -- they shadow only inside their own
    body and cannot change the meaning of a name out here.
    """
    names: set[str] = set()
    for node in ast.walk(statement):
        match node:
            case ast.Name(id=name, ctx=ast.Store() | ast.Del()):
                names.add(name)
            case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) | ast.ClassDef(name=name):
                names.add(name)
            case ast.ExceptHandler(name=str() as name):
                names.add(name)
            case ast.Global(names=declared) | ast.Nonlocal(names=declared):
                names.update(declared)
            case ast.alias(name=imported, asname=alias):
                names.add(alias if alias is not None else imported.split(".")[0])
            case ast.MatchAs(name=str() as name) | ast.MatchStar(name=str() as name):
                names.add(name)
            case ast.MatchMapping(rest=str() as name):
                names.add(name)
            case _:
                continue
    return frozenset(names)


def _is_cast_callee(func: ast.expr) -> bool:
    """A bare ``cast`` name or any attribute access ending in ``.cast``.

    Duplicated from ``no_chained_casts`` and ``require_safety_comment`` on purpose:
    each rule file stays readable and editable on its own once vendored.
    """
    match func:
        case ast.Name(id="cast"):
            return True
        case ast.Attribute(attr="cast"):
            return True
        case _:
            return False


def _argument(call: ast.Call, *, index: int, keyword: str) -> ast.expr | None:
    """Positional argument ``index`` of ``call``, or its ``keyword=`` spelling."""
    if len(call.args) > index:
        return call.args[index]
    for entry in call.keywords:
        if entry.arg == keyword:
            return entry.value
    return None


RULE = Rule(
    id=RULE_ID,
    description=(
        "A value must not be widened to `Any`/`object` and then cast back to a narrow"
        " type: the evidence was already there before the widening."
    ),
    messages={"widen_then_cast": _MESSAGE},
    handlers=(
        on(ast.Module, _check_module),
        on(ast.FunctionDef, _check_function),
        on(ast.AsyncFunctionDef, _check_function),
    ),
)
