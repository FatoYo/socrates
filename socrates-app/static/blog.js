'use strict';

const ROW_HEIGHT = 64; // must match .post-row height in app.css
// cats holds category NAMES: positions in the list shift when a new category appears.
const EMPTY_FILTERS = () => ({ year: null, month: null, day: null, vis: 'all', cats: [] });

const Blog = {
  el: $('#page-blog'),
  posts: null,       // [{ id, date, title, priv, cats: [index], y, m, d, dn }] latest first
  byId: new Map(),
  categories: [],
  catIndex: new Map(),
  catSel: new Set(),   // positions of the selected categories in the current index
  fetchedAt: 0,
  filters: Object.assign(EMPTY_FILTERS(), store.get('blogFilters', {})),
  filtered: [],
  currentId: store.get('blogPost', null),
  shownId: null,
  token: 0,
  catOpen: false,
  collapsed: store.get('blogFiltersCollapsed', false),
  catQuery: '',

  async show() {
    if (!App.settings.blogReachable) {
      this.el.replaceChildren(notice('Blog folder not set',
        'Choose the root folder of the blog archive on the Settings page.'));
      this.posts = null;
      return;
    }
    if (!this.posts || Date.now() - this.fetchedAt > 60000) {
      if (!this.posts) {
        this.el.replaceChildren(h('div', { class: 'empty' },
          'Loading the blog index… The first scan of a large archive can take a little while.'));
      }
      try {
        this.ingest(await api('/api/blog/index'));
      } catch (e) {
        this.posts = null;
        this.el.replaceChildren(e.status === 409
          ? notice('Blog folder not reachable', 'The saved blog folder cannot be read. Check it on the Settings page.')
          : notice('Could not load the blog', e.message, false));
        return;
      }
    }
    if (!this.filtersEl || !this.el.contains(this.filtersEl)) this.build();
    this.apply('nearest');
  },

  /** After a download: reload the index, and if the newest post was shown, show the new newest one. */
  async refreshAfterDownload() {
    const wasNewest = this.filtered.length > 0 && this.filtered[0].id === this.currentId;
    this.fetchedAt = 0;
    await this.show();
    if (wasNewest && this.filtered.length && this.filtered[0].id !== this.currentId) this.select(this.filtered[0].id, 'center');
  },

  ingest(res) {
    this.categories = res.categories;
    this.catIndex = new Map(res.categories.map((c, i) => [c, i]));
    this.posts = res.posts.map(([id, date, title, priv, cats]) => ({
      id, date, title, priv: !!priv, cats,
      y: +date.slice(0, 4), m: +date.slice(5, 7), d: +date.slice(8, 10), dn: dayNum(date),
    }));
    this.byId = new Map(this.posts.map((p) => [p.id, p]));
    this.fetchedAt = Date.now();
    const f = this.filters;
    // Keep selected categories that still exist (older versions stored positions: dropped)
    f.cats = (Array.isArray(f.cats) ? f.cats : []).filter((c) => typeof c === 'string' && this.catIndex.has(c));
    if (!['all', 'public', 'private'].includes(f.vis)) f.vis = 'all';
  },

  build() {
    this.filtersEl = h('div', { class: 'blog-filters' });
    this.spacer = h('div', { class: 'spacer' });
    this.listEl = h('div', { class: 'post-list' }, this.spacer);
    this.listEl.addEventListener('scroll', () => this.renderRows());
    this.viewEl = h('article', { class: 'post-view' });
    this.el.replaceChildren(this.filtersEl, h('div', { class: 'blog-body' }, this.listEl, this.viewEl));
    if (this.resizeObserver) this.resizeObserver.disconnect();
    this.resizeObserver = new ResizeObserver(() => this.renderRows());
    this.resizeObserver.observe(this.listEl);
  },

  /* ------------------------------------------------------------ filtering */

  /** skip: letters of filters to ignore, y=year m=month d=day c=categories (type is always applied). */
  match(p, skip = '') {
    const f = this.filters;
    if (f.vis === 'public' && p.priv) return false;
    if (f.vis === 'private' && !p.priv) return false;
    if (f.year && !skip.includes('y') && p.y !== f.year) return false;
    if (f.month && !skip.includes('m') && p.m !== f.month) return false;
    if (f.day && !skip.includes('d') && p.d !== f.day) return false;
    if (this.catSel.size && !skip.includes('c') && !p.cats.some((c) => this.catSel.has(c))) return false;
    return true;
  },

  counts(skip, key) {
    const out = new Map();
    for (const p of this.posts) {
      if (!this.match(p, skip)) continue;
      out.set(p[key], (out.get(p[key]) || 0) + 1);
    }
    return out;
  },

  /** Drop date selections that no longer have posts, so the list never ends up empty because of them. */
  prune() {
    const f = this.filters;
    if (f.year && !this.counts('ymd', 'y').has(f.year)) f.year = null;
    if (f.month && !this.counts('md', 'm').has(f.month)) { f.month = null; f.day = null; }
    if (!f.month) f.day = null;
    if (f.day && !this.counts('d', 'd').has(f.day)) f.day = null;
  },

  isFiltered() {
    const f = this.filters;
    return !!(f.year || f.month || f.day || f.vis !== 'all' || f.cats.length);
  },

  setFilter(patch) {
    Object.assign(this.filters, patch);
    this.apply('nearest');
  },

  toggleCat(i) {
    const name = this.categories[i];
    if (name == null) return;
    const cats = this.filters.cats.includes(name) ? this.filters.cats.filter((c) => c !== name) : [...this.filters.cats, name];
    this.setFilter({ cats });
  },

  clear() {
    this.filters = EMPTY_FILTERS();
    this.catQuery = '';
    this.listEl.scrollTop = 0;
    this.apply('latest');
  },

  /**
   * Recompute the list. The shown post stays if it still matches; otherwise the matching post
   * closest in date to it is shown ("latest" mode shows the newest post).
   */
  apply(mode) {
    // Positions of the selected categories in this index, for matching posts
    this.catSel = new Set(this.filters.cats.map((c) => this.catIndex.get(c)).filter((i) => i != null));
    this.prune();
    this.filtered = this.posts.filter((p) => this.match(p));
    store.set('blogFilters', this.filters);
    this.renderFilters();
    let target = null;
    if (this.filtered.length) {
      const cur = this.byId.get(this.currentId);
      if (mode === 'latest' || !cur) target = this.filtered[0];
      else if (this.match(cur)) target = cur;
      else {
        let bestDiff = Infinity;
        for (const p of this.filtered) {
          const diff = Math.abs(p.dn - cur.dn);
          if (diff < bestDiff) { target = p; bestDiff = diff; }
        }
      }
    }
    if (target) {
      this.select(target.id, 'center');
    } else {
      this.renderRows();
      this.shownId = null;
      this.token++;
      this.viewEl.replaceChildren(h('div', { class: 'empty' }, 'No posts match these filters.'));
    }
  },

  /* ------------------------------------------------------------ filter header */

  renderFilters() {
    const f = this.filters;
    const years = this.counts('ymd', 'y');
    const months = this.counts('md', 'm');
    const days = f.month ? this.counts('d', 'd') : null;
    const sortNum = (m) => [...m.keys()].sort((a, b) => a - b);
    const chip = (label, active, onclick, count, title) =>
      h('button', { class: 'chip' + (active ? ' active' : ''), onclick, title },
        label, count != null ? h('span', { class: 'count' }, count.toLocaleString()) : null);
    const row = (label, ...content) => h('div', { class: 'filter-row' }, h('div', { class: 'label' }, label), ...content);

    this.catChipsEl = h('div', { class: 'chips' });
    const catRow = row('Categories',
      h('div', { class: 'chips' },
        h('button', { class: 'chip', onclick: () => { this.catOpen = !this.catOpen; this.renderFilters(); } },
          this.catOpen ? 'Hide categories ▴' : `Show categories (${this.categories.length}) ▾`),
        this.catOpen ? this.catSearchInput() : null,
        this.catChipsEl));

    const describe = [];
    if (f.year) describe.push(String(f.year));
    if (f.month) describe.push(MONTH_LONG[f.month - 1] + (f.day ? ' ' + f.day : ''));
    if (f.vis !== 'all') describe.push(f.vis);
    if (f.cats.length) describe.push(f.cats.join(' or '));

    const collapsed = this.collapsed;
    this.filtersEl.classList.toggle('collapsed', collapsed);
    const summary = h('div', { class: 'filter-summary' },
      h('button', {
        class: 'btn small filter-toggle', 'aria-expanded': String(!collapsed),
        title: collapsed ? 'Show the filters' : 'Hide the filters to make room for the post',
        onclick: () => this.toggleCollapsed(),
      }, icon(collapsed ? 'down' : 'up', 13), collapsed ? 'Filters' : 'Hide filters'),
      h('span', { class: 'count' }, `${this.filtered.length.toLocaleString()} ${this.filtered.length === 1 ? 'post' : 'posts'}`),
      h('span', { class: 'hint' }, describe.length ? describe.join(' · ') : 'all posts, latest first'),
      h('span', { class: 'spacer' }),
      h('span', { class: 'hint' }, h('kbd', null, '↑'), ' ', h('kbd', null, '↓'), ' newer / older'),
      h('button', { class: 'btn small', disabled: !this.isFiltered(), onclick: () => this.clear() }, '✕ Clear filters'),
      Downloads.button('blog', () => this.refreshAfterDownload()));
    if (collapsed) {
      this.filtersEl.replaceChildren(summary);
      return;
    }

    this.filtersEl.replaceChildren(
      row('Year', h('div', { class: 'chips' }, sortNum(years).map((y) =>
        chip(y, f.year === y, () => this.setFilter({ year: f.year === y ? null : y }), null, `${years.get(y).toLocaleString()} posts`)))),
      row('Month', h('div', { class: 'chips' }, sortNum(months).map((m) =>
        chip(MONTH_SHORT[m - 1], f.month === m, () => this.setFilter({ month: f.month === m ? null : m, day: null }), null,
          `${months.get(m).toLocaleString()} posts`)))),
      row('Day', days
        ? h('div', { class: 'chips' }, sortNum(days).map((d) =>
          chip(d, f.day === d, () => this.setFilter({ day: f.day === d ? null : d }), null, `${days.get(d).toLocaleString()} posts`)))
        : h('div', { class: 'hint' }, 'Pick a month to see the days with posts.')),
      row('Type', h('div', { class: 'seg' }, [['all', 'All'], ['public', 'Public'], ['private', 'Private']].map(([v, label]) =>
        h('button', { class: f.vis === v ? 'active' : '', onclick: () => this.setFilter({ vis: v }) }, label)))),
      catRow,
      summary);
    this.renderCatChips();
  },

  toggleCollapsed() {
    this.collapsed = !this.collapsed;
    store.set('blogFiltersCollapsed', this.collapsed);
    this.renderFilters();
  },

  catSearchInput() {
    const input = h('input', { class: 'cat-search', type: 'search', placeholder: 'Find a category…', value: this.catQuery });
    input.addEventListener('input', () => { this.catQuery = input.value; this.renderCatChips(); });
    input.addEventListener('keydown', (e) => { if (e.key === 'Escape') { input.value = ''; this.catQuery = ''; this.renderCatChips(); } });
    return input;
  },

  renderCatChips() {
    const f = this.filters;
    const counts = new Map();
    for (const p of this.posts) {
      if (!this.match(p, 'c')) continue;
      for (const c of p.cats) counts.set(c, (counts.get(c) || 0) + 1);
    }
    const q = this.catQuery.trim().toLowerCase();
    const show = this.categories
      .map((name, i) => ({ name, i, n: counts.get(i) || 0 }))
      .filter((c) => f.cats.includes(c.name) || (this.catOpen && c.n > 0 && (!q || c.name.toLowerCase().includes(q))));
    this.catChipsEl.replaceChildren(...show.map((c) =>
      h('button', { class: 'chip' + (f.cats.includes(c.name) ? ' active' : ''), onclick: () => this.toggleCat(c.i) },
        c.name, h('span', { class: 'count' }, c.n.toLocaleString()))));
  },

  /* ------------------------------------------------------------ post list (virtualised) */

  renderRows() {
    if (!this.listEl) return;
    const n = this.filtered.length;
    if (!n) {
      this.spacer.style.height = '';
      this.spacer.replaceChildren(h('div', { class: 'empty' }, 'No posts'));
      return;
    }
    this.spacer.style.height = n * ROW_HEIGHT + 'px';
    const top = this.listEl.scrollTop, height = this.listEl.clientHeight || 600;
    const start = Math.max(0, Math.floor(top / ROW_HEIGHT) - 6);
    const end = Math.min(n, Math.ceil((top + height) / ROW_HEIGHT) + 6);
    const rows = [];
    for (let i = start; i < end; i++) {
      const p = this.filtered[i];
      rows.push(h('div', {
        class: 'post-row' + (p.id === this.currentId ? ' active' : ''),
        style: `top:${i * ROW_HEIGHT}px`,
        title: p.title,
        onclick: () => this.select(p.id),
      },
      h('div', { class: 'd' }, fmtShort(p.date), p.priv ? h('span', { class: 'badge private' }, 'PRIVATE') : null),
      h('div', { class: 't' }, p.title)));
    }
    this.spacer.replaceChildren(...rows);
  },

  scrollToRow(i, how) {
    const top = i * ROW_HEIGHT, view = this.listEl.clientHeight, st = this.listEl.scrollTop;
    if (how === 'center') {
      if (top < st || top + ROW_HEIGHT > st + view) this.listEl.scrollTop = top - view / 2 + ROW_HEIGHT / 2;
    } else if (top < st) this.listEl.scrollTop = top;
    else if (top + ROW_HEIGHT > st + view) this.listEl.scrollTop = top + ROW_HEIGHT - view;
  },

  select(id, how = 'nearest') {
    this.currentId = id;
    store.set('blogPost', id);
    const i = this.filtered.findIndex((p) => p.id === id);
    if (i >= 0) this.scrollToRow(i, how);
    this.renderRows();
    if (this.shownId !== id) this.loadPost(id);
    else if (this.lastPost && this.lastPost.id === id && this.metaEl && this.metaEl.isConnected) {
      // Same post: refresh only the header (category highlights, newer/older buttons).
      const meta = this.buildMeta(this.lastPost);
      this.metaEl.replaceWith(meta);
      this.metaEl = meta;
      this.renderPostNav();
    }
  },

  move(dir) { // -1 = newer (up the list), +1 = older
    if (!this.filtered.length) return;
    const i = this.filtered.findIndex((p) => p.id === this.currentId);
    const next = i < 0 ? 0 : i + dir;
    if (next < 0 || next >= this.filtered.length) return;
    this.select(this.filtered[next].id);
  },

  /* ------------------------------------------------------------ post view */

  async loadPost(id) {
    this.shownId = id;
    const token = ++this.token;
    try {
      const post = await api('/api/blog/post?id=' + encodeURIComponent(id));
      if (token === this.token) this.renderPost(post);
    } catch (e) {
      if (token === this.token) {
        this.viewEl.replaceChildren(h('div', { class: 'empty' }, 'This post could not be loaded: ' + e.message));
      }
    }
  },

  renderPost(post) {
    this.lastPost = post;
    this.metaEl = this.buildMeta(post);
    const body = h('div', { class: 'markdown', html: post.html }); // HTML is sanitised on the server
    body.querySelectorAll('a[href]').forEach((a) => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
    body.querySelectorAll('img').forEach((img) => { img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; });
    this.viewEl.replaceChildren(this.metaEl, body);
    this.viewEl.scrollTop = 0;
    this.renderPostNav();
  },

  buildMeta(post) {
    this.navEl = h('span', { class: 'post-nav' });
    return h('div', { class: 'post-meta' },
      h('span', { class: 'badge ' + (post.private ? 'private' : 'public') }, post.private ? 'PRIVATE' : 'PUBLIC'),
      h('span', null, fmtLong(post.date)),
      post.author ? h('span', null, post.author) : null,
      post.categories.map((c) => {
        const i = this.catIndex.get(c);
        return h('button', {
          class: 'chip' + (this.filters.cats.includes(c) ? ' active' : ''),
          title: 'Filter by this category',
          onclick: () => i != null && this.toggleCat(i),
        }, c);
      }),
      h('span', { class: 'spacer' }),
      this.navEl,
      post.source ? h('a', { href: post.source, target: '_blank', rel: 'noopener noreferrer' }, 'Original ↗') : null);
  },

  renderPostNav() {
    if (!this.navEl) return;
    const i = this.filtered.findIndex((p) => p.id === this.currentId);
    this.navEl.replaceChildren(
      h('button', { class: 'btn small', title: 'Newer post (↑)', disabled: i <= 0, onclick: () => this.move(-1) }, icon('up', 13), 'Newer'),
      h('button', { class: 'btn small', title: 'Older post (↓)', disabled: i < 0 || i >= this.filtered.length - 1, onclick: () => this.move(1) }, icon('down', 13), 'Older'));
  },

  onKey(e) {
    if (!this.posts || !this.listEl || e.altKey || e.metaKey || e.ctrlKey || e.shiftKey) return;
    if (e.key === 'ArrowUp') { e.preventDefault(); this.move(-1); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); this.move(1); }
  },
};
