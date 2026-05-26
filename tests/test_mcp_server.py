from __future__ import annotations

import json
import tempfile
from unittest.mock import patch

import openprint.mcp_server as mcp_mod
from openprint.mcp_server import (
    _get_client,
    cancel_job,
    check_printer_compatibility,
    discover_printers,
    duplex_modes,
    get_job_status,
    get_printer_info,
    get_printer_status,
    list_jobs,
    media_sizes,
    print_document,
    protocol_summary,
)
from openprint.testkit import TEST_PDF


def _reset_client():
    mcp_mod._client = None


def test_discover_printers_found():
    _reset_client()
    fake_printers = [
        {"name": "Office", "host": "192.168.1.10", "port": 631, "color": True, "duplex": True},
    ]
    with patch.object(mcp_mod.Client, "discover", return_value=fake_printers):
        result = json.loads(discover_printers())
    assert result["count"] == 1
    assert result["printers"][0]["name"] == "Office"


def test_discover_printers_none():
    _reset_client()
    with patch.object(mcp_mod.Client, "discover", return_value=[]):
        result = json.loads(discover_printers())
    assert result["printers"] == []
    assert "No printers found" in result["message"]


def test_print_document_file_not_found():
    result = json.loads(print_document("/nonexistent/file.pdf"))
    assert "error" in result
    assert "not found" in result["error"].lower()


def test_print_document_not_pdf():
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"hello")
        f.flush()
        result = json.loads(print_document(f.name))
    assert "error" in result
    assert "PDF" in result["error"]


def test_print_document_success():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(TEST_PDF)
        f.flush()
        pdf_path = f.name

    fake_response = {"job_id": "job_abc123", "status": "queued"}
    with patch.object(mcp_mod.Client, "print", return_value=fake_response):
        result = json.loads(print_document(pdf_path, printer_url="http://localhost:631"))
    assert result["job_id"] == "job_abc123"


def test_get_printer_info_success():
    fake_info = {"name": "Test Printer", "status": "idle", "capabilities": {"color": True}}
    with patch.object(mcp_mod.Client, "printer_info", return_value=fake_info):
        result = json.loads(get_printer_info(printer_url="http://localhost:631"))
    assert result["name"] == "Test Printer"


def test_get_printer_info_error():
    with patch.object(mcp_mod.Client, "printer_info", side_effect=Exception("Connection refused")):
        result = json.loads(get_printer_info(printer_url="http://localhost:631"))
    assert "error" in result


def test_get_printer_status_success():
    fake_status = {"state": "idle", "supplies": {"black": 80}, "jobs_queued": 0}
    with patch.object(mcp_mod.Client, "printer_status", return_value=fake_status):
        result = json.loads(get_printer_status(printer_url="http://localhost:631"))
    assert result["state"] == "idle"


def test_list_jobs_success():
    fake_jobs = {"jobs": [{"id": "job_1", "status": "completed"}], "total": 1}
    with patch.object(mcp_mod.Client, "list_jobs", return_value=fake_jobs):
        result = json.loads(list_jobs(printer_url="http://localhost:631"))
    assert result["total"] == 1


def test_get_job_status_success():
    fake_job = {"id": "job_abc", "status": "printing", "pages_printed": 2, "pages_total": 5}
    with patch.object(mcp_mod.Client, "job_status", return_value=fake_job):
        result = json.loads(get_job_status("job_abc", printer_url="http://localhost:631"))
    assert result["status"] == "printing"
    assert result["pages_printed"] == 2


def test_cancel_job_success():
    fake_result = {"id": "job_abc", "status": "canceled"}
    with patch.object(mcp_mod.Client, "cancel_job", return_value=fake_result):
        result = json.loads(cancel_job("job_abc", printer_url="http://localhost:631"))
    assert result["status"] == "canceled"


def test_cancel_job_error():
    with patch.object(mcp_mod.Client, "cancel_job", side_effect=Exception("Job not found")):
        result = json.loads(cancel_job("job_nope", printer_url="http://localhost:631"))
    assert "error" in result


def test_check_printer_compatibility_unreachable():
    result = json.loads(check_printer_compatibility("192.0.2.1", port=9999))
    assert result["host"] == "192.0.2.1"


def test_protocol_summary_resource():
    result = json.loads(protocol_summary())
    assert result["protocol"] == "OpenPrint Protocol v1"
    assert "POST /opp/v1/jobs" in result["endpoints"]
    assert result["default_port"] == 631


def test_duplex_modes_resource():
    result = json.loads(duplex_modes())
    assert "none" in result["modes"]
    assert "long-edge" in result["modes"]
    assert "short-edge" in result["modes"]


def test_media_sizes_resource():
    result = json.loads(media_sizes())
    assert "a4" in result["sizes"]
    assert "letter" in result["sizes"]
    assert result["default"] == "a4"


def test_get_client_reuses_instance():
    _reset_client()
    c1 = _get_client()
    c2 = _get_client()
    assert c1 is c2


def test_get_client_with_url_creates_new():
    _reset_client()
    c1 = _get_client(printer_url="http://host1:631")
    c2 = _get_client(printer_url="http://host2:631")
    assert c1 is not c2


def test_mcp_server_has_tools():
    from openprint.mcp_server import mcp
    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    assert "discover_printers" in tool_names
    assert "print_document" in tool_names
    assert "get_printer_info" in tool_names
    assert "get_printer_status" in tool_names
    assert "list_jobs" in tool_names
    assert "get_job_status" in tool_names
    assert "cancel_job" in tool_names
    assert "test_printer" in tool_names
