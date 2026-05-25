from __future__ import annotations

from openprint.errors import InvalidPDF

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
    count = data.count(b"/Type /Page")
    pages_tree_count = data.count(b"/Type /Pages")
    return max(count - pages_tree_count, 1)


def parse_page_range(pages: str, total: int) -> list[int]:
    """Parse a page range string like '1-3,5' into a list of page numbers."""
    if pages == "all":
        return list(range(1, total + 1))

    result: list[int] = []
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = max(1, int(start_s))
            end = min(total, int(end_s))
            result.extend(range(start, end + 1))
        else:
            page = int(part)
            if 1 <= page <= total:
                result.append(page)
    if not result:
        raise InvalidPDF(f"Page range '{pages}' selects no valid pages.")
    return sorted(set(result))
