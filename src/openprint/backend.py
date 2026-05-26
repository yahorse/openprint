from __future__ import annotations

import abc

from openprint.models import Capabilities, Job, PrinterState, SupplyLevels


class PrintBackend(abc.ABC):
    """Abstract backend that actually sends data to a printer."""

    @abc.abstractmethod
    async def print_job(self, job: Job, pdf_data: bytes) -> None:
        """Send a PDF to the physical printer."""

    @abc.abstractmethod
    async def cancel_job(self, job: Job) -> None:
        """Cancel a job on the physical printer."""

    @abc.abstractmethod
    async def get_state(self) -> PrinterState:
        """Get the current printer state."""

    @abc.abstractmethod
    async def get_supplies(self) -> SupplyLevels:
        """Get supply levels (ink, paper)."""

    @abc.abstractmethod
    async def get_capabilities(self) -> Capabilities:
        """Get printer capabilities."""

    @abc.abstractmethod
    async def get_printer_name(self) -> str:
        """Get the printer's display name."""
