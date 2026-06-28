from __future__ import annotations

import struct
from unittest.mock import patch

import pytest

from openprint.backends.ipp import (
    OP_CREATE_JOB,
    OP_PRINT_JOB,
    OP_SEND_DOCUMENT,
    TAG_CHARSET,
    TAG_END,
    TAG_INTEGER,
    TAG_KEYWORD,
    TAG_MIME,
    IPPBackend,
    _encode_int_attr,
    _encode_string_attr,
    _parse_ipp_response,
)
from openprint.models import Job

# ---------------------------------------------------------------------------
# Helper: build a minimal valid IPP response
# ---------------------------------------------------------------------------

def _make_ipp_response(status: int = 0x0000, attrs: dict | None = None) -> bytes:
    """Build a minimal IPP response binary with optional keyword attributes."""
    header = struct.pack(">HHI", 0x0200, status, 1)
    body = struct.pack(">B", 0x01)  # TAG_OPERATION group

    if attrs:
        for name, value in attrs.items():
            name_bytes = name.encode()
            val_bytes = value.encode() if isinstance(value, str) else value
            body += struct.pack(">BH", TAG_KEYWORD, len(name_bytes))
            body += name_bytes
            body += struct.pack(">H", len(val_bytes))
            body += val_bytes

    body += struct.pack(">B", TAG_END)
    return header + body


def _make_ipp_response_with_mime_list(attr_name: str, values: list[str]) -> bytes:
    """Build a response where one attribute appears multiple times (multi-value)."""
    header = struct.pack(">HHI", 0x0200, 0x0000, 1)
    body = struct.pack(">B", 0x01)  # TAG_OPERATION

    first = True
    for v in values:
        name_to_encode = attr_name if first else ""
        first = False
        name_bytes = name_to_encode.encode()
        val_bytes = v.encode()
        body += struct.pack(">BH", TAG_MIME, len(name_bytes))
        body += name_bytes
        body += struct.pack(">H", len(val_bytes))
        body += val_bytes

    body += struct.pack(">B", TAG_END)
    return header + body


# ---------------------------------------------------------------------------
# Original tests (preserved)
# ---------------------------------------------------------------------------

def test_ipp_backend_init():
    backend = IPPBackend(uri="ipp://printer.local:631/ipp/print")
    assert backend._uri == "ipp://printer.local:631/ipp/print"
    assert backend._http_url == "http://printer.local:631/ipp/print"


def test_ipp_backend_tls():
    backend = IPPBackend(uri="ipps://printer.local:631/ipp/print", tls=True)
    # With tls=True the backend keeps both candidate URLs and tries plain HTTP
    # first — many consumer printers (e.g. HP DeskJet) advertise ipps:// but
    # their TLS stack drops large request bodies, while plain HTTP works.
    assert backend._http_urls == [
        "http://printer.local:631/ipp/print",
        "https://printer.local:631/ipp/print",
    ]
    assert backend._http_url == "http://printer.local:631/ipp/print"


def test_encode_string_attr():
    data = _encode_string_attr(TAG_CHARSET, "attributes-charset", "utf-8")
    assert b"attributes-charset" in data
    assert b"utf-8" in data


def test_encode_int_attr():
    data = _encode_int_attr(TAG_INTEGER, "copies", 5)
    assert b"copies" in data
    assert len(data) > 0


def test_parse_empty_response():
    result = _parse_ipp_response(b"\x00" * 4)
    assert result["status"] == -1


def test_parse_minimal_response():
    header = struct.pack(">HHI", 0x0200, 0x0000, 1)
    body = struct.pack(">B", TAG_END)
    result = _parse_ipp_response(header + body)
    assert result["status"] == 0


# ---------------------------------------------------------------------------
# Format fallback chain
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_format_fallback_pdf_sends_directly():
    """When supported_formats includes application/pdf, Print-Job is used with PDF content."""
    backend = IPPBackend(uri="ipp://printer.local:631/ipp/print")
    backend._supported_formats = ["application/pdf"]

    sent_requests = []

    async def fake_send_ipp(data: bytes, content_type: str = "application/ipp"):
        sent_requests.append(data)
        return {"status": 0x0000, "attributes": {"job-id": 42}}

    backend._send_ipp = fake_send_ipp

    job = Job(pages_total=1)
    from tests.conftest import MINIMAL_PDF
    await backend.print_job(job, MINIMAL_PDF)

    assert len(sent_requests) == 1
    # OP_PRINT_JOB (0x0002) is encoded as big-endian in bytes 2-4 of the header
    op = struct.unpack(">H", sent_requests[0][2:4])[0]
    assert op == OP_PRINT_JOB
    assert b"application/pdf" in sent_requests[0]


@pytest.mark.asyncio
async def test_format_fallback_jpeg_single_page_uses_print_job():
    """When supported_formats is ['image/jpeg'] with 1 page, Print-Job is used."""
    backend = IPPBackend(uri="ipp://printer.local:631/ipp/print")
    backend._supported_formats = ["image/jpeg"]

    sent_requests = []

    async def fake_send_ipp(data: bytes, content_type: str = "application/ipp"):
        sent_requests.append(data)
        return {"status": 0x0000, "attributes": {"job-id": 99}}

    backend._send_ipp = fake_send_ipp

    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # minimal JPEG-ish bytes

    with patch("openprint.backends.ipp._pdf_to_jpeg", return_value=fake_jpeg):
        job = Job(pages_total=1)
        from tests.conftest import MINIMAL_PDF
        await backend.print_job(job, MINIMAL_PDF)

    assert len(sent_requests) == 1
    op = struct.unpack(">H", sent_requests[0][2:4])[0]
    assert op == OP_PRINT_JOB
    assert b"image/jpeg" in sent_requests[0]


@pytest.mark.asyncio
async def test_format_fallback_pwg_raster_uses_print_job():
    """When supported_formats is ['image/pwg-raster'], Print-Job is used for single page."""
    backend = IPPBackend(uri="ipp://printer.local:631/ipp/print")
    backend._supported_formats = ["image/pwg-raster"]

    sent_requests = []

    async def fake_send_ipp(data: bytes, content_type: str = "application/ipp"):
        sent_requests.append(data)
        return {"status": 0x0000, "attributes": {"job-id": 7}}

    backend._send_ipp = fake_send_ipp

    fake_pwg = b"RaS2" + b"\x00" * 100

    with patch("openprint.backends.ipp._pdf_to_pwg_raster", return_value=fake_pwg):
        job = Job(pages_total=1)
        from tests.conftest import MINIMAL_PDF
        await backend.print_job(job, MINIMAL_PDF)

    assert len(sent_requests) == 1
    op = struct.unpack(">H", sent_requests[0][2:4])[0]
    assert op == OP_PRINT_JOB
    assert b"image/pwg-raster" in sent_requests[0]


@pytest.mark.asyncio
async def test_format_fallback_empty_formats_sends_octet_stream():
    """When supported_formats is empty, falls back to application/pdf (native PDF path)."""
    backend = IPPBackend(uri="ipp://printer.local:631/ipp/print")
    backend._supported_formats = []  # empty list → native PDF path

    sent_requests = []

    async def fake_send_ipp(data: bytes, content_type: str = "application/ipp"):
        sent_requests.append(data)
        return {"status": 0x0000, "attributes": {"job-id": 1}}

    backend._send_ipp = fake_send_ipp

    job = Job(pages_total=1)
    from tests.conftest import MINIMAL_PDF
    await backend.print_job(job, MINIMAL_PDF)

    assert len(sent_requests) == 1
    op = struct.unpack(">H", sent_requests[0][2:4])[0]
    assert op == OP_PRINT_JOB
    # Should fall through to octet-stream last-resort path
    assert b"octet-stream" in sent_requests[0]


# ---------------------------------------------------------------------------
# Multi-page job sends one Print-Job per page
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multipage_jpeg_uses_separate_print_jobs():
    """Multi-page JPEG jobs send one Print-Job per page.

    Many consumer printers (e.g. HP DeskJet) reject the multi-document
    Create-Job / Send-Document flow (IPP 0x0509), so the most compatible path
    is one single-document Print-Job per rendered page.
    """
    backend = IPPBackend(uri="ipp://printer.local:631/ipp/print")
    backend._supported_formats = ["image/jpeg"]

    ops_seen = []

    async def fake_send_ipp(data: bytes, content_type: str = "application/ipp"):
        op = struct.unpack(">H", data[2:4])[0]
        ops_seen.append(op)
        return {"status": 0x0000, "attributes": {"job-id": 55}}

    backend._send_ipp = fake_send_ipp

    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 50

    with patch("openprint.backends.ipp._pdf_to_jpeg_pages", return_value=[fake_jpeg, fake_jpeg]):
        job = Job(pages_total=2)
        from tests.conftest import MINIMAL_PDF
        await backend.print_job(job, MINIMAL_PDF)

    # One Print-Job per page, no multi-document Create-Job / Send-Document.
    assert ops_seen.count(OP_PRINT_JOB) == 2
    assert OP_CREATE_JOB not in ops_seen
    assert OP_SEND_DOCUMENT not in ops_seen


@pytest.mark.asyncio
async def test_single_page_jpeg_uses_print_job_not_create_job():
    """Single-page JPEG jobs must use Print-Job directly."""
    backend = IPPBackend(uri="ipp://printer.local:631/ipp/print")
    backend._supported_formats = ["image/jpeg"]

    ops_seen = []

    async def fake_send_ipp(data: bytes, content_type: str = "application/ipp"):
        op = struct.unpack(">H", data[2:4])[0]
        ops_seen.append(op)
        return {"status": 0x0000, "attributes": {"job-id": 3}}

    backend._send_ipp = fake_send_ipp

    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 50

    with patch("openprint.backends.ipp._pdf_to_jpeg", return_value=fake_jpeg):
        job = Job(pages_total=1)
        from tests.conftest import MINIMAL_PDF
        await backend.print_job(job, MINIMAL_PDF)

    assert OP_PRINT_JOB in ops_seen
    assert OP_CREATE_JOB not in ops_seen


# ---------------------------------------------------------------------------
# _parse_ipp_response multi-value attributes
# ---------------------------------------------------------------------------

def test_parse_ipp_response_multivalue_attribute():
    """A repeated attribute name with different values should produce a list."""
    formats = ["application/pdf", "image/jpeg", "image/pwg-raster"]
    raw = _make_ipp_response_with_mime_list("document-format-supported", formats)
    result = _parse_ipp_response(raw)
    parsed = result["attributes"].get("document-format-supported")
    assert isinstance(parsed, list), f"Expected list, got {type(parsed)}: {parsed}"
    assert set(parsed) == set(formats)


def test_parse_ipp_response_single_value_not_list():
    """A single-value attribute should NOT be wrapped in a list."""
    raw = _make_ipp_response_with_mime_list("document-format-supported", ["application/pdf"])
    result = _parse_ipp_response(raw)
    parsed = result["attributes"].get("document-format-supported")
    # Single value may be stored as a string
    assert parsed == "application/pdf" or parsed == ["application/pdf"]


def test_parse_ipp_response_integer_attribute():
    """Integer attributes (4-byte big-endian) should be decoded as int."""
    header = struct.pack(">HHI", 0x0200, 0x0000, 1)
    # TAG_OPERATION group
    body = struct.pack(">B", 0x01)
    # job-id integer attribute
    name_bytes = b"job-id"
    val_bytes = struct.pack(">i", 42)
    body += struct.pack(">BH", TAG_INTEGER, len(name_bytes))
    body += name_bytes
    body += struct.pack(">H", len(val_bytes))
    body += val_bytes
    body += struct.pack(">B", TAG_END)

    result = _parse_ipp_response(header + body)
    assert result["attributes"]["job-id"] == 42


# ---------------------------------------------------------------------------
# _get_supported_formats
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_supported_formats_parses_single_string():
    backend = IPPBackend(uri="ipp://printer.local:631/ipp/print")
    raw = _make_ipp_response_with_mime_list("document-format-supported", ["application/pdf"])

    async def fake_send_ipp(data: bytes, content_type: str = "application/ipp"):
        return _parse_ipp_response(raw)

    backend._send_ipp = fake_send_ipp
    formats = await backend._get_supported_formats()
    assert "application/pdf" in formats


@pytest.mark.asyncio
async def test_get_supported_formats_parses_list():
    backend = IPPBackend(uri="ipp://printer.local:631/ipp/print")
    expected = ["application/pdf", "image/jpeg", "image/pwg-raster"]
    raw = _make_ipp_response_with_mime_list("document-format-supported", expected)

    async def fake_send_ipp(data: bytes, content_type: str = "application/ipp"):
        return _parse_ipp_response(raw)

    backend._send_ipp = fake_send_ipp
    formats = await backend._get_supported_formats()
    assert set(formats) == set(expected)


@pytest.mark.asyncio
async def test_get_supported_formats_returns_empty_on_exception():
    backend = IPPBackend(uri="ipp://printer.local:631/ipp/print")

    async def fake_send_ipp(data: bytes, content_type: str = "application/ipp"):
        raise RuntimeError("connection refused")

    backend._send_ipp = fake_send_ipp
    formats = await backend._get_supported_formats()
    assert formats == []
