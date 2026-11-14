'use strict';

const PAGES = { arrays: Arrays, blog: Blog, logs: Logs, settings: Settings };
const PAGE_TITLES = { arrays: 'Arrays', blog: 'Blog', logs: 'Logs', settings: 'Settings' };

/** "#/logs?run=arrays/2026-11-12_050000" -> { name: "logs", params: URLSearchParams } */
function parseHash() {
  const [path, query] = location.hash.replace(/^#\/?/, '').split('?');
  return { name: path, params: new URLSearchParams(query || '') };
}

const App = {
  el: $('#app'),
  settings: {},
  route: null,

  async init() {
    const mobile = window.matchMedia('(max-width: 760px)');
    if (store.get('sidebarCollapsed', false)) this.el.classList.add('collapsed');
    $('#menuToggle').addEventListener('click', () => {
      if (mobile.matches) this.el.classList.toggle('mobile-open');
      else store.set('sidebarCollapsed', this.el.classList.toggle('collapsed'));
    });
    $('#mobileMenu').addEventListener('click', () => this.el.classList.add('mobile-open'));
    $('#backdrop').addEventListener('click', () => this.el.classList.remove('mobile-open'));

    window.addEventListener('hashchange', () => this.go());
    document.addEventListener('keydown', (e) => this.onKey(e));

    try { this.settings = await api('/api/settings'); } catch { this.settings = {}; }
    this.showVersion(this.settings.build);
    if (!PAGES[parseHash().name]) {
      const configured = this.settings.arraysReachable || this.settings.blogReachable;
      const last = store.get('route', 'arrays');
      location.replace('#/' + (configured ? (PAGES[last] ? last : 'arrays') : 'settings'));
    }
    this.go();
  },

  /** Version and source link at the bottom of the menu. */
  showVersion(build) {
    if (!build) return;
    const link = $('#appVersion');
    const dev = build.version === 'dev';
    $('.version-text', link).textContent = dev ? 'dev' : `v${build.version}`;
    link.href = build.repo;
    link.title = dev ? 'Development version · source code on GitHub'
      : `Version ${build.version}` + (build.revision ? ` (commit ${build.revision.slice(0, 7)})` : '') + ' · source code on GitHub';
  },

  go() {
    let { name, params } = parseHash();
    if (!PAGES[name]) name = 'arrays';
    const same = name === this.route && !document.getElementById('page-' + name).hidden;
    if (same && !params.toString()) return;
    if (this.route && this.route !== name && PAGES[this.route].hide) PAGES[this.route].hide();
    this.route = name;
    for (const key of Object.keys(PAGES)) document.getElementById('page-' + key).hidden = key !== name;
    document.querySelectorAll('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.route === name));
    $('#mobilePage').textContent = PAGE_TITLES[name];
    document.title = `Socrates · ${PAGE_TITLES[name]}`;
    this.el.classList.remove('mobile-open');
    Calendar.close();
    if (Search.isOpen()) Search.close();
    store.set('route', name);
    PAGES[name].show(params);
  },

  onKey(e) {
    if (Search.isOpen()) return; // the search box handles its own keys
    if (Calendar.isOpen() && e.key === 'Escape') { e.preventDefault(); Calendar.close(); return; }
    if (e.key === 'Escape' && this.el.classList.contains('mobile-open')) { this.el.classList.remove('mobile-open'); return; }
    if (isTyping(e)) return;
    if (this.route === 'arrays') Arrays.onKey(e);
    else if (this.route === 'blog') Blog.onKey(e);
  },
};

App.init();
