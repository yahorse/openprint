# OpenPrint Troubleshooting

Common problems and how to fix them.

---

## 1. "No printers found"

The bridge starts but `GET /opp/v1/printers` returns an empty list, or `opp discover` finds nothing.

**Likely causes and fixes:**

**CUPS is not running (for local printers)**

The bridge discovers local printers by querying CUPS. If CUPS is stopped, no local printers appear.

```bash
# Check CUPS status
systemctl status cups

# Start CUPS
sudo systemctl start cups

# Verify CUPS sees your printers
lpstat -p
```

**mDNS is blocked by a firewall (for network printers)**

IPP printer discovery uses mDNS (multicast DNS on 224.0.0.251:5353/UDP). If your firewall blocks multicast or UDP port 5353, the bridge will not find network printers.

```bash
# Linux — allow mDNS through ufw
sudo ufw allow 5353/udp

# Or through iptables
sudo iptables -A INPUT -p udp --dport 5353 -j ACCEPT
```

On Docker: the compose file uses `network_mode: host`. If you changed this to bridge networking, mDNS will not work. Keep `network_mode: host` for discovery to function.

**The printer is on a different subnet**

mDNS is link-local — it only works on the same Layer 2 network segment. If your printer is on a different VLAN or subnet, mDNS will not reach it. Add the printer manually:

```bash
opp print document.pdf -p http://192.168.10.50:631/ipp/print
```

Or use `opp test` to confirm direct connectivity:

```bash
opp test 192.168.10.50
```

**Network scan is disabled**

If the bridge was started with `--no-network-scan`, only CUPS printers will be discovered. Restart without that flag.

---

## 2. "IPP print failed: status 0x040a"

This IPP status code means `client-error-document-format-not-supported`. The printer rejected the document format sent.

**The printer does not support PDF natively**

Many inkjet printers (especially older or budget models) do not understand PDF. OpenPrint falls back to JPEG or PWG Raster rasterisation, but this requires PyMuPDF and Pillow:

```bash
pip install openprint[pdf]
```

Without `openprint[pdf]`, the format fallback chain cannot proceed past `application/octet-stream`, and the job will fail on printers that only accept `image/jpeg` or `image/pwg-raster`.

After installing, restart the bridge and retry the job.

**Verify what formats the printer supports:**

```bash
curl http://localhost:631/opp/v1/printers/PRINTER_ID/formats
```

If the list is empty or only contains `image/pwg-raster`, PyMuPDF is required.

---

## 3. "All IPP URLs failed"

The `IPPBackend` tried all candidate HTTP/HTTPS URLs and got a connection error on every one.

**The printer is offline or unreachable**

```bash
# Ping the printer
ping 192.168.1.100

# Test the IPP port directly
curl -v http://192.168.1.100:631/ipp/print

# Full compatibility check
opp test 192.168.1.100
```

**The printer's IP address has changed**

If your printer uses DHCP without a reservation, its IP can change after a router restart. The bridge re-resolves mDNS hostnames (e.g. `myprinter.local`) automatically every 30 seconds via the health monitor. If you added the printer by IP address directly, update the URI.

**A firewall is blocking port 631**

Port 631 (IPP) must be reachable from the bridge host to the printer. Check both the host firewall and any network-level ACLs.

```bash
# Test TCP connectivity to port 631
nc -zv 192.168.1.100 631
```

**The printer requires HTTPS (ipps://) but TLS fails**

Some printers advertise `ipps://` but have broken or self-signed TLS. The bridge tries plain HTTP first (the more reliable path for consumer printers). If HTTP also fails, confirm the printer is powered on and connected to the network.

---

## 4. "Jobs complete but nothing prints"

The API returns `status: completed` and the SSE stream fires the `complete` event, but no paper comes out.

**The printer is in a stopped state (low ink, paper jam, etc.)**

Many printers accept the IPP job and report success even when they cannot physically print. Check the printer's physical state and control panel.

```bash
# Check supply levels via OPP
curl http://localhost:631/opp/v1/printers/PRINTER_ID/supplies

# Check printer state
curl http://localhost:631/opp/v1/status
```

Common causes: ink cartridge is empty or flagged as "non-genuine", paper tray is empty, cover is open, or the printer is in a "stopped" state in CUPS.

For CUPS-managed printers, force-resume:

```bash
sudo cupsenable PRINTER_NAME
sudo lp -d PRINTER_NAME /path/to/test.pdf
```

**PJL RESET trick for stuck HP printers**

Some HP printers get stuck in an error state that persists even after clearing the error condition. Sending a PJL RESET over a raw TCP socket can clear it:

```bash
printf "\x1b%%-12345X@PJL RESET\r\n\x1b%%-12345X" | nc -q 1 192.168.1.100 9100
```

After this, the printer should return to idle and process queued jobs.

**The job was sent to the wrong printer**

If multiple printers are bridged, check the `printer` field in the job response to confirm the job went to the expected printer.

---

## 5. "HTTPS ReadTimeout for large files"

Large PDFs (typically over 5–10 MB) fail with a timeout when sent to certain printers over HTTPS.

**This is a known issue with HP DeskJet and similar consumer printers.**

These printers advertise `ipps://` (HTTPS) but their TLS implementation drops connections when the request body exceeds a certain size. The bridge works around this by trying plain HTTP first:

```python
# From backends/ipp.py
if tls:
    self._http_urls = [http_url, https_url]
else:
    self._http_urls = [http_url]
```

If you are bypassing the bridge and connecting directly with `ipps://`, switch to `ipp://` (plain HTTP on port 631). IPP over plain HTTP is not encrypted but is functionally equivalent for local network printing.

For large documents, consider:
- Compressing or downsizing the PDF before sending
- Printing page ranges instead of the whole document at once
- Rasterising to JPEG at lower DPI (the bridge does this automatically when PDF is not supported)

---

## 6. "Port 631 already in use"

```
ERROR: [Errno 98] Address already in use
```

CUPS is almost certainly listening on port 631 already.

```bash
# Find what is using port 631
sudo lsof -i :631
# or
sudo ss -tlnp | grep 631
```

**Option A — Stop CUPS (if you only want OpenPrint)**

```bash
sudo systemctl stop cups
sudo systemctl disable cups
opp bridge
```

Note: disabling CUPS means CUPS-based printer management tools (`lp`, `lpr`, printer settings GUI) will stop working.

**Option B — Move CUPS to a different port and keep both**

Edit `/etc/cups/cupsd.conf` and change `Port 631` to `Port 6310`, then restart CUPS and run `opp bridge` on 631.

**Option C — Run the bridge on a different port**

```bash
opp bridge --port 8631
```

Clients must then use port 8631: `http://bridge-host:8631/opp/v1/jobs`.

---

## 7. Ink alert warnings firing constantly

The bridge logs warnings like:

```
Printer HP-DeskJet: cyan ink critically low (8%)
```

on every startup or printer discovery.

This is expected behaviour. The bridge queries supply levels once during `_prefetch_printer_info` and warns if any colour is below 10%. If the cartridge is genuinely low, replace it. If the printer is misreporting levels, the warnings can be suppressed by increasing the log level:

```bash
opp bridge 2>/dev/null          # suppress stderr entirely
# or set the log level in Python
import logging
logging.getLogger("openprint.bridge").setLevel(logging.ERROR)
```

The low-ink threshold is 10% for the startup warning and 15% for the per-job response warning included in the `POST /opp/v1/jobs` response body.

---

## 8. Dashboard shows "Ink levels not available"

The web dashboard displays "Ink levels not available" for a printer.

**The printer does not report `marker-levels` via IPP**

Supply level reporting is an optional IPP attribute. Many printers — especially older laser printers and some budget inkjets — do not include `marker-levels` or `marker-names` in their `Get-Printer-Attributes` response.

You can verify:

```bash
# Check the raw supply data returned by the bridge
curl http://localhost:631/opp/v1/printers/PRINTER_ID/supplies
```

If the response is `{"supplies": {"black": null, "cyan": null, "magenta": null, "yellow": null}}`, the printer is not reporting levels. There is no workaround — this is a limitation of the printer's firmware.

For CUPS-managed printers, CUPS may be able to query supply levels through a vendor-specific backend even when the IPP attribute is absent. In that case, supply data would come from the CUPS backend rather than direct IPP.

---

## Diagnostic commands

```bash
# Test a specific printer's compatibility
opp test 192.168.1.100

# Check what printers the bridge has discovered
curl http://localhost:631/opp/v1/printers | python3 -m json.tool

# Check bridge status (state + supplies + job counts)
curl http://localhost:631/opp/v1/status | python3 -m json.tool

# List recent jobs
curl http://localhost:631/opp/v1/jobs | python3 -m json.tool

# Stream live printer events
curl -N http://localhost:631/opp/v1/status/events

# View bridge logs (systemd)
sudo journalctl -u openprint-bridge -f --since "10 minutes ago"
```

---

## Filing a bug report

If none of the above solves your problem, please open an issue at https://github.com/yahorse/openprint/issues and include:

1. Output of `opp test <printer-ip>` (if applicable)
2. Bridge logs from the time of the failure (`journalctl -u openprint-bridge` or terminal output)
3. Output of `curl http://localhost:631/opp/v1/printers` and `curl http://localhost:631/opp/v1/status`
4. Printer make, model, and firmware version
5. How you installed OpenPrint (pip, binary, Docker) and the version (`opp --version`)
