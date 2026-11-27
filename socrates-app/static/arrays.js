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
  chartOn: false,    // the second column shows the chart of the first column's ticker instead of an array
  chartView: null,   // { chart, series, remove } while the chart is on screen
  chartTicker: null, // ticker the chart shows or is loading
  chartData: new Map(), // ticker -> candles loaded since the chart was opened; reopening it loads new ones
  chartShown: null,  // ticker whose candles are on the chart
  chartRetry: null,  // timer that loads the chart again once a request limit allows it
  chartMark: false,  // H: a dashed line marks the candle of the array's date
  revShown: null,    // R: reversal rays on the chart; starts from the Settings default on each page load
  chartMax: false,   // the chart is maximized: the array column is hidden and the chart uses the full width

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
      this.chartOn = !!(saved && saved.chart) && this.cols.length === 1;
      this.chartMark = !!(saved && saved.mark);
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

  save() { store.set('arrays', { cols: this.cols, focus: this.focus, chart: this.chartOn, mark: this.chartMark }); },

  /* ------------------------------------------------------------ rendering */

  render() {
    Calendar.close();
    this.closeChart();
    const two = this.cols.length === 2;
    this.columnsEl = h('div', { class: 'columns' + (two || this.chartOn ? '' : ' single') + (this.chartOn ? ' with-chart' : '') });
    this.el.replaceChildren(
      h('div', { class: 'arrays-toolbar' },
        h('button', { class: 'btn', onclick: () => this.toggleCompare() },
          two ? '− Remove second array' : '+ Add second array for comparison'),
        h('button', { class: 'btn', title: 'Daily candles of the ticker shown in the array', onclick: () => this.toggleChart() },
          this.chartOn ? '− Remove chart' : '+ Add chart'),
        h('div', { class: 'spacer' }),
        h('button', {
          class: 'btn small', title: 'Show keyboard shortcuts',
          onclick: () => { this.showHelp = !this.showHelp; store.set('arraysHelp', this.showHelp); this.render(); },
        }, this.showHelp ? 'Hide shortcuts' : 'Keyboard shortcuts'),
        h('button', { class: 'btn small', title: 'Rescan the arrays folder for new files', onclick: () => this.show(true) }, '↻ Refresh'),
        Downloads.button('arrays', () => this.refreshAfterDownload()),
        Downloads.note('arrays')),
      ...(this.showHelp ? [this.helpPanel()] : []),
      this.columnsEl);
    this.colEls = this.cols.map((_, ci) => this.createCol(ci));
    this.columnsEl.append(...this.colEls.map((c) => c.root));
    this.cols.forEach((_, ci) => this.updateCol(ci));
    if (this.chartOn) this.openChart();
  },

  helpPanel() {
    const row = (keys, text) => h('div', null, keys.map((k) => [h('kbd', null, k), ' ']), text);
    return h('div', { class: 'shortcuts' },
      row(['⇧', '←'], 'focus first array'),
      row(['⇧', '→'], 'focus second array'),
      row(['⌥', 'S'], 'find a ticker'),
      row(['D', 'W', 'M'], 'daily, weekly, monthly'),
      row(['←', '→'], 'older / newer array'),
      row(['↑', '↓'], 'previous / next ticker'),
      row(['H'], 'mark the array\'s date on the chart'),
      row(['R'], 'show / hide the reversals on the chart'));
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
    if (this.chartOn) {
      this.loadChart();
      this.markChart();
    }
    this.save();
  },

  /* ------------------------------------------------------------ chart */

  async openChart() {
    const size = h('button', { class: 'icon-toggle', onclick: () => this.toggleChartSize() });
    const label = h('div', { class: 'col-label' }, h('span', { class: 'dot' }), 'Chart', h('span', { class: 'spacer' }), size);
    const title = h('div', { class: 'chart-title' });
    const legend = h('div', { class: 'chart-legend' });
    const box = h('div', { class: 'chart-box' });
    const msg = h('div', { class: 'frame-msg' });
    msg.hidden = true;
    const frame = h('div', { class: 'frame chart-frame' }, box, msg);
    const caption = h('div', { class: 'caption' });
    this.chartEls = { root: h('div', { class: 'col chart-col' }, h('div', { class: 'col-head' }, label, title), legend, frame, caption),
                      title, legend, box, msg, frame, caption, size };
    this.columnsEl.append(this.chartEls.root);
    this.applyChartSize(); // after adding it, so its size can be measured
    this.chartTicker = null;
    this.chartData = new Map();
    this.chartShown = null;
    const els = this.chartEls;
    try {
      const view = await PriceChart.create(box);
      if (this.chartEls !== els) { view.remove(); return; } // closed or re-rendered while loading
      this.chartView = view;
    } catch (e) {
      this.chartMessage(e.message);
      return;
    }
    this.loadChart();
  },

  /** The height an array frame has when one array is shown alone (column at most 1000 px wide,
   *  frame at most 960 px, ratio 960:566): the maximized chart uses it. */
  singleFrameHeight(col) {
    const st = getComputedStyle(col);
    const inner = parseFloat(st.paddingLeft) + parseFloat(st.paddingRight) + parseFloat(st.borderLeftWidth) + parseFloat(st.borderRightWidth);
    const width = Math.min(960, Math.min(1000, this.columnsEl.clientWidth) - inner);
    return Math.round(width * 566 / 960);
  },

  /** Maximize: hide the array and let the chart use the full width, as tall as a single array. */
  toggleChartSize() {
    const els = this.chartEls;
    if (!els) return;
    this.chartMax = !this.chartMax;
    this.applyChartSize();
  },

  applyChartSize() {
    const els = this.chartEls;
    if (!els) return;
    this.columnsEl.classList.toggle('chart-max', this.chartMax);
    els.frame.style.height = this.chartMax ? this.singleFrameHeight(els.root) + 'px' : '';
    fill(els.size, icon(this.chartMax ? 'minimize' : 'maximize', 14));
    els.size.title = this.chartMax ? 'Minimize the chart and show the array again' : 'Maximize the chart to the full width';
  },

  closeChart() {
    clearTimeout(this.chartRetry);
    this.chartRetry = null;
    if (this.chartView) this.chartView.remove();
    this.chartView = null;
    this.chartEls = null;
    this.chartTicker = null;
    this.chartShown = null;
  },

  /** Settings > Charts: { shown, weekly, monthly, weeklyCount, monthlyCount }. */
  revSettings() {
    return { shown: true, weekly: true, monthly: true, weeklyCount: 3, monthlyCount: 3,
             ...((App.settings && App.settings.chartReversals) || {}) };
  },

  /** Draw the reversal rays of the ticker on the chart, or clear them (R, Settings > Charts). */
  drawReversals() {
    const view = this.chartView;
    if (!view) return;
    const res = this.chartShown && this.chartData.get(this.chartShown);
    const st = this.revSettings();
    if (this.revShown === null) this.revShown = st.shown;
    const counts = { W: st.weekly ? st.weeklyCount : 0, M: st.monthly ? st.monthlyCount : 0 };
    const rays = this.revShown && res ? PriceChart.reversalRays(res.reversals, view.series.data(), counts) : [];
    view.rays.set(rays);
    if (this.chartEls) {
      const frames = this.revShown ? Object.keys(counts).filter((tf) => counts[tf]) : [];
      this.chartEls.legend.replaceChildren(PriceChart.legend(frames) || '');
    }
  },

  /** Draw or clear the dashed line at the candle of the first column's array date (H). */
  markChart() {
    const view = this.chartView, col = this.cols[0];
    if (!view || !col || this.chartShown !== col.ticker) return;
    const candles = view.series.data();
    const i = this.chartMark && col.date ? PriceChart.candleIndex(candles, col.date) : -1;
    view.mark.set(i >= 0 ? candles[i].time : null);
    view.pin(i >= 0 ? candles[i].time : null);
    if (i >= 0) PriceChart.reveal(view, i);
  },

  chartMessage(text, settingsLink = false) {
    const els = this.chartEls;
    if (!els) return;
    els.msg.replaceChildren(text, settingsLink ? h('div', null, h('a', { class: 'btn small', href: '#/settings/charts' }, 'Open Settings')) : '');
    els.msg.hidden = !text;
    els.box.style.visibility = text ? 'hidden' : '';
  },

  /** Show the daily candles of the first column's ticker; does nothing when it is already shown. */
  async loadChart() {
    const els = this.chartEls, view = this.chartView;
    const ticker = this.cols[0] && this.cols[0].ticker;
    if (!els || !view || !ticker || ticker === this.chartTicker) return;
    this.chartTicker = ticker;
    clearTimeout(this.chartRetry);
    this.chartRetry = null;
    els.title.replaceChildren(h('strong', null, ticker), h('span', null, ' · Daily · loading…'));
    els.frame.classList.add('loading');
    let res = this.chartData.get(ticker);
    try {
      if (!res) {
        res = await api('/api/chart/' + encodeURIComponent(ticker));
        // An answer limited by Twelve Data's rate is not kept, so the ticker loads again later
        if (!res.retryIn) this.chartData.set(ticker, res);
      }
    } catch (e) {
      if (this.chartEls !== els || this.chartTicker !== ticker) return;
      els.frame.classList.remove('loading');
      els.title.replaceChildren(h('strong', null, ticker), h('span', null, ' · Daily'));
      view.series.setData([]);
      this.chartShown = null;
      view.mark.set(null);
      view.rays.set([]);
      els.caption.replaceChildren();
      this.chartMessage(e.message, !!(e.body && e.body.keySet === false));
      const wait = e.body && e.body.retryIn;
      // A request limit: wait for it, without asking again on every click or key. Other errors: try again on the next one.
      if (wait) this.retryChart(ticker, wait); else this.chartTicker = null;
      return;
    }
    if (this.chartEls !== els || this.chartTicker !== ticker) return; // another ticker was chosen meanwhile
    els.frame.classList.remove('loading');
    if (res.covered === false) {
      // No chart data for this market: an empty chart with a short note, and no further requests
      view.series.setData([]);
      view.mark.set(null);
      view.rays.set([]);
      view.pin(null);
      this.chartShown = null;
      els.title.replaceChildren(h('strong', null, ticker), h('span', null, ' · Daily'));
      els.legend.replaceChildren();
      els.caption.replaceChildren();
      this.chartMessage('This market is not covered by the chart data.');
      return;
    }
    this.chartMessage('');
    view.series.setData(res.candles);
    PriceChart.showLatest(view, res.candles.length);
    if (res.retryIn) this.retryChart(ticker, res.retryIn);
    view.showOhlc(null);
    this.chartShown = ticker;
    this.drawReversals();
    this.markChart();
    els.title.replaceChildren(h('strong', null, ticker), h('span', null, ' · Daily' + (res.symbol !== ticker ? ` · ${res.symbol}` : '')));
    const at = res.fetchedAt ? new Date(res.fetchedAt) : null;
    els.caption.replaceChildren(
      h('span', { title: 'Data from Twelve Data. Close and reopen the chart to load newer candles.' },
        res.note || (at ? `Twelve Data, ${pad2(at.getHours())}:${pad2(at.getMinutes())}` : '')));
  },

  /** After a request limit: load the chart again by itself once Twelve Data allows it, if the
   *  same ticker is still shown. A click on the ticker in the meantime also tries again. */
  retryChart(ticker, seconds) {
    if (!seconds) return;
    clearTimeout(this.chartRetry);
    this.chartRetry = setTimeout(() => {
      this.chartRetry = null;
      if (this.chartEls && this.chartTicker === ticker) {
        this.chartTicker = null;
        this.loadChart();
      }
    }, seconds * 1000);
  },

  toggleChart() {
    this.chartMax = false; // Remove chart and + Add chart both start at the normal size
    if (this.chartOn) {
      this.chartOn = false;
    } else {
      this.chartOn = true;
      this.cols.splice(1); // the chart takes the second column's place
      this.focus = 0;
    }
    this.save();
    this.render();
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
    this.chartOn = false; // the second array replaces the chart
    this.chartMax = false;
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
        if (e.key.toLowerCase() === 'r' && this.chartOn) {
          e.preventDefault();
          if (this.revShown === null) this.revShown = this.revSettings().shown;
          this.revShown = !this.revShown;
          this.drawReversals();
          return;
        }
        if (e.key.toLowerCase() === 'h' && this.chartOn) {
          e.preventDefault();
          this.chartMark = !this.chartMark;
          this.markChart();
          this.save();
          return;
        }
        const f = { d: 'D', w: 'W', m: 'M' }[e.key.toLowerCase()];
        if (f && this.dates(this.cols[ci], f).length) {
          e.preventDefault();
          this.setFrame(ci, f);
        }
      }
    }
  },
};
