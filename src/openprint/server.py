from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from openprint.auth import verify_auth
from openprint.config import ServerConfig
from openprint.discovery import PrinterAdvertiser
from openprint.errors import FileTooLarge, InvalidParameter, NotFound, OPPError, PrinterUnavailable
from openprint.middleware import ErrorHandlerMiddleware, RequestLoggingMiddleware
from openprint.models import (
    Capabilities,
    DuplexMode,
    Job,
    JobList,
    JobStatus,
    PrinterInfo,
    PrinterState,
    PrinterStatus,
    SupplyLevels,
    TrayStatus,
)
from openprint.pdf import parse_page_range, validate_pdf
from openprint.status import EventBus, event_stream

logger = logging.getLogger("openprint")


class Server:
    def __init__(self, **kwargs: Any) -> None:
        self.config = ServerConfig(**kwargs)
        self.jobs: dict[str, Job] = {}
        self.event_bus = EventBus()
        self.printer_state = PrinterState.IDLE
        self._advertiser: PrinterAdvertiser | None = None
        self._app: FastAPI | None = None

    def _create_app(self) -> FastAPI:
        @asynccontextmanager
        async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
            if self.config.enable_discovery:
                self._advertiser = PrinterAdvertiser(
                    name=self.config.name,
                    port=self.config.port,
                    color=self.config.color,
                    duplex=self.config.duplex,
                )
                await self._advertiser.start()
                logger.info("mDNS: advertising '%s' on port %d", self.config.name, self.config.port)
            yield
            if self._advertiser:
                await self._advertiser.stop()

        app = FastAPI(title="OpenPrint Server", version="0.1.0", lifespan=lifespan)

        if self.config.log_requests:
            app.add_middleware(RequestLoggingMiddleware)
        app.add_middleware(ErrorHandlerMiddleware)

        self._register_routes(app)
        self._app = app
        return app

    def _auth(self, request: Request) -> None:
        verify_auth(request, self.config.auth_token)

    def _register_routes(self, app: FastAPI) -> None:
        @app.get("/opp/v1/printer")
        async def get_printer(request: Request) -> PrinterInfo:
            self._auth(request)
            return PrinterInfo(
                name=self.config.name,
                capabilities=Capabilities(
                    color=self.config.color,
                    duplex=self.config.duplex,
                    media_sizes=self.config.supported_media,
                    max_file_size=self.config.max_file_size,
                ),
                status=self.printer_state,
            )

        @app.post("/opp/v1/jobs", status_code=201)
        async def create_job(
            request: Request,
            file: UploadFile = File(...),
            copies: int = Form(1),
            color: bool = Form(True),
            duplex: str = Form("none"),
            media: str = Form("a4"),
            pages: str = Form("all"),
            priority: int = Form(50),
        ) -> dict[str, Any]:
            self._auth(request)

            if self.printer_state in (PrinterState.ERROR, PrinterState.OFFLINE):
                raise PrinterUnavailable("Printer is not available.")

            if media not in self.config.supported_media:
                raise InvalidParameter(
                    f"Unsupported media size '{media}'. "
                    f"Supported: {', '.join(self.config.supported_media)}"
                )

            try:
                duplex_mode = DuplexMode(duplex)
            except ValueError:
                raise InvalidParameter(
                    f"Invalid duplex mode '{duplex}'. Use: none, long-edge, short-edge"
                )

            data = await file.read()
            if len(data) > self.config.max_file_size:
                raise FileTooLarge(
                    f"File size {len(data)} exceeds maximum {self.config.max_file_size}."
                )

            page_count = validate_pdf(data)
            selected_pages = parse_page_range(pages, page_count)

            job = Job(
                pages_total=len(selected_pages),
                copies=copies,
                color=color,
                duplex=duplex_mode,
                media=media,
                priority=priority,
                file_size=len(data),
            )
            self.jobs[job.id] = job

            asyncio.create_task(self._process_job(job))

            return {
                "id": job.id,
                "status": job.status.value,
                "created_at": job.created_at.isoformat(),
                "pages_total": job.pages_total,
                "copies": job.copies,
            }

        @app.get("/opp/v1/jobs")
        async def list_jobs(
            request: Request,
            status: str | None = None,
            limit: int = 50,
        ) -> JobList:
            self._auth(request)
            jobs = list(self.jobs.values())
            if status:
                jobs = [j for j in jobs if j.status.value == status]
            jobs.sort(key=lambda j: j.created_at, reverse=True)
            return JobList(jobs=jobs[:limit], total=len(jobs))

        @app.get("/opp/v1/jobs/{job_id}")
        async def get_job(request: Request, job_id: str) -> Job:
            self._auth(request)
            if job_id not in self.jobs:
                raise NotFound(f"Job '{job_id}' not found.")
            return self.jobs[job_id]

        @app.delete("/opp/v1/jobs/{job_id}")
        async def cancel_job(request: Request, job_id: str) -> dict[str, str]:
            self._auth(request)
            if job_id not in self.jobs:
                raise NotFound(f"Job '{job_id}' not found.")
            job = self.jobs[job_id]
            if job.status not in (JobStatus.QUEUED, JobStatus.PROCESSING):
                raise InvalidParameter(
                    f"Cannot cancel job in '{job.status.value}' state."
                )
            job.status = JobStatus.CANCELED
            await self.event_bus.publish(
                f"job:{job_id}", "status", {"status": "canceled"}
            )
            await self.event_bus.close_channel(f"job:{job_id}")
            return {"id": job.id, "status": job.status.value}

        @app.get("/opp/v1/jobs/{job_id}/events")
        async def job_events(request: Request, job_id: str) -> StreamingResponse:
            self._auth(request)
            if job_id not in self.jobs:
                raise NotFound(f"Job '{job_id}' not found.")
            return StreamingResponse(
                event_stream(self.event_bus, f"job:{job_id}"),
                media_type="text/event-stream",
            )

        @app.get("/opp/v1/status")
        async def get_status(request: Request) -> PrinterStatus:
            self._auth(request)
            queued = sum(
                1 for j in self.jobs.values() if j.status == JobStatus.QUEUED
            )
            printing = sum(
                1 for j in self.jobs.values()
                if j.status in (JobStatus.PROCESSING, JobStatus.PRINTING)
            )
            return PrinterStatus(
                state=self.printer_state,
                supplies=SupplyLevels(
                    paper={
                        "tray1": TrayStatus(media="a4", level="full"),
                    }
                ),
                jobs_queued=queued,
                jobs_printing=printing,
            )

        @app.get("/opp/v1/status/events")
        async def status_events(request: Request) -> StreamingResponse:
            self._auth(request)
            return StreamingResponse(
                event_stream(self.event_bus, "printer:status"),
                media_type="text/event-stream",
            )

    async def _process_job(self, job: Job) -> None:
        channel = f"job:{job.id}"
        try:
            job.status = JobStatus.PROCESSING
            self.printer_state = PrinterState.PRINTING
            await self.event_bus.publish(channel, "status", {"status": "processing"})
            await self.event_bus.publish("printer:status", "state", {"state": "printing"})

            job.status = JobStatus.PRINTING
            for page in range(1, job.pages_total + 1):
                await asyncio.sleep(0.5)
                job.pages_printed = page
                await self.event_bus.publish(
                    channel,
                    "progress",
                    {"pages_printed": page, "pages_total": job.pages_total},
                )

            job.status = JobStatus.COMPLETED
            await self.event_bus.publish(
                channel,
                "complete",
                {"status": "completed", "pages_printed": job.pages_total},
            )
        except asyncio.CancelledError:
            job.status = JobStatus.CANCELED
        except Exception as exc:
            job.status = JobStatus.ERROR
            job.error = str(exc)
            await self.event_bus.publish(channel, "error", {"error": str(exc)})
        finally:
            await self.event_bus.close_channel(channel)
            active = any(
                j.status in (JobStatus.PROCESSING, JobStatus.PRINTING)
                for j in self.jobs.values()
            )
            if not active:
                self.printer_state = PrinterState.IDLE
                await self.event_bus.publish(
                    "printer:status", "state", {"state": "idle"}
                )

    def run(self) -> None:
        app = self._create_app()
        logging.basicConfig(level=logging.INFO)
        uvicorn.run(app, host=self.config.host, port=self.config.port)

    def create_app(self) -> FastAPI:
        return self._create_app()


def main() -> None:
    Server().run()
