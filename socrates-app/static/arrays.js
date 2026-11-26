'use strict';

const FRAMES = ['D', 'W', 'M'];
const FRAME_NAME = { D: 'Daily', W: 'Weekly', M: 'Monthly' };
const FRAME_RANK = { D: 0, W: 1, M: 2 };

/** Key of the period (day, ISO week or month) that a "YYYYMMDD" date falls in. */
function periodKey(date, frame) {
  if (frame === 'D') return date;
  if (frame === 'M') return date.slice(0, 6);
  const n = dayNum(date);
  const weekday = (new Date(n * 864e5).getUTCDay() + 6) % 7; // Monday = 0
  return 'W' + (n - weekday);
}

/**
 * The one rule for which array to show for a date, used everywhere the date has to carry over
 * (ticker click, ticker search, ↑/↓, time frame switch, a new second column, column sync).
 * `list` holds ascending "YYYYMMDD" dates. Prefers the array in the same day, week or month as
 * `date` (period of `periodFrame`), closest to `date`; otherwise the nearest array overall, or
 * null when `exact` is set. Without a date, the latest array.
 */
function arrayFor(list, date, periodFrame, exact = false) {
  if (!list.length) return null;
  if (!date) return exact ? null : list[list.length - 1];
  const key = periodKey(date, periodFrame);
  const target = dayNum(date);
  let best = null, bestDiff = Infinity;
  for (const d of list) {
    if (periodKey(d, periodFrame) !== key) continue;
    const diff = Math.abs(dayNum(d) - target);
    if (diff < bestDiff) { best = d; bestDiff = diff; }
  }
  if (best || exact) return best;
  return list[nearestIndex(list, date)];
}

const Arrays = {
  el: $('#page-arrays'),
  data: null,        // { TICKER: { D: [asc dates], W: [...], M: [...] } }
  tickers: [],
  cols: [],          // [{ ticker, frame, date, anchor }]: anchor is the date the user chose
  colEls: [],
  focus: 0,
  showHelp: store.get('arraysHelp', false),

  async show(force = false) {
    if (!App.settings.arraysReachable) {
      this.el.replaceChildren(notice('Arrays folder not set',
        'Choose the root folder of your timing arrays on the Settings page.'));
      this.data = null;
      return;
    }
    if (!this.data) this.el.replaceChildren(h('div', { class: 'empty' }, 'Loading arrays…'));
    try {
      const res = await api('/api/arrays' + (force ? '?refresh=1' : ''));
      this.data = res.tickers;
    } catch (e) {
      this.data = null;
      this.el.replaceChildren(e.status === 409
        ? notice('Arrays folder not reachable', 'The saved arrays folder cannot be read. Check it on the Settings page.')
        : notice('Could not load arrays', e.message, false));
      return;
    }
    this.tickers = Object.keys(this.data).sort((a, b) => a.localeCompare(b));
    if (!this.tickers.length) {
      this.el.replaceChildren(notice('No arrays found', 'The arrays folder has no ticker folders with arrays.'));
      return;
    }
    if (!this.cols.length) {
      const saved = store.get('arrays', null);
      this.cols = saved && Array.isArray(saved.cols) && saved.cols.length ? saved.cols.slice(0, 2) : [{}];
      this.focus = saved && saved.focus === 1 ? 1 : 0;
    }
    this.cols = this.cols.map((c) => this.normalize(c));
    if (this.focus >= this.cols.length) this.focus = 0;
    this.render();
  },

  /** After a download: rescan, and move a column that was showing the latest array to the new latest. */
  async refreshAfterDownload() {
    const wasLatest = this.data ? this.cols.map((c) => { const l = this.dates(c); return c.date === l[l.length - 1]; }) : [];
    await this.show(true);
    if (!this.data) return;
    this.cols.forEach((c, i) => {
      const l = this.dates(c);
      if (wasLatest[i] && l.length) c.date = c.anchor = l[l.length - 1];
    });
    this.changed();
  },

  dates(col, frame = col.frame) {
    const t = this.data && this.data[col.ticker];
    return (t && t[frame]) || [];
  },

  /** Make a column state valid for the current data, keeping as much of it as possible. */
  normalize(c) {
    const col = { ticker: c.ticker, frame: c.frame, date: c.date, anchor: c.anchor || c.date };
    if (!this.data[col.ticker]) col.ticker = this.tickers[0];
    if (!FRAMES.includes(col.frame)) col.frame = 'D';
    if (!this.dates(col).length) col.frame = FRAMES.find((f) => this.dates(col, f).length) || 'D';
    col.date = arrayFor(this.dates(col), col.anchor, col.frame);
    return col;
  },

  save() { store.set('arrays', { cols: this.cols, focus: this.focus }); },

  /* ------------------------------------------------------------ rendering */

  render() {
    Calendar.close();
    const two = this.cols.length === 2;
    this.columnsEl = h('div', { class: 'columns' + (two ? '' : ' single') });
    this.el.replaceChildren(
      h('div', { class: 'arrays-toolbar' },
        h('button', { class: 'btn', onclick: () => this.toggleCompare() },
          two ? '− Remove second array' : '+ Add second array for comparison'),
        h('div', { class: 'spacer' }),
        h('button', {
          class: 'btn small', title: 'Show keyboard shortcuts',
          onclick: () => { this.showHelp = !this.showHelp; store.set('arraysHelp', this.showHelp); this.render(); },
        }, this.showHelp ? 'Hide shortcuts' : 'Keyboard shortcuts'),
        h('button', { class: 'btn small', title: 'Rescan the arrays folder for new files', onclick: () => this.show(true) }, '↻ Refresh'),
        Downloads.button('arrays', () => this.refreshAfterDownload())),
      ...(this.showHelp ? [this.helpPanel()] : []),
      this.columnsEl);
    this.colEls = this.cols.map((_, ci) => this.createCol(ci));
    this.columnsEl.append(...this.colEls.map((c) => c.root));
    this.cols.forEach((_, ci) => this.updateCol(ci));
  },

  helpPanel() {
    const row = (keys, text) => h('div', null, keys.map((k) => [h('kbd', null, k), ' ']), text);
    return h('div', { class: 'shortcuts' },
      row(['⇧', '←'], 'focus first array'),
      row(['⇧', '→'], 'focus second array'),
      row(['⌥', 'S'], 'find a ticker'),
      row(['D', 'W', 'M'], 'daily, weekly, monthly'),
      row(['←', '→'], 'older / newer array'),
      row(['↑', '↓'], 'previous / next ticker'));
  },

  createCol(ci) {
    const head = h('div', { class: 'col-head' });
    const img = h('img', { alt: '', title: 'Open full size in a new tab' });
    const msg = h('div', { class: 'frame-msg' });
    msg.hidden = true;
    const frame = h('div', { class: 'frame' }, img, msg);
    const caption = h('div', { class: 'caption' });
    // mousedown only moves the focus highlight; it must not re-render, or the click would be lost.
    const root = h('div', { class: 'col', onmousedown: () => this.setFocus(ci) }, head, frame, caption);
    img.addEventListener('load', () => { frame.classList.remove('loading'); img.hidden = false; msg.hidden = true; });
    img.addEventListener('error', () => {
      if (!img.getAttribute('src')) return;
      frame.classList.remove('loading');
      img.hidden = true;
      msg.hidden = false;
      msg.textContent = 'The array image could not be loaded.';
    });
    img.addEventListener('click', () => { if (img.getAttribute('src')) window.open(img.src, '_blank', 'noopener'); });
    return { root, head, frame, img, msg, caption };
  },

  updateCol(ci) {
    const col = this.cols[ci], els = this.colEls[ci];
    if (!col || !els) return;
    const two = this.cols.length === 2;
    els.root.classList.toggle('focused', ci === this.focus);
    const list = this.dates(col);
    const idx = col.date ? list.indexOf(col.date) : -1;
    const latest = list[list.length - 1];

    els.head.replaceChildren(
      h('div', { class: 'col-label' }, h('span', { class: 'dot' }), two ? `Array ${ci + 1}` : 'Array'),
      h('div', { class: 'chips' }, this.tickers.map((t) => h('button', {
        class: 'chip' + (t === col.ticker ? ' active' : ''),
        onclick: () => this.setTicker(ci, t),
      }, t))),
      h('div', { class: 'col-row' },
        h('div', { class: 'seg' }, FRAMES.map((f) => h('button', {
          class: f === col.frame ? 'active' : '',
          disabled: !this.dates(col, f).length,
          title: `${FRAME_NAME[f]} (${f.toLowerCase()})`,
          onclick: () => this.setFrame(ci, f),
        }, FRAME_NAME[f]))),
        h('div', { class: 'date-nav' },
          h('button', { class: 'btn small', title: 'Older array (←)', disabled: idx <= 0, onclick: () => this.step(ci, -1) }, icon('left', 14)),
          h('button', {
            class: 'btn date-btn', title: 'Jump to a date', disabled: !list.length,
            onclick: (e) => this.openCalendar(ci, e.currentTarget),
          }, icon('calendar', 15), col.date ? fmtLong(col.date) : 'No arrays'),
          h('button', { class: 'btn small', title: 'Newer array (→)', disabled: idx < 0 || idx >= list.length - 1, onclick: () => this.step(ci, 1) }, icon('right', 14)),
          col.date && col.date !== latest
            ? h('button', { class: 'btn small', title: 'Jump to the latest array', onclick: () => this.setDate(ci, latest, true) }, 'Latest')
            : null)));

    if (col.date) {
      const src = `/api/arrays/image/${encodeURIComponent(col.ticker)}/${col.frame}/${col.date}`;
      if (els.img.getAttribute('src') !== src) {
        els.frame.classList.add('loading');
        els.img.src = src;
      }
      els.caption.replaceChildren(
        h('code', null, `${col.date} ${col.frame} ${col.ticker}`),
        h('span', null, `${idx + 1} of ${list.length} ${FRAME_NAME[col.frame].toLowerCase()} arrays`));
      for (const n of [idx - 1, idx + 1]) {
        if (n >= 0 && n < list.length) new Image().src = `/api/arrays/image/${encodeURIComponent(col.ticker)}/${col.frame}/${list[n]}`;
      }
    } else {
      els.img.removeAttribute('src');
      els.img.hidden = true;
      els.frame.classList.remove('loading');
      els.msg.hidden = false;
      els.msg.textContent = `No ${FRAME_NAME[col.frame].toLowerCase()} arrays for ${col.ticker}.`;
      els.caption.replaceChildren();
    }
  },

  changed() {
    Calendar.close();
    this.cols.forEach((_, ci) => this.updateCol(ci));
    this.save();
  },

  /* ------------------------------------------------------------ actions */

  setFocus(ci) {
    if (ci === this.focus || ci >= this.cols.length) return;
    this.focus = ci;
    this.colEls.forEach((els, i) => els.root.classList.toggle('focused', i === ci));
    this.save();
  },

  /**
   * Switch a column to another ticker and/or time frame, keeping the date: the new array is the
   * one for the date the user chose (see arrayFor), so Daily 13 Nov -> Monthly -> Daily returns to
   * 13 Nov. Mouse clicks, the ticker search and the keyboard all go through here.
   */
  switchArray(ci, change) {
    const col = this.cols[ci];
    const date = col.anchor || col.date;
    Object.assign(col, change);
    if (!this.dates(col).length) col.frame = FRAMES.find((f) => this.dates(col, f).length) || col.frame;
    col.date = arrayFor(this.dates(col), date, col.frame);
    this.setFocus(ci);
    this.changed();
  },

  setTicker(ci, ticker) { this.switchArray(ci, { ticker }); },

  setFrame(ci, frame) { this.switchArray(ci, { frame }); },

  /** Show the previous (dir -1, towards A) or next (dir +1, towards Z) ticker, wrapping at the ends. */
  stepTicker(ci, dir) {
    const n = this.tickers.length;
    if (n < 2) return;
    this.setTicker(ci, this.tickers[(this.tickers.indexOf(this.cols[ci].ticker) + dir + n) % n]);
  },

  step(ci, dir) {
    const col = this.cols[ci];
    const list = this.dates(col);
    const idx = list.indexOf(col.date);
    const next = idx + dir;
    if (idx < 0 || next < 0 || next >= list.length) return;
    this.setDate(ci, list[next], true);
  },

  /** Set a column's date; with sync, move the other column to the matching period when it has one. */
  setDate(ci, date, sync) {
    this.cols[ci].date = date;
    this.cols[ci].anchor = date;
    this.setFocus(ci);
    if (sync && this.cols.length === 2) this.syncOther(ci);
    this.changed();
  },

  /**
   * The other column follows the focused column's date within the coarser of the two time frames:
   * D+D same day, D+W same week, D+M or W+M same month. If the other column has no array in that
   * period it stays where it is.
   */
  syncOther(ci) {
    const src = this.cols[ci], other = this.cols[1 - ci];
    if (!src.date || !other.date) return;
    const coarse = FRAME_RANK[src.frame] >= FRAME_RANK[other.frame] ? src.frame : other.frame;
    const best = arrayFor(this.dates(other), src.date, coarse, true);
    if (best) Object.assign(other, { date: best, anchor: src.date });
  },

  toggleCompare() {
    if (this.cols.length === 2) {
      this.cols.splice(1);
      this.focus = 0;
    } else {
      // Same ticker and time frame as the first array, at its date (or the nearest available one).
      this.cols.push(this.normalize({ ...this.cols[0] }));
      this.focus = 1;
    }
    this.save();
    this.render();
  },

  openCalendar(ci, anchor) {
    const col = this.cols[ci];
    const list = this.dates(col);
    if (!list.length) return;
    this.setFocus(ci);
    Calendar.open(anchor, {
      dates: list,
      selected: col.date,
      label: `${FRAME_NAME[col.frame].toLowerCase()} arrays`,
      onPick: (d) => this.setDate(ci, d, true),
    });
  },

  openSearch() {
    const ci = this.focus;
    Search.open(this.tickers.map((t) => {
      const counts = FRAMES.filter((f) => this.dates({ ticker: t }, f).length).join(' · ');
      return { value: t, meta: counts };
    }), (t) => this.setTicker(ci, t));
  },

  /* ------------------------------------------------------------ keyboard */

  onKey(e) {
    if (!this.data || !this.cols.length || !this.el.contains(this.columnsEl)) return;
    const ci = this.focus;
    const plain = !e.altKey && !e.metaKey && !e.ctrlKey && !e.shiftKey;

    if (e.altKey && !e.metaKey && !e.ctrlKey && e.code === 'KeyS') { // Option+S (e.key is "ß" on a Mac)
      e.preventDefault();
      Calendar.close();
      this.openSearch();
      return;
    }
    if (e.shiftKey && !e.altKey && !e.metaKey && !e.ctrlKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
      e.preventDefault();
      Calendar.close();
      this.setFocus(e.key === 'ArrowLeft' ? 0 : this.cols.length - 1);
      return;
    }
    if (!plain) return;

    switch (e.key) {
      case 'ArrowRight': e.preventDefault(); this.step(ci, 1); return;
      case 'ArrowLeft': e.preventDefault(); this.step(ci, -1); return;
      case 'ArrowUp': e.preventDefault(); this.stepTicker(ci, -1); return;
      case 'ArrowDown': e.preventDefault(); this.stepTicker(ci, 1); return;
      default: {
        const f = { d: 'D', w: 'W', m: 'M' }[e.key.toLowerCase()];
        if (f && this.dates(this.cols[ci], f).length) {
          e.preventDefault();
          this.setFrame(ci, f);
        }
      }
    }
  },
};
