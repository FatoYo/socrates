"""
Daily candles for the chart on the Arrays page, from Twelve Data (https://twelvedata.com).

A ticker's candles are fetched when its chart is opened and kept in prices.sqlite, so opening it
again costs no API credit until the data can have changed: about a minute while the US market is
open (crypto trades around the clock), otherwise until the next session ends. The API key is
sent in a header, never in the URL, so it does not end up in logs.
"""
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

API_URL = "https://api.twelvedata.com/time_series"
CANDLES = 500                 # candles loaded per ticker (one request, one credit either way)
LIVE_SECONDS = 60             # how long a ticker's candles are reused while its market is open
SETTLE = timedelta(minutes=15)  # after the close, wait this long before the day's candle counts as final
CALLS_PER_MINUTE = 8          # the free Basic plan's limit
NEW_YORK = ZoneInfo("America/New_York")
SESSION_OPEN, SESSION_CLOSE = (9, 30), (16, 0)
# Socrates names that Twelve Data writes differently. "chartSymbols" in settings.json can add or
# override entries, e.g. {"BTC": "BTC/USD"}.
DEFAULT_SYMBOLS = {"BTC": "BTC/USD"}
# Every Socrates Platform market and its Twelve Data symbol on the free plan (null: not available)
MARKETS_FILE = Path(__file__).resolve().parent / "chart-markets.json"
NOT_COVERED = "Twelve Data has no daily prices for this market on the free plan, so there is no chart."
_markets = None


class ChartError(Exception):
    """Candles could not be loaded; the message is shown on the chart. `retry_in`: seconds after
    which asking again can work (a request limit), else None."""

    def __init__(self, message, retry_in=None):
        super().__init__(message)
        self.retry_in = retry_in


class NotCovered(ChartError):
    """The market has no chart data; no request is made."""


def markets():
    global _markets
    if _markets is None:
        try:
            _markets = json.loads(MARKETS_FILE.read_text(encoding="utf-8"))["markets"]
        except (OSError, ValueError, KeyError):
            _markets = {}
    return _markets


def provider_symbol(ticker, overrides=None):
    """The Twelve Data symbol for a Socrates ticker, or None when the market isn't covered. A ticker
    missing from the market list is tried as it is."""
    if isinstance(overrides, dict):
        own = {str(k).upper(): str(v) for k, v in overrides.items() if v}
        if ticker in own:
            return own[ticker]
    if ticker in DEFAULT_SYMBOLS:
        return DEFAULT_SYMBOLS[ticker]
    known = markets()
    return known[ticker] if ticker in known else ticker


def uncovered(tickers, overrides=None):
    """The tickers whose markets have no chart data."""
    return sorted(t for t in tickers if provider_symbol(t, overrides) is None)


def is_crypto(symbol):
    return "/" in symbol


def _session(day):
    """Open and close of the US session on a date (New York time)."""
    return (datetime(day.year, day.month, day.day, *SESSION_OPEN, tzinfo=NEW_YORK),
            datetime(day.year, day.month, day.day, *SESSION_CLOSE, tzinfo=NEW_YORK))


def market_open(now):
    """Whether the US stock market is in its regular session. Holidays are not known, so on a
    holiday this says open and the chart just asks a little more often."""
    ny = now.astimezone(NEW_YORK)
    start, end = _session(ny.date())
    return ny.weekday() < 5 and start <= ny < end


def last_close(now):
    """The end of the most recent US session at or before now."""
    day = now.astimezone(NEW_YORK).date()
    while True:
        _, end = _session(day)
        if day.weekday() < 5 and end <= now:
            return end
        day -= timedelta(days=1)


def is_fresh(fetched_at, symbol, now):
    """Whether cached candles fetched at `fetched_at` are still current."""
    if now - fetched_at < timedelta(seconds=LIVE_SECONDS):
        return True
    if is_crypto(symbol) or market_open(now):
        return False
    return fetched_at >= last_close(now) + SETTLE


class Limiter:
    """At most CALLS_PER_MINUTE requests in any 60 seconds, across all users of the app."""

    def __init__(self, per_minute=CALLS_PER_MINUTE, clock=time.monotonic):
        self.per_minute, self.clock, self.calls = per_minute, clock, deque()
        self.until = 0  # no requests before this time (after Twelve Data refused one)

    def block(self, seconds):
        self.until = max(self.until, self.clock() + seconds)

    def take(self):
        now = self.clock()
        if now < self.until:
            return False
        while self.calls and now - self.calls[0] >= 60:
            self.calls.popleft()
        if len(self.calls) >= self.per_minute:
            return False
        self.calls.append(now)
        return True

    def wait(self):
        """Seconds until the next request is allowed (0 when one is allowed now)."""
        now = self.clock()
        if now < self.until:
            return max(1, int(self.until - now) + 1)
        while self.calls and now - self.calls[0] >= 60:
            self.calls.popleft()
        return 0 if len(self.calls) < self.per_minute else max(1, int(60 - (now - self.calls[0])) + 1)


class Candles:
    def __init__(self, db_path, fetch=None, clock=lambda: datetime.now(timezone.utc)):
        self.db_path = db_path
        self.fetch = fetch or fetch_twelve_data
        self.clock = clock
        self.limiter = Limiter()
        self.lock = threading.Lock()  # one request at a time, so two clicks don't both spend a credit

    def _db(self):
        db = sqlite3.connect(self.db_path)
        db.execute("""CREATE TABLE IF NOT EXISTS candles (
            symbol TEXT NOT NULL, day TEXT NOT NULL,
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL,
            PRIMARY KEY (symbol, day))""")
        db.execute("CREATE TABLE IF NOT EXISTS fetches (symbol TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, "
                   "requested INTEGER NOT NULL DEFAULT 0)")
        try:  # caches made before the number of candles was stored
            db.execute("ALTER TABLE fetches ADD COLUMN requested INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        return db

    def _cached(self, db, symbol):
        rows = db.execute("SELECT day, open, high, low, close FROM candles WHERE symbol = ? "
                          "ORDER BY day DESC LIMIT ?", (symbol, CANDLES)).fetchall()
        row = db.execute("SELECT fetched_at, requested FROM fetches WHERE symbol = ?", (symbol,)).fetchone()
        # A cache filled with fewer candles than are asked for now counts as not fetched
        fetched = datetime.fromisoformat(row[0]) if row and row[1] >= CANDLES else None
        return [{"time": d, "open": o, "high": h, "low": l, "close": c} for d, o, h, l, c in reversed(rows)], fetched

    def get(self, ticker, api_key, overrides=None):
        """The ticker's last CANDLES daily candles, from the cache when it is current."""
        symbol = provider_symbol(ticker, overrides)
        if symbol is None:
            raise NotCovered(NOT_COVERED)
        with self.lock:
            db = self._db()
            try:
                candles, fetched = self._cached(db, symbol)
                now = self.clock()
                note, retry_in = "", None
                if not (candles and fetched and is_fresh(fetched, symbol, now)):
                    if not api_key:
                        raise ChartError("Add a Twelve Data API key in Settings to see the chart.")
                    if not self.limiter.take():
                        retry_in = self.limiter.wait()
                        note = f"Twelve Data allows 8 requests a minute; the newest data loads in {retry_in} s."
                    else:
                        try:
                            rows = self.fetch(symbol, api_key, CANDLES)
                        except ChartError as e:
                            if e.retry_in:
                                self.limiter.block(e.retry_in)
                            if not candles:
                                raise
                            retry_in = e.retry_in
                            note = f"{e} Showing the data from the last successful load."
                        else:
                            with db:
                                db.executemany("INSERT OR REPLACE INTO candles VALUES (?, ?, ?, ?, ?, ?, ?)",
                                               [(symbol, r["time"], r["open"], r["high"], r["low"], r["close"],
                                                 r.get("volume")) for r in rows])
                                db.execute("INSERT OR REPLACE INTO fetches VALUES (?, ?, ?)", (symbol, now.isoformat(), CANDLES))
                            candles, fetched = self._cached(db, symbol)
                    if not candles:
                        raise ChartError(note or f"Twelve Data returned no candles for {symbol}.", retry_in)
            finally:
                db.close()
        return {"ticker": ticker, "symbol": symbol, "candles": candles,
                "fetchedAt": fetched.isoformat() if fetched else None, "note": note, "retryIn": retry_in}


def fetch_twelve_data(symbol, api_key, count):
    """Daily candles, oldest first: [{"time": "YYYY-MM-DD", "open", "high", "low", "close", "volume"}]."""
    query = urlencode({"symbol": symbol, "interval": "1day", "outputsize": count, "order": "asc"})
    request = urllib.request.Request(f"{API_URL}?{query}", headers={
        "Authorization": f"apikey {api_key}", "User-Agent": "Socrates (+https://github.com/FatoYo/socrates)"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
    except urllib.error.HTTPError as e:
        try:
            data = json.load(e)
        except ValueError:
            raise ChartError(f"Twelve Data answered with HTTP {e.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise ChartError(f"Twelve Data could not be reached ({reason}).") from None
    except ValueError:
        raise ChartError("Twelve Data sent an answer that is not JSON.") from None
    if not isinstance(data, dict) or data.get("status") == "error":
        code = data.get("code") if isinstance(data, dict) else None
        message = (data.get("message") if isinstance(data, dict) else "") or "unknown error"
        if code == 401:
            raise ChartError("Twelve Data rejected the API key. Check it in Settings.")
        if code == 429:
            # Twelve Data counts its own minute (and a daily cap), so ask again after a full minute
            raise ChartError("Twelve Data's request limit was reached (8 a minute, 800 a day on the free plan).", 60)
        raise ChartError(f"Twelve Data has no daily data for {symbol}: {message}")
    try:
        return [{"time": v["datetime"][:10], "open": float(v["open"]), "high": float(v["high"]),
                 "low": float(v["low"]), "close": float(v["close"]),
                 "volume": float(v["volume"]) if v.get("volume") not in (None, "") else None}
                for v in data.get("values") or []]
    except (KeyError, TypeError, ValueError):
        raise ChartError("Twelve Data sent candles in an unexpected format.") from None


# --------------------------------------------------------------------------- reversals

RAYS_PER_GROUP = 5  # most per group (active bullish, active bearish, elected); the page shows the top N of each


def read_reversals(db_path, ticker):
    """The ticker's weekly and monthly reversals from the arrays extractor's reversals.sqlite
    (read-only). Missing file or table: no reversals."""
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (ticker,)).fetchone():
            return []
        cols = {r[1] for r in db.execute(f'PRAGMA table_info("{ticker}")')}
        double = '"double"' if "double" in cols else "0"
        rows = db.execute(f'SELECT timeframe, direction, price, major, {double}, status, elected_period, first_seen, id '
                          f'FROM "{ticker}" WHERE timeframe IN (\'W\', \'M\')').fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()
    return [{"timeframe": t, "direction": d, "price": p, "major": bool(m), "double": bool(dbl), "status": s,
             "electedPeriod": e, "firstSeen": f, "id": i} for t, d, p, m, dbl, s, e, f, i in rows]


def period_end(timeframe, start):
    """Last day of the week (its Friday) or month that starts on `start` ("YYYY-MM-DD")."""
    day = datetime.strptime(start, "%Y-%m-%d").date()
    if timeframe == "W":
        return (day + timedelta(days=4)).isoformat()
    following = day.replace(day=28) + timedelta(days=4)
    return (following - timedelta(days=following.day)).isoformat()


def select_reversals(reversals, last_close):
    """The reversals to draw, ranked within each group of a timeframe: active bullish and active
    bearish nearest to the last close first, elected most recent first, Major (and Double) reversals
    before all others. Up to RAYS_PER_GROUP per group; `rank` 0 is the top one. Each also gets
    `start`: the day it was first seen if active, the last day of its period if elected."""
    def strong(r):
        return r["major"] or r["double"]

    def top(group, start):
        """The first RAYS_PER_GROUP of a sorted group, skipping a reversal whose ray would lie on one
        already taken (same price and start; the site lists such pairs with other tags). As the
        group is sorted with Major first, the Major one of such a pair is kept."""
        out, seen = [], set()
        for r in group:
            where = (r["price"], start(r))
            if where in seen:
                continue
            seen.add(where)
            out.append(dict(r, start=where[1], rank=len(out)))
            if len(out) == RAYS_PER_GROUP:
                break
        return out

    chosen = []
    for tf in ("W", "M"):
        mine = [r for r in reversals if r["timeframe"] == tf]
        for direction in ("bullish", "bearish"):
            active = [r for r in mine if r["status"] == "active" and r["direction"] == direction]
            active.sort(key=lambda r: (not strong(r), abs(r["price"] - last_close) if last_close else 0))
            chosen += top(active, lambda r: r["firstSeen"])
        elected = [r for r in mine if r["status"] == "elected" and r["electedPeriod"]]
        elected.sort(key=lambda r: (not strong(r), _neg_date(r["electedPeriod"]), -r["id"]))
        chosen += top(elected, lambda r: period_end(tf, r["electedPeriod"]))
    for r in chosen:
        for k in ("firstSeen", "id"):
            r.pop(k, None)
    return chosen


def _neg_date(day):
    """Sort key that puts later dates first."""
    return -int(day.replace("-", ""))
