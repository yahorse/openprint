from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
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
    SupplyLevels,
)
from openprint.pdf import parse_page_range, validate_pdf
from openprint.progress import JobProgressTracker
from openprint.resilience import (
    PrinterHealthMonitor,
    RetryPrinter,
)
from openprint.scanner import CUPSWatcher, NetworkPrinterScanner
from openprint.status import EventBus, event_stream
from openprint.store import JobStore

logger = logging.getLogger("openprint.bridge")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


class BridgedPrinter:
    """A single printer exposed via OPP."""

    def __init__(
        self, printer_id: str, backend: CUPSBackend | IPPBackend, source: str = "cups",
    ) -> None:
        self.printer_id = printer_id
        self.backend = backend
        self.source = source
        self.jobs: dict[str, Job] = {}
        self.job_data: dict[str, bytes] = {}
        # Cached static info fetched once on discovery
        self.cached_name: str | None = None
        self.cached_caps: Capabilities | None = None
        self.cached_supplies: SupplyLevels | None = None
        # Webhook URLs keyed by job_id
        self.job_webhooks: dict[str, str] = {}
        # Timestamp of last successful prefetch (for cache TTL)
        self._prefetch_timestamp: float | None = None


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
        self._health_monitor: PrinterHealthMonitor | None = None

        self.enable_persistence: bool = kwargs.get("enable_persistence", True)
        self.enable_network_scan: bool = kwargs.get("enable_network_scan", True)
        self.enable_cups_watch: bool = kwargs.get("enable_cups_watch", True)
        self.enable_dashboard: bool = kwargs.get("enable_dashboard", True)
        self.enable_health_check: bool = kwargs.get("enable_health_check", True)
        self.tls_cert: str | None = kwargs.get("tls_cert")
        self.tls_key: str | None = kwargs.get("tls_key")

        # Job queue size limit
        self.config.max_queue_size: int = kwargs.get("max_queue_size", 100)

        # CORS origins (default open; pass [] to disable)
        self.config.cors_origins: list[str] = kwargs.get("cors_origins", ["*"])

    async def _prefetch_printer_info(self, bp: BridgedPrinter) -> None:
        """Fetch static printer info once on discovery and cache it."""
        try:
            bp.cached_name = await bp.backend.get_printer_name()
            bp.cached_caps = await bp.backend.get_capabilities()
            if isinstance(bp.backend, IPPBackend):
                bp.backend._supported_formats = await bp.backend._get_supported_formats()
            logger.info(
                "Prefetched info for %s: name=%r formats=%s",
                bp.printer_id,
                bp.cached_name,
                getattr(bp.backend, "_supported_formats", None),
            )
            bp._prefetch_timestamp = asyncio.get_event_loop().time()
        except Exception as exc:
            logger.warning("Failed to prefetch info for %s: %s", bp.printer_id, exc)

        # Fetch and cache supply levels; warn on critically low ink
        try:
            supplies = await bp.backend.get_supplies()
            bp.cached_supplies = supplies
            supply_data = supplies.model_dump()
            for color, level in supply_data.items():
                if isinstance(level, (int, float)):
                    if level == 0:
                        logger.warning(
                            "Printer %s: %s ink critically low (%d%%)",
                            bp.printer_id, color, level,
                        )
                    elif level < 10:
                        logger.warning(
                            "Printer %s: %s ink critically low (%d%%)",
                            bp.printer_id, color, level,
                        )
        except Exception as exc:
            logger.warning("Failed to fetch supplies for %s: %s", bp.printer_id, exc)

    def _refresh_stale_caches(self) -> None:
        """Schedule a prefetch for any printer whose cached info is older than 300 seconds."""
        now = asyncio.get_event_loop().time()
        for bp in self.printers.values():
            if bp._prefetch_timestamp is None or (now - bp._prefetch_timestamp) > 300:
                logger.debug(
                    "Cache stale for %s (age=%s), refreshing",
                    bp.printer_id,
                    None if bp._prefetch_timestamp is None else f"{now - bp._prefetch_timestamp:.0f}s",
                )
                asyncio.create_task(self._prefetch_printer_info(bp))

    async def _on_cups_found(self, printer_info: dict[str, Any]) -> None:
        name = printer_info["name"]
        if name not in self.printers:
            backend = CUPSBackend(printer_name=name)
            bp = BridgedPrinter(name, backend, source="cups")
            self.printers[name] = bp
            logger.info("Hot-added CUPS printer: %s", name)
            asyncio.create_task(self._prefetch_printer_info(bp))
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
            bp = BridgedPrinter(pid, backend, source="ipp")
            self.printers[pid] = bp
            logger.info("Hot-added IPP printer: %s (%s)", printer_info["name"], pid)
            asyncio.create_task(self._prefetch_printer_info(bp))
            await self.event_bus.publish(
                "printer:status", "state", {"printer": pid, "state": "idle"}
            )
            if self._health_monitor:
                self._health_monitor.register(
                    pid,
                    host=printer_info["host"],
                    port=printer_info["port"],
                    hostname=printer_info.get("hostname"),
                )

    async def _on_ipp_lost(self, printer_id: str) -> None:
        if printer_id in self.printers and self.printers[printer_id].source == "ipp":
            del self.printers[printer_id]
            logger.info("Removed IPP printer: %s", printer_id)

    async def _on_health_change(self, printer_id: str, state: str) -> None:
        logger.info("Printer %s health changed: %s", printer_id, state)
        await self.event_bus.publish(
            "printer:status", "state", {"printer": printer_id, "state": state}
        )
        # Refresh any stale printer info caches on every health tick
        self._refresh_stale_caches()
        # When a printer comes back online, reschedule any queued jobs
        if state in ("online", "idle") and printer_id in self.printers:
            bp = self.printers[printer_id]
            for job in bp.jobs.values():
                if job.status == JobStatus.QUEUED:
                    logger.info(
                        "Printer %s back online — rescheduling queued job %s",
                        printer_id, job.id,
                    )
                    asyncio.create_task(self._process_job(bp, job))

    async def _advertise_printer(self, name: str) -> None:
        bp = self.printers.get(name)
        if not bp:
            return
        try:
            caps = bp.cached_caps or await bp.backend.get_capabilities()
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
            bp = BridgedPrinter(name, backend, source="cups")
            self.printers[name] = bp
            logger.info("Bridged CUPS printer: %s", name)
            asyncio.create_task(self._prefetch_printer_info(bp))

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

            # Start health monitor for all printers
            if self.enable_health_check:
                self._health_monitor = PrinterHealthMonitor(check_interval=30.0)
                self._health_monitor.set_callback(self._on_health_change)
                await self._health_monitor.start()
                logger.info("Printer health monitor started (30s interval)")

            # Advertise all initial printers via mDNS
            if self.config.enable_discovery:
                for name in list(self.printers):
                    await self._advertise_printer(name)

            yield

            # Cleanup
            if self._health_monitor:
                await self._health_monitor.stop()
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
        app.add_middleware(SecurityHeadersMiddleware)

        if self.config.cors_origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=self.config.cors_origins,
                allow_methods=["*"],
                allow_headers=["*"],
            )

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
        @app.get(
            "/opp/v1/printers",
            summary="List printers",
            description="Returns all discovered printers with their current state and capabilities.",
        )
        async def list_printers(request: Request) -> list[dict[str, Any]]:
            self._auth(request)
            result = []
            for pid, bp in self.printers.items():
                try:
                    state = await bp.backend.get_state()
                except Exception:
                    state = PrinterState.OFFLINE
                caps = bp.cached_caps or Capabilities()
                result.append({
                    "id": pid,
                    "name": bp.cached_name or pid,
                    "source": bp.source,
                    "status": state.value,
                    "capabilities": caps.model_dump(),
                })
            return result

        @app.get(
            "/opp/v1/printers/{printer_id}",
            summary="Get printer",
            description="Returns detailed information about a specific printer, including capabilities and current state.",
        )
        async def get_printer(request: Request, printer_id: str) -> PrinterInfo:
            self._auth(request)
            bp = self._get_printer(printer_id)
            caps = bp.cached_caps or await bp.backend.get_capabilities()
            state = await bp.backend.get_state()
            name = bp.cached_name or await bp.backend.get_printer_name()
            return PrinterInfo(name=name, capabilities=caps, status=state)

        @app.get(
            "/opp/v1/printer",
            summary="Get default printer",
            description="Returns information about the default (first discovered) printer.",
        )
        async def get_default_printer(request: Request) -> PrinterInfo:
            self._auth(request)
            if not self.printers:
                raise PrinterUnavailable("No printers available.")
            name = next(iter(self.printers))
            bp = self.printers[name]
            caps = bp.cached_caps or await bp.backend.get_capabilities()
            state = await bp.backend.get_state()
            printer_name = bp.cached_name or await bp.backend.get_printer_name()
            return PrinterInfo(name=printer_name, capabilities=caps, status=state)

        @app.get(
            "/opp/v1/printers/{printer_id}/formats",
            summary="Get supported document formats",
            description="Returns the list of document formats the printer supports, such as application/pdf or image/jpeg.",
        )
        async def get_printer_formats(
            request: Request, printer_id: str
        ) -> dict[str, Any]:
            self._auth(request)
            bp = self._get_printer(printer_id)
            formats = getattr(bp.backend, "_supported_formats", None) or []
            return {"printer_id": printer_id, "formats": formats}

        @app.get(
            "/opp/v1/printers/{printer_id}/supplies",
            summary="Get printer supply levels",
            description="Returns live ink or toner levels for a specific printer.",
        )
        async def get_printer_supplies(
            request: Request, printer_id: str
        ) -> dict[str, Any]:
            self._auth(request)
            bp = self._get_printer(printer_id)
            supplies = await bp.backend.get_supplies()
            return {"printer_id": printer_id, "supplies": supplies.model_dump()}

        @app.post(
            "/opp/v1/jobs",
            status_code=201,
            summary="Create print job",
            description="Submits a new print job with the given document and print options. Optionally provide a webhook_url to receive a callback when the job completes or fails.",
        )
        async def create_job(
            request: Request,
            file: UploadFile = File(...),  # noqa: B008
            printer: str = Form(""),
            copies: int = Form(1),
            color: bool = Form(True),
            duplex: str = Form("none"),
            media: str = Form("a4"),
            pages: str = Form("all"),
            priority: int = Form(50),
            webhook_url: str = Form(""),
        ) -> dict[str, Any]:
            self._auth(request)

            # Enforce global job queue size limit
            total_queued = sum(
                1
                for bp in self.printers.values()
                for j in bp.jobs.values()
                if j.status in (JobStatus.QUEUED, JobStatus.PROCESSING)
            )
            if total_queued >= self.config.max_queue_size:
                raise PrinterUnavailable("Job queue is full. Try again later.")

            if printer and printer in self.printers:
                bp = self.printers[printer]
            elif self.printers:
                bp = next(iter(self.printers.values()))
            else:
                raise PrinterUnavailable("No printers available.")

            try:
                state = await bp.backend.get_state()
            except Exception:
                state = PrinterState.OFFLINE

            if state == PrinterState.ERROR:
                raise PrinterUnavailable(f"Printer '{bp.printer_id}' is in error state.")
            # Don't reject offline printers immediately — the job processor
            # will retry and attempt to wake the printer

            try:
                duplex_mode = DuplexMode(duplex)
            except ValueError as err:
                raise InvalidParameter(
                    f"Invalid duplex mode '{duplex}'. Use: none, long-edge, short-edge"
                ) from err

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

            # Validate and register webhook if provided
            if webhook_url:
                parsed = urlparse(webhook_url)
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    raise InvalidParameter(
                        "webhook_url must be a valid http or https URL"
                    )
                bp.job_webhooks[job.id] = webhook_url

            if self._store:
                self._store.save(job, bp.printer_id)

            asyncio.create_task(self._process_job(bp, job))

            # Check supply levels and add warnings if any are below 15%
            response: dict[str, Any] = {
                "id": job.id,
                "printer": bp.printer_id,
                "status": job.status.value,
                "created_at": job.created_at.isoformat(),
                "pages_total": job.pages_total,
                "copies": job.copies,
            }
            try:
                supplies = await bp.backend.get_supplies()
                supply_data = supplies.model_dump()
                low_warnings = [
                    f"{color} ink low ({level}%)"
                    for color, level in supply_data.items()
                    if isinstance(level, (int, float)) and level < 15
                ]
                if low_warnings:
                    response["warnings"] = low_warnings
            except Exception:
                pass  # Supply check failures should not block job creation

            return response

        @app.get(
            "/opp/v1/jobs",
            summary="List jobs",
            description="Returns a paginated list of print jobs, optionally filtered by printer or status.",
        )
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

        @app.get(
            "/opp/v1/jobs/{job_id}",
            summary="Get job",
            description="Returns the current state and metadata for a specific print job.",
        )
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

        @app.delete(
            "/opp/v1/jobs/{job_id}",
            summary="Cancel job",
            description="Cancels a queued or processing print job.",
        )
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

        @app.get(
            "/opp/v1/jobs/{job_id}/events",
            summary="Job event stream",
            description="Server-Sent Events stream for real-time status updates on a specific print job.",
        )
        async def job_events(request: Request, job_id: str) -> StreamingResponse:
            self._auth(request)
            found = any(job_id in bp.jobs for bp in self.printers.values())
            if not found:
                raise NotFound(f"Job '{job_id}' not found.")
            return StreamingResponse(
                event_stream(self.event_bus, f"job:{job_id}"),
                media_type="text/event-stream",
            )

        @app.get(
            "/opp/v1/status",
            summary="Get bridge status",
            description="Returns an aggregate status summary for all printers, including state, supply levels, and job counts.",
        )
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

        @app.get(
            "/opp/v1/status/events",
            summary="Status event stream",
            description="Server-Sent Events stream for real-time printer state change notifications across all printers.",
        )
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

    async def _fire_webhook(self, webhook_url: str, job: Job) -> None:
        """POST job completion data to the registered webhook URL."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    webhook_url,
                    json={
                        "job_id": job.id,
                        "status": job.status.value,
                        "error": job.error,
                    },
                )
        except Exception as exc:
            logger.warning("Webhook delivery failed for job %s: %s", job.id, exc)

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

            # For IPP printers, use retry logic to handle sleeping printers
            if isinstance(bp.backend, IPPBackend):
                retry = RetryPrinter(
                    bp.backend,
                    host=bp.backend._http_url.split("//")[1].split(":")[0],
                    port=int(bp.backend._http_url.split(":")[-1].split("/")[0]),
                    max_retries=3,
                    retry_delay=5.0,
                    wake_timeout=20.0,
                )
                await retry.print_with_retry(job, pdf_data)
            else:
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
            # Fire webhook if registered
            webhook_url = bp.job_webhooks.pop(job.id, "")
            if webhook_url:
                asyncio.create_task(self._fire_webhook(webhook_url, job))

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
    parser.add_argument(
        "--no-network-scan", action="store_true", help="Disable IPP network scanning",
    )
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
