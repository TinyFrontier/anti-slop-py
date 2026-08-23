"""Tests for the ``no-module-mocking`` rule (PLAN.md section 3.4, row 4)."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.rules.no_module_mocking import MESSAGE_PROBLEM, MESSAGE_RECIPE, RULE

VALID_SNIPPETS = [
    # The replacement this rule asks for: the collaborator arrives as an argument.
    """
    def test_saves_the_order(tmp_path):
        service = OrderService(clock=FrozenClock(), store=InMemoryStore())
        assert service.save(order) == Saved(id=1)
    """,
    # A local name that shadows the import is not the import any more.
    """
    from unittest.mock import patch

    def test_thing():
        patch = my_own_helper
        patch("pkg.mod.fn")
    """,
    # A `patch` that comes from somewhere else entirely.
    """
    from mylib.fixtures import patch

    def test_thing():
        patch("pkg.mod.fn")
    """,
    # Building mocks is not patching a module: only the seam is the problem.
    """
    from unittest import mock

    def test_thing():
        collaborator = mock.MagicMock()
        assert run(collaborator) is None
    """,
    # `patch.dict` edits a dictionary's contents (usually the environment); there is
    # no collaborator to inject in its place.
    """
    from unittest.mock import patch

    def test_thing():
        with patch.dict(os.environ, {"TZ": "UTC"}):
            assert now().tzname() == "UTC"
    """,
    # monkeypatch's process-state helpers are left alone.
    """
    def test_thing(monkeypatch):
        monkeypatch.setenv("TZ", "UTC")
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
    """,
    # `monkeypatch` that is not a fixture (not a parameter) is not pytest's.
    """
    from mylib.testing import monkeypatch

    def test_thing():
        monkeypatch.setattr(service, "run", fake)
    """,
    # A local variable called `mocker` is not the pytest-mock fixture either.
    """
    def test_thing():
        mocker = build_recorder()
        mocker.patch("pkg.mod.fn")
    """,
    # Calls through something that is not a plain name chain are out of scope.
    """
    def test_thing(registry):
        registry["patch"]("pkg.mod.fn")
    """,
    # A method named `patch` on a domain object: nothing to do with mock.
    """
    def test_thing():
        client = Client()
        client.patch("/orders/1", json={"status": "sent"})
    """,
]

INVALID_SNIPPETS = [
    # Context-manager form.
    ("""
    from unittest.mock import patch

    def test_thing():
        with patch("pkg.mod.fn") as fake:
            assert run() is None
    """, 1),
    # Decorator form, through an aliased import.
    ("""
    from unittest.mock import patch as p

    @p("pkg.mod.fn")
    def test_thing(fake):
        assert run() is None
    """, 1),
    # Plain-call form, kept in a variable.
    ("""
    from unittest.mock import patch

    def test_thing():
        patcher = patch("pkg.mod.fn")
        patcher.start()
    """, 1),
    # The module form: `mock` is the imported `unittest.mock`.
    ("""
    from unittest import mock

    def test_thing():
        with mock.patch.object(Service, "run") as fake:
            assert run() is None
    """, 1),
    # The fully qualified path.
    ("""
    import unittest.mock

    def test_thing():
        with unittest.mock.patch("pkg.mod.fn"):
            assert run() is None
    """, 1),
    # The `mock` backport distribution.
    ("""
    import mock

    def test_thing():
        with mock.patch("pkg.mod.fn"):
            assert run() is None
    """, 1),
    # `patch.multiple`.
    ("""
    from unittest.mock import patch

    def test_thing():
        with patch.multiple(Service, run=DEFAULT, load=DEFAULT):
            assert run() is None
    """, 1),
    # The pytest-mock fixture.
    ("""
    def test_thing(mocker):
        mocker.patch("pkg.mod.fn")
    """, 1),
    # The pytest-mock fixture, attribute form.
    ("""
    def test_thing(mocker):
        mocker.patch.object(Service, "run", return_value=None)
    """, 1),
    # monkeypatch, string-path form.
    ("""
    def test_thing(monkeypatch):
        monkeypatch.setattr("pkg.mod.fn", fake)
    """, 1),
    # monkeypatch, object form: the same module attribute, reached differently.
    ("""
    import pkg.mod

    def test_thing(monkeypatch):
        monkeypatch.setattr(pkg.mod, "fn", fake)
    """, 1),
    # Stacked decorators: one diagnostic each.
    ("""
    from unittest.mock import patch

    @patch("pkg.mod.load")
    @patch("pkg.mod.save")
    def test_thing(save, load):
        assert run() is None
    """, 2),
    # A nested helper still sees the module-level import.
    ("""
    from unittest.mock import patch

    def test_thing():
        def arrange():
            return patch("pkg.mod.fn")

        with arrange():
            assert run() is None
    """, 1),
    # An async test is no different.
    ("""
    from unittest.mock import patch

    async def test_thing():
        with patch("pkg.mod.fn"):
            assert await run() is None
    """, 1),
    # The fixture may be declared anywhere in the signature.
    ("""
    def test_thing(tmp_path, *, monkeypatch):
        monkeypatch.setattr(service, "run", fake)
    """, 1),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_message_names_the_call_and_the_replacement() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        from unittest.mock import patch

        def test_thing():
            with patch("pkg.mod.fn"):
                assert run() is None
        """,
    )
    assert "`patch`" in diagnostic.message
    assert "Protocol" in diagnostic.message


def test_the_message_is_composed_from_two_reusable_halves() -> None:
    """Phase 6 rebuilds the diagnostic from ``MESSAGE_PROBLEM`` plus its own recipe."""
    (diagnostic,) = assert_invalid(
        RULE,
        """
        def test_thing(mocker):
            mocker.patch("pkg.mod.fn")
        """,
    )
    problem = MESSAGE_PROBLEM.format(target="mocker.patch")
    assert diagnostic.message == f"{problem} {MESSAGE_RECIPE}"


def test_diagnostic_covers_the_whole_call() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        def test_thing(monkeypatch):
            monkeypatch.setattr("pkg.mod.fn", fake)
        """,
    )
    assert (diagnostic.line, diagnostic.col) == (2, 5)
    assert (diagnostic.end_line, diagnostic.end_col) == (2, 44)


def test_has_no_options() -> None:
    with pytest.raises(ValueError, match="allow-anything"):
        assert_valid(
            RULE,
            ["def test_thing(mocker):\n    mocker.patch('x')\n"],
            options={"allow-anything": True},
        )
