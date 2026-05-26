from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openprint.scanner import (
    IPP_SERVICE_TYPES,
    CUPSWatcher,
    NetworkPrinterScanner,
)


# ---------------------------------------------------------------------------
# Original tests (preserved)
# ---------------------------------------------------------------------------

def test_ipp_service_types():
    assert "_ipp._tcp.local." in IPP_SERVICE_TYPES
    assert "_ipps._tcp.local." in IPP_SERVICE_TYPES


def test_scanner_init():
    scanner = NetworkPrinterScanner()
    assert scanner.known_printers == {}


def test_scanner_with_callbacks():
    async def on_found(p):
        pass

    async def on_lost(name):
        pass

    scanner = NetworkPrinterScanner(on_found=on_found, on_lost=on_lost)
    assert scanner._on_found is on_found
    assert scanner._on_lost is on_lost


def test_cups_watcher_init():
    watcher = CUPSWatcher(interval=5.0)
    assert watcher._interval == 5.0
    assert watcher._known == set()


# ---------------------------------------------------------------------------
# NetworkPrinterScanner._parse_service_info
# ---------------------------------------------------------------------------

def _make_service_info(
    ip: str = "192.168.1.50",
    port: int = 631,
    props: dict[bytes, bytes] | None = None,
) -> MagicMock:
    info = MagicMock()
    info.addresses = [socket.inet_aton(ip)]
    info.port = port
    info.properties = props or {
        b"ty": b"HP LaserJet Pro",
        b"Color": b"T",
        b"Duplex": b"T",
        b"pdl": b"application/pdf,image/jpeg",
    }
    info.name = "HP_LaserJet._ipp._tcp.local."
    return info


def test_parse_service_info_returns_expected_keys():
    scanner = NetworkPrinterScanner()
    info = _make_service_info()
    result = scanner._parse_service_info(info, "_ipp._tcp.local.")
    assert result is not None
    for key in ("id", "name", "host", "port", "tls", "uri", "color", "duplex"):
        assert key in result, f"Missing key: {key}"


def test_parse_service_info_ipp_not_tls():
    scanner = NetworkPrinterScanner()
    info = _make_service_info(ip="10.0.0.1", port=631)
    result = scanner._parse_service_info(info, "_ipp._tcp.local.")
    assert result is not None
    assert result["tls"] is False
    assert result["uri"].startswith("ipp://")


def test_parse_service_info_ipps_is_tls():
    scanner = NetworkPrinterScanner()
    info = _make_service_info(ip="10.0.0.1", port=443)
    result = scanner._parse_service_info(info, "_ipps._tcp.local.")
    assert result is not None
    assert result["tls"] is True
    assert result["uri"].startswith("ipps://")


def test_parse_service_info_no_address_returns_none():
    scanner = NetworkPrinterScanner()
    info = MagicMock()
    info.addresses = []
    info.port = 631
    info.properties = {}
    info.name = "Empty._ipp._tcp.local."
    result = scanner._parse_service_info(info, "_ipp._tcp.local.")
    assert result is None


def test_parse_service_info_printer_id_format():
    scanner = NetworkPrinterScanner()
    info = _make_service_info(ip="192.168.2.100", port=631)
    result = scanner._parse_service_info(info, "_ipp._tcp.local.")
    assert result is not None
    assert result["id"] == "ipp_192_168_2_100_631"


def test_parse_service_info_color_and_duplex_from_props():
    scanner = NetworkPrinterScanner()
    info = _make_service_info(
        props={b"ty": b"Test Printer", b"Color": b"F", b"Duplex": b"T"}
    )
    result = scanner._parse_service_info(info, "_ipp._tcp.local.")
    assert result is not None
    assert result["color"] is False
    assert result["duplex"] is True


# ---------------------------------------------------------------------------
# NetworkPrinterScanner._on_state_change
# ---------------------------------------------------------------------------

def test_on_state_change_added_calls_on_found():
    from zeroconf import ServiceStateChange

    found_printers = []

    loop = asyncio.new_event_loop()

    async def on_found(p):
        found_printers.append(p)

    scanner = NetworkPrinterScanner(on_found=on_found)
    scanner._loop = loop

    info = _make_service_info(ip="10.1.1.1", port=631)
    mock_zeroconf = MagicMock()
    mock_zeroconf.get_service_info.return_value = info

    scanner._on_state_change(
        mock_zeroconf, "_ipp._tcp.local.", "HP._ipp._tcp.local.", ServiceStateChange.Added
    )

    # Run the coroutine submitted to the loop
    loop.run_until_complete(asyncio.sleep(0.05))
    loop.close()

    assert len(found_printers) == 1
    assert found_printers[0]["host"] == "10.1.1.1"


def test_on_state_change_removed_calls_on_lost():
    from zeroconf import ServiceStateChange

    lost_ids = []

    loop = asyncio.new_event_loop()

    async def on_lost(pid):
        lost_ids.append(pid)

    scanner = NetworkPrinterScanner(on_lost=on_lost)
    scanner._loop = loop

    # Pre-populate known printers so the removal is recognised
    scanner._known["ipp_HP_LaserJet"] = {"id": "ipp_HP_LaserJet", "name": "HP LaserJet"}

    mock_zeroconf = MagicMock()
    scanner._on_state_change(
        mock_zeroconf,
        "_ipp._tcp.local.",
        "HP LaserJet._ipp._tcp.local.",
        ServiceStateChange.Removed,
    )

    loop.run_until_complete(asyncio.sleep(0.05))
    loop.close()

    assert "ipp_HP_LaserJet" in lost_ids
    assert "ipp_HP_LaserJet" not in scanner._known


def test_on_state_change_added_no_service_info_ignored():
    from zeroconf import ServiceStateChange

    scanner = NetworkPrinterScanner()
    mock_zeroconf = MagicMock()
    mock_zeroconf.get_service_info.return_value = None

    # Should not raise or add anything
    scanner._on_state_change(
        mock_zeroconf, "_ipp._tcp.local.", "Unknown._ipp._tcp.local.", ServiceStateChange.Added
    )

    assert scanner.known_printers == {}


# ---------------------------------------------------------------------------
# CUPSWatcher callbacks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cups_watcher_calls_on_found_for_new_printer():
    found = []

    async def on_found(p):
        found.append(p)

    watcher = CUPSWatcher(interval=0.05, on_found=on_found)

    printer_list = [{"name": "HP_LaserJet", "uri": "ipp://localhost/hp"}]

    with patch(
        "openprint.scanner.CUPSBackend.list_printers",
        new_callable=AsyncMock,
        return_value=printer_list,
    ):
        watcher._task = asyncio.create_task(watcher._poll_loop())
        await asyncio.sleep(0.15)
        watcher._task.cancel()
        try:
            await watcher._task
        except asyncio.CancelledError:
            pass

    assert any(p["name"] == "HP_LaserJet" for p in found)


@pytest.mark.asyncio
async def test_cups_watcher_calls_on_lost_when_printer_disappears():
    lost = []

    async def on_lost(name):
        lost.append(name)

    watcher = CUPSWatcher(interval=0.05, on_lost=on_lost)
    watcher._known = {"HP_LaserJet"}

    # First poll returns empty list → HP_LaserJet was removed
    with patch(
        "openprint.scanner.CUPSBackend.list_printers",
        new_callable=AsyncMock,
        return_value=[],
    ):
        watcher._task = asyncio.create_task(watcher._poll_loop())
        await asyncio.sleep(0.15)
        watcher._task.cancel()
        try:
            await watcher._task
        except asyncio.CancelledError:
            pass

    assert "HP_LaserJet" in lost


@pytest.mark.asyncio
async def test_cups_watcher_tolerates_list_printers_exception():
    """CUPSWatcher should log and continue when CUPSBackend.list_printers raises."""
    watcher = CUPSWatcher(interval=0.05)

    with patch(
        "openprint.scanner.CUPSBackend.list_printers",
        new_callable=AsyncMock,
        side_effect=RuntimeError("cups not running"),
    ):
        watcher._task = asyncio.create_task(watcher._poll_loop())
        await asyncio.sleep(0.15)
        watcher._task.cancel()
        try:
            await watcher._task
        except asyncio.CancelledError:
            pass

    # If we reach here without an unhandled exception the test passes
    assert True
