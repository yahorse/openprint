from __future__ import annotations

import logging
import struct
from typing import Any

import httpx

from openprint.backend import PrintBackend
from openprint.models import Capabilities, Job, PrinterState, SupplyLevels

logger = logging.getLogger("openprint.ipp")

# IPP operation codes
OP_PRINT_JOB = 0x0002
OP_CANCEL_JOB = 0x0008
OP_GET_PRINTER_ATTRIBUTES = 0x000B
OP_GET_JOBS = 0x000A

# IPP tags
TAG_OPERATION = 0x01
TAG_JOB = 0x02
TAG_END = 0x03
TAG_CHARSET = 0x47
TAG_NATURAL_LANG = 0x48
TAG_URI = 0x45
TAG_NAME = 0x42
TAG_KEYWORD = 0x44
TAG_INTEGER = 0x21
TAG_ENUM = 0x23
TAG_BOOLEAN = 0x22
TAG_MIME = 0x49


def _encode_attribute(tag: int, name: str, value: bytes) -> bytes:
    name_bytes = name.encode()
    return (
        struct.pack(">BH", tag, len(name_bytes))
        + name_bytes
        + struct.pack(">H", len(value))
        + value
    )


def _encode_string_attr(tag: int, name: str, value: str) -> bytes:
    return _encode_attribute(tag, name, value.encode())


def _encode_int_attr(tag: int, name: str, value: int) -> bytes:
    return _encode_attribute(tag, name, struct.pack(">i", value))


def _encode_bool_attr(name: str, value: bool) -> bytes:
    return _encode_attribute(TAG_BOOLEAN, name, struct.pack(">B", int(value)))


def _build_ipp_request(
    operation: int,
    request_id: int,
    printer_uri: str,
    attributes: list[bytes] | None = None,
    job_id: int | None = None,
    document: bytes | None = None,
) -> bytes:
    header = struct.pack(">HHI", 0x0200, operation, request_id)

    body = struct.pack(">B", TAG_OPERATION)
    body += _encode_string_attr(TAG_CHARSET, "attributes-charset", "utf-8")
    body += _encode_string_attr(TAG_NATURAL_LANG, "attributes-natural-language", "en")
    body += _encode_string_attr(TAG_URI, "printer-uri", printer_uri)

    if document is not None:
        body += _encode_string_attr(TAG_MIME, "document-format", "application/pdf")

    if job_id is not None:
        body += _encode_int_attr(TAG_INTEGER, "job-id", job_id)

    if attributes:
        body += struct.pack(">B", TAG_JOB)
        for attr in attributes:
            body += attr

    body += struct.pack(">B", TAG_END)

    result = header + body
    if document:
        result += document
    return result


def _parse_ipp_response(data: bytes) -> dict[str, Any]:
    if len(data) < 8:
        return {"status": -1, "attributes": {}}

    status = struct.unpack(">H", data[2:4])[0]

    attrs: dict[str, Any] = {}
    pos = 8

    while pos < len(data):
        if data[pos] in (TAG_OPERATION, TAG_JOB, 0x04):  # 0x04 = printer attrs
            pos += 1
            continue
        if data[pos] == TAG_END:
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
        raw_value = data[pos:pos + val_len]
        pos += val_len

        if tag in (TAG_CHARSET, TAG_NATURAL_LANG, TAG_URI, TAG_NAME, TAG_KEYWORD, TAG_MIME):
            value = raw_value.decode("utf-8", errors="replace")
        elif tag == TAG_INTEGER and val_len == 4:
            value = struct.unpack(">i", raw_value)[0]
        elif tag == TAG_ENUM and val_len == 4:
            value = struct.unpack(">I", raw_value)[0]
        elif tag == TAG_BOOLEAN and val_len == 1:
            value = bool(raw_value[0])
        else:
            value = raw_value

        if name:
            if name in attrs:
                existing = attrs[name]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    attrs[name] = [existing, value]
            else:
                attrs[name] = value

    return {"status": status, "attributes": attrs}


# Maps IPP printer-state to OPP PrinterState
_IPP_STATE_MAP = {
    3: PrinterState.IDLE,
    4: PrinterState.PRINTING,
    5: PrinterState.ERROR,
}


class IPPBackend(PrintBackend):
    """Backend that prints directly to an IPP printer over HTTP.

    No CUPS needed. Speaks raw IPP protocol to the printer.
    """

    def __init__(self, uri: str, tls: bool = False) -> None:
        self._uri = uri
        self._tls = tls
        scheme = "https" if tls else "http"
        # Convert ipp:// URI to http:// for httpx
        self._http_url = uri.replace("ipp://", f"{scheme}://").replace("ipps://", "https://")
        self._request_id = 1
        self._ipp_job_ids: dict[str, int] = {}

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send_ipp(
        self, data: bytes, content_type: str = "application/ipp"
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            resp = await client.post(
                self._http_url,
                content=data,
                headers={"Content-Type": content_type},
            )
            return _parse_ipp_response(resp.content)

    async def print_job(self, job: Job, pdf_data: bytes) -> None:
        attrs: list[bytes] = []

        if job.copies > 1:
            attrs.append(_encode_int_attr(TAG_INTEGER, "copies", job.copies))

        if job.duplex.value == "long-edge":
            attrs.append(_encode_string_attr(TAG_KEYWORD, "sides", "two-sided-long-edge"))
        elif job.duplex.value == "short-edge":
            attrs.append(_encode_string_attr(TAG_KEYWORD, "sides", "two-sided-short-edge"))
        else:
            attrs.append(_encode_string_attr(TAG_KEYWORD, "sides", "one-sided"))

        if not job.color:
            attrs.append(_encode_string_attr(TAG_KEYWORD, "print-color-mode", "monochrome"))

        media_map = {
            "a4": "iso_a4_210x297mm",
            "letter": "na_letter_8.5x11in",
            "legal": "na_legal_8.5x14in",
            "a3": "iso_a3_297x420mm",
            "a5": "iso_a5_148x210mm",
        }
        ipp_media = media_map.get(job.media, job.media)
        attrs.append(_encode_string_attr(TAG_KEYWORD, "media", ipp_media))

        request = _build_ipp_request(
            OP_PRINT_JOB,
            self._next_request_id(),
            self._uri,
            attributes=attrs,
            document=pdf_data,
        )

        result = await self._send_ipp(request)

        if result["status"] > 0x00FF:
            raise RuntimeError(f"IPP print failed: status 0x{result['status']:04x}")

        ipp_job_id = result["attributes"].get("job-id")
        if ipp_job_id:
            self._ipp_job_ids[job.id] = ipp_job_id

        logger.info("Sent to IPP printer %s, job-id: %s", self._uri, ipp_job_id)

    async def cancel_job(self, job: Job) -> None:
        ipp_job_id = self._ipp_job_ids.get(job.id)
        if not ipp_job_id:
            return

        request = _build_ipp_request(
            OP_CANCEL_JOB,
            self._next_request_id(),
            self._uri,
            job_id=ipp_job_id,
        )
        await self._send_ipp(request)

    async def get_state(self) -> PrinterState:
        request = _build_ipp_request(
            OP_GET_PRINTER_ATTRIBUTES,
            self._next_request_id(),
            self._uri,
        )
        try:
            result = await self._send_ipp(request)
            state_val = result["attributes"].get("printer-state", 3)
            if isinstance(state_val, int):
                return _IPP_STATE_MAP.get(state_val, PrinterState.IDLE)
            return PrinterState.IDLE
        except Exception:
            return PrinterState.OFFLINE

    async def get_supplies(self) -> SupplyLevels:
        request = _build_ipp_request(
            OP_GET_PRINTER_ATTRIBUTES,
            self._next_request_id(),
            self._uri,
        )
        supplies = SupplyLevels()
        try:
            result = await self._send_ipp(request)
            attrs = result["attributes"]

            levels = attrs.get("marker-levels", [])
            names = attrs.get("marker-names", [])
            if not isinstance(levels, list):
                levels = [levels]
            if not isinstance(names, list):
                names = [names]

            for name, level in zip(names, levels, strict=False):
                if not isinstance(level, int):
                    continue
                name_lower = str(name).lower()
                if "black" in name_lower:
                    supplies.black = level
                elif "cyan" in name_lower:
                    supplies.cyan = level
                elif "magenta" in name_lower:
                    supplies.magenta = level
                elif "yellow" in name_lower:
                    supplies.yellow = level
        except Exception:
            pass

        return supplies

    async def get_capabilities(self) -> Capabilities:
        request = _build_ipp_request(
            OP_GET_PRINTER_ATTRIBUTES,
            self._next_request_id(),
            self._uri,
        )
        try:
            result = await self._send_ipp(request)
            attrs = result["attributes"]

            color = attrs.get("color-supported", True)
            sides = attrs.get("sides-supported", [])
            if isinstance(sides, str):
                sides = [sides]
            duplex = any("two-sided" in str(s) for s in sides) if sides else False

            return Capabilities(color=bool(color), duplex=duplex)
        except Exception:
            return Capabilities()

    async def get_printer_name(self) -> str:
        request = _build_ipp_request(
            OP_GET_PRINTER_ATTRIBUTES,
            self._next_request_id(),
            self._uri,
        )
        try:
            result = await self._send_ipp(request)
            return result["attributes"].get("printer-name", self._uri)
        except Exception:
            return self._uri
