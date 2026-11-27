#!/usr/bin/env python3
"""
Socrates - browse Socrates Platform timing arrays and the Armstrong Economics blog archive.

A small local web server. It reads two folders configured on the Settings page:
  * the arrays root: one subfolder per ticker, holding "YYYYMMDD <D|W|M> <TICKER>.png|jpg|jpeg"
  * the blog root:   one subfolder per year, holding "YYYY-MM-DD [(PB) ]<Title>.md"
and serves a single-page app from ./static.
"""

import argparse
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import markdown
import nh3
from flask import Flask, abort, jsonify, request, send_file, send_from_directory

import charts as charts_mod
import scheduler as sched_mod

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = sched_mod.DATA_DIR or APP_DIR   # SOCRATES_DATA_DIR, or next to the code
SETTINGS_FILE = DATA_DIR / "settings.json"
CACHE_DIR = DATA_DIR / ".cache"
BLOG_CACHE_FILE = CACHE_DIR / "blog-index.json"
CHART_KEY = "twelveDataApiKey"    # settings.json key of the Twelve Data API key (never sent to the browser)
CHART_KEY_RE = re.compile(r"^[A-Za-z0-9]{16,64}$")
CHART_TICKER_RE = re.compile(r"^[A-Z0-9]{1,16}$")
CHARTS = charts_mod.Candles(DATA_DIR / "prices.sqlite")
REVERSALS_DB = sched_mod.ARRAYS_CONFIG.parent / "reversals.sqlite"  # written by the arrays extractor
# Defaults of settings.json "chartReversals"; the counts are the top N per group (1-5)
CHART_REVERSALS = {"shown": True, "weekly": True, "monthly": True, "weeklyCount": 3, "monthlyCount": 3}


def _reversal_value(key, value, default):
    if key.endswith("Count"):
        try:
            return min(5, max(1, int(value)))
        except (TypeError, ValueError):
            return default
    return bool(value)


def chart_reversals(s):
    stored = s.get("chartReversals")
    stored = stored if isinstance(stored, dict) else {}
    return {k: _reversal_value(k, stored.get(k, v), v) for k, v in CHART_REVERSALS.items()}

REPO_URL = "https://github.com/FatoYo/socrates"


def load_build_info():
    """Version shown at the bottom of the menu. The Docker build writes build-info.json
    (version, commit, repository); a copy run with ./socrates.sh has none and shows "dev"."""
    info = {"version": "dev", "revision": "", "repo": REPO_URL}
    try:
        with open(APP_DIR / "build-info.json", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            info.update({k: str(v) for k, v in data.items() if k in info and v})
    except (OSError, ValueError):
        pass
    if not info["repo"].startswith(("https://", "http://")):
        info["repo"] = REPO_URL
    return info


BUILD_INFO = load_build_info()

ARRAYS_TTL_SECONDS = 20       # how long an arrays scan is reused before rescanning
BLOG_TTL_SECONDS = 60         # how long a blog scan is reused before rescanning
SKIPPED_ARRAY_FOLDERS = {"GMW"}  # folders in the arrays root that hold no timing arrays

ARRAY_FILE_RE = re.compile(r"^(\d{8}) ([DWM]) (.+)\.(png|jpe?g)$", re.IGNORECASE)
YEAR_DIR_RE = re.compile(r"^(19|20)\d{2}$")
BLOG_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\(PB\) )?(.*)\.md$", re.IGNORECASE)
PRIVATE_CATEGORY = "Private Blog"


# --------------------------------------------------------------------------- settings

_settings_lock = threading.Lock()


def protect_settings():
    """settings.json can hold the Twelve Data API key: keep it readable by the app's user only."""
    try:
        if SETTINGS_FILE.exists() and SETTINGS_FILE.stat().st_mode & 0o077:
            os.chmod(SETTINGS_FILE, 0o600)
    except OSError:
        pass


protect_settings()


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(updates):
    with _settings_lock:
        data = load_settings()
        data.update(updates)
        tmp = SETTINGS_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)  # it can hold the Twelve Data API key
        os.replace(tmp, SETTINGS_FILE)
        return data


def resolve_location(raw):
    """Turn what the user typed into a local filesystem path.

    Accepts plain paths (with ~), file:// URLs, and network share addresses
    (smb://, afp://, nfs://, \\\\server\\share). Network shares must already be
    mounted; on macOS they are mounted under /Volumes/<share>.
    Returns (path, error_message).
    """
    value = (raw or "").strip().strip('"').strip("'").strip()
    if not value:
        return None, "Enter a folder location."

    share_parts = None
    if value.startswith("\\\\"):
        share_parts = [p for p in value[2:].replace("\\", "/").split("/") if p]
    else:
        parsed = urlparse(value)
        scheme = parsed.scheme.lower()
        if scheme == "file":
            value = unquote(parsed.path)
        elif scheme in ("smb", "afp", "nfs", "cifs"):
            share_parts = [parsed.netloc.split("@")[-1]] + [
                unquote(p) for p in parsed.path.split("/") if p
            ]

    if share_parts is not None:
        if len(share_parts) < 2:
            return None, "A network address needs a share name, e.g. smb://server/share/folder."
        share, rest = share_parts[1], share_parts[2:]
        volumes = Path("/Volumes")
        candidates = [volumes / share]
        if volumes.is_dir():
            # macOS appends "-1", "-2", ... when a share name is mounted more than once
            candidates += sorted(p for p in volumes.glob(f"{share}-*") if p.is_dir())
        for mount in candidates:
            path = mount.joinpath(*rest)
            if path.is_dir():
                return str(path), None
        return None, (
            f"The network share '{share}' is not mounted. Connect to it first "
            "(Finder > Go > Connect to Server), then enter the address again "
            f"or use the mounted path, e.g. /Volumes/{share}/..."
        )

    path = Path(os.path.expanduser(value))
    if not path.is_absolute():
        return None, "Enter an absolute path, e.g. /Users/you/Documents/Folder."
    return str(path), None


def check_readable_dir(path):
    if not os.path.exists(path):
        return "The folder does not exist or is not reachable."
    if not os.path.isdir(path):
        return "The location is a file, not a folder."
    try:
        with os.scandir(path):
            pass
    except OSError as e:
        return f"The folder cannot be read: {e.strerror or e}."
    return None


# --------------------------------------------------------------------------- arrays

def scan_arrays(root):
    """Return {ticker: {"D": {date: filename}, "W": {...}, "M": {...}}} plus skipped folder names."""
    tickers, skipped = {}, []
    with os.scandir(root) as entries:
        folders = sorted((e for e in entries if e.is_dir() and not e.name.startswith(".")),
                         key=lambda e: e.name.upper())
    for folder in folders:
        if folder.name.upper() in SKIPPED_ARRAY_FOLDERS:
            continue
        frames = {"D": {}, "W": {}, "M": {}}
        try:
            with os.scandir(folder.path) as files:
                names = [f.name for f in files if f.is_file() and not f.name.startswith(".")]
        except OSError:
            skipped.append(folder.name)
            continue
        for name in names:
            m = ARRAY_FILE_RE.match(name)
            if not m or m.group(3).upper() != folder.name.upper():
                continue
            date, frame = m.group(1), m.group(2).upper()
            try:
                datetime.strptime(date, "%Y%m%d")
            except ValueError:
                continue
            existing = frames[frame].get(date)
            # If both a png and a jpeg exist for the same array, prefer the png.
            if existing is None or (existing.lower().endswith((".jpg", ".jpeg"))
                                    and name.lower().endswith(".png")):
                frames[frame][date] = name
        if any(frames.values()):
            tickers[folder.name] = {"folder": folder.path, "frames": frames}
        else:
            skipped.append(folder.name)
    return tickers, skipped


class ArraysIndex:
    def __init__(self):
        self.lock = threading.Lock()
        self.root = None
        self.data = {}
        self.scanned_at = 0.0

    def get(self, root, force=False):
        with self.lock:
            stale = time.time() - self.scanned_at > ARRAYS_TTL_SECONDS
            if force or root != self.root or stale:
                self.data, _ = scan_arrays(root)
                self.root, self.scanned_at = root, time.time()
            return self.data


arrays_index = ArraysIndex()


def validate_arrays_root(path):
    error = check_readable_dir(path)
    if error:
        return None, error
    try:
        tickers, skipped = scan_arrays(path)
    except OSError as e:
        return None, f"The folder cannot be read: {e.strerror or e}."
    if not tickers:
        return None, ("No ticker folders with timing arrays were found. Expected subfolders such "
                      "as 'AEM' holding files named like '20261001 D AEM.png'.")
    count = sum(len(v) for t in tickers.values() for v in t["frames"].values())
    summary = f"{len(tickers)} tickers, {count:,} arrays"
    if skipped:
        summary += f" (ignored folders without arrays: {', '.join(skipped)})"
    return summary, None


# --------------------------------------------------------------------------- blog

def _unquote_yaml_scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        inner = value[1:-1]
        return re.sub(r'\\(.)', lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), inner)
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def split_front_matter(text):
    """Return (front_matter_dict, body). Tolerant parser for the ae-blog front matter format."""
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text.startswith("---"):
        return {}, text
    end = re.search(r"^---[ \t]*$", text[3:], re.MULTILINE)
    if not end:
        return {}, text
    fm_text = text[3:3 + end.start()]
    body = text[3 + end.end():].lstrip("\r\n")
    meta, current = {}, None
    for line in fm_text.splitlines():
        item = re.match(r"^\s+-\s*(.*)$", line) or re.match(r"^-\s+(.*)$", line)
        if item and current:
            if not isinstance(meta.get(current), list):
                meta[current] = []
            value = _unquote_yaml_scalar(item.group(1))
            if value:
                meta[current].append(value)
            continue
        kv = re.match(r"^([^\s:][^:]*):\s?(.*)$", line)
        if kv:
            current = kv.group(1).strip()
            value = kv.group(2).strip()
            meta[current] = _unquote_yaml_scalar(value) if value else []
    return meta, body


def read_front_matter(path):
    """Read only as much of the file as needed for the front matter."""
    with open(path, "rb") as f:
        head = f.read(8192)
        text = head.decode("utf-8", errors="replace")
        if text.startswith("---") and not re.search(r"^---[ \t]*$", text[3:], re.MULTILINE):
            text = (head + f.read()).decode("utf-8", errors="replace")
    meta, _ = split_front_matter(text)
    return meta


def post_meta_from_file(year, name, path):
    m = BLOG_FILE_RE.match(name)
    meta = read_front_matter(path)
    date = m.group(1) if m else None
    posted = meta.get("Posted")
    if not date and isinstance(posted, str) and re.match(r"^\d{4}-\d{2}-\d{2}", posted):
        date = posted[:10]
    if not date:
        return None
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return None
    categories = meta.get("Categories")
    categories = [c for c in categories if c] if isinstance(categories, list) else (
        [categories] if isinstance(categories, str) and categories else [])
    title = meta.get("Title") if isinstance(meta.get("Title"), str) and meta.get("Title") else None
    if not title:
        title = m.group(3) if m else name[:-3]
    private = bool(m and m.group(2)) or PRIVATE_CATEGORY in categories
    return {"date": date, "title": title, "private": private, "categories": categories}


def list_blog_files(root):
    """Yield (id, year, name, path, mtime, size) for every .md file in year subfolders."""
    with os.scandir(root) as entries:
        years = sorted(e.name for e in entries if e.is_dir() and YEAR_DIR_RE.match(e.name))
    for year in years:
        ydir = os.path.join(root, year)
        try:
            with os.scandir(ydir) as files:
                for f in files:
                    if f.name.startswith(".") or not f.name.lower().endswith(".md"):
                        continue
                    try:
                        if not f.is_file():
                            continue
                        st = f.stat()
                    except OSError:
                        continue
                    yield f"{year}/{f.name}", year, f.name, f.path, st.st_mtime, st.st_size
        except OSError:
            continue


class BlogIndex:
    def __init__(self):
        self.lock = threading.Lock()
        self.root = None
        self.posts = {}        # id -> meta (with "mtime", "size")
        self.scanned_at = 0.0
        self.payload = None    # cached JSON response for the client
        self._load_cache()

    def _load_cache(self):
        try:
            with open(BLOG_CACHE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            self._cache_root, self._cache_posts = data.get("root"), data.get("posts", {})
        except (OSError, ValueError):
            self._cache_root, self._cache_posts = None, {}

    def _save_cache(self):
        try:
            CACHE_DIR.mkdir(exist_ok=True)
            tmp = BLOG_CACHE_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"root": self.root, "posts": self.posts}, f)
            os.replace(tmp, BLOG_CACHE_FILE)
        except OSError:
            pass

    def _scan(self, root):
        previous = self.posts if self.root == root else (
            self._cache_posts if self._cache_root == root else {})
        found, to_read = {}, []
        for pid, year, name, path, mtime, size in list_blog_files(root):
            old = previous.get(pid)
            if old and old.get("mtime") == mtime and old.get("size") == size:
                found[pid] = old
            else:
                to_read.append((pid, year, name, path, mtime, size))

        def read(item):
            pid, year, name, path, mtime, size = item
            try:
                meta = post_meta_from_file(year, name, path)
            except OSError:
                return pid, None
            if meta:
                meta.update(mtime=mtime, size=size)
            return pid, meta

        if to_read:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for pid, meta in pool.map(read, to_read):
                    if meta:
                        found[pid] = meta
        changed = bool(to_read) or found.keys() != previous.keys()
        self.posts, self.root, self.scanned_at = found, root, time.time()
        if changed or self._cache_root != root:
            self._save_cache()
            self._cache_root, self._cache_posts = root, {}
        return changed

    def get(self, root, force=False):
        with self.lock:
            stale = time.time() - self.scanned_at > BLOG_TTL_SECONDS
            if force or root != self.root or stale or self.payload is None:
                changed = self._scan(root)
                if changed or self.payload is None:
                    self.payload = self._build_payload()
            return self.posts, self.payload

    def _build_payload(self):
        categories = sorted({c for p in self.posts.values() for c in p["categories"]
                             if c != PRIVATE_CATEGORY}, key=str.lower)
        cat_index = {c: i for i, c in enumerate(categories)}
        rows = []
        for pid, p in self.posts.items():
            rows.append([pid, p["date"], p["title"], 1 if p["private"] else 0,
                         [cat_index[c] for c in p["categories"] if c in cat_index]])
        # Latest first; for the same day keep a stable order by title
        rows.sort(key=lambda r: (r[1], r[2].lower()), reverse=True)
        return json.dumps({"categories": categories, "posts": rows}, ensure_ascii=False,
                          separators=(",", ":"))


blog_index = BlogIndex()


def validate_blog_root(path):
    error = check_readable_dir(path)
    if error:
        return None, error
    years, count = [], 0
    try:
        with os.scandir(path) as entries:
            year_dirs = sorted(e.name for e in entries if e.is_dir() and YEAR_DIR_RE.match(e.name))
        for year in year_dirs:
            n = sum(1 for _ in Path(path, year).glob("*.md"))
            if n:
                years.append(year)
                count += n
    except OSError as e:
        return None, f"The folder cannot be read: {e.strerror or e}."
    if not years:
        return None, ("No year folders with markdown posts were found. Expected subfolders such as "
                      "'2009', '2010' holding .md files.")
    span = years[0] if len(years) == 1 else f"{years[0]}–{years[-1]}"
    return f"{len(years)} year folders ({span}), {count:,} posts", None


IMAGE_LINE_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
LIST_LINE_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s+")
TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")


def prepare_markdown(body):
    """ae-blog writes one paragraph per line. Make sure every line is its own block even where a
    blank line is missing (keeping lists, quotes, tables and code fences together), and drop an
    image that repeats the previous non-blank line (private posts often contain each image twice)."""
    out, prev, in_fence, blank_before = [], None, False, False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            out.append(line)
            prev, blank_before = line, False
            continue
        if in_fence:
            out.append(line)
            continue
        if not stripped:
            if not blank_before:
                out.append("")
            blank_before = True
            continue
        if prev is not None and IMAGE_LINE_RE.match(line) and stripped == prev.strip():
            continue
        if prev is not None and not blank_before:
            same_block = (
                (LIST_LINE_RE.match(line) and LIST_LINE_RE.match(prev))
                or (stripped.startswith(">") and prev.strip().startswith(">"))
                or (TABLE_LINE_RE.match(line) and TABLE_LINE_RE.match(prev))
            )
            if not same_block:
                out.append("")
        out.append(line)
        prev, blank_before = line, False
    return "\n".join(out)


def render_post(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        meta, body = split_front_matter(f.read())
    html = markdown.markdown(prepare_markdown(body), extensions=["extra", "sane_lists"])
    html = nh3.clean(html, link_rel="noopener noreferrer")
    return meta, html


# --------------------------------------------------------------------------- web app

app = Flask(__name__, static_folder=None)


class QuietPollingFilter(logging.Filter):
    """Keep the terminal readable: the Settings and Logs pages ask for the job status every
    few seconds, so successful status requests are not printed. Every other request, and any
    status request that fails, is still printed."""
    POLLING = re.compile(r'"GET /api/(?:jobs|logs/runs|logs/lines)(?:\?\S*)? HTTP/[\d.]+" (?:200|304) ')
    ANSI = re.compile(r"\x1b\[[0-9;]*m")

    def filter(self, record):
        return not self.POLLING.search(self.ANSI.sub("", record.getMessage()))


logging.getLogger("werkzeug").addFilter(QuietPollingFilter())


def configured(key):
    path = load_settings().get(key)
    return path if path and os.path.isdir(path) else None


def require_json():
    # A JSON content type cannot be sent cross-site without a CORS preflight, which this
    # server never approves, so other web pages cannot change the settings.
    if not request.is_json:
        abort(415)
    return request.get_json(silent=True) or {}


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    response = send_from_directory(STATIC_DIR, name)
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/settings")
def get_settings():
    s = load_settings()
    return jsonify({
        "arraysRoot": s.get("arraysRoot", ""),
        "blogRoot": s.get("blogRoot", ""),
        "arraysReachable": bool(configured("arraysRoot")),
        "blogReachable": bool(configured("blogRoot")),
        "chartKeySet": bool(s.get(CHART_KEY)),
        "chartReversals": chart_reversals(s),
        "build": BUILD_INFO,
    })


@app.post("/api/settings/chart-key")
def set_chart_key():
    """Save or remove the Twelve Data API key. The key itself is never sent back."""
    body = require_json()
    if body.get("remove"):
        save_settings({CHART_KEY: ""})
        return jsonify({"ok": True, "chartKeySet": False})
    key = str(body.get("apiKey") or "").strip()
    if not CHART_KEY_RE.match(key):
        return bad_request("Paste the API key from your Twelve Data account (letters and digits only).")
    save_settings({CHART_KEY: key})
    return jsonify({"ok": True, "chartKeySet": True})


@app.post("/api/settings/chart-reversals")
def set_chart_reversals():
    body = require_json()
    value = {k: _reversal_value(k, body[k], v) if k in body else v for k, v in chart_reversals(load_settings()).items()}
    save_settings({"chartReversals": value})
    return jsonify({"ok": True, "chartReversals": value})


@app.get("/api/chart/<ticker>")
def get_chart(ticker):
    if not CHART_TICKER_RE.match(ticker):
        abort(404)
    s = load_settings()
    try:
        res = CHARTS.get(ticker, s.get(CHART_KEY, ""), s.get("chartSymbols"))
    except charts_mod.NotCovered as e:
        return jsonify({"ok": False, "covered": False, "error": str(e), "ticker": ticker})
    except charts_mod.ChartError as e:
        return jsonify({"ok": False, "error": str(e), "keySet": bool(s.get(CHART_KEY)), "retryIn": e.retry_in}), 502
    last_close = res["candles"][-1]["close"] if res["candles"] else None
    res["reversals"] = charts_mod.select_reversals(charts_mod.read_reversals(REVERSALS_DB, ticker), last_close)
    return jsonify(res)


@app.post("/api/settings/<kind>")
def set_location(kind):
    if kind not in ("arrays", "blog"):
        abort(404)
    body = require_json()
    path, error = resolve_location(body.get("path"))
    if not error:
        validator = validate_arrays_root if kind == "arrays" else validate_blog_root
        summary, error = validator(path)
    if error:
        return jsonify({"ok": False, "error": error, "path": path}), 400
    save_settings({f"{kind}Root": path})
    return jsonify({"ok": True, "path": path, "summary": summary})


@app.get("/api/settings/<kind>/check")
def check_location(kind):
    if kind not in ("arrays", "blog"):
        abort(404)
    path = load_settings().get(f"{kind}Root")
    if not path:
        return jsonify({"ok": False, "error": "Not configured yet."})
    validator = validate_arrays_root if kind == "arrays" else validate_blog_root
    summary, error = validator(path)
    return jsonify({"ok": not error, "path": path, "summary": summary, "error": error})


@app.get("/api/arrays")
def get_arrays():
    root = configured("arraysRoot")
    if not root:
        return jsonify({"error": "not-configured"}), 409
    data = arrays_index.get(root, force=request.args.get("refresh") == "1")
    tickers = {t: {f: sorted(dates) for f, dates in v["frames"].items()} for t, v in data.items()}
    return jsonify({"tickers": tickers})


@app.get("/api/arrays/image/<ticker>/<frame>/<date>")
def get_array_image(ticker, frame, date):
    root = configured("arraysRoot")
    if not root:
        abort(404)
    data = arrays_index.get(root)
    entry = data.get(ticker)
    name = entry and entry["frames"].get(frame, {}).get(date)
    if not name:
        data = arrays_index.get(root, force=True)  # the file may be newer than the last scan
        entry = data.get(ticker)
        name = entry and entry["frames"].get(frame, {}).get(date)
    if not name:
        abort(404)
    return send_file(os.path.join(entry["folder"], name), max_age=0)


@app.get("/api/blog/index")
def get_blog_index():
    root = configured("blogRoot")
    if not root:
        return jsonify({"error": "not-configured"}), 409
    _, payload = blog_index.get(root, force=request.args.get("refresh") == "1")
    return app.response_class(payload, mimetype="application/json")


@app.get("/api/blog/post")
def get_blog_post():
    root = configured("blogRoot")
    pid = request.args.get("id", "")
    if not root:
        abort(404)
    posts, _ = blog_index.get(root)
    post = posts.get(pid)
    if not post:
        abort(404)  # only files found by the index scan can be read
    year, name = pid.split("/", 1)
    try:
        meta, html = render_post(os.path.join(root, year, name))
    except OSError:
        abort(404)
    tags = meta.get("Tags") if isinstance(meta.get("Tags"), list) else []
    source = meta.get("Source") if isinstance(meta.get("Source"), str) else ""
    return jsonify({
        "id": pid,
        "date": post["date"],
        "title": post["title"],
        "private": post["private"],
        "categories": [c for c in post["categories"] if c != PRIVATE_CATEGORY],
        "tags": tags,
        "author": meta.get("Author") if isinstance(meta.get("Author"), str) else "",
        "source": source if source.startswith(("http://", "https://")) else "",
        "html": html,
    })


# --------------------------------------------------------------------------- extractors, schedule, logs

def _refresh_after_run(job):
    """A finished run may have added arrays or posts: rescan on the next request."""
    if job == "arrays":
        arrays_index.scanned_at = 0.0
    else:
        blog_index.scanned_at = 0.0


scheduler = sched_mod.Scheduler(load_settings, on_finished=_refresh_after_run)


def bad_request(message):
    return jsonify({"ok": False, "error": message}), 400


@app.get("/api/extractors")
def get_extractors():
    try:
        res = sched_mod.extractor_settings(configured("arraysRoot"))
    except ValueError as e:
        return bad_request(str(e))
    overrides = load_settings().get("chartSymbols")
    for t in res["tickers"]:
        t["chartCovered"] = charts_mod.provider_symbol(t["symbol"], overrides) is not None
    # Every market without chart data, so tickers being added can be marked before they are saved
    res["chartUncovered"] = charts_mod.uncovered(charts_mod.markets(), overrides)
    return jsonify(res)


@app.post("/api/extractors/credentials")
def set_credentials():
    body = require_json()
    try:
        sched_mod.set_credentials(body.get("username"), body.get("password"))
    except ValueError as e:
        return bad_request(str(e))
    return jsonify({"ok": True})


@app.post("/api/extractors/tickers")
def save_tickers():
    """Save the ticker changes made on the Settings page, then download the daily, weekly and
    monthly arrays of every added ticker in one run."""
    body = require_json()
    try:
        added, removed = sched_mod.save_tickers(body.get("add") or [], body.get("remove") or [],
                                                configured("arraysRoot"))
    except ValueError as e:
        return bad_request(str(e))
    for symbol in added:
        scheduler.queue_new_ticker(symbol)  # all added tickers share one queued run
    return jsonify({"ok": True, "added": added, "removed": removed})


@app.get("/api/schedule")
def get_schedule():
    return jsonify({
        "schedule": sched_mod.schedule_settings(load_settings()),
        "timezones": sched_mod.timezones(),
        "status": scheduler.status(),
    })


@app.post("/api/schedule")
def set_schedule():
    body = require_json()
    try:
        clean = sched_mod.validate_schedule(body)
    except ValueError as e:
        return bad_request(str(e))
    save_settings({"schedule": clean})
    scheduler.reanchor()
    scheduler.cleanup_logs()
    return jsonify({"ok": True, "schedule": clean, "status": scheduler.status()})


@app.get("/api/jobs")
def get_jobs():
    return jsonify(scheduler.status())


@app.post("/api/jobs/<job>/run")
def run_job(job):
    body = require_json()
    try:
        scheduler.run_now(job, frames=body.get("frames"), pages=body.get("pages"))
    except ValueError as e:
        return bad_request(str(e))
    return jsonify({"ok": True, "status": scheduler.status()})


DOWNLOAD_JOBS = {"arrays": ["arrays"], "blog": ["blog-public", "blog-private"]}


@app.post("/api/download/<kind>")
def download_now(kind):
    """The "Download now" buttons: run the jobs of a page the way the schedule runs them.
    A job that is already running or waiting is left as it is."""
    if kind not in DOWNLOAD_JOBS:
        abort(404)
    require_json()
    started = []
    for job in DOWNLOAD_JOBS[kind]:
        try:
            scheduler.run_now(job, as_scheduled=True)
            started.append(job)
        except ValueError:
            pass  # already running or waiting
    return jsonify({"ok": True, "started": started, "status": scheduler.status()})


DATE_ARG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _date_arg(name):
    value = request.args.get(name) or None
    if value and not DATE_ARG_RE.match(value):
        abort(400)
    return value


@app.get("/api/logs/runs")
def get_runs():
    job = request.args.get("job") or None
    if job and job not in sched_mod.JOBS:
        abort(400)
    runs = scheduler.runs(job=job, date_from=_date_arg("from"), date_to=_date_arg("to"))
    return jsonify({"runs": runs, "jobs": {k: v["label"] for k, v in sched_mod.JOBS.items()}})


@app.get("/api/logs/lines")
def get_log_lines():
    job = request.args.get("job") or None
    level = (request.args.get("level") or "DEBUG").upper()
    if (job and job not in sched_mod.JOBS) or level not in sched_mod.LEVELS:
        abort(400)
    lines, truncated = scheduler.log_lines(run_id=request.args.get("run") or None, job=job, min_level=level,
                                           date_from=_date_arg("from"), date_to=_date_arg("to"))
    return jsonify({"lines": lines, "truncated": truncated, "limit": sched_mod.MAX_LOG_LINES})


def main():
    parser = argparse.ArgumentParser(description="Socrates - timing arrays and blog browser")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Address to listen on (default: 127.0.0.1, this machine only). "
                             "Use 0.0.0.0 to allow other devices on the local network.")
    parser.add_argument("--port", type=int, default=8050, help="Port (default: 8050)")
    parser.add_argument("--debug", action="store_true", help="Run Flask in debug mode")
    parser.add_argument("--production", action="store_true",
                        help="Serve with waitress instead of Flask's development server "
                             "(used by the Docker image; needs the waitress package)")
    args = parser.parse_args()
    if args.production and args.debug:
        parser.error("--production and --debug cannot be combined")
    # In debug mode Flask runs the app in a second, reloading process. Only that one may run
    # the scheduler, or every scheduled job would start twice.
    if not args.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        first = sched_mod.initial_schedule(load_settings())
        if first:
            save_settings({"schedule": first})
            scheduler.reanchor()  # wait for the new time; don't run at once for a time already passed today
            print(f"Automatic downloads start at {first['startTime']} (chosen at random on the first start; "
                  "change it on the Settings page)")
        scheduler.start()
    print(f"Socrates running at http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.port}", flush=True)
    if args.production:
        # One process with threads: a second process would start a second scheduler.
        from waitress import serve
        serve(app, host=args.host, port=args.port, threads=8, ident="Socrates")
    else:
        app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
