import pytest

from openprint.scanner import (
    CUPSWatcher,
    IPP_SERVICE_TYPES,
    NetworkPrinterScanner,
)


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
