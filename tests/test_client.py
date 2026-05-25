import pytest

from openprint.client import Client


def test_client_no_url():
    client = Client()
    with pytest.raises(RuntimeError, match="No base_url"):
        client.printer_info()


def test_client_with_url():
    client = Client(base_url="http://localhost:631")
    assert client.base_url == "http://localhost:631"


def test_client_auth_header():
    client = Client(auth_token="secret")
    headers = client._headers()
    assert headers["Authorization"] == "Bearer secret"


def test_client_no_auth_header():
    client = Client()
    headers = client._headers()
    assert "Authorization" not in headers


def test_client_url_building():
    client = Client(base_url="http://printer.local:631")
    assert client._url("/printer") == "http://printer.local:631/opp/v1/printer"
    assert client._url("/jobs") == "http://printer.local:631/opp/v1/jobs"
