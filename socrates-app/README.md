# Socrates Web App
**Created by** FatoYo November 2026

A web app to browse the timing arrays and reversals saved by `socrates-arrays-extractor` and the blog archive saved by `ae-blog`. It runs both downloaders on a schedule and charts each ticker's daily candles with its reversals.

## Overview

This application consists of three components:

1. **socrates.py** - Small web server that reads the arrays and blog folders and serves the app
2. **socrates.sh** - Shell wrapper for easy execution with automatic venv management
3. **static/** - The web page itself (HTML, CSS and JavaScript, no build step)

## Features

- Browse timing arrays by ticker and by Daily, Weekly or Monthly time frame
- Calendar that only lets you pick dates that have an array
- Second array column for side-by-side comparison, with date syncing between the columns
- Daily candle chart of the array's ticker instead of the second array (needs a free Twelve Data API key)
- Weekly and monthly reversals drawn on the chart as horizontal rays
- Scheduled downloads of the arrays, reversals and blog, with retries and a Logs page
- Keyboard shortcuts and fuzzy ticker search (`Option` + `S`)
- Browse the blog by year, month, day, public/private and category
- Blog posts rendered from markdown, with the filter header collapsible for reading
- Folder locations validated before saving and remembered after restart
- Works with local folders and connected network shares
- Layout adapts to narrow screens such as phones
- Light and dark mode, following the system setting

## Installation

### Prerequisites

- Python 3.11 or higher
- pip3
- A web browser
- Arrays downloaded by `socrates-arrays-extractor` and/or posts downloaded by `ae-blog`

### Setup

1. Go to the app folder:
```bash
cd /path/to/socrates/socrates-app
```

2. Make the shell script executable:
```bash
chmod +x socrates.sh
```

3. Start the app. Dependencies are installed automatically on first run:
```bash
./socrates.sh
```

The shell script will automatically:
- Create a virtual environment if it doesn't exist
- Install required dependencies
- Activate the environment and start the web server

4. Open <http://localhost:8050> in your browser.

5. On first start the app opens the **Settings** page. Enter the arrays folder and the blog folder (see [Settings](#settings)).

Stop the app with `Ctrl` + `C` in the terminal.

## Usage

```bash
./socrates.sh [--port PORT] [--host HOST] [--debug]
```

**Parameters:**

| Parameter | Description |
|-----------|-------------|
| `--port PORT` | Port to listen on (default: 8050) |
| `--host HOST` | Address to listen on (default: `127.0.0.1`, this machine only). Use `0.0.0.0` to open the app from a phone or another computer on the local network |
| `--debug` | Run Flask in debug mode |
| `--production` | Serve with waitress instead of Flask's development server (used by the Docker image) |

| Environment variable | Description |
|----------------------|-------------|
| `SOCRATES_DATA_DIR` | Folder for everything the app writes: `settings.json`, the extractor configs (`socrates-arrays-config.json`, `ae-blog-config.json`), `logs/`, `.state/` and `.cache/`. Unset (the default), they stay next to the code and the extractor configs are each extractor's `config.json`. The Docker image sets it to `/data` |
| `-h, --help` | Show help message |

**Examples:**
```bash
# Start on the default port, this machine only
./socrates.sh

# Use another port
./socrates.sh --port 9000

# Allow other devices on the local network (open http://<this-computer-ip>:8050)
./socrates.sh --host 0.0.0.0
```

The app has no login. Only use `--host 0.0.0.0` on a network you trust.

### Running without the shell script

```bash
python3 -m venv venv
source venv/bin/activate
pip3 install -r requirements.txt
python3 socrates.py [options]
```

**Recommended:** Use `./socrates.sh` instead for automatic environment management.

## Settings

Settings has four pages in the menu: **Archive** (the Socrates login and the archive folders), **Markets**, **Routines** (automatic downloads and the jobs table) and **Charts**.

Open **Settings** from the menu and enter the two root folders. A folder is checked when you press **Validate & save** (or Enter), and it is saved only if it passes. Saved folders are stored in `settings.json` next to `socrates.py` and used again the next time the app starts.

| Setting | Expected contents | Example |
|---------|-------------------|---------|
| Timing arrays folder | One subfolder per ticker (`AEM`, `BTC`, `DIA`, ...) with images named `YYYYMMDD <D\|W\|M> <TICKER>.png` (or `.jpg` / `.jpeg`) | `/Users/you/Library/CloudStorage/WorkDocsDrive-Documents/Personal/SOC` |
| Blog archive folder | One subfolder per year (`2009`, `2010`, ...) with the `.md` posts written by ae-blog | `/Users/you/Documents/Armstrong Economics Blog` |

**Ignored content:**
- Arrays: the `GMW` folder, files that are not PNG or JPEG, and file names that don't follow the convention or whose ticker doesn't match the folder name (for example `20251001 D NVO.png` inside `NET/`)
- Blog: folders that are not years and files that are not `.md`

Posts with `(PB)` in the file name or the `Private Blog` category are shown as private.

**Accepted locations:**
- Absolute paths, `~` included: `~/Documents/Armstrong Economics Blog`
- `file://` URLs
- Network shares, once connected (Finder > Go > Connect to Server). Enter the mounted path (`/Volumes/Share/SOC`) or the share address (`smb://nas.local/Share/SOC`), which is mapped to the mount under `/Volumes`.

### Downloads

The **Archive**, **Markets** and **Routines** pages set up the extractors that come in the same package (`../socrates-arrays-extractor` and `../ae-blog`, next to `socrates-app`). The app always finds them relative to its own folder.

**Socrates Platform account.** One e-mail and password, written to both `../socrates-arrays-extractor/config.json` and `../ae-blog/config.json`. After saving, the password is never sent back to the browser. Leave the field empty to keep the current one. Both `config.json` files are git-ignored.

**Twelve Data API key** (Charts page). Needed for the chart on the Arrays page; a free account at [twelvedata.com](https://twelvedata.com/pricing) is enough. It is saved as `twelveDataApiKey` in `settings.json`, where it can also be entered by hand, and is never sent back to the browser. `chart-markets.json` maps every Socrates Platform market to its Twelve Data symbol; about half (US stocks and ETFs, currencies, crypto, gold) have daily prices on the free plan. Markets without them are marked with ⚠ on the Markets page and show no chart. Other names can be mapped with `"chartSymbols": {"TICKER": "SYMBOL"}` in `settings.json`.

**Reversals on the chart** (Charts page). The weekly and monthly reversals are drawn as horizontal rays: the top active bullish, active bearish and elected ones per timeframe (1–5, default 3), Major ones first. Bullish green, bearish red, elected grey; monthly rays are thicker; Major solid, others dashed. Choose whether they show by default and which timeframes are drawn.

**Market subscriptions** (Markets page). Sorted alphabetically, with the row for adding one at the top. The tickers the arrays extractor downloads. To add one, enter the symbol and the address of its Socrates Platform page (open the instrument on the site and copy the URL, e.g. `https://standard.socratesplatform.com/MarketLanding/Analysis/?analysisId=…`). Arrays are saved to `<timing arrays folder>/<TICKER>`, and that folder is created when the ticker is added. **Add ticker** and **Remove** only mark the changes in the table (new rows are highlighted, removed rows are struck through and can be undone). **Save** writes them to the config and then downloads the daily, weekly and monthly arrays and reversals of all added tickers in one run, so the current week's and month's arrays aren't missing until the next weekly or monthly run. That run waits if another job is running, and its failures are retried like a scheduled run. **Discard changes** drops unsaved changes, and so does leaving the Settings page. Removing a ticker keeps its folder and arrays.

**Automatic downloads** (Routines page, saved with **Save routines**). While the app is running, it starts the extractors in the background, one job at a time:

| Job | When | Command |
|-----|------|---------|
| Timing arrays & reversals | Daily at the first run time (a random minute between 05:00 and 05:59, picked on the first start) | Tue–Fri `-d`, Saturday `-dw`, Sunday and Monday nothing. The 1st of the month adds `-m` (alone on a Sunday or Monday). Always with `--reversals` |
| Public blog | Every N hours from the first run time (default 12) | `ae-blog.sh 1-2 --public` (no login) |
| Private blog | Every N hours from the first run time (default 12) | `ae-blog.sh 1 --private` (one login) |

The reversals are saved in `reversals.sqlite` next to the arrays extractor's `config.json` (in Docker, the data folder). All jobs run with `--log-level debug`. If the arrays run fails, some arrays are outdated or some reversals fail, only those arrays and their reversals are downloaded again after **Retry after** hours (default 1), in one call with one login. **Attempts per day** (default 3) counts the first run too. A run is stopped after 60 minutes (arrays) or 20 minutes (blog).

The first run time is picked at random between 05:00 and 05:59 the first time the app starts, so installs in the same time zone don't all log in to the Socrates Platform at the same minute. It is saved in `settings.json` and stays the same after restarts.

The time zone, first run time, intervals, retries, log retention and on/off switch for each job can all be changed.

**Jobs table.** Shows the last and next run of each job. The **Run now** controls are only for running a job by hand with your own choice and don't change the automatic downloads:

- Timing arrays & reversals: tick Daily, Weekly and/or Monthly (only Daily is ticked by default).
- Public and private blog: the list pages to check, e.g. `1`, `1-3` or `1,3,5` (default `1`).

**Download now.** The Arrays page and the Blog page each have a **⬇ Download now** button that runs their jobs right away, the same way the schedule does:

- Arrays page: today's scheduled arrays and reversals (e.g. Daily + Weekly on a Saturday). On a Sunday or Monday, when nothing is scheduled, it downloads the Daily array.
- Blog page: the public blog (pages 1-2) and then the private blog (page 1).

The button shows "Downloading…" while the jobs run. Next to it is the result of the newest run, retries and scheduled runs included, which updates when the next run finishes.

A run that was due while the app was stopped runs once when it starts again. The very first start, and saving a new schedule, wait for the next scheduled time instead of running at once.

The app must run as a single process (it does with `./socrates.sh`), otherwise each process would start its own scheduler.

**Terminal output.** The terminal the app runs in shows one line when a job starts and one when it finishes, plus the retry plan if arrays or reversals failed:

```
[2026-11-17 05:42:00] Job: Timing arrays & reversals started, scheduled run (attempt 1 of 3): socrates-arrays.sh -d --reversals --log-level debug
[2026-11-17 05:45:10] Job: Timing arrays & reversals failed: Extraction finished with errors: 22 of 23 file(s) created, 22 of 23 reversal table(s) saved, failed: UCO D, reversals failed: UCO D
[2026-11-17 05:45:10] Job: Timing arrays & reversals: Retrying 1 array(s) and reversals at 06:45 (attempt 2 of 3): UCO D
```

The full output of each job is only on the Logs page. The Settings and Logs pages ask for the job status every few seconds; those requests are not printed when they succeed, so the terminal only lists other requests and errors.

## Logs Page

Every run's output is saved as it arrives to `logs/<job>/<YYYY-MM-DD_HHMMSS>.log`, and the runs are listed in `logs/runs.jsonl`. The **Logs** page lists the runs (job, time, scheduled/retry/manual, attempt, result and last summary line) and shows the log lines of one run or of all runs.

- **Level:** Debug (everything), Info, Warning or Error. Each shows that level and the more severe ones.
- **Result of a run:** Success, Outdated (some arrays weren't updated on the site yet and are retried), Failed, Timed out or Interrupted.
- **Filter:** job and date range.
- A run in progress updates live. Click the date in the Jobs table on the Settings › Routines page to open that run.
- Lines from the app itself start with `Socrates app:` (which command ran, the retry plan, the exit code). Lines without a `[date time] LEVEL` prefix, such as installer output from the wrapper scripts, are shown as Debug, and a Python traceback as Error.
- Logs older than **Keep logs for** days (default 30) are deleted automatically. At most the last 5,000 lines are shown at once.

## Arrays Page

- Click a ticker, then **Daily**, **Weekly** or **Monthly**. Changing the ticker or time frame keeps the date you were looking at, or the nearest one. **Latest** jumps to the newest array.
- Click the date button to open a calendar. Only dates that have an array can be clicked, the others are greyed out.
- **+ Add second array for comparison** adds a second column with its own filters. It starts as a copy of the first column. The columns sit side by side on wide screens and stack on narrow ones.
- **+ Add chart** shows the daily candles of the first column's ticker in the second column instead (500 candles, about two years, are loaded; the last 100 are in view, scroll or zoom for the rest), and follows it when you change the ticker. The candles come from Twelve Data and are kept while the chart stays open; close and reopen it to load newer ones. A ticker costs one request the first time it is shown after the chart is opened, and none outside market hours once that day's candles are saved in `prices.sqlite`. The chart uses [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) (Apache-2.0), bundled in `static/vendor`.
- The array frame keeps the 960×566 ratio. Images are never enlarged, and larger images are shrunk to fit. Click an image to open it at full size.
- **↻ Refresh** rescans the folder for new arrays. The app also rescans by itself every 20 seconds while in use.

### Keyboard Shortcuts

| Keys | Action |
|------|--------|
| `Shift` + `←` / `→` | Focus the first / second column (the focused column has a blue outline) |
| `Option` + `S` | Ticker search for the focused column (type, `Enter` to open, `Esc` to close) |
| `D`, `W`, `M` | Daily, Weekly, Monthly |
| `←` / `→` | Older / newer array in the current time frame |
| `↑` / `↓` | Previous / next ticker in alphabetical order (wraps around). The time frame is kept and the array nearest to the current date opens |
| `H` | With the chart shown: mark the array's date on the chart with a dashed blue line, or hide it. The line follows the array when you change the date or ticker |
| `R` | With the chart shown: show or hide the reversals on the chart |

### Syncing Two Columns

When you move the focused column to another date, the other column follows within the coarser of the two time frames:

| Columns | The other column moves to |
|---------|---------------------------|
| Daily + Daily | The same day |
| Daily + Weekly | The same week |
| Daily or Weekly + Monthly | The same month |

If the other column has no array in that period, it stays where it is. Picking a date in the calendar syncs the same way.

## Blog Page

- Filter rows: **Year**, **Month**, **Day** (shown once a month is picked), **Type** (All / Public / Private) and **Categories** (with a search box).
- Filters in different rows combine. Selecting several categories shows posts in any of them.
- Each row only offers values that have posts under the other filters. When a filter change leaves the shown post outside the results, the matching post closest in date is shown instead.
- **✕ Clear filters** shows every post, latest first.
- **Hide filters** collapses the header to its one-line status bar (post count, active filters, Clear filters) to leave more room for reading. **Filters** expands it again. The choice is remembered.
- `↑` / `↓` move to the newer / older post in the list.
- Click a category on a post to filter by it. **Original ↗** opens the post on the website.
- If an image repeats on the very next line (common in private posts), it is shown once. The markdown files are never changed.

## Files

| File | Description |
|------|-------------|
| `settings.json` | Saved folder locations and the Twelve Data API key (created on first save, readable by your user only, not committed to git) |
| `prices.sqlite` | Daily candles cached for the chart; can be deleted, it is filled again (not committed to git) |
| `../socrates-arrays-extractor/reversals.sqlite` | The reversals of every ticker, one table each, written by the arrays download (not committed to git) |
| `.cache/blog-index.json` | Blog index cache, so only new or changed posts are read on later starts (not committed to git) |
| `venv/` | Virtual environment created by `socrates.sh` (not committed to git) |
| `logs/` | Output of every automatic and manual download (not committed to git) |
| `.state/scheduler.json` | Last and next run of each job and pending retries, so a restart keeps the schedule (not committed to git) |
| `../socrates-arrays-extractor/config.json`, `../ae-blog/config.json` | Socrates login and tickers, edited on the Settings page (not committed to git) |

In Docker all of these are in the data folder (`/data`), with the extractor configs as `socrates-arrays-config.json` and `ae-blog-config.json` and the reversals as `reversals.sqlite`.

**Backup:** the two archive folders, `settings.json`, both configs and `reversals.sqlite`. Past arrays and posts, the logins, the API key and the reversal history can't be downloaded again; the rest is rebuilt by the app.

Delete `.cache/` to force a full rescan of the blog. Delete `settings.json` to start the setup again.

## Troubleshooting

### Virtual Environment Issues
```bash
# Remove and recreate venv
rm -rf venv
./socrates.sh
```

### Permission Issues
```bash
# Make shell script executable
chmod +x socrates.sh
```

### Port Already in Use
If the app fails with `Address already in use`, another program (or another copy of the app) is using port 8050:
```bash
# Find what is using the port
lsof -i :8050

# Or start on another port
./socrates.sh --port 8051
```

### Folder Not Accepted in Settings
- Check that the path is absolute and the folder exists
- For network shares, connect to the share in Finder first, then validate again
- The arrays folder must be the root that holds the ticker folders, not a ticker folder itself
- The blog folder must be the root that holds the year folders, not a year folder itself

### "Folder not reachable" on the Arrays or Blog Page
The saved folder could not be read, for example because a network share is disconnected or a cloud drive is offline. Reconnect it and reload the page, or check it on the Settings page.

### New Arrays or Posts Not Showing
Click **↻ Refresh** on the Arrays page or reload the Blog page. The blog index is refreshed at most once a minute.

### Blog Images Missing
Images are loaded from the original websites, so they need an internet connection. The text of the posts is read from the local files.

## Dependencies

- `flask>=3.1.3` - Web server
- `markdown>=3.11.3` - Converts blog posts from markdown to HTML
- `nh3>=0.3.7` - Removes unsafe HTML (scripts, `javascript:` links) from rendered posts
- `tzdata>=2026.6` - Time zone list for the schedule (needed where the system has none, e.g. slim Docker images)
- `waitress>=3.0.2` - Production web server for `--production` (the Docker image)

## License

Copyright (C) 2025-2026 FatoYo

Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0), see [LICENSE](../LICENSE). This project is not affiliated with Armstrong Economics or the Socrates Platform. It needs your own subscription, and you are responsible for following those sites' terms of use.
