from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from openprint.backends.cups import CUPSBackend
from openprint.bridge import Bridge, BridgedPrinter
from openprint.models import Capabilities, PrinterState, SupplyLevels

from tests.conftest import MINIMAL_PDF


def _make_mock_backend(name: str) -> CUPSBackend:
    backend = CUPSBackend(printer_name=name)
    backend.get_state = AsyncMock(return_value=PrinterState.IDLE)
    backend.get_capabilities = AsyncMock(return_value=Capabilities())
    backend.get_supplies = AsyncMock(return_value=SupplyLevels())
    backend.get_printer_name = AsyncMock(return_value=name)
    backend.print_job = AsyncMock()
    backend.cancel_job = AsyncMock()
    return backend


@pytest.fixture
def bridge():
    b = Bridge(enable_discovery=False, log_requests=False)
    b.printers = {
        "HP_LaserJet": BridgedPrinter("HP_LaserJet", _make_mock_backend("HP_LaserJet")),
        "Canon_Inkjet": BridgedPrinter("Canon_Inkjet", _make_mock_backend("Canon_Inkjet")),
    }
    return b


@pytest.fixture
def bridge_app(bridge):
    with patch.object(CUPSBackend, "list_printers", new_callable=AsyncMock, return_value=[]):
        return TestClient(bridge.create_app())


def test_list_printers(bridge_app):
    resp = bridge_app.get("/opp/v1/printers")
    assert resp.status_code == 200
    printers = resp.json()
    assert len(printers) == 2
    names = {p["id"] for p in printers}
    assert "HP_LaserJet" in names
    assert "Canon_Inkjet" in names


def test_get_specific_printer(bridge_app):
    resp = bridge_app.get("/opp/v1/printers/HP_LaserJet")
    assert resp.status_code == 200
    assert resp.json()["name"] == "HP_LaserJet"


def test_get_default_printer(bridge_app):
    resp = bridge_app.get("/opp/v1/printer")
    assert resp.status_code == 200
    assert resp.json()["name"] in ("HP_LaserJet", "Canon_Inkjet")


def test_get_unknown_printer(bridge_app):
    resp = bridge_app.get("/opp/v1/printers/nonexistent")
    assert resp.status_code == 404


def test_submit_job_to_bridge(bridge_app):
    resp = bridge_app.post(
        "/opp/v1/jobs",
        files={"file": ("test.pdf", MINIMAL_PDF, "application/pdf")},
        data={"printer": "HP_LaserJet"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["printer"] == "HP_LaserJet"
    assert data["status"] == "queued"


def test_submit_job_default_printer(bridge_app):
    resp = bridge_app.post(
        "/opp/v1/jobs",
        files={"file": ("test.pdf", MINIMAL_PDF, "application/pdf")},
    )
    assert resp.status_code == 201


def test_list_jobs_across_printers(bridge_app):
    bridge_app.post(
        "/opp/v1/jobs",
        files={"file": ("a.pdf", MINIMAL_PDF, "application/pdf")},
        data={"printer": "HP_LaserJet"},
    )
    bridge_app.post(
        "/opp/v1/jobs",
        files={"file": ("b.pdf", MINIMAL_PDF, "application/pdf")},
        data={"printer": "Canon_Inkjet"},
    )
    resp = bridge_app.get("/opp/v1/jobs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


def test_status_all_printers(bridge_app):
    resp = bridge_app.get("/opp/v1/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "HP_LaserJet" in data
    assert "Canon_Inkjet" in data


def test_submit_invalid_pdf(bridge_app):
    resp = bridge_app.post(
        "/opp/v1/jobs",
        files={"file": ("bad.txt", b"not a pdf", "application/pdf")},
    )
    assert resp.status_code == 400
