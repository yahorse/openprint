"""OpenPrint MCP Server — expose printing capabilities to AI assistants."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from openprint.client import Client
from openprint.models import DuplexMode
from openprint.testkit import test_printer as run_printer_test

mcp = FastMCP(
    "openprint",
    instructions=(
        "OpenPrint MCP server for driverless printing over HTTP. "
        "Use discover_printers to find printers on the network, then print_document "
        "to send PDFs. Use get_printer_status or get_job_status to monitor state."
    ),
)

_client: Client | None = None


def _get_client(printer_url: str | None = None, auth_token: str | None = None) -> Client:
    global _client
    if printer_url:
        return Client(base_url=printer_url, auth_token=auth_token)
    if _client is None:
        _client = Client(auth_token=auth_token)
    return _client


@mcp.tool()
def discover_printers(timeout: float = 3.0) -> str:
    """Discover OpenPrint-compatible printers on the local network via mDNS.

    Args:
        timeout: How long to scan in seconds (default 3.0).

    Returns:
        JSON list of discovered printers with name, host, port, color, and duplex support.
    """
    client = _get_client()
    printers = client.discover(timeout=timeout)
    if not printers:
        return json.dumps({
            "printers": [],
            "message": "No printers found. Ensure a printer or bridge is running.",
        })
    return json.dumps({"printers": printers, "count": len(printers)}, indent=2)


@mcp.tool()
def print_document(
    file_path: str,
    printer_url: str | None = None,
    copies: int = 1,
    color: bool = True,
    duplex: str = "none",
    media: str = "a4",
    pages: str = "all",
    priority: int = 50,
    auth_token: str | None = None,
) -> str:
    """Print a PDF document to an OpenPrint printer.

    Args:
        file_path: Path to the PDF file to print.
        printer_url: Printer URL (e.g. http://192.168.1.100:631).
            If omitted, uses last discovered printer.
        copies: Number of copies (default 1).
        color: Print in color (default True). Set False for black-and-white.
        duplex: Duplex mode: "none", "long-edge", or "short-edge" (default "none").
        media: Paper size: "a4", "letter", etc. (default "a4").
        pages: Page range: "all", "1-3", "1,3,5" (default "all").
        priority: Print priority 1-100 (default 50).
        auth_token: Bearer token if the printer requires authentication.

    Returns:
        JSON with job ID and status.
    """
    path = Path(file_path)
    if not path.exists():
        return json.dumps({"error": f"File not found: {file_path}"})
    if not path.suffix.lower() == ".pdf":
        return json.dumps({"error": f"Only PDF files are supported, got: {path.suffix}"})

    client = _get_client(printer_url=printer_url, auth_token=auth_token)
    try:
        result = client.print(
            file_path=file_path,
            copies=copies,
            color=color,
            duplex=DuplexMode(duplex),
            media=media,
            pages=pages,
            priority=priority,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def get_printer_info(
    printer_url: str | None = None,
    auth_token: str | None = None,
) -> str:
    """Get information about a printer including name, capabilities, and status.

    Args:
        printer_url: Printer URL. If omitted, uses last discovered printer.
        auth_token: Bearer token if required.

    Returns:
        JSON with printer name, manufacturer, model, capabilities
        (color, duplex, media sizes), and current state.
    """
    client = _get_client(printer_url=printer_url, auth_token=auth_token)
    try:
        info = client.printer_info()
        return json.dumps(info, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def get_printer_status(
    printer_url: str | None = None,
    auth_token: str | None = None,
) -> str:
    """Get the current status of a printer including state, supply levels, and job counts.

    Args:
        printer_url: Printer URL. If omitted, uses last discovered printer.
        auth_token: Bearer token if required.

    Returns:
        JSON with printer state (idle/printing/error/offline),
        supply levels (ink/toner percentages), error list, and job counts.
    """
    client = _get_client(printer_url=printer_url, auth_token=auth_token)
    try:
        status = client.printer_status()
        return json.dumps(status, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def list_jobs(
    printer_url: str | None = None,
    status: str | None = None,
    limit: int = 50,
    auth_token: str | None = None,
) -> str:
    """List print jobs, optionally filtered by status.

    Args:
        printer_url: Printer URL. If omitted, uses last discovered printer.
        status: Filter by job status: "queued", "processing", "printing",
            "completed", "canceled", "error". Omit for all.
        limit: Maximum number of jobs to return (default 50).
        auth_token: Bearer token if required.

    Returns:
        JSON with list of jobs and total count.
    """
    client = _get_client(printer_url=printer_url, auth_token=auth_token)
    try:
        result = client.list_jobs(status=status, limit=limit)
        return json.dumps(result, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def get_job_status(
    job_id: str,
    printer_url: str | None = None,
    auth_token: str | None = None,
) -> str:
    """Get the status of a specific print job.

    Args:
        job_id: The job ID returned when the job was submitted.
        printer_url: Printer URL. If omitted, uses last discovered printer.
        auth_token: Bearer token if required.

    Returns:
        JSON with job details: status, pages printed/total, creation time, and any errors.
    """
    client = _get_client(printer_url=printer_url, auth_token=auth_token)
    try:
        result = client.job_status(job_id)
        return json.dumps(result, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def cancel_job(
    job_id: str,
    printer_url: str | None = None,
    auth_token: str | None = None,
) -> str:
    """Cancel a print job that is queued or processing.

    Args:
        job_id: The job ID to cancel.
        printer_url: Printer URL. If omitted, uses last discovered printer.
        auth_token: Bearer token if required.

    Returns:
        JSON confirming cancellation or error message.
    """
    client = _get_client(printer_url=printer_url, auth_token=auth_token)
    try:
        result = client.cancel_job(job_id)
        return json.dumps(result, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool(name="test_printer")
def check_printer_compatibility(host: str, port: int = 631) -> str:
    """Test whether a printer is compatible with OpenPrint.

    Runs a full compatibility test: network reachability, HTTP response, IPP protocol support,
    PDF support, and driverless printing capability.

    Args:
        host: Printer IP address or hostname.
        port: Printer port (default 631).

    Returns:
        JSON with test results for each check and overall compatibility verdict.
    """
    try:
        results = asyncio.run(run_printer_test(host, port))
        return json.dumps(results, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc), "host": host, "port": port})


@mcp.resource("openprint://protocol")
def protocol_summary() -> str:
    """OpenPrint protocol summary and available endpoints."""
    return json.dumps({
        "protocol": "OpenPrint Protocol v1",
        "description": "Driverless HTTP/REST printing — send a PDF over HTTP, get a print.",
        "endpoints": {
            "GET /opp/v1/printer": "Get printer info and capabilities",
            "POST /opp/v1/jobs": "Submit a print job (multipart: file + options)",
            "GET /opp/v1/jobs": "List print jobs (query: status, limit)",
            "GET /opp/v1/jobs/{id}": "Get job status",
            "DELETE /opp/v1/jobs/{id}": "Cancel a job",
            "GET /opp/v1/status": "Get printer status and supply levels",
            "GET /opp/v1/printers": "List all printers (bridge mode only)",
        },
        "discovery": "mDNS service type: _opp._tcp.local.",
        "default_port": 631,
    }, indent=2)


@mcp.resource("openprint://duplex-modes")
def duplex_modes() -> str:
    """Available duplex (two-sided printing) modes."""
    return json.dumps({
        "modes": {
            "none": "Single-sided printing",
            "long-edge": "Two-sided, flip on long edge (standard book-style)",
            "short-edge": "Two-sided, flip on short edge (calendar-style)",
        },
    }, indent=2)


@mcp.resource("openprint://media-sizes")
def media_sizes() -> str:
    """Common paper sizes supported by OpenPrint."""
    return json.dumps({
        "sizes": {
            "a4": "210 x 297 mm (international standard)",
            "letter": "8.5 x 11 in (US standard)",
            "legal": "8.5 x 14 in",
            "a3": "297 x 420 mm",
            "a5": "148 x 210 mm",
            "b5": "176 x 250 mm",
            "tabloid": "11 x 17 in",
        },
        "default": "a4",
    }, indent=2)


def main() -> None:
    """Run the OpenPrint MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
