#!/usr/bin/env python3
"""
Socrates Platform Timing Arrays Extractor
Extracts Daily, Weekly, and Monthly timing arrays from Socrates Platform
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# Disable Selenium Manager's anonymous usage stats (sent to plausible.io)
os.environ.setdefault("SE_AVOID_STATS", "true")

from selenium import webdriver
from selenium.common.exceptions import (
    InvalidSessionIdException, NoSuchElementException, NoSuchWindowException,
    TimeoutException, WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from PIL import Image

PAGE_LOAD_TIMEOUT = 60  # seconds; stops a page that never finishes loading from hanging the run
REVERSALS_DB = "reversals.sqlite"  # next to config.json
OUTDATED_EXIT = 2       # exit status: no errors, but some arrays were still the previous period's

log = logging.getLogger('socrates-arrays')

def setup_logging(level_name):
    """Log lines look like: [2026-11-11 08:48:19] INFO    message"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)-7s %(message)s',
                                           '%Y-%m-%d %H:%M:%S'))
    log.handlers[:] = [handler]
    log.setLevel(getattr(logging, level_name.upper()))
    log.propagate = False

class ExtractionError(Exception):
    """A failure whose message is already readable"""

class BrowserGone(Exception):
    """The browser crashed or was closed, so no further arrays can be extracted"""

def browser_is_gone(e):
    msg = getattr(e, 'msg', None) or ''
    return (isinstance(e, (InvalidSessionIdException, NoSuchWindowException))
            or 'chrome not reachable' in msg or 'session deleted' in msg
            or 'disconnected' in msg)

def describe_error(e):
    """Short, readable reason for a failure instead of a Python or Selenium stack trace"""
    if isinstance(e, (ExtractionError, BrowserGone)):
        return str(e)
    if isinstance(e, TimeoutException):
        return 'the page did not respond in time (the Socrates Platform may be slow or unavailable)'
    if isinstance(e, NoSuchElementException):
        return 'an expected element was not found on the page (the page layout may have changed)'
    if isinstance(e, WebDriverException):
        if browser_is_gone(e):
            return 'the browser closed unexpectedly'
        # Selenium messages end with a long native stack trace; keep the first line only
        lines = (e.msg or '').strip().splitlines()
        return f'browser error ({lines[0]})' if lines else 'browser error'
    if isinstance(e, OSError):
        return f'file error - {e.strerror or e}' + (f': {e.filename}' if e.filename else '')
    lines = str(e).strip().splitlines()
    return f'{type(e).__name__}: {lines[0]}' if lines else type(e).__name__

DEFAULT_CONFIG = Path(__file__).parent / "config.json"

def load_config(config_path=DEFAULT_CONFIG):
    """Load configuration from config.json (by default the one next to this script)"""
    config_path = Path(config_path)
    if not config_path.exists():
        raise ExtractionError(f"config file not found: {config_path}")
    try:
        with open(config_path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"{config_path.name} is not valid JSON (line {e.lineno}: {e.msg})") from None

def container_chrome(options):
    """Settings for Chromium in a container (the Socrates Docker image sets these variables):
    CHROME_BIN and CHROMEDRIVER_PATH point at the installed browser and driver, so Selenium
    never downloads one; CHROME_NO_SANDBOX=1 is needed when the container drops all privileges.
    Returns the Service to start the driver with."""
    if os.environ.get("CHROME_BIN"):
        options.binary_location = os.environ["CHROME_BIN"]
    if os.environ.get("CHROME_NO_SANDBOX") == "1":
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
    return Service(executable_path=os.environ["CHROMEDRIVER_PATH"]) if os.environ.get("CHROMEDRIVER_PATH") else Service()

def setup_driver(headless=True):
    """Initialize Chrome WebDriver"""
    options = Options()
    if headless:
        options.add_argument('--headless=new')
        options.add_argument('--disable-gpu')
        options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36')
    options.add_argument('--window-size=1320,800')
    options.add_argument('--disable-blink-features=AutomationControlled')
    # Less memory: no extensions, background services or translation, and a small cache
    for flag in ('--disable-extensions', '--disable-background-networking', '--disable-component-update',
                 '--disable-default-apps', '--disable-sync', '--no-first-run',
                 '--disable-features=Translate,OptimizationHints,MediaRouter', '--disk-cache-size=33554432'):
        options.add_argument(flag)
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)
    service = container_chrome(options)
    
    driver = webdriver.Chrome(options=options, service=service)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver

def login(driver, config):
    """Login to Socrates Platform"""
    log.debug("Logging in to Socrates Platform...")
    submitted = False
    try:
        driver.get(config['credentials']['url'])
        
        # Click login link
        login_link = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.LINK_TEXT, "Login"))
        )
        login_link.click()
        
        # Fill credentials - use placeholder text to find fields
        email_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder='Enter your email address']"))
        )
        email_field.send_keys(config['credentials']['username'])
        
        password_field = driver.find_element(By.CSS_SELECTOR, "input[placeholder='Enter your password']")
        password_field.send_keys(config['credentials']['password'])
        
        # Submit - find button by text
        submit_button = driver.find_element(By.XPATH, "//button[text()='Submit']")
        submit_button.click()
        submitted = True
        
        # Wait for login to complete
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.LINK_TEXT, "Private Blog"))
        )
    except TimeoutException:
        if submitted:
            raise ExtractionError("Login failed: it was not confirmed in time - check the username and "
                                  "password in config.json, or the Socrates Platform is slow") from None
        raise ExtractionError("Login failed: the Socrates Platform login page did not load in time "
                              "(the site may be slow or unavailable)") from None
    except KeyError as e:
        raise ExtractionError(f"Login failed: config.json has no credentials {e}") from None
    except WebDriverException as e:
        raise ExtractionError(f"Login failed: {describe_error(e)}") from None
    log.debug("Login successful")

def open_ticker_page(driver, ticker_config):
    """Open a ticker's dashboard and wait until it has loaded"""
    driver.get(ticker_config['url'])
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Daily Price Chart')]"))
    )
    time.sleep(1)

def get_timing_array(driver, ticker, ticker_config, timelevel, array_type, today):
    """Extract timing array for a specific ticker and timelevel. Returns True when the array is
    current and saved, False when the site still shows an outdated one. Raises on failure."""
    log.debug(f"Processing {ticker} - {array_type} array...")
    
    archive_dir = Path(ticker_config['archivePath'])
    if not archive_dir.is_dir():
        raise ExtractionError(f"archive folder does not exist: {archive_dir}")
    
    open_ticker_page(driver, ticker_config)
    
    # Map array_type to full timing name
    timing_name = {
        'D': 'Daily',
        'W': 'Weekly',
        'M': 'Monthly'
    }.get(array_type, array_type)
    
    # Extract tokens and make API call
    script = f"""
    const existingPopup = document.querySelector('#move-popup-01');
    if (existingPopup) existingPopup.remove();
    
    const marketId = document.querySelector('input[name="ResearchItemId"]')?.value;
    const token = document.querySelector('input[name="__RequestVerificationToken"]')?.value;
    
    if (!token || !marketId) return {{ success: false, error: 'Tokens not found' }};
    
    const response = await fetch('https://standard.socratesplatform.com/Chart/GetLatestForecastArray', {{
        method: 'POST',
        headers: {{
            'accept': '*/*',
            'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'x-requested-with': 'XMLHttpRequest'
        }},
        body: `marketId=${{marketId}}&timelevel={timelevel}&__RequestVerificationToken=${{token}}`
    }});
    
    if (!response.ok) return {{ success: false, status: response.status }};
    
    const data = await response.json();
    document.body.insertAdjacentHTML('beforeend', data.html);
    const popup = document.querySelector('#move-popup-01');
    
    if (popup) {{
        popup.style.cssText = 'position:absolute;top:100px;left:100px;width:100%;max-width:1000px;height:auto;z-index:999999;display:block;visibility:visible;overflow:visible;opacity:1';
        const blockDiv = popup.querySelector('.block.block-3c');
        if (blockDiv) {{
            blockDiv.style.cssText = 'background-color:rgb(43,51,66);border:2px solid #666;border-radius:5px;padding:20px;width:auto;height:auto;overflow:visible';
            document.body.classList.add('premium-mt-page');
            popup.querySelectorAll('.title-bl')[1].textContent = '{ticker} {timing_name} Timing Arrays';
            popup.querySelectorAll('.dateRowLabel').forEach(el => el.style.cssText = 'color:#fff;font-size:11px;text-align:center;font-family:ProximaNova-Regular');
            popup.querySelectorAll('td.datalabel').forEach(el => el.style.cssText = 'color:#fff;font-size:12px;font-family:ProximaNova-Regular');
            const table = blockDiv.querySelector('table');
            if (table) table.style.cssText = 'color:#fff;font-size:12px';
        }}
    }}
    
    const dateLabels = document.querySelectorAll('.dateRowLabel');
    const secondDate = dateLabels[1]?.textContent.trim();
    return {{ success: true, secondDate }};
    """
    
    result = driver.execute_script(script)
    
    if not result or not result.get('success'):
        result = result or {}
        reason = result.get('error') or (f"HTTP {result['status']}" if result.get('status') else 'unknown error')
        raise ExtractionError(f"the array request failed ({reason})")
    if not result.get('secondDate'):
        raise ExtractionError("the array was returned without a date label")
    
    # Date of the array, and whether the site already shows the current period's array
    second_date = result['secondDate']
    try:
        day = array_date(second_date, timelevel, today)
    except (KeyError, ValueError):
        raise ExtractionError(f"unexpected date label '{second_date}'") from None
    expected = expected_date(array_type, today)
    outdated = day < expected
    screenshot_path = archive_dir / f"{day:%Y%m%d} {array_type} {ticker}.png"
    # "Saved:" lines mark the arrays that are done; outdated ones get "Outdated:" instead, so the
    # Socrates app downloads them again on its next retry.
    stale = (f"Outdated: {ticker} {array_type} array is dated {day:%Y-%m-%d}, expected "
             f"{expected:%Y-%m-%d} or later (the site still shows the previous one)")
    if outdated and screenshot_path.exists():
        log.warning(f"{stale}; kept the existing {screenshot_path.name}")
        return False

    if not driver.execute_script(YEAR_SCRIPT, str(day.year)):
        log.debug("Title bar not found, the array is saved without the year")
    time.sleep(0.5)
    
    # Take screenshot
    if not driver.save_screenshot(str(screenshot_path)):
        raise ExtractionError(f"could not save the screenshot to {screenshot_path}")
    
    # Crop image
    crop_image(screenshot_path, driver)
    
    if outdated:
        log.warning(f"{stale}; saved {screenshot_path.name} because it was missing")
        return False
    log.debug(f"Saved: {screenshot_path.name}")
    return True

MONTHS = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
          'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}

def array_date(label, timelevel, today):
    """Date of an array from its second date label: "25-Nov" (daily, weekly) or "Nov" (monthly).
    The label has no year, so the year is the one that puts the date closest to today: an
    outdated "31-Dec" array read on 1 January belongs to the year before, not the new one."""
    if timelevel == 3:  # Monthly - only month name
        month, day = MONTHS[label], 1
    else:  # Daily/Weekly - "DD-Mon" format
        day_text, month_text = label.split('-')
        month, day = MONTHS[month_text], int(day_text)
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:  # 29-Feb in a year without it
            pass
    if not candidates:
        raise ValueError(label)
    return min(candidates, key=lambda d: abs((d - today).days))

def expected_date(array_type, today):
    """Oldest date a current array can have on `today`. The site labels a fresh array with the
    next trading day: Tuesday to Friday the daily array has that day's date, on Saturday and Sunday
    the daily and weekly arrays have the coming Monday's, and a monthly array has the 1st of the
    month. An older date means the site still shows the previous period's array. No holiday
    calendar is needed: a date later than expected (after a holiday) is still current."""
    if array_type == 'D':
        return today
    if array_type == 'W':
        week_of = today + timedelta(days=2)  # Saturday and Sunday count towards the next week
        return week_of - timedelta(days=week_of.weekday())
    return today.replace(day=1)

# Writes the array's year at the right end of the title bar, in the style of the date labels
YEAR_SCRIPT = """
const title = document.querySelectorAll('#move-popup-01 .title-bl')[1];
if (!title) return false;
title.style.display = 'flex';
title.style.justifyContent = 'space-between';
title.style.alignItems = 'center';
const year = document.createElement('span');
year.textContent = arguments[0];
year.style.cssText = 'color:#fff;font-size:11px;font-family:ProximaNova-Regular';
title.appendChild(year);
return true;
"""

def crop_image(image_path, driver):
    """Crop image to exact specifications with dynamic detection"""
    img = Image.open(image_path)
    
    # Get device pixel ratio to handle Retina displays
    pixel_ratio = driver.execute_script("return window.devicePixelRatio")
    
    # Base coordinates for 1x scaling
    base_left, base_top = 119, 119
    base_width, base_height = 960, 566
    
    # Scale coordinates by pixel ratio
    left = int(base_left * pixel_ratio)
    top = int(base_top * pixel_ratio)
    width = int(base_width * pixel_ratio)
    height = int(base_height * pixel_ratio)
    
    cropped = img.crop((left, top, left + width, top + height))
    
    # Resize to standard dimensions if pixel ratio is not 1.0
    if pixel_ratio != 1.0:
        cropped = cropped.resize((base_width, base_height), Image.LANCZOS)
    
    cropped.save(image_path)

# Reversals: the table the site shows with the R key, read from the ticker page that is already
# open. Returns its title and one entry per price, with the text lines and indicator tags.
REVERSALS_SCRIPT = """
const v = name => document.querySelector(`input[name="${name}"]`)?.value;
const marketId = v('ResearchItemId'), analysisId = v('DashboardData.DailyAnalysisId');
const token = v('__RequestVerificationToken');
if (!marketId || !analysisId || !token) return { success: false, error: 'Tokens not found' };
const response = await fetch('/Chart/GetReversalTableFullscreen', {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded; charset=UTF-8', 'x-requested-with': 'XMLHttpRequest' },
    body: new URLSearchParams({ marketId, timelevel: arguments[0], dailyAnalysisId: analysisId,
                                __RequestVerificationToken: token })
});
if (!response.ok) return { success: false, status: response.status };
const doc = new DOMParser().parseFromString((await response.json()).html, 'text/html');
const texts = cell => [...cell.childNodes].filter(n => n.nodeType === Node.TEXT_NODE)
    .map(n => n.textContent.trim()).filter(Boolean);
const tags = cell => [...cell.querySelectorAll('[data-type]')].map(s => ({
    type: s.dataset.type, value: s.dataset.value || '', kind: s.dataset.reversaltype || '', text: s.textContent.trim() }));
const blocks = {};
doc.querySelectorAll('.block').forEach(b => { blocks[b.querySelector('.title-bl')?.textContent.trim() || ''] = b; });
const titles = Object.keys(blocks);
const active = titles.find(t => t.endsWith('Active Reversals'));
const elected = titles.find(t => t.endsWith('Elected Reversals'));
// A block is left out when it has no reversals (e.g. a market with only elected ones)
if (!active && !elected) return { success: false, error: `unexpected table: ${titles.join(', ') || 'empty'}` };
const rows = [];
if (active) blocks[active].querySelectorAll('.tr').forEach(tr => {
    const [side, ...cells] = tr.children;
    cells.forEach(c => rows.push({ status: 'active', side: side.textContent.trim(), text: texts(c), tags: tags(c) }));
});
if (elected) blocks[elected].querySelectorAll('.tr > .td').forEach(c => rows.push({ status: 'elected', text: texts(c), tags: tags(c) }));
return { success: true, title: active || elected, rows };
"""

def reversal_period(text):
    """Start of the period a reversal was elected in: "Nov 19 2026" (daily),
    "Week of Nov 16 2026" (weekly) or "Month of May 2026" (monthly)."""
    parts = text.removeprefix('Week of ').removeprefix('Month of ').split()
    if len(parts) == 3:
        return date(int(parts[2]), MONTHS[parts[0]], int(parts[1]))
    if len(parts) == 2:
        return date(int(parts[1]), MONTHS[parts[0]], 1)
    raise ValueError(text)

KNOWN_TAGS = {'ReversalPosition', 'Importance', 'MajorMinor', 'Extended'}
unknown_tags = set()  # tags already reported, so each one is logged once per run

def parse_reversal(row):
    """One reversal from the table: direction, price, indicators and, once elected, its period"""
    text, tag_list = row['text'], row['tags']
    tags = {t['type']: t['value'] for t in tag_list}
    # "Dbl" (double reversal) has not been seen on the site yet, so it is recognised by its label
    # or a "Double" value on any tag
    double = any(t['text'] == 'Dbl' or 'Double' in (t['value'], t['kind']) for t in tag_list)
    for t in tag_list:
        if t['type'] not in KNOWN_TAGS and t['text'] != 'Dbl' and t['type'] not in unknown_tags:
            unknown_tags.add(t['type'])
            log.warning(f"Unknown reversal tag {t['type']}={t['value']} ('{t['text']}'), not saved")
    side = row['side'] if row['status'] == 'active' else (text[1] if len(text) > 1 else '')
    direction = side.split()[0].lower() if side else ''
    if direction not in ('bullish', 'bearish') or not text:
        raise ValueError(f"unexpected reversal {' / '.join(text) or 'without text'}")
    elected = None
    if row['status'] == 'elected':
        if len(text) < 3:
            raise ValueError(f"elected reversal without a period: {' / '.join(text)}")
        elected = reversal_period(text[2]).isoformat()
    return {
        'status': row['status'],
        'direction': direction,
        'price': float(text[0].replace(',', '')),
        'position': int(tags['ReversalPosition']) if 'ReversalPosition' in tags else None,
        'major': int(tags.get('MajorMinor') == 'Major'),
        'double': int(double),
        'extended': int('Extended' in tags),
        'order_number': int(tags['Importance']) if 'Importance' in tags else None,  # the site's tag name
        'elected_period': elected,
    }

def open_reversals_db(path):
    """The reversals database, created when missing"""
    try:
        return sqlite3.connect(path)
    except sqlite3.Error as e:
        raise ExtractionError(f"could not open the reversals database {path}: {e}") from None

def save_reversals(db, ticker, timeframe, reversals, today):
    """Merge one table read into the ticker's table. A reversal is the same one when its time frame,
    direction, price and position match: a known active one is updated, or marked elected when it
    shows up on the elected side, and anything not yet stored is added. Returns (added, elected)."""
    table = '"' + ticker.replace('"', '""') + '"'
    day = today.isoformat()
    added = newly_elected = 0
    with db:
        db.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
            id INTEGER PRIMARY KEY,
            timeframe TEXT NOT NULL,       -- D, W or M
            direction TEXT NOT NULL,       -- bullish or bearish
            price REAL NOT NULL,
            position INTEGER,              -- 1-4, the site's ReversalPosition number (not a rank)
            major INTEGER NOT NULL,        -- 1 for "Maj"
            double INTEGER NOT NULL,       -- 1 for "Dbl"
            extended INTEGER NOT NULL,     -- 1 for "Ext"
            order_number INTEGER,          -- the order number, when shown (the site's Importance tag)
            status TEXT NOT NULL,          -- active or elected
            elected_period TEXT,           -- first day of the day, week or month it was elected in
            first_seen TEXT NOT NULL       -- date of the run that found it first
            )""")
        known = db.execute(f"SELECT id, direction, price, position, status, elected_period FROM {table} "
                           "WHERE timeframe = ?", (timeframe,)).fetchall()
        used = set()

        def find(r, status, period=None):
            for rid, direction, price, position, st, ep in known:
                if (rid not in used and st == status and (direction, price, position) == (r['direction'], r['price'], r['position'])
                        and (status == 'active' or ep == period)):
                    used.add(rid)
                    return rid
            return None

        def update(rid, r):
            db.execute(f"UPDATE {table} SET major = ?, double = ?, extended = ?, order_number = ?, status = ?, elected_period = ? "
                       "WHERE id = ?",
                       (r['major'], r['double'], r['extended'], r['order_number'], r['status'], r['elected_period'], rid))

        # Elected ones first, so a reversal that was just elected is matched to its active row
        for r in sorted(reversals, key=lambda r: r['status'] != 'elected'):
            rid = find(r, r['status'], r['elected_period'])
            if rid is None and r['status'] == 'elected':
                rid = find(r, 'active')
                newly_elected += rid is not None
            if rid is not None:
                update(rid, r)
            else:
                db.execute(f"INSERT INTO {table} (timeframe, direction, price, position, major, double, extended, order_number, "
                           "status, elected_period, first_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (timeframe, r['direction'], r['price'], r['position'], r['major'], r['double'], r['extended'],
                            r['order_number'], r['status'], r['elected_period'], day))
                added += 1
    return added, newly_elected

def get_reversals(driver, db, ticker, ticker_config, timelevel, array_type, today, reload):
    """Read the ticker's reversals for one time frame and store them. The ticker page is already
    open after its array; reload is set when the array step failed and may have left another page."""
    log.debug(f"Processing {ticker} - {array_type} reversals...")
    if reload:
        open_ticker_page(driver, ticker_config)
    result = driver.execute_script(REVERSALS_SCRIPT, timelevel) or {}
    if not result.get('success'):
        reason = result.get('error') or (f"HTTP {result['status']}" if result.get('status') else 'unknown error')
        raise ExtractionError(f"the reversals request failed ({reason})")
    timing_name = {'D': 'Daily', 'W': 'Weekly', 'M': 'Monthly'}[array_type]
    if not result['title'].startswith(timing_name):
        raise ExtractionError(f"expected the {timing_name} reversals, the site returned '{result['title']}'")
    try:
        reversals = [parse_reversal(row) for row in result['rows']]
    except (KeyError, ValueError) as e:
        raise ExtractionError(f"could not read the reversals table: {e}") from None
    try:
        added, elected = save_reversals(db, ticker, array_type, reversals, today)
    except sqlite3.Error as e:
        raise ExtractionError(f"could not save the reversals: {e}") from None
    active = sum(r['status'] == 'active' for r in reversals)
    log.debug(f"Reversals saved: {ticker} {array_type}, {active} active and {len(reversals) - active} elected "
              f"on the site, {added} new, {elected} newly elected")  # the app counts these lines

def quit_driver(driver):
    """Close the browser, ignoring errors from a browser that already crashed"""
    try:
        driver.quit()
    except Exception:
        pass

def extract(args):
    """Run the extraction. Returns the exit status."""
    frames = [(level, code) for level, code, wanted in
              ((1, 'D', args.daily), (2, 'W', args.weekly), (3, 'M', args.monthly)) if wanted]
    
    # Load configuration
    config = load_config(args.config)
    
    # Determine which tickers to process
    if args.ticker:
        ticker_list = [t.strip().upper() for t in args.ticker.split(',') if t.strip()]
        unknown = [t for t in ticker_list if t not in config['tickers']]
        if unknown:
            log.warning(f"Ignoring ticker(s) not in config.json: {', '.join(unknown)}")
        tickers = {t: config['tickers'][t] for t in ticker_list if t in config['tickers']}
        if not tickers:
            raise ExtractionError("No valid tickers specified")
    else:
        tickers = config['tickers']
    
    codes = ', '.join(code for _, code in frames)
    what = (f"reversals: {codes}" if args.reversals_only else
            f"arrays: {codes}" + (", with reversals" if args.reversals else ""))
    log.info(f"Starting extraction for {len(tickers)} ticker(s), {what}")
    
    today = datetime.now().date()  # the run's date, also when it goes past midnight
    created = 0
    failed = []    # "TICKER F" for each array that could not be saved
    outdated = []  # "TICKER F" for each array the site still showed for the previous period
    rev_saved = 0
    rev_failed = []  # "TICKER F" for each time frame whose reversals could not be saved
    # The reversals database lives next to config.json, in the same data folder
    db = open_reversals_db(Path(args.config).resolve().parent / REVERSALS_DB) if args.reversals else None
    driver = None
    try:
        # Setup browser
        try:
            driver = setup_driver(headless=not args.interactive)
        except WebDriverException as e:
            raise ExtractionError(f"Could not start Chrome: {describe_error(e)}") from None
        
        # Login
        login(driver, config)
        
        # Process each ticker; a failed array is logged and the run continues
        for ticker, ticker_config in tickers.items():
            for timelevel, array_type in frames:
                # The reversals read the page the array step opened; without it, or when it
                # failed, they open the ticker page themselves
                page_needed = args.reversals_only
                if not args.reversals_only:
                    try:
                        if get_timing_array(driver, ticker, ticker_config, timelevel, array_type, today):
                            created += 1
                        else:
                            outdated.append(f"{ticker} {array_type}")
                    except Exception as e:
                        page_needed = True
                        failed.append(f"{ticker} {array_type}")
                        log.error(f"Failed: {ticker} - {array_type} array: {describe_error(e)}")
                        if isinstance(e, WebDriverException) and browser_is_gone(e):
                            raise BrowserGone("The browser closed unexpectedly, stopping") from None
                if not db:
                    continue
                try:
                    get_reversals(driver, db, ticker, ticker_config, timelevel, array_type, today, page_needed)
                    rev_saved += 1
                except Exception as e:
                    rev_failed.append(f"{ticker} {array_type}")
                    log.error(f"Failed: {ticker} - {array_type} reversals: {describe_error(e)}")
                    if isinstance(e, WebDriverException) and browser_is_gone(e):
                        raise BrowserGone("The browser closed unexpectedly, stopping") from None
    except (ExtractionError, BrowserGone) as e:
        log.error(str(e))
    finally:
        if driver:
            quit_driver(driver)
        if db:
            db.close()
    
    level, message, status = run_summary(len(tickers) * len(frames), args, created, failed, outdated,
                                         rev_saved, rev_failed)
    log.log(level, message)
    return status

def run_summary(expected, args, created, failed, outdated, rev_saved, rev_failed):
    """The run's last log line and exit status. Arrays are counted as files, reversals as the
    tables saved (one per ticker and time frame); the Socrates app shows this line as the result."""
    arrays = not args.reversals_only
    not_run = expected - created - len(failed) - len(outdated) if arrays else 0
    rev_not_run = expected - rev_saved - len(rev_failed) if args.reversals else 0
    parts = []
    if arrays:
        parts.append(f"{created} file(s) created" if created == expected else
                     f"{created} of {expected} file(s) {'created' if failed or not_run else 'up to date'}")
    if args.reversals:
        parts.append(f"{rev_saved} reversal table(s) saved" if rev_saved == expected else
                     f"{rev_saved} of {expected} reversal table(s) saved")
    problems = []
    if failed:
        problems.append(f"failed: {', '.join(failed)}")
    if rev_failed:
        problems.append(f"reversals failed: {', '.join(rev_failed)}")
    if outdated:
        problems.append(f"outdated: {', '.join(outdated)}")
    if not_run or rev_not_run:
        problems.append(f"{max(not_run, rev_not_run)} not attempted")
    text = ', '.join(parts + problems)
    if failed or rev_failed or not_run or rev_not_run:
        return logging.ERROR, f"Extraction finished with errors: {text}", 1
    if outdated:
        return logging.WARNING, f"Extraction finished with outdated arrays: {text}", OUTDATED_EXIT
    return logging.INFO, f"Extraction complete! {text}", 0

def main():
    parser = argparse.ArgumentParser(
        description='Extract Socrates Platform timing arrays and reversals',
        epilog='Timeframes: -d, -w and -m can be combined (e.g. -dwm); without any, daily is used. '
               'Exit status: 0 when every array (and set of reversals) was saved and the arrays are current, 1 when anything failed, '
               '2 when nothing failed but some arrays were still outdated on the site, '
               '130 when interrupted with Ctrl+C.')
    parser.add_argument('-d', '--daily', action='store_true', help='Daily timeframe')
    parser.add_argument('-w', '--weekly', action='store_true', help='Weekly timeframe')
    parser.add_argument('-m', '--monthly', action='store_true', help='Monthly timeframe')
    parser.add_argument('--reversals', action='store_true',
                        help=f'Also save the reversals of the chosen timeframes, in {REVERSALS_DB} next to the config file')
    parser.add_argument('--reversals-only', action='store_true',
                        help='Save only the reversals of the chosen timeframes, without the arrays')
    parser.add_argument('--ticker', type=str, help='Comma-separated list of tickers (e.g., TQQQ,UCO)')
    parser.add_argument('--interactive', action='store_true', help='Open a visible browser window (default is headless, no GUI)')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG),
                        help='Path to the config file (default: config.json next to this script)')
    parser.add_argument('--log-level', default='info', choices=['debug', 'info', 'warning', 'error'],
                        help='Lowest level of log messages to show (default: info; '
                             'debug also shows progress)')
    
    args = parser.parse_args()
    setup_logging(args.log_level)
    args.reversals = args.reversals or args.reversals_only
    
    # Default to daily if no flags specified
    if not (args.daily or args.weekly or args.monthly):
        args.daily = True
    
    try:
        return extract(args)
    except KeyboardInterrupt:
        log.warning("Interrupted by the user")
        return 130
    except ExtractionError as e:
        log.error(str(e))
        return 1
    except Exception as e:
        log.error(f"Unexpected error: {describe_error(e)}")
        log.debug("Details of the error above:", exc_info=True)
        return 1

if __name__ == '__main__':
    sys.exit(main())
