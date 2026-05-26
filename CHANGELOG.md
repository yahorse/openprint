# Changelog
All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- Multi-page PDF printing via IPP Create-Job + Send-Document
- Automatic format negotiation: pdf → octet-stream → jpeg → pwg-raster fallback chain
- PWG Raster encoder for printers that don't support PDF or JPEG
- Page range support in print jobs
- Printer info caching on discovery (name, capabilities, supported formats)
- GET /opp/v1/printers/{id}/formats — list printer's supported document formats
- GET /opp/v1/printers/{id}/supplies — live ink/toner levels per printer
- Webhook support: POST callback on job completion/failure
- Ink level alerts: warnings at <15%, critical logs at <10%
- Automatic retry of queued jobs when printer comes back online
- OpenAPI descriptions on all endpoints
- Enhanced web dashboard: ink level bars, job queue, SSE live updates
- HTTP-first IPP strategy for printers with broken TLS stacks
- Multi-value IPP attribute parsing fix (document-format-supported)

### Fixed
- document-format IPP attribute now correctly placed in operation group
- IPP response parser correctly handles multi-value attributes

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
