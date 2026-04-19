#!/usr/bin/env python3
"""
Ruckus One AP Radio Control

Enables or disables AP radios via the Ruckus One API.
Designed to run as a cron job for scheduled radio management.

Usage:
    python ap_radio_control.py enable --radios 24g,5g
    python ap_radio_control.py disable --radios 24g,5g,6g
    python ap_radio_control.py disable --radios 24g,5g --aps SERIAL1,SERIAL2
"""

import argparse
import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "ap_radio_control.log"

logger = logging.getLogger("ap_radio_control")
logger.setLevel(logging.DEBUG)

file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(console_handler)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv(Path(__file__).resolve().parent / ".env")

TENANT_ID = os.getenv("RUCKUS_TENANT_ID")
CLIENT_ID = os.getenv("RUCKUS_CLIENT_ID")
CLIENT_SECRET = os.getenv("RUCKUS_CLIENT_SECRET")
API_BASE_URL = os.getenv("RUCKUS_API_BASE_URL", "https://api.ruckus.cloud")
AUTH_BASE_URL = os.getenv("RUCKUS_AUTH_BASE_URL", "https://ruckus.cloud")
VENUE_ID = os.getenv("RUCKUS_VENUE_ID")
AP_SERIAL_NUMBERS = [
    s.strip() for s in os.getenv("RUCKUS_AP_SERIAL_NUMBERS", "").split(",") if s.strip()
]

VALID_RADIOS = {"24g", "5g", "6g"}

# Map CLI radio names to the JSON enable-flag keys and their radio params sections
RADIO_FLAG_MAP = {
    "24g": "enable24G",
    "5g": "enable50G",
    "6g": "enable6G",
}
RADIO_PARAMS_MAP = {
    "24g": "apRadioParams24G",
    "5g": "apRadioParams50G",
    "6g": "apRadioParams6G",
}
# Maps radio band to the key in the wifiAvailableChannels response
RADIO_AVAILABLE_CHANNELS_KEY = {
    "24g": "2.4GChannels",
    "5g": "5GChannels",
    "6g": "6GChannels",
}

# Shared session with a real User-Agent to avoid Cloudflare bot challenges
session = requests.Session()
session.headers.update({
    "User-Agent": "RuckusAPRadioControl/1.0",
    "Accept": "application/json",
})

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def authenticate() -> str:
    """Obtain a JWT access token via OAuth2 client credentials."""
    url = f"{AUTH_BASE_URL}/oauth2/token/{TENANT_ID}"
    logger.info("Authenticating to %s", url)

    resp = session.post(
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=30,
    )

    logger.debug("Auth response status: %d", resp.status_code)
    logger.debug("Auth response body: %s", resp.text)

    resp.raise_for_status()

    if not resp.text.strip():
        raise RuntimeError(f"Authentication returned empty response (HTTP {resp.status_code})")

    try:
        data = resp.json()
    except requests.exceptions.JSONDecodeError:
        raise RuntimeError(f"Authentication returned non-JSON response: {resp.text[:500]}")

    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in auth response. Keys returned: {list(data.keys())}")
    logger.info("Authentication successful")
    return token


# ---------------------------------------------------------------------------
# Radio settings helpers
# ---------------------------------------------------------------------------


def get_available_channels(token: str, venue_id: str, serial_number: str) -> dict:
    """GET the regulatory-compliant available channels for an AP."""
    url = f"{API_BASE_URL}/venues/{venue_id}/aps/{serial_number}/wifiAvailableChannels"
    logger.info("GET available channels for AP %s", serial_number)

    resp = session.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    logger.debug("Available channels for AP %s: %s", serial_number, json.dumps(data, indent=2))
    return data


def get_radio_settings(token: str, venue_id: str, serial_number: str) -> dict:
    """GET the current radio settings for an AP."""
    url = f"{API_BASE_URL}/venues/{venue_id}/aps/{serial_number}/radioSettings"
    logger.info("GET radio settings for AP %s", serial_number)

    resp = session.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
        },
        timeout=30,
    )
    resp.raise_for_status()
    settings = resp.json()
    logger.debug("Current settings for AP %s: %s", serial_number, json.dumps(settings, indent=2))
    return settings


def poll_activity(token: str, request_id: str, max_wait: int = 60, interval: int = 5) -> bool:
    """Poll the activity endpoint until the async operation completes."""
    url = f"{API_BASE_URL}/api/tenant/{TENANT_ID}/activity/{request_id}"
    logger.info("Polling activity %s (max %ds)", request_id, max_wait)

    # Brief delay before the first poll — the activity record may not exist yet
    time.sleep(2)

    elapsed = 0
    while elapsed < max_wait:
        try:
            resp = session.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                logger.debug("Activity %s not found yet, retrying…", request_id)
                time.sleep(interval)
                elapsed += interval
                continue
            raise
        data = resp.json()
        status = data.get("status", "UNKNOWN")
        logger.debug("Activity %s status: %s", request_id, status)

        if status == "SUCCESS":
            logger.info("Activity %s completed successfully", request_id)
            return True
        if status in ("FAIL", "FAILED", "ERROR"):
            logger.error("Activity %s failed: %s", request_id, json.dumps(data, indent=2))
            return False

        time.sleep(interval)
        elapsed += interval

    logger.error("Activity %s timed out after %ds", request_id, max_wait)
    return False


def update_radio_settings(token: str, venue_id: str, serial_number: str, settings: dict) -> dict:
    """PUT updated radio settings for an AP."""
    url = f"{API_BASE_URL}/venues/{venue_id}/aps/{serial_number}/radioSettings"
    logger.info("PUT radio settings for AP %s", serial_number)
    logger.debug("Payload: %s", json.dumps(settings, indent=2))

    resp = session.put(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=settings,
        timeout=30,
    )
    logger.debug("PUT response status: %d", resp.status_code)
    logger.debug("PUT response body: %s", resp.text)
    resp.raise_for_status()

    result = resp.json()

    # Handle async response (HTTP 202) — poll is best-effort
    request_id = result.get("requestId")
    if request_id:
        try:
            if not poll_activity(token, request_id):
                logger.warning("Activity polling reported failure for AP %s, but the update may still apply", serial_number)
        except requests.HTTPError as exc:
            logger.warning("Could not poll activity status (requestId: %s): %s", request_id, exc)

    logger.info("Successfully updated radio settings for AP %s", serial_number)
    return result


def set_radio_state(
    token: str, venue_id: str, serial_number: str, radios: set[str], enable: bool
) -> None:
    """Fetch current settings, toggle the requested radio flags, and push back."""
    action = "Enabling" if enable else "Disabling"
    radio_labels = ", ".join(sorted(radios))
    logger.info("%s radios [%s] on AP %s", action, radio_labels, serial_number)

    settings = get_radio_settings(token, venue_id, serial_number)

    # Fetch regulatory-compliant channels when enabling (API requires allowedChannels)
    available_channels = None
    if enable:
        available_channels = get_available_channels(token, venue_id, serial_number)

    for radio in radios:
        flag_key = RADIO_FLAG_MAP[radio]
        settings[flag_key] = enable
        logger.debug("  %s = %s", flag_key, enable)

        # Override venue settings so AP-level enable/disable flags take effect
        params_key = RADIO_PARAMS_MAP[radio]
        if params_key in settings:
            settings[params_key]["useVenueSettings"] = False
            logger.debug("  %s.useVenueSettings = False", params_key)

            # API requires allowedChannels when enabling a radio
            if enable and "allowedChannels" not in settings[params_key]:
                channels_key = RADIO_AVAILABLE_CHANNELS_KEY[radio]
                band_channels = available_channels.get(channels_key, {})
                # 2.4G and 6G have top-level "auto"; 5G nests under indoor/outdoor
                if "auto" in band_channels:
                    allowed = band_channels["auto"]
                else:
                    allowed = band_channels.get("indoor", {}).get("auto", [])
                settings[params_key]["allowedChannels"] = allowed
                logger.debug("  %s.allowedChannels = %s", params_key, allowed)

            # 5G also requires allowedOutdoorChannels
            if enable and radio == "5g" and "allowedOutdoorChannels" not in settings[params_key]:
                outdoor = available_channels.get("5GChannels", {}).get("outdoor", {}).get("auto", [])
                settings[params_key]["allowedOutdoorChannels"] = outdoor
                logger.debug("  %s.allowedOutdoorChannels = %s", params_key, outdoor)

    update_radio_settings(token, venue_id, serial_number, settings)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enable or disable Ruckus One AP radios via the API."
    )
    parser.add_argument(
        "action",
        choices=["enable", "disable", "status"],
        help="Action to perform on the specified radios (status shows current state).",
    )
    parser.add_argument(
        "--radios",
        required=True,
        help="Comma-separated list of radios to target: 24g, 5g, 6g (e.g. --radios 24g,5g).",
    )
    parser.add_argument(
        "--aps",
        default=None,
        help="Comma-separated AP serial numbers (overrides .env).",
    )
    parser.add_argument(
        "--venue",
        default=None,
        help="Venue ID (overrides .env).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Validate radios
    radios = {r.strip().lower() for r in args.radios.split(",")}
    invalid = radios - VALID_RADIOS
    if invalid:
        logger.error("Invalid radio(s): %s. Valid options: %s", invalid, VALID_RADIOS)
        sys.exit(1)

    # Resolve AP list
    ap_serials = (
        [s.strip() for s in args.aps.split(",") if s.strip()]
        if args.aps
        else AP_SERIAL_NUMBERS
    )
    if not ap_serials:
        logger.error("No AP serial numbers provided. Set RUCKUS_AP_SERIAL_NUMBERS in .env or use --aps.")
        sys.exit(1)

    # Resolve venue
    venue_id = args.venue or VENUE_ID
    if not venue_id:
        logger.error("No venue ID provided. Set RUCKUS_VENUE_ID in .env or use --venue.")
        sys.exit(1)

    # Validate required credentials
    missing = []
    if not TENANT_ID:
        missing.append("RUCKUS_TENANT_ID")
    if not CLIENT_ID:
        missing.append("RUCKUS_CLIENT_ID")
    if not CLIENT_SECRET:
        missing.append("RUCKUS_CLIENT_SECRET")
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)

    logger.info(
        "=== AP Radio Control: %s radios [%s] on %d AP(s) ===",
        args.action.upper(),
        args.radios,
        len(ap_serials),
    )

    try:
        token = authenticate()
    except requests.HTTPError as exc:
        logger.error("Authentication failed: %s", exc)
        sys.exit(1)

    if args.action == "status":
        for serial in ap_serials:
            try:
                settings = get_radio_settings(token, venue_id, serial)
                for radio in sorted(radios):
                    flag_key = RADIO_FLAG_MAP[radio]
                    state = "ENABLED" if settings.get(flag_key) else "DISABLED"
                    logger.info("AP %s — %s (%s): %s", serial, radio.upper(), flag_key, state)
            except requests.HTTPError as exc:
                logger.error("Failed to get status for AP %s: %s", serial, exc)
        return

    enable = args.action == "enable"

    failures = []
    for serial in ap_serials:
        try:
            set_radio_state(token, venue_id, serial, radios, enable)
        except requests.HTTPError as exc:
            logger.error("Failed to update AP %s: %s", serial, exc)
            failures.append(serial)

    if failures:
        logger.error("Completed with errors. Failed APs: %s", ", ".join(failures))
        sys.exit(1)

    logger.info("=== All APs updated successfully ===")


if __name__ == "__main__":
    main()
