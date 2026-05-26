"""Poll an OpenPrint job until it reaches a terminal state.

Submits a PDF to the first discovered printer, then polls
GET /opp/v1/jobs/{id} every 2 seconds, printing live progress.

Usage:
    python examples/job_polling.py <file.pdf> [printer-id]

Requires only the stdlib (urllib).  If you prefer httpx, swap the
_get() / _post_file() helpers for httpx equivalents.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

POLL_INTERVAL = 2  # seconds between status checks
BASE_URL = "http://localhost:631"  # change to your OPP server


# ---------------------------------------------------------------------------
# Minimal HTTP helpers using stdlib urllib
# ---------------------------------------------------------------------------

def _get(url: str) -> dict:
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def _post_file(url: str, file_path: Path, printer_id: str) -> dict:
    """Submit a print job via multipart/form-data (minimal implementation)."""
    boundary = "----OPPBoundary"
    file_bytes = file_path.read_bytes()
    filename = file_path.name

    body_parts = [
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="printer"\r\n\r\n'
        f"{printer_id}\r\n",
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n",
    ]
    body = b"".join(p.encode() for p in body_parts) + file_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------

TERMINAL_STATES = {"completed", "error", "canceled"}


def poll_job(job_id: str) -> None:
    url = f"{BASE_URL}/opp/v1/jobs/{job_id}"
    print(f"Polling job {job_id} every {POLL_INTERVAL}s ...\n")

    while True:
        try:
            job = _get(url)
        except urllib.error.URLError as exc:
            print(f"  [poll] request failed: {exc} — retrying")
            time.sleep(POLL_INTERVAL)
            continue

        status = job.get("status", "unknown")
        pages_printed = job.get("pages_printed", 0)
        pages_total = job.get("pages_total") or "?"

        bar = _progress_bar(pages_printed, job.get("pages_total") or 0)
        print(f"  status={status:12s}  pages={pages_printed}/{pages_total}  {bar}")

        if status == "completed":
            print("\nJob completed successfully.")
            break
        elif status == "error":
            reason = job.get("error", "unknown error")
            print(f"\nJob failed: {reason}")
            sys.exit(1)
        elif status == "canceled":
            print("\nJob was canceled.")
            sys.exit(1)

        time.sleep(POLL_INTERVAL)


def _progress_bar(done: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return "[" + " " * width + "]"
    filled = min(width, int(width * done / total))
    return "[" + "#" * filled + " " * (width - filled) + "]"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python job_polling.py <file.pdf> [printer-id]")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    if not file_path.exists():
        print(f"File not found: {file_path}")
        sys.exit(1)

    # Discover printers and pick the first (or the one specified)
    printers = _get(f"{BASE_URL}/opp/v1/printers")
    if not printers:
        print("No printers found. Is the OPP server running?")
        sys.exit(1)

    if len(sys.argv) >= 3:
        printer_id = sys.argv[2]
        match = next((p for p in printers if p["id"] == printer_id), None)
        if match is None:
            print(f"Printer '{printer_id}' not found.")
            sys.exit(1)
        printer = match
    else:
        printer = printers[0]

    print(f"Submitting '{file_path.name}' to {printer['name']} ...")
    job = _post_file(f"{BASE_URL}/opp/v1/jobs", file_path, printer["id"])
    print(f"Job submitted: id={job['id']}  initial status={job['status']}\n")

    poll_job(job["id"])


if __name__ == "__main__":
    main()
