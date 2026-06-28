# Changelog
All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- MCP server now prints **any** file type — HTML, text, and images are
  auto-converted to PDF (`integrations` layer)
- Printer resolution chain for the MCP server and CLI: explicit URL → mDNS
  discovery → saved default printer (`set_default_printer`/`get_default_printer`)
- Raw-IPP target support: `ipp://host` or a bare `host`/`host:port` prints
  directly, rasterising for printers with no PDF interpreter (e.g. HP DeskJet)
- Wi-Fi Direct connect/restore for printers reachable only over their SoftAP
  (Windows): `connect_wifi_direct`, `restore_wifi`
- `opp print` accepts any file type plus raw-IPP/host targets and a `--pages` flag
- Automatic format negotiation: pdf → octet-stream → jpeg → pwg-raster fallback chain
- PWG Raster encoder for printers that don't support PDF or JPEG
- Printer info caching on discovery (name, capabilities, supported formats)
- GET /opp/v1/printers/{id}/formats — list printer's supported document formats
- GET /opp/v1/printers/{id}/supplies — live ink/toner levels per printer
- Webhook support: POST callback on job completion/failure
- Ink level alerts: warnings at <15%, critical logs at <10%
- Automatic retry of queued jobs when printer comes back online
- OpenAPI descriptions on all endpoints
- Enhanced web dashboard: ink level bars, job queue, SSE live updates
- HTTP-first IPP strategy for printers with broken TLS stacks

### Fixed
- CUPS print jobs crashed reading a non-existent `Job.pages` field — `pages` is
  now a first-class `Job` field carried end to end
- Page ranges now render the **requested** pages; previously a range like `5-7`
  printed the first three pages instead
- Multi-page IPP jobs send one Print-Job per page — consumer printers commonly
  reject the multi-document Create-Job/Send-Document flow (IPP `0x0509`)
- Malformed `pages` input returns HTTP 400 (`invalid_parameter`) instead of a 500
- PDF page-count estimate tolerates `/Type/Page` without a space
- Unhandled server errors return a clean JSON 500 instead of leaking a stack trace
- document-format IPP attribute now correctly placed in operation group
- IPP response parser correctly handles multi-value attributes
- Multi-value IPP attribute parsing fix (document-format-supported)

## [0.1.0] - 2025-01-01
### Added
- Initial release
- IPP backend for driverless printing
- CUPS bridge mode
- mDNS printer discovery
- Job persistence (SQLite)
- Web dashboard
- MCP server integration
- TLS support
- CLI (opp)
