from __future__ import annotations

import datetime
import ipaddress
import logging
import ssl
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
    """Generate a self-signed TLS certificate for the OPP server.

    Uses the ``cryptography`` library so no external ``openssl`` binary is
    required — this works identically on Linux, macOS, and Windows.
    """
    cert_path.parent.mkdir(parents=True, exist_ok=True)

    if cert_path.exists() and key_path.exists():
        logger.info("Using existing TLS cert: %s", cert_path)
        return cert_path, key_path

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    san = x509.SubjectAlternativeName(
        [
            x509.DNSName(hostname),
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(san, critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

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
