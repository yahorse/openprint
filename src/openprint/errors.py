from __future__ import annotations

from typing import Any


class OPPError(Exception):
    code: str = "internal_error"
    status_code: int = 500

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


class InvalidPDF(OPPError):
    code = "invalid_pdf"
    status_code = 400


class InvalidParameter(OPPError):
    code = "invalid_parameter"
    status_code = 400


class Unauthorized(OPPError):
    code = "unauthorized"
    status_code = 401


class NotFound(OPPError):
    code = "not_found"
    status_code = 404


class FileTooLarge(OPPError):
    code = "file_too_large"
    status_code = 413


class PrinterUnavailable(OPPError):
    code = "printer_unavailable"
    status_code = 503
