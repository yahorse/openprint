"""OpenPrint MCP Server — expose printing capabilities to AI assistants."""

from __future__ import annotations

import asyncio
import json

from mcp.server.fastmcp import FastMCP

from openprint import integrations
from openprint.client import Client
from openprint.integrations import OPPConfig
from openprint.testkit import test_printer as run_printer_test

mcp = FastMCP(
    "openprint",
    instructions=(
        "OpenPrint MCP server for driverless printing. print_document accepts PDFs "
        "as well as HTML, text, and images (auto-converted to PDF) and prints to "
        "either OPP servers or raw IPP printers — it rasterises for printers with no "
        "PDF interpreter (e.g. HP DeskJet). It discovers printers via mDNS and falls "
        "back to a saved default (set_default_printer). For a printer reachable only "
        "over Wi-Fi Direct, use connect_wifi_direct first, then print, then "
        "restore_wifi. Use get_printer_status or get_job_status to monitor state."
    ),
)

_config = OPPConfig()

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
        saved = _config.default_printer
        return json.dumps({
            "printers": [],
            "saved_default": saved,
            "message": (
                "No printers found via mDNS. "
                + (
                    f"A default printer is saved ({saved}); print_document will use it."
                    if saved
                    else "No default saved either — set one with set_default_printer, "
                    "or pass printer_url to print_document."
                )
            ),
        })
    return json.dumps(
        {"printers": printers, "count": len(printers), "saved_default": _config.default_printer},
        indent=2,
    )


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
    """Print a document to a printer — handles everything end to end.

    Accepts PDF, HTML (.html/.htm), text (.txt/.md/.csv/.log), and images
    (.png/.jpg/...); non-PDFs are rendered to PDF automatically. Picks the
    target by: explicit printer_url, then mDNS discovery, then the saved default
    printer (see set_default_printer). Raw IPP printers (ipp://... or a bare
    host/IP) are driven directly and rasterised when they lack a PDF interpreter
    (e.g. HP DeskJet); OPP servers/bridges (http://...) use the OPP protocol.
    On success the chosen printer is remembered as the default.

    Args:
        file_path: Path to the file to print (PDF/HTML/text/image).
        printer_url: Printer target. Accepts "http://host:631" (OPP),
            "ipp://host:631/ipp/print" (raw IPP), or a bare "host" / "host:port"
            (treated as raw IPP). If omitted, discovery then saved default.
        copies: Number of copies (default 1).
        color: Print in color (default True). Set False for black-and-white.
        duplex: Duplex mode: "none", "long-edge", or "short-edge" (default "none").
        media: Paper size: "a4", "letter", etc. (default "a4").
        pages: Page range: "all", "1-3", "1,3,5" (default "all"). OPP targets only.
        priority: Print priority 1-100 (default 50). OPP targets only.
        auth_token: Bearer token if an OPP printer requires authentication.

    Returns:
        JSON with job id, transport ("ipp"/"opp"), chosen printer, and status.
    """
    try:
        result = integrations.print_file(
            file_path,
            printer_url=printer_url,
            copies=copies,
            color=color,
            duplex=duplex,
            media=media,
            pages=pages,
            priority=priority,
            auth_token=auth_token,
            config=_config,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc), "file_path": file_path})


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


@mcp.tool()
def set_default_printer(printer_url: str) -> str:
    """Save a default printer used when discovery finds nothing.

    Args:
        printer_url: "http://host:631" (OPP), "ipp://host:631/ipp/print" (raw IPP),
            or a bare "host"/"host:port" (treated as raw IPP).

    Returns:
        JSON confirming the saved default.
    """
    target = integrations._normalize_target(printer_url)
    _config.default_printer = target
    return json.dumps({"saved": True, "default_printer": target}, indent=2)


@mcp.tool()
def get_default_printer() -> str:
    """Get the currently saved default printer, if any."""
    return json.dumps({"default_printer": _config.default_printer}, indent=2)


@mcp.tool()
def connect_wifi_direct(ssid: str, password: str | None = None) -> str:
    """Connect this PC to a printer's Wi-Fi Direct network (Windows only).

    Many consumer printers (e.g. HP DeskJet) are reachable only over their own
    Wi-Fi Direct SoftAP. This adds a saved WLAN profile and connects, remembering
    the password so future calls need no password. NOTE: connecting drops your
    normal internet/Wi-Fi; the previously-connected SSID is returned as
    "previous_ssid" so you can restore_wifi afterwards.

    Args:
        ssid: The Wi-Fi Direct SSID, e.g. "DIRECT-52-HP DeskJet 2900 series".
        password: WPA2 passphrase. Optional if previously remembered.

    Returns:
        JSON with connection result and previous_ssid for later restore.
    """
    try:
        return json.dumps(
            integrations.connect_wifi_direct(ssid, password, config=_config), indent=2
        )
    except Exception as exc:
        return json.dumps({"error": str(exc), "ssid": ssid})


@mcp.tool()
def restore_wifi(ssid: str) -> str:
    """Reconnect to a normal Wi-Fi network after Wi-Fi Direct printing (Windows only).

    Args:
        ssid: The SSID to reconnect to (typically connect_wifi_direct's previous_ssid).

    Returns:
        JSON with the restore result.
    """
    try:
        return json.dumps(integrations.restore_wifi(ssid), indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc), "ssid": ssid})


@mcp.tool()
def ensure_bridge(url: str = "http://127.0.0.1:631") -> str:
    """Ensure an OPP bridge is running locally (best effort).

    Starts `opp bridge` if one isn't already answering. Bridging relies on CUPS,
    so on Windows this typically reports no bridged printers — prefer a direct
    IPP printer URL there.

    Args:
        url: Where to look for / expose the bridge (default http://127.0.0.1:631).

    Returns:
        JSON describing whether a bridge is running.
    """
    try:
        return json.dumps(integrations.ensure_bridge(url), indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc), "url": url})


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


# ---------------------------------------------------------------------------
# Prompts (skills) — guided multi-step workflows
# ---------------------------------------------------------------------------


@mcp.prompt()
def setup_printer() -> list[dict[str, str]]:
    """Walk through discovering and verifying a new printer."""
    return [
        {
            "role": "user",
            "content": (
                "Help me set up a printer with OpenPrint. "
                "First, discover printers on my network using discover_printers. "
                "If none are found, ask me for the printer's IP address and run "
                "test_printer against it to check compatibility. "
                "Once we have a working printer, show me its capabilities with "
                "get_printer_info and confirm it's ready to use. "
                "If the printer isn't directly compatible, suggest running "
                "'opp bridge' to bridge it through CUPS."
            ),
        },
    ]


@mcp.prompt()
def print_file(file_path: str) -> list[dict[str, str]]:
    """Guide through printing a file with the right settings."""
    return [
        {
            "role": "user",
            "content": (
                f"I want to print: {file_path}\n\n"
                "First, check if a printer is available using discover_printers. "
                "Then get the printer's capabilities with get_printer_info so you "
                "know what it supports (color, duplex, media sizes). "
                "Ask me about any preferences: color vs black-and-white, "
                "single vs double-sided, paper size, number of copies, "
                "and page range. Use sensible defaults based on the printer's "
                "capabilities. Then print the document with print_document "
                "and report the job ID. Finally check the job status with "
                "get_job_status to confirm it was accepted."
            ),
        },
    ]


@mcp.prompt()
def troubleshoot_printer(printer_url: str = "") -> list[dict[str, str]]:
    """Diagnose and fix printer issues."""
    target = f" at {printer_url}" if printer_url else ""
    return [
        {
            "role": "user",
            "content": (
                f"My printer{target} isn't working. Help me diagnose it.\n\n"
                "Run through these steps:\n"
                "1. If I gave a URL, check get_printer_status for the state "
                "and any errors. If no URL, run discover_printers to find it.\n"
                "2. If the printer is offline or unreachable, run test_printer "
                "with its IP to check network, HTTP, and IPP connectivity.\n"
                "3. Check supply levels — low ink/toner or paper can cause "
                "printers to refuse jobs.\n"
                "4. Check list_jobs for stuck jobs (status 'error' or "
                "'processing' for too long) and offer to cancel them.\n"
                "5. Summarize what you found and suggest fixes. Common ones: "
                "restart the bridge, power-cycle the printer, clear the queue, "
                "or check the network cable/wifi."
            ),
        },
    ]


@mcp.prompt()
def check_all_printers() -> list[dict[str, str]]:
    """Get a status overview of all printers on the network."""
    return [
        {
            "role": "user",
            "content": (
                "Give me a status report on all my printers.\n\n"
                "1. Run discover_printers to find all available printers.\n"
                "2. For each printer found, call get_printer_status to get "
                "its state and supply levels.\n"
                "3. Also call list_jobs for each to see if anything is "
                "queued or stuck.\n"
                "4. Present a summary table showing each printer's name, "
                "state (idle/printing/error/offline), ink/toner levels, "
                "and any queued or errored jobs. Flag anything that needs "
                "attention."
            ),
        },
    ]


@mcp.prompt()
def manage_queue(printer_url: str = "") -> list[dict[str, str]]:
    """Review and manage the print queue."""
    target = f"at {printer_url} " if printer_url else ""
    return [
        {
            "role": "user",
            "content": (
                f"Show me the print queue {target}and help me manage it.\n\n"
                "1. Use list_jobs to get all jobs. Show them in a table "
                "with ID, status, pages, and creation time.\n"
                "2. Highlight any jobs that are in 'error' state or have "
                "been 'processing' unusually long.\n"
                "3. Ask if I want to cancel any stuck or errored jobs.\n"
                "4. If I say yes, use cancel_job for each one and confirm "
                "the cancellation."
            ),
        },
    ]


def main() -> None:
    """Run the OpenPrint MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
