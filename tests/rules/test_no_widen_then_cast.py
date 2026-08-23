"""Tests for the ``no-widen-then-cast`` rule (PLAN.md section 3.4, row 14)."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid, run_rule

from anti_slop.rules.no_widen_then_cast import RULE

VALID_SNIPPETS = [
    # A cast with no widening in between is require-safety-comment's business.
    """
    u: User = load()
    user = cast(User, u)
    """,
    # A re-binding without an annotation is not an explicit widening: the checker
    # still knows the type, so nothing was thrown away.
    """
    u: User = load()
    raw = u
    user = cast(User, raw)
    """,
    # The widened name is reassigned before the cast: the chain is broken.
    """
    u: User = load()
    raw: Any = u
    raw = fetch_raw()
    user = cast(User, raw)
    """,
    # Reassigned inside a branch this rule does not follow -- conservatively silent.
    """
    u: User = load()
    raw: Any = u
    if fallback:
        raw = fetch_raw()
    user = cast(User, raw)
    """,
    # A chain never crosses a function boundary.
    """
    def outer() -> None:
        u: User = load()
        raw: Any = u

        def inner() -> User:
            return cast(User, raw)
    """,
    # Casting back to `Any` claims nothing narrow, so it is not this pattern.
    """
    u: User = load()
    raw: Any = u
    boxed = cast(Any, raw)
    """,
    # Nothing narrow was ever proven: `raw` is widened from a bare call result.
    """
    raw: Any = fetch_raw()
    user = cast(User, raw)
    """,
    # An annotation without a value proves nothing about a value.
    """
    u: User
    raw: Any = u
    user = cast(User, raw)
    """,
    # A lambda body runs later, under bindings this straight-line pass cannot see.
    """
    u: User = load()
    raw: Any = u
    make = lambda: cast(User, raw)
    """,
    # The widening happens after the cast, not before it.
    """
    u: User = load()
    user = cast(User, raw)
    raw: Any = u
    """,
    # A lower-cased callee does not read as a constructor, so step 1 is not proven.
    """
    u = load_user()
    raw: Any = u
    user = cast(User, raw)
    """,
    # The cast value is an expression, not the widened name itself.
    """
    u: User = load()
    raw: Any = u
    user = cast(User, raw.inner)
    """,
]

INVALID_SNIPPETS = [
    # The canonical chain, at module level.
    ("""
    u: User = load()
    raw: Any = u
    user = cast(User, raw)
    """, 1),
    # The same chain inside a function.
    ("""
    def handle() -> User:
        u: User = load()
        raw: Any = u
        return cast(User, raw)
    """, 1),
    # ... and inside an async function.
    ("""
    async def handle() -> User:
        u: User = await load()
        raw: object = u
        return cast(User, raw)
    """, 1),
    # A constructor call is a proven narrow binding too.
    ("""
    u = User(name="ada")
    raw: Any = u
    user = cast(User, raw)
    """, 1),
    # Two hops: the chain is followed through an intermediate widened name.
    ("""
    u: User = load()
    raw: Any = u
    boxed: object = raw
    user = cast(User, boxed)
    """, 1),
    # A parameter's annotation is the checker's own guarantee, so it seeds step 1.
    ("""
    def handle(u: User) -> User:
        raw: Any = u
        return cast(User, raw)
    """, 1),
    # A quoted cast target is the same claim, written as a forward reference.
    ("""
    u: User = load()
    raw: Any = u
    user = cast("User", raw)
    """, 1),
    # The qualified spelling of the call, and the fully qualified `typing.Any`.
    ("""
    u: User = load()
    raw: typing.Any = u
    user = typing.cast(User, raw)
    """, 1),
    # The keyword spelling of `cast(typ=..., val=...)`.
    ("""
    u: User = load()
    raw: Any = u
    user = cast(typ=User, val=raw)
    """, 1),
    # A method body is analysed like any other function body.
    ("""
    class Repository:
        def find(self, key: str) -> User:
            u: User = self._load(key)
            raw: Any = u
            return cast(User, raw)
    """, 1),
    # Two independent chains in one scope.
    ("""
    def handle(u: User, o: Order) -> tuple[User, Order]:
        raw_user: Any = u
        raw_order: object = o
        return cast(User, raw_user), cast(Order, raw_order)
    """, 2),
    # The cast target need not be the original type: the round trip is the defect.
    ("""
    u: User = load()
    raw: Any = u
    record = cast(UserRecord, raw)
    """, 1),
    # A statement that merely reads the widened name does not break the chain.
    ("""
    u: User = load()
    raw: Any = u
    log.debug("raw=%s", raw)
    for entry in entries:
        report(entry)
    user = cast(User, raw)
    """, 1),
    # The cast is nested inside a larger expression on a linear statement.
    ("""
    u: User = load()
    raw: Any = u
    store(key, cast(User, raw), timeout=5)
    """, 1),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_message_names_the_whole_chain() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        u: User = load()
        raw: Any = u
        user = cast(User, raw)
        """,
    )
    assert "`raw`" in diagnostic.message
    assert "`u`" in diagnostic.message
    assert "`Any`" in diagnostic.message
    assert "`raw: Any = u`" in diagnostic.message
    assert diagnostic.line == 3, "the cast is the reported step, not the widening"
    assert diagnostic.col == 8


def test_object_widening_is_named_in_the_message() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        u: User = load()
        raw: object = u
        user = cast(User, raw)
        """,
    )
    assert "`object`" in diagnostic.message


def test_a_nested_function_gets_its_own_chain() -> None:
    """The outer chain stops at the boundary; the inner one is complete on its own."""
    (diagnostic,) = assert_invalid(
        RULE,
        """
        def outer() -> None:
            u: User = load()
            raw: Any = u

            def inner(o: Order) -> Order:
                boxed: Any = o
                return cast(Order, boxed)
        """,
    )
    assert "`boxed`" in diagnostic.message
    assert "`o`" in diagnostic.message


def test_module_level_flow_is_analysed() -> None:
    diagnostics = run_rule(
        RULE,
        """
        import typing

        CONFIG: Config = load_config()
        RAW: Any = CONFIG
        RESOLVED = typing.cast(Config, RAW)
        """,
    )
    assert len(diagnostics) == 1
    assert diagnostics[0].line == 5


def test_class_body_flow_is_not_analysed() -> None:
    """Documented limitation: only module and function bodies are walked."""
    diagnostics = run_rule(
        RULE,
        """
        class Settings:
            base: Config = load_config()
            raw: Any = base
            resolved = cast(Config, raw)
        """,
    )
    assert diagnostics == ()


def test_rule_id_suppression_silences_the_report() -> None:
    diagnostics = run_rule(
        RULE,
        """
        u: User = load()
        raw: Any = u
        user = cast(User, raw)  # anti-slop: ignore[no-widen-then-cast]
        """,
    )
    assert diagnostics == ()
