# Ruckus One AP Radio Scheduler

A Python script to enable/disable Ruckus One AP radios via the API, designed to run as a cron job.

## Setup

```bash
# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip3 install -r requirements.txt

# Copy the example env file and fill in your credentials
cp .env.example .env
```

Edit `.env` with your Ruckus One credentials (found in **Administration → Account Management → Settings → Application Token**).

## Usage

```bash
# Disable 2.4 GHz and 5 GHz radios
python3 ap_radio_control.py disable --radios 24g,5g

# Enable 2.4 GHz and 5 GHz radios
python3 ap_radio_control.py enable --radios 24g,5g

# Check current radio state
python3 ap_radio_control.py status --radios 24g,5g

# Disable all radios (2.4G, 5G, 6G)
python3 ap_radio_control.py disable --radios 24g,5g,6g

# Override APs or venue from the command line
python3 ap_radio_control.py disable --radios 24g,5g --aps SERIAL1,SERIAL2 --venue VENUE123
```

## Cron Job Examples

Edit your crontab with `crontab -e` and add:

```cron
# Disable 2.4G and 5G radios at 10:00 PM daily
0 22 * * * /path/to/venv/bin/python /path/to/ap_radio_control.py disable --radios 24g,5g

# Enable 2.4G and 5G radios at 7:00 AM daily
0 7 * * * /path/to/venv/bin/python /path/to/ap_radio_control.py enable --radios 24g,5g
```

> **Tip:** Use the full path to the Python binary inside your virtual environment so cron picks up the installed packages.

## Logs

Logs are written to `logs/ap_radio_control.log` (auto-rotated at 5 MB, 5 backups kept).

## API Documentation

- [Update AP Radio Settings](https://docs.ruckus.cloud/api/wifi-17.3.3.307/ap/updateapradio)

## How It Works

1. Authenticates to Ruckus One using OAuth2 client credentials to obtain a JWT token
2. For each AP: **GETs** the current radio settings, toggles only the requested enable flags, then **PUTs** the updated settings back
3. This GET-then-PUT approach preserves all existing channel, power, and band configurations
