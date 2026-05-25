from __future__ import annotations

import asyncio

from openprint.backend import PrintBackend
from openprint.models import Capabilities, Job, PrinterState, SupplyLevels


class DummyBackend(PrintBackend):
    """Simulated printer for testing. Doesn't print anything."""

    def __init__(self, name: str = "Dummy Printer", delay_per_page: float = 0.5) -> None:
        self._name = name
        self._delay = delay_per_page

    async def print_job(self, job: Job, pdf_data: bytes) -> None:
        for page in range(1, job.pages_total + 1):
            await asyncio.sleep(self._delay)
            job.pages_printed = page

    async def cancel_job(self, job: Job) -> None:
        pass

    async def get_state(self) -> PrinterState:
        return PrinterState.IDLE

    async def get_supplies(self) -> SupplyLevels:
        return SupplyLevels()

    async def get_capabilities(self) -> Capabilities:
        return Capabilities()

    async def get_printer_name(self) -> str:
        return self._name
