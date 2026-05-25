# OpenPrint Protocol (OPP)

An open source printing protocol that actually works every time.

HTTP/REST-based, PDF-native, driverless printing with real-time status and automatic discovery.

## Why?

Printing in 2026 is still broken. Drivers crash, proprietary protocols lock you in, and network printers vanish for no reason. OPP fixes this with a dead-simple HTTP API that any device can implement.

**Design principles:**

- **PDF-native** — send a PDF, get a print. No PPDs, no rasterization on the client, no driver installs.
- **HTTP/REST** — any language, any platform. `curl` can print a document.
- **Discovery built in** — mDNS/DNS-SD announces printers automatically. No IP addresses to hunt down.
- **Real-time status** — Server-Sent Events stream job progress and printer state live.
- **Zero configuration** — works out of the box. Sensible defaults, optional fine-tuning.

## Quick Start

### Install

```bash
pip install openprint
```

### Print a file

```python
from openprint import Client

client = Client()
printers = client.discover()
printer = printers[0]

job = client.print(printer, "document.pdf")
print(f"Job {job.id}: {job.status}")
```

### Or use curl

```bash
# Discover printers
curl http://printer.local:631/opp/v1/printer

# Print a PDF
curl -X POST http://printer.local:631/opp/v1/jobs \
  -F "file=@document.pdf" \
  -F "copies=1" \
  -F "color=true"

# Check job status
curl http://printer.local:631/opp/v1/jobs/abc123

# Stream live status
curl http://printer.local:631/opp/v1/jobs/abc123/events
```

### Run a print server

```python
from openprint import Server

server = Server(name="My Printer", port=631)
server.run()
```

## Protocol Overview

OPP is versioned at `/opp/v1/`. All payloads are JSON (except file uploads which use `multipart/form-data`).

| Endpoint | Method | Description |
|---|---|---|
| `/opp/v1/printer` | GET | Printer info and capabilities |
| `/opp/v1/jobs` | GET | List jobs |
| `/opp/v1/jobs` | POST | Submit a print job |
| `/opp/v1/jobs/{id}` | GET | Job status |
| `/opp/v1/jobs/{id}` | DELETE | Cancel job |
| `/opp/v1/jobs/{id}/events` | GET | SSE stream for job updates |
| `/opp/v1/status` | GET | Printer status (paper, ink, errors) |
| `/opp/v1/status/events` | GET | SSE stream for printer status |

See the full [protocol specification](spec/openprint-protocol-v1.md) for details.

## Architecture

```
┌──────────┐     HTTP/REST      ┌──────────────┐
│  Client   │ ──────────────── │  OPP Server   │
│  (app)    │   PDF + JSON     │  (printer)    │
└──────────┘                   └──────────────┘
      │                               │
      │  mDNS/DNS-SD discovery        │  Renders & prints
      └───────────────────────────────┘
```

## Project Structure

```
openprint/
├── spec/                    # Protocol specification
├── src/openprint/           # Reference implementation
│   ├── server.py            # OPP server
│   ├── client.py            # OPP client
│   ├── discovery.py         # mDNS/DNS-SD printer discovery
│   ├── models.py            # Data models
│   ├── pdf.py               # PDF validation and handling
│   ├── status.py            # Real-time status via SSE
│   ├── auth.py              # API key authentication
│   ├── config.py            # Configuration management
│   ├── middleware.py         # Request logging and error handling
│   └── errors.py            # Error types
├── tests/                   # Test suite
├── examples/                # Usage examples
└── docker/                  # Container support
```

## Configuration

```python
from openprint import Server

server = Server(
    name="Office Printer",
    port=631,
    auth_token="your-secret-token",    # Optional API key
    max_file_size=100_000_000,          # 100MB limit
    supported_media=["a4", "letter"],   # Paper sizes
    color=True,                         # Color support
    duplex=True,                        # Duplex support
)
```

Or via environment variables:

```bash
export OPP_NAME="Office Printer"
export OPP_PORT=631
export OPP_AUTH_TOKEN="your-secret-token"
```

## Development

```bash
git clone https://github.com/yahorse/openprint.git
cd openprint
pip install -e ".[dev]"
pytest
```

## License

MIT
