# Socrates Platform Timing Arrays Extractor

**Created by** FatoYo November 2025

A Python script that automatically extracts Daily, Weekly, and Monthly timing arrays from the Socrates Platform and saves them as cropped screenshots. It can also save the reversals of the same timeframes in a SQLite database.

## What it does

This tool logs into the Socrates Platform, navigates to specified ticker dashboards, and captures timing arrays and reversals for technical analysis. It automatically:

- Logs into your Socrates Platform account
- Extracts timing arrays for specified tickers
- Saves properly formatted and cropped screenshots
- Optionally saves the active and elected reversals in `reversals.sqlite`, one table per ticker
- Supports Daily (D), Weekly (W), and Monthly (M) timeframes

## Installation

### First-time setup

1. Make sure you have Python 3.11 or higher installed
2. Configure your credentials in `config.json` (see Configuration section)
3. Run the script - it will automatically create the virtual environment and install dependencies on first run

```bash
./socrates-arrays.sh
```

The script automatically handles virtual environment creation and dependency installation when run for the first time.

### Configuration

Copy `config.example.json` to `config.json` and fill in your Socrates Platform credentials and ticker settings. `config.json` is git-ignored so your password is never committed. When you use the Socrates web app (`socrates-app`), its Settings page edits this file for you.

```bash
cp config.example.json config.json
```

```json
{
  "credentials": {
    "url": "https://standard.socratesplatform.com/",
    "username": "your-email@example.com",
    "password": "your-password"
  },
  "tickers": {
    "TQQQ": {
      "url": "https://standard.socratesplatform.com/Research/TQQQ",
      "archivePath": "/path/to/save/screenshots"
    }
  }
}
```

## Usage

Run the script using the provided shell wrapper:

```bash
./socrates-arrays.sh [options]
```

### Examples

Extract daily arrays for TQQQ:
```bash
./socrates-arrays.sh -d --ticker "TQQQ"
```

Extract all timeframes for multiple tickers:
```bash
./socrates-arrays.sh -dwm --ticker "TQQQ,UCO"
```

Extract all timeframes with their reversals:
```bash
./socrates-arrays.sh -dwm --reversals
```

Save only the weekly and monthly reversals, without arrays:
```bash
./socrates-arrays.sh -wm --reversals-only
```

Extract weekly arrays for all configured tickers:
```bash
./socrates-arrays.sh -w
```

Extract daily arrays with a visible browser window:
```bash
./socrates-arrays.sh -d --interactive
```

Also show progress (login steps, each array processed and saved):
```bash
./socrates-arrays.sh -dwm --log-level debug
```

### Options

- `-d, --daily`: Daily timeframe
- `-w, --weekly`: Weekly timeframe
- `-m, --monthly`: Monthly timeframe
- `--reversals`: Also save the reversals of the chosen timeframes (see Reversals below)
- `--reversals-only`: Save only the reversals of the chosen timeframes, without the arrays
- `--ticker TICKER`: Comma-separated list of tickers (e.g., TQQQ,UCO)
- `--interactive`: Open a visible browser window while extracting
- `--log-level LEVEL`: Lowest level of log messages to show: `debug`, `info` (default), `warning` or `error`
- `--config PATH`: Config file to use (default: `config.json` next to the script)

The browser runs in headless mode (no GUI) by default. Use `--interactive` to watch it work.
The timeframe flags can be combined (`-dwm`). If none is given, Daily is used.
If no ticker is specified, all tickers in config.json are processed.

## Logging

Every output line has a date, time and level:

```
[2026-11-11 08:48:19] INFO    Starting extraction for 23 ticker(s), arrays: D
[2026-11-11 08:48:21] DEBUG   Logging in to Socrates Platform...
[2026-11-11 08:48:31] DEBUG   Login successful
[2026-11-11 08:48:31] DEBUG   Processing TQQQ - D array...
[2026-11-11 08:48:39] DEBUG   Saved: 20261111 D TQQQ.png
[2026-11-11 08:48:50] ERROR   Failed: UCO - D array: the page did not respond in time (the Socrates Platform may be slow or unavailable)
[2026-11-11 08:50:55] ERROR   Extraction finished with errors: 22 of 23 file(s) created, failed: UCO D
```

With `--reversals`, each array is followed by `Processing TQQQ - D reversals...` and `Reversals saved: TQQQ D, …` (or `Failed: TQQQ - D reversals: …`), and the last line also counts the reversal tables, e.g. `Extraction complete! 23 file(s) created, 23 reversal table(s) saved`.

| Level | Used for |
|-------|----------|
| `INFO` | Start of the run and `Extraction complete! N file(s) created` |
| `DEBUG` | Progress: login steps, each array and set of reversals processed and saved |
| `WARNING` | An array is outdated (see below), tickers given with `--ticker` that are not in `config.json` (they are skipped), or the run was stopped with Ctrl+C |
| `ERROR` | An array, a set of reversals, the login or the browser failed. The message gives a short reason instead of a Python stack trace, and the final summary lists the arrays that failed |

By default (`--log-level info`) the `INFO`, `WARNING` and `ERROR` lines are shown, so errors always appear. Add `--log-level debug` to also see the `DEBUG` progress lines. The example above is a run with `--log-level debug`.

## Error Handling and Exit Status

- If one array fails (for example the page does not load in time), the error is logged and the script continues with the next array. The final line lists the failed arrays so you can run just those again, e.g. `./socrates-arrays.sh -d --ticker UCO`.
- If the login fails, Chrome cannot start, or the browser crashes, the script stops with an error message and closes the browser.
- A page that never finishes loading is abandoned after 60 seconds. Failed arrays are not retried by the script itself; the Socrates web app retries them.

| Status | Meaning |
|--------|---------|
| `0` | Every requested array (and set of reversals) was saved, and the arrays are current |
| `1` | At least one array or set of reversals failed, or the run stopped because of a login, browser or config error |
| `2` | Nothing failed, but the site still showed outdated arrays for some tickers |
| `130` | Stopped with Ctrl+C |

## Outdated Arrays

Sometimes the Socrates Platform hasn't updated yet and still shows the previous period's array. Such an array is logged as outdated, the run continues, and the script exits with status `2`. The Socrates web app then downloads those arrays again with its retry settings.

The saved images show the array's year on the right of the title bar.

## Reversals

With `--reversals`, the script also reads the reversals table (the one the R key shows on the site) for each chosen timeframe and saves it in `reversals.sqlite` next to `config.json`, which is created on the first run. The database has one table per ticker and one row per reversal, with its timeframe, direction, price, the numbers shown next to it (position and order), the Major, Double and Extended flags, and whether it is active or elected (and in which day, week or month). New reversals are added, and a reversal that gets elected is updated, so elected reversals are kept after they leave the site's list.

## Requirements

- Python 3.11+
- Chrome browser (for Selenium WebDriver)
- Valid Socrates Platform account

## Troubleshooting

### Permission denied error
If you get a "Permission denied" error when running the script:
```bash
chmod +x socrates-arrays.sh
```

### Python not found
If you get "python3: command not found":
- On macOS: Install Python via Homebrew or python.org
- On Linux: `sudo apt install python3 python3-venv` (Ubuntu/Debian)

### Virtual environment creation fails
If venv creation fails, try:
```bash
python3 -m pip install --user virtualenv
python3 -m virtualenv venv
```

### Chrome/ChromeDriver issues
Ensure Chrome browser is installed and up to date. The script uses Chrome's built-in WebDriver.

## License

Copyright (C) 2025-2026 FatoYo

Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0), see [LICENSE](../LICENSE). This project is not affiliated with Armstrong Economics or the Socrates Platform. It needs your own subscription, and you are responsible for following those sites' terms of use.
