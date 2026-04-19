# AGENTS.md

## Build & Run
- **Setup:** `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
- **Run:** `python ap_radio_control.py {enable|disable|status} --radios 24g,5g [--aps SERIAL] [--venue ID]`
- **No tests currently.** Validate changes manually: `python ap_radio_control.py status --radios 24g,5g`

## Architecture
- Single-script CLI tool (`ap_radio_control.py`) for Ruckus One AP radio management via REST API.
- OAuth2 client credentials auth → JWT token → GET current radio settings → modify enable flags → PUT back.
- Config loaded from `.env` via `python-dotenv`. Secrets must never be logged or committed.
- Async API responses (HTTP 202 + `requestId`) are polled best-effort; updates succeed without polling.
- Key constraint: `useVenueSettings` must be set to `false` on radio params for AP-level overrides to apply.
- Key constraint: `allowedChannels` (string array) must be included when enabling a radio; fetched dynamically from `GET /venues/{venueId}/aps/{serialNumber}/wifiAvailableChannels`.
- Logs written to `logs/ap_radio_control.log` (rotating, 5MB, 5 backups).

## API Documentation
- Update AP Radio Settings: https://docs.ruckus.cloud/api/wifi-17.3.3.312/ap/updateapradio

## Code Style
- Python 3.10+ (type hints use `X | None` syntax, `set[str]`). Stdlib imports first, then third-party.
- Uses `requests.Session` with shared headers for all HTTP calls. Auth via `session.post`, API via `session.get/put`.
- Logging: `logger = logging.getLogger("ap_radio_control")` — DEBUG to file, INFO to console.
- Constants are module-level UPPER_SNAKE_CASE dicts/lists. Functions use snake_case, descriptive names.
- Error handling: `resp.raise_for_status()` with `requests.HTTPError` caught at call sites. Credential validation at startup via `sys.exit(1)`.
