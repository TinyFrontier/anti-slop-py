"""Tests for the ``fastapi/no-state-attribute-access`` rule."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.contrib.fastapi.rules.no_state_attribute_access import RULE

VALID_SNIPPETS = [
    # The recipe: the collaborator arrives through a dependency.
    """
    from fastapi import Depends, FastAPI

    app = FastAPI()

    @app.get("/users/{user_id}")
    async def read_user(user_id: int, db: Database = Depends(get_db)) -> UserOut:
        return await db.load(user_id)
    """,
    # Per-request state set by a middleware is a different object with a different
    # lifetime: out of scope by design.
    """
    from fastapi import FastAPI, Request

    app = FastAPI()

    @app.get("/me")
    async def read_me(request: Request) -> UserOut:
        return request.state.user
    """,
    # A `state` attribute on something that is not a resolvable application.
    """
    from app.settings import settings

    def read_flag() -> bool:
        return settings.state.debug
    """,
    # The bag itself, passed around without an attribute read: nothing dynamic yet.
    """
    from fastapi import FastAPI

    app = FastAPI()

    def describe() -> str:
        return repr(app.state)
    """,
    # A non-`state` attribute of the application.
    """
    from fastapi import FastAPI, Request

    app = FastAPI()

    @app.get("/routes")
    async def routes(request: Request) -> RouteList:
        return RouteList(paths=[route.path for route in request.app.routes])
    """,
    # `getattr` on an unrelated object: another rule's business, not this one's.
    """
    from app.settings import settings

    def read_flag(name: str) -> bool:
        return getattr(settings, name)
    """,
    # An unannotated parameter proves nothing about what it holds.
    """
    def seed(client) -> None:
        client.app.state.db = FakeDatabase()
    """,
    # A local name that merely happens to be called `state`.
    """
    def summarize(state: dict[str, str]) -> str:
        return state["db"]
    """,
]

INVALID_SNIPPETS = [
    # A read of the application bag at module level.
    ("""
    from fastapi import FastAPI

    app = FastAPI()

    def engine() -> Engine:
        return app.state.engine
    """, 1),
    # A write into the bag from a startup hook.
    ("""
    from fastapi import FastAPI

    app = FastAPI()

    @app.on_event("startup")
    async def start() -> None:
        app.state.engine = create_engine()
    """, 1),
    # `request.app.state` inside a handler.
    ("""
    from fastapi import FastAPI, Request

    app = FastAPI()

    @app.get("/users/{user_id}")
    async def read_user(user_id: int, request: Request) -> UserOut:
        return await request.app.state.db.load(user_id)
    """, 1),
    # The same reach from a middleware -- this rule is not restricted to handlers.
    ("""
    from starlette.requests import Request

    async def audit(request: Request, call_next):
        request.app.state.hits += 1
        return await call_next(request)
    """, 1),
    # A test that seeds the bag through a `FastAPI`-annotated fixture parameter.
    ("""
    from fastapi import FastAPI

    def test_reads_user(app: FastAPI) -> None:
        app.state.db = FakeDatabase()
        assert app.state.db.users == []
    """, 2),
    # `getattr` on the bag: the same access through a computed name.
    ("""
    from fastapi import FastAPI

    app = FastAPI()

    def resource(name: str) -> Resource:
        return getattr(app.state, name)
    """, 1),
    # `setattr` on the bag, with the module-qualified application.
    ("""
    import fastapi

    app = fastapi.FastAPI()

    def seed(name: str, value: Resource) -> None:
        setattr(app.state, name, value)
    """, 1),
    # `delattr` through `request.app`, with the quoted `Request` annotation.
    ("""
    from fastapi import FastAPI as App, Request

    app = App()

    @app.delete("/cache")
    async def drop_cache(request: "Request") -> None:
        delattr(request.app.state, "cache")
    """, 1),
    # `del app.state.attr` -- a delete target is still an attribute access.
    ("""
    from fastapi import FastAPI

    app = FastAPI()

    def reset() -> None:
        del app.state.engine
    """, 1),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_message_names_the_access_and_the_depends_recipe() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        from fastapi import FastAPI

        app = FastAPI()

        def engine() -> Engine:
            return app.state.engine
        """,
    )
    assert "`app.state.engine`" in diagnostic.message
    assert "`engine`" in diagnostic.message
    assert "Depends(get_thing)" in diagnostic.message
    assert "dependency_overrides" in diagnostic.message


def test_reflective_access_gets_its_own_message() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        from fastapi import FastAPI

        app = FastAPI()

        def resource(name: str) -> Resource:
            return getattr(app.state, name)
        """,
    )
    assert "computed name" in diagnostic.message
    assert "Depends(get_thing)" in diagnostic.message


def test_suppression_uses_the_prefixed_rule_id() -> None:
    assert_valid(
        RULE,
        [
            """
            from fastapi import FastAPI

            app = FastAPI()

            def engine() -> Engine:
                # anti-slop: ignore[fastapi/no-state-attribute-access]
                return app.state.engine
            """
        ],
    )
