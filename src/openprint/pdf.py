from __future__ import annotations

from openprint.errors import InvalidParameter, InvalidPDF

PDF_HEADER = b"%PDF-"


def validate_pdf(data: bytes) -> int:
    """Validate PDF data and return page count."""
    if not data.startswith(PDF_HEADER):
        raise InvalidPDF("The uploaded file is not a valid PDF document.")
    if len(data) < 67:
        raise InvalidPDF("The uploaded file is too small to be a valid PDF.")
    page_count = _estimate_page_count(data)
    if page_count < 1:
        raise InvalidPDF("The PDF contains no pages.")
    return page_count


def _estimate_page_count(data: bytes) -> int:
    # Count page objects, tolerating both "/Type /Page" and "/Type/Page" spacing.
    # "/Type /Page" also matches the "/Type /Pages" tree node as a prefix, so the
    # Pages-tree count is subtracted back out.
    page_objs = data.count(b"/Type /Page") + data.count(b"/Type/Page")
    pages_tree = data.count(b"/Type /Pages") + data.count(b"/Type/Pages")
    return max(page_objs - pages_tree, 1)


def _to_int(value: str, pages: str) -> int:
    try:
        return int(value)
    except ValueError as err:
        raise InvalidParameter(
            f"Invalid page range '{pages}': '{value}' is not a number."
        ) from err


def parse_page_range(pages: str, total: int) -> list[int]:
    """Parse a page range string like '1-3,5' into a list of page numbers.

    Raises :class:`InvalidParameter` (HTTP 400) on malformed input rather than
    letting a bare ``ValueError`` escape as a 500.
    """
    if pages == "all":
        return list(range(1, total + 1))

    result: list[int] = []
    for part in pages.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = max(1, _to_int(start_s.strip(), pages))
            end = min(total, _to_int(end_s.strip(), pages))
            result.extend(range(start, end + 1))
        else:
            page = _to_int(part, pages)
            if 1 <= page <= total:
                result.append(page)
    if not result:
        raise InvalidParameter(f"Page range '{pages}' selects no valid pages.")
    return sorted(set(result))
