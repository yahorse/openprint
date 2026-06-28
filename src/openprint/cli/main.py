"""OpenPrint CLI — print from the terminal like it's 2026."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from openprint.client import Client
from openprint.discovery import PrinterScanner


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="opp",
        description="OpenPrint — print files without drivers",
    )
    sub = parser.add_subparsers(dest="command")

    # opp print
    p_print = sub.add_parser("print", help="Print a file (PDF/HTML/text/image)")
    p_print.add_argument("file", help="File to print (PDF, HTML, text, or image)")
    p_print.add_argument(
        "-p", "--printer",
        help="Printer name, URL (http/ipp), or host/IP. Omit to discover or use the saved default.",
    )
    p_print.add_argument("-n", "--copies", type=int, default=1)
    p_print.add_argument("--bw", action="store_true", help="Print in grayscale")
    p_print.add_argument("--duplex", choices=["none", "long-edge", "short-edge"], default="none")
    p_print.add_argument("--media", default="a4", help="Paper size (a4, letter, legal)")
    p_print.add_argument("--pages", default="all", help="Page range: all, 1-3, 1,3,5")

    # opp discover
    sub.add_parser("discover", help="Find printers on the network")

    # opp status
    p_status = sub.add_parser("status", help="Check printer status")
    p_status.add_argument("-p", "--printer", help="Printer URL")

    # opp test
    p_test = sub.add_parser("test", help="Test if a printer works with OPP")
    p_test.add_argument("target", nargs="?", help="Printer IP, hostname, or URL")

    # opp jobs
    p_jobs = sub.add_parser("jobs", help="List recent print jobs")
    p_jobs.add_argument("-p", "--printer", help="Printer URL")

    # opp server
    p_server = sub.add_parser("server", help="Run the OPP server")
    p_server.add_argument("--port", type=int, default=631)

    # opp bridge
    p_bridge = sub.add_parser("bridge", help="Bridge CUPS printers to OPP")
    p_bridge.add_argument("--port", type=int, default=631)
    p_bridge.add_argument("--tls-auto", action="store_true")

    args = parser.parse_args()

    if args.command == "print":
        cmd_print(args)
    elif args.command == "discover":
        cmd_discover()
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "test":
        cmd_test(args)
    elif args.command == "jobs":
        cmd_jobs(args)
    elif args.command == "server":
        cmd_server(args)
    elif args.command == "bridge":
        cmd_bridge(args)
    else:
        parser.print_help()


def _looks_like_target(value: str) -> bool:
    """True if *value* is a URL or host/IP rather than a discoverable printer name."""
    if value.startswith(("http://", "https://", "ipp://", "ipps://")):
        return True
    # IPs and hostnames have a dot or an explicit port; printer names usually
    # have spaces and neither. "printer.local" / "192.168.1.5" / "host:631" -> target.
    return " " not in value and ("." in value or ":" in value)


def _resolve_printer_arg(printer: str | None) -> str | None:
    """Map a --printer value to a target URL for the integrations layer.

    URLs and host/IPs pass through untouched. A bare printer *name* is looked up
    via mDNS discovery and resolved to its http URL (preserving older behaviour).
    Returns None when nothing was given, letting print_file discover or fall back
    to the saved default.
    """
    if not printer:
        return None
    if _looks_like_target(printer):
        return printer
    # Treat as a discoverable printer name.
    client = Client()
    printers = client.discover()
    match = [p for p in printers if p["name"] == printer]
    if not match:
        print(f"Printer '{printer}' not found. Run: opp discover")
        sys.exit(1)
    return f"http://{match[0]['host']}:{match[0]['port']}"


def cmd_print(args: Any) -> None:
    from openprint import integrations

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    target = _resolve_printer_arg(args.printer)
    if target is None and not args.printer:
        print("No printer given — discovering / using saved default...")

    print(f"Printing {path.name}...")
    try:
        job = integrations.print_file(
            path,
            printer_url=target,
            copies=args.copies,
            color=not args.bw,
            duplex=args.duplex,
            media=args.media,
            pages=args.pages,
        )
    except Exception as exc:
        print(f"Print failed: {exc}")
        sys.exit(1)

    where = job.get("printer", "printer")
    job_id = job.get("id") or job.get("job_id", "?")
    status = job.get("status", "submitted")
    print(f"Done. Job {job_id} — {status}  ({where})")


def cmd_discover() -> None:
    print("Scanning for printers (3 seconds)...\n")
    scanner = PrinterScanner()
    printers = asyncio.run(scanner.scan(timeout=3.0))

    if not printers:
        print("No OPP printers found.")
        print("\nTo bridge your existing printers:  opp bridge")
        return

    for p in printers:
        color = "color" if p["color"] else "mono"
        duplex = "duplex" if p["duplex"] else "simplex"
        print(f"  {p['name']}")
        print(f"    {p['host']}:{p['port']}  {color}  {duplex}")
        print()

    print(f"Found {len(printers)} printer(s).")


def cmd_status(args: Any) -> None:
    client = _get_client(args)
    try:
        status = client.printer_status()
        print(json.dumps(status, indent=2))
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)


def cmd_test(args: Any) -> None:
    from openprint.testkit import test_printer
    target = args.target
    if not target:
        print("Scanning for printers to test...")
        scanner = PrinterScanner()
        printers = asyncio.run(scanner.scan(timeout=3.0))
        if not printers:
            print("No printers found. Provide an IP: opp test 192.168.1.100")
            sys.exit(1)
        target = printers[0]["host"]
        print(f"Testing: {printers[0]['name']} ({target})")

    asyncio.run(test_printer(target))


def cmd_jobs(args: Any) -> None:
    client = _get_client(args)
    try:
        data = client.list_jobs()
        jobs = data.get("jobs", [])
        if not jobs:
            print("No recent jobs.")
            return
        for j in jobs:
            status = j["status"]
            pages = f"{j['pages_printed']}/{j['pages_total']}"
            print(f"  {j['id']}  {status:<12}  {pages} pages")
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)


def cmd_server(args: Any) -> None:
    from openprint.server import Server
    Server(port=args.port).run()


def cmd_bridge(args: Any) -> None:
    from openprint.bridge import Bridge
    kwargs: dict[str, Any] = {"port": args.port}
    if args.tls_auto:
        from openprint.tls import generate_self_signed_cert
        cert, key = generate_self_signed_cert()
        kwargs["tls_cert"] = str(cert)
        kwargs["tls_key"] = str(key)
    Bridge(**kwargs).run()


def _get_client(args: Any) -> Client:
    url = getattr(args, "printer", None)
    if url:
        return Client(base_url=url)
    client = Client()
    printers = client.discover(timeout=2.0)
    if not printers:
        print("No printers found. Provide URL: --printer http://...")
        sys.exit(1)
    return client
