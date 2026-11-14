'use strict';

const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR'];
const TRIGGER_LABEL = { schedule: 'scheduled', retry: 'retry', manual: 'manual', 'new-ticker': 'new ticker' };

const Logs = {
  el: $('#page-logs'),
  filters: Object.assign({ job: '', level: 'INFO', from: '', to: '', run: '' }, store.get('logFilters', {})),
  jobs: {},
  runs: [],
  timer: null,
  token: 0,

  show(params) {
    if (params && params.get('run')) {
      this.filters.run = params.get('run');
      this.filters.level = 'DEBUG';
      history.replaceState(null, '', '#/logs');
    }
    this.build();
    this.load();
    clearInterval(this.timer);
    this.timer = setInterval(() => this.poll(), 3000);
  },

  hide() {
    clearInterval(this.timer);
    this.timer = null;
  },

  build() {
    this.headEl = h('div', { class: 'log-filters' });
    this.runsEl = h('div', { class: 'log-runs' });
    this.linesEl = h('div', { class: 'log-lines' });
    fill(this.el, this.headEl, h('div', { class: 'log-body' }, this.runsEl, this.linesEl));
  },

  setFilter(patch) {
    Object.assign(this.filters, patch);
    store.set('logFilters', { ...this.filters, run: '' });
    this.load();
  },

  query(extra) {
    const f = this.filters, q = new URLSearchParams();
    if (f.job) q.set('job', f.job);
    if (f.from) q.set('from', f.from);
    if (f.to) q.set('to', f.to);
    for (const [k, v] of Object.entries(extra || {})) if (v) q.set(k, v);
    return q.toString();
  },

  async load(quiet) {
    const token = ++this.token;
    if (!quiet) fill(this.linesEl, h('div', { class: 'empty' }, 'Loading…'));
    try {
      const f = this.filters;
      const [runs, lines] = await Promise.all([
        api('/api/logs/runs?' + this.query()),
        api('/api/logs/lines?' + this.query({ run: f.run, level: f.level })),
      ]);
      if (token !== this.token) return;
      this.jobs = runs.jobs;
      this.runs = runs.runs;
      this.renderHead();
      this.renderRuns();
      this.renderLines(lines, quiet);
    } catch (e) {
      if (token === this.token) fill(this.linesEl, h('div', { class: 'empty' }, 'Could not load the logs: ' + e.message));
    }
  },

  poll() {
    if (!this.el.isConnected || this.el.hidden) return;
    const f = this.filters;
    const live = f.run ? this.runs.some((r) => r.id === f.run && r.status === 'running')
      : this.runs.some((r) => r.status === 'running');
    // Also pick up runs that started since the page was loaded.
    if (live || Date.now() - (this.lastFull || 0) > 15000) {
      this.lastFull = Date.now();
      this.load(true);
    }
  },

  renderHead() {
    const f = this.filters;
    const date = (key) => {
      const input = h('input', { type: 'date', value: f[key] });
      input.addEventListener('change', () => this.setFilter({ [key]: input.value }));
      return input;
    };
    const job = h('select', null,
      h('option', { value: '' }, 'All jobs'),
      Object.entries(this.jobs).map(([k, label]) => h('option', { value: k }, label)));
    job.value = f.job;
    job.addEventListener('change', () => this.setFilter({ job: job.value, run: '' }));
    const filtered = f.job || f.from || f.to || f.level !== 'INFO' || f.run;
    fill(this.headEl, 
      h('div', { class: 'filter-row' }, h('div', { class: 'label' }, 'Level'),
        h('div', { class: 'seg' }, LOG_LEVELS.map((l) => h('button', {
          class: f.level === l ? 'active' : '',
          title: l === 'DEBUG' ? 'Everything' : `${l} and more severe`,
          onclick: () => this.setFilter({ level: l }),
        }, l[0] + l.slice(1).toLowerCase()))),
        h('span', { class: 'hint' }, f.level === 'DEBUG' ? 'all lines' : `${f.level.toLowerCase()} and above`)),
      h('div', { class: 'filter-row' }, h('div', { class: 'label' }, 'Filter'),
        h('div', { class: 'inline' }, job, h('span', { class: 'hint' }, 'from'), date('from'),
          h('span', { class: 'hint' }, 'to'), date('to'),
          h('button', { class: 'btn small', disabled: !filtered, onclick: () => this.setFilter({ job: '', from: '', to: '', level: 'INFO', run: '' }) }, '✕ Clear'),
          h('button', { class: 'btn small', onclick: () => this.load() }, '↻ Refresh'))));
  },

  renderRuns() {
    const f = this.filters;
    const item = (r) => h('button', {
      class: 'run-item' + (f.run === r.id ? ' active' : ''),
      onclick: () => this.setFilter({ run: f.run === r.id ? '' : r.id }),
      title: r.summary || '',
    },
    h('div', { class: 'top' }, h('span', { class: 'job' }, this.jobs[r.job] || r.job), statusBadge(r.status)),
    h('div', { class: 'meta' }, r.start, ' · ', TRIGGER_LABEL[r.trigger] || r.trigger,
      r.attempt && r.maxAttempts ? ` · attempt ${r.attempt}/${r.maxAttempts}` : ''),
    r.summary ? h('div', { class: 'sum' }, r.summary) : null);
    fill(this.runsEl, 
      h('button', { class: 'run-item all' + (f.run ? '' : ' active'), onclick: () => this.setFilter({ run: '' }) },
        h('div', { class: 'top' }, h('span', { class: 'job' }, 'All runs'), h('span', { class: 'hint' }, `${this.runs.length}`))),
      this.runs.length ? this.runs.map(item) : h('div', { class: 'empty' }, 'No runs yet. Runs appear here once a scheduled or manual download has started.'));
  },

  renderLines(res, quiet) {
    const el = this.linesEl;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    const showJob = !this.filters.run && !this.filters.job;
    const rows = res.lines.map((l) => h('div', { class: 'log-line ' + l.level.toLowerCase() },
      h('span', { class: 't' }, l.t),
      h('span', { class: 'lvl' }, l.level),
      showJob ? h('span', { class: 'jb' }, this.jobs[l.job] || l.job) : null,
      h('span', { class: 'm' }, l.msg)));
    fill(el, 
      res.truncated ? h('div', { class: 'hint trunc' }, `Showing the last ${res.limit.toLocaleString()} lines. Narrow the filters to see older ones.`) : null,
      rows.length ? h('div', { class: 'log-table' + (showJob ? ' with-job' : '') }, rows)
        : h('div', { class: 'empty' }, 'No log lines match these filters.'));
    if (!quiet || atBottom) el.scrollTop = el.scrollHeight;
  },
};
