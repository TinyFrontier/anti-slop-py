"""``no-dynamic-dispatch`` -- reject dispatch by namespace lookup or by a runtime
attribute/method name (port of ``no-reflect-apply``).

Two shapes are flagged, the Python equivalents of TypeScript's ``Reflect.apply``:

* **Namespace subscription** -- ``globals()[name]``, ``locals()[name]`` and
  ``vars(obj)[name]``, whether or not the result is then called
  (``globals()[name]()``). The subscript itself is what is flagged, so a subsequent
  call on the result does not double-report: ``globals()[name]`` and
  ``globals()[name]()`` both produce exactly one diagnostic, anchored at the
  subscript.
* **``operator.attrgetter``/``operator.methodcaller``** -- matched as a bare name
  (``attrgetter(...)``) or any attribute access ending in
  ``.attrgetter``/``.methodcaller`` (``operator.attrgetter(...)``).
  ``operator.itemgetter`` is deliberately **not** flagged: it dispatches by sequence
  index or mapping key, not by attribute/method name, and is the ordinary idiomatic
  way to build a sort key or projection -- flagging it would ban a pattern with no
  dispatch-by-name character at all.

Callee detection for the namespace case matches only the bare builtin names
``globals``/``locals``/``vars`` -- not an attribute access such as
``some_module.globals()``.
"""

from __future__ import annotations

import ast

from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import (
    CONFIDENCE_HIGH,
    FIX_NONE,
    TIER_ESCAPE_HATCH,
    Rule,
    RuleMetadata,
    on,
)

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "no-dynamic-dispatch"

# `globals()`/`locals()`/`vars(...)`, matched as bare builtin calls only.
_NAMESPACE_CALL_NAMES = frozenset({"globals", "locals", "vars"})

# `attrgetter`/`methodcaller`, matched as a bare name or the trailing attribute of a
# qualified name (`operator.attrgetter`, `operator.methodcaller`). `itemgetter` is
# intentionally excluded -- it dispatches by index/key, not by name.
_DISPATCH_FACTORY_NAMES = frozenset({"attrgetter", "methodcaller"})

_MESSAGE_NAMESPACE = (
    "`{source}[...]` dispatches by looking a name up in {description}: any string"
    " reaches this lookup, so neither the reader nor the type checker can enumerate"
    " what could actually run. Replace it with an explicit"
    " `Mapping[Literal[...], Callable[...]]` naming every valid key, or branch"
    " explicitly on the domain value instead of the name that happens to be bound in"
    " this namespace."
)

_MESSAGE_FACTORY = (
    "`{name}(...)` builds a callable that dispatches by an attribute/method name"
    " chosen at the call site: nothing enumerates which attribute or method actually"
    " gets invoked. Replace it with an explicit"
    " `Mapping[Literal[...], Callable[...]]` or explicit branching on the domain"
    " value instead of dispatching by name."
)


def _check_subscript(context: RuleContext, node: ast.Subscript) -> None:
    if not _is_namespace_call(node.value):
        return
    context.report(
        node,
        "namespace_subscript",
        source=ast.unparse(node.value),
        description=_namespace_description(node.value),
    )


def _check_call(context: RuleContext, node: ast.Call) -> None:
    name = _dispatch_factory_name(node.func)
    if name is None:
        return
    context.report(node, "dispatch_factory", name=name)


def _is_namespace_call(node: ast.expr) -> bool:
    match node:
        case ast.Call(func=ast.Name(id=name)) if name in _NAMESPACE_CALL_NAMES:
            return True
        case _:
            return False


def _namespace_description(node: ast.expr) -> str:
    match node:
        case ast.Call(func=ast.Name(id="globals")):
            return "the module's global namespace"
        case ast.Call(func=ast.Name(id="locals")):
            return "the current local namespace"
        case ast.Call(func=ast.Name(id="vars")):
            return "an object's `__dict__`"
        case _:
            return "a runtime namespace"


def _dispatch_factory_name(func: ast.expr) -> str | None:
    match func:
        case ast.Name(id=name) if name in _DISPATCH_FACTORY_NAMES:
            return name
        case ast.Attribute(attr=attr) if attr in _DISPATCH_FACTORY_NAMES:
            return attr
        case _:
            return None


_METADATA = RuleMetadata(
    tier=TIER_ESCAPE_HATCH,
    confidence=CONFIDENCE_HIGH,
    fix=FIX_NONE,
    tags=("reflection",),
    problem=(
        "Dispatching by a name computed at runtime turns an entire namespace into an"
        " implicit dispatch table that nothing enumerates. `globals()`, `locals()`"
        " and `vars(obj)` each return a plain dict, so subscripting one by a string"
        " means any string reaches the lookup; `attrgetter`/`methodcaller` do the"
        " same through a factory instead of `[]`. Neither the reader nor the type"
        " checker can say what could actually run, and a rename anywhere in the"
        " module silently changes the set of reachable targets."
    ),
    recipe=(
        "Write the dispatch table out: a `Mapping[Literal[...], Callable[...]]` that"
        " names every valid key, so the set of targets is enumerable, checkable and"
        " greppable. Where there are only a handful of cases, branch explicitly on"
        " the domain value instead of on the name that happens to be bound in this"
        " namespace -- and parse the input that produced the name into that domain"
        " value at the I/O boundary."
    ),
    when_to_disable=(
        "A module whose job really is reflective -- a plugin loader that discovers"
        " entry points, a REPL, a serialization framework walking arbitrary objects"
        " -- takes a per-line suppression at the two or three places that do the"
        " lookup. Turning the rule off wholesale is worth it only in a codebase built"
        " around dynamic dispatch as its architecture, where every finding would be"
        " the design working as intended."
    ),
    fp_caveats=(
        "The namespace builtins are matched by bare name, with no scope resolution"
        " behind them, so a local variable or parameter that shadows `globals`,"
        " `locals` or `vars` is indistinguishable from the builtin and is still"
        " flagged. `attrgetter`/`methodcaller` are matched by trailing attribute"
        " name, so an unrelated object with a method of that name is flagged too."
        " `itemgetter` is excluded by design and never reported."
    ),
)

RULE = Rule(
    id=RULE_ID,
    description=(
        "Dispatch must not go through a namespace subscript or an"
        " attrgetter/methodcaller built from a runtime name."
    ),
    messages={
        "namespace_subscript": _MESSAGE_NAMESPACE,
        "dispatch_factory": _MESSAGE_FACTORY,
    },
    handlers=(
        on(ast.Subscript, _check_subscript),
        on(ast.Call, _check_call),
    ),
    metadata=_METADATA,
)
