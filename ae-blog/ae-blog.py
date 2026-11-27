#!/usr/bin/env python3
import sys
import os
import re
import argparse
import json
import logging
import threading
from pathlib import Path
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from bs4.element import NavigableString, PreformattedString
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "https://www.armstrongeconomics.com/blog/"
PRIVATE_BLOG_BASE = "https://standard.socratesplatform.com/Blog/Articles"
PRIVATE_PREFIX = "(PB)"
MAX_TITLE_LENGTH = 200

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger('ae-blog')

class ErrorCounter(logging.Handler):
    """Counts ERROR messages so the script can exit with status 1 when anything failed."""
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.count = 0

    def emit(self, record):
        self.count += 1

error_counter = ErrorCounter()

def setup_logging(level_name):
    """Log lines look like: [2026-11-11 08:48:19] INFO    message"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level_name.upper()))
    handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)-7s %(message)s',
                                           '%Y-%m-%d %H:%M:%S'))
    log.handlers[:] = [handler, error_counter]
    log.setLevel(logging.DEBUG)
    log.propagate = False

def describe_error(e):
    """Short, readable reason for a failure instead of the raw exception text"""
    if isinstance(e, requests.exceptions.Timeout):
        return 'the website did not respond in time'
    if isinstance(e, requests.exceptions.HTTPError):
        code = e.response.status_code if e.response is not None else 'error'
        return f'the website returned HTTP {code}'
    if isinstance(e, requests.exceptions.ConnectionError):
        return 'could not connect to the website'
    selenium_errors = sys.modules.get('selenium.common.exceptions')
    if selenium_errors and isinstance(e, selenium_errors.TimeoutException):
        return 'the page did not respond in time'
    if selenium_errors and isinstance(e, selenium_errors.WebDriverException):
        # Selenium messages end with a long native stack trace; keep the first line only
        msg = (e.msg or '').strip().splitlines()
        return f"browser error ({msg[0]})" if msg else 'browser error'
    text = str(e).strip().splitlines()
    return f"{type(e).__name__}: {text[0]}" if text else type(e).__name__

def load_config(config_path):
    """Load configuration from JSON file"""
    if not config_path.exists():
        log.error(f"Config file not found: {config_path}")
        sys.exit(1)
    
    with open(config_path) as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# File naming (single source of truth for saving AND duplicate checks)
# ---------------------------------------------------------------------------

def clean_title_for_obsidian(title):
    """Turn a blog post title into an Obsidian-safe file name (without extension).

    Obsidian refuses file names containing  * " \\ / < > : | ?  and internal
    links break on  # ^ [ ] |.  Each is replaced with a readable equivalent;
    the original title is kept unchanged in the front matter and H1 heading.
    """
    title = title.replace('\xa0', ' ')
    title = re.sub(r'[\x00-\x1f\x7f]', '', title)       # control characters
    title = re.sub(r'\s*:\s*', ' - ', title)             # "A: B"     -> "A - B"
    title = re.sub(r'(?<=\d)\s*/\s*(?=\d)', '-', title)  # "12/27/2013" -> "12-27-2013"
    title = re.sub(r'\b[wW]/\s*', 'with ', title)        # "w/ Martin"   -> "with Martin"
    title = re.sub(r'\s*/\s*', ' or ', title)            # "Inflation/Deflation" -> "Inflation or Deflation"
    title = re.sub(r'\s*[\\|]\s*', ' - ', title)         # "A|B"      -> "A - B"
    title = re.sub(r'#(?=\d)', 'No. ', title)            # "#1"       -> "No. 1"
    title = title.replace('"', "'")                     # straight double quote
    title = title.replace('[', '(').replace(']', ')')
    title = re.sub(r'\s<\s', ' less than ', title)       # "A < B" comparison
    title = re.sub(r'\s>\s', ' greater than ', title)    # "A > B" comparison
    title = title.replace('*', '_')                     # "F**k"     -> "F__k"
    title = re.sub(r'[?<>#^]', '', title)                # drop the rest
    title = re.sub(r'\s+', ' ', title)
    title = re.sub(r'\s+-(?:\s+-)+\s+', ' - ', title)    # collapse " - - " runs
    title = title.strip(' .-')                          # no leading/trailing dots or dashes
    if len(title) > MAX_TITLE_LENGTH:
        title = title[:MAX_TITLE_LENGTH].rstrip(' .-')
    return title or 'Untitled'

def post_filepath(archive_path, title, date_obj, private=False):
    """Archive path for a post: <archive>/<YYYY>/<YYYY-MM-DD> [(PB) ]<clean title>.md"""
    prefix = f"{PRIVATE_PREFIX} " if private else ""
    filename = f"{date_obj.strftime('%Y-%m-%d')} {prefix}{clean_title_for_obsidian(title)}.md"
    return Path(archive_path) / date_obj.strftime('%Y') / filename

def parse_listing_date(date_str):
    """Parse a listing date such as 'December 29, 2025'"""
    return datetime.strptime(date_str, '%B %d, %Y')

def file_exists(title, date_str, archive_path, private=False):
    """Check if blog post file already exists"""
    try:
        return post_filepath(archive_path, title, parse_listing_date(date_str), private).exists()
    except ValueError:
        return False

# ---------------------------------------------------------------------------
# Private blog login
# ---------------------------------------------------------------------------

def setup_driver(interactive=False):
    """Initialize Chrome WebDriver (headless unless interactive is True)"""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    
    options = Options()
    if not interactive:
        options.add_argument('--headless=new')
    options.add_argument('--window-size=1320,800')
    options.add_argument('--disable-blink-features=AutomationControlled')
    # Less memory: no extensions, background services or translation, and a small cache
    for flag in ('--disable-extensions', '--disable-background-networking', '--disable-component-update',
                 '--disable-default-apps', '--disable-sync', '--no-first-run',
                 '--disable-features=Translate,OptimizationHints,MediaRouter', '--disk-cache-size=33554432'):
        options.add_argument(flag)
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)
    
    # Chromium in a container (the Socrates Docker image sets these): use the installed
    # browser and driver instead of downloading one, and run without Chrome's sandbox,
    # which needs privileges a hardened container does not have.
    if os.environ.get('CHROME_BIN'):
        options.binary_location = os.environ['CHROME_BIN']
    if os.environ.get('CHROME_NO_SANDBOX') == '1':
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
    driver_path = os.environ.get('CHROMEDRIVER_PATH')
    service = Service(executable_path=driver_path) if driver_path else Service()
    
    driver = webdriver.Chrome(options=options, service=service)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver

def login_socrates(driver, config):
    """Login to Socrates Platform using browser and return session with cookies"""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    
    log.debug("Logging in to Socrates Platform...")
    driver.get(config['credentials']['url'])
    
    # Click login link
    login_link = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.LINK_TEXT, "Login"))
    )
    login_link.click()
    
    # Fill credentials
    email_field = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder='Enter your email address']"))
    )
    email_field.send_keys(config['credentials']['username'])
    
    password_field = driver.find_element(By.CSS_SELECTOR, "input[placeholder='Enter your password']")
    password_field.send_keys(config['credentials']['password'])
    
    # Submit
    submit_button = driver.find_element(By.XPATH, "//button[text()='Submit']")
    submit_button.click()
    
    # Wait for login to complete
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.LINK_TEXT, "Private Blog"))
    )
    log.debug("Login successful")
    
    # Transfer cookies to requests session
    session = requests.Session()
    for cookie in driver.get_cookies():
        session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain', ''))
    
    return session

# ---------------------------------------------------------------------------
# Listing pages (find post URLs)
# ---------------------------------------------------------------------------

def get_private_blog_url(page_num):
    """Construct URL for private blog page"""
    if page_num == 1:
        return f"{PRIVATE_BLOG_BASE}?class=Blog"
    return f"{PRIVATE_BLOG_BASE}?page={page_num}&pageSize=10"

def extract_private_blog_page(session, page_num):
    """Extract private blog posts from a single page"""
    url = get_private_blog_url(page_num)
    
    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()
    except Exception as e:
        return page_num, [], describe_error(e)
    
    soup = BeautifulSoup(response.content, 'html.parser')
    posts = []
    
    for post_div in soup.find_all('div', class_='post'):
        h2 = post_div.find('h2')
        if not h2:
            continue
        link = h2.find('a', href=True)
        if not link:
            continue
        
        href = link.get('href', '')
        if '/Blog/Article/' not in href:
            continue
        
        title = link.get_text(strip=True)
        if not title:
            continue
        
        if href.startswith('/'):
            href = f"https://standard.socratesplatform.com{href}"
        
        date_str = None
        meta_div = post_div.find('div', class_='meta')
        if meta_div:
            date_span = meta_div.find('span', class_='span-01')
            if date_span:
                raw_date = date_span.get_text(strip=True)
                if ',' in raw_date:
                    parts = raw_date.split(', ', 1)
                    if len(parts) == 2:
                        date_str = parts[1]
        
        if date_str:
            posts.append({'title': title, 'url': href, 'date': date_str, 'private': True})
    
    return page_num, posts, None

def extract_private_blog_posts(session, pages):
    """Extract private blog posts from Socrates Platform using session"""
    all_posts = []
    
    if len(pages) > 1:
        log.debug(f"Extracting private blog from {len(pages)} pages in parallel...")
        max_workers = min(5, len(pages))
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(extract_private_blog_page, session, p): p for p in pages}
            
            for future in as_completed(futures):
                page_num, posts, error = future.result()
                if error:
                    log.error(f"Private blog page {page_num}: {error}")
                else:
                    all_posts.extend(posts)
                    log.debug(f"Private blog page {page_num}: found {len(posts)} posts")
    else:
        page_num = pages[0]
        log.debug(f"Fetching private blog page {page_num}...")
        _, posts, error = extract_private_blog_page(session, page_num)
        if error:
            log.error(f"Private blog page {page_num}: {error}")
        else:
            all_posts.extend(posts)
            log.debug(f"Private blog page {page_num}: found {len(posts)} posts")
    
    return all_posts

def parse_pages_param(pages_str):
    """Parse pages parameter: single int, comma-separated, or range"""
    if not pages_str:
        return [1]
    
    pages = []
    for part in pages_str.split(','):
        if '-' in part:
            start, end = map(int, part.split('-'))
            pages.extend(range(start, end + 1))
        else:
            pages.append(int(part))
    return sorted(set(pages))

def get_page_url(page_num):
    """Construct URL for given page number"""
    if page_num == 1:
        return BASE_URL
    return f"{BASE_URL}page/{page_num}/"

def extract_blog_posts(page_num):
    """Extract blog posts from a page"""
    url = get_page_url(page_num)
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except Exception as e:
        log.error(f"Public blog page {page_num}: {describe_error(e)}")
        return []
    
    soup = BeautifulSoup(response.content, 'html.parser')
    posts = []
    
    # Find all article containers with blog post info
    # Look for links followed by date text
    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        
        # Filter for actual blog post URLs
        if not href.startswith('https://www.armstrongeconomics.com/'):
            continue
        if any(x in href for x in ['/category/', '/tag/', '/page/', '/cart/', '/login/', 
                                     '/about', '/contact', '/shop/', '/events/', '/models/',
                                     '/conferences/', '/library-research/', '/privacy-policy/',
                                     '/archives', '/sitemap', '/terms-and-conditions']):
            continue
        
        title = link.get_text(strip=True)
        if not title or len(title) < 5:
            continue
            
        # Look for date in nearby text
        parent = link.parent
        if parent:
            # Find next elements that might contain date
            for sibling in parent.find_next_siblings(limit=5):
                text = sibling.get_text(strip=True)
                if any(month in text.upper() for month in 
                       ['JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE',
                        'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER']):
                    # Convert to proper format
                    try:
                        parts = text.split()
                        if len(parts) >= 3:
                            date_str = f"{parts[0]} {parts[1].rstrip(',')}, {parts[2]}"
                            posts.append({'title': title, 'url': href, 'date': date_str})
                            break
                    except:
                        pass
    
    # Remove duplicates (same URL)
    seen = set()
    unique_posts = []
    for post in posts:
        if post['url'] not in seen:
            seen.add(post['url'])
            unique_posts.append(post)
    
    return unique_posts

def filter_posts(posts, archive_path):
    """Apply filtering rules for public posts"""
    valid = []
    excluded_title = []
    already_downloaded = []
    
    for post in posts:
        title = post['title']
        
        # Check title filters (only for public posts)
        if 'Interview:' in title or 'private blog' in title.lower() or 'Market Talk' in title:
            excluded_title.append(post)
            continue
        
        # Check if file exists
        if file_exists(title, post['date'], archive_path):
            already_downloaded.append(post)
            continue
        
        valid.append(post)
    
    return valid, excluded_title, already_downloaded

def filter_private_posts(posts, archive_path):
    """Filter private posts - no title filters, only check for existing files"""
    valid = []
    already_downloaded = []
    
    for post in posts:
        # Check if file exists (with (PB) prefix)
        if file_exists(post['title'], post['date'], archive_path, private=True):
            already_downloaded.append(post)
            continue
        
        valid.append(post)
    
    return valid, already_downloaded

# ---------------------------------------------------------------------------
# Post pages (download content and convert to markdown)
# ---------------------------------------------------------------------------

INLINE_MARKS = {'strong': '**', 'b': '**', 'em': '*', 'i': '*'}
SKIPPED_INLINE_TAGS = {'script', 'style', 'img', 'noscript'}

def escape_markdown(text):
    """Keep literal asterisks (e.g. 'F**k') from being read as bold or italic."""
    return text.replace('\\', '\\\\').replace('*', '\\*')

def _wrap(mark_open, mark_close, inner):
    """Wrap text in marks, keeping surrounding spaces outside them
    ("has **NEVER** been", not "has** NEVER **been") and never letting a mark span a line."""
    core = inner.strip()
    if not core:
        return inner
    lead = inner[:len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()):]
    lines = [line.strip() for line in core.split('\n')]
    core = '\n'.join(f"{mark_open}{line}{mark_close}" if line else '' for line in lines)
    return f"{lead}{core}{trail}"

def _inline(node, active):
    out = []
    for child in node.children:
        if isinstance(child, NavigableString):
            if isinstance(child, PreformattedString):  # comments, CDATA, doctype
                continue
            out.append(escape_markdown(re.sub(r'\s+', ' ', str(child))))
        elif child.name == 'br':
            out.append('\n')
        elif child.name in SKIPPED_INLINE_TAGS:
            continue
        else:
            mark = INLINE_MARKS.get(child.name)
            if child.name == 'u' and 'u' not in active:
                out.append(_wrap('<u>', '</u>', _inline(child, active | {'u'})))
            elif mark and mark not in active:
                out.append(_wrap(mark, mark, _inline(child, active | {mark})))
            else:
                out.append(_inline(child, active))
    return ''.join(out)

def inline_markdown(elem):
    """Text of a paragraph or heading with bold, italic and underline kept as markdown.

    get_text(strip=True) drops this formatting and also the spaces around inline
    tags: "has <strong>NEVER</strong> been" became "hasNEVERbeen"."""
    text = _inline(elem, frozenset())
    text = re.sub(r'(?<!\\)\*\*\*\*', '', text)  # adjacent bold runs: **A****B** -> **AB**
    lines = (re.sub(r' {2,}', ' ', line).strip() for line in text.split('\n'))
    return '\n'.join(line for line in lines if line)

def extract_blog_content(url, session=None):
    """Extract blog post content from Armstrong Economics or Socrates Platform URL"""
    is_private = 'socratesplatform.com' in url
    
    if is_private and not session:
        raise ValueError("Session required for private blog URLs")
    
    response = session.get(url) if session else requests.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')
    
    if is_private:
        return extract_private_blog_content(soup, url)
    return extract_public_blog_content(soup, url)

def extract_private_blog_content(soup, url):
    """Extract content from Socrates Platform private blog"""
    # Find the post container
    post_div = soup.find('div', class_='post')
    
    # Extract title from h2 inside post div
    title = 'Untitled'
    if post_div:
        h2 = post_div.find('h2')
        if h2:
            title = h2.get_text(strip=True)
    
    # Extract date from <div class="meta"><span class="span-01">Monday, December 29, 2025</span>
    posted_date = ''
    if post_div:
        meta_div = post_div.find('div', class_='meta')
        if meta_div:
            date_span = meta_div.find('span', class_='span-01')
            if date_span:
                raw_date = date_span.get_text(strip=True)
                # Remove day name: "Monday, December 29, 2025" -> "December 29, 2025"
                if ',' in raw_date:
                    parts = raw_date.split(', ', 1)
                    if len(parts) == 2:
                        try:
                            posted_date = datetime.strptime(parts[1], '%B %d, %Y').strftime('%Y-%m-%d')
                        except:
                            pass
    
    if not posted_date:
        posted_date = datetime.now().strftime('%Y-%m-%d')
    
    # Extract content from <div id="blogtext"> or <div class="body">
    content_parts = []
    content_div = soup.find(id='blogtext') or (post_div.find('div', class_='body') if post_div else None)
    
    if content_div:
        for elem in content_div.find_all(['p', 'h2', 'h3', 'h4', 'img']):
            if elem.name == 'p':
                # Check for images inside paragraph
                img = elem.find('img')
                if img:
                    img_url = img.get('src', '')
                    img_alt = img.get('alt', '')
                    if img_url:
                        if img_url.startswith('/'):
                            img_url = f"https://standard.socratesplatform.com{img_url}"
                        content_parts.append(f"![{img_alt}]({img_url})")
                else:
                    text = elem.get_text(strip=True)
                    if text and text != '\xa0':
                        content_parts.append(inline_markdown(elem))
            elif elem.name == 'img':
                img_url = elem.get('src', '')
                img_alt = elem.get('alt', '')
                if img_url:
                    if img_url.startswith('/'):
                        img_url = f"https://standard.socratesplatform.com{img_url}"
                    content_parts.append(f"![{img_alt}]({img_url})")
            elif elem.name in ['h2', 'h3', 'h4']:
                heading_text = inline_markdown(elem).replace('\n', ' ')
                if heading_text:
                    level = '#' * int(elem.name[1])
                    content_parts.append(f"\n{level} {heading_text}\n")
    
    return {
        'title': title,
        'posted': posted_date,
        'author': 'Martin Armstrong',
        'reading_time': '',
        'categories': ['Private Blog'],
        'tags': [],
        'content': content_parts,
        'url': url,
        'private': True
    }

def extract_public_blog_content(soup, url):
    """Extract content from Armstrong Economics public blog"""
    
    # Extract metadata
    title = soup.find('h1').get_text(strip=True)
    
    # Extract date and author from h6
    meta_heading = soup.find('h6')
    meta_text = meta_heading.get_text(strip=True)
    
    date_match = re.search(r'Posted\s+(\w+\s+\d+,\s+\d{4})', meta_text)
    author_match = re.search(r'[Bb]y\s+([^|]+)', meta_text)
    
    posted_date = datetime.strptime(date_match.group(1), '%b %d, %Y').strftime('%Y-%m-%d') if date_match else ''
    author = author_match.group(1).strip() if author_match else 'Martin Armstrong'
    
    # Extract reading time from span attribute
    reading_time = ''
    rt_span = meta_heading.find('span', class_='bsf-rt-display-time')
    if rt_span and rt_span.get('reading_time'):
        reading_time = rt_span['reading_time']
    
    # Extract categories and tags
    categories = []
    tags = []
    
    categories_text = soup.find(string='Categories: ')
    if categories_text:
        for sibling in categories_text.parent.next_siblings:
            if hasattr(sibling, 'name') and sibling.name == 'span' and 'cat_tags_item' in sibling.get('class', []):
                link = sibling.find('a', href=lambda x: x and '/category/' in x)
                if link:
                    categories.append(link.get_text(strip=True))
            elif hasattr(sibling, 'name') and sibling.get_text(strip=True) == 'Tags:':
                break
    
    tags_text = soup.find(string='Tags: ')
    if tags_text:
        for sibling in tags_text.parent.next_siblings:
            if hasattr(sibling, 'name') and sibling.name == 'span' and 'cat_tags_item' in sibling.get('class', []):
                link = sibling.find('a', href=lambda x: x and '/tag/' in x)
                if link:
                    tags.append(link.get_text(strip=True))
    
    # Extract main content
    content_parts = []
    
    # Find all content between h1 and "Categories:"
    h1 = soup.find('h1')
    if h1:
        for elem in h1.find_all_next():
            if elem.name and elem.get_text(strip=True) == 'Categories:':
                break
            
            if elem.name == 'h6':
                continue  # Skip meta heading (date/author already extracted)
            if elem.name == 'p':
                text = elem.get_text(strip=True)
                if text and text not in ['SPREAD THE LOVE']:
                    content_parts.append(inline_markdown(elem))
            elif elem.name == 'a' and elem.find('img'):
                img = elem.find('img')
                img_url = img.get('src', '')
                img_alt = img.get('alt', '')
                if img_url and not img_url.endswith('envelop.webp'):
                    content_parts.append(f"![{img_alt}]({img_url})")
            elif elem.name in ['h2', 'h3', 'h4', 'h5', 'h6']:
                plain_heading = elem.get_text(strip=True)
                heading_text = inline_markdown(elem).replace('\n', ' ')
                if plain_heading and plain_heading not in ['BLOG ALERTS', 'RELATED POSTS', 'BLOG CATEGORIES']:
                    level = '#' * (int(elem.name[1]) - 1) if int(elem.name[1]) > 1 else '##'
                    content_parts.append(f"\n{level} {heading_text}\n")
    
    return {
        'title': title,
        'posted': posted_date,
        'author': author,
        'reading_time': reading_time,
        'categories': categories,
        'tags': tags,
        'content': content_parts,
        'url': url,
        'private': False
    }

def yaml_quote(value):
    """Quote a value for YAML front matter so ':' '#' and leading quotes parse."""
    return json.dumps(str(value), ensure_ascii=False)

def create_markdown(data):
    """Create markdown content with front matter"""
    md = "---\n"
    md += f"Title: {yaml_quote(data['title'])}\n"
    md += f"Posted: {data['posted']}\n"
    md += f"Author: {yaml_quote(data['author'])}\n"
    md += f"Reading Time (min): {data['reading_time']}\n"
    md += "Categories:\n"
    for cat in data['categories']:
        md += f"  - {yaml_quote(cat)}\n"
    md += "Tags:\n"
    for tag in data['tags']:
        md += f"  - {yaml_quote(tag)}\n"
    md += f"Source: {data['url']}\n"
    md += "---\n\n"
    md += f"# {data['title']}\n\n"
    
    # Add date/author subtitle
    date_obj = datetime.strptime(data['posted'], '%Y-%m-%d')
    formatted_date = date_obj.strftime('%b %d, %Y')
    md += f"##### Posted {formatted_date} by {data['author']}\n\n"
    
    md += "\n\n".join(data['content'])
    
    return md

def convert_post_to_markdown(post, archive_path, session=None):
    """Download a listed post and save it as markdown.

    The file name is built from the listing title and date (the same values
    file_exists() checks), so a saved post is always found on the next run.
    Returns (filepath, error).
    """
    try:
        data = extract_blog_content(post['url'], session)
        filepath = post_filepath(archive_path, post['title'],
                                 parse_listing_date(post['date']), post.get('private', False))
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(create_markdown(data), encoding='utf-8')
        return str(filepath), None
    except Exception as e:
        return None, describe_error(e)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Extract Armstrong Economics blog URLs and save them locally in markdown format',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python ae-blog.py                              # Public blog page 1
  python ae-blog.py 1-5                          # Public blog pages 1-5
  python ae-blog.py --private                    # Private blog page 1
  python ae-blog.py 1-10 --private               # Private blog pages 1-10
  python ae-blog.py --private --interactive      # Private blog, show browser during login
  python ae-blog.py -a --config config.json      # Both public and private
  python ae-blog.py 1-5 --all --dryrun           # Both, dry run
  python ae-blog.py -a --log-level debug         # Also show progress (pages, login, each saved post)

Exit status: 0 when everything succeeded, 1 when any error was logged.
        ''')
    
    parser.add_argument('pages', nargs='?', default=None, 
                        help='Page numbers: single (1), comma-separated (3,7,11), or range (1-15)')
    
    # Source selection (mutually exclusive)
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument('--public', action='store_true',
                              help='Extract public blog only (default)')
    source_group.add_argument('--private', action='store_true',
                              help='Extract private blog only')
    source_group.add_argument('-a', '--all', action='store_true',
                              help='Extract both public and private blogs')
    
    parser.add_argument('--config', default='config.json',
                        help='Path to config.json for private blog (default: config.json)')
    parser.add_argument('--path', default=os.getcwd(), 
                        help='Archive path (default: current directory)')
    parser.add_argument('--dryrun', action='store_true',
                        help='Extract URLs only without converting to markdown files')
    parser.add_argument('--interactive', action='store_true',
                        help='Show the Chrome window during private blog login '
                             '(default: headless, no window)')
    parser.add_argument('--log-level', default='info', choices=['debug', 'info', 'warning', 'error'],
                        help='Lowest level of log messages to show (default: info; '
                             'debug also shows progress)')
    
    args = parser.parse_args()
    setup_logging(args.log_level)
    
    # Determine source: default to public if none specified
    do_public = args.public or args.all or (not args.private and not args.all)
    do_private = args.private or args.all
    
    pages = parse_pages_param(args.pages)
    archive_path = args.path
    
    source = f"{'public' if do_public else ''}{' + ' if do_public and do_private else ''}{'private' if do_private else ''}"
    log.info(f"Starting {'dry run' if args.dryrun else 'extraction'}: source {source}, "
             f"pages {pages}, archive path {archive_path}")
    
    all_valid = []
    total_found = 0
    total_excluded_title = 0
    total_already_downloaded = 0
    session = None
    
    # Extract public blog posts
    if do_public:
        log.debug("Processing public blog...")
        if len(pages) > 1:
            log.debug(f"Extracting public blog from {len(pages)} pages in parallel...")
            max_workers = min(5, len(pages))
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_page = {executor.submit(extract_blog_posts, page): page for page in pages}
                
                for future in as_completed(future_to_page):
                    page_num = future_to_page[future]
                    try:
                        posts = future.result()
                        
                        if not posts:
                            log.debug(f"Public blog page {page_num}: no posts found")
                            continue
                        
                        valid, excluded_title, already_downloaded = filter_posts(posts, archive_path)
                        
                        total_found += len(posts)
                        total_excluded_title += len(excluded_title)
                        total_already_downloaded += len(already_downloaded)
                        all_valid.extend(valid)
                        
                        log.debug(f"Public blog page {page_num}: found {len(posts)}, valid {len(valid)}, "
                                  f"excluded {len(excluded_title)}, already downloaded {len(already_downloaded)}")
                    except Exception as e:
                        log.error(f"Public blog page {page_num}: {describe_error(e)}")
        else:
            page_num = pages[0]
            log.debug(f"Fetching public blog page {page_num}...")
            posts = extract_blog_posts(page_num)
            
            if posts:
                valid, excluded_title, already_downloaded = filter_posts(posts, archive_path)
                
                total_found += len(posts)
                total_excluded_title += len(excluded_title)
                total_already_downloaded += len(already_downloaded)
                all_valid.extend(valid)
                
                log.debug(f"Public blog page {page_num}: found {len(posts)}, valid {len(valid)}, "
                          f"excluded {len(excluded_title)}, already downloaded {len(already_downloaded)}")
            else:
                log.debug(f"Public blog page {page_num}: no posts found")
    
    # Extract private blog posts
    if do_private:
        log.debug("Processing private blog...")
        config_path = Path(args.config)
        config = load_config(config_path)
        driver = None
        
        try:
            driver = setup_driver(interactive=args.interactive)
            session = login_socrates(driver, config)
            driver.quit()
            driver = None
            
            private_posts = extract_private_blog_posts(session, pages)
            
            if private_posts:
                valid, already_downloaded = filter_private_posts(private_posts, archive_path)
                
                total_found += len(private_posts)
                total_already_downloaded += len(already_downloaded)
                all_valid.extend(valid)
                
                log.debug(f"Private blog: found {len(private_posts)}, valid {len(valid)}, "
                          f"already downloaded {len(already_downloaded)}")
            else:
                log.debug("Private blog: no posts found")
        except Exception as e:
            log.error(f"Private blog: {describe_error(e)}")
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass  # the browser may already have crashed
    
    # The list of new posts is the result of a dry run, so show it at INFO there
    list_level = logging.INFO if args.dryrun else logging.DEBUG
    for i, post in enumerate(all_valid, 1):
        prefix = f"{PRIVATE_PREFIX} " if post.get('private') else ""
        log.log(list_level, f"New post {i}: {prefix}{post['url']}")
    
    log.info(f"Summary: {total_found} posts found, {len(all_valid)} new, "
             f"{total_excluded_title} excluded by title filter, {total_already_downloaded} already downloaded")
    
    # Convert URLs to markdown unless --dryrun flag is set
    if not args.dryrun and all_valid:
        log.debug(f"Converting {len(all_valid)} blog posts to markdown...")
        
        success_count = 0
        error_count = 0
        
        total = len(all_valid)
        counter = [0]  # Use list for mutable counter in closure
        lock = threading.Lock()
        
        def convert_post(post):
            """Convert a single post, returns (idx, title, filepath, error)"""
            with lock:
                counter[0] += 1
                idx = counter[0]
            post_session = session if post.get('private') else None
            filepath, error = convert_post_to_markdown(post, archive_path, post_session)
            return idx, post['title'], filepath, error
        
        if len(all_valid) > 1:
            max_workers = min(5, len(all_valid))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(convert_post, p) for p in all_valid]
                for future in as_completed(futures):
                    idx, title, filepath, error = future.result()
                    if error:
                        log.error(f"[{idx}/{total}] Failed: {title} - {error}")
                        error_count += 1
                    else:
                        log.debug(f"[{idx}/{total}] Saved: {filepath}")
                        success_count += 1
        else:
            idx, title, filepath, error = convert_post(all_valid[0])
            if error:
                log.error(f"[{idx}/{total}] Failed: {title} - {error}")
                error_count += 1
            else:
                log.debug(f"[{idx}/{total}] Saved: {filepath}")
                success_count += 1
        
        log.info(f"Download complete: {success_count} successful, {error_count} errors")
    
    if error_counter.count:
        log.info(f"Finished with {error_counter.count} error(s)")
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
