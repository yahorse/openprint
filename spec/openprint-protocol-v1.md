# OpenPrint Protocol Specification v1

## Overview

The OpenPrint Protocol (OPP) is an HTTP/REST-based printing protocol. It enables any device to print PDF documents over a network without drivers, proprietary software, or complex configuration.

## Design Goals

1. **Simplicity** — a single HTTP POST prints a document.
2. **PDF-native** — PDF is the wire format. No intermediate raster, no PPD files.
3. **Discoverable** — printers announce themselves via mDNS/DNS-SD.
4. **Observable** — real-time status via Server-Sent Events.
5. **Secure** — optional API key authentication, TLS support.

## Base URL

All endpoints live under `/opp/v1/`. Servers MUST listen on port 631 by default. Servers MAY support other ports.

## Content Types

- Request/response bodies: `application/json`
- File uploads: `multipart/form-data`
- Event streams: `text/event-stream`

## Authentication

Authentication is optional. When enabled, clients send a Bearer token:

```
Authorization: Bearer <token>
```

Unauthenticated requests to a protected server return `401 Unauthorized`.

## Discovery

OPP printers register a DNS-SD service:

- Service type: `_opp._tcp.local.`
- TXT records:
  - `v=1` — protocol version
  - `name=<printer name>` — human-readable name
  - `color=<true|false>` — color support
  - `duplex=<true|false>` — duplex support
  - `pdf=<version>` — maximum PDF version supported (e.g., `2.0`)

## Endpoints

### GET /opp/v1/printer

Returns printer identity and capabilities.

**Response** `200 OK`:

```json
{
  "name": "Office Printer",
  "manufacturer": "Generic",
  "model": "OPP Reference Server",
  "protocol_version": "1.0",
  "capabilities": {
    "color": true,
    "duplex": true,
    "media_sizes": ["a4", "letter", "legal"],
    "max_pdf_version": "2.0",
    "max_file_size": 104857600,
    "copies_max": 99
  },
  "status": "idle"
}
```

### POST /opp/v1/jobs

Submit a print job. Uses `multipart/form-data`.

**Form fields:**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `file` | file | yes | — | PDF file |
| `copies` | integer | no | 1 | Number of copies |
| `color` | boolean | no | true | Color or grayscale |
| `duplex` | string | no | "none" | "none", "long-edge", "short-edge" |
| `media` | string | no | "a4" | Paper size |
| `pages` | string | no | "all" | Page range, e.g. "1-3,5" |
| `priority` | integer | no | 50 | 1 (lowest) to 100 (highest) |

**Response** `201 Created`:

```json
{
  "id": "job_abc123",
  "status": "queued",
  "created_at": "2026-01-15T10:30:00Z",
  "pages_total": 5,
  "copies": 1
}
```

**Error responses:**

- `400 Bad Request` — invalid or corrupt PDF, missing file, invalid parameters
- `401 Unauthorized` — missing or invalid auth token
- `413 Payload Too Large` — file exceeds `max_file_size`
- `503 Service Unavailable` — printer offline or in error state

### GET /opp/v1/jobs

List jobs. Supports query parameters `status` (filter) and `limit` (max results, default 50).

**Response** `200 OK`:

```json
{
  "jobs": [
    {
      "id": "job_abc123",
      "status": "printing",
      "created_at": "2026-01-15T10:30:00Z",
      "pages_total": 5,
      "pages_printed": 2,
      "copies": 1
    }
  ],
  "total": 1
}
```

### GET /opp/v1/jobs/{id}

Get a single job's status.

**Response** `200 OK`:

```json
{
  "id": "job_abc123",
  "status": "printing",
  "created_at": "2026-01-15T10:30:00Z",
  "pages_total": 5,
  "pages_printed": 3,
  "copies": 1,
  "error": null
}
```

**Job statuses:** `queued`, `processing`, `printing`, `completed`, `canceled`, `error`

### DELETE /opp/v1/jobs/{id}

Cancel a job. Only jobs in `queued` or `processing` state can be canceled.

**Response** `200 OK`:

```json
{
  "id": "job_abc123",
  "status": "canceled"
}
```

### GET /opp/v1/jobs/{id}/events

Server-Sent Events stream for a specific job.

**Event types:**

- `status` — job status changed
- `progress` — page printed
- `error` — job error
- `complete` — job finished

**Example stream:**

```
event: status
data: {"status": "processing"}

event: progress
data: {"pages_printed": 1, "pages_total": 5}

event: progress
data: {"pages_printed": 2, "pages_total": 5}

event: complete
data: {"status": "completed", "pages_printed": 5}
```

### GET /opp/v1/status

Printer status including supplies and errors.

**Response** `200 OK`:

```json
{
  "state": "idle",
  "supplies": {
    "black": 72,
    "cyan": 45,
    "magenta": 88,
    "yellow": 63,
    "paper": {
      "tray1": {"media": "a4", "level": "full"},
      "tray2": {"media": "letter", "level": "low"}
    }
  },
  "errors": [],
  "jobs_queued": 0,
  "jobs_printing": 0
}
```

**Printer states:** `idle`, `printing`, `error`, `offline`, `maintenance`

### GET /opp/v1/status/events

Server-Sent Events stream for printer status changes.

**Event types:**

- `state` — printer state changed
- `supplies` — supply level changed
- `error` — printer error occurred
- `error_cleared` — printer error resolved

## Error Format

All errors return a JSON body:

```json
{
  "error": {
    "code": "invalid_pdf",
    "message": "The uploaded file is not a valid PDF document."
  }
}
```

## Standard Error Codes

| Code | HTTP Status | Description |
|---|---|---|
| `invalid_pdf` | 400 | File is not valid PDF |
| `invalid_parameter` | 400 | Invalid request parameter |
| `unauthorized` | 401 | Invalid or missing auth token |
| `not_found` | 404 | Job or resource not found |
| `file_too_large` | 413 | PDF exceeds size limit |
| `printer_unavailable` | 503 | Printer offline or in error state |

## Versioning

The protocol version is in the URL path (`/opp/v1/`). Breaking changes increment the major version. Non-breaking additions (new optional fields) are allowed within a version.
