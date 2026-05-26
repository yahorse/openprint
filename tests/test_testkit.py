from openprint.testkit import TEST_PDF, _ipp_string_attr, _parse_ipp_attrs


def test_test_pdf_is_valid():
    assert TEST_PDF.startswith(b"%PDF-")
    assert b"OpenPrint Test" in TEST_PDF
    assert b"%%EOF" in TEST_PDF


def test_ipp_string_attr():
    data = _ipp_string_attr(0x47, "attributes-charset", "utf-8")
    assert b"attributes-charset" in data
    assert b"utf-8" in data


def test_parse_empty_ipp():
    attrs = _parse_ipp_attrs(b"")
    assert attrs == {}


def test_parse_short_ipp():
    attrs = _parse_ipp_attrs(b"\x00" * 8 + b"\x03")
    assert attrs == {}
