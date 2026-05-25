from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from openprint.discovery import PrinterScanner
from openprint.models import DuplexMode


class Client:
    """OPP client for discovering printers and submitting print jobs."""

    def __init__(
        self,
        base_url: str | None = None,
        auth_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url
        self.auth_token = auth_token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _url(self, path: str) -> str:
        if not self.base_url:
            raise RuntimeError(
                "No base_url set. Call discover() first or provide a base_url."
            )
        return f"{self.base_url}/opp/v1{path}"

    def discover(self, timeout: float = 3.0) -> list[dict[str, Any]]:
        scanner = PrinterScanner()
        printers = asyncio.run(scanner.scan(timeout=timeout))
        if printers and not self.base_url:
            p = printers[0]
            self.base_url = f"http://{p['host']}:{p['port']}"
        return printers

    def printer_info(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as http:
            resp = http.get(self._url("/printer"), headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    def print(
        self,
        file_path: str | Path,
        copies: int = 1,
        color: bool = True,
        duplex: str | DuplexMode = DuplexMode.NONE,
        media: str = "a4",
        pages: str = "all",
        priority: int = 50,
    ) -> dict[str, Any]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        with (
            httpx.Client(timeout=self.timeout) as http,
            open(path, "rb") as f,
        ):
            resp = http.post(
                self._url("/jobs"),
                headers=self._headers(),
                files={"file": (path.name, f, "application/pdf")},
                data={
                    "copies": str(copies),
                    "color": str(color).lower(),
                    "duplex": str(duplex.value if isinstance(duplex, DuplexMode) else duplex),
                    "media": media,
                    "pages": pages,
                    "priority": str(priority),
                },
            )
            resp.raise_for_status()
            return resp.json()

    def job_status(self, job_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as http:
            resp = http.get(self._url(f"/jobs/{job_id}"), headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as http:
            resp = http.delete(
                self._url(f"/jobs/{job_id}"), headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json()

    def list_jobs(
        self, status: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        with httpx.Client(timeout=self.timeout) as http:
            resp = http.get(
                self._url("/jobs"), headers=self._headers(), params=params
            )
            resp.raise_for_status()
            return resp.json()

    def printer_status(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as http:
            resp = http.get(
                self._url("/status"), headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json()
