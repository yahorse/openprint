from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from starlette.responses import StreamingResponse

from openprint.auth import verify_auth
from openprint.backends.cups import CUPSBackend
from openprint.config import ServerConfig
from openprint.discovery import PrinterAdvertiser
from openprint.errors import (
    FileTooLarge,
    InvalidParameter,
    NotFound,
    OPPError,
    PrinterUnavailable,
)
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

logger = logging.getLogger("openprint.bridge")


class BridgedPrinter:
    """A single CUPS printer exposed via OPP."""

    def __init__(self, cups_name: str, backend: CUPSBackend) -> None:
        self.cups_name = cups_name
        self.backend = backend
        self.jobs: dict[str, Job] = {}
        self.job_data: dict[str, bytes] = {}


class Bridge:
    """Discovers all CUPS printers and serves them through a single OPP endpoint.

    Each CUPS printer gets its own entry in the /opp/v1/printers list and is
    advertised via mDNS so OPP clients discover them automatically.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.config = ServerConfig(**kwargs)
        self.printers: dict[str, BridgedPrinter] = {}
        self.event_bus = EventBus()
        self._advertisers: list[PrinterAdvertiser] = []
        self._app: FastAPI | None = None

    async def _discover_cups_printers(self) -> None:
        cups_printers = await CUPSBackend.list_printers()
        if not cups_printers:
            logger.warning("No CUPS printers found. Is CUPS running?")
            return

        for p in cups_printers:
            name = p["name"]
            backend = CUPSBackend(printer_name=name)
            self.printers[name] = BridgedPrinter(cups_name=name, backend=backend)
            logger.info("Bridged CUPS printer: %s", name)

    def _create_app(self) -> FastAPI:
        @asynccontextmanager
        async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
            await self._discover_cups_printers()

            if self.config.enable_discovery:
                for name, bp in self.printers.items():
                    caps = await bp.backend.get_capabilities()
                    adv = PrinterAdvertiser(
                        name=name,
                        port=self.config.port,
                        color=caps.color,
                        duplex=caps.duplex,
                    )
                    await adv.start()
                    self._advertisers.append(adv)
                    logger.info("mDNS: advertising '%s'", name)

            yield

            for adv in self._advertisers:
                await adv.stop()

        app = FastAPI(title="OpenPrint Bridge", version="0.1.0", lifespan=lifespan)

        if self.config.log_requests:
            app.add_middleware(RequestLoggingMiddleware)
        app.add_middleware(ErrorHandlerMiddleware)

        self._register_routes(app)
        self._app = app
        return app

    def _auth(self, request: Request) -> None:
        verify_auth(request, self.config.auth_token)

    def _get_printer(self, printer_id: str) -> BridgedPrinter:
        if printer_id not in self.printers:
            raise NotFound(f"Printer '{printer_id}' not found.")
        return self.printers[printer_id]

    def _register_routes(self, app: FastAPI) -> None:
        @app.get("/opp/v1/printers")
        async def list_printers(request: Request) -> list[dict[str, Any]]:
            """List all bridged printers."""
            self._auth(request)
            result = []
            for name, bp in self.printers.items():
                state = await bp.backend.get_state()
                caps = await bp.backend.get_capabilities()
                result.append({
                    "id": name,
                    "name": name,
                    "status": state.value,
                    "capabilities": caps.model_dump(),
                })
            return result

        @app.get("/opp/v1/printers/{printer_id}")
        async def get_printer(request: Request, printer_id: str) -> PrinterInfo:
            self._auth(request)
            bp = self._get_printer(printer_id)
            caps = await bp.backend.get_capabilities()
            state = await bp.backend.get_state()
            return PrinterInfo(
                name=printer_id,
                capabilities=caps,
                status=state,
            )

        # Keep /opp/v1/printer for single-printer compat (uses first printer)
        @app.get("/opp/v1/printer")
        async def get_default_printer(request: Request) -> PrinterInfo:
            self._auth(request)
            if not self.printers:
                raise PrinterUnavailable("No printers available.")
            name = next(iter(self.printers))
            bp = self.printers[name]
            caps = await bp.backend.get_capabilities()
            state = await bp.backend.get_state()
            return PrinterInfo(name=name, capabilities=caps, status=state)

        @app.post("/opp/v1/jobs", status_code=201)
        async def create_job(
            request: Request,
            file: UploadFile = File(...),
            printer: str = Form(""),
            copies: int = Form(1),
            color: bool = Form(True),
            duplex: str = Form("none"),
            media: str = Form("a4"),
            pages: str = Form("all"),
            priority: int = Form(50),
        ) -> dict[str, Any]:
            self._auth(request)

            # Pick the target printer
            if printer and printer in self.printers:
                bp = self.printers[printer]
            elif self.printers:
                bp = next(iter(self.printers.values()))
            else:
                raise PrinterUnavailable("No printers available.")

            state = await bp.backend.get_state()
            if state in (PrinterState.ERROR, PrinterState.OFFLINE):
                raise PrinterUnavailable(f"Printer '{bp.cups_name}' is not available.")

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
            bp.jobs[job.id] = job
            bp.job_data[job.id] = data

            asyncio.create_task(self._process_job(bp, job))

            return {
                "id": job.id,
                "printer": bp.cups_name,
                "status": job.status.value,
                "created_at": job.created_at.isoformat(),
                "pages_total": job.pages_total,
                "copies": job.copies,
            }

        @app.get("/opp/v1/jobs")
        async def list_jobs(
            request: Request,
            printer: str | None = None,
            status: str | None = None,
            limit: int = 50,
        ) -> JobList:
            self._auth(request)
            all_jobs: list[Job] = []
            for name, bp in self.printers.items():
                if printer and name != printer:
                    continue
                all_jobs.extend(bp.jobs.values())
            if status:
                all_jobs = [j for j in all_jobs if j.status.value == status]
            all_jobs.sort(key=lambda j: j.created_at, reverse=True)
            return JobList(jobs=all_jobs[:limit], total=len(all_jobs))

        @app.get("/opp/v1/jobs/{job_id}")
        async def get_job(request: Request, job_id: str) -> Job:
            self._auth(request)
            for bp in self.printers.values():
                if job_id in bp.jobs:
                    return bp.jobs[job_id]
            raise NotFound(f"Job '{job_id}' not found.")

        @app.delete("/opp/v1/jobs/{job_id}")
        async def cancel_job(request: Request, job_id: str) -> dict[str, str]:
            self._auth(request)
            for bp in self.printers.values():
                if job_id in bp.jobs:
                    job = bp.jobs[job_id]
                    if job.status not in (JobStatus.QUEUED, JobStatus.PROCESSING):
                        raise InvalidParameter(
                            f"Cannot cancel job in '{job.status.value}' state."
                        )
                    job.status = JobStatus.CANCELED
                    await bp.backend.cancel_job(job)
                    await self.event_bus.publish(
                        f"job:{job_id}", "status", {"status": "canceled"}
                    )
                    await self.event_bus.close_channel(f"job:{job_id}")
                    return {"id": job.id, "status": job.status.value}
            raise NotFound(f"Job '{job_id}' not found.")

        @app.get("/opp/v1/jobs/{job_id}/events")
        async def job_events(request: Request, job_id: str) -> StreamingResponse:
            self._auth(request)
            found = any(job_id in bp.jobs for bp in self.printers.values())
            if not found:
                raise NotFound(f"Job '{job_id}' not found.")
            return StreamingResponse(
                event_stream(self.event_bus, f"job:{job_id}"),
                media_type="text/event-stream",
            )

        @app.get("/opp/v1/status")
        async def get_status(request: Request) -> dict[str, Any]:
            self._auth(request)
            result: dict[str, Any] = {}
            for name, bp in self.printers.items():
                state = await bp.backend.get_state()
                supplies = await bp.backend.get_supplies()
                queued = sum(1 for j in bp.jobs.values() if j.status == JobStatus.QUEUED)
                printing = sum(
                    1 for j in bp.jobs.values()
                    if j.status in (JobStatus.PROCESSING, JobStatus.PRINTING)
                )
                result[name] = {
                    "state": state.value,
                    "supplies": supplies.model_dump(),
                    "jobs_queued": queued,
                    "jobs_printing": printing,
                }
            return result

        @app.get("/opp/v1/status/events")
        async def status_events(request: Request) -> StreamingResponse:
            self._auth(request)
            return StreamingResponse(
                event_stream(self.event_bus, "printer:status"),
                media_type="text/event-stream",
            )

    async def _process_job(self, bp: BridgedPrinter, job: Job) -> None:
        channel = f"job:{job.id}"
        try:
            job.status = JobStatus.PROCESSING
            await self.event_bus.publish(channel, "status", {"status": "processing"})

            pdf_data = bp.job_data.pop(job.id, b"")

            job.status = JobStatus.PRINTING
            await self.event_bus.publish(channel, "status", {"status": "printing"})
            await self.event_bus.publish(
                "printer:status", "state",
                {"printer": bp.cups_name, "state": "printing"},
            )

            await bp.backend.print_job(job, pdf_data)

            job.status = JobStatus.COMPLETED
            job.pages_printed = job.pages_total
            await self.event_bus.publish(
                channel,
                "complete",
                {"status": "completed", "pages_printed": job.pages_total},
            )
            logger.info("Job %s completed on %s", job.id, bp.cups_name)

        except asyncio.CancelledError:
            job.status = JobStatus.CANCELED
        except Exception as exc:
            job.status = JobStatus.ERROR
            job.error = str(exc)
            await self.event_bus.publish(channel, "error", {"error": str(exc)})
            logger.error("Job %s failed on %s: %s", job.id, bp.cups_name, exc)
        finally:
            await self.event_bus.close_channel(channel)
            await self.event_bus.publish(
                "printer:status", "state",
                {"printer": bp.cups_name, "state": "idle"},
            )

    def run(self) -> None:
        app = self._create_app()
        logging.basicConfig(level=logging.INFO)
        logger.info("OpenPrint Bridge starting on port %d", self.config.port)
        logger.info("Will bridge all CUPS printers to OPP")
        uvicorn.run(app, host=self.config.host, port=self.config.port)

    def create_app(self) -> FastAPI:
        return self._create_app()


def main() -> None:
    Bridge().run()
