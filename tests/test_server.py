from fastapi.testclient import TestClient

from openprint.models import JobStatus
from openprint.server import Server
from tests.conftest import MINIMAL_PDF, MULTI_PAGE_PDF


def test_get_printer(app: TestClient):
    resp = app.get("/opp/v1/printer")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Test Printer"
    assert data["protocol_version"] == "1.0"
    assert data["capabilities"]["color"] is True


def test_submit_job(app: TestClient):
    resp = app.post(
        "/opp/v1/jobs",
        files={"file": ("test.pdf", MINIMAL_PDF, "application/pdf")},
        data={"copies": "1", "color": "true", "media": "a4"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"].startswith("job_")
    assert data["status"] == "queued"
    assert data["copies"] == 1


def test_submit_job_multi_page(app: TestClient):
    resp = app.post(
        "/opp/v1/jobs",
        files={"file": ("multi.pdf", MULTI_PAGE_PDF, "application/pdf")},
        data={"pages": "1-2"},
    )
    assert resp.status_code == 201
    assert resp.json()["pages_total"] == 2


def test_submit_invalid_pdf(app: TestClient):
    resp = app.post(
        "/opp/v1/jobs",
        files={"file": ("bad.txt", b"not a pdf", "application/pdf")},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_pdf"


def test_submit_unsupported_media(app: TestClient):
    resp = app.post(
        "/opp/v1/jobs",
        files={"file": ("test.pdf", MINIMAL_PDF, "application/pdf")},
        data={"media": "tabloid"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_parameter"


def test_list_jobs_empty(app: TestClient):
    resp = app.get("/opp/v1/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["jobs"] == []
    assert data["total"] == 0


def test_list_jobs_after_submit(app: TestClient):
    app.post(
        "/opp/v1/jobs",
        files={"file": ("test.pdf", MINIMAL_PDF, "application/pdf")},
    )
    resp = app.get("/opp/v1/jobs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_get_job(app: TestClient):
    submit = app.post(
        "/opp/v1/jobs",
        files={"file": ("test.pdf", MINIMAL_PDF, "application/pdf")},
    )
    job_id = submit.json()["id"]
    resp = app.get(f"/opp/v1/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == job_id


def test_get_job_not_found(app: TestClient):
    resp = app.get("/opp/v1/jobs/nonexistent")
    assert resp.status_code == 404


def test_cancel_job(app: TestClient, server: Server):
    submit = app.post(
        "/opp/v1/jobs",
        files={"file": ("test.pdf", MINIMAL_PDF, "application/pdf")},
    )
    job_id = submit.json()["id"]
    server.jobs[job_id].status = JobStatus.QUEUED
    resp = app.delete(f"/opp/v1/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "canceled"


def test_get_status(app: TestClient):
    resp = app.get("/opp/v1/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "idle"
    assert "supplies" in data
