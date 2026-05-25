from openprint.models import (
    Capabilities,
    DuplexMode,
    Job,
    JobStatus,
    PrinterInfo,
    PrinterState,
    PrinterStatus,
)


def test_job_defaults():
    job = Job()
    assert job.status == JobStatus.QUEUED
    assert job.id.startswith("job_")
    assert job.pages_printed == 0
    assert job.copies == 1
    assert job.error is None


def test_printer_info_defaults():
    info = PrinterInfo(name="Test")
    assert info.name == "Test"
    assert info.protocol_version == "1.0"
    assert info.status == PrinterState.IDLE
    assert info.capabilities.color is True


def test_capabilities_defaults():
    caps = Capabilities()
    assert "a4" in caps.media_sizes
    assert caps.max_file_size == 104_857_600
    assert caps.copies_max == 99


def test_printer_status_defaults():
    status = PrinterStatus()
    assert status.state == PrinterState.IDLE
    assert status.jobs_queued == 0
    assert status.errors == []


def test_duplex_modes():
    assert DuplexMode.NONE.value == "none"
    assert DuplexMode.LONG_EDGE.value == "long-edge"
    assert DuplexMode.SHORT_EDGE.value == "short-edge"
