# OpenPrint Deployment Guide

This guide covers every supported way to run the OpenPrint bridge, from a quick local test to a hardened production service.

---

## Quick Start (pip)

The fastest way to get a bridge running:

```bash
pip install openprint[pdf]
opp bridge
```

`openprint[pdf]` installs PyMuPDF and Pillow, which are required to print to printers that do not natively support PDF (see [Format Negotiation](ARCHITECTURE.md)). Without these packages, only printers that accept `application/pdf` or `application/octet-stream` directly will work.

The bridge listens on `0.0.0.0:631` by default. Open `http://localhost:631` to see the web dashboard.

---

## Raspberry Pi (Recommended Home Setup)

A Raspberry Pi running CUPS is the recommended way to bridge all your home or office printers through a single always-on endpoint.

```bash
# 1. Install CUPS
sudo apt update && sudo apt install -y cups

# 2. Add your printers to CUPS (web UI at http://pi.local:631/admin)

# 3. Install OpenPrint
pip install openprint[pdf]

# 4. Install and start as a systemd service
sudo bash scripts/install.sh
```

After `install.sh`, every device on your network can print by POSTing to `http://pi.local:631/opp/v1/jobs`. No drivers, no apps.

---

## Systemd (Linux)

For any Linux host where you want the bridge to start on boot and restart on failure.

### 1. Install the service file

```bash
sudo cp systemd/openprint-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### 2. Create the service user

```bash
sudo useradd -r -s /bin/false -d /home/openprint openprint
sudo mkdir -p /home/openprint/.openprint
sudo chown openprint:openprint /home/openprint/.openprint
```

### 3. Configure the service

The service file runs `openprint-bridge` (the entry point installed by pip). Edit `/etc/systemd/system/openprint-bridge.service` to adjust arguments:

```ini
[Service]
ExecStart=/usr/local/bin/openprint-bridge --port 631 --host 0.0.0.0
Environment=OPP_PORT=631
Environment=OPP_HOST=0.0.0.0
```

If you want TLS, add `--tls-auto` or point to your own certs:

```ini
ExecStart=/usr/local/bin/openprint-bridge --tls-cert /etc/openprint/cert.pem --tls-key /etc/openprint/key.pem
```

### 4. Enable and start

```bash
sudo systemctl enable openprint-bridge
sudo systemctl start openprint-bridge
```

### 5. View logs

```bash
sudo journalctl -u openprint-bridge -f
```

The service file includes systemd hardening (`NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`). The only writable path outside of `/tmp` is `/home/openprint/.openprint`, where the SQLite job database lives.

---

## Docker

### Basic usage

```bash
cd docker
docker compose up -d
```

The compose file builds from `docker/Dockerfile.bridge` and:
- Maps port `631:631`
- Mounts a named volume `openprint-data` to `/home/openprint/.openprint` for persistent job history
- Mounts `/var/run/cups` from the host so the bridge can discover and print to host CUPS printers
- Uses `network_mode: host` — required for mDNS printer discovery (zeroconf needs to send/receive multicast on the host network interface)

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `OPP_NAME` | `OpenPrint Bridge` | Bridge display name advertised via mDNS |
| `OPP_PORT` | `631` | Port the HTTP server listens on |

### Volume mounts

| Mount | Purpose |
|---|---|
| `openprint-data:/home/openprint/.openprint` | SQLite job database persistence |
| `/var/run/cups:/var/run/cups` | Access to host CUPS socket for local printers |

### Stopping and removing

```bash
docker compose down          # stop, keep volumes
docker compose down -v       # stop and delete job history volume
```

---

## Port 631 on Linux

Port 631 is the IANA-assigned port for IPP. Binding to ports below 1024 requires elevated privileges on Linux.

**Option 1 — Run as root (not recommended for production):**

```bash
sudo opp bridge
```

**Option 2 — Grant the binary the `cap_net_bind_service` capability (recommended):**

```bash
sudo setcap 'cap_net_bind_service=+ep' $(which opp)
opp bridge   # now runs as a regular user on port 631
```

Or for the pip-installed entry point:

```bash
sudo setcap 'cap_net_bind_service=+ep' $(which openprint-bridge)
```

**Option 3 — Use a high port and proxy with nginx:**

Run the bridge on port 8631 and have nginx forward port 631:

```bash
opp bridge --port 8631
```

nginx config fragment:

```nginx
stream {
    server {
        listen 631;
        proxy_pass 127.0.0.1:8631;
    }
}
```

**Note:** If CUPS is running on the same machine, it likely already occupies port 631. Either stop CUPS (`sudo systemctl stop cups`), configure CUPS to use a different port, or run the bridge on a high port (option 3 above). The bridge does not need CUPS to be running — it can discover and print to IPP printers directly.

---

## TLS

### Auto self-signed certificate

The simplest option. Generates a self-signed cert on first run and reuses it:

```bash
opp bridge --tls-auto
```

Clients will need to skip certificate verification or trust the generated cert. The cert is stored in `~/.openprint/`.

### Custom certificate

Bring your own cert and key (e.g. from Let's Encrypt):

```bash
opp bridge --tls-cert /etc/openprint/cert.pem --tls-key /etc/openprint/key.pem
```

With a valid cert, clients can connect without disabling certificate verification:

```bash
curl -X POST https://bridge.example.com:631/opp/v1/jobs -F "file=@doc.pdf"
```

### No TLS

The default. Suitable for trusted local networks. All traffic is plain HTTP.

```bash
opp bridge
```

---

## Configuration Reference

All configuration is passed as CLI flags to `opp bridge` or `openprint-bridge`.

| Flag | Default | Description |
|---|---|---|
| `--port` | `631` | TCP port to listen on |
| `--host` | `0.0.0.0` | IP address to bind to |
| `--tls-cert PATH` | — | Path to TLS certificate file (PEM) |
| `--tls-key PATH` | — | Path to TLS private key file (PEM) |
| `--tls-auto` | `false` | Generate and use a self-signed certificate |
| `--no-dashboard` | `false` | Disable the web dashboard (saves memory) |
| `--no-network-scan` | `false` | Disable mDNS scanning for IPP printers |
| `--auth-token TOKEN` | — | Require `Authorization: Bearer TOKEN` on all API requests |

### Feature flags (Python API / environment)

When constructing `Bridge()` programmatically, additional flags are available:

| Keyword argument | Default | Description |
|---|---|---|
| `enable_persistence` | `True` | SQLite job history at `~/.openprint/jobs.db` |
| `enable_network_scan` | `True` | mDNS IPP printer discovery |
| `enable_cups_watch` | `True` | Poll for new/removed CUPS printers every 10s |
| `enable_dashboard` | `True` | Web dashboard at `/` |
| `enable_health_check` | `True` | Ping printers every 30s, trigger recovery |

---

## Verifying the Deployment

Once the bridge is running, verify it is working:

```bash
# List discovered printers
curl http://localhost:631/opp/v1/printers

# Check bridge status (state + supply levels for all printers)
curl http://localhost:631/opp/v1/status

# Print a test document
curl -X POST http://localhost:631/opp/v1/jobs \
  -F "file=@/path/to/document.pdf"

# Watch the job status via SSE (replace JOB_ID)
curl -N http://localhost:631/opp/v1/jobs/JOB_ID/events
```

Or use the CLI:

```bash
opp status
opp print document.pdf
opp jobs
```
