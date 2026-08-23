"""Tests for the ``fastapi/no-untyped-route-response`` rule."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.contrib.fastapi.rules.no_untyped_route_response import RULE

VALID_SNIPPETS = [
    # A pydantic model as the response contract.
    """
    from fastapi import FastAPI
    from pydantic import BaseModel

    app = FastAPI()

    class UserOut(BaseModel):
        email: str

    @app.get("/users/{user_id}")
    async def read_user(user_id: int) -> UserOut:
        return load_user(user_id)
    """,
    # `-> None`: a 204 or an effect-only route is a complete contract.
    """
    from fastapi import FastAPI

    app = FastAPI()

    @app.delete("/users/{user_id}")
    async def delete_user(user_id: int) -> None:
        drop_user(user_id)
    """,
    # A raw framework response: a deliberate step below the serialization layer.
    """
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.get("/health")
    def health() -> JSONResponse:
        return JSONResponse({"ok": True})
    """,
    # A container of a domain type.
    """
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/users")
    async def list_users() -> list[UserOut]:
        return load_users()
    """,
    # Not a route handler: an unannotated plain function is a different rule's
    # business (or nobody's).
    """
    from fastapi import FastAPI

    app = FastAPI()

    def build_payload():
        return {"ok": True}
    """,
    # Another framework's `.get` decorator: `app` does not resolve to FastAPI.
    """
    from flask import Flask

    app = Flask(__name__)

    @app.get("/health")
    def health():
        return {"ok": True}
    """,
    # A router imported from another module: unresolvable, therefore untouched.
    """
    from app.api import router

    @router.get("/health")
    def health() -> dict:
        return {"ok": True}
    """,
    # A decorator that is not a route at all, on a function with no annotation.
    """
    from fastapi import FastAPI

    app = FastAPI()

    @app.on_event("startup")
    async def warm_cache():
        await cache.warm()
    """,
]

INVALID_SNIPPETS = [
    # No return annotation at all.
    ("""
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/users/{user_id}")
    async def read_user(user_id: int):
        return load_user(user_id)
    """, 1),
    # `-> dict`.
    ("""
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/users/{user_id}")
    async def read_user(user_id: int) -> dict:
        return load_user(user_id)
    """, 1),
    # `-> dict[str, Any]`.
    ("""
    from typing import Any

    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/config")
    def read_config() -> dict[str, Any]:
        return load_config()
    """, 1),
    # `-> Any`.
    ("""
    from typing import Any

    from fastapi import APIRouter

    router = APIRouter()

    @router.post("/rpc")
    async def rpc(call: RpcIn) -> Any:
        return dispatch(call)
    """, 1),
    # `-> Mapping[str, str]`, on the module-qualified application.
    ("""
    from collections.abc import Mapping

    import fastapi

    app = fastapi.FastAPI()

    @app.get("/labels")
    def labels() -> Mapping[str, str]:
        return load_labels()
    """, 1),
    # The quoted spelling.
    ("""
    from fastapi import FastAPI as App

    app = App()

    @app.get("/labels")
    def labels() -> "dict":
        return load_labels()
    """, 1),
    # A websocket handler with no declared contract.
    ("""
    from fastapi import FastAPI, WebSocket

    app = FastAPI()

    @app.websocket("/ws")
    async def stream(socket: WebSocket):
        await socket.accept()
    """, 1),
    # Two handlers, two diagnostics -- one of each kind.
    ("""
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/a")
    def read_a():
        return load_a()

    @app.get("/b")
    def read_b() -> dict:
        return load_b()
    """, 2),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_missing_annotation_message_names_the_handler_and_the_recipe() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/users/{user_id}")
        async def read_user(user_id: int):
            return load_user(user_id)
        """,
    )
    assert "`read_user`" in diagnostic.message
    assert "no return annotation" in diagnostic.message
    assert "BaseModel" in diagnostic.message
    assert "response_model=" in diagnostic.message


def test_untyped_annotation_message_quotes_the_annotation() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/config")
        def read_config() -> dict:
            return load_config()
        """,
    )
    assert "returns `dict`" in diagnostic.message
    assert diagnostic.line == 6


def test_suppression_uses_the_prefixed_rule_id() -> None:
    assert_valid(
        RULE,
        [
            """
            from fastapi import FastAPI

            app = FastAPI()

            @app.get("/config")
            def read_config() -> dict:  # anti-slop: ignore[fastapi/no-untyped-route-response]
                return load_config()
            """
        ],
    )


def test_response_model_keyword_declares_the_contract() -> None:
    assert_valid(
        RULE,
        [
            """
            from fastapi import APIRouter
            router = APIRouter()

            @router.get("/items", response_model=ItemList)
            async def list_items(page: int):
                ...
            """,
            """
            from fastapi import APIRouter
            router = APIRouter()

            @router.post("/orders", response_model=OpenOrderResult | PendingOrderResult)
            async def create_order(payload: OrderIn):
                ...
            """,
        ],
    )


def test_response_model_of_dict_still_counts_as_no_contract() -> None:
    assert_invalid(
        RULE,
        """
        from fastapi import APIRouter
        router = APIRouter()

        @router.get("/raw", response_model=dict)
        async def raw() -> Payload:
            ...
        """,
        count=1,
    )


def test_response_model_wins_over_a_dict_return_annotation() -> None:
    assert_valid(
        RULE,
        [
            """
            from fastapi import APIRouter
            router = APIRouter()

            @router.get("/items", response_model=ItemList)
            async def list_items() -> dict:
                ...
            """,
        ],
    )
