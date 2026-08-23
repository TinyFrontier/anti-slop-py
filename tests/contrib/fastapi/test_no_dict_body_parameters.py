"""Tests for the ``fastapi/no-dict-body-parameters`` rule."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.contrib.fastapi.rules.no_dict_body_parameters import RULE

VALID_SNIPPETS = [
    # The recipe itself: a pydantic model as the body.
    """
    from fastapi import FastAPI
    from pydantic import BaseModel

    app = FastAPI()

    class UserIn(BaseModel):
        email: str

    @app.post("/users")
    async def create_user(payload: UserIn) -> UserIn:
        return payload
    """,
    # A plain function with a dict parameter is not a route handler.
    """
    from fastapi import FastAPI

    app = FastAPI()

    def normalize(payload: dict) -> dict:
        return payload
    """,
    # `Depends(...)` marks a dependency, not the request body.
    """
    from fastapi import Depends, FastAPI

    app = FastAPI()

    @app.get("/items")
    def read_items(settings: dict = Depends(get_settings)) -> ItemList:
        return ItemList(settings=settings)
    """,
    # The `Annotated[..., Depends(...)]` spelling of the same thing.
    """
    from typing import Annotated

    from fastapi import Depends, FastAPI

    app = FastAPI()

    @app.get("/items")
    def read_items(settings: Annotated[dict, Depends(get_settings)]) -> ItemList:
        return ItemList(settings=settings)
    """,
    # `Query(...)`: a mapping read from the query string is not the body.
    """
    from fastapi import FastAPI, Query

    app = FastAPI()

    @app.get("/search")
    def search(filters: dict = Query(default={})) -> Results:
        return Results(filters=filters)
    """,
    # A `.get` decorator from another framework entirely: `app` resolves to a Flask
    # application, not to FastAPI/APIRouter, so this is not a route handler.
    """
    from flask import Flask

    app = Flask(__name__)

    @app.get("/items")
    def read_items(payload: dict) -> dict:
        return payload
    """,
    # A router imported from another module cannot be resolved to a construction
    # call, so its handlers are outside this rule's reach (documented limitation).
    """
    from app.api import router

    @router.post("/users")
    async def create_user(payload: dict) -> dict:
        return payload
    """,
    # `**kwargs` is never the request body, whatever it is annotated with.
    """
    from fastapi import FastAPI

    app = FastAPI()

    @app.post("/events")
    async def emit(event: EventIn, **extra: dict) -> None:
        record(event, extra)
    """,
    # An aliased import of `FastAPI`, with a properly typed body: the alias is
    # resolved (so the function *is* recognized as a handler) and stays clean.
    """
    from fastapi import FastAPI as App

    app = App()

    @app.post("/users")
    async def create_user(payload: UserIn) -> UserOut:
        return UserOut.from_in(payload)
    """,
]

INVALID_SNIPPETS = [
    # The canonical violation: a bare `dict` body.
    ("""
    from fastapi import FastAPI

    app = FastAPI()

    @app.post("/users")
    async def create_user(payload: dict) -> UserOut:
        return UserOut(**payload)
    """, 1),
    # `dict[str, Any]`, the subscripted spelling.
    ("""
    from typing import Any

    from fastapi import FastAPI

    app = FastAPI()

    @app.post("/users")
    async def create_user(payload: dict[str, Any]) -> UserOut:
        return UserOut(**payload)
    """, 1),
    # `typing.Dict`, and on an `APIRouter` rather than the application.
    ("""
    from typing import Dict

    from fastapi import APIRouter

    router = APIRouter()

    @router.put("/users/{user_id}")
    async def replace_user(user_id: int, payload: Dict[str, str]) -> UserOut:
        return UserOut(**payload)
    """, 1),
    # `Any` as the body annotation.
    ("""
    from typing import Any

    from fastapi import FastAPI

    app = FastAPI()

    @app.patch("/users/{user_id}")
    def patch_user(user_id: int, payload: Any) -> UserOut:
        return UserOut(**payload)
    """, 1),
    # `Mapping[...]` -- a read-only mapping is just as unvalidated.
    ("""
    from collections.abc import Mapping

    from fastapi import FastAPI

    app = FastAPI()

    @app.post("/events")
    async def emit(event: Mapping[str, str]) -> None:
        record(event)
    """, 1),
    # The quoted spelling of the same annotation.
    ("""
    from fastapi import FastAPI

    app = FastAPI()

    @app.post("/users")
    async def create_user(payload: "dict[str, Any]") -> UserOut:
        return UserOut(**payload)
    """, 1),
    # The module-qualified construction (`import fastapi`), keyword-only parameter.
    ("""
    import fastapi

    app = fastapi.FastAPI()

    @app.post("/users")
    async def create_user(*, payload: dict) -> UserOut:
        return UserOut(**payload)
    """, 1),
    # `Body(...)` is not an exemption: a parameter marked with it *is* the body.
    ("""
    from typing import Annotated

    from fastapi import Body, FastAPI

    app = FastAPI()

    @app.post("/users")
    async def create_user(payload: Annotated[dict, Body()]) -> UserOut:
        return UserOut(**payload)
    """, 1),
    # Two unvalidated parameters, two diagnostics.
    ("""
    from typing import Any

    from fastapi import FastAPI

    app = FastAPI()

    @app.post("/merge")
    async def merge(left: dict, right: Any) -> Merged:
        return Merged(left=left, right=right)
    """, 2),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_message_names_the_parameter_and_the_model_recipe() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        from fastapi import FastAPI

        app = FastAPI()

        @app.post("/users")
        async def create_user(payload: dict) -> UserOut:
            return UserOut(**payload)
        """,
    )
    assert "`payload: dict`" in diagnostic.message
    assert "BaseModel" in diagnostic.message


def test_diagnostic_anchors_on_the_parameter() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        from fastapi import FastAPI

        app = FastAPI()

        @app.post("/users")
        async def create_user(user_id: int, payload: dict) -> UserOut:
            return UserOut(**payload)
        """,
    )
    assert diagnostic.line == 6
    assert diagnostic.col == 37


def test_suppression_uses_the_prefixed_rule_id() -> None:
    assert_valid(
        RULE,
        [
            """
            from fastapi import FastAPI

            app = FastAPI()

            @app.post("/users")
            # anti-slop: ignore[fastapi/no-dict-body-parameters]
            async def create_user(payload: dict) -> UserOut:
                return UserOut(**payload)
            """
        ],
    )
