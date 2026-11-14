#!/usr/bin/env python3
"""
Socrates Platform Timing Arrays Extractor
Extracts Daily, Weekly, and Monthly timing arrays from Socrates Platform
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
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

def get_timing_array(driver, ticker, ticker_config, timelevel, array_type):
    """Extract timing array for a specific ticker and timelevel. Raises on failure."""
    log.debug(f"Processing {ticker} - {array_type} array...")
    
    archive_dir = Path(ticker_config['archivePath'])
    if not archive_dir.is_dir():
        raise ExtractionError(f"archive folder does not exist: {archive_dir}")
    
    # Navigate to ticker dashboard
    driver.get(ticker_config['url'])
    
    # Wait for page load
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Daily Price Chart')]"))
    )
    time.sleep(1)
    
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
    
    time.sleep(0.5)
    
    # Format date
    second_date = result['secondDate']
    try:
        formatted_date = format_date(second_date, timelevel)
    except (KeyError, ValueError):
        raise ExtractionError(f"unexpected date label '{second_date}'") from None
    
    # Take screenshot
    screenshot_path = archive_dir / f"{formatted_date} {array_type} {ticker}.png"
    if not driver.save_screenshot(str(screenshot_path)):
        raise ExtractionError(f"could not save the screenshot to {screenshot_path}")
    
    # Crop image
    crop_image(screenshot_path, driver)
    
    log.debug(f"Saved: {screenshot_path.name}")
    return screenshot_path

def format_date(date_str, timelevel):
    """Format date string to YYYYMMDD"""
    months = {
        'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
        'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
        'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'
    }
    
    current_year = datetime.now().year
    
    if timelevel == 3:  # Monthly - only month name
        month = months[date_str]
        return f"{current_year}{month}01"
    else:  # Daily/Weekly - "DD-Mon" format
        day, month = date_str.split('-')
        return f"{current_year}{months[month]}{day.zfill(2)}"

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
    
    log.info(f"Starting extraction for {len(tickers)} ticker(s), "
             f"arrays: {', '.join(code for _, code in frames)}")
    
    created = 0
    failed = []  # "TICKER F" for each array that could not be saved
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
                try:
                    get_timing_array(driver, ticker, ticker_config, timelevel, array_type)
                    created += 1
                except Exception as e:
                    failed.append(f"{ticker} {array_type}")
                    log.error(f"Failed: {ticker} - {array_type} array: {describe_error(e)}")
                    if isinstance(e, WebDriverException) and browser_is_gone(e):
                        raise BrowserGone("The browser closed unexpectedly, stopping") from None
    except (ExtractionError, BrowserGone) as e:
        log.error(str(e))
    finally:
        if driver:
            quit_driver(driver)
    
    expected = len(tickers) * len(frames)
    if created == expected:
        log.info(f"Extraction complete! {created} file(s) created")
        return 0
    not_run = expected - created - len(failed)
    summary = f"Extraction finished with errors: {created} of {expected} file(s) created"
    if failed:
        summary += f", failed: {', '.join(failed)}"
    if not_run:
        summary += f", {not_run} not attempted"
    log.error(summary)
    return 1

def main():
    parser = argparse.ArgumentParser(
        description='Extract Socrates Platform timing arrays',
        epilog='Exit status: 0 when every array was saved, 1 when anything failed, '
               '130 when interrupted with Ctrl+C.')
    parser.add_argument('-d', '--daily', action='store_true', help='Extract Daily arrays')
    parser.add_argument('-w', '--weekly', action='store_true', help='Extract Weekly arrays')
    parser.add_argument('-m', '--monthly', action='store_true', help='Extract Monthly arrays')
    parser.add_argument('--ticker', type=str, help='Comma-separated list of tickers (e.g., TQQQ,UCO)')
    parser.add_argument('--interactive', action='store_true', help='Open a visible browser window (default is headless, no GUI)')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG),
                        help='Path to the config file (default: config.json next to this script)')
    parser.add_argument('--log-level', default='info', choices=['debug', 'info', 'warning', 'error'],
                        help='Lowest level of log messages to show (default: info; '
                             'debug also shows progress)')
    
    args = parser.parse_args()
    setup_logging(args.log_level)
    
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
