'use strict';

/**
 * The daily candle chart shown next to an array (Arrays page, "+ Add chart"). Uses TradingView
 * Lightweight Charts (static/vendor, Apache-2.0); the library is loaded the first time a chart is
 * opened. Candles come from /api/chart/<ticker>, which caches Twelve Data's answers.
 */
const PriceChart = {
  library: null,  // promise of the loaded library
  VISIBLE: 100,   // candles in view when a chart opens; the rest of the year is a scroll or zoom away
  /* Reversal rays: width by timeframe, colour by timeframe and direction (elected: grey),
     dashed unless the reversal is Major (or Double). */
  RAY_STYLE: {
    W: { width: 2, bullish: '#22c55e', bearish: '#ef4444', elected: '#9ca3af' },
    M: { width: 3, bullish: '#15803d', bearish: '#991b1b', elected: '#4b5563' },
  },

  load() {
    if (!this.library) {
      this.library = new Promise((resolve, reject) => {
        const script = h('script', { src: '/static/vendor/lightweight-charts.standalone.production.js' });
        script.onload = () => resolve(window.LightweightCharts);
        script.onerror = () => { this.library = null; reject(new Error('The chart library could not be loaded.')); };
        document.head.append(script);
      });
    }
    return this.library;
  },

  /** Colours from the page's light or dark theme. */
  theme() {
    const css = getComputedStyle(document.documentElement);
    const v = (name) => css.getPropertyValue(name).trim();
    return {
      layout: { background: { type: 'solid', color: v('--panel-2') }, textColor: v('--muted'), attributionLogo: true },
      grid: { vertLines: { color: v('--border') }, horzLines: { color: v('--border') } },
      rightPriceScale: { borderColor: v('--border') },
      timeScale: { borderColor: v('--border') },
    };
  },

  /** "Fri 27 Nov 2026" for the crosshair; the chart passes a day as "YYYY-MM-DD" or { year, month, day }. */
  dayLabel(t) {
    const d = typeof t === 'string' ? new Date(t + 'T00:00:00Z')
      : typeof t === 'number' ? new Date(t * 1000) : new Date(Date.UTC(t.year, t.month - 1, t.day));
    const wd = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getUTCDay()];
    return `${wd} ${String(d.getUTCDate()).padStart(2, '0')} ${MONTH_SHORT[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  },

  /** One-line key to the reversal rays: "Weekly" with its bullish and bearish colours, its elected
   *  grey, the same for Monthly, then dashed (Regular) and solid (Major) lines. */
  legend(frames) {
    const sample = (color, width, dashed) => h('span', { class: 'ray-sample', style: `border-top: ${width}px ${dashed ? 'dashed' : 'solid'} ${color}` });
    const item = (label, ...samples) => h('span', { class: 'ray-item' }, ...samples, h('span', null, label));
    const items = [];
    for (const [tf, name] of [['W', 'Weekly'], ['M', 'Monthly']]) {
      if (!frames.includes(tf)) continue;
      const s = this.RAY_STYLE[tf];
      items.push(item(name, sample(s.bullish, s.width), sample(s.bearish, s.width)), item('Elected', sample(s.elected, s.width)));
    }
    if (!items.length) return null;
    items.push(item('Regular', sample('currentColor', 2, true)), item('Major', sample('currentColor', 2, false)));
    return h('div', { class: 'ray-legend' }, ...items);
  },

  /** Create a chart in `container`. Returns { chart, series, remove() }. */
  async create(container) {
    const LWC = await this.load();
    const chart = LWC.createChart(container, {
      autoSize: true,
      ...this.theme(),
      crosshair: { mode: LWC.CrosshairMode.Normal },
      localization: { timeFormatter: (t) => this.dayLabel(t) },
    });
    // "Auto (fits data to screen)": the price axis fits the visible candles. Dragging the axis turns
    // it off, double-clicking the axis turns it back on (both are the library's defaults).
    // The library leaves 20% free above the candles and 10% below; 5% each uses the height better.
    chart.priceScale('right').applyOptions({ autoScale: true, scaleMargins: { top: 0.05, bottom: 0.05 } });
    const series = chart.addSeries(LWC.CandlestickSeries, {
      upColor: '#26a69a', downColor: '#ef5350', borderVisible: false,
      wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    });
    const mark = this.markLine('#2962ff');
    series.attachPrimitive(mark.primitive);
    const rays = this.rayLines();
    series.attachPrimitive(rays.primitive);
    // Open, high, low, close, top left: of the candle under the mouse; when the mouse is elsewhere,
    // of the pinned candle (the one H marks, set with pin()), else of the newest. Pinning shows
    // that candle at once, even with the mouse on the chart; the next mouse move takes over again.
    const ohlc = h('div', { class: 'chart-ohlc' });
    container.append(ohlc);
    let pinned = null, keyHasIt = false, lastPoint = null;
    const showOhlc = (param) => {
      const point = param && param.point;
      // A scroll or redraw after a key press also reports a crosshair move: only a real mouse move
      // (a new pointer position) takes the readout back from the key
      if (point && lastPoint && point.x === lastPoint.x && point.y === lastPoint.y && keyHasIt) param = null;
      else if (param) { keyHasIt = false; lastPoint = point || null; }
      const data = series.data();
      const c = (param && param.time !== undefined && param.seriesData.get(series))
        || (pinned !== null && data.find((d) => d.time === pinned)) || data[data.length - 1];
      if (!c) { ohlc.replaceChildren(); return; }
      const i = data.findIndex((d) => d.time === c.time);
      const prev = i > 0 ? data[i - 1].close : null;
      const f = series.priceFormatter();
      const up = c.close >= c.open;
      const value = (label, v) => [h('span', { class: 'k' }, label), h('span', { class: up ? 'up' : 'down' }, f.format(v))];
      const change = prev ? c.close - prev : null;
      ohlc.replaceChildren(...value('O', c.open), ...value('H', c.high), ...value('L', c.low), ...value('C', c.close),
        change === null ? '' : h('span', { class: change >= 0 ? 'up' : 'down' },
          `${change >= 0 ? '+' : '−'}${f.format(Math.abs(change))} (${change >= 0 ? '+' : '−'}${Math.abs(change / prev * 100).toFixed(2)}%)`));
    };
    chart.subscribeCrosshairMove(showOhlc);
    const scheme = window.matchMedia('(prefers-color-scheme: dark)');
    const retheme = () => chart.applyOptions(this.theme());
    scheme.addEventListener('change', retheme);
    const pin = (time) => { pinned = time; keyHasIt = time !== null; showOhlc(null); };
    return { chart, series, mark, rays, showOhlc, pin, remove: () => { scheme.removeEventListener('change', retheme); chart.remove(); } };
  },

  /**
   * A dashed vertical line through one candle, drawn over the chart (a series primitive).
   * Returns { primitive, set(time | null) }.
   */
  markLine(color) {
    let param = null, time = null, x = null;
    const view = {
      zOrder: () => 'top',
      renderer: () => ({
        draw: (target) => target.useBitmapCoordinateSpace(({ context, bitmapSize, horizontalPixelRatio, verticalPixelRatio }) => {
          if (x === null) return;
          // Whole device pixels, so the line stays sharp on any screen
          const width = Math.max(1, Math.floor(horizontalPixelRatio));
          const left = Math.round(x * horizontalPixelRatio) - Math.floor(width / 2);
          const dash = Math.round(5 * verticalPixelRatio), gap = Math.round(4 * verticalPixelRatio);
          context.fillStyle = color;
          for (let y = 0; y < bitmapSize.height; y += dash + gap) {
            context.fillRect(left, y, width, Math.min(dash, bitmapSize.height - y));
          }
        }),
      }),
    };
    return {
      primitive: {
        attached: (p) => { param = p; p.requestUpdate(); },
        detached: () => { param = null; },
        updateAllViews: () => { x = time && param ? param.chart.timeScale().timeToCoordinate(time) : null; },
        paneViews: () => [view],
      },
      get time() { return time; },
      set(t) {
        time = t;
        if (param) param.requestUpdate();
      },
    };
  },

  /**
   * Horizontal rays (a series primitive): each starts at a candle and runs to the right edge.
   * Each ray also gets a price label on the price axis, in its colour.
   * Returns { primitive, set([{ from: candle index or null for the left edge, price, color, width, dashed }]) }.
   */
  rayLines() {
    let param = null, rays = [], drawn = [], labels = [];
    const view = {
      zOrder: () => 'bottom',
      renderer: () => ({
        draw: (target) => target.useBitmapCoordinateSpace(({ context, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
          for (const r of drawn) {
            const height = Math.max(1, Math.round(r.width * vr));
            const top = Math.round(r.y * vr - height / 2);
            const left = Math.max(0, Math.round(r.x * hr));
            if (top + height < 0 || top > bitmapSize.height || left >= bitmapSize.width) continue;
            context.fillStyle = r.color;
            if (!r.dashed) { context.fillRect(left, top, bitmapSize.width - left, height); continue; }
            const dash = Math.round(8 * hr), gap = Math.round(5 * hr);
            for (let x = left; x < bitmapSize.width; x += dash + gap) context.fillRect(x, top, Math.min(dash, bitmapSize.width - x), height);
          }
        }),
      }),
    };
    return {
      primitive: {
        attached: (p) => { param = p; p.requestUpdate(); },
        detached: () => { param = null; },
        updateAllViews: () => {
          if (!param) { drawn = []; return; }
          const ts = param.chart.timeScale();
          drawn = rays.map((r) => ({ ...r, x: r.from === null ? 0 : ts.logicalToCoordinate(r.from),
                                     y: param.series.priceToCoordinate(r.price) }))
            .filter((r) => r.x !== null && r.y !== null);
          const format = param.series.priceFormatter();
          labels = drawn.map((r) => ({
            coordinate: () => r.y, text: () => format.format(r.price),
            textColor: () => '#ffffff', backColor: () => r.color,
          }));
        },
        paneViews: () => [view],
        priceAxisViews: () => labels,
      },
      get rays() { return rays; },
      set(list) {
        rays = list || [];
        if (param) param.requestUpdate();
      },
    };
  },

  /**
   * Rays for the reversals the server chose, on these candles. `counts` maps each timeframe to show
   * to the number of top reversals per group (active bullish, active bearish, elected), e.g. { W: 3, M: 3 }.
   */
  reversalRays(reversals, candles, counts) {
    return (reversals || []).filter((r) => r.rank < (counts[r.timeframe] || 0)).map((r) => {
      const style = this.RAY_STYLE[r.timeframe];
      // The ray starts at the last candle on or before its start day; before the data, at the left edge
      let from = null;
      for (let i = candles.length - 1; i >= 0; i--) if (candles[i].time <= r.start) { from = i; break; }
      return { from, price: r.price, width: style.width, dashed: !(r.major || r.double),
               color: r.status === 'elected' ? style.elected : style[r.direction] };
    });
  },

  /**
   * The candle for an array dated "YYYYMMDD": the same day, or else the first trading day after
   * it within a week (a weekly array's Monday or a monthly array's 1st can fall on a holiday or
   * weekend). Returns the candle's index, or -1 when there is none.
   */
  candleIndex(candles, date) {
    const day = `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}`;
    const limit = new Date(Date.parse(day) + 6 * 864e5).toISOString().slice(0, 10);
    return candles.findIndex((c) => c.time >= day && c.time <= limit);
  },

  /** Scroll the chart, keeping its zoom, so the candle at `index` is in view. */
  reveal(view, index) {
    const ts = view.chart.timeScale(), range = ts.getVisibleLogicalRange();
    if (!range || (index >= range.from + 1 && index <= range.to - 1)) return;
    const half = (range.to - range.from) / 2;
    ts.setVisibleLogicalRange({ from: index - half, to: index + half });
  },

  /** Put the newest VISIBLE candles in view, with a little room on the right. */
  showLatest(view, count) {
    if (!count) return;
    view.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, count - this.VISIBLE) - 0.5, to: count - 1 + 3 });
  },
};
