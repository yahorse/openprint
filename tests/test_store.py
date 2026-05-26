import tempfile
from pathlib import Path

import pytest

from openprint.models import Job, JobStatus
from openprint.store import JobStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = JobStore(db_path=Path(tmp) / "test.db")
        yield s
        s.close()


def test_save_and_get(store: JobStore):
    job = Job(pages_total=5, copies=2)
    store.save(job, "TestPrinter")
    loaded = store.get(job.id)
    assert loaded is not None
    assert loaded.id == job.id
    assert loaded.pages_total == 5
    assert loaded.copies == 2


def test_get_nonexistent(store: JobStore):
    assert store.get("nonexistent") is None


def test_update_status(store: JobStore):
    job = Job(pages_total=3)
    store.save(job, "TestPrinter")
    store.update_status(job.id, "completed", pages_printed=3)
    loaded = store.get(job.id)
    assert loaded is not None
    assert loaded.status == JobStatus.COMPLETED
    assert loaded.pages_printed == 3


def test_update_status_error(store: JobStore):
    job = Job(pages_total=3)
    store.save(job, "TestPrinter")
    store.update_status(job.id, "error", error="Paper jam")
    loaded = store.get(job.id)
    assert loaded is not None
    assert loaded.status == JobStatus.ERROR
    assert loaded.error == "Paper jam"


def test_list_jobs(store: JobStore):
    for i in range(5):
        job = Job(pages_total=i + 1)
        store.save(job, "Printer1" if i < 3 else "Printer2")

    jobs, total = store.list_jobs()
    assert total == 5
    assert len(jobs) == 5


def test_list_jobs_filter_printer(store: JobStore):
    store.save(Job(pages_total=1), "Printer1")
    store.save(Job(pages_total=2), "Printer2")

    jobs, total = store.list_jobs(printer="Printer1")
    assert total == 1
    assert len(jobs) == 1


def test_list_jobs_filter_status(store: JobStore):
    j1 = Job(pages_total=1, status=JobStatus.COMPLETED)
    j2 = Job(pages_total=2, status=JobStatus.QUEUED)
    store.save(j1, "P1")
    store.save(j2, "P1")

    jobs, total = store.list_jobs(status="completed")
    assert total == 1


def test_list_jobs_limit(store: JobStore):
    for _i in range(10):
        store.save(Job(pages_total=1), "P1")

    jobs, total = store.list_jobs(limit=3)
    assert total == 10
    assert len(jobs) == 3
