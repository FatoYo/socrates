'use strict';

const FRAME_LABEL = { D: 'Daily', W: 'Weekly', M: 'Monthly' };
const STATUS_LABEL = {
  success: 'Success', outdated: 'Outdated', failed: 'Failed', timeout: 'Timed out', running: 'Running', interrupted: 'Interrupted',
};

/** The Settings sub-pages, in menu order: #/settings/<key> */
const SETTINGS_PAGES = { archive: 'Archive', markets: 'Markets', routines: 'Routines', charts: 'Charts' };

function statusBadge(status) {
  return h('span', { class: 'run-badge ' + status }, STATUS_LABEL[status] || status);
}

const Settings = {
  el: $('#page-settings'),
  timer: null,

  async show(params, sub = 'archive') {
    try { App.settings = await api('/api/settings'); } catch { /* keep what we have */ }
    const s = App.settings;
    this.tickerEdits = null;  // unsaved ticker changes are dropped when the page is opened again
    this.accountEl = h('div', { class: 'card' }, h('div', { class: 'hint' }, 'Loading…'));
    this.tickersEl = h('div', { class: 'card' }, h('div', { class: 'hint' }, 'Loading…'));
    this.scheduleEl = h('div', { class: 'card' }, h('div', { class: 'hint' }, 'Loading…'));
    this.jobsEl = h('div', { class: 'card' });
    clearInterval(this.timer);
    this.timer = null;
    const page = {
      archive: () => this.archivePage(s),
      markets: () => [this.tickersEl],
      routines: () => [this.scheduleEl, this.jobsEl],
      charts: () => [this.chartKeyCard(), this.chartReversalsCard()],
    }[sub] || (() => this.archivePage(s));
    fill(this.el, h('div', { class: 'settings' }, h('h1', null, SETTINGS_PAGES[sub] || 'Archive'), page()));
    if (sub === 'archive' || sub === 'markets') this.loadExtractors();
    if (sub === 'routines') {
      this.loadSchedule();
      this.timer = setInterval(() => this.refreshJobs(), 5000);
    }
  },

  /** Archive: the Socrates login the downloads use, then the archive folders. */
  archivePage(s) {
    const page = [
      h('h3', { class: 'section' }, 'Downloads'),
      this.accountEl,
      h('h3', { class: 'section' }, 'Archive'),
      h('p', { class: 'hint section-note' },
        'In Docker, enter /archive/arrays and /archive/blog, the folders mounted by the compose file. ' +
        'Without Docker, enter a local folder (e.g. /Users/username/SOC) or a connected network share, ' +
        'by its mounted path (e.g. /Volumes/Share/SOC) or its address (e.g. smb://nas.local/Share/SOC).'),
      this.card('arrays', 'Timing arrays folder',
        'The root folder with one subfolder per ticker (for example <code>AEM</code>, <code>BTC</code>, <code>DIA</code>) ' +
        'holding array images.',
        '/archive/arrays', s.arraysRoot),
      this.card('blog', 'Blog archive folder',
        'The root folder with one subfolder per year (<code>2009</code>, <code>2010</code>, …) holding the ' +
        '<code>.md</code> posts.',
        '/archive/blog', s.blogRoot),
    ];
    for (const kind of ['arrays', 'blog']) {
      if (s[kind + 'Root']) this.check(kind);
    }
    return page;
  },

  hide() {
    clearInterval(this.timer);
    this.timer = null;
  },

  /* ------------------------------------------------------------ folders */

  card(kind, title, desc, placeholder, value) {
    const input = h('input', { type: 'text', placeholder, spellcheck: 'false', autocomplete: 'off', autocapitalize: 'off' });
    input.value = value || '';
    const status = h('div', { class: 'status' });
    const button = h('button', { class: 'btn primary', onclick: () => this.validate(kind) }, 'Validate & save');
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); this.validate(kind); } });
    this[kind] = { input, status, button };
    return h('div', { class: 'card' },
      h('h2', null, title),
      h('p', { class: 'desc', html: desc }),
      h('div', { class: 'path-row' }, input, button),
      status);
  },

  setStatus(el, cls, text) {
    el.className = 'status ' + cls;
    el.textContent = text;
  },

  async check(kind) {
    const { status } = this[kind];
    this.setStatus(status, 'busy', 'Checking the saved folder…');
    try {
      const r = await api(`/api/settings/${kind}/check`);
      if (r.ok) this.setStatus(status, 'ok', `✓ Saved and readable: ${r.summary}`);
      else this.setStatus(status, 'err', `✕ Saved folder has a problem: ${r.error}`);
    } catch (e) {
      this.setStatus(status, 'err', `✕ ${e.message}`);
    }
  },

  async validate(kind) {
    const { input, button, status } = this[kind];
    button.disabled = true;
    this.setStatus(status, 'busy', 'Checking the folder…');
    try {
      const r = await postJSON(`/api/settings/${kind}`, { path: input.value });
      input.value = r.path;
      App.settings[kind + 'Root'] = r.path;
      App.settings[kind + 'Reachable'] = true;
      if (kind === 'arrays') { Arrays.data = null; this.loadExtractors(); }
      else { Blog.posts = null; Blog.fetchedAt = 0; }
      this.setStatus(status, 'ok', `✓ Saved. Found ${r.summary}.`);
    } catch (e) {
      this.setStatus(status, 'err', `✕ ${e.message}`);
    } finally {
      button.disabled = false;
    }
  },

  /* ------------------------------------------------------------ charts */

  chartKeyCard() {
    const card = h('div', { class: 'card' });
    const render = (message) => {
      const set = App.settings.chartKeySet;
      const key = h('input', {
        type: 'password', autocomplete: 'off', spellcheck: 'false',
        placeholder: set ? 'API key is set. Paste a new one to change it' : 'Twelve Data API key',
      });
      const status = h('div', { class: 'status' });
      const send = async (body, done) => {
        save.disabled = true;
        this.setStatus(status, 'busy', 'Saving…');
        try {
          const r = await postJSON('/api/settings/chart-key', body);
          App.settings.chartKeySet = r.chartKeySet;
          render(done);
        } catch (e) {
          this.setStatus(status, 'err', '✕ ' + e.message);
          save.disabled = false;
        }
      };
      const save = h('button', { class: 'btn primary', onclick: () => send({ apiKey: key.value }, '✓ Saved. Open a chart on the Arrays page to try it.') }, 'Save');
      key.addEventListener('keydown', (e) => { if (e.key === 'Enter') save.click(); });
      const remove = set ? h('button', { class: 'btn', onclick: () => send({ remove: true }, 'Removed.') }, 'Remove key') : null;
      if (message) this.setStatus(status, 'ok', message);
      else if (!set) this.setStatus(status, 'err', 'Not set yet. The chart on the Arrays page needs it.');
      fill(card,
        h('h2', null, 'Twelve Data API key'),
        h('p', { class: 'desc' }, 'The daily candles for the chart on the Arrays page come from ',
          h('a', { href: 'https://twelvedata.com/pricing', target: '_blank', rel: 'noopener' }, 'Twelve Data'),
          '. A free account allows 8 requests a minute and 800 a day. Each ticker costs one request the first time it is shown ' +
          'after the chart is opened.'),
        h('div', { class: 'form-grid' }, h('label', null, 'API key'), key),
        h('div', { class: 'card-actions' }, save, remove),
        status);
    };
    render();
    return card;
  },

  /** Which reversals the chart on the Arrays page draws. Each box is saved when it is clicked. */
  chartReversalsCard() {
    const st = { shown: true, weekly: true, monthly: true, weeklyCount: 3, monthlyCount: 3, ...(App.settings.chartReversals || {}) };
    const status = h('div', { class: 'status' });
    const send = async (key, value, undo) => {
      this.setStatus(status, 'busy', 'Saving…');
      try {
        const r = await postJSON('/api/settings/chart-reversals', { [key]: value });
        App.settings.chartReversals = r.chartReversals;
        Arrays.revShown = null; // the Arrays page starts again from the new default
        this.setStatus(status, 'ok', '✓ Saved.');
        return r.chartReversals;
      } catch (e) {
        undo();
        this.setStatus(status, 'err', '✕ ' + e.message);
        return null;
      }
    };
    const box = (key) => {
      const input = h('input', { type: 'checkbox' });
      input.checked = st[key];
      input.addEventListener('change', () => send(key, input.checked, () => { input.checked = !input.checked; }));
      return input;
    };
    const count = (key) => {
      const input = h('input', { type: 'number', min: 1, max: 5, step: 1, class: 'num', title: '1 to 5' });
      input.value = st[key];
      let last = st[key];
      input.addEventListener('change', async () => {
        const n = Math.min(5, Math.max(1, Math.round(Number(input.value) || last)));
        input.value = n;
        const saved = await send(key, n, () => { input.value = last; });
        if (saved) last = saved[key];
      });
      return h('span', { class: 'inline' }, 'show top', input);
    };
    const row = (label, input, extra) => [h('label', null, label),
      h('div', { class: 'inline' }, input, typeof extra === 'string' ? h('span', { class: 'hint' }, extra) : extra || null)];
    return h('div', { class: 'card' },
      h('h2', null, 'Reversals on the chart'),
      h('p', { class: 'desc' }, 'Display reversals on the chart, Major ones first.'),
      h('div', { class: 'form-grid' },
        row('Show by default', box('shown'), 'Press R on the Arrays page to show or hide them'),
        row('Weekly reversals', box('weekly'), count('weeklyCount')),
        row('Monthly reversals', box('monthly'), count('monthlyCount'))),
      status);
  },

  /* ------------------------------------------------------------ account and tickers */

  async loadExtractors() {
    try {
      this.ext = await api('/api/extractors');
    } catch (e) {
      fill(this.accountEl, h('div', { class: 'status err' }, '✕ ' + e.message));
      fill(this.tickersEl, );
      return;
    }
    this.renderAccount();
    this.renderTickers();
  },

  renderAccount() {
    const x = this.ext;
    const user = h('input', { type: 'email', placeholder: 'you@example.com', autocomplete: 'off', spellcheck: 'false' });
    user.value = x.username;
    const pass = h('input', {
      type: 'password', autocomplete: 'new-password',
      placeholder: x.passwordSet ? 'Password is set. Type a new one to change it' : 'Password',
    });
    const status = h('div', { class: 'status' });
    const save = async () => {
      button.disabled = true;
      this.setStatus(status, 'busy', 'Saving…');
      try {
        await postJSON('/api/extractors/credentials', { username: user.value, password: pass.value });
        pass.value = '';
        await this.loadExtractors();
        this.setStatus(this.accountStatus, 'ok', '✓ Saved to both the arrays and the blog downloader.');
      } catch (e) {
        this.setStatus(status, 'err', '✕ ' + e.message);
      } finally {
        button.disabled = false;
      }
    };
    const button = h('button', { class: 'btn primary', onclick: save }, 'Save');
    for (const input of [user, pass]) input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); });
    this.accountStatus = status;
    const missing = !x.passwordSet || !x.blogLoginSet;
    if (missing && !status.textContent) {
      this.setStatus(status, 'err', 'Not set yet. Automatic downloads of the arrays and the private blog need it.');
    }
    fill(this.accountEl, 
      h('h2', null, 'Socrates Platform account'),
      h('p', { class: 'desc' }, 'Used by the arrays extractor and the private blog download.'),
      h('div', { class: 'form-grid' },
        h('label', null, 'E-mail'), user,
        h('label', null, 'Password'), pass),
      h('div', { class: 'card-actions' }, button),
      status);
  },

  /* Ticker changes are collected here and written to the config only on Save:
     { add: [{ symbol, url }], remove: Set(symbol) } */
  pendingTickers() {
    if (!this.tickerEdits) this.tickerEdits = { add: [], remove: new Set() };
    return this.tickerEdits;
  },

  renderTickers(message) {
    const x = this.ext;
    const root = App.settings.arraysRoot;
    const edits = this.pendingTickers();
    const status = h('div', { class: 'status' });
    const symbol = h('input', { type: 'text', placeholder: 'TQQQ', class: 'symbol-input', spellcheck: 'false', autocomplete: 'off' });
    const url = h('input', { type: 'url', placeholder: 'https://standard.socratesplatform.com/MarketLanding/Analysis/?analysisId=…', spellcheck: 'false' });
    symbol.addEventListener('input', () => { symbol.value = symbol.value.toUpperCase(); });
    const changes = edits.add.length + edits.remove.size;

    const add = () => {
      const s = symbol.value.trim().toUpperCase(), u = url.value.trim();
      const taken = x.tickers.some((t) => t.symbol === s && !edits.remove.has(s)) || edits.add.some((t) => t.symbol === s);
      let error = null;
      if (!/^[A-Z0-9]{1,16}$/.test(s)) error = 'A ticker symbol uses only letters and digits, e.g. TQQQ.';
      else if (!u.startsWith('https://standard.socratesplatform.com/')) error = 'The URL must be a Socrates Platform page starting with https://standard.socratesplatform.com/';
      else if (taken) error = `${s} is already in the list.`;
      if (error) { this.setStatus(status, 'err', '✕ ' + error); return; }
      if (edits.remove.has(s)) edits.remove.delete(s);  // re-adding a ticker marked for removal
      edits.add.push({ symbol: s, url: u });
      this.renderTickers();
      $('.tickers .symbol-input').focus();
    };
    const save = async () => {
      saveBtn.disabled = true;
      this.setStatus(status, 'busy', 'Saving…');
      try {
        const r = await postJSON('/api/extractors/tickers', { add: edits.add, remove: [...edits.remove] });
        this.tickerEdits = null;
        await this.loadExtractors();
        const parts = [];
        if (r.added.length) parts.push(`Downloading the daily, weekly and monthly arrays and reversals of ${r.added.join(', ')} now.`);
        if (r.removed.length) parts.push(`Removed ${r.removed.join(', ')}. Their folders and arrays were kept.`);
        this.setStatus(this.tickerStatus, 'ok', '✓ Saved. ' + parts.join(' '));
        this.refreshJobs();
      } catch (e) {
        this.setStatus(status, 'err', '✕ ' + e.message);
        saveBtn.disabled = false;
      }
    };
    const discard = () => { this.tickerEdits = null; this.renderTickers(); };
    const toggleRemove = (s) => {
      edits.remove.has(s) ? edits.remove.delete(s) : edits.remove.add(s);
      this.renderTickers();
    };
    const dropAdded = (s) => {
      edits.add = edits.add.filter((t) => t.symbol !== s);
      this.renderTickers();
    };
    const addBtn = h('button', { class: 'btn', onclick: add, disabled: !root }, 'Add ticker');
    const saveBtn = h('button', { class: 'btn primary', onclick: save, disabled: !changes }, 'Save');
    for (const input of [symbol, url]) input.addEventListener('keydown', (e) => { if (e.key === 'Enter') add(); });

    const link = (u) => h('a', { href: u, target: '_blank', rel: 'noopener noreferrer', title: u },
      u.replace(/^https:\/\/standard\.socratesplatform\.com/, ''));
    const all = [...x.tickers.map((t) => ({ ...t, pending: false })), ...edits.add.map((t) => ({ ...t, pending: true }))]
      .sort((a, b) => a.symbol.localeCompare(b.symbol));
    const uncovered = new Set(x.chartUncovered || []);
    const noChart = (t) => (t.pending ? uncovered.has(t.symbol) : t.chartCovered === false)
      ? h('span', { class: 'no-chart', title: 'Market data is not available for the charts' }, '⚠') : null;
    const rows = all.map((t) => {
      if (t.pending) {
        return h('tr', { class: 'pending-add' },
          h('td', { class: 'sym' }, t.symbol, noChart(t), h('span', { class: 'tag-new' }, 'new')),
          h('td', { class: 'url' }, link(t.url)),
          h('td', { class: 'path' }, root ? `${root}/${t.symbol}` : ''),
          h('td', null, h('button', { class: 'btn small', onclick: () => dropAdded(t.symbol) }, 'Remove')));
      }
      const removed = edits.remove.has(t.symbol);
      return h('tr', { class: removed ? 'pending-remove' : '' },
        h('td', { class: 'sym' }, t.symbol, noChart(t)),
        h('td', { class: 'url' }, link(t.url)),
        h('td', { class: 'path' }, t.folder, t.folder && !t.folderExists ? h('span', { class: 'warn' }, ' (missing)') : null),
        h('td', null, h('button', { class: 'btn small', onclick: () => toggleRemove(t.symbol) }, removed ? 'Undo' : 'Remove')));
    });
    this.tickerStatus = status;
    if (message) this.setStatus(status, message[0], message[1]);
    else if (changes) {
      this.setStatus(status, 'busy', `${changes} unsaved change${changes === 1 ? '' : 's'}. Press Save to apply ` +
        (edits.add.length ? 'and download the arrays and reversals of the new tickers.' : 'them.'));
    }
    fill(this.tickersEl,
      h('h2', null, `Market subscriptions (${x.tickers.length})`),
      h('p', { class: 'desc' }, 'The arrays and reversals of the markets you subscribe to are downloaded automatically.'),
      h('p', { class: 'desc' },
        'Removing a ticker stops its arrays and reversals from being downloaded. ' +
        'Its folder, arrays and saved reversals are kept, and the arrays stay visible on the Arrays page.'),
      root ? null : h('div', { class: 'status err' }, 'Set the timing arrays folder on the ',
        h('a', { href: '#/settings/archive' }, 'Archive'), ' page first.'),
      h('div', { class: 'table-wrap' }, h('table', { class: 'tickers' },
        h('thead', null,
          h('tr', null, h('th', null, 'Ticker'), h('th', null, 'Socrates page'), h('th', null, 'Saved to'), h('th')),
          h('tr', { class: 'add-row' }, h('td', null, symbol), h('td', { colspan: 2 }, url), h('td', null, addBtn))),
        h('tbody', null, rows.length ? rows : h('tr', null, h('td', { colspan: 4, class: 'hint' }, 'No tickers yet.'))))),
      h('div', { class: 'card-actions' },
        changes ? h('button', { class: 'btn', onclick: discard }, 'Discard changes') : null, saveBtn),
      status);
  },

  /* ------------------------------------------------------------ schedule */

  async loadSchedule() {
    try {
      const r = await api('/api/schedule');
      this.sched = r.schedule;
      this.timezones = r.timezones;
      this.renderSchedule();
      this.renderJobs(r.status);
    } catch (e) {
      fill(this.scheduleEl, h('div', { class: 'status err' }, '✕ ' + e.message));
    }
  },

  renderSchedule() {
    const s = this.sched;
    const f = {};
    const num = (key, attrs) => (f[key] = h('input', { type: 'number', class: 'num', value: s[key], ...attrs }));
    const check = (key) => {
      f[key] = h('input', { type: 'checkbox' });
      f[key].checked = !!s[key];
      return f[key];
    };
    f.timezone = h('input', { type: 'text', list: 'tz-list', value: s.timezone, spellcheck: 'false', class: 'tz' });
    f.startTime = h('input', { type: 'time', value: s.startTime });
    const status = h('div', { class: 'status' });
    const save = async () => {
      const body = {};
      for (const [k, el] of Object.entries(f)) body[k] = el.type === 'checkbox' ? el.checked : el.value;
      button.disabled = true;
      this.setStatus(status, 'busy', 'Saving…');
      try {
        const r = await postJSON('/api/schedule', body);
        this.sched = r.schedule;
        this.renderJobs(r.status);
        this.setStatus(status, 'ok', '✓ Saved. The next runs below use the new settings.');
      } catch (e) {
        this.setStatus(status, 'err', '✕ ' + e.message);
      } finally {
        button.disabled = false;
      }
    };
    const button = h('button', { class: 'btn primary', onclick: save }, 'Save routines');
    const row = (label, ...content) => [h('label', null, label), h('div', { class: 'inline' }, ...content)];
    fill(this.scheduleEl, 
      h('h2', null, 'Automatic downloads'),
      h('p', { class: 'desc' }, 'Runs the arrays & reversals extractor and the blog downloader in the background.'),
      h('datalist', { id: 'tz-list' }, this.timezones.map((z) => h('option', { value: z }))),
      h('div', { class: 'form-grid' },
        row('Time zone', f.timezone),
        row('First run', f.startTime),
        h('div', { class: 'form-sub' }, 'Timing arrays & reversals'),
        row('Enabled', check('arraysEnabled')),
        row('Retry after', num('arraysRetryHours', { min: 0.25, max: 24, step: 0.25 }), 'hours',
          h('span', { class: 'hint' }, 'Failed only')),
        row('Attempts per day', num('arraysMaxAttempts', { min: 1, max: 10, step: 1 })),
        h('div', { class: 'form-sub' }, 'Public blog (pages 1–2, no login)'),
        row('Enabled', check('publicEnabled')),
        row('Check every', num('publicEveryHours', { min: 1, max: 168, step: 1 }), 'hours'),
        h('div', { class: 'form-sub' }, 'Private blog (page 1, one login)'),
        row('Enabled', check('privateEnabled')),
        row('Check every', num('privateEveryHours', { min: 1, max: 168, step: 1 }), 'hours'),
        h('div', { class: 'form-sub' }, 'Logs'),
        row('Keep logs for', num('logRetentionDays', { min: 1, max: 3650, step: 1 }), 'days')),
      h('div', { class: 'card-actions' }, button),
      status);
  },

  async refreshJobs() {
    if (!this.jobsEl || !this.jobsEl.isConnected) return;
    // Don't redraw the table while the user is typing in a Pages field.
    if (this.jobsEl.contains(document.activeElement) && document.activeElement.tagName === 'INPUT') return;
    try { this.renderJobs(await api('/api/jobs')); } catch { /* try again on the next tick */ }
  },

  renderJobs(st) {
    if (!st) return;
    const rows = Object.entries(st.jobs).map(([job, j]) => {
      let state;
      if (j.running) state = h('span', { class: 'run-badge running' }, 'Running');
      else if (j.queued) state = h('span', { class: 'run-badge queued' }, 'Waiting');
      else if (j.lastRun) state = statusBadge(j.lastRun.status);
      else state = h('span', { class: 'hint' }, 'Never run');
      const last = j.lastRun
        ? h('div', null, h('a', { href: `#/logs?run=${encodeURIComponent(j.lastRun.id)}` }, j.lastRun.start),
          j.lastRun.summary ? h('div', { class: 'summary' }, j.lastRun.summary) : null)
        : null;
      let next = h('span', { class: 'hint' }, 'Off');
      if (j.retry) next = h('div', null, `Retry at ${j.retry.at.replace('T', ' ')}`, h('div', { class: 'summary' }, `attempt ${j.retry.attempt}: ${j.retry.remaining.join(', ')}`));
      else if (j.nextRun) next = h('div', null, j.nextRun.at, j.nextRun.frames ? h('div', { class: 'summary' }, j.nextRun.frames.map((f) => FRAME_LABEL[f]).join(' + ')) : null);
      return h('tr', null,
        h('td', { class: 'job' }, j.label),
        h('td', null, state, last),
        h('td', null, next),
        h('td', null, this.runNowControl(job, j)));
    });
    fill(this.jobsEl, 
      h('h2', null, 'Jobs'),
      h('p', { class: 'desc' }, 'Run now is for manual runs only and doesn\'t affect the routines.'),
      h('p', { class: 'desc' }, `Times are in ${st.timezone} (now ${st.now}). `, h('a', { href: '#/logs' }, 'Open the logs')),
      h('div', { class: 'table-wrap' }, h('table', { class: 'jobs' },
        h('thead', null, h('tr', null, h('th', null, 'Job'), h('th', null, 'Last run'), h('th', null, 'Next run'), h('th'))),
        h('tbody', null, rows))),
      this.jobStatus || (this.jobStatus = h('div', { class: 'status' })));
  },

  runNowControl(job, j) {
    const busy = j.running || j.queued;
    if (job !== 'arrays') {
      // Pages to check in a manual run, e.g. "1", "1-3" or "1,3,5". Kept while the app is open.
      if (!this.runPages) this.runPages = {};
      const input = h('input', { type: 'text', class: 'pages-input', value: this.runPages[job] || '1',
        title: 'Blog list pages to check, e.g. 1, 1-3 or 1,3,5', spellcheck: 'false', autocomplete: 'off' });
      input.addEventListener('input', () => { this.runPages[job] = input.value; });
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !busy) this.runNow(job, null, input.value); });
      return h('div', { class: 'run-now' },
        h('label', { class: 'frame-check' }, 'Pages', input),
        h('button', { class: 'btn small', disabled: busy, onclick: () => this.runNow(job, null, input.value) }, 'Run now'));
    }
    if (!this.runFrames) this.runFrames = new Set(['D']);
    return h('div', { class: 'run-now' },
      ['D', 'W', 'M'].map((f) => {
        const box = h('input', { type: 'checkbox', title: FRAME_LABEL[f] });
        box.checked = this.runFrames.has(f);
        box.addEventListener('change', () => { box.checked ? this.runFrames.add(f) : this.runFrames.delete(f); });
        return h('label', { class: 'frame-check', title: FRAME_LABEL[f] }, box, f);
      }),
      h('button', { class: 'btn small', disabled: busy, onclick: () => this.runNow(job, [...this.runFrames]) }, 'Run now'));
  },

  async runNow(job, frames, pages) {
    try {
      const r = await postJSON(`/api/jobs/${job}/run`, { frames, pages });
      this.renderJobs(r.status);
      this.setStatus(this.jobStatus, 'ok', '✓ Started. Follow it on the Logs page.');
    } catch (e) {
      this.setStatus(this.jobStatus, 'err', '✕ ' + e.message);
    }
  },
};
