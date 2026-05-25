from openprint.backends.ipp import (
    IPPBackend,
    _encode_string_attr,
    _encode_int_attr,
    _encode_bool_attr,
    _parse_ipp_response,
    TAG_CHARSET,
    TAG_INTEGER,
    TAG_END,
)


def test_ipp_backend_init():
    backend = IPPBackend(uri="ipp://printer.local:631/ipp/print")
    assert backend._uri == "ipp://printer.local:631/ipp/print"
    assert backend._http_url == "http://printer.local:631/ipp/print"


def test_ipp_backend_tls():
    backend = IPPBackend(uri="ipps://printer.local:631/ipp/print", tls=True)
    assert backend._http_url == "https://printer.local:631/ipp/print"


def test_encode_string_attr():
    data = _encode_string_attr(TAG_CHARSET, "attributes-charset", "utf-8")
    assert b"attributes-charset" in data
    assert b"utf-8" in data


def test_encode_int_attr():
    data = _encode_int_attr(TAG_INTEGER, "copies", 5)
    assert b"copies" in data
    assert len(data) > 0


def test_parse_empty_response():
    result = _parse_ipp_response(b"\x00" * 4)
    assert result["status"] == -1


def test_parse_minimal_response():
    import struct
    header = struct.pack(">HHI", 0x0200, 0x0000, 1)
    body = struct.pack(">B", TAG_END)
    result = _parse_ipp_response(header + body)
    assert result["status"] == 0
