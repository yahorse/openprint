from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from openprint.backend import PrintBackend
from openprint.models import (
    Capabilities,
    Job,
    PrinterState,
    SupplyLevels,
    TrayStatus,
)

logger = logging.getLogger("openprint.cups")

# Maps CUPS state reasons to OPP printer states
_CUPS_STATE_MAP = {
    "3": PrinterState.IDLE,       # idle
    "4": PrinterState.PRINTING,   # processing
    "5": PrinterState.ERROR,      # stopped
}


class CUPSBackend(PrintBackend):
    """Backend that prints through CUPS using lp/lpstat CLI commands.

    Works on any system with CUPS installed (Linux, macOS). No pycups
    dependency needed — just shell out to lp/lpstat/lpinfo which are
    always available when CUPS is installed.
    """

    def __init__(self, printer_name: str | None = None) -> None:
        self._printer = printer_name
        self._cups_job_ids: dict[str, int] = {}

    async def print_job(self, job: Job, pdf_data: bytes) -> None:
        printer = self._printer or await self._default_printer()
        if not printer:
            raise RuntimeError("No CUPS printer available")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_data)
            tmp_path = f.name

        try:
            cmd = ["lp", "-d", printer, "-n", str(job.copies)]

            if job.duplex.value == "long-edge":
                cmd += ["-o", "sides=two-sided-long-edge"]
            elif job.duplex.value == "short-edge":
                cmd += ["-o", "sides=two-sided-short-edge"]

            if not job.color:
                cmd += ["-o", "ColorModel=Gray"]

            media_map = {
                "a4": "A4", "letter": "Letter", "legal": "Legal",
                "a3": "A3", "a5": "A5", "b5": "JIS-B5",
            }
            cups_media = media_map.get(job.media, job.media)
            cmd += ["-o", f"media={cups_media}"]

            if job.pages != "all":
                cmd += ["-o", f"page-ranges={job.pages}"]

            cmd.append(tmp_path)

            result = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True
            )

            if result.returncode != 0:
                raise RuntimeError(f"lp failed: {result.stderr.strip()}")

            # lp outputs: "request id is PrinterName-123 (1 file(s))"
            request_line = result.stdout.strip()
            if "request id is" in request_line:
                cups_id_str = request_line.split("request id is")[1].split("(")[0].strip()
                cups_job_num = cups_id_str.split("-")[-1]
                try:
                    self._cups_job_ids[job.id] = int(cups_job_num)
                except ValueError:
                    pass

            logger.info("Submitted to CUPS: %s -> %s", job.id, request_line)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    async def cancel_job(self, job: Job) -> None:
        cups_id = self._cups_job_ids.get(job.id)
        if cups_id:
            await asyncio.to_thread(
                subprocess.run,
                ["cancel", str(cups_id)],
                capture_output=True,
            )

    async def get_state(self) -> PrinterState:
        printer = self._printer or await self._default_printer()
        if not printer:
            return PrinterState.OFFLINE

        result = await asyncio.to_thread(
            subprocess.run,
            ["lpstat", "-p", printer],
            capture_output=True, text=True,
        )

        output = result.stdout.lower()
        if "idle" in output:
            return PrinterState.IDLE
        elif "printing" in output or "processing" in output:
            return PrinterState.PRINTING
        elif "disabled" in output or "stopped" in output:
            return PrinterState.ERROR
        elif result.returncode != 0:
            return PrinterState.OFFLINE
        return PrinterState.IDLE

    async def get_supplies(self) -> SupplyLevels:
        printer = self._printer or await self._default_printer()
        if not printer:
            return SupplyLevels()

        supplies = SupplyLevels()

        # Try to get marker levels via lpstat -l -p
        result = await asyncio.to_thread(
            subprocess.run,
            ["lpstat", "-l", "-p", printer],
            capture_output=True, text=True,
        )

        # Parse marker-levels from the output if available
        for line in result.stdout.splitlines():
            line_lower = line.lower().strip()
            if "marker-levels" in line_lower or "ink" in line_lower or "toner" in line_lower:
                levels = [s.strip() for s in line.split(":")[-1].split(",")]
                for i, level_str in enumerate(levels):
                    try:
                        level = int(level_str.strip().rstrip("%"))
                    except ValueError:
                        continue
                    if i == 0:
                        supplies.black = level
                    elif i == 1:
                        supplies.cyan = level
                    elif i == 2:
                        supplies.magenta = level
                    elif i == 3:
                        supplies.yellow = level

        return supplies

    async def get_capabilities(self) -> Capabilities:
        printer = self._printer or await self._default_printer()
        if not printer:
            return Capabilities()

        result = await asyncio.to_thread(
            subprocess.run,
            ["lpoptions", "-p", printer, "-l"],
            capture_output=True, text=True,
        )

        color = True
        duplex = False
        media_sizes: list[str] = ["a4", "letter"]

        for line in result.stdout.splitlines():
            if line.startswith("ColorModel"):
                color = "Gray" not in line or "RGB" in line or "CMYK" in line
            elif line.startswith("Duplex") or line.startswith("sides"):
                duplex = "two-sided" in line.lower() or "duplex" in line.lower()
            elif line.startswith("PageSize") or line.startswith("media"):
                sizes = line.split(":")[1] if ":" in line else ""
                media_sizes = _parse_media_sizes(sizes)

        return Capabilities(color=color, duplex=duplex, media_sizes=media_sizes)

    async def get_printer_name(self) -> str:
        return self._printer or await self._default_printer() or "Unknown"

    async def _default_printer(self) -> str | None:
        result = await asyncio.to_thread(
            subprocess.run,
            ["lpstat", "-d"],
            capture_output=True, text=True,
        )
        # Output: "system default destination: PrinterName"
        if result.returncode == 0 and ":" in result.stdout:
            return result.stdout.split(":")[-1].strip()
        return None

    @staticmethod
    async def list_printers() -> list[dict[str, Any]]:
        """List all CUPS printers on this system."""
        result = await asyncio.to_thread(
            subprocess.run,
            ["lpstat", "-p", "-d"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return []

        printers: list[dict[str, Any]] = []
        default = None

        for line in result.stdout.splitlines():
            if line.startswith("system default destination:"):
                default = line.split(":")[-1].strip()
            elif line.startswith("printer "):
                parts = line.split()
                name = parts[1]
                is_idle = "idle" in line.lower()
                is_enabled = "enabled" in line.lower() or "idle" in line.lower()
                printers.append({
                    "name": name,
                    "state": "idle" if is_idle else "busy",
                    "enabled": is_enabled,
                    "default": name == default,
                })

        return printers


def _parse_media_sizes(raw: str) -> list[str]:
    cups_to_opp = {
        "A4": "a4", "A3": "a3", "A5": "a5",
        "Letter": "letter", "Legal": "legal",
        "Tabloid": "tabloid", "B5": "b5",
    }
    sizes: list[str] = []
    for token in raw.replace("*", "").split():
        opp_name = cups_to_opp.get(token)
        if opp_name:
            sizes.append(opp_name)
    return sizes or ["a4", "letter"]
