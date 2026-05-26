from __future__ import annotations

import io
import logging
import struct
from typing import Any

import httpx

from openprint.backend import PrintBackend
from openprint.models import Capabilities, Job, PrinterState, SupplyLevels

logger = logging.getLogger("openprint.ipp")

# Formats the backend can send natively (no conversion needed)
_NATIVE_FORMATS = {"application/pdf", "application/octet-stream"}
# Fallback JPEG DPI when PDF conversion is required
_JPEG_DPI = 150


def _pdf_to_jpeg(pdf_data: bytes, page_index: int = 0, dpi: int = _JPEG_DPI) -> bytes:
    """Render a single page of a PDF to a JPEG byte string."""
    try:
        import fitz  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "pymupdf is required to print to printers that do not support PDF. "
            "Install it with: pip install pymupdf"
        ) from exc

    doc = fitz.open(stream=pdf_data, filetype="pdf")
    page = doc[page_index]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    from PIL import Image  # type: ignore[import]
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    buf = io.BytesIO()
    img.save(buf, "JPEG", dpi=(dpi, dpi), quality=85)
    return buf.getvalue()


def _pdf_to_jpeg_pages(
    pdf_data: bytes, page_indices: list[int], dpi: int = _JPEG_DPI
) -> list[bytes]:
    """Render a list of page indices from a PDF to individual JPEG byte strings."""
    try:
        import fitz  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "pymupdf is required to print to printers that do not support PDF. "
            "Install it with: pip install pymupdf"
        ) from exc

    from PIL import Image  # type: ignore[import]

    doc = fitz.open(stream=pdf_data, filetype="pdf")
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    results: list[bytes] = []
    for idx in page_indices:
        page = doc[idx]
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        buf = io.BytesIO()
        img.save(buf, "JPEG", dpi=(dpi, dpi), quality=85)
        results.append(buf.getvalue())
    return results


def _pdf_to_pwg_raster(pdf_data: bytes, page_indices: list[int], dpi: int = _JPEG_DPI) -> bytes:
    """Convert selected PDF pages to a PWG Raster stream (RaS2, uncompressed RGB)."""
    try:
        import fitz  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "pymupdf is required for PWG Raster conversion. "
            "Install it with: pip install pymupdf"
        ) from exc

    doc = fitz.open(stream=pdf_data, filetype="pdf")
    mat = fitz.Matrix(dpi / 72, dpi / 72)

    # 32-byte file header: magic + zeros
    file_header = b"RaS2" + b"\x00" * 28

    buf = io.BytesIO()
    buf.write(file_header)

    for idx in page_indices:
        page = doc[idx]
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        width = pix.width
        height = pix.height
        pixel_data = bytes(pix.samples)  # raw RGB, width*height*3 bytes

        # Build 1796-byte page header
        ph = bytearray(1796)

        # bytes 0-63: MediaColor
        mc = b"Plain\x00"
        ph[0:len(mc)] = mc

        # bytes 64-127: MediaType
        mt = b"stationery\x00"
        ph[64:64 + len(mt)] = mt

        # bytes 128-191: OutputType (zeros — already zero)
        # bytes 192-255: AdvanceDistance (zeros)

        def set_u32(offset: int, value: int) -> None:
            struct.pack_into(">I", ph, offset, value)

        set_u32(256, 0)   # AdvanceMedia
        set_u32(260, 0)   # Collate
        set_u32(264, 0)   # CutMedia
        set_u32(268, 0)   # Duplex
        set_u32(272, dpi) # HWResolutionX
        set_u32(276, dpi) # HWResolutionY
        # ImagingBoundingBox: 4 x uint32 at 280
        for i in range(4):
            set_u32(280 + i * 4, 0)
        set_u32(296, 0)   # InsertSheet
        set_u32(300, 0)   # Jog
        set_u32(304, 0)   # LeadingEdge
        set_u32(308, 0)   # Margins[0]
        set_u32(312, 0)   # Margins[1]
        set_u32(316, 0)   # ManualFeed
        set_u32(320, 0)   # MediaPosition
        set_u32(324, 0)   # MediaWeight
        set_u32(328, 0)   # MirrorPrint
        set_u32(332, 0)   # NegativePrint
        set_u32(336, 1)   # NumCopies
        set_u32(340, 0)   # Orientation
        set_u32(344, 0)   # OutputFaceUp
        set_u32(348, width)   # PageSizeX (pixels)
        set_u32(352, height)  # PageSizeY (pixels)
        set_u32(356, 0)   # Separations
        set_u32(360, 0)   # TraySwitch
        set_u32(364, 0)   # Tumble
        set_u32(368, width)           # cupsWidth
        set_u32(372, height)          # cupsHeight
        set_u32(376, 0)               # cupsMediaType
        set_u32(380, 8)               # cupsBitsPerColor
        set_u32(384, 24)              # cupsBitsPerPixel
        set_u32(388, width * 3)       # cupsBytesPerLine
        set_u32(392, 0)               # cupsColorOrder (chunky)
        set_u32(396, 1)               # cupsColorSpace (RGB)
        set_u32(400, 0)               # cupsCompression (none)
        set_u32(404, 0)               # cupsRowCount
        set_u32(408, 0)               # cupsRowFeed
        set_u32(412, 0)               # cupsRowStep
        set_u32(416, 3)               # cupsNumColors
        # remaining bytes already zero

        buf.write(bytes(ph))
        buf.write(pixel_data)

    return buf.getvalue()


# IPP operation codes
OP_PRINT_JOB = 0x0002
OP_CREATE_JOB = 0x0005
OP_SEND_DOCUMENT = 0x0006
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
    doc_format: str = "application/octet-stream",
    last_document: bool | None = None,
) -> bytes:
    header = struct.pack(">HHI", 0x0200, operation, request_id)

    body = struct.pack(">B", TAG_OPERATION)
    body += _encode_string_attr(TAG_CHARSET, "attributes-charset", "utf-8")
    body += _encode_string_attr(TAG_NATURAL_LANG, "attributes-natural-language", "en")
    body += _encode_string_attr(TAG_URI, "printer-uri", printer_uri)

    if document is not None:
        body += _encode_string_attr(TAG_MIME, "document-format", doc_format)

    if job_id is not None:
        body += _encode_int_attr(TAG_INTEGER, "job-id", job_id)

    if last_document is not None:
        body += _encode_bool_attr("last-document", last_document)

    if attributes:
        body += struct.pack(">B", TAG_JOB)
        for attr in attributes:
            body += attr

    body += struct.pack(">B", TAG_END)

    result = header + body
    if document:
        result += document
    return result


def _build_send_document_request(
    request_id: int,
    printer_uri: str,
    job_id: int,
    document: bytes,
    doc_format: str,
    last_document: bool,
) -> bytes:
    """Build an IPP Send-Document request for one page/document of a multi-doc job."""
    header = struct.pack(">HHI", 0x0200, OP_SEND_DOCUMENT, request_id)

    body = struct.pack(">B", TAG_OPERATION)
    body += _encode_string_attr(TAG_CHARSET, "attributes-charset", "utf-8")
    body += _encode_string_attr(TAG_NATURAL_LANG, "attributes-natural-language", "en")
    body += _encode_string_attr(TAG_URI, "printer-uri", printer_uri)
    body += _encode_int_attr(TAG_INTEGER, "job-id", job_id)
    body += _encode_string_attr(TAG_MIME, "document-format", doc_format)
    body += _encode_bool_attr("last-document", last_document)
    body += struct.pack(">B", TAG_END)

    return header + body + document


def _parse_ipp_response(data: bytes) -> dict[str, Any]:
    if len(data) < 8:
        return {"status": -1, "attributes": {}}

    status = struct.unpack(">H", data[2:4])[0]

    attrs: dict[str, Any] = {}
    pos = 8
    current_name = ""

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
            value: Any = raw_value.decode("utf-8", errors="replace")
        elif tag == TAG_INTEGER and val_len == 4:
            value = struct.unpack(">i", raw_value)[0]
        elif tag == TAG_ENUM and val_len == 4:
            value = struct.unpack(">I", raw_value)[0]
        elif tag == TAG_BOOLEAN and val_len == 1:
            value = bool(raw_value[0])
        else:
            value = raw_value

        # Multi-value attributes: continuation values have empty name
        attr_name = name if name else current_name
        if not attr_name:
            continue
        if name:
            current_name = name
        if attr_name in attrs:
            existing = attrs[attr_name]
            if isinstance(existing, list):
                existing.append(value)
            else:
                attrs[attr_name] = [existing, value]
        else:
            attrs[attr_name] = value

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
        # Build both HTTP and HTTPS candidate URLs; try HTTP first — many consumer
        # printers (e.g. HP DeskJet) advertise ipps:// but their TLS stack drops
        # large request bodies, while plain HTTP on port 631 works reliably.
        http_url = uri.replace("ipp://", "http://").replace("ipps://", "http://")
        https_url = uri.replace("ipp://", "https://").replace("ipps://", "https://")
        if tls:
            self._http_urls = [http_url, https_url]
        else:
            self._http_urls = [http_url]
        self._http_url = self._http_urls[0]
        self._request_id = 1
        self._ipp_job_ids: dict[str, int] = {}
        self._supported_formats: list[str] | None = None

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send_ipp(
        self, data: bytes, content_type: str = "application/ipp"
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for url in self._http_urls:
            try:
                async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                    resp = await client.post(
                        url,
                        content=data,
                        headers={"Content-Type": content_type},
                    )
                    # Remember the working URL for future requests
                    self._http_url = url
                    self._http_urls = [url]
                    return _parse_ipp_response(resp.content)
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.debug("IPP url %s failed (%s), trying next", url, exc)
        raise RuntimeError(f"All IPP URLs failed: {last_exc}")

    async def _get_supported_formats(self) -> list[str]:
        """Query the printer for its supported document formats."""
        request = _build_ipp_request(
            OP_GET_PRINTER_ATTRIBUTES,
            self._next_request_id(),
            self._uri,
        )
        try:
            result = await self._send_ipp(request)
            raw = result["attributes"].get("document-format-supported", [])
            if isinstance(raw, str):
                return [raw]
            return list(raw) if isinstance(raw, list) else []
        except Exception:
            return []

    def _build_job_attrs(self, job: Job) -> list[bytes]:
        """Build common IPP job-attribute bytes from a Job model."""
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

        return attrs

    async def _send_multipage_image_job(
        self,
        job: Job,
        page_data_list: list[bytes],
        doc_format: str,
    ) -> dict[str, Any]:
        """Send a multi-document IPP job using Create-Job + Send-Document.

        Returns the result of the final Send-Document call.
        """
        attrs = self._build_job_attrs(job)

        # Step 1: Create-Job (no document data, just job attributes)
        create_request = _build_ipp_request(
            OP_CREATE_JOB,
            self._next_request_id(),
            self._uri,
            attributes=attrs,
        )
        create_result = await self._send_ipp(create_request)
        if create_result["status"] > 0x00FF:
            raise RuntimeError(
                f"IPP Create-Job failed: status 0x{create_result['status']:04x}"
            )
        ipp_job_id: int = create_result["attributes"].get("job-id", 0)
        if not ipp_job_id:
            raise RuntimeError("IPP Create-Job did not return a job-id")

        # Step 2: Send-Document for each page
        last_result: dict[str, Any] = {}
        for i, page_data in enumerate(page_data_list):
            is_last = i == len(page_data_list) - 1
            send_request = _build_send_document_request(
                request_id=self._next_request_id(),
                printer_uri=self._uri,
                job_id=ipp_job_id,
                document=page_data,
                doc_format=doc_format,
                last_document=is_last,
            )
            last_result = await self._send_ipp(send_request)
            if last_result["status"] > 0x00FF:
                raise RuntimeError(
                    f"IPP Send-Document (page {i}) failed: "
                    f"status 0x{last_result['status']:04x}"
                )

        return last_result

    async def print_job(self, job: Job, pdf_data: bytes) -> None:
        # Detect supported formats on first use
        if self._supported_formats is None:
            self._supported_formats = await self._get_supported_formats()

        formats = self._supported_formats
        format_set = set(formats) if formats else set()

        # Determine which page indices to render
        if job.pages_total and job.pages_total > 0:
            page_indices = list(range(job.pages_total))
        else:
            # Discover page count from the PDF itself (if fitz is available)
            try:
                import fitz  # type: ignore[import]
                doc = fitz.open(stream=pdf_data, filetype="pdf")
                page_indices = list(range(len(doc)))
            except Exception:
                page_indices = [0]

        # ------------------------------------------------------------------ #
        # Format fallback chain:                                              #
        #   1. application/pdf           — native, preferred                  #
        #   2. application/octet-stream  — native fallback (always try)       #
        #   3. image/jpeg                — rasterise via PyMuPDF              #
        #   4. image/pwg-raster          — rasterise to PWG Raster            #
        # ------------------------------------------------------------------ #

        if not formats or "application/pdf" in format_set:
            # Native PDF path — single Print-Job request
            doc_format = "application/pdf" if "application/pdf" in format_set else "application/octet-stream"
            attrs = self._build_job_attrs(job)
            request = _build_ipp_request(
                OP_PRINT_JOB,
                self._next_request_id(),
                self._uri,
                attributes=attrs,
                document=pdf_data,
                doc_format=doc_format,
            )
            result = await self._send_ipp(request)
            if result["status"] > 0x00FF:
                raise RuntimeError(f"IPP print failed: status 0x{result['status']:04x}")
            ipp_job_id = result["attributes"].get("job-id")
            if ipp_job_id:
                self._ipp_job_ids[job.id] = ipp_job_id
            logger.info("Sent PDF to IPP printer %s, job-id: %s", self._uri, ipp_job_id)
            return

        if "application/octet-stream" in format_set:
            # octet-stream fallback — single Print-Job request
            attrs = self._build_job_attrs(job)
            request = _build_ipp_request(
                OP_PRINT_JOB,
                self._next_request_id(),
                self._uri,
                attributes=attrs,
                document=pdf_data,
                doc_format="application/octet-stream",
            )
            result = await self._send_ipp(request)
            if result["status"] > 0x00FF:
                raise RuntimeError(f"IPP print failed: status 0x{result['status']:04x}")
            ipp_job_id = result["attributes"].get("job-id")
            if ipp_job_id:
                self._ipp_job_ids[job.id] = ipp_job_id
            logger.info(
                "Sent octet-stream to IPP printer %s, job-id: %s", self._uri, ipp_job_id
            )
            return

        if "image/jpeg" in format_set:
            logger.info(
                "Printer doesn't support PDF; converting %d page(s) to JPEG", len(page_indices)
            )
            if len(page_indices) == 1:
                # Single page — use the simpler Print-Job path
                jpeg_data = _pdf_to_jpeg(pdf_data, page_index=page_indices[0])
                attrs = self._build_job_attrs(job)
                request = _build_ipp_request(
                    OP_PRINT_JOB,
                    self._next_request_id(),
                    self._uri,
                    attributes=attrs,
                    document=jpeg_data,
                    doc_format="image/jpeg",
                )
                result = await self._send_ipp(request)
                if result["status"] > 0x00FF:
                    raise RuntimeError(f"IPP print failed: status 0x{result['status']:04x}")
                ipp_job_id = result["attributes"].get("job-id")
                if ipp_job_id:
                    self._ipp_job_ids[job.id] = ipp_job_id
                logger.info(
                    "Sent JPEG to IPP printer %s, job-id: %s", self._uri, ipp_job_id
                )
            else:
                # Multi-page — use Create-Job + Send-Document
                jpeg_pages = _pdf_to_jpeg_pages(pdf_data, page_indices)
                result = await self._send_multipage_image_job(job, jpeg_pages, "image/jpeg")
                ipp_job_id = result["attributes"].get("job-id")
                if ipp_job_id:
                    self._ipp_job_ids[job.id] = ipp_job_id
                logger.info(
                    "Sent %d JPEG pages to IPP printer %s", len(jpeg_pages), self._uri
                )
            return

        if "image/pwg-raster" in format_set:
            logger.info(
                "Printer doesn't support PDF or JPEG; converting %d page(s) to PWG Raster",
                len(page_indices),
            )
            pwg_data = _pdf_to_pwg_raster(pdf_data, page_indices)
            if len(page_indices) == 1:
                attrs = self._build_job_attrs(job)
                request = _build_ipp_request(
                    OP_PRINT_JOB,
                    self._next_request_id(),
                    self._uri,
                    attributes=attrs,
                    document=pwg_data,
                    doc_format="image/pwg-raster",
                )
                result = await self._send_ipp(request)
                if result["status"] > 0x00FF:
                    raise RuntimeError(f"IPP print failed: status 0x{result['status']:04x}")
                ipp_job_id = result["attributes"].get("job-id")
                if ipp_job_id:
                    self._ipp_job_ids[job.id] = ipp_job_id
                logger.info(
                    "Sent PWG Raster to IPP printer %s, job-id: %s", self._uri, ipp_job_id
                )
            else:
                # PWG Raster is already a single stream with all pages; send as one document
                # but use Create-Job/Send-Document to be consistent with multi-page flow
                result = await self._send_multipage_image_job(
                    job, [pwg_data], "image/pwg-raster"
                )
                ipp_job_id = result["attributes"].get("job-id")
                if ipp_job_id:
                    self._ipp_job_ids[job.id] = ipp_job_id
                logger.info(
                    "Sent PWG Raster (%d pages) to IPP printer %s", len(page_indices), self._uri
                )
            return

        # Last resort: send the PDF raw and hope for the best
        logger.warning(
            "No known format match for printer formats %s; sending PDF as octet-stream", formats
        )
        attrs = self._build_job_attrs(job)
        request = _build_ipp_request(
            OP_PRINT_JOB,
            self._next_request_id(),
            self._uri,
            attributes=attrs,
            document=pdf_data,
            doc_format="application/octet-stream",
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
