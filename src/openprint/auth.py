from __future__ import annotations

import hmac

from fastapi import Request

from openprint.errors import Unauthorized


def verify_auth(request: Request, token: str | None) -> None:
    """Verify Bearer token if auth is enabled."""
    if token is None:
        return

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise Unauthorized("Missing or invalid Authorization header.")

    provided = auth_header[7:]
    if not hmac.compare_digest(provided, token):
        raise Unauthorized("Invalid authentication token.")
