import pytest

from openprint.backends.cups import CUPSBackend, _parse_media_sizes
from openprint.backends.dummy import DummyBackend
from openprint.models import Job, PrinterState


@pytest.mark.asyncio
async def test_dummy_backend_print():
    backend = DummyBackend(delay_per_page=0.01)
    job = Job(pages_total=3)
    await backend.print_job(job, b"fake pdf data")
    assert job.pages_printed == 3


@pytest.mark.asyncio
async def test_dummy_backend_state():
    backend = DummyBackend()
    state = await backend.get_state()
    assert state == PrinterState.IDLE


@pytest.mark.asyncio
async def test_dummy_backend_name():
    backend = DummyBackend(name="Test Printer")
    assert await backend.get_printer_name() == "Test Printer"


def test_cups_backend_init():
    backend = CUPSBackend(printer_name="TestPrinter")
    assert backend._printer == "TestPrinter"


def test_cups_backend_init_no_name():
    backend = CUPSBackend()
    assert backend._printer is None


def test_parse_media_sizes():
    raw = " *Letter A4 Legal A3 "
    sizes = _parse_media_sizes(raw)
    assert "letter" in sizes
    assert "a4" in sizes
    assert "legal" in sizes
    assert "a3" in sizes


def test_parse_media_sizes_empty():
    sizes = _parse_media_sizes("")
    assert sizes == ["a4", "letter"]
