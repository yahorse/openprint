import asyncio

import pytest
from fastapi.testclient import TestClient

from openprint.models import Job, JobStatus
from openprint.server import Server
from tests.conftest import MINIMAL_PDF, MULTI_PAGE_PDF


@pytest.mark.asyncio
async def test_process_job_honors_cooperative_cancel():
    # A cancel that lands mid-run (here, after the first page) stops the loop
    # before it can mark the job COMPLETED.
    server = Server()
    job = Job(pages_total=5, status=JobStatus.QUEUED)
    server.jobs[job.id] = job

    orig_publish = server.event_bus.publish

    async def flip_on_progress(channel, event, data):
        if event == "progress":
            job.status = JobStatus.CANCELED
        await orig_publish(channel, event, data)

    server.event_bus.publish = flip_on_progress

    await server._process_job(job)
    assert job.status == JobStatus.CANCELED
    assert job.pages_printed < 5


@pytest.mark.asyncio
async def test_cancel_stops_in_flight_job():
    # Cancelling a running job leaves it CANCELED, not COMPLETED, and it stops
    # before printing every page.
    server = Server()
    job = Job(pages_total=20, status=JobStatus.QUEUED)
    server.jobs[job.id] = job
    server._spawn_job(job)
    task = server._job_tasks[job.id]

    await asyncio.sleep(0.6)  # let it print roughly one page
    job.status = JobStatus.CANCELED
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert job.status == JobStatus.CANCELED
    assert job.pages_printed < 20


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
