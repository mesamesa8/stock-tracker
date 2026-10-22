/* ==========================================================================
   高値ブレイク板 - アプリロジック
   フレームワーク不使用。data/latest.json と data/history/*.json を
   fetch して描画するだけのシンプルな構成。
   ========================================================================== */

const DATA_BASE = "data";

const SORT_OPTIONS_TODAY = [
  { value: "score_desc", label: "総合スコアが高い順" },
  { value: "win_rate_desc", label: "期待勝率が高い順" },
  { value: "expected_return_desc", label: "期待値が高い順" },
  { value: "tightness_desc", label: "チャートの締まりが良い順" },
  { value: "sector_desc", label: "業種調整が高い順" },
  { value: "sector_asc", label: "業種名順" },
  { value: "code_asc", label: "銘柄コード順" },
];

const SORT_OPTIONS_HISTORY = [
  { value: "score_desc", label: "総合スコアが高い順" },
  { value: "pct_change_desc", label: "値上がり率が大きい順" },
  { value: "pct_change_asc", label: "値下がり率が大きい順" },
  { value: "days_elapsed_desc", label: "経過日数が長い順" },
  { value: "win_rate_desc", label: "期待勝率が高い順" },
  { value: "tightness_desc", label: "チャートの締まりが良い順" },
  { value: "sector_asc", label: "業種名順" },
  { value: "code_asc", label: "銘柄コード順" },
];

const SORT_OPTIONS_WATCH = [
  { value: "pivot_near", label: "ピボットに近い順" },
  { value: "vol_dry_asc", label: "出来高が細っている順" },
  { value: "code_asc", label: "銘柄コード順" },
];

const state = {
  mode: "today", // "today" | "history" | "watch"
  patternFilter: "all",
  watchData: null,
  sortBy: "score_desc",
  historyIndex: null,
  historyCache: {},
  currentData: null,
};

const els = {
  listRoot: document.getElementById("listRoot"),
  updatedAt: document.getElementById("updatedAt"),
  tabToday: document.getElementById("tabToday"),
  tabHistory: document.getElementById("tabHistory"),
  tabWatch: document.getElementById("tabWatch"),
  historyPicker: document.getElementById("historyPicker"),
  patternPicker: document.getElementById("patternPicker"),
  patternSelect: document.getElementById("patternSelect"),
  dateSelect: document.getElementById("dateSelect"),
  sortSelect: document.getElementById("sortSelect"),
  infoBtn: document.getElementById("infoBtn"),
  infoSheetBackdrop: document.getElementById("infoSheetBackdrop"),
  infoSheetClose: document.getElementById("infoSheetClose"),
};

init();

async function init() {
  bindEvents();
  populateSortOptions("today");
  await loadToday();
}

function bindEvents() {
  els.tabToday.addEventListener("click", () => switchMode("today"));
  els.tabHistory.addEventListener("click", () => switchMode("history"));
  els.tabWatch.addEventListener("click", () => switchMode("watch"));
  els.dateSelect.addEventListener("change", (e) => loadHistoryDate(e.target.value));
  els.patternSelect.addEventListener("change", (e) => {
    state.patternFilter = e.target.value;
    if (state.watchData) renderPatterns(state.watchData);
  });
  els.sortSelect.addEventListener("change", (e) => {
    state.sortBy = e.target.value;
    if (state.mode === "watch") {
      if (state.watchData) renderPatterns(state.watchData);
    } else if (state.currentData) {
      renderStocks(state.currentData, { showPerformance: state.mode === "history" });
    }
  });
  els.infoBtn.addEventListener("click", () => (els.infoSheetBackdrop.hidden = false));
  els.infoSheetClose.addEventListener("click", () => (els.infoSheetBackdrop.hidden = true));
  els.infoSheetBackdrop.addEventListener("click", (e) => {
    if (e.target === els.infoSheetBackdrop) els.infoSheetBackdrop.hidden = true;
  });
}

function sortOptionsFor(mode) {
  if (mode === "history") return SORT_OPTIONS_HISTORY;
  if (mode === "watch") return SORT_OPTIONS_WATCH;
  return SORT_OPTIONS_TODAY;
}

function populateSortOptions(mode) {
  const options = sortOptionsFor(mode);
  els.sortSelect.innerHTML = options.map((o) => `<option value="${o.value}">${o.label}</option>`).join("");
  // 今のタブで選べない並び替えを選んでいた場合は、そのタブの既定(先頭)に戻す
  if (!options.some((o) => o.value === state.sortBy)) {
    state.sortBy = options[0].value;
  }
  els.sortSelect.value = state.sortBy;
}

async function switchMode(mode) {
  state.mode = mode;
  for (const [m, btn] of [["today", els.tabToday], ["history", els.tabHistory], ["watch", els.tabWatch]]) {
    btn.classList.toggle("is-active", mode === m);
    btn.setAttribute("aria-selected", String(mode === m));
  }
  els.historyPicker.hidden = mode !== "history";
  els.patternPicker.hidden = mode !== "watch";
  populateSortOptions(mode);

  if (mode === "today") {
    await loadToday();
  } else if (mode === "watch") {
    await loadWatch();
  } else {
    await ensureHistoryIndex();
  }
}

async function loadWatch() {
  showMessage("読み込み中…");
  try {
    state.watchData = await fetchJson(`${DATA_BASE}/patterns.json`);
    renderPatterns(state.watchData);
    setUpdatedAt(state.watchData);
  } catch (err) {
    showMessage("チャートの型の判定データがまだありません。夕方以降にもう一度確認してください。");
  }
}

function sortPatterns(items, sortBy) {
  const comparators = {
    // ブレイク済みを先頭に、形成中はピボットに近い順
    pivot_near: (a, b) =>
      (a.status !== "breakout") - (b.status !== "breakout") ||
      (a.dist_to_pivot_pct ?? 999) - (b.dist_to_pivot_pct ?? 999),
    vol_dry_asc: (a, b) => (a.vol_dry_ratio ?? 999) - (b.vol_dry_ratio ?? 999),
    code_asc: (a, b) => String(a.code).localeCompare(String(b.code)) || a.pattern.localeCompare(b.pattern),
  };
  return items.sort(comparators[sortBy] || comparators.pivot_near);
}

function renderPatterns(data) {
  const all = data.patterns || [];
  const items = sortPatterns(
    all.filter((p) => state.patternFilter === "all" || p.pattern === state.patternFilter),
    state.sortBy,
  );
  if (items.length === 0) {
    showMessage("この条件に当てはまる銘柄はありませんでした。");
    return;
  }
  const nBreakout = items.filter((p) => p.status === "breakout").length;
  els.listRoot.innerHTML =
    `<p class="sort-status">${items.length}件(本日ブレイク ${nBreakout}件) ・ 並び順: ${escapeHtml(currentSortLabel())}</p>` +
    items.map(renderPatternCard).join("");
}

function renderPatternCard(p) {
  const isBreakout = p.status === "breakout";
  const distLabel = isBreakout
    ? `ブレイク ${formatSigned(-p.dist_to_pivot_pct)}%`
    : `あと ${formatNum(p.dist_to_pivot_pct)}%`;
  const vol = p.vol_dry_ratio;
  const volLabel = vol == null ? "-" : `${vol.toFixed(2)}倍${vol < 1 ? "(減少)" : ""}`;
  const details = (p.details || [])
    .map(([k, v]) => `<div class="pattern-detail"><span>${escapeHtml(k)}</span><span>${escapeHtml(v)}</span></div>`)
    .join("");

  return `
    <article class="stock-card pattern-card">
      <div class="stock-card-top">
        <div class="stock-name-block">
          <div class="stock-code">${escapeHtml(p.code)} ・ ${escapeHtml(p.sector33_name || "-")}</div>
          <div class="stock-name">${escapeHtml(p.company_name || p.code)}</div>
        </div>
        <div class="pattern-badges">
          <span class="pattern-badge">${escapeHtml(p.pattern_label)}</span>
          <span class="status-badge ${isBreakout ? "is-breakout" : ""}">${isBreakout ? "本日ブレイク" : "形成中"}</span>
        </div>
      </div>

      <div class="pattern-chart">${renderPatternChart(p)}</div>

      <div class="stock-metrics">
        <div class="metric">
          <div class="metric-label">終値</div>
          <div class="metric-value">${formatYen(p.close)}</div>
        </div>
        <div class="metric">
          <div class="metric-label">ピボット</div>
          <div class="metric-value">${formatYen(p.pivot)}</div>
        </div>
        <div class="metric">
          <div class="metric-label">位置</div>
          <div class="metric-value ${isBreakout ? "is-up-text" : ""}">${distLabel}</div>
        </div>
      </div>

      <div class="pattern-details">
        ${details}
        <div class="pattern-detail"><span>直近の出来高</span><span>${volLabel}</span></div>
      </div>
    </article>
  `;
}

// 終値の線に、ベースの範囲(網掛け)・ピボットの水平線・左の高値などの印を重ねて描く
function renderPatternChart(p) {
  const chart = p.chart || {};
  const closes = chart.closes || [];
  if (closes.length < 2) return "";
  const w = 320;
  const h = 120;
  const padTop = 14;
  const padBottom = 4;
  const markers = chart.markers || [];
  const values = closes.concat(markers.map((m) => m[1]), [p.pivot]).filter((v) => v != null);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const x = (i) => (i / (closes.length - 1)) * w;
  const y = (v) => padTop + (1 - (v - min) / range) * (h - padTop - padBottom);

  const line = "M" + closes.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" L");
  const baseX = x(Math.max(0, chart.base_start_i || 0));
  const pivotY = y(p.pivot).toFixed(1);
  const marks = markers
    .map(([i, v, label]) => {
      const cx = x(i);
      const cy = y(v);
      const isLow = label.includes("安値") || label === "底";
      const ty = isLow ? cy + 11 : cy - 5;
      const anchor = cx < 30 ? "start" : cx > w - 30 ? "end" : "middle";
      return `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="2.6" class="chart-marker"></circle>
        <text x="${cx.toFixed(1)}" y="${ty.toFixed(1)}" text-anchor="${anchor}" class="chart-label">${escapeHtml(label)}</text>`;
    })
    .join("");

  return `
    <svg viewBox="0 0 ${w} ${h}" width="100%" height="100%" role="img"
      aria-label="${escapeHtml(chart.start_date)}から${escapeHtml(chart.end_date)}までの終値チャート">
      <rect x="${baseX.toFixed(1)}" y="0" width="${(w - baseX).toFixed(1)}" height="${h}" class="chart-base"></rect>
      <line x1="0" x2="${w}" y1="${pivotY}" y2="${pivotY}" class="chart-pivot"></line>
      <path d="${line}" class="chart-line"></path>
      ${marks}
    </svg>
    <div class="chart-dates"><span>${escapeHtml(chart.start_date)}</span><span>網掛け=ベース ・ 点線=ピボット</span><span>${escapeHtml(chart.end_date)}</span></div>
  `;
}

async function fetchJson(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`fetch failed: ${path} (${res.status})`);
  return res.json();
}

async function loadToday() {
  showMessage("読み込み中…");
  try {
    const data = await fetchJson(`${DATA_BASE}/latest.json`);
    state.currentData = data;
    renderStocks(data, { showPerformance: false });
    setUpdatedAt(data);
  } catch (err) {
    showMessage("本日のデータをまだ取得できていません。夕方以降にもう一度確認してください。");
  }
}

async function ensureHistoryIndex() {
  if (!state.historyIndex) {
    try {
      state.historyIndex = await fetchJson(`${DATA_BASE}/history/index.json`);
    } catch (err) {
      showMessage("履歴データがまだありません。");
      return;
    }
  }
  const dates = state.historyIndex.dates || [];
  if (dates.length === 0) {
    showMessage("履歴データがまだありません。");
    return;
  }
  els.dateSelect.innerHTML = dates
    .map((d) => `<option value="${d.date}">${d.date}(${d.n_stocks}銘柄)</option>`)
    .join("");
  await loadHistoryDate(dates[0].date);
}

async function loadHistoryDate(dateStr) {
  showMessage("読み込み中…");
  try {
    let data = state.historyCache[dateStr];
    if (!data) {
      data = await fetchJson(`${DATA_BASE}/history/${dateStr}.json`);
      state.historyCache[dateStr] = data;
    }
    state.currentData = data;
    renderStocks(data, { showPerformance: true });
    setUpdatedAt(data);
  } catch (err) {
    showMessage("このデータの読み込みに失敗しました。");
  }
}

function setUpdatedAt(data) {
  const label = { today: "最終更新", history: "この一覧の抽出日", watch: "判定日" }[state.mode];
  els.updatedAt.textContent = `${label}: ${data.date || "-"}`;
}

function showMessage(msg) {
  els.listRoot.innerHTML = `<p class="state-msg">${escapeHtml(msg)}</p>`;
}

function sortStocks(stocks, sortBy) {
  const withDays = stocks.map((s) => ({ ...s, _daysElapsed: daysElapsed(s) }));

  const comparators = {
    score_desc: (a, b) => (b.composite_score ?? -999) - (a.composite_score ?? -999),
    win_rate_desc: (a, b) => (b.expected_win_rate_pct ?? -999) - (a.expected_win_rate_pct ?? -999),
    expected_return_desc: (a, b) => (b.expected_return_pct ?? -999) - (a.expected_return_pct ?? -999),
    pct_change_desc: (a, b) => (b.current_pct_change ?? -999) - (a.current_pct_change ?? -999),
    pct_change_asc: (a, b) => (a.current_pct_change ?? 999) - (b.current_pct_change ?? 999),
    days_elapsed_desc: (a, b) => (b._daysElapsed ?? -1) - (a._daysElapsed ?? -1),
    tightness_desc: (a, b) => (b.tightness_bonus_pct ?? -999) - (a.tightness_bonus_pct ?? -999),
    sector_desc: (a, b) => (b.sector_adjustment_pct ?? -999) - (a.sector_adjustment_pct ?? -999),
    sector_asc: (a, b) =>
      String(a.sector33_name || "").localeCompare(String(b.sector33_name || ""), "ja") ||
      (b.composite_score ?? -999) - (a.composite_score ?? -999),
    code_asc: (a, b) => String(a.code).localeCompare(String(b.code)),
  };

  const cmp = comparators[sortBy] || comparators.score_desc;
  return withDays.sort(cmp);
}

function daysElapsed(s) {
  if (!s.price_history || s.price_history.length === 0) return 0;
  return s.price_history.length - 1; // 載った日を0日目として数える(営業日ベース)
}

function renderStocks(data, { showPerformance }) {
  const stocks = sortStocks((data.stocks || []).slice(), state.sortBy);

  if (stocks.length === 0) {
    showMessage("この日はルールに合致する銘柄がありませんでした。");
    return;
  }

  const maxScore = Math.max(...stocks.map((s) => s.composite_score ?? 0), 0.01);

  const sortLabel = currentSortLabel();
  els.listRoot.innerHTML =
    `<p class="sort-status">${stocks.length}銘柄 ・ 並び順: ${escapeHtml(sortLabel)}</p>` +
    stocks.map((s, i) => renderCard(s, maxScore, showPerformance, i + 1)).join("");
}

function currentSortLabel() {
  const found = sortOptionsFor(state.mode).find((o) => o.value === state.sortBy);
  return found ? found.label : "";
}

// 並び替えの基準になっている値をカードに表示し、並び替えが効いていることを目で確認できるようにする
function sortKeyBadge(s) {
  switch (state.sortBy) {
    case "win_rate_desc": return `期待勝率 ${formatNum(s.expected_win_rate_pct)}%`;
    case "expected_return_desc": return `期待値 ${formatSigned(s.expected_return_pct)}`;
    case "tightness_desc": return `締まり ${formatSigned(s.tightness_bonus_pct)}`;
    case "sector_desc": return `業種調整 ${formatSigned(s.sector_adjustment_pct)}`;
    case "pct_change_desc":
    case "pct_change_asc": return `騰落 ${formatSigned(s.current_pct_change)}%`;
    case "days_elapsed_desc": return `経過${daysElapsed(s)}営業日`;
    default: return "";
  }
}

function renderCard(s, maxScore, showPerformance, rank) {
  const rankOpacity = Math.max(0.25, Math.min(1, (s.composite_score ?? 0) / maxScore));
  const perfHtml = showPerformance ? renderPerformance(s) : "";

  return `
    <article class="stock-card" style="--rank-opacity: ${rankOpacity}">
      <div class="stock-card-top">
        <div class="stock-name-block">
          <div class="stock-code">${rank}位 ・ ${escapeHtml(s.code)} ・ ${escapeHtml(s.sector33_name || "-")}${sortKeyBadge(s) ? ` <span class="sort-key">${escapeHtml(sortKeyBadge(s))}</span>` : ""}</div>
          <div class="stock-name">${escapeHtml(s.company_name || s.code)}</div>
        </div>
        <div class="stock-score">
          <div class="stock-score-value">${formatNum(s.composite_score)}</div>
          <div class="stock-score-label">総合スコア</div>
        </div>
      </div>

      <div class="score-breakdown">
        期待値 ${formatSigned(s.expected_return_pct)} + 業種 ${formatSigned(s.sector_adjustment_pct)} + 締まり ${formatSigned(s.tightness_bonus_pct)}
        ${s.expected_value_sample_size != null ? `<span class="score-breakdown-n">(n=${s.expected_value_sample_size})</span>` : ""}
      </div>

      <div class="stock-metrics">
        <div class="metric">
          <div class="metric-label">終値</div>
          <div class="metric-value">${formatYen(s.entry_close)}</div>
        </div>
        <div class="metric">
          <div class="metric-label">損切</div>
          <div class="metric-value">${formatYen(s.stop_loss_price)}</div>
        </div>
        <div class="metric">
          <div class="metric-label">利確</div>
          <div class="metric-value">${formatYen(s.take_profit_price)}</div>
        </div>
      </div>

      ${perfHtml}
    </article>
  `;
}

function renderPerformance(s) {
  const pct = s.current_pct_change;
  const pctClass = pct == null || Math.abs(pct) < 0.05 ? "is-flat" : pct > 0 ? "is-up" : "is-down";
  const pctLabel = pct == null ? "-" : `${pct > 0 ? "+" : ""}${pct.toFixed(1)}%`;
  const spark = renderSparkline(s.price_history, pctClass);
  const days = daysElapsed(s);

  return `
    <div class="perf-row">
      <span class="perf-pill ${pctClass}">${pctLabel}</span>
      <div class="sparkline">${spark}</div>
      <div class="perf-right">
        <span class="perf-current">${formatYen(s.current_close)}円</span>
        <span class="perf-days">経過${days}営業日</span>
      </div>
    </div>
  `;
}

function renderSparkline(history, pctClass) {
  if (!history || history.length < 2) {
    return "";
  }
  const w = 100;
  const h = 28;
  const values = history.map((p) => p[1]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const points = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return [x, y];
  });

  const linePath = "M" + points.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" L");
  const fillPath = `${linePath} L${w},${h} L0,${h} Z`;

  const color = pctClass === "is-up" ? "var(--up)" : pctClass === "is-down" ? "var(--down)" : "var(--text-faint)";
  const fillColor = pctClass === "is-up" ? "var(--up-bg)" : pctClass === "is-down" ? "var(--down-bg)" : "transparent";

  return `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" width="100%" height="100%">
      <path class="fill" d="${fillPath}" fill="${fillColor}"></path>
      <path class="line" d="${linePath}" stroke="${color}"></path>
    </svg>
  `;
}

function formatNum(v) {
  if (v == null || Number.isNaN(v)) return "-";
  return v.toFixed(2);
}

function formatSigned(v) {
  if (v == null || Number.isNaN(v)) return "-";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}`;
}

function formatYen(v) {
  if (v == null || Number.isNaN(v)) return "-";
  return Number(v).toLocaleString("ja-JP", { maximumFractionDigits: 1 });
}

function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
