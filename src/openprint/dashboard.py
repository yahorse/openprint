from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

STATIC_DIR = Path(__file__).parent / "static"


def mount_dashboard(app: FastAPI) -> None:
    """Mount the web dashboard on the FastAPI app."""

    @app.get("/")
    async def dashboard_index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
