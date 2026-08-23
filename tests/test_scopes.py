"""Tests for lexical name resolution (``engine/scopes.py``)."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from anti_slop.engine.comments import build_comment_map
from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule
from anti_slop.engine.scopes import (
    AssignmentBinding,
    DefinitionBinding,
    ImportBinding,
    OpaqueBinding,
    ParameterBinding,
    ScopeKind,
    TypeAliasBinding,
    TypeParameterBinding,
    build_scope_table,
    dotted_parts,
    scope_table_for,
)
from anti_slop.engine.walker import ParentMap, Walker
from harness import PROBE_METADATA

_PROBE_RULE = Rule(
    id="probe",
    description="Test-only rule used to build a RuleContext.",
    messages={"probe": "probe"},
    handlers=(),
    metadata=PROBE_METADATA,
)


def _module(source: str) -> ast.Module:
    return ast.parse(textwrap.dedent(source).lstrip("\n"))


def _calls(tree: ast.AST) -> list[ast.Call]:
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        match node:
            case ast.Call() as call:
                found.append(call)
            case _:
                pass
    return found


def _names(tree: ast.AST, wanted: str) -> list[ast.Name]:
    found: list[ast.Name] = []
    for node in ast.walk(tree):
        match node:
            case ast.Name(id=name) as candidate if name == wanted:
                found.append(candidate)
            case _:
                pass
    return found


def _comprehensions(tree: ast.AST) -> list[ast.ListComp]:
    found: list[ast.ListComp] = []
    for node in ast.walk(tree):
        match node:
            case ast.ListComp() as comprehension:
                found.append(comprehension)
            case _:
                pass
    return found


def _globals(tree: ast.AST) -> list[ast.Global]:
    found: list[ast.Global] = []
    for node in ast.walk(tree):
        match node:
            case ast.Global() as declaration:
                found.append(declaration)
            case _:
                pass
    return found


def _loads(names: list[ast.Name]) -> list[ast.Name]:
    """The reading uses among ``names`` -- the ones that are not assignment targets."""
    found: list[ast.Name] = []
    for name in names:
        match name.ctx:
            case ast.Load():
                found.append(name)
            case _:
                pass
    return found


def test_import_forms_resolve_to_dotted_paths() -> None:
    tree = _module("""
        from unittest.mock import patch as p
        from unittest import mock
        import unittest.mock
        import mock as backport

        p()
        mock.patch()
        unittest.mock.patch()
        backport.patch.object()
    """)
    table = build_scope_table(tree)
    resolved = [table.qualified_name(call.func) for call in _calls(tree)]
    assert resolved == [
        "unittest.mock.patch",
        "unittest.mock.patch",
        "unittest.mock.patch",
        "mock.patch.object",
    ]


def test_plain_import_binds_only_the_top_level_package() -> None:
    tree = _module("import unittest.mock\n")
    table = build_scope_table(tree)
    assert table.module_scope.names == {"unittest"}
    match table.module_scope.binding("unittest"):
        case ImportBinding() as binding:
            assert binding.module == "unittest.mock"
            assert binding.imported_name is None
            assert binding.qualified == "unittest"
        case other:
            raise AssertionError(other)


def test_relative_import_has_no_resolvable_path() -> None:
    tree = _module("from . import mock\n\nmock.patch()\n")
    table = build_scope_table(tree)
    (call,) = _calls(tree)
    assert table.qualified_name(call.func) is None
    match table.module_scope.binding("mock"):
        case ImportBinding() as binding:
            assert binding.level == 1
        case other:
            raise AssertionError(other)


def test_star_import_binds_nothing() -> None:
    table = build_scope_table(_module("from unittest.mock import *\n"))
    assert table.module_scope.names == frozenset()


def test_local_assignment_shadows_a_module_import() -> None:
    tree = _module("""
        from unittest.mock import patch

        def test_thing():
            patch = helper
            patch("pkg.mod.fn")
    """)
    table = build_scope_table(tree)
    (call,) = _calls(tree)
    assert table.qualified_name(call.func) is None
    match table.resolve("patch", call.func):
        case AssignmentBinding(name="patch"):
            pass
        case other:
            raise AssertionError(other)


def test_parameters_resolve_to_parameter_bindings() -> None:
    tree = _module("""
        def test_thing(mocker, monkeypatch=None, *args, only_kw, **rest):
            mocker.patch("pkg.mod.fn")
    """)
    table = build_scope_table(tree)
    (call,) = _calls(tree)
    for parameter in ("mocker", "monkeypatch", "args", "only_kw", "rest"):
        match table.resolve(parameter, call.func):
            case ParameterBinding(name=name):
                assert name == parameter
            case other:
                raise AssertionError((parameter, other))


def test_a_name_bound_twice_resolves_to_nothing() -> None:
    tree = _module("""
        from unittest.mock import patch

        patch = wrapped

        patch("pkg.mod.fn")
    """)
    table = build_scope_table(tree)
    (call,) = _calls(tree)
    assert table.resolve("patch", call.func) is None
    assert table.module_scope.declares("patch")
    assert len(table.module_scope.bindings("patch")) == 2


def test_class_body_is_invisible_from_a_nested_function() -> None:
    tree = _module("""
        class Repository:
            handler = build

            def run(self):
                handler()
    """)
    table = build_scope_table(tree)
    (call,) = _calls(tree)
    assert table.resolve("handler", call.func) is None


def test_class_body_sees_its_own_names() -> None:
    tree = _module("""
        class Repository:
            handler = build
            alias = handler
    """)
    table = build_scope_table(tree)
    (use,) = _loads(_names(tree, "handler"))
    match table.resolve("handler", use):
        case AssignmentBinding(name="handler"):
            pass
        case other:
            raise AssertionError(other)
    assert table.scope_of(use).kind is ScopeKind.CLASS


def test_annotations_and_defaults_belong_to_the_enclosing_scope() -> None:
    """A signature is evaluated where the `def` is written, not inside the body."""
    tree = _module("""
        Payload = dict

        def handle(value: Payload = Payload) -> Payload:
            Payload = str
            return value
    """)
    table = build_scope_table(tree)
    signature_uses = _loads(_names(tree, "Payload"))
    assert len(signature_uses) == 3
    for name in signature_uses:
        assert table.scope_of(name) is table.module_scope
        match table.resolve("Payload", name):
            case AssignmentBinding(value=ast.Name(id="dict")):
                pass
            case other:
                raise AssertionError(other)


def test_comprehension_scope_owns_its_target() -> None:
    tree = _module("""
        source = [1]
        result = [item for item in source if item]
    """)
    table = build_scope_table(tree)
    (comprehension,) = _comprehensions(tree)
    generator = comprehension.generators[0]
    assert table.scope_of(comprehension) is table.module_scope

    inner = table.scope_of(comprehension.elt)
    assert inner.kind is ScopeKind.COMPREHENSION
    match inner.binding("item"):
        case OpaqueBinding(name="item"):
            pass
        case other:
            raise AssertionError(other)
    assert table.scope_of(generator.ifs[0]) is inner
    # The leftmost iterable is evaluated outside the comprehension.
    assert table.scope_of(generator.iter) is table.module_scope


def test_type_parameters_are_bound_in_their_own_scope() -> None:
    tree = _module("""
        def identity[T](value: T) -> T:
            return value
    """)
    table = build_scope_table(tree)
    annotation = _names(tree, "T")[0]
    assert table.scope_of(annotation).kind is ScopeKind.TYPE_PARAMS
    match table.resolve("T", annotation):
        case TypeParameterBinding(name="T"):
            pass
        case other:
            raise AssertionError(other)


def test_definitions_and_type_aliases_get_their_own_binding_kinds() -> None:
    tree = _module("""
        type Payload = dict[str, str]

        class Repository: ...

        def run(): ...
    """)
    table = build_scope_table(tree)
    scope = table.module_scope
    match scope.binding("Payload"):
        case TypeAliasBinding(name="Payload"):
            pass
        case other:
            raise AssertionError(other)
    for name in ("Repository", "run"):
        match scope.binding(name):
            case DefinitionBinding():
                pass
            case other:
                raise AssertionError((name, other))


def test_untracked_binding_forms_are_opaque() -> None:
    tree = _module("""
        for row in rows:
            pass

        with open(path) as handle:
            pass

        try:
            pass
        except OSError as error:
            pass

        first, *rest = values

        def mutate():
            global counter
    """)
    table = build_scope_table(tree)
    scope = table.module_scope
    for name in ("row", "handle", "error", "first", "rest"):
        match scope.binding(name):
            case OpaqueBinding():
                pass
            case other:
                raise AssertionError((name, other))
    (declaration,) = _globals(tree)
    match table.scope_of(declaration).binding("counter"):
        case OpaqueBinding(name="counter"):
            pass
        case other:
            raise AssertionError(other)


def test_dotted_parts_splits_only_pure_name_chains() -> None:
    tree = _module("a.b.c()\nvalues[0].run()\n")
    first, second = _calls(tree)
    assert dotted_parts(first.func) == ("a", "b", "c")
    assert dotted_parts(second.func) is None


def _context(source: str, tree: ast.Module) -> RuleContext:
    parents = ParentMap()
    Walker(parents).run(tree)
    path = Path("snippet.py")
    return RuleContext(
        path=path,
        source=source,
        lines=source.splitlines(),
        comments=build_comment_map(path, source),
        parents=parents,
        rule=_PROBE_RULE,
        options={},
        report=lambda diagnostic: None,
    )


def test_scope_table_for_finds_the_module_and_caches_it() -> None:
    source = "from unittest.mock import patch\n\npatch('pkg.mod.fn')\n"
    tree = ast.parse(source)
    context = _context(source, tree)
    (call,) = _calls(tree)

    table = scope_table_for(context, call)
    assert table.qualified_name(call.func) == "unittest.mock.patch"
    assert scope_table_for(context, call) is table
    assert scope_table_for(context, tree) is table


def test_scope_table_for_a_detached_node_claims_nothing() -> None:
    source = "patch('pkg.mod.fn')\n"
    tree = ast.parse(source)
    context = _context(source, tree)
    detached = ast.parse("from unittest.mock import patch\n\npatch('x')\n")
    (call,) = _calls(detached)

    table = scope_table_for(context, call)
    assert table.qualified_name(call.func) is None
