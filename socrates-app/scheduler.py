"""
Scheduled runs of the timing arrays extractor and the blog downloader, the extractor
config files they read, and the log files of every run.

The extractors live next to this app (the package layout is socrates/{socrates-app,
socrates-arrays-extractor, ae-blog}) and are started as subprocesses of their shell
wrappers, one at a time. A run's output is read from its pipe line by line and written
to logs/<job>/<YYYY-MM-DD_HHMMSS>.log, and every run is recorded in logs/runs.jsonl.
"""

import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

APP_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = APP_DIR.parent
ARRAYS_DIR = PACKAGE_DIR / "socrates-arrays-extractor"
BLOG_DIR = PACKAGE_DIR / "ae-blog"
ARRAYS_SCRIPT = ARRAYS_DIR / "socrates-arrays.sh"
BLOG_SCRIPT = BLOG_DIR / "ae-blog.sh"

# Everything the app writes. Without SOCRATES_DATA_DIR it stays next to the code, as when
# running ./socrates.sh; the Docker image sets it to /data (a volume), so the code can stay
# read-only and settings, configs and logs survive image updates.
DATA_DIR = Path(os.environ["SOCRATES_DATA_DIR"]).resolve() if os.environ.get("SOCRATES_DATA_DIR") else None
if DATA_DIR:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
ARRAYS_CONFIG = DATA_DIR / "socrates-arrays-config.json" if DATA_DIR else ARRAYS_DIR / "config.json"
BLOG_CONFIG = DATA_DIR / "ae-blog-config.json" if DATA_DIR else BLOG_DIR / "config.json"
LOG_DIR = (DATA_DIR or APP_DIR) / "logs"
RUNS_FILE = LOG_DIR / "runs.jsonl"
STATE_FILE = (DATA_DIR or APP_DIR) / ".state" / "scheduler.json"

SOCRATES_URL = "https://standard.socratesplatform.com/"
PLACEHOLDER_USERNAMES = {"", "your-email@example.com"}
FRAME_ORDER = "DWM"
TICK_SECONDS = 20
CLEANUP_EVERY_SECONDS = 6 * 3600
MAX_LOG_LINES = 5000

JOBS = {
    "arrays": {"label": "Timing arrays", "enabledKey": "arraysEnabled", "timeout": 3600},
    "blog-public": {"label": "Public blog", "enabledKey": "publicEnabled", "timeout": 1200},
    "blog-private": {"label": "Private blog", "enabledKey": "privateEnabled", "timeout": 1200},
}

LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]
LEVEL_RANK = {name: i for i, name in enumerate(LEVELS)}
LEVEL_RANK["CRITICAL"] = LEVEL_RANK["ERROR"]
LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(.*)$")
SAVED_RE = re.compile(r"\bSaved: \d{8} ([DWM]) (.+)\.png$")
ERROR_LINE_RE = re.compile(r"^(Traceback \(most recent call last\)|[A-Za-z_.]*(Error|Exception)\b)")
APP_PREFIX = "Socrates app: "
SCHEDULED_PAGES = {"blog-public": "1-2", "blog-private": "1"}
PAGES_RE = re.compile(r"^\d{1,4}(-\d{1,4})?(,\d{1,4}(-\d{1,4})?)*$")
STATUS_TEXT = {"success": "finished", "outdated": "finished with outdated arrays", "failed": "failed",
               "timeout": "timed out"}
OUTDATED_EXIT = 2   # socrates-arrays.py: no errors, but some arrays were still the previous period's


# --------------------------------------------------------------------------- json files

def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, data, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    if private:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- extractor configs

_config_lock = threading.Lock()
TICKER_RE = re.compile(r"^[A-Z0-9]{1,16}$")


def _load_config(path):
    try:
        data = read_json(path, {})
    except ValueError as e:
        raise ValueError(f"{path} is not valid JSON ({e})") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    if not isinstance(data.get("credentials"), dict):
        data["credentials"] = {}
    return data


def _arrays_config():
    data = _load_config(ARRAYS_CONFIG)
    if not isinstance(data.get("tickers"), dict):
        data["tickers"] = {}
    return data


def _sync_archive_paths(cfg, arrays_root):
    """Every ticker is saved to <arrays root>/<TICKER>. Returns True when something changed."""
    changed = False
    for symbol, entry in cfg["tickers"].items():
        want = os.path.join(arrays_root, symbol)
        if entry.get("archivePath") != want or entry.get("ticker") != symbol:
            entry["archivePath"], entry["ticker"] = want, symbol
            changed = True
    return changed


def _write_arrays_config(cfg):
    cfg["tickers"] = dict(sorted(cfg["tickers"].items()))
    write_json(ARRAYS_CONFIG, cfg, private=True)


def _credentials_set(creds):
    return bool(creds.get("password")) and creds.get("username", "") not in PLACEHOLDER_USERNAMES


def extractor_settings(arrays_root):
    """Ticker list and login status for the Settings page. Never returns the password."""
    with _config_lock:
        cfg = _arrays_config()
        if arrays_root and _sync_archive_paths(cfg, arrays_root) and ARRAYS_CONFIG.exists():
            _write_arrays_config(cfg)
        blog = _load_config(BLOG_CONFIG)
    creds = cfg["credentials"]
    tickers = []
    for symbol, entry in sorted(cfg["tickers"].items()):
        folder = os.path.join(arrays_root, symbol) if arrays_root else ""
        tickers.append({
            "symbol": symbol,
            "url": entry.get("url", ""),
            "folder": folder,
            "folderExists": bool(folder) and os.path.isdir(folder),
        })
    return {
        "tickers": tickers,
        "username": creds.get("username", "") if creds.get("username") not in PLACEHOLDER_USERNAMES else "",
        "passwordSet": _credentials_set(creds),
        "blogLoginSet": _credentials_set(blog["credentials"]),
        "arraysConfig": str(ARRAYS_CONFIG),
        "blogConfig": str(BLOG_CONFIG),
        "arraysScriptFound": ARRAYS_SCRIPT.is_file(),
        "blogScriptFound": BLOG_SCRIPT.is_file(),
    }


def set_credentials(username, password):
    """Write the Socrates login to both extractor configs. An empty password keeps the current one."""
    username = (username or "").strip()
    if not username or "@" not in username:
        raise ValueError("Enter the e-mail address you log in to the Socrates Platform with.")
    with _config_lock:
        arrays, blog = _arrays_config(), _load_config(BLOG_CONFIG)
        current = arrays["credentials"].get("password") or blog["credentials"].get("password")
        if not password and not current:
            raise ValueError("Enter the password.")
        for cfg in (arrays, blog):
            creds = cfg["credentials"]
            creds["url"] = creds.get("url") or SOCRATES_URL
            creds["username"] = username
            creds["password"] = password or current
        _write_arrays_config(arrays)
        write_json(BLOG_CONFIG, blog, private=True)


def add_ticker(symbol, url, arrays_root):
    symbol = (symbol or "").strip().upper()
    url = (url or "").strip()
    if not arrays_root:
        raise ValueError("Set the timing arrays folder first.")
    if not TICKER_RE.match(symbol):
        raise ValueError("A ticker symbol uses only letters and digits, e.g. TQQQ.")
    if not url.startswith(SOCRATES_URL):
        raise ValueError(f"The URL must be a Socrates Platform page starting with {SOCRATES_URL}")
    with _config_lock:
        cfg = _arrays_config()
        if symbol in cfg["tickers"]:
            raise ValueError(f"{symbol} is already in the list.")
        folder = os.path.join(arrays_root, symbol)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            raise ValueError(f"Could not create the folder {folder}: {e.strerror or e}") from None
        cfg["tickers"][symbol] = {"ticker": symbol, "url": url, "archivePath": folder}
        _sync_archive_paths(cfg, arrays_root)
        _write_arrays_config(cfg)
    return symbol


def save_tickers(add, remove, arrays_root):
    """Apply the ticker changes from the Settings page in one write: remove the symbols in
    `remove`, add the {symbol, url} entries in `add` (creating their folders). Everything is
    checked before anything is changed. Returns (added symbols, removed symbols)."""
    if not arrays_root:
        raise ValueError("Set the timing arrays folder first.")
    if not isinstance(add, list) or not isinstance(remove, list):
        raise ValueError("Invalid ticker changes.")
    with _config_lock:
        cfg = _arrays_config()
        removed = []
        for symbol in remove:
            symbol = str(symbol).strip().upper()
            if symbol not in cfg["tickers"]:
                raise ValueError(f"{symbol} is not in the list.")
            removed.append(symbol)
        keep = set(cfg["tickers"]) - set(removed)
        added = []
        for entry in add:
            entry = entry if isinstance(entry, dict) else {}
            symbol = str(entry.get("symbol", "")).strip().upper()
            url = str(entry.get("url", "")).strip()
            if not TICKER_RE.match(symbol):
                raise ValueError(f"'{symbol}': a ticker symbol uses only letters and digits, e.g. TQQQ.")
            if not url.startswith(SOCRATES_URL):
                raise ValueError(f"{symbol}: the URL must be a Socrates Platform page starting with {SOCRATES_URL}")
            if symbol in keep or any(a[0] == symbol for a in added):
                raise ValueError(f"{symbol} is already in the list.")
            added.append((symbol, url))
        for symbol, _ in added:
            folder = os.path.join(arrays_root, symbol)
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as e:
                raise ValueError(f"Could not create the folder {folder}: {e.strerror or e}") from None
        for symbol in removed:
            del cfg["tickers"][symbol]
        for symbol, url in added:
            cfg["tickers"][symbol] = {"ticker": symbol, "url": url, "archivePath": os.path.join(arrays_root, symbol)}
        _sync_archive_paths(cfg, arrays_root)
        _write_arrays_config(cfg)
    return [a[0] for a in added], removed


def remove_ticker(symbol):
    """Remove a ticker from the extractor config. Its folder and arrays are kept."""
    with _config_lock:
        cfg = _arrays_config()
        if symbol not in cfg["tickers"]:
            raise ValueError(f"{symbol} is not in the list.")
        del cfg["tickers"][symbol]
        _write_arrays_config(cfg)


# --------------------------------------------------------------------------- schedule settings

def local_timezone():
    for candidate in (os.environ.get("TZ", ""),
                      os.path.realpath("/etc/localtime").split("zoneinfo/", 1)[-1]):
        try:
            if candidate and "/" in candidate or candidate == "UTC":
                ZoneInfo(candidate)
                return candidate
        except Exception:
            pass
    return "UTC"


_timezones = None


def timezones():
    global _timezones
    if _timezones is None:
        _timezones = sorted(z for z in available_timezones() if "/" in z or z == "UTC")
    return _timezones


DEFAULT_SCHEDULE = {
    "timezone": None,          # filled in with this machine's time zone
    "startTime": "05:00",      # replaced on the first start by a random time 05:00-05:59
    "arraysEnabled": True,
    "arraysRetryHours": 1,
    "arraysMaxAttempts": 3,    # total runs per day, the first one included
    "publicEnabled": True,
    "publicEveryHours": 12,
    "privateEnabled": True,
    "privateEveryHours": 12,
    "logRetentionDays": 30,
}


def initial_schedule(settings):
    """On the first start, pick a random start time between 05:00 and 05:59 and return the
    schedule to save, so installs in the same time zone don't all log in at the same minute.
    Returns None when a start time is already saved."""
    stored = settings.get("schedule") if isinstance(settings.get("schedule"), dict) else {}
    if stored.get("startTime"):
        return None
    return {**stored, "startTime": f"05:{random.randrange(60):02d}"}


def schedule_settings(settings):
    sched = dict(DEFAULT_SCHEDULE)
    stored = settings.get("schedule")
    if isinstance(stored, dict):
        sched.update({k: v for k, v in stored.items() if k in DEFAULT_SCHEDULE})
    if not sched["timezone"]:
        sched["timezone"] = local_timezone()
    return sched


def validate_schedule(data):
    """Return a clean schedule dict, or raise ValueError with a message for the user."""
    if not isinstance(data, dict):
        raise ValueError("Invalid schedule.")
    out = {}
    tz = str(data.get("timezone", "")).strip()
    try:
        ZoneInfo(tz)
    except Exception:
        raise ValueError(f"Unknown time zone '{tz}'. Pick one from the list, e.g. Europe/Prague.") from None
    out["timezone"] = tz
    start = str(data.get("startTime", "")).strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", start):
        raise ValueError("The start time must be HH:MM, e.g. 05:00.")
    out["startTime"] = start

    def number(key, label, low, high, integer=True):
        try:
            value = float(data.get(key))
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be a number.") from None
        if integer and value != int(value):
            raise ValueError(f"{label} must be a whole number.")
        if not low <= value <= high:
            raise ValueError(f"{label} must be between {low:g} and {high:g}.")
        out[key] = int(value) if integer else value

    number("arraysRetryHours", "Retry after (hours)", 0.25, 24, integer=False)
    number("arraysMaxAttempts", "Attempts per day", 1, 10)
    number("publicEveryHours", "Public blog interval (hours)", 1, 168)
    number("privateEveryHours", "Private blog interval (hours)", 1, 168)
    number("logRetentionDays", "Keep logs for (days)", 1, 3650)
    for key in ("arraysEnabled", "publicEnabled", "privateEnabled"):
        out[key] = bool(data.get(key))
    return out


# --------------------------------------------------------------------------- schedule rules

ANCHOR = datetime(2026, 1, 1)


def array_frames_for(day):
    """Arrays to extract on a given date (the run is in the morning, before the market opens).
    Tue-Sat: daily (the previous day's close). Saturday also weekly. The 1st also monthly.
    Monday has no daily: Friday's close was already saved on Saturday."""
    weekday = day.weekday()  # Monday = 0
    frames = []
    if 1 <= weekday <= 5:
        frames.append("D")
    if weekday == 5:
        frames.append("W")
    if day.day == 1:
        frames.append("M")
    return frames


def _every_hours(job, sched):
    return 24 if job == "arrays" else sched["publicEveryHours" if job == "blog-public" else "privateEveryHours"]


def latest_slot(job, sched, now):
    """The most recent scheduled time at or before now. Times are local wall-clock times,
    so 05:00 stays 05:00 across daylight saving changes."""
    hour, minute = map(int, sched["startTime"].split(":"))
    anchor = ANCHOR.replace(hour=hour, minute=minute)
    step = timedelta(hours=_every_hours(job, sched))
    return anchor + ((now - anchor) // step) * step


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _human(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _sort_frames(frames):
    return sorted(set(frames), key=FRAME_ORDER.index)


# --------------------------------------------------------------------------- log files

def parse_log(path, start, run_id=None, job=None):
    """Yield (time, level, message) for each line of a run's log. Lines without the
    "[date time] LEVEL" prefix (installer output, a Python crash) take the time and level
    of the line before them; a traceback line is an error."""
    t, level, after_app_line = start, "INFO", True
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for raw in f:
            line = raw.rstrip("\n")
            m = LINE_RE.match(line)
            if m:
                t, level, msg = m.group(1), m.group(2), m.group(3)
                if level == "CRITICAL":
                    level = "ERROR"
                after_app_line = msg.startswith(APP_PREFIX)
            else:
                if not line.strip():
                    continue
                msg = line
                if ERROR_LINE_RE.match(line.strip()):
                    level = "ERROR"
                elif after_app_line:
                    level = "DEBUG"
            yield t, level, msg


# --------------------------------------------------------------------------- scheduler

def script_environment(tz):
    """Environment for the extractor scripts: without this app's virtual environment, so each
    script's own wrapper creates and uses its own, and in the schedule's time zone."""
    env = dict(os.environ)
    app_venv = env.pop("VIRTUAL_ENV", None) or sys.prefix
    app_bin = os.path.join(app_venv, "bin")
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                  if p and os.path.normpath(p) != os.path.normpath(app_bin))
    env.pop("PYTHONHOME", None)
    env.update(PYTHONUNBUFFERED="1", TZ=tz, SE_AVOID_STATS="true")
    return env


class Scheduler:
    def __init__(self, get_settings, on_finished=None):
        self.get_settings = get_settings
        self.on_finished = on_finished
        self.lock = threading.RLock()
        self.wake = threading.Condition(self.lock)
        self.queue = deque()
        self.current = None          # run record of the job that is running
        self.started = False
        try:
            self.state = read_json(STATE_FILE, {})
        except ValueError:
            self.state = {}
        if not isinstance(self.state.get("jobs"), dict):
            self.state = {"jobs": {}}

    # ------------------------------------------------------------ lifecycle

    def start(self):
        with self.lock:
            if self.started:
                return
            self.started = True
        LOG_DIR.mkdir(exist_ok=True)
        # Temp folders of runs that were cut off when the app stopped
        for job in JOBS:
            for old in Path(tempfile.gettempdir()).glob(f"socrates-{job}-*"):
                shutil.rmtree(old, ignore_errors=True)
        threading.Thread(target=self._tick_loop, name="socrates-scheduler", daemon=True).start()
        threading.Thread(target=self._worker_loop, name="socrates-job-runner", daemon=True).start()

    def _now(self, sched):
        return datetime.now(ZoneInfo(sched["timezone"])).replace(tzinfo=None)

    def _save_state(self):
        write_json(STATE_FILE, self.state)

    def _job_state(self, job):
        return self.state["jobs"].setdefault(job, {})

    def _busy(self, job):
        return (self.current and self.current["job"] == job) or any(i["job"] == job for i in self.queue)

    def _enqueue(self, job, trigger, **params):
        if self._busy(job):
            return False
        self.queue.append({"job": job, "trigger": trigger, **params})
        self.wake.notify_all()
        return True

    # ------------------------------------------------------------ deciding what is due

    def _tick_loop(self):
        last_cleanup = 0.0
        while True:
            try:
                self.tick()
                if time.time() - last_cleanup > CLEANUP_EVERY_SECONDS:
                    last_cleanup = time.time()
                    self.cleanup_logs()
            except Exception:
                traceback.print_exc()
            time.sleep(TICK_SECONDS)

    def tick(self, now=None):
        sched = schedule_settings(self.get_settings())
        now = now or self._now(sched)
        with self.lock:
            for job, info in JOBS.items():
                js = self._job_state(job)
                if not sched[info["enabledKey"]]:
                    # Switched off: forget the schedule, so switching on starts at the next time.
                    js.pop("lastSlot", None)
                    js.pop("retry", None)
                    continue
                slot = _iso(latest_slot(job, sched, now))
                if "lastSlot" not in js:
                    js["lastSlot"] = slot  # first start or just switched on: wait for the next time
                elif slot > js["lastSlot"]:
                    js["lastSlot"] = slot
                    if job == "arrays":
                        frames = array_frames_for(datetime.fromisoformat(slot).date())
                        if frames:
                            js.pop("retry", None)  # a new day replaces yesterday's retries
                            self._enqueue(job, "schedule", frames=frames, attempt=1)
                    else:
                        self._enqueue(job, "schedule")
                retry = js.get("retry")
                if job == "arrays" and retry and retry["at"] <= _iso(now) and not self._busy(job):
                    js.pop("retry")
                    self._enqueue(job, "retry", remaining=retry["remaining"], attempt=retry["attempt"])
            self._save_state()

    def reanchor(self):
        """After the schedule is changed, start counting from the new times instead of
        running at once for a time that has just passed."""
        sched = schedule_settings(self.get_settings())
        now = self._now(sched)
        with self.lock:
            for job, info in JOBS.items():
                js = self._job_state(job)
                if sched[info["enabledKey"]]:
                    slot = _iso(latest_slot(job, sched, now))
                    js["lastSlot"] = max(js.get("lastSlot", slot), slot)
            self._save_state()

    def scheduled_frames_today(self):
        """What a "Download now" of the arrays fetches: today's scheduled arrays, or the daily
        array on a day without a scheduled run (Sunday, Monday)."""
        sched = schedule_settings(self.get_settings())
        return array_frames_for(self._now(sched).date()) or ["D"]

    def queue_new_ticker(self, symbol):
        """Download the daily, weekly and monthly arrays of a ticker that was just added, so the
        current week's and month's arrays are not missing until the next weekly/monthly run.
        Queued even while another arrays run is busy; several new tickers share one run."""
        wanted = [f"{symbol} {f}" for f in FRAME_ORDER]
        with self.lock:
            for item in self.queue:
                if item["job"] == "arrays" and item["trigger"] == "new-ticker":
                    item["remaining"] = sorted(set(item["remaining"]) | set(wanted))
                    return
            self.queue.append({"job": "arrays", "trigger": "new-ticker", "remaining": wanted, "attempt": 1})
            self.wake.notify_all()

    def run_now(self, job, frames=None, pages=None, as_scheduled=False):
        """Queue a manual run. as_scheduled runs the job the way the schedule does ("Download
        now"); otherwise frames (arrays) or pages (blog) are what the user picked."""
        if job not in JOBS:
            raise ValueError("Unknown job.")
        params = {}
        if job == "arrays":
            if as_scheduled:
                frames = self.scheduled_frames_today()
            frames = _sort_frames(f for f in (frames or []) if f in FRAME_ORDER)
            if not frames:
                raise ValueError("Pick at least one of Daily, Weekly or Monthly.")
            params = {"frames": frames, "attempt": 1}
        elif not as_scheduled:
            pages = str(pages or "").replace(" ", "")
            if not PAGES_RE.match(pages):
                raise ValueError("Pages must be like 1, 1-2 or 1,3,5.")
            params = {"pages": pages}
        with self.lock:
            if self._busy(job):
                raise ValueError(f"{JOBS[job]['label']} is already running or waiting to run.")
            if job == "arrays":
                self._job_state(job).pop("retry", None)
                self._save_state()
            self._enqueue(job, "manual", **params)

    # ------------------------------------------------------------ running a job

    def _worker_loop(self):
        while True:
            with self.lock:
                while not self.queue:
                    self.wake.wait()
                item = self.queue.popleft()
            try:
                self._run(item)
            except Exception:
                traceback.print_exc()
            finally:
                with self.lock:
                    self.current = None
            if self.on_finished:
                try:
                    self.on_finished(item["job"])
                except Exception:
                    traceback.print_exc()

    def _command(self, item, settings):
        """Return (command, working folder, requested arrays or None). Raises ValueError
        with a message when the run cannot start because something is not set up."""
        job = item["job"]
        if job == "arrays":
            root = settings.get("arraysRoot")
            if not root or not os.path.isdir(root):
                raise ValueError("the timing arrays folder is not set or not reachable (Settings)")
            if not ARRAYS_SCRIPT.is_file():
                raise ValueError(f"the arrays extractor was not found at {ARRAYS_SCRIPT}")
            with _config_lock:
                cfg = _arrays_config()
                if _sync_archive_paths(cfg, root):
                    _write_arrays_config(cfg)
            if not _credentials_set(cfg["credentials"]):
                raise ValueError("the Socrates username and password are not set (Settings)")
            if item.get("remaining"):
                # Tickers removed from the list since this run was queued are left out.
                requested = [r for r in item["remaining"] if r.split(" ")[0] in cfg["tickers"]]
                if not requested:
                    raise ValueError("its tickers are no longer in the list (Settings)")
                tickers = sorted({r.split(" ")[0] for r in requested})
                frames = _sort_frames(r.split(" ")[1] for r in requested)
                ticker_args = ["--ticker", ",".join(tickers)]
            else:
                tickers = sorted(cfg["tickers"])
                if not tickers:
                    raise ValueError("there are no tickers in the list (Settings)")
                frames = item["frames"]
                requested = [f"{t} {f}" for t in tickers for f in frames]
                ticker_args = []
            flags = "-" + "".join(f.lower() for f in frames)
            # The extractor reads config.json from its own folder unless told otherwise.
            config_args = ["--config", str(ARRAYS_CONFIG)] if DATA_DIR else []
            return (["/bin/bash", str(ARRAYS_SCRIPT), flags, "--log-level", "debug", *ticker_args, *config_args],
                    ARRAYS_DIR, requested)
        root = settings.get("blogRoot")
        if not root or not os.path.isdir(root):
            raise ValueError("the blog archive folder is not set or not reachable (Settings)")
        if not BLOG_SCRIPT.is_file():
            raise ValueError(f"the blog downloader was not found at {BLOG_SCRIPT}")
        pages = item.get("pages") or SCHEDULED_PAGES[job]
        if job == "blog-public":
            return (["/bin/bash", str(BLOG_SCRIPT), pages, "--public", "--path", root,
                     "--log-level", "debug"], BLOG_DIR, None)
        if not _credentials_set(_load_config(BLOG_CONFIG)["credentials"]):
            raise ValueError("the Socrates username and password are not set (Settings)")
        return (["/bin/bash", str(BLOG_SCRIPT), pages, "--private", "--config", str(BLOG_CONFIG),
                 "--path", root, "--log-level", "debug"], BLOG_DIR, None)

    def _run(self, item):
        try:
            self._run_item(item)
        finally:
            with self.lock:
                self.current = None

    def _run_item(self, item):
        settings = self.get_settings()
        sched = schedule_settings(settings)
        tz = sched["timezone"]
        job = item["job"]
        started = self._now(sched)
        log_path = LOG_DIR / job / f"{started.strftime('%Y-%m-%d_%H%M%S')}.log"
        n = 1
        while log_path.exists():
            n += 1
            log_path = LOG_DIR / job / f"{started.strftime('%Y-%m-%d_%H%M%S')}-{n}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        attempt = item.get("attempt")
        max_attempts = sched["arraysMaxAttempts"] if job == "arrays" else None
        record = {
            "id": f"{job}/{log_path.stem}",
            "job": job,
            "trigger": item["trigger"],
            "attempt": attempt,
            "maxAttempts": max_attempts,
            "start": _human(started),
            "end": None,
            "status": "running",
            "exitCode": None,
            "summary": "",
            "log": str(log_path.relative_to(LOG_DIR)),
            "timezone": tz,
        }
        with self.lock:
            self.current = record
        self._append_run(record)

        def app_line(f, level, message):
            f.write(f"[{_human(self._now(sched))}] {level:<7} {APP_PREFIX}{message}\n")
            f.flush()

        saved, summary, timed_out, code = set(), "", False, None
        with open(log_path, "w", encoding="utf-8") as f:
            what = {"schedule": "Scheduled run", "retry": "Retry", "manual": "Manual run",
                    "new-ticker": "Run for a new ticker"}[item["trigger"]]
            if attempt and job == "arrays":
                what += f" (attempt {attempt} of {max_attempts})"
            try:
                cmd, cwd, requested = self._command(item, settings)
            except ValueError as e:
                app_line(f, "INFO", f"{what} of the {JOBS[job]['label'].lower()} job")
                app_line(f, "ERROR", f"Not started: {e}")
                record.update(status="failed", summary=f"Not started: {e}", end=_human(self._now(sched)))
                self._append_run(record)
                self._console(sched, f"{JOBS[job]['label']} not started: {e}")
                return
            shown = " ".join(c if " " not in c else f'"{c}"' for c in cmd[1:])
            app_line(f, "INFO", f"{what}: {shown}")
            self._console(sched, f"{JOBS[job]['label']} started, {what[0].lower() + what[1:]}: "
                                 f"{os.path.basename(cmd[1])} {' '.join(cmd[2:])}")
            next_step = None
            env = script_environment(tz)
            # The run's own temp folder, for Chromium's profile, cache and shared memory. In the
            # Docker image /tmp is in RAM, so it is deleted as soon as the run ends.
            run_tmp = tempfile.mkdtemp(prefix=f"socrates-{job}-")
            env.update(TMPDIR=run_tmp, TMP=run_tmp, TEMP=run_tmp)
            try:
                proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                        errors="replace", bufsize=1, env=env, start_new_session=True)
            except OSError as e:
                app_line(f, "ERROR", f"Could not start the script: {e.strerror or e}")
                proc = None
            if proc:
                timeout = JOBS[job]["timeout"]
                limit = f"{timeout // 60} minutes" if timeout >= 60 else f"{timeout} seconds"
                timer_hit = threading.Event()

                def stop():
                    timer_hit.set()
                    self._kill(proc)

                timer = threading.Timer(timeout, stop)
                timer.daemon = True
                timer.start()
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    f.write(line + "\n")
                    f.flush()
                    m = LINE_RE.match(line)
                    if m:
                        if m.group(2) in ("INFO", "WARNING", "ERROR", "CRITICAL"):
                            summary = m.group(3)
                        hit = SAVED_RE.search(m.group(3))
                        if hit:
                            saved.add(f"{hit.group(2)} {hit.group(1)}")
                code = proc.wait()
                timer.cancel()
                self._end_group(proc)
                timed_out = timer_hit.is_set()
                if timed_out:
                    summary = f"Stopped after {limit} without finishing"
                    app_line(f, "ERROR", summary)

            if code == 0:
                status = "success"
                app_line(f, "INFO", "Finished successfully")
            elif code == OUTDATED_EXIT and job == "arrays" and not timed_out:
                # Nothing failed, but the site still served old arrays for some tickers. They have
                # no "Saved:" line, so they are retried below like failed ones.
                status = "outdated"
                app_line(f, "WARNING", "Finished, but some arrays were outdated on the Socrates Platform")
            else:
                status = "timeout" if timed_out else "failed"
                if code is not None and not timed_out:
                    app_line(f, "ERROR", f"Finished with exit code {code}")
                if proc is None:
                    summary = "The script could not be started"
            shutil.rmtree(run_tmp, ignore_errors=True)
            record.update(status=status, exitCode=code, summary=summary, end=_human(self._now(sched)))

            if job == "arrays" and status != "success":
                remaining = [r for r in requested if r not in saved]
                record["remaining"] = remaining
                if not remaining:
                    app_line(f, "INFO", "All requested arrays were saved, no retry needed")
                elif attempt < max_attempts:
                    at = self._now(sched) + timedelta(hours=sched["arraysRetryHours"])
                    with self.lock:
                        js = self._job_state(job)
                        pending = js.get("retry")
                        if pending:
                            # Another run already has a retry waiting (e.g. a new ticker's run and
                            # the daily run both failed): retry everything in one call.
                            remaining = sorted(set(remaining) | set(pending["remaining"]))
                            at = min(at, datetime.fromisoformat(pending["at"]))
                        js["retry"] = {"at": _iso(at), "remaining": remaining,
                                       "attempt": max(attempt + 1, pending["attempt"] if pending else 0)}
                        self._save_state()
                    next_step = (f"Retrying {len(remaining)} array(s) at {at.strftime('%H:%M')} "
                                 f"(attempt {attempt + 1} of {max_attempts}): {', '.join(remaining)}")
                    app_line(f, "INFO", next_step)
                else:
                    next_step = (f"Giving up after {attempt} attempt(s). Not saved or still outdated: "
                                 f"{', '.join(remaining)}")
                    app_line(f, "ERROR", next_step)
        self._append_run(record)
        result = STATUS_TEXT.get(record["status"], record["status"])
        self._console(sched, f"{JOBS[job]['label']} {result}" + (f": {summary}" if summary else ""))
        if next_step:
            self._console(sched, f"{JOBS[job]['label']}: {next_step}")

    def _console(self, sched, message):
        """One line in the terminal the app runs in when a job starts or finishes.
        The job's own output only goes to its log file."""
        print(f"[{_human(self._now(sched))}] Job: {message}", flush=True)

    @staticmethod
    def _end_group(proc):
        """After a run, stop whatever it left behind, such as a Chromium that did not quit."""
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass  # nothing left

    @staticmethod
    def _kill(proc):
        """Stop a run and everything it started (Chrome included)."""
        for sig, wait in ((signal.SIGTERM, 15), (signal.SIGKILL, 5)):
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            try:
                proc.wait(wait)
                return
            except subprocess.TimeoutExpired:
                continue

    # ------------------------------------------------------------ runs and logs

    def _append_run(self, record):
        with self.lock:
            LOG_DIR.mkdir(exist_ok=True)
            with open(RUNS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def runs(self, job=None, date_from=None, date_to=None):
        """All recorded runs, newest first. A run left "running" by a stopped app is "interrupted"."""
        merged = {}
        try:
            with open(RUNS_FILE, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec, dict) and rec.get("id"):
                        merged[rec["id"]] = rec
        except FileNotFoundError:
            pass
        current_id = self.current["id"] if self.current else None
        out = []
        for rec in merged.values():
            if rec["status"] == "running" and rec["id"] != current_id:
                rec = dict(rec, status="interrupted", summary=rec.get("summary") or "The app stopped during this run")
            day = rec["start"][:10]
            if (job and rec["job"] != job) or (date_from and day < date_from) or (date_to and day > date_to):
                continue
            out.append(rec)
        out.sort(key=lambda r: (r["start"], r["id"]), reverse=True)
        return out

    def log_lines(self, run_id=None, job=None, min_level="DEBUG", date_from=None, date_to=None):
        """Log lines, oldest first, filtered by level and date. Returns (lines, truncated)."""
        rank = LEVEL_RANK.get(min_level, 0)
        if run_id:
            runs = [r for r in self.runs() if r["id"] == run_id]
        else:
            # A run that started the day before the range may still have lines inside it.
            start = None
            if date_from:
                start = (datetime.fromisoformat(date_from) - timedelta(days=1)).strftime("%Y-%m-%d")
            runs = self.runs(job=job, date_from=start, date_to=date_to)
        lines = []
        for rec in sorted(runs, key=lambda r: (r["start"], r["id"])):
            path = LOG_DIR / rec["log"]
            for t, level, msg in parse_log(path, rec["start"]):
                if LEVEL_RANK[level] < rank:
                    continue
                if (date_from and t[:10] < date_from) or (date_to and t[:10] > date_to):
                    continue
                lines.append({"t": t, "level": level, "msg": msg, "run": rec["id"], "job": rec["job"]})
        lines.sort(key=lambda line: line["t"])  # stable: lines of the same second keep their order
        truncated = len(lines) > MAX_LOG_LINES
        return lines[-MAX_LOG_LINES:], truncated

    def cleanup_logs(self):
        """Delete runs and log files older than the retention setting."""
        sched = schedule_settings(self.get_settings())
        cutoff = (self._now(sched) - timedelta(days=sched["logRetentionDays"])).strftime("%Y-%m-%d")
        with self.lock:
            keep, drop = [], []
            for rec in self.runs():
                (keep if rec["start"][:10] >= cutoff or rec["status"] == "running" else drop).append(rec)
            if not drop:
                return
            for rec in drop:
                try:
                    (LOG_DIR / rec["log"]).unlink()
                except OSError:
                    pass
            tmp = RUNS_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for rec in sorted(keep, key=lambda r: r["start"]):
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            os.replace(tmp, RUNS_FILE)

    # ------------------------------------------------------------ status for the Settings page

    def status(self):
        settings = self.get_settings()
        sched = schedule_settings(settings)
        now = self._now(sched)
        last = {}
        for rec in self.runs():
            last.setdefault(rec["job"], rec)
        out = {}
        with self.lock:
            queued = [i["job"] for i in self.queue]
            for job, info in JOBS.items():
                js = self.state["jobs"].get(job, {})
                enabled = sched[info["enabledKey"]]
                entry = {
                    "label": info["label"],
                    "enabled": enabled,
                    "running": bool(self.current and self.current["job"] == job),
                    "queued": job in queued,
                    "lastRun": last.get(job),
                    "nextRun": None,
                    "retry": js.get("retry"),
                }
                if enabled:
                    step = timedelta(hours=_every_hours(job, sched))
                    slot = latest_slot(job, sched, now) + step
                    if job == "arrays":
                        for _ in range(14):
                            frames = array_frames_for(slot.date())
                            if frames:
                                break
                            slot += step
                        entry["nextRun"] = {"at": _human(slot), "frames": frames}
                    else:
                        entry["nextRun"] = {"at": _human(slot)}
                out[job] = entry
        return {"timezone": sched["timezone"], "now": _human(now), "jobs": out,
                "todayFrames": array_frames_for(now.date())}
