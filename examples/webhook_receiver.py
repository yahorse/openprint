"""Minimal webhook receiver for OpenPrint job status callbacks.

Start this server, then submit a print job with a webhook URL:

    python examples/webhook_receiver.py

    # In another terminal:
    opp print myfile.pdf --webhook http://localhost:8080/webhook

The server prints each incoming job-status payload to stdout and
shows how to verify the callback comes from your expected printer/job.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- configuration --------------------------------------------------------
WEBHOOK_HOST = "0.0.0.0"
WEBHOOK_PORT = 8080

# Optional: set these to only accept callbacks for a specific job/printer.
EXPECTED_PRINTER_ID = None  # e.g. "hp-laserjet-e82650"
EXPECTED_JOB_ID = None      # e.g. "job-20260101-001"
# --------------------------------------------------------------------------


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/webhook":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            print("[webhook] ERROR: received non-JSON body")
            self.send_response(400)
            self.end_headers()
            return

        # --- verification -------------------------------------------------
        printer_id = payload.get("printer_id")
        job_id = payload.get("job_id")

        if EXPECTED_PRINTER_ID and printer_id != EXPECTED_PRINTER_ID:
            print(f"[webhook] IGNORED: unknown printer '{printer_id}'")
            self.send_response(200)
            self.end_headers()
            return

        if EXPECTED_JOB_ID and job_id != EXPECTED_JOB_ID:
            print(f"[webhook] IGNORED: unknown job '{job_id}'")
            self.send_response(200)
            self.end_headers()
            return
        # ------------------------------------------------------------------

        status = payload.get("status", "unknown")
        pages_printed = payload.get("pages_printed", 0)
        pages_total = payload.get("pages_total", "?")

        print(f"[webhook] job={job_id}  printer={printer_id}")
        print(f"          status={status}  pages={pages_printed}/{pages_total}")

        if status == "completed":
            print("[webhook] Job finished successfully.")
        elif status == "error":
            reason = payload.get("error", "unknown error")
            print(f"[webhook] Job failed: {reason}")
        elif status == "canceled":
            print("[webhook] Job was canceled.")

        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:  # suppress default access log
        pass


def main() -> None:
    server = HTTPServer((WEBHOOK_HOST, WEBHOOK_PORT), WebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Webhook receiver listening on http://{WEBHOOK_HOST}:{WEBHOOK_PORT}/webhook")
    print("Press Ctrl-C to stop.\n")
    try:
        thread.join()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
