from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openprint.discovery import SERVICE_TYPE, PrinterAdvertiser, PrinterScanner


# ---------------------------------------------------------------------------
# Original tests (preserved)
# ---------------------------------------------------------------------------

def test_service_type():
    assert SERVICE_TYPE == "_opp._tcp.local."


def test_advertiser_init():
    adv = PrinterAdvertiser(name="Test", port=631)
    assert adv.name == "Test"
    assert adv.port == 631


def test_scanner_init():
    scanner = PrinterScanner()
    assert scanner._found == []


# ---------------------------------------------------------------------------
# PrinterAdvertiser
# ---------------------------------------------------------------------------

def test_advertiser_service_info_contains_correct_name():
    adv = PrinterAdvertiser(name="My Printer", port=8631)
    assert adv._info.name == f"My Printer.{SERVICE_TYPE}"


def test_advertiser_service_info_contains_correct_port():
    adv = PrinterAdvertiser(name="Test", port=9000)
    assert adv._info.port == 9000


def test_advertiser_service_info_properties_color_and_duplex():
    adv = PrinterAdvertiser(name="Colorful", port=631, color=True, duplex=False)
    props = adv._info.properties
    # properties values may be bytes or str depending on zeroconf version
    color_val = props.get(b"color") or props.get("color")
    duplex_val = props.get(b"duplex") or props.get("duplex")
    assert color_val in (b"true", "true")
    assert duplex_val in (b"false", "false")


def test_advertiser_get_local_ip_returns_string():
    ip = PrinterAdvertiser._get_local_ip()
    assert isinstance(ip, str)
    # Should be a valid dotted-quad or loopback
    parts = ip.split(".")
    assert len(parts) == 4


def test_advertiser_get_local_ip_fallback_on_os_error():
    with patch("socket.socket") as mock_socket_cls:
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = OSError("no network")
        mock_socket_cls.return_value = mock_sock
        ip = PrinterAdvertiser._get_local_ip()
    assert ip == "127.0.0.1"


# ---------------------------------------------------------------------------
# PrinterScanner._on_change
# ---------------------------------------------------------------------------

def test_scanner_on_change_added_appends_printer():
    from zeroconf import ServiceStateChange

    scanner = PrinterScanner()

    # Build a mock ServiceInfo
    mock_info = MagicMock()
    mock_info.addresses = [socket.inet_aton("192.168.1.10")]
    mock_info.port = 631
    mock_info.properties = {
        b"name": b"HP LaserJet",
        b"color": b"true",
        b"duplex": b"false",
        b"v": b"1",
        b"pdf": b"2.0",
    }

    mock_zeroconf = MagicMock()
    mock_zeroconf.get_service_info.return_value = mock_info

    scanner._on_change(
        mock_zeroconf,
        SERVICE_TYPE,
        f"HP LaserJet.{SERVICE_TYPE}",
        ServiceStateChange.Added,
    )

    assert len(scanner._found) == 1
    found = scanner._found[0]
    assert found["host"] == "192.168.1.10"
    assert found["port"] == 631
    assert found["color"] is True
    assert found["duplex"] is False


def test_scanner_on_change_added_no_service_info_ignored():
    from zeroconf import ServiceStateChange

    scanner = PrinterScanner()
    mock_zeroconf = MagicMock()
    mock_zeroconf.get_service_info.return_value = None

    scanner._on_change(
        mock_zeroconf,
        SERVICE_TYPE,
        "Unknown.local.",
        ServiceStateChange.Added,
    )

    assert scanner._found == []


def test_scanner_on_change_removed_is_ignored_gracefully():
    """PrinterScanner only handles Added; Removed should not raise."""
    from zeroconf import ServiceStateChange

    scanner = PrinterScanner()
    mock_zeroconf = MagicMock()

    # Should not raise
    scanner._on_change(
        mock_zeroconf,
        SERVICE_TYPE,
        "OldPrinter.local.",
        ServiceStateChange.Removed,
    )

    assert scanner._found == []


def test_scanner_found_list_accumulates_multiple_printers():
    from zeroconf import ServiceStateChange

    scanner = PrinterScanner()

    def make_info(ip: str, name_bytes: bytes) -> MagicMock:
        mock_info = MagicMock()
        mock_info.addresses = [socket.inet_aton(ip)]
        mock_info.port = 631
        mock_info.properties = {
            b"name": name_bytes,
            b"color": b"true",
            b"duplex": b"true",
        }
        return mock_info

    mock_zeroconf = MagicMock()
    mock_zeroconf.get_service_info.side_effect = [
        make_info("10.0.0.1", b"Printer A"),
        make_info("10.0.0.2", b"Printer B"),
    ]

    for suffix in ["A", "B"]:
        scanner._on_change(
            mock_zeroconf,
            SERVICE_TYPE,
            f"Printer {suffix}.{SERVICE_TYPE}",
            ServiceStateChange.Added,
        )

    assert len(scanner._found) == 2
    hosts = {p["host"] for p in scanner._found}
    assert hosts == {"10.0.0.1", "10.0.0.2"}
