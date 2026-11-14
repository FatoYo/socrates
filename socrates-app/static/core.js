'use strict';

/* ---------------------------------------------------------------- helpers */

const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'html') el.innerHTML = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, v);
    }
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}

/** Replace an element's children; nested arrays are flattened and null/false are skipped. */
function fill(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false)
    .map((k) => (k instanceof Node ? k : String(k))));
  return el;
}

function icon(name, size = 16) {
  const paths = {
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3 10h18M8 3v4M16 3v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    left: '<path d="M15 5l-7 7 7 7" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
    right: '<path d="M9 5l7 7-7 7" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
    up: '<path d="M5 15l7-7 7 7" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
    down: '<path d="M5 9l7 7 7-7" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
  };
  const span = document.createElement('span');
  span.style.display = 'inline-flex';
  span.innerHTML = `<svg viewBox="0 0 24 24" width="${size}" height="${size}">${paths[name]}</svg>`;
  return span;
}

const store = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem('socrates.' + key);
      return v == null ? fallback : JSON.parse(v);
    } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem('socrates.' + key, JSON.stringify(value)); } catch { /* storage full or disabled */ }
  },
};

async function api(path, options = {}) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const err = new Error((body && body.error) || res.statusText || 'Request failed');
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

function postJSON(path, body) {
  return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
}

function isTyping(e) {
  const t = e.target;
  return !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
}

function notice(title, text, withButton = true) {
  return h('div', { class: 'notice' },
    h('h2', null, title),
    h('p', null, text),
    withButton ? h('a', { class: 'btn primary', href: '#/settings' }, 'Open Settings') : null);
}

/* ---------------------------------------------------------------- dates
   Arrays use "YYYYMMDD", blog posts use "YYYY-MM-DD". Both are handled as UTC day numbers. */

const MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTH_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const pad2 = (n) => String(n).padStart(2, '0');

function dayNum(s) {
  const c = s.length === 8 ? s : s.replace(/-/g, '');
  return Date.UTC(+c.slice(0, 4), +c.slice(4, 6) - 1, +c.slice(6, 8)) / 864e5;
}

const fmtLongIntl = new Intl.DateTimeFormat(undefined, { timeZone: 'UTC', weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' });
const fmtShortIntl = new Intl.DateTimeFormat(undefined, { timeZone: 'UTC', year: 'numeric', month: 'short', day: 'numeric' });
const fmtLong = (s) => fmtLongIntl.format(new Date(dayNum(s) * 864e5));
const fmtShort = (s) => fmtShortIntl.format(new Date(dayNum(s) * 864e5));

/** Index of the entry in an ascending list of "YYYYMMDD" strings closest to `date` (ties go to the earlier one). */
function nearestIndex(list, date) {
  if (!list.length) return -1;
  let lo = 0, hi = list.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (list[mid] < date) lo = mid + 1; else hi = mid;
  }
  if (lo === list.length) return lo - 1;
  if (list[lo] === date || lo === 0) return lo;
  return dayNum(date) - dayNum(list[lo - 1]) <= dayNum(list[lo]) - dayNum(date) ? lo - 1 : lo;
}

/* ---------------------------------------------------------------- calendar popover */

const Calendar = {
  el: $('#calendar'),
  anchor: null,
  opts: null,
  available: null,
  view: 0, // year * 12 + month index

  isOpen() { return !this.el.hidden; },

  open(anchor, opts) {
    if (this.isOpen() && this.anchor === anchor) { this.close(); return; }
    this.anchor = anchor;
    this.opts = opts;
    this.available = new Set(opts.dates);
    const d = opts.selected || opts.dates[opts.dates.length - 1];
    this.view = +d.slice(0, 4) * 12 + (+d.slice(4, 6) - 1);
    this.el.hidden = false;
    this.render();
    this.position();
    setTimeout(() => document.addEventListener('mousedown', this.onOutside), 0);
  },

  close() {
    if (this.el.hidden) return;
    this.el.hidden = true;
    this.anchor = null;
    document.removeEventListener('mousedown', this.onOutside);
  },

  onOutside(e) {
    if (!Calendar.el.contains(e.target) && !(Calendar.anchor && Calendar.anchor.contains(e.target))) Calendar.close();
  },

  position() {
    if (!this.anchor || !this.anchor.isConnected) { this.close(); return; }
    const r = this.anchor.getBoundingClientRect();
    const w = this.el.offsetWidth, ht = this.el.offsetHeight;
    const left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), window.innerWidth - w - 8);
    let top = r.bottom + 6;
    if (top + ht > window.innerHeight - 8) top = Math.max(8, r.top - ht - 6);
    this.el.style.left = left + 'px';
    this.el.style.top = top + 'px';
  },

  ymOf(s) { return +s.slice(0, 4) * 12 + (+s.slice(4, 6) - 1); },

  setView(ym) {
    const { dates } = this.opts;
    this.view = Math.min(Math.max(ym, this.ymOf(dates[0])), this.ymOf(dates[dates.length - 1]));
    this.render();
  },

  render() {
    const { dates, selected, label } = this.opts;
    const minYm = this.ymOf(dates[0]), maxYm = this.ymOf(dates[dates.length - 1]);
    const y = Math.floor(this.view / 12), m = this.view % 12;
    const lead = (new Date(Date.UTC(y, m, 1)).getUTCDay() + 6) % 7; // Monday first
    const days = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    const now = new Date();
    const today = `${now.getFullYear()}${pad2(now.getMonth() + 1)}${pad2(now.getDate())}`;
    const cells = [];
    let inMonth = 0;
    for (let i = 0; i < lead; i++) cells.push(h('span'));
    for (let d = 1; d <= days; d++) {
      const ds = `${y}${pad2(m + 1)}${pad2(d)}`;
      const cls = (ds === today ? ' today' : '');
      if (this.available.has(ds)) {
        inMonth++;
        cells.push(h('button', {
          class: (ds === selected ? 'selected' : '') + cls,
          title: fmtLong(ds),
          onclick: () => this.pick(ds),
        }, d));
      } else {
        cells.push(h('span', { class: 'day' + cls, title: 'No array' }, d));
      }
    }
    this.el.replaceChildren(
      h('div', { class: 'cal-head' },
        h('button', { title: 'Previous year', disabled: this.view <= minYm, onclick: () => this.setView(this.view - 12) }, '«'),
        h('button', { title: 'Previous month', disabled: this.view <= minYm, onclick: () => this.setView(this.view - 1) }, '‹'),
        h('div', { class: 'title' }, `${MONTH_LONG[m]} ${y}`),
        h('button', { title: 'Next month', disabled: this.view >= maxYm, onclick: () => this.setView(this.view + 1) }, '›'),
        h('button', { title: 'Next year', disabled: this.view >= maxYm, onclick: () => this.setView(this.view + 12) }, '»')),
      h('div', { class: 'cal-grid' },
        ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'].map((d) => h('div', { class: 'wd' }, d)),
        cells),
      h('div', { class: 'cal-foot' },
        h('span', null, inMonth ? `${inMonth} ${label} this month` : `No ${label} this month`),
        h('button', { class: 'btn small', onclick: () => this.pick(dates[dates.length - 1]) }, 'Latest')));
  },

  pick(ds) {
    const cb = this.opts.onPick;
    this.close();
    cb(ds);
  },
};

window.addEventListener('resize', () => Calendar.close());
document.addEventListener('scroll', () => Calendar.isOpen() && Calendar.position(), true);

/* ---------------------------------------------------------------- fuzzy ticker search */

const Search = {
  el: $('#search'),
  input: $('#searchInput'),
  list: $('#searchResults'),
  items: [],
  index: 0,
  options: [],
  onPick: null,

  isOpen() { return !this.el.hidden; },

  /** options: [{ value, meta }] */
  open(options, onPick) {
    this.options = options;
    this.onPick = onPick;
    this.input.value = '';
    this.el.hidden = false;
    this.update();
    this.input.focus();
  },

  close() {
    this.el.hidden = true;
    this.input.blur();
  },

  /** Returns { score, marks } or null. Exact > prefix > substring > in-order letters. */
  score(text, q) {
    const t = text.toUpperCase();
    if (!q) return { score: 0, marks: [] };
    const range = (a, n) => Array.from({ length: n }, (_, i) => a + i);
    if (t === q) return { score: 1000, marks: range(0, q.length) };
    if (t.startsWith(q)) return { score: 800 - t.length, marks: range(0, q.length) };
    const at = t.indexOf(q);
    if (at >= 0) return { score: 600 - at * 5 - t.length, marks: range(at, q.length) };
    const marks = [];
    let j = 0;
    for (let i = 0; i < t.length && j < q.length; i++) if (t[i] === q[j]) { marks.push(i); j++; }
    if (j < q.length) return null;
    const spread = marks[marks.length - 1] - marks[0];
    return { score: 300 - spread * 4 - marks[0] * 3 - t.length, marks };
  },

  update() {
    const q = this.input.value.trim().toUpperCase();
    this.items = this.options
      .map((o) => ({ ...o, ...(this.score(o.value, q) || { score: null }) }))
      .filter((o) => o.score !== null)
      .sort((a, b) => b.score - a.score || a.value.localeCompare(b.value));
    this.index = 0;
    this.renderList();
  },

  renderList() {
    if (!this.items.length) {
      this.list.replaceChildren(h('li', { class: 'none' }, 'No matching ticker'));
      return;
    }
    this.list.replaceChildren(...this.items.map((o, i) => {
      const marks = new Set(o.marks);
      const label = [...o.value].map((ch, k) => (marks.has(k) ? h('mark', null, ch) : ch));
      return h('li', {
        class: i === this.index ? 'active' : '',
        onmousedown: (e) => { e.preventDefault(); this.pick(i); },
        onmousemove: () => { if (this.index !== i) { this.index = i; this.renderList(); } },
      }, h('span', null, label), h('span', { class: 'meta' }, o.meta || ''));
    }));
    const active = this.list.children[this.index];
    if (active) active.scrollIntoView({ block: 'nearest' });
  },

  pick(i) {
    const item = this.items[i];
    if (!item) return;
    const cb = this.onPick;
    this.close();
    cb(item.value);
  },
};

Search.input.addEventListener('input', () => Search.update());
Search.input.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    if (!Search.items.length) return;
    Search.index = (Search.index + (e.key === 'ArrowDown' ? 1 : -1) + Search.items.length) % Search.items.length;
    Search.renderList();
  } else if (e.key === 'Enter') {
    e.preventDefault();
    Search.pick(Search.index);
  } else if (e.key === 'Escape') {
    e.preventDefault();
    Search.close();
  }
  e.stopPropagation();
});
Search.el.addEventListener('mousedown', (e) => { if (e.target === Search.el) Search.close(); });

/* ---------------------------------------------------------------- "Download now" buttons
   Arrays and Blog pages: run that page's jobs the way the schedule runs them, show progress,
   and refresh the page when they finish (also when a scheduled run was already in progress). */

const Downloads = {
  JOBS: { arrays: ['arrays'], blog: ['blog-public', 'blog-private'] },
  state: {},     // kind -> { busy, cls, msg, link, onDone }
  status: null,  // last /api/jobs response
  timer: null,

  button(kind, onDone) {
    const st = this.state[kind] || (this.state[kind] = { busy: false, cls: '', msg: '' });
    st.onDone = onDone;
    const el = h('span', { class: 'dl', 'data-dl': kind });
    this.paint(kind, el);
    if (!this.timer) this.poll();  // notice runs that are already in progress
    return el;
  },

  title(kind) {
    if (kind === 'blog') return 'Check the public blog (pages 1-2) and the private blog (page 1) for new posts now';
    const frames = this.status ? (this.status.todayFrames.length ? this.status.todayFrames : ['D']) : null;
    const names = { D: 'daily', W: 'weekly', M: 'monthly' };
    return 'Download today\'s timing arrays now, the same as the scheduled run' +
      (frames ? ` (${frames.map((f) => names[f]).join(' + ')})` : '');
  },

  paint(kind, only) {
    const st = this.state[kind];
    const els = only ? [only] : document.querySelectorAll(`[data-dl="${kind}"]`);
    for (const el of els) {
      fill(el,
        h('button', { class: 'btn small', disabled: st.busy, title: this.title(kind), onclick: () => this.start(kind) },
          st.busy ? '⏳ Downloading…' : '⬇ Download now'),
        st.msg ? h('span', { class: 'dl-note ' + st.cls }, st.msg,
          st.link ? [' ', h('a', { href: st.link }, 'Logs')] : null) : null);
    }
  },

  async start(kind) {
    const st = this.state[kind];
    try {
      const r = await postJSON('/api/download/' + kind, {});
      Object.assign(st, { busy: true, cls: '', msg: r.started.length ? '' : 'Already running', link: null });
      this.status = r.status;
    } catch (e) {
      Object.assign(st, { cls: 'err', msg: '✕ ' + e.message, link: null });
    }
    this.paint(kind);
    this.schedulePoll(1000);
  },

  schedulePoll(ms) {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.poll(), ms);
  },

  async poll() {
    this.timer = 'polling';
    try {
      this.status = await api('/api/jobs');
    } catch {
      this.schedulePoll(5000);
      return;
    }
    let anyBusy = false;
    for (const [kind, jobs] of Object.entries(this.JOBS)) {
      const st = this.state[kind];
      if (!st) continue;
      const info = jobs.map((j) => this.status.jobs[j]);
      const busy = info.some((j) => j.running || j.queued);
      if (busy) {
        Object.assign(st, { busy: true, cls: '', msg: info.some((j) => j.running) ? '' : 'Waiting for another job', link: null });
      } else if (st.busy) {
        // Just finished: report the result and refresh the page.
        const runs = info.map((j) => j.lastRun).filter(Boolean);
        const bad = runs.find((r) => r.status !== 'success');
        if (bad) Object.assign(st, { cls: 'err', msg: '✕ ' + (bad.summary || 'Failed'), link: `#/logs?run=${encodeURIComponent(bad.id)}` });
        else {
          // One job: its summary. Several: "Public blog: 10 posts found, 1 new · Private blog: …"
          const text = runs.map((r) => {
            const s = (r.summary || 'done').replace(/^Summary:\s*/, '');
            return runs.length > 1 ? `${this.status.jobs[r.job].label}: ${s}` : s;
          });
          Object.assign(st, { cls: 'ok', msg: '✓ ' + text.join(' · '), link: null });
        }
        st.busy = false;
        if (st.onDone) { try { st.onDone(); } catch { /* the page refreshes on its own later */ } }
      }
      anyBusy = anyBusy || busy;
      this.paint(kind);
    }
    this.timer = null;
    if (anyBusy) this.schedulePoll(3000);
  },
};
