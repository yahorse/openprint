"""Inspect printer format support and understand OpenPrint's fallback chain.

OpenPrint automatically negotiates the best document format for each
printer.  This example shows:

  1. How to query a printer's supported formats via the API.
  2. The automatic fallback chain OpenPrint follows.
  3. How to install optional PDF conversion support.

Usage:
    python examples/format_negotiation.py [printer-id]

    # To enable PDF -> raster conversion install the pdf extra:
    pip install "openprint[pdf]"
"""

import json
import sys
import urllib.request

BASE_URL = "http://localhost:631"  # change to your OPP server

# OpenPrint's automatic format fallback chain (highest preference first).
# When the source file is PDF, OpenPrint tries each format in order and
# uses the first one the printer claims to support.
FALLBACK_CHAIN = [
    ("application/pdf",        "PDF — sent as-is (fastest, best quality)"),
    ("application/octet-stream", "octet-stream — raw passthrough"),
    ("image/jpeg",             "JPEG — converted page-by-page (requires [pdf] extra)"),
    ("image/pwg-raster",       "PWG Raster — universal raster format (requires [pdf] extra)"),
]


def _get(url: str) -> object:
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def show_formats(printer: dict) -> None:
    printer_id = printer["id"]
    name = printer["name"]

    print(f"Printer : {name}  (id={printer_id})")
    print(f"Host    : {printer['host']}:{printer['port']}")
    print()

    try:
        formats: list[str] = _get(f"{BASE_URL}/opp/v1/printers/{printer_id}/formats")  # type: ignore[assignment]
    except Exception as exc:
        print(f"Could not fetch formats: {exc}")
        return

    if not formats:
        print("  Printer reported no supported formats.")
        return

    print("Supported document formats reported by printer:")
    for fmt in formats:
        print(f"  {fmt}")
    print()

    print("OpenPrint fallback chain — first match wins:")
    chosen: str | None = None
    for mime, description in FALLBACK_CHAIN:
        supported = mime in formats
        marker = ">" if (supported and chosen is None) else " "
        tick = "YES" if supported else "no "
        if supported and chosen is None:
            chosen = mime
        print(f"  {marker} [{tick}]  {mime}")
        print(f"          {description}")
    print()

    if chosen:
        print(f"OpenPrint will use: {chosen}")
    else:
        print("WARNING: no preferred format matched — OpenPrint will use octet-stream fallback.")

    print()
    needs_pdf_extra = chosen in {"image/jpeg", "image/pwg-raster"}
    if needs_pdf_extra:
        print("This printer requires raster conversion.  Install the PDF extra:")
        print('    pip install "openprint[pdf]"')
    else:
        print("No extra dependencies needed for this printer.")


def main() -> None:
    printers: list[dict] = _get(f"{BASE_URL}/opp/v1/printers")  # type: ignore[assignment]

    if not printers:
        print("No printers found. Is the OPP server running?")
        sys.exit(1)

    if len(sys.argv) >= 2:
        printer_id = sys.argv[1]
        match = next((p for p in printers if p["id"] == printer_id), None)
        if match is None:
            print(f"Printer '{printer_id}' not found.")
            sys.exit(1)
        targets = [match]
    else:
        targets = printers

    for printer in targets:
        show_formats(printer)
        print("-" * 60)
        print()


if __name__ == "__main__":
    main()
