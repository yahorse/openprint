import subprocess
import tempfile
from pathlib import Path

from openprint.tls import create_ssl_context, generate_self_signed_cert


def test_generate_cert_creates_files():
    with tempfile.TemporaryDirectory() as tmp:
        cert = Path(tmp) / "test.crt"
        key = Path(tmp) / "test.key"

        # Only test if openssl is available
        result = subprocess.run(["which", "openssl"], capture_output=True)
        if result.returncode != 0:
            return

        c, k = generate_self_signed_cert(cert, key, hostname="test.local")
        assert c.exists()
        assert k.exists()
        assert c == cert
        assert k == key


def test_generate_cert_reuses_existing():
    with tempfile.TemporaryDirectory() as tmp:
        cert = Path(tmp) / "test.crt"
        key = Path(tmp) / "test.key"
        cert.write_text("existing cert")
        key.write_text("existing key")

        c, k = generate_self_signed_cert(cert, key)
        assert c.read_text() == "existing cert"


def test_create_ssl_context_no_auto():
    ctx = create_ssl_context(auto_generate=False)
    assert ctx is None
