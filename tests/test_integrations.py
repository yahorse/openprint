from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from openprint import integrations as ip
from openprint.integrations import OPPConfig


def _fresh_config() -> OPPConfig:
    d = Path(tempfile.mkdtemp(prefix="opp-cfg-"))
    return OPPConfig(path=d / "config.json")


def test_normalize_target_variants():
    assert ip._normalize_target("192.168.1.5") == "ipp://192.168.1.5:631/ipp/print"
    assert ip._normalize_target("192.168.1.5:9100") == "ipp://192.168.1.5:9100/ipp/print"
    assert ip._normalize_target("ipp://x/ipp/print") == "ipp://x/ipp/print"
    assert ip._normalize_target("http://x:631") == "http://x:631"
    assert ip._is_ipp(ip._normalize_target("host"))
    assert not ip._is_ipp("http://host:631")


def test_config_roundtrip():
    cfg = _fresh_config()
    assert cfg.default_printer is None
    cfg.default_printer = "ipp://1.2.3.4:631/ipp/print"
    cfg.remember_wifi("DIRECT-52", "pw")
    reloaded = OPPConfig(path=cfg.path)
    assert reloaded.default_printer == "ipp://1.2.3.4:631/ipp/print"
    assert reloaded.wifi_password("DIRECT-52") == "pw"


def test_resolve_prefers_explicit(monkeypatch):
    # explicit url wins, no discovery attempted
    monkeypatch.setattr(ip, "PrinterScanner", None)  # would crash if used
    assert ip.resolve_printer("host", config=_fresh_config()) == "ipp://host:631/ipp/print"


def test_resolve_falls_back_to_saved(monkeypatch):
    cfg = _fresh_config()
    cfg.default_printer = "ipp://saved:631/ipp/print"

    class _EmptyScanner:
        async def scan(self, timeout: float = 3.0):
            return []

    monkeypatch.setattr(ip, "PrinterScanner", _EmptyScanner)
    assert ip.resolve_printer(None, config=cfg) == "ipp://saved:631/ipp/print"


def test_resolve_raises_when_nothing(monkeypatch):
    class _EmptyScanner:
        async def scan(self, timeout: float = 3.0):
            return []

    monkeypatch.setattr(ip, "PrinterScanner", _EmptyScanner)
    with pytest.raises(RuntimeError):
        ip.resolve_printer(None, config=_fresh_config())


def test_convert_text_to_pdf():
    src = Path(tempfile.mkdtemp()) / "note.txt"
    src.write_text("line one\nline two\n", "utf-8")
    pdf = ip.convert_to_pdf(src)
    assert pdf.suffix == ".pdf"
    assert pdf.read_bytes().startswith(b"%PDF")


def test_convert_unsupported_raises():
    src = Path(tempfile.mkdtemp()) / "thing.xyz"
    src.write_text("data", "utf-8")
    with pytest.raises(ValueError):
        ip.convert_to_pdf(src)


def test_convert_pdf_passthrough():
    from openprint.testkit import TEST_PDF

    src = Path(tempfile.mkdtemp()) / "doc.pdf"
    src.write_bytes(TEST_PDF)
    # PDFs are returned unchanged (same path).
    assert ip.convert_to_pdf(src) == src
