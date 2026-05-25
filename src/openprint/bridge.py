from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from starlette.responses import StreamingResponse

from openprint.auth import verify_auth
from openprint.backends.cups import CUPSBackend
from openprint.backends.ipp import IPPBackend
from openprint.config import ServerConfig
from openprint.dashboard import mount_dashboard
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
from openprint.progress import JobProgressTracker
from openprint.scanner import CUPSWatcher, NetworkPrinterScanner
from openprint.status import EventBus, event_stream
from openprint.store import JobStore

logger = logging.getLogger("openprint.bridge")


class BridgedPrinter:
    """A single printer exposed via OPP."""

    def __init__(self, printer_id: str, backend: CUPSBackend | IPPBackend, source: str = "cups") -> None:
        self.printer_id = printer_id
        self.backend = backend
        self.source = source
        self.jobs: dict[str, Job] = {}
        self.job_data: dict[str, bytes] = {}


class Bridge:
    """Discovers all local and network printers and serves them through OPP.

    Features:
    - Auto-discovers CUPS printers at startup
    - Watches for new CUPS printers appearing/disappearing
    - Scans the network for IPP printers via mDNS
    - Persists job history to SQLite
    - Tracks real job progress from CUPS
    - Serves a web dashboard
    - Optional TLS
    """

    def __init__(self, **kwargs: Any) -> None:
        self.config = ServerConfig(**kwargs)
        self.printers: dict[str, BridgedPrinter] = {}
        self.event_bus = EventBus()
        self._advertisers: list[PrinterAdvertiser] = []
        self._app: FastAPI | None = None

        self._store: JobStore | None = None
        self._progress: JobProgressTracker | None = None
        self._network_scanner: NetworkPrinterScanner | None = None
        self._cups_watcher: CUPSWatcher | None = None

        self.enable_persistence: bool = kwargs.get("enable_persistence", True)
        self.enable_network_scan: bool = kwargs.get("enable_network_scan", True)
        self.enable_cups_watch: bool = kwargs.get("enable_cups_watch", True)
        self.enable_dashboard: bool = kwargs.get("enable_dashboard", True)
        self.tls_cert: str | None = kwargs.get("tls_cert")
        self.tls_key: str | None = kwargs.get("tls_key")

    async def _on_cups_found(self, printer_info: dict[str, Any]) -> None:
        name = printer_info["name"]
        if name not in self.printers:
            backend = CUPSBackend(printer_name=name)
            self.printers[name] = BridgedPrinter(name, backend, source="cups")
            logger.info("Hot-added CUPS printer: %s", name)
            await self.event_bus.publish(
                "printer:status", "state", {"printer": name, "state": "idle"}
            )
            if self.config.enable_discovery:
                await self._advertise_printer(name)

    async def _on_cups_lost(self, name: str) -> None:
        if name in self.printers and self.printers[name].source == "cups":
            del self.printers[name]
            logger.info("Removed CUPS printer: %s", name)
            await self.event_bus.publish(
                "printer:status", "state", {"printer": name, "state": "offline"}
            )

    async def _on_ipp_found(self, printer_info: dict[str, Any]) -> None:
        pid = printer_info["id"]
        if pid not in self.printers:
            backend = IPPBackend(
                uri=printer_info["uri"],
                tls=printer_info.get("tls", False),
            )
            self.printers[pid] = BridgedPrinter(pid, backend, source="ipp")
            logger.info("Hot-added IPP printer: %s (%s)", printer_info["name"], pid)
            await self.event_bus.publish(
                "printer:status", "state", {"printer": pid, "state": "idle"}
            )

    async def _on_ipp_lost(self, printer_id: str) -> None:
        if printer_id in self.printers and self.printers[printer_id].source == "ipp":
            del self.printers[printer_id]
            logger.info("Removed IPP printer: %s", printer_id)

    async def _advertise_printer(self, name: str) -> None:
        bp = self.printers.get(name)
        if not bp:
            return
        try:
            caps = await bp.backend.get_capabilities()
            adv = PrinterAdvertiser(
                name=name,
                port=self.config.port,
                color=caps.color,
                duplex=caps.duplex,
            )
            await adv.start()
            self._advertisers.append(adv)
        except Exception as exc:
            logger.warning("Failed to advertise %s: %s", name, exc)

    async def _discover_cups_printers(self) -> None:
        cups_printers = await CUPSBackend.list_printers()
        if not cups_printers:
            logger.warning("No CUPS printers found. Is CUPS running?")
            return

        for p in cups_printers:
            name = p["name"]
            backend = CUPSBackend(printer_name=name)
            self.printers[name] = BridgedPrinter(name, backend, source="cups")
            logger.info("Bridged CUPS printer: %s", name)

    def _create_app(self) -> FastAPI:
        @asynccontextmanager
        async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
            # Discover initial CUPS printers
            await self._discover_cups_printers()

            # Start persistent job store
            if self.enable_persistence:
                self._store = JobStore()
                logger.info("Job persistence enabled")

            # Start CUPS job progress tracker
            self._progress = JobProgressTracker()
            await self._progress.start()

            # Start CUPS watcher for hot-plug detection
            if self.enable_cups_watch:
                self._cups_watcher = CUPSWatcher(
                    interval=10.0,
                    on_found=self._on_cups_found,
                    on_lost=self._on_cups_lost,
                )
                await self._cups_watcher.start()

            # Start network scanner for IPP printers
            if self.enable_network_scan:
                self._network_scanner = NetworkPrinterScanner(
                    on_found=self._on_ipp_found,
                    on_lost=self._on_ipp_lost,
                )
                await self._network_scanner.start()

            # Advertise all initial printers via mDNS
            if self.config.enable_discovery:
                for name in list(self.printers):
                    await self._advertise_printer(name)

            yield

            # Cleanup
            if self._cups_watcher:
                await self._cups_watcher.stop()
            if self._network_scanner:
                await self._network_scanner.stop()
            if self._progress:
                await self._progress.stop()
            if self._store:
                self._store.close()
            for adv in self._advertisers:
                await adv.stop()

        app = FastAPI(title="OpenPrint Bridge", version="0.1.0", lifespan=lifespan)

        if self.config.log_requests:
            app.add_middleware(RequestLoggingMiddleware)
        app.add_middleware(ErrorHandlerMiddleware)

        if self.enable_dashboard:
            mount_dashboard(app)

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
            self._auth(request)
            result = []
            for pid, bp in self.printers.items():
                try:
                    state = await bp.backend.get_state()
                    caps = await bp.backend.get_capabilities()
                except Exception:
                    state = PrinterState.OFFLINE
                    caps = Capabilities()
                result.append({
                    "id": pid,
                    "name": await bp.backend.get_printer_name(),
                    "source": bp.source,
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
            name = await bp.backend.get_printer_name()
            return PrinterInfo(name=name, capabilities=caps, status=state)

        @app.get("/opp/v1/printer")
        async def get_default_printer(request: Request) -> PrinterInfo:
            self._auth(request)
            if not self.printers:
                raise PrinterUnavailable("No printers available.")
            name = next(iter(self.printers))
            bp = self.printers[name]
            caps = await bp.backend.get_capabilities()
            state = await bp.backend.get_state()
            printer_name = await bp.backend.get_printer_name()
            return PrinterInfo(name=printer_name, capabilities=caps, status=state)

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

            if printer and printer in self.printers:
                bp = self.printers[printer]
            elif self.printers:
                bp = next(iter(self.printers.values()))
            else:
                raise PrinterUnavailable("No printers available.")

            state = await bp.backend.get_state()
            if state in (PrinterState.ERROR, PrinterState.OFFLINE):
                raise PrinterUnavailable(f"Printer '{bp.printer_id}' is not available.")

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

            if self._store:
                self._store.save(job, bp.printer_id)

            asyncio.create_task(self._process_job(bp, job))

            return {
                "id": job.id,
                "printer": bp.printer_id,
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

            if self._store:
                jobs, total = self._store.list_jobs(
                    printer=printer, status=status, limit=limit
                )
                return JobList(jobs=jobs, total=total)

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
            if self._store:
                job = self._store.get(job_id)
                if job:
                    return job
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
                    if self._store:
                        self._store.update_status(job_id, "canceled")
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
            for pid, bp in self.printers.items():
                try:
                    state = await bp.backend.get_state()
                    supplies = await bp.backend.get_supplies()
                except Exception:
                    state = PrinterState.OFFLINE
                    supplies = SupplyLevels()
                queued = sum(1 for j in bp.jobs.values() if j.status == JobStatus.QUEUED)
                printing = sum(
                    1 for j in bp.jobs.values()
                    if j.status in (JobStatus.PROCESSING, JobStatus.PRINTING)
                )
                result[pid] = {
                    "state": state.value,
                    "source": bp.source,
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

    async def _on_job_progress(self, job_id: str, status: str, pages: int) -> None:
        channel = f"job:{job_id}"
        if status == "completed":
            await self.event_bus.publish(
                channel, "complete", {"status": "completed", "pages_printed": pages}
            )
        elif status == "error":
            await self.event_bus.publish(channel, "error", {"error": "CUPS job failed"})
        if self._store:
            self._store.update_status(job_id, status, pages)

    async def _process_job(self, bp: BridgedPrinter, job: Job) -> None:
        channel = f"job:{job.id}"
        try:
            job.status = JobStatus.PROCESSING
            if self._store:
                self._store.update_status(job.id, "processing")
            await self.event_bus.publish(channel, "status", {"status": "processing"})

            pdf_data = bp.job_data.pop(job.id, b"")

            job.status = JobStatus.PRINTING
            if self._store:
                self._store.update_status(job.id, "printing")
            await self.event_bus.publish(channel, "status", {"status": "printing"})
            await self.event_bus.publish(
                "printer:status", "state",
                {"printer": bp.printer_id, "state": "printing"},
            )

            await bp.backend.print_job(job, pdf_data)

            # For CUPS backend, track progress via lpstat polling
            if isinstance(bp.backend, CUPSBackend) and self._progress:
                cups_id = bp.backend._cups_job_ids.get(job.id)
                if cups_id:
                    self._progress.track(
                        job, cups_id, on_progress=self._on_job_progress
                    )
                    return  # Progress tracker will handle completion

            job.status = JobStatus.COMPLETED
            job.pages_printed = job.pages_total
            if self._store:
                self._store.update_status(job.id, "completed", job.pages_total)
            await self.event_bus.publish(
                channel,
                "complete",
                {"status": "completed", "pages_printed": job.pages_total},
            )
            logger.info("Job %s completed on %s", job.id, bp.printer_id)

        except asyncio.CancelledError:
            job.status = JobStatus.CANCELED
            if self._store:
                self._store.update_status(job.id, "canceled")
        except Exception as exc:
            job.status = JobStatus.ERROR
            job.error = str(exc)
            if self._store:
                self._store.update_status(job.id, "error", error=str(exc))
            await self.event_bus.publish(channel, "error", {"error": str(exc)})
            logger.error("Job %s failed on %s: %s", job.id, bp.printer_id, exc)
        finally:
            await self.event_bus.close_channel(channel)
            await self.event_bus.publish(
                "printer:status", "state",
                {"printer": bp.printer_id, "state": "idle"},
            )

    def run(self) -> None:
        app = self._create_app()
        logging.basicConfig(level=logging.INFO)
        logger.info("OpenPrint Bridge starting on port %d", self.config.port)

        ssl_keyfile = self.tls_key
        ssl_certfile = self.tls_cert

        if ssl_certfile and ssl_keyfile:
            logger.info("TLS enabled: %s", ssl_certfile)
            uvicorn.run(
                app,
                host=self.config.host,
                port=self.config.port,
                ssl_keyfile=ssl_keyfile,
                ssl_certfile=ssl_certfile,
            )
        else:
            uvicorn.run(app, host=self.config.host, port=self.config.port)

    def create_app(self) -> FastAPI:
        return self._create_app()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="OpenPrint Bridge")
    parser.add_argument("--port", type=int, default=631, help="Port to listen on")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--tls-cert", help="Path to TLS certificate")
    parser.add_argument("--tls-key", help="Path to TLS private key")
    parser.add_argument("--tls-auto", action="store_true", help="Auto-generate self-signed cert")
    parser.add_argument("--no-dashboard", action="store_true", help="Disable web dashboard")
    parser.add_argument("--no-network-scan", action="store_true", help="Disable IPP network scanning")
    parser.add_argument("--auth-token", help="Require this token for API access")
    args = parser.parse_args()

    tls_cert = args.tls_cert
    tls_key = args.tls_key
    if args.tls_auto and not tls_cert:
        from openprint.tls import generate_self_signed_cert
        cert_path, key_path = generate_self_signed_cert()
        tls_cert = str(cert_path)
        tls_key = str(key_path)

    bridge = Bridge(
        port=args.port,
        host=args.host,
        tls_cert=tls_cert,
        tls_key=tls_key,
        enable_dashboard=not args.no_dashboard,
        enable_network_scan=not args.no_network_scan,
        auth_token=args.auth_token,
    )
    bridge.run()
