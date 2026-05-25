from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any

from openprint.models import Job, JobStatus

logger = logging.getLogger("openprint.progress")


class JobProgressTracker:
    """Polls CUPS for real job progress updates."""

    def __init__(self, poll_interval: float = 2.0) -> None:
        self._interval = poll_interval
        self._tracked: dict[str, tuple[Job, int]] = {}  # opp_job_id -> (Job, cups_job_id)
        self._task: asyncio.Task[None] | None = None
        self._callbacks: dict[str, Any] = {}

    def track(
        self,
        job: Job,
        cups_job_id: int,
        on_progress: Any = None,
    ) -> None:
        self._tracked[job.id] = (job, cups_job_id)
        if on_progress:
            self._callbacks[job.id] = on_progress

    def untrack(self, job_id: str) -> None:
        self._tracked.pop(job_id, None)
        self._callbacks.pop(job_id, None)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._check_jobs()
            except Exception as exc:
                logger.warning("Progress poll error: %s", exc)
            await asyncio.sleep(self._interval)

    async def _check_jobs(self) -> None:
        if not self._tracked:
            return

        result = await asyncio.to_thread(
            subprocess.run,
            ["lpstat", "-o", "-l"],
            capture_output=True, text=True,
        )

        active_cups_jobs = self._parse_lpstat(result.stdout)

        for opp_id, (job, cups_id) in list(self._tracked.items()):
            cups_status = active_cups_jobs.get(cups_id)

            if cups_status is None and job.status in (
                JobStatus.PROCESSING, JobStatus.PRINTING
            ):
                job.status = JobStatus.COMPLETED
                job.pages_printed = job.pages_total
                logger.info("Job %s completed (CUPS job %d done)", opp_id, cups_id)
                callback = self._callbacks.get(opp_id)
                if callback:
                    await callback(opp_id, "completed", job.pages_total)
                self.untrack(opp_id)

            elif cups_status == "held" or cups_status == "stopped":
                if job.status != JobStatus.ERROR:
                    job.status = JobStatus.ERROR
                    job.error = f"CUPS job {cups_id} {cups_status}"
                    callback = self._callbacks.get(opp_id)
                    if callback:
                        await callback(opp_id, "error", 0)

    @staticmethod
    def _parse_lpstat(output: str) -> dict[int, str]:
        jobs: dict[int, str] = {}
        for line in output.splitlines():
            # Format: "PrinterName-123    user  1024  Mon 01 Jan 2026 10:00:00"
            parts = line.strip().split()
            if not parts:
                continue
            job_ref = parts[0]
            if "-" in job_ref:
                try:
                    job_num = int(job_ref.rsplit("-", 1)[-1])
                    status = "active"
                    lower = line.lower()
                    if "held" in lower:
                        status = "held"
                    elif "stopped" in lower:
                        status = "stopped"
                    jobs[job_num] = status
                except ValueError:
                    continue
        return jobs
