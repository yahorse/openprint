from fastapi.testclient import TestClient

from tests.conftest import MINIMAL_PDF


def test_auth_required_no_token(authed_app: TestClient):
    resp = authed_app.get("/opp/v1/printer")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_auth_required_wrong_token(authed_app: TestClient):
    resp = authed_app.get(
        "/opp/v1/printer",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


def test_auth_valid_token(authed_app: TestClient):
    resp = authed_app.get(
        "/opp/v1/printer",
        headers={"Authorization": "Bearer test-secret-token"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Secure Printer"


def test_auth_submit_job(authed_app: TestClient):
    resp = authed_app.post(
        "/opp/v1/jobs",
        files={"file": ("test.pdf", MINIMAL_PDF, "application/pdf")},
        headers={"Authorization": "Bearer test-secret-token"},
    )
    assert resp.status_code == 201


def test_auth_submit_job_no_token(authed_app: TestClient):
    resp = authed_app.post(
        "/opp/v1/jobs",
        files={"file": ("test.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert resp.status_code == 401
