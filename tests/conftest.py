from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openprint.server import Server


@pytest.fixture
def server() -> Server:
    return Server(
        name="Test Printer",
        port=0,
        enable_discovery=False,
        log_requests=False,
    )


@pytest.fixture
def app(server: Server) -> TestClient:
    return TestClient(server.create_app())


@pytest.fixture
def authed_server() -> Server:
    return Server(
        name="Secure Printer",
        port=0,
        auth_token="test-secret-token",
        enable_discovery=False,
        log_requests=False,
    )


@pytest.fixture
def authed_app(authed_server: Server) -> TestClient:
    return TestClient(authed_server.create_app())


MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type /Page>>endobj\n"
    b"2 0 obj<</Type /Pages/Kids[1 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type /Catalog/Pages 2 0 R>>endobj\n"
    b"trailer<</Root 3 0 R>>\n%%EOF"
)

MULTI_PAGE_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type /Page>>endobj\n"
    b"2 0 obj<</Type /Page>>endobj\n"
    b"3 0 obj<</Type /Page>>endobj\n"
    b"4 0 obj<</Type /Pages/Kids[1 0 R 2 0 R 3 0 R]/Count 3>>endobj\n"
    b"5 0 obj<</Type /Catalog/Pages 4 0 R>>endobj\n"
    b"trailer<</Root 5 0 R>>\n%%EOF"
)
