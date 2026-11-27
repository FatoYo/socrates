# Armstrong Economics Blog Archiver
**Created by** FatoYo November 2025

A Python application to extract and convert Armstrong Economics blog posts to markdown format with Obsidian-compatible front matter. Supports both public blog and private Socrates Platform blog.

## Overview

This application consists of two components:

1. **ae-blog.py** - Finds blog post URLs on listing pages, downloads each new post and saves it as markdown
2. **ae-blog.sh** - Shell wrapper for easy execution with automatic venv management

## Features

- Extract blog post URLs from Armstrong Economics public blog
- Extract blog post URLs from Socrates Platform private blog
- Filter out "Interview:", "Private blog", and "Market Talk" posts (public only)
- Check for already downloaded posts to avoid duplicates
- Convert blog posts to markdown with Obsidian front matter
- Parallel processing for link extraction (up to 5 concurrent pages)
- Dry-run mode to preview URLs without downloading
- Headless private blog login by default, with `--interactive` to show the browser
- Organize files by year in folder structure: `YYYY/YYYY-MM-DD <Title>.md`
- Private blog posts prefixed with `(PB)` in filename
- Obsidian-safe file names, so posts with special characters in the title are never downloaded twice
- Support for single pages, ranges, or comma-separated page numbers

## Installation

### Prerequisites

- Python 3.11 or higher
- pip3
- Chrome browser (for private blog authentication)

### Setup

1. Clone or download the repository:
```bash
cd /path/to/ae-blog
```

2. Make the shell script executable:
```bash
chmod +x ae-blog.sh
```

3. Dependencies are installed automatically on first run:
```bash
./ae-blog.sh --help
```

The shell script will automatically:
- Create a virtual environment if it doesn't exist
- Install required dependencies
- Activate the environment and run the application

### Private Blog Configuration

To access the Socrates Platform private blog, copy `config.example.json` to `config.json` and fill in your credentials. `config.json` is git-ignored so your password is never committed. When you use the Socrates web app (`socrates-app`), its Settings page writes this file for you.

```bash
cp config.example.json config.json
```

```json
{
  "credentials": {
    "url": "https://standard.socratesplatform.com/",
    "username": "your-email@example.com",
    "password": "your-password"
  }
}
```

## Scripts Documentation

### Main Application (ae-blog.sh)

Primary script for extracting blog post URLs and converting them to markdown. This shell wrapper automatically manages the virtual environment.

**Usage:**
```bash
./ae-blog.sh [pages] [--public|--private|-a|--all] [--config CONFIG] [--path PATH] [--dryrun] [--interactive] [--log-level LEVEL]
```

**Parameters:**

| Parameter | Description |
|-----------|-------------|
| `pages` | Page numbers (optional, default: 1). Formats: `1`, `1-15`, `1,3,5,10` |
| `--public` | Extract public blog only (default if no source specified) |
| `--private` | Extract private blog only |
| `-a, --all` | Extract both public and private blogs |
| `--config PATH` | Path to config.json (default: config.json) |
| `--path PATH` | Archive directory (default: current directory) |
| `--dryrun` | Extract URLs only without converting to markdown |
| `--interactive` | Show the Chrome window during private blog login (default: headless, no window) |
| `--log-level LEVEL` | Lowest level of log messages to show: `debug`, `info` (default), `warning` or `error`. See [Logging](#logging) |
| `-h, --help` | Show help message |

**Examples:**
```bash
# Public blog page 1 (default behavior)
./ae-blog.sh

# Public blog pages 1-5
./ae-blog.sh 1-5 --public

# Public blog with custom path
./ae-blog.sh 1-10 --path "/custom/path/Armstrong Economics Blog"

# Private blog only (headless login, no browser window)
./ae-blog.sh --private

# Private blog with visible browser during login
./ae-blog.sh --private --interactive

# Private blog pages 1-10 with custom config
./ae-blog.sh 1-10 --private --config /path/to/config.json

# Both public and private blogs
./ae-blog.sh 1-5 -a --config config.json

# Dry run (preview URLs without downloading)
./ae-blog.sh 1-10 --all --dryrun

# Both blogs, also showing progress (pages, login, each saved post)
./ae-blog.sh -a --log-level debug
```

### Logging

Every output line has a date, time and level:

```
[2026-11-11 08:48:19] INFO    Starting extraction: source public + private, pages [1], archive path /Users/you/Documents/Armstrong Economics Blog
[2026-11-11 08:48:20] DEBUG   Public blog page 1: found 10, valid 2, excluded 2, already downloaded 6
[2026-11-11 08:48:24] DEBUG   Login successful
[2026-11-11 08:48:25] ERROR   Private blog page 1: the website did not respond in time
[2026-11-11 08:48:25] INFO    Summary: 10 posts found, 2 new, 2 excluded by title filter, 6 already downloaded
[2026-11-11 08:48:26] DEBUG   [1/2] Saved: /Users/you/Documents/Armstrong Economics Blog/2026/2026-11-11 October Inflation.md
[2026-11-11 08:48:26] INFO    Download complete: 2 successful, 0 errors
[2026-11-11 08:48:26] INFO    Finished with 1 error(s)
```

| Level | Used for |
|-------|----------|
| `INFO` | Start of the run, summary of posts found, download result |
| `DEBUG` | Progress: pages fetched, login steps, each saved post, the list of new post URLs (shown at `INFO` in a dry run) |
| `ERROR` | A listing page, the private blog login or a post download failed. The message gives a short reason, for example "the website did not respond in time", instead of a Python stack trace |

By default (`--log-level info`) the `INFO`, `WARNING` and `ERROR` lines are shown, so errors always appear. Add `--log-level debug` to also see the `DEBUG` progress lines. The example above is a run with `--log-level debug`.

### Exit Status

| Status | Meaning |
|--------|---------|
| `0` | The run finished without errors |
| `1` | At least one error was logged (a page, the login or a post failed), or the config file is missing |

Posts that were saved before an error are kept. Run the script again later to download the rest: posts that already exist are skipped.

### Core Python Script

**ae-blog.py** - All extraction and markdown conversion logic (called by ae-blog.sh)

It can be run directly but requires manual virtual environment setup:

```bash
# Manual setup (only needed if not using ae-blog.sh)
python3 -m venv venv
source venv/bin/activate
pip3 install -r requirements.txt

# Then run the script directly
python3 ae-blog.py [options]
```

**Recommended:** Use `./ae-blog.sh` instead for automatic environment management.

## Output Format

### File Structure
```
Archive Path/
├── 2025/
│   ├── 2025-12-29 (PB) Nickel - Preparing for War.md
│   ├── 2025-12-29 The Most Frequently Asked Question of 2025.md
│   ├── 2025-11-23 Thank You for Attending the 2025 WEC!.md
│   └── ...
├── 2024/
│   └── ...
```

- Public blog posts: `YYYY-MM-DD Title.md`
- Private blog posts: `YYYY-MM-DD (PB) Title.md`

### Obsidian-Safe File Names

Titles are cleaned by `clean_title_for_obsidian()` in `ae-blog.py` before they become file names. The original title stays unchanged in the front matter and the `#` heading.

The same function builds the file name when a post is saved and when the archive is checked for posts that are already downloaded. The name is always built from the title and date on the listing page, so a post whose title contains forbidden characters is still recognised and not downloaded again.

| Character | Replacement | Example |
|-----------|-------------|---------|
| `/` between words | ` or ` | `Inflation/Deflation` → `Inflation or Deflation` |
| `/` between digits | `-` | `9/11`, `12/27/2013` → `9-11`, `12-27-2013` |
| `w/` | `with ` | `w/ Martin Armstrong` → `with Martin Armstrong` |
| `:` `\` `\|` | ` - ` | `Gold: Outlook` → `Gold - Outlook` |
| `#` before a digit | `No. ` | `#1 Investment` → `No. 1 Investment` |
| `"` | `'` | `"Quote"` → `'Quote'` |
| `[` `]` | `(` `)` | `[Update]` → `(Update)` |
| ` < ` ` > ` | ` less than ` ` greater than ` | `Yield > 10 Year` → `Yield greater than 10 Year` |
| `*` | `_` | `F**k` → `F__k` |
| `?` `<` `>` `#` `^` (other) | removed | `Recession?` → `Recession` |

Whitespace is collapsed, leading/trailing dots and dashes are trimmed, and names are capped at 200 characters. Front matter values are written as quoted YAML strings so titles containing `:` or `#` parse correctly.

### Text Formatting

Bold, italic and underlined text in a post is kept: `<strong>`/`<b>` become `**bold**`, `<em>`/`<i>` become `*italic*`, and `<u>` stays as `<u>underline</u>` (markdown has no underline syntax). Spaces around formatted words are kept, and a line break inside a paragraph becomes a new line. Literal asterisks in the text are escaped (`F\*\*k`) so they are not read as formatting. Link text is kept, the link targets are not.

Posts saved before this change have no formatting, and words next to formatted text may be joined together (for example `hasNEVERbeen`). They are not updated automatically.

### Markdown Front Matter
```yaml
---
Title: "Blog Post Title"
Posted: 2025-11-23
Author: "Martin Armstrong"
Reading Time (min): 5
Categories:
  - "Category 1"
  - "Category 2"
Tags:
  - "Tag 1"
  - "Tag 2"
Source: https://www.armstrongeconomics.com/...
---

# Blog Post Title

##### Posted Nov 23, 2025 by Martin Armstrong

Content here...
```

## Filtering Rules

**Public blog posts** are automatically excluded if titles contain:
- "Interview:"
- "Private blog" (case-insensitive)
- "Market Talk"

**Private blog posts** have no title filters applied.

Both public and private posts that already exist in the archive folder are skipped.

## Performance

- **Link extraction**: Parallel processing when multiple pages requested (up to 5 pages simultaneously)
- **Speed improvement**: Approximately 3-5x faster for bulk operations
- **Dry-run mode**: Fast URL extraction and validation without downloading content

## Troubleshooting

### Virtual Environment Issues
```bash
# Remove and recreate venv
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip3 install -r requirements.txt
```

### Permission Issues
```bash
# Make shell script executable
chmod +x ae-blog.sh
```

### Module Not Found
```bash
# Ensure dependencies are installed
source venv/bin/activate
pip3 install -r requirements.txt
```

### Private Blog Login Issues
- Ensure Chrome browser is installed
- Check credentials in config.json
- The login process uses Selenium with Chrome in headless mode by default, so no browser window appears
- Add `--interactive` to show the browser window and watch the login steps when debugging
- `ae-blog.sh` sets `SE_AVOID_STATS=true` so Selenium Manager does not send usage statistics. Set it yourself if you run `ae-blog.py` directly

## Dependencies

- `requests>=2.34.2` - HTTP library for fetching web pages
- `beautifulsoup4>=4.15.0` - HTML parsing library
- `selenium>=4.52.0` - Browser automation for private blog login

## License

Copyright (C) 2025-2026 FatoYo

Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0), see [LICENSE](../LICENSE). This project is not affiliated with Armstrong Economics or the Socrates Platform. It needs your own subscription, and you are responsible for following those sites' terms of use.

## Notes

- The application respects the website structure and extracts publicly available content
- Files are organized by year for easy navigation
- Obsidian-compatible front matter allows for easy integration with note-taking apps
- Parallel processing significantly speeds up bulk operations
- Private blog requires valid Socrates Platform subscription
