# Changelog

All notable changes to this project are documented here.

## [0.1.0] - 2026-10-14

Initial release.

- Async client for the Acumulus API (XML-over-POST via `xmlstring`, JSON
  responses with an XML fallback, status handling, retries).
- MCP tools: `whoami`, `list_picklist`, `create_invoice`, and a generic
  `acumulus_request` escape hatch.
- `ACUMULUS_TESTMODE=1` for dry-run writes.
- Test suite (20 tests) and reproducible wheel/sdist build.
