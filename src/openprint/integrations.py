"""High-level conveniences that make the MCP server "just work".

This module wraps the lower-level pieces (discovery, the IPP backend, the OPP
HTTP client) with the glue an AI assistant needs to print *anything* to
*whatever printer is around* without the caller wiring it up by hand:

* :func:`convert_to_pdf`   — render HTML / text / images to PDF so any file prints.
* :class:`OPPConfig`       — persist the last-used printer and Wi-Fi Direct creds.
* :func:`resolve_printer`  — discover via mDNS, else fall back to the saved printer.
* :func:`print_file`       — one call: convert if needed, route to IPP or OPP, return a job.
* :func:`connect_wifi_direct` / :func:`restore_wifi` — bring up a printer's
  Wi-Fi Direct SoftAP on Windows (netsh) and reconnect afterwards.
* :func:`ensure_bridge`    — best-effort: make sure an `opp bridge` is reachable.

Everything degrades gracefully: missing optional deps or non-Windows hosts
raise clear, actionable errors rather than blowing up deep in a stack trace.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from openprint.backends.ipp import IPPBackend
from openprint.discovery import PrinterScanner
from openprint.models import DuplexMode, Job

# --------------------------------------------------------------------------- #
# Config persistence
# --------------------------------------------------------------------------- #

CONFIG_DIR = Path(os.environ.get("OPENPRINT_HOME", Path.home() / ".openprint"))
CONFIG_PATH = CONFIG_DIR / "config.json"


class OPPConfig:
    """Tiny JSON-backed store for the default printer and Wi-Fi Direct profiles.

    Shape on disk::

        {
          "default_printer": "ipp://192.168.68.50:631/ipp/print",
          "wifi_direct": {"DIRECT-52-HP DeskJet 2900 series": "12345678"}
        }
    """

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self._data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        try:
            self._data = json.loads(self.path.read_text("utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2), "utf-8")

    @property
    def default_printer(self) -> str | None:
        return self._data.get("default_printer")

    @default_printer.setter
    def default_printer(self, value: str | None) -> None:
        if value:
            self._data["default_printer"] = value
        else:
            self._data.pop("default_printer", None)
        self.save()

    def wifi_password(self, ssid: str) -> str | None:
        return self._data.get("wifi_direct", {}).get(ssid)

    def remember_wifi(self, ssid: str, password: str) -> None:
        self._data.setdefault("wifi_direct", {})[ssid] = password
        self.save()


# --------------------------------------------------------------------------- #
# File -> PDF conversion
# --------------------------------------------------------------------------- #

# Files we can hand to a printer (or convert) directly.
_PDF_SUFFIXES = {".pdf"}
_HTML_SUFFIXES = {".html", ".htm"}
_TEXT_SUFFIXES = {".txt", ".md", ".log", ".csv"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


def _find_chrome() -> str | None:
    """Locate a Chromium-family browser usable for headless HTML->PDF."""
    env = os.environ.get("OPENPRINT_CHROME")
    if env and Path(env).exists():
        return env
    for name in ("chrome", "chromium", "chromium-browser", "msedge", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def _html_to_pdf(src: Path, dst: Path) -> None:
    chrome = _find_chrome()
    if not chrome:
        raise RuntimeError(
            "No Chrome/Edge/Chromium found to render HTML. Set OPENPRINT_CHROME "
            "to a browser executable, or convert the file to PDF yourself."
        )
    # file:// URL so relative assets resolve.
    url = src.resolve().as_uri()
    # A dedicated user-data-dir forces a fresh browser process; otherwise, when a
    # normal Chrome is already running, the headless launch delegates to it and
    # exits 0 without ever rendering the PDF.
    profile = Path(tempfile.mkdtemp(prefix="openprint-chrome-"))
    if dst.exists():
        dst.unlink()
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={profile}",
        "--no-pdf-header-footer",
        f"--print-to-pdf={dst}",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    # Some builds write the file just after exit; poll briefly before giving up.
    for _ in range(20):
        if dst.exists() and dst.stat().st_size > 0:
            break
        time.sleep(0.25)
    shutil.rmtree(profile, ignore_errors=True)
    if not dst.exists() or dst.stat().st_size == 0:
        # Retry once with the legacy headless flag for older browser builds.
        cmd[1] = "--headless"
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        for _ in range(20):
            if dst.exists() and dst.stat().st_size > 0:
                break
            time.sleep(0.25)
    if not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError(
            f"HTML->PDF conversion produced no output (exit {proc.returncode}). "
            f"{(proc.stderr or '').strip()[:300]}"
        )


def _text_to_pdf(src: Path, dst: Path) -> None:
    try:
        import fitz  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "pymupdf is required to print text files. Install: pip install pymupdf"
        ) from exc
    text = src.read_text("utf-8", errors="replace")
    doc = fitz.open()
    # US Letter; insert_textbox paginates by returning leftover text.
    rect = fitz.Rect(54, 54, 558, 738)
    remaining = text
    while remaining:
        page = doc.new_page(width=612, height=792)
        leftover = page.insert_textbox(rect, remaining, fontsize=10, fontname="cour")
        # insert_textbox returns the height used (>=0) or a negative overflow value;
        # when it can't fit everything it returns the count of chars NOT written.
        if leftover is None or leftover >= 0 or not isinstance(leftover, (int, float)):
            break
        written = len(remaining) + int(leftover)  # leftover is negative
        if written <= 0:
            break
        remaining = remaining[written:]
    doc.save(dst)


def _image_to_pdf(src: Path, dst: Path) -> None:
    try:
        import fitz  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "pymupdf is required to print images. Install: pip install pymupdf"
        ) from exc
    doc = fitz.open()
    img = fitz.open(str(src))
    rect = img[0].rect
    pdfbytes = img.convert_to_pdf()
    img.close()
    imgpdf = fitz.open("pdf", pdfbytes)
    page = doc.new_page(width=rect.width, height=rect.height)
    page.show_pdf_page(page.rect, imgpdf, 0)
    doc.save(dst)


def convert_to_pdf(file_path: str | Path) -> Path:
    """Return a path to a PDF representation of *file_path*.

    PDFs are returned unchanged. HTML, text and images are rendered to a PDF in
    a temp directory. Unsupported types raise ``ValueError``.
    """
    src = Path(file_path)
    if not src.exists():
        raise FileNotFoundError(f"File not found: {src}")
    suffix = src.suffix.lower()
    if suffix in _PDF_SUFFIXES:
        return src

    tmp_dir = Path(tempfile.gettempdir()) / "openprint"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dst = tmp_dir / (src.stem + ".pdf")

    if suffix in _HTML_SUFFIXES:
        _html_to_pdf(src, dst)
    elif suffix in _TEXT_SUFFIXES:
        _text_to_pdf(src, dst)
    elif suffix in _IMAGE_SUFFIXES:
        _image_to_pdf(src, dst)
    else:
        raise ValueError(
            f"Don't know how to convert '{suffix}' to PDF. Supported: PDF, HTML, "
            "text (.txt/.md/.csv/.log), and images (.png/.jpg/...)."
        )
    return dst


# --------------------------------------------------------------------------- #
# Target resolution + printing
# --------------------------------------------------------------------------- #


def _is_ipp(target: str) -> bool:
    return target.startswith(("ipp://", "ipps://"))


def _is_http(target: str) -> bool:
    return target.startswith(("http://", "https://"))


def _normalize_target(target: str) -> str:
    """Turn a bare host/IP into a full IPP printer URI."""
    if _is_ipp(target) or _is_http(target):
        return target
    # bare host or host:port -> raw IPP printer
    if ":" not in target:
        target = f"{target}:631"
    return f"ipp://{target}/ipp/print"


def resolve_printer(
    printer_url: str | None = None,
    *,
    discover_timeout: float = 3.0,
    config: OPPConfig | None = None,
) -> str:
    """Decide what to print to.

    Priority: explicit ``printer_url`` -> mDNS discovery -> saved default printer.
    Returns a normalized target (``ipp://...``, ``http://...``). Raises if nothing
    can be found.
    """
    if printer_url:
        return _normalize_target(printer_url)

    # mDNS discovery (OPP-native printers / bridges)
    try:
        scanner = PrinterScanner()
        printers = asyncio.run(scanner.scan(timeout=discover_timeout))
    except Exception:
        printers = []
    if printers:
        p = printers[0]
        return f"http://{p['host']}:{p['port']}"

    cfg = config or OPPConfig()
    if cfg.default_printer:
        return _normalize_target(cfg.default_printer)

    raise RuntimeError(
        "No printer specified, none discovered on the network, and no default "
        "saved. Set one with set_default_printer, or pass printer_url."
    )


def _count_pdf_pages(pdf_data: bytes) -> int:
    try:
        import fitz  # type: ignore[import]

        return len(fitz.open(stream=pdf_data, filetype="pdf"))
    except Exception:
        from openprint.pdf import validate_pdf

        try:
            return validate_pdf(pdf_data)
        except Exception:
            return 1


def _ipp_print(
    uri: str,
    pdf_data: bytes,
    *,
    copies: int,
    color: bool,
    duplex: str,
    media: str,
) -> dict[str, Any]:
    """Print to a raw IPP printer (the path proven to work with the HP DeskJet)."""
    backend = IPPBackend(uri=uri)
    job = Job(
        pages_total=_count_pdf_pages(pdf_data),
        copies=copies,
        color=color,
        duplex=DuplexMode(duplex),
        media=media,
    )

    async def _run() -> None:
        backend._supported_formats = await backend._get_supported_formats()
        await backend.print_job(job, pdf_data)

    asyncio.run(_run())
    return {
        "id": job.id,
        "ipp_job_id": backend._ipp_job_ids.get(job.id),
        "status": "accepted",
        "transport": "ipp",
        "printer": uri,
        "formats": backend._supported_formats,
    }


def _opp_print(
    base_url: str,
    pdf_path: Path,
    *,
    copies: int,
    color: bool,
    duplex: str,
    media: str,
    pages: str,
    priority: int,
    auth_token: str | None,
) -> dict[str, Any]:
    """Print via the OPP HTTP protocol (an `opp server`/`opp bridge`)."""
    from openprint.client import Client

    client = Client(base_url=base_url, auth_token=auth_token)
    result = client.print(
        file_path=pdf_path,
        copies=copies,
        color=color,
        duplex=DuplexMode(duplex),
        media=media,
        pages=pages,
        priority=priority,
    )
    result.setdefault("transport", "opp")
    result.setdefault("printer", base_url)
    return result


def print_file(
    file_path: str | Path,
    *,
    printer_url: str | None = None,
    copies: int = 1,
    color: bool = True,
    duplex: str = "none",
    media: str = "a4",
    pages: str = "all",
    priority: int = 50,
    auth_token: str | None = None,
    remember: bool = True,
    config: OPPConfig | None = None,
) -> dict[str, Any]:
    """Convert (if needed), resolve a target, print, and remember the printer.

    Routes raw-IPP targets through :class:`IPPBackend` (handles printers with no
    PDF interpreter by rasterising) and OPP HTTP targets through the client.
    """
    cfg = config or OPPConfig()
    pdf_path = convert_to_pdf(file_path)
    target = resolve_printer(printer_url, config=cfg)

    if _is_ipp(target):
        result = _ipp_print(
            target,
            pdf_path.read_bytes(),
            copies=copies,
            color=color,
            duplex=duplex,
            media=media,
        )
    else:
        result = _opp_print(
            target,
            pdf_path,
            copies=copies,
            color=color,
            duplex=duplex,
            media=media,
            pages=pages,
            priority=priority,
            auth_token=auth_token,
        )

    if str(pdf_path) != str(Path(file_path)):
        result["converted_from"] = str(Path(file_path))
    if remember:
        cfg.default_printer = target
    return result


# --------------------------------------------------------------------------- #
# Wi-Fi Direct (Windows) — bring up the printer's SoftAP, then restore
# --------------------------------------------------------------------------- #

_WLAN_PROFILE_TEMPLATE = """<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{ssid}</name>
  <SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>manual</connectionMode>
  <MSM><security>
    <authEncryption>
      <authentication>WPA2PSK</authentication>
      <encryption>AES</encryption>
      <useOneX>false</useOneX>
    </authEncryption>
    <sharedKey>
      <keyType>passPhrase</keyType>
      <protected>false</protected>
      <keyMaterial>{password}</keyMaterial>
    </sharedKey>
  </security></MSM>
</WLANProfile>
"""


def _netsh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["netsh", *args], capture_output=True, text=True, timeout=30
    )


def current_wifi_ssid() -> str | None:
    """Return the SSID of the currently-connected Wi-Fi network, if any."""
    if platform.system() != "Windows":
        return None
    out = _netsh("wlan", "show", "interfaces").stdout
    for line in out.splitlines():
        s = line.strip()
        # Avoid matching "BSSID"; require a leading "SSID"
        if s.lower().startswith("ssid") and "bssid" not in s.lower():
            _, _, val = s.partition(":")
            val = val.strip()
            if val:
                return val
    return None


def connect_wifi_direct(
    ssid: str, password: str | None = None, *, config: OPPConfig | None = None
) -> dict[str, Any]:
    """Connect to a printer's Wi-Fi Direct SoftAP (Windows only).

    Adds a saved WLAN profile (so future connects need no password) and connects.
    Remembers the password in the OPP config. Returns the previously-connected
    SSID under ``previous_ssid`` so the caller can restore it afterwards.
    """
    if platform.system() != "Windows":
        raise RuntimeError("connect_wifi_direct is only implemented on Windows.")
    cfg = config or OPPConfig()
    password = password or cfg.wifi_password(ssid)
    if not password:
        raise RuntimeError(
            f"No password for '{ssid}'. Provide it once; it will be remembered."
        )

    previous = current_wifi_ssid()

    profile_xml = _WLAN_PROFILE_TEMPLATE.format(ssid=ssid, password=password)
    tmp = Path(tempfile.gettempdir()) / "openprint" / "wd_profile.xml"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(profile_xml, "utf-8")
    add = _netsh("wlan", "add", "profile", f"filename={tmp}", "user=current")
    conn = _netsh("wlan", "connect", f"name={ssid}", f"ssid={ssid}")
    try:
        tmp.unlink()
    except OSError:
        pass

    cfg.remember_wifi(ssid, password)

    # Give the association + DHCP a moment.
    time.sleep(6)
    now = current_wifi_ssid()
    ok = (now or "").strip() == ssid.strip()
    return {
        "connected": ok,
        "ssid": ssid,
        "current_ssid": now,
        "previous_ssid": previous,
        "add_profile": add.stdout.strip() or add.stderr.strip(),
        "connect": conn.stdout.strip() or conn.stderr.strip(),
    }


def restore_wifi(ssid: str | None) -> dict[str, Any]:
    """Reconnect to a previously-used Wi-Fi network (Windows only)."""
    if platform.system() != "Windows":
        raise RuntimeError("restore_wifi is only implemented on Windows.")
    if not ssid:
        return {"restored": False, "reason": "no previous ssid given"}
    conn = _netsh("wlan", "connect", f"name={ssid}", f"ssid={ssid}")
    time.sleep(5)
    now = current_wifi_ssid()
    return {
        "restored": (now or "").strip() == ssid.strip(),
        "ssid": ssid,
        "current_ssid": now,
        "output": conn.stdout.strip() or conn.stderr.strip(),
    }


# --------------------------------------------------------------------------- #
# Bridge management
# --------------------------------------------------------------------------- #


def bridge_running(url: str = "http://127.0.0.1:631") -> bool:
    """Return True if an OPP server/bridge answers at *url*."""
    try:
        with httpx.Client(timeout=2.0) as http:
            resp = http.get(f"{url}/opp/v1/printer")
            return resp.status_code < 500
    except Exception:
        return False


def ensure_bridge(url: str = "http://127.0.0.1:631") -> dict[str, Any]:
    """Best-effort: make sure an `opp bridge` is reachable.

    If one is already answering, returns immediately. Otherwise spawns
    ``opp bridge`` in the background and waits briefly for it to come up.
    Bridging relies on CUPS, so on Windows this will usually report that no
    CUPS-backed printers are available — surfaced as a clear message rather
    than a crash.
    """
    if bridge_running(url):
        return {"running": True, "started": False, "url": url}

    opp = shutil.which("opp")
    if not opp:
        # Fall back to the module entry point in the current interpreter.
        import sys

        cmd = [sys.executable, "-m", "openprint.cli", "bridge"]
    else:
        cmd = [opp, "bridge"]

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return {"running": False, "started": False, "error": str(exc), "url": url}

    for _ in range(10):
        time.sleep(1)
        if bridge_running(url):
            return {"running": True, "started": True, "url": url}

    return {
        "running": False,
        "started": True,
        "url": url,
        "note": (
            "Started 'opp bridge' but it isn't answering yet. On Windows the "
            "bridge needs CUPS printers, which usually aren't present; prefer a "
            "direct IPP printer URL instead."
        ),
    }
