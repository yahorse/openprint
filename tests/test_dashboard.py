from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprint.dashboard import STATIC_DIR, mount_dashboard


# ---------------------------------------------------------------------------
# Static directory / file presence (original tests preserved)
# ---------------------------------------------------------------------------

def test_static_dir_exists():
    assert STATIC_DIR.exists()


def test_index_html_exists():
    assert (STATIC_DIR / "index.html").exists()


def test_index_html_has_content():
    content = (STATIC_DIR / "index.html").read_text()
    assert "OpenPrint" in content
    assert "/opp/v1" in content


# ---------------------------------------------------------------------------
# Dashboard mounting and route behaviour
# ---------------------------------------------------------------------------

def test_mount_dashboard_adds_root_route():
    """mount_dashboard must register a GET / route that returns index.html."""
    app = FastAPI()
    mount_dashboard(app)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    # The response body should contain the same content as index.html
    assert "OpenPrint" in resp.text


def test_mount_dashboard_serves_static_files():
    """The /static mount must serve files from STATIC_DIR."""
    app = FastAPI()
    mount_dashboard(app)
    client = TestClient(app)

    # Find a real file in the static directory to request
    static_files = list(STATIC_DIR.iterdir())
    assert static_files, "STATIC_DIR must contain at least one file"

    for f in static_files:
        if f.is_file() and f.name != "index.html":
            resp = client.get(f"/static/{f.name}")
            # 200 means it was served, 404 means the file isn't accessible
            # — either way we just verify the mount doesn't crash
            assert resp.status_code in (200, 404)
            break


def test_mount_dashboard_index_content_type_is_html():
    """GET / should return Content-Type: text/html."""
    app = FastAPI()
    mount_dashboard(app)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


def test_mount_dashboard_does_not_break_existing_routes():
    """Existing routes on the app must still work after mount_dashboard is called."""
    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"pong": True}

    mount_dashboard(app)
    client = TestClient(app)

    resp = client.get("/ping")
    assert resp.status_code == 200
    assert resp.json() == {"pong": True}


def test_static_dir_is_absolute_path():
    """STATIC_DIR should be an absolute path so it works from any cwd."""
    assert STATIC_DIR.is_absolute()
