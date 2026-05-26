# OpenPrint Architecture

This document describes the internal architecture of the OpenPrint bridge — how its components fit together, how data flows through the system, and the design decisions behind the implementation.

---

## High-Level Overview

```
                          ┌─────────────────────────────────────────────────────┐
                          │                   Bridge process                    │
                          │                                                     │
  HTTP client             │  FastAPI/uvicorn                                    │
  (any device)  ────────► │  ┌──────────────────────────────────────────────┐  │
                          │  │  OPP HTTP API (9 endpoints)                  │  │
                          │  └──────────┬───────────────────────────────────┘  │
                          │             │                                        │
                          │  ┌──────────▼──────────────────────────────────┐   │
                          │  │  Bridge  (bridge.py)                        │   │
                          │  │  - BridgedPrinter registry                  │   │
                          │  │  - Job lifecycle management                 │   │
                          │  │  - EventBus pub/sub                         │   │
                          │  └──┬─────────┬──────────────┬─────────────────┘   │
                          │     │         │              │                      │
                          │  ┌──▼──┐  ┌───▼────┐  ┌────▼──────────────────┐   │
                          │  │ IPP │  │ CUPS   │  │ Background services   │   │
                          │  │Back │  │Backend │  │ NetworkPrinterScanner │   │
                          │  │end  │  │        │  │ CUPSWatcher           │   │
                          │  └──┬──┘  └───┬────┘  │ PrinterHealthMonitor  │   │
                          │     │         │        │ JobProgressTracker    │   │
                          │     │         │        │ JobStore (SQLite)     │   │
                          │     │         │        └───────────────────────┘   │
                          └─────┼─────────┼────────────────────────────────────┘
                                │         │
                           IPP/HTTP    CUPS socket
                                │         │
                         ┌──────▼──┐  ┌───▼──────┐
                         │Network  │  │Local CUPS │
                         │printers │  │printers   │
                         └─────────┘  └──────────┘
```

---

## Component Reference

### Bridge (`bridge.py`)

The central orchestrator. Owns the printer registry, HTTP API, and job lifecycle.

**Responsibilities:**
- Maintains `printers: dict[str, BridgedPrinter]` — the live set of known printers
- Creates and starts all background services during FastAPI lifespan startup
- Routes incoming `POST /opp/v1/jobs` requests to the correct `BridgedPrinter`
- Spawns `_process_job` as an asyncio background task per job
- Publishes all state changes to `EventBus` for SSE delivery
- Fires optional webhook `POST` on job completion or failure

**Key classes:**

`BridgedPrinter` — a thin wrapper around a backend instance, carrying:
- `printer_id` — stable identifier (CUPS name or mDNS-derived ID)
- `backend` — `IPPBackend` or `CUPSBackend`
- `source` — `"ipp"` or `"cups"`
- `jobs` / `job_data` — in-memory job state (job data is removed after processing starts)
- `cached_name`, `cached_caps`, `cached_supplies` — static info fetched once on discovery
- `job_webhooks` — per-job webhook URLs

`Bridge` — the main class. Constructed once per process; `bridge.run()` blocks.

---

### IPPBackend (`backends/ipp.py`)

Speaks raw IPP/1.1 over HTTP directly to network printers. No CUPS required.

**Key responsibilities:**
- Encodes/decodes binary IPP protocol messages (operation codes, attribute tags, TLV encoding)
- Implements `Get-Printer-Attributes`, `Print-Job`, `Create-Job`, `Send-Document`, `Cancel-Job`
- Tries HTTP before HTTPS: many consumer printers (e.g. HP DeskJet) advertise `ipps://` but drop large TLS payloads; plain HTTP on port 631 is more reliable
- Caches `_supported_formats` after first query so subsequent jobs skip the round-trip
- Implements the format fallback chain (see below)

**Format negotiation** — `print_job()` walks this chain:
1. `application/pdf` — native, preferred; single `Print-Job` request
2. `application/octet-stream` — raw binary fallback; single `Print-Job` request
3. `image/jpeg` — rasterises each page via PyMuPDF + Pillow at 150 DPI; single-page jobs use `Print-Job`, multi-page use `Create-Job` + `Send-Document` per page
4. `image/pwg-raster` — builds a PWG RaS2 stream (uncompressed RGB); sent as a single document even for multi-page (all pages in one stream)
5. Last resort — sends PDF as `application/octet-stream` regardless of advertised formats

**IPP state map:**

| IPP `printer-state` value | OPP `PrinterState` |
|---|---|
| 3 | `IDLE` |
| 4 | `PRINTING` |
| 5 | `ERROR` |
| (exception / unreachable) | `OFFLINE` |

---

### CUPSBackend (`backends/cups.py`)

Wraps a locally installed CUPS printer. Uses the CUPS command-line tools (`lp`, `lpstat`, `cancel`) or the CUPS IPP socket at `/var/run/cups/cups.sock`.

Supplies job IDs back to the bridge via `_cups_job_ids` so the `JobProgressTracker` can poll progress.

---

### NetworkPrinterScanner (`scanner.py`)

Uses `zeroconf` to browse for `_ipp._tcp.local.` and `_ipps._tcp.local.` mDNS service records. On each discovery event:

1. Resolves the service to `{host, port, hostname, uri, tls}`
2. Calls `on_found(printer_info)` callback → `Bridge._on_ipp_found`
3. On service removal, calls `on_lost(printer_id)` → `Bridge._on_ipp_lost`

The scanner runs continuously; printers that appear mid-session are added immediately without restarting the bridge.

---

### CUPSWatcher (`scanner.py`)

Polls `CUPSBackend.list_printers()` every 10 seconds (configurable). Compares the result against the known set and fires:
- `on_found(printer_info)` → `Bridge._on_cups_found` for new printers
- `on_lost(name)` → `Bridge._on_cups_lost` for removed printers

Handles the case where CUPS printers are added or removed without restarting the bridge (e.g. plugging in a USB printer).

---

### PrinterHealthMonitor (`resilience.py`)

Runs a background loop that pings every registered IPP printer every 30 seconds via a lightweight `Get-Printer-Attributes` request.

- Detects printers that go offline between jobs
- Re-resolves mDNS hostnames to catch IP changes
- Fires `on_health_change(printer_id, state)` → `Bridge._on_health_change`
- When a printer comes back online (`state == "online"` or `"idle"`), the bridge reschedules any jobs that are still in `QUEUED` status for that printer

---

### RetryPrinter (`resilience.py`)

Wraps `IPPBackend` with retry logic. Used by `Bridge._process_job` for all IPP printer jobs.

- Attempts `IPPBackend.print_job()` up to `max_retries` times (default: 3)
- Waits `retry_delay` seconds (default: 5) between attempts with exponential backoff
- Before the first retry, attempts Wake-on-LAN if a MAC address is known, then waits `wake_timeout` seconds (default: 20) for the printer to come online
- On all retries exhausted, re-raises the last exception

---

### EventBus (`status.py`)

Async pub/sub hub for Server-Sent Events.

- Each channel is identified by a string key: `"job:{job_id}"` or `"printer:status"`
- `publish(channel, event_type, data)` — broadcasts a JSON-encoded SSE frame to all subscribers on that channel
- `event_stream(bus, channel)` — async generator consumed by FastAPI's `StreamingResponse`; yields SSE-formatted bytes
- `close_channel(channel)` — signals all subscribers to close the connection (sent when a job reaches a terminal state)

SSE format emitted:

```
event: status
data: {"status": "printing"}

event: complete
data: {"status": "completed", "pages_printed": 5}
```

---

### JobStore (`store.py`)

SQLite-backed persistence at `~/.openprint/jobs.db`. Stores job metadata and status across bridge restarts.

Operations:
- `save(job, printer_id)` — insert new job row
- `update_status(job_id, status, pages_printed, error)` — update existing row
- `get(job_id)` — fetch single job
- `list_jobs(printer, status, limit)` — paginated query with optional filters

When the store is enabled (default), the `/opp/v1/jobs` endpoint queries it rather than scanning in-memory state, so job history survives restarts.

---

### JobProgressTracker (`progress.py`)

Polls `lpstat -l -j {cups_job_id}` on an interval for CUPS-backed jobs. Parses the output to extract pages-printed and job state, then fires `on_progress(job_id, status, pages)` callbacks.

Used only for CUPS backend jobs. IPP backend jobs have no equivalent polling mechanism; their completion is inferred from the `Print-Job` response.

---

### PrinterAdvertiser (`discovery.py`)

After discovering a printer, the bridge can advertise it on the local network via mDNS so that other OPP clients can find it without manual configuration. Uses `zeroconf` to register a `_opp._tcp.local.` service record.

---

## Data Flow: Print Job

```
Client                    Bridge                      IPPBackend / CUPSBackend
  │                         │                                │
  │  POST /opp/v1/jobs      │                                │
  │  (multipart/form-data)  │                                │
  │ ───────────────────────►│                                │
  │                         │  validate PDF                  │
  │                         │  create Job object             │
  │                         │  store in JobStore             │
  │                         │  spawn _process_job task       │
  │  201 { id, status }     │                                │
  │ ◄───────────────────────│                                │
  │                         │                                │
  │  GET /opp/v1/jobs/{id}  │                                │
  │  /events  (SSE)         │                                │
  │ ───────────────────────►│                                │
  │                         │                                │
  │            [background] │                                │
  │                         │  status → PROCESSING           │
  │                         │  EventBus.publish("processing")│
  │  ◄── SSE: processing ───│                                │
  │                         │  status → PRINTING             │
  │                         │  EventBus.publish("printing")  │
  │  ◄── SSE: printing ─────│                                │
  │                         │                                │
  │                         │  (IPP path)                    │
  │                         │  RetryPrinter.print_with_retry │
  │                         │ ──────────────────────────────►│
  │                         │                                │  IPP Print-Job
  │                         │                                │ ────────────►
  │                         │                                │  (printer)
  │                         │                                │ ◄────────────
  │                         │ ◄──────────────────────────────│
  │                         │                                │
  │                         │  status → COMPLETED            │
  │                         │  JobStore.update_status        │
  │                         │  EventBus.publish("complete")  │
  │  ◄── SSE: complete ─────│                                │
  │                         │  fire webhook (if registered)  │
  │                         │  close SSE channel             │
```

---

## Data Flow: Printer Discovery (IPP via mDNS)

```
NetworkPrinterScanner          Bridge                    IPPBackend
        │                        │                           │
        │  mDNS browse           │                           │
        │  _ipp._tcp.local.      │                           │
        │                        │                           │
        │  service found         │                           │
        │  resolve host/port/uri │                           │
        │  ──────────────────────►                           │
        │  _on_ipp_found()       │                           │
        │                        │  create IPPBackend        │
        │                        │  create BridgedPrinter    │
        │                        │  add to printers registry │
        │                        │                           │
        │                        │  spawn _prefetch_printer_info task
        │                        │ ─────────────────────────►│
        │                        │                           │  Get-Printer-Attributes
        │                        │                           │ ──────────────► (printer)
        │                        │                           │ ◄──────────────
        │                        │  cache: name, caps,       │
        │                        │  supported_formats,       │
        │                        │  supply levels            │
        │                        │ ◄─────────────────────────│
        │                        │                           │
        │                        │  register with health monitor
        │                        │  advertise via mDNS (optional)
        │                        │  publish printer:status event
```

---

## Caching Strategy

OpenPrint distinguishes between data that rarely changes (cached once) and data that changes frequently (fetched live).

**Cached once on discovery (`_prefetch_printer_info`):**
- `cached_name` — printer display name from `printer-name` IPP attribute
- `cached_caps` — `Capabilities(color, duplex)` from `color-supported` and `sides-supported`
- `_supported_formats` — list of MIME types from `document-format-supported`
- `cached_supplies` — supply levels at discovery time (used only to emit low-ink warnings at startup)

**Fetched live (per request):**
- Printer state (`printer-state`) — fetched on every `GET /opp/v1/printers` call and before job creation
- Supply levels — fetched live on `GET /opp/v1/printers/{id}/supplies` and on job creation (for low-ink warnings in the response)

This means the `/opp/v1/printers` list endpoint is fast (no network I/O for name/caps), while supply and state calls hit the printer in real time.

---

## Technology Stack

| Layer | Technology |
|---|---|
| HTTP framework | FastAPI + uvicorn (ASGI) |
| HTTP client (IPP) | httpx (async) |
| Printer protocol | Raw IPP/1.1 over HTTP (hand-rolled encoder/decoder) |
| PDF rasterisation | PyMuPDF (`fitz`) + Pillow (optional, `openprint[pdf]`) |
| mDNS | zeroconf |
| Job persistence | SQLite (stdlib `sqlite3`) |
| Data models | Pydantic v2 |
| TLS | uvicorn SSL + optional auto-generated self-signed cert |
| Python version | 3.10+ |
