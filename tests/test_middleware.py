from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprint.errors import InvalidParameter
from openprint.middleware import ErrorHandlerMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ErrorHandlerMiddleware)

    @app.get("/opp-error")
    async def opp_error():
        raise InvalidParameter("bad thing")

    @app.get("/boom")
    async def boom():
        raise RuntimeError("unexpected internal failure")

    return app


def test_opp_error_returns_structured_body():
    client = TestClient(_app(), raise_server_exceptions=False)
    resp = client.get("/opp-error")
    assert resp.status_code == 400
    assert resp.json() == {"error": {"code": "invalid_parameter", "message": "bad thing"}}


def test_unexpected_exception_returns_clean_500():
    # A non-OPP exception must become a clean JSON 500, not leak the stack trace.
    client = TestClient(_app(), raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "internal_error"
    assert "unexpected internal failure" not in resp.text
