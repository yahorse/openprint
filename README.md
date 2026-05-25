# OpenPrint

**Printing is broken. We're fixing it.**

It's 2026 and printing a PDF still requires downloading a 200MB driver, installing a vendor app that harvests your data, and praying the printer doesn't say "offline" for no reason. HP charges $1/page through subscriptions. Canon bricks printers with firmware updates. Epson won't let you scan when the yellow ink is low.

Enough.

OpenPrint is an open source printing protocol. Send a PDF over HTTP, get a print. No drivers. No apps. No ink DRM. Seven API endpoints replace the entire printing stack.

```bash
pip install openprint
```

## Print in one line

```bash
# From the CLI
opp print document.pdf

# With curl
curl -X POST http://printer.local:631/opp/v1/jobs -F "file=@document.pdf"

# With Python
from openprint import Client
Client().discover()
Client().print("document.pdf")
```

## Use your existing printers right now

You don't need a new printer. The bridge wraps every printer you already own:

```bash
# Start the bridge — all your printers are now available via OPP
opp bridge
```

That's it. Every CUPS printer and every IPP network printer is now discoverable and printable via HTTP. No drivers on any client device. Ever.

```
Phone/Laptop/IoT              Bridge (Pi, server, Docker)         Your Printers
┌──────────┐                 ┌─────────────────────────┐        ┌─────────────┐
│  HTTP    │   POST /jobs    │  openprint-bridge       │  IPP   │ HP LaserJet │
│  client  │ ──────────────→ │                         │ ─────→ │ Canon Inkjet│
│  (any)   │   PDF + JSON   │  auto-discovers printers│  CUPS  │ Brother MFC │
└──────────┘                 │  wakes sleeping printers│        │ Epson WF    │
      ↑                      │  retries on failure     │        └─────────────┘
      │ mDNS discovery       │  web dashboard at :631  │
      └──────────────────────┘─────────────────────────┘
```

## Test your printer

```bash
opp test 192.168.1.100
```

```
==================================================
  OpenPrint Compatibility Test
  Target: 192.168.1.100:631
==================================================

  Network connectivity... [PASS] Reachable
  HTTP response...        [PASS] HTTP 200
  IPP protocol...         [PASS] HP LaserJet Pro (state: idle)
  Supported formats:      application/pdf, image/pwg-raster
  PDF support...          [PASS] application/pdf supported
  Driverless printing...  [PASS] PDF-native (best)

==================================================
  [PASS] This printer works with OpenPrint!

  Print to it:
    opp print document.pdf -p http://192.168.1.100:631
==================================================
```

## What it does that nothing else does

| Problem | Before | OpenPrint |
|---|---|---|
| **Drivers** | 200MB download per printer per OS | None. Zero. PDF over HTTP. |
| **"Printer offline"** | Wait and pray | Auto-wakes printer, retries with backoff |
| **Printer changes IP** | Broken until you reconfigure | mDNS re-resolves automatically |
| **New printer on WiFi** | Manual setup on every device | Detected in seconds, works immediately |
| **Print from phone** | Install vendor app (HP Smart, Canon PRINT) | Any HTTP client. Browser. curl. |
| **Print from server** | Install CUPS + drivers on every server | `curl -F "file=@report.pdf" http://bridge:631/opp/v1/jobs` |
| **Ink DRM** | "Non-genuine cartridge detected" | Not our problem. We just send PDFs. |
| **Status** | "Check printer" (thanks) | Real-time SSE: page 3/5 printing, cyan at 45% |

## Deploy in 60 seconds

### Raspberry Pi (recommended)

```bash
sudo apt install cups
pip install openprint
sudo bash scripts/install.sh
```

Starts on boot. Every device in your house prints through one endpoint.

### Docker

```bash
cd docker
docker compose up -d
```

### Any Linux/macOS machine

```bash
pip install openprint
opp bridge
```

## The protocol

Seven endpoints. That's the whole thing.

| Endpoint | Method | What it does |
|---|---|---|
| `/opp/v1/printer` | GET | Printer info and capabilities |
| `/opp/v1/printers` | GET | List all printers (bridge mode) |
| `/opp/v1/jobs` | POST | Submit a print job (PDF upload) |
| `/opp/v1/jobs` | GET | List jobs |
| `/opp/v1/jobs/{id}` | GET | Job status |
| `/opp/v1/jobs/{id}` | DELETE | Cancel job |
| `/opp/v1/jobs/{id}/events` | GET | Real-time SSE job updates |
| `/opp/v1/status` | GET | Printer status, supplies, errors |
| `/opp/v1/status/events` | GET | Real-time SSE printer status |

Full spec: [spec/openprint-protocol-v1.md](spec/openprint-protocol-v1.md)

## CLI

```bash
opp discover              # Find printers on the network
opp print doc.pdf         # Print to the first available printer
opp print doc.pdf -p HP   # Print to a specific printer
opp print doc.pdf --bw    # Grayscale
opp print doc.pdf --duplex long-edge
opp status                # Printer status and supply levels
opp jobs                  # Recent print jobs
opp test 192.168.1.100    # Test printer compatibility
opp bridge                # Bridge all local printers to OPP
opp server                # Run a standalone OPP server
```

## Web dashboard

Open `http://<bridge-ip>:631` in any browser. Dark mode. Drag-and-drop PDF printing. Live status for all printers and jobs.

## Features

- **Live printer detection** — mDNS scanner + CUPS watcher detect printers joining/leaving the network in real-time
- **Direct IPP** — talks to IPP printers without CUPS, no configuration needed
- **Wake-on-LAN** — wakes sleeping printers before printing
- **Retry with backoff** — handles "printer offline" gracefully instead of failing
- **Health monitoring** — checks all printers every 30s, re-resolves mDNS for IP changes
- **TLS** — `opp bridge --tls-auto` or bring your own certs
- **Job persistence** — SQLite-backed history at `~/.openprint/jobs.db`
- **Systemd service** — runs on boot, auto-restarts on failure

## Report your printer

**Help us build the compatibility database.** Run `opp test` on your printer and [submit a report](https://github.com/yahorse/openprint/issues/new?template=printer-report.yml):

```bash
opp test <your-printer-ip>
```

Every report helps. We want to know what works and what doesn't.

## Contributing

```bash
git clone https://github.com/yahorse/openprint.git
cd openprint
pip install -e ".[dev]"
pytest
```

90 tests. All passing. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Why this exists

Printer manufacturers have had decades to make printing work. They chose instead to:

- Sell ink at [$12,000 per gallon](https://www.businessinsider.com/why-printer-ink-so-expensive-2019-8)
- Ship printers that [refuse to scan when ink is low](https://www.theverge.com/2021/8/12/22621513/hp-printers-all-in-one-printing-ink-cartridge-low)
- Push [firmware updates that brick third-party cartridges](https://www.eff.org/deeplinks/2020/11/ink-stained-wretches-fighting-free-printer)
- Require [monthly subscriptions to use your own printer](https://www.theverge.com/2024/9/10/24240534/hp-all-in-plan-monthly-subscription-printers-ink)
- Build apps that [collect your data](https://foundation.mozilla.org/en/privacynotincluded/hp-deskjet-2742e-all-in-one-printer/) and show ads on your printer's screen

The technology to make printing simple has existed for 15 years. A printer is a computer with a PDF renderer and a paper feed. It should be a web server that accepts file uploads. That's it.

No printer company will build this because it would make their driver ecosystems, proprietary apps, ink DRM, and subscription models obsolete. Open source has to do it.

## License

MIT — do whatever you want with it.
