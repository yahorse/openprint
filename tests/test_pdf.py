import pytest

from openprint.errors import InvalidPDF
from openprint.pdf import parse_page_range, validate_pdf
from tests.conftest import MINIMAL_PDF, MULTI_PAGE_PDF


def test_validate_valid_pdf():
    count = validate_pdf(MINIMAL_PDF)
    assert count >= 1


def test_validate_multi_page_pdf():
    count = validate_pdf(MULTI_PAGE_PDF)
    assert count == 3


def test_validate_not_pdf():
    with pytest.raises(InvalidPDF, match="not a valid PDF"):
        validate_pdf(b"this is not a pdf")


def test_validate_too_small():
    with pytest.raises(InvalidPDF, match="too small"):
        validate_pdf(b"%PDF-1.4\nshort")


def test_parse_page_range_all():
    pages = parse_page_range("all", 5)
    assert pages == [1, 2, 3, 4, 5]


def test_parse_page_range_single():
    pages = parse_page_range("3", 5)
    assert pages == [3]


def test_parse_page_range_range():
    pages = parse_page_range("2-4", 5)
    assert pages == [2, 3, 4]


def test_parse_page_range_complex():
    pages = parse_page_range("1-2,4", 5)
    assert pages == [1, 2, 4]


def test_parse_page_range_out_of_bounds():
    pages = parse_page_range("1-10", 3)
    assert pages == [1, 2, 3]


def test_parse_page_range_invalid():
    with pytest.raises(InvalidPDF, match="no valid pages"):
        parse_page_range("10-20", 3)
