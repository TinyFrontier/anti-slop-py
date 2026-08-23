"""Tests for the ``fastapi/no-raw-request-parsing`` rule."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.contrib.fastapi.rules.no_raw_request_parsing import RULE

VALID_SNIPPETS = [
    # The recipe: the body is declared in the signature.
    """
    from fastapi import FastAPI

    app = FastAPI()

    @app.post("/users")
    async def create_user(payload: UserIn) -> UserOut:
        return store(payload)
    """,
    # The handler keeps a `Request` for what the signature cannot express, and
    # touches only the parts that are not the body.
    """
    from fastapi import FastAPI, Request

    app = FastAPI()

    @app.post("/users")
    async def create_user(request: Request, payload: UserIn) -> UserOut:
        log(request.headers["x-trace-id"], request.url)
        return store(payload)
    """,
    # The same call outside a route handler: a middleware is not this rule's target.
    """
    from fastapi import FastAPI, Request

    app = FastAPI()

    async def audit(request: Request, call_next):
        body = await request.json()
        return await call_next(request)
    """,
    # A nested function inside a handler: the nearest enclosing `def` decides.
    """
    from fastapi import FastAPI, Request

    app = FastAPI()

    @app.post("/users")
    async def create_user(request: Request) -> UserOut:
        async def read():
            return await request.json()
        return UserOut(**await read())
    """,
    # An unannotated parameter proves nothing about what it holds.
    """
    from fastapi import FastAPI

    app = FastAPI()

    @app.post("/users")
    async def create_user(request) -> UserOut:
        return UserOut(**await request.json())
    """,
    # A same-named method on some other object.
    """
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/mirror")
    async def mirror(client: HttpClient) -> Mirror:
        return Mirror(**await client.json())
    """,
    # `request.stream()` is not a body decode: out of scope by design.
    """
    from fastapi import FastAPI, Request

    app = FastAPI()

    @app.post("/upload")
    async def upload(request: Request) -> None:
        async for chunk in request.stream():
            sink.write(chunk)
    """,
    # A router imported from another module: its handlers are unresolvable.
    """
    from app.api import router
    from fastapi import Request

    @router.post("/users")
    async def create_user(request: Request) -> UserOut:
        return UserOut(**await request.json())
    """,
]

INVALID_SNIPPETS = [
    # The canonical violation.
    ("""
    from fastapi import FastAPI, Request

    app = FastAPI()

    @app.post("/users")
    async def create_user(request: Request) -> UserOut:
        payload = await request.json()
        return UserOut(**payload)
    """, 1),
    # The synchronous spelling, without `await`.
    ("""
    from fastapi import FastAPI, Request

    app = FastAPI()

    @app.post("/users")
    def create_user(request: Request) -> UserOut:
        return UserOut(**request.json())
    """, 1),
    # `request.form()`.
    ("""
    from fastapi import APIRouter, Request

    router = APIRouter()

    @router.post("/login")
    async def login(request: Request) -> Session:
        form = await request.form()
        return open_session(form["email"])
    """, 1),
    # `request.body()`.
    ("""
    from fastapi import FastAPI, Request

    app = FastAPI()

    @app.put("/blob")
    async def store_blob(request: Request) -> None:
        raw = await request.body()
        sink.write(raw)
    """, 1),
    # The attribute-qualified annotation, with the module-qualified application.
    ("""
    import fastapi

    app = fastapi.FastAPI()

    @app.post("/users")
    async def create_user(request: fastapi.Request) -> UserOut:
        return UserOut(**await request.json())
    """, 1),
    # The quoted annotation, and an aliased `FastAPI` import.
    ("""
    from fastapi import FastAPI as App

    app = App()

    @app.post("/users")
    async def create_user(request: "Request") -> UserOut:
        return UserOut(**await request.json())
    """, 1),
    # The Starlette spelling of the annotation.
    ("""
    from fastapi import FastAPI
    from starlette.requests import Request

    app = FastAPI()

    @app.post("/users")
    async def create_user(request: Request) -> UserOut:
        return UserOut(**await request.json())
    """, 1),
    # Two decodes in one handler, two diagnostics.
    ("""
    from fastapi import FastAPI, Request

    app = FastAPI()

    @app.post("/users")
    async def create_user(request: Request) -> UserOut:
        raw = await request.body()
        payload = await request.json()
        return UserOut(**payload, raw=raw)
    """, 2),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_message_names_the_call_the_handler_and_the_recipe() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        from fastapi import FastAPI, Request

        app = FastAPI()

        @app.post("/users")
        async def create_user(request: Request) -> UserOut:
            return UserOut(**await request.json())
        """,
    )
    assert "`request.json()`" in diagnostic.message
    assert "`create_user`" in diagnostic.message
    assert "BaseModel" in diagnostic.message


def test_suppression_uses_the_prefixed_rule_id() -> None:
    assert_valid(
        RULE,
        [
            """
            from fastapi import FastAPI, Request

            app = FastAPI()

            @app.post("/users")
            async def create_user(request: Request) -> UserOut:
                # anti-slop: ignore[fastapi/no-raw-request-parsing]
                payload = await request.json()
                return UserOut(**payload)
            """
        ],
    )
