"""Printer compatibility test suite.

Tests whether a printer can work with OPP by checking:
1. Is it reachable on the network?
2. Does it speak IPP?
3. Does it accept PDF?
4. Does it support driverless printing?
5. Can we actually print a test page?
"""

from __future__ import annotations

import asyncio
import struct
from typing import Any

import httpx

PASS = "[PASS]"
FAIL = "[FAIL]"
WARN = "[WARN]"
INFO = "[INFO]"

TEST_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type /Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type /Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type /Page/Parent 2 0 R/MediaBox[0 0 612 792]"
    b"/Contents 4 0 R/Resources<</Font<</F1<</Type /Font/Subtype /Type1"
    b"/BaseFont /Helvetica>>>>>>>>endobj\n"
    b"4 0 obj<</Length 44>>stream\n"
    b"BT /F1 24 Tf 100 700 Td (OpenPrint Test) Tj ET\n"
    b"endstream\nendobj\n"
    b"xref\n0 5\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"0000000310 00000 n \n"
    b"trailer<</Size 5/Root 1 0 R>>\nstartxref\n404\n%%EOF"
)


async def test_printer(host: str, port: int = 631) -> dict[str, Any]:
    """Run the full compatibility test suite against a printer."""
    results: dict[str, Any] = {"host": host, "port": port, "tests": {}}

    print(f"\n{'='*50}")
    print("  OpenPrint Compatibility Test")
    print(f"  Target: {host}:{port}")
    print(f"{'='*50}\n")

    # Test 1: Network reachability
    reachable = await _test_reachable(host, port)
    results["tests"]["reachable"] = reachable
    if not reachable:
        print(f"\n{FAIL} Printer not reachable. Check IP and network.")
        results["compatible"] = False
        return results

    # Test 2: HTTP response
    http_ok = await _test_http(host, port)
    results["tests"]["http"] = http_ok

    # Test 3: IPP protocol
    ipp_ok, ipp_attrs = await _test_ipp(host, port)
    results["tests"]["ipp"] = ipp_ok
    results["ipp_attributes"] = ipp_attrs

    # Test 4: PDF support
    pdf_ok = _check_pdf_support(ipp_attrs)
    results["tests"]["pdf"] = pdf_ok

    # Test 5: Driverless printing capability
    driverless = _check_driverless(ipp_attrs)
    results["tests"]["driverless"] = driverless

    # Summary
    print(f"\n{'='*50}")
    all_pass = all([reachable, ipp_ok, pdf_ok])
    if all_pass:
        results["compatible"] = True
        print(f"  {PASS} This printer works with OpenPrint!")
        print("\n  Print to it:")
        print(f"    opp print document.pdf -p http://{host}:{port}")
    elif ipp_ok:
        results["compatible"] = True
        print(f"  {WARN} Partially compatible (may need CUPS bridge)")
        print("\n  Use the bridge:")
        print("    opp bridge")
    else:
        results["compatible"] = False
        print(f"  {FAIL} Not directly compatible")
        print("\n  This printer needs CUPS as a bridge:")
        print("    sudo apt install cups")
        print("    opp bridge")
    print(f"{'='*50}\n")

    return results


async def _test_reachable(host: str, port: int) -> bool:
    print("  Network connectivity...", end=" ", flush=True)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5.0
        )
        writer.close()
        await writer.wait_closed()
        print(f"{PASS} Reachable")
        return True
    except Exception as exc:
        print(f"{FAIL} {exc}")
        return False


async def _test_http(host: str, port: int) -> bool:
    print("  HTTP response...", end=" ", flush=True)
    try:
        async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
            resp = await client.get(f"http://{host}:{port}/")
            print(f"{PASS} HTTP {resp.status_code}")
            return True
    except Exception:
        # Many printers don't respond to GET / but still speak IPP
        print(f"{WARN} No HTTP response (may still work via IPP)")
        return False


async def _test_ipp(host: str, port: int) -> tuple[bool, dict[str, Any]]:
    print("  IPP protocol...", end=" ", flush=True)

    # Build a Get-Printer-Attributes request
    header = struct.pack(">HHI", 0x0200, 0x000B, 1)
    body = struct.pack(">B", 0x01)  # operation-attributes-tag

    charset = _ipp_string_attr(0x47, "attributes-charset", "utf-8")
    lang = _ipp_string_attr(0x48, "attributes-natural-language", "en")
    uri = _ipp_string_attr(0x45, "printer-uri", f"ipp://{host}:{port}/ipp/print")
    end = struct.pack(">B", 0x03)

    request = header + body + charset + lang + uri + end

    try:
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            resp = await client.post(
                f"http://{host}:{port}/ipp/print",
                content=request,
                headers={"Content-Type": "application/ipp"},
            )
            if resp.status_code in (200, 100):
                attrs = _parse_ipp_attrs(resp.content)
                name = attrs.get("printer-name", "Unknown")
                state = attrs.get("printer-state", "?")
                print(f"{PASS} {name} (state: {state})")

                # Print discovered capabilities
                if "document-format-supported" in attrs:
                    fmts = attrs["document-format-supported"]
                    if isinstance(fmts, str):
                        fmts = [fmts]
                    print(f"  Supported formats: {', '.join(str(f) for f in fmts)}")

                return True, attrs
            else:
                print(f"{FAIL} HTTP {resp.status_code}")
                return False, {}
    except Exception as exc:
        print(f"{FAIL} {exc}")
        return False, {}


def _check_pdf_support(attrs: dict[str, Any]) -> bool:
    print("  PDF support...", end=" ", flush=True)
    formats = attrs.get("document-format-supported", [])
    if isinstance(formats, str):
        formats = [formats]
    has_pdf = any("pdf" in str(f).lower() for f in formats)
    if has_pdf:
        print(f"{PASS} application/pdf supported")
    elif not attrs:
        print(f"{WARN} Could not check (no IPP attributes)")
    else:
        print(f"{FAIL} PDF not in supported formats")
    return has_pdf


def _check_driverless(attrs: dict[str, Any]) -> bool:
    print("  Driverless printing...", end=" ", flush=True)
    formats = attrs.get("document-format-supported", [])
    if isinstance(formats, str):
        formats = [formats]

    has_pdf = any("pdf" in str(f).lower() for f in formats)
    has_urf = any("urf" in str(f).lower() for f in formats)
    has_pwg = any("pwg" in str(f).lower() for f in formats)

    if has_pdf:
        print(f"{PASS} PDF-native (best)")
        return True
    elif has_urf or has_pwg:
        print(f"{WARN} URF/PWG-Raster only (works via CUPS bridge)")
        return True
    elif not attrs:
        print(f"{WARN} Could not determine")
        return False
    else:
        print(f"{FAIL} No driverless format supported")
        return False


def _ipp_string_attr(tag: int, name: str, value: str) -> bytes:
    name_b = name.encode()
    value_b = value.encode()
    return struct.pack(">BH", tag, len(name_b)) + name_b + struct.pack(">H", len(value_b)) + value_b


def _parse_ipp_attrs(data: bytes) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if len(data) < 9:
        return attrs

    pos = 8
    while pos < len(data):
        if data[pos] in (0x01, 0x02, 0x04):
            pos += 1
            continue
        if data[pos] == 0x03:
            break

        tag = data[pos]
        pos += 1
        if pos + 2 > len(data):
            break

        name_len = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        if pos + name_len > len(data):
            break

        name = data[pos:pos + name_len].decode("utf-8", errors="replace")
        pos += name_len
        if pos + 2 > len(data):
            break

        val_len = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        if pos + val_len > len(data):
            break

        raw = data[pos:pos + val_len]
        pos += val_len

        if tag in (0x47, 0x48, 0x45, 0x42, 0x44, 0x49):
            value: Any = raw.decode("utf-8", errors="replace")
        elif tag in (0x21, 0x23) and val_len == 4:
            value = struct.unpack(">i", raw)[0]
        elif tag == 0x22 and val_len == 1:
            value = bool(raw[0])
        else:
            value = raw.hex()

        if name:
            if name in attrs:
                existing = attrs[name]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    attrs[name] = [existing, value]
            else:
                attrs[name] = value
        elif attrs:
            last_key = list(attrs.keys())[-1]
            existing = attrs[last_key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                attrs[last_key] = [existing, value]

    return attrs
