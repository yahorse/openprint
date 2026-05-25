from __future__ import annotations

import logging
import ssl
import subprocess
from pathlib import Path

logger = logging.getLogger("openprint.tls")

DEFAULT_CERT_DIR = Path.home() / ".openprint" / "certs"
DEFAULT_CERT_FILE = DEFAULT_CERT_DIR / "server.crt"
DEFAULT_KEY_FILE = DEFAULT_CERT_DIR / "server.key"


def generate_self_signed_cert(
    cert_path: Path = DEFAULT_CERT_FILE,
    key_path: Path = DEFAULT_KEY_FILE,
    hostname: str = "openprint.local",
) -> tuple[Path, Path]:
    """Generate a self-signed TLS certificate for the OPP server."""
    cert_path.parent.mkdir(parents=True, exist_ok=True)

    if cert_path.exists() and key_path.exists():
        logger.info("Using existing TLS cert: %s", cert_path)
        return cert_path, key_path

    result = subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_path),
            "-out", str(cert_path),
            "-days", "3650",
            "-nodes",
            "-subj", f"/CN={hostname}",
            "-addext", f"subjectAltName=DNS:{hostname},DNS:localhost,IP:127.0.0.1",
        ],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Failed to generate TLS cert: {result.stderr}")

    logger.info("Generated self-signed TLS cert: %s", cert_path)
    return cert_path, key_path


def create_ssl_context(
    cert_path: Path | str | None = None,
    key_path: Path | str | None = None,
    auto_generate: bool = True,
) -> ssl.SSLContext | None:
    """Create an SSL context for the server."""
    if cert_path and key_path:
        cert = Path(cert_path)
        key = Path(key_path)
    elif auto_generate:
        cert, key = generate_self_signed_cert()
    else:
        return None

    if not cert.exists() or not key.exists():
        if auto_generate:
            cert, key = generate_self_signed_cert(cert, key)
        else:
            return None

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx
