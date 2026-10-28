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
  { value: "after_pct_desc", label: "その後の値上がり率順" },
  { value: "op_yoy_desc", label: "営業利益の伸びが大きい順" },
  { value: "vol_dry_asc", label: "出来高が細っている順" },
  { value: "code_asc", label: "銘柄コード順" },
];

const SORT_OPTIONS_PICKUP = [
  { value: "op_yoy_desc", label: "営業利益の伸びが大きい順" },
  { value: "sales_yoy_desc", label: "売上高の伸びが大きい順" },
  { value: "pivot_near", label: "ピボットに近い順" },
  { value: "code_asc", label: "銘柄コード順" },
];

const state = {
  mode: "today", // "today" | "history" | "watch" | "pickup"
  pickupData: null,
  patternFilter: "all",
  watchData: null,
  watchIndex: null,
  watchCache: {},
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
  tabPickup: document.getElementById("tabPickup"),
  historyPicker: document.getElementById("historyPicker"),
  patternPicker: document.getElementById("patternPicker"),
  watchDatePicker: document.getElementById("watchDatePicker"),
  watchDateSelect: document.getElementById("watchDateSelect"),
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
  els.tabPickup.addEventListener("click", () => switchMode("pickup"));
  els.dateSelect.addEventListener("change", (e) => loadHistoryDate(e.target.value));
  els.watchDateSelect.addEventListener("change", (e) => loadWatchDate(e.target.value));
  els.patternSelect.addEventListener("change", (e) => {
    state.patternFilter = e.target.value;
    if (state.watchData) renderPatterns(state.watchData);
  });
  els.sortSelect.addEventListener("change", (e) => {
    state.sortBy = e.target.value;
    if (state.mode === "watch") {
      if (state.watchData) renderPatterns(state.watchData);
    } else if (state.mode === "pickup") {
      if (state.pickupData) renderPickup(state.pickupData);
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
  if (mode === "pickup") return SORT_OPTIONS_PICKUP;
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
  const tabs = [["today", els.tabToday], ["history", els.tabHistory], ["watch", els.tabWatch], ["pickup", els.tabPickup]];
  for (const [m, btn] of tabs) {
    btn.classList.toggle("is-active", mode === m);
    btn.setAttribute("aria-selected", String(mode === m));
  }
  els.historyPicker.hidden = mode !== "history";
  els.patternPicker.hidden = mode !== "watch";
  els.watchDatePicker.hidden = mode !== "watch";
  populateSortOptions(mode);

  if (mode === "today") {
    await loadToday();
  } else if (mode === "watch") {
    await loadWatch();
  } else if (mode === "pickup") {
    await loadPickup();
  } else {
    await ensureHistoryIndex();
  }
}

async function loadWatch() {
  if (!state.watchIndex) {
    try {
      state.watchIndex = await fetchJson(`${DATA_BASE}/patterns/index.json`);
    } catch (err) {
      state.watchIndex = { dates: [] };
    }
  }
  const dates = state.watchIndex.dates || [];
  if (dates.length === 0) {
    // 日付ごとのファイルがまだない場合は、最新日の結果だけを表示する
    els.watchDatePicker.hidden = true;
    await loadWatchDate(null);
    return;
  }
  els.watchDateSelect.innerHTML = dates
    .map((d) => `<option value="${d.date}">${d.date.slice(5)}(${d.n}件${d.n_breakout ? `・突破${d.n_breakout}` : ""})</option>`)
    .join("");
  const selected = state.watchData && dates.some((d) => d.date === state.watchData.date)
    ? state.watchData.date
    : dates[0].date;
  els.watchDateSelect.value = selected;
  await loadWatchDate(selected);
}

async function loadWatchDate(dateStr) {
  showMessage("読み込み中…");
  try {
    let data = dateStr ? state.watchCache[dateStr] : null;
    if (!data) {
      data = await fetchJson(dateStr ? `${DATA_BASE}/patterns/${dateStr}.json` : `${DATA_BASE}/patterns.json`);
      if (dateStr) state.watchCache[dateStr] = data;
    }
    state.watchData = data;
    renderPatterns(data);
    setUpdatedAt(data);
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
    after_pct_desc: (a, b) => (b.after?.pct ?? -999) - (a.after?.pct ?? -999),
    op_yoy_desc: (a, b) => (b.fins?.op_yoy ?? -999) - (a.fins?.op_yoy ?? -999),
    sales_yoy_desc: (a, b) => (b.fins?.sales_yoy ?? -999) - (a.fins?.sales_yoy ?? -999),
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
    `<p class="sort-status">${items.length}件(当日ブレイク ${nBreakout}件) ・ 並び順: ${escapeHtml(currentSortLabel())}</p>` +
    renderAfterSummary(items) +
    items.map(renderPatternCard).join("");
}

// 過去の判定日を見ているときに、その後の結果をまとめて表示する
function renderAfterSummary(items) {
  const withAfter = items.filter((p) => p.after);
  if (withAfter.length === 0) return "";
  const days = Math.max(...withAfter.map((p) => p.after.days));
  const broke = withAfter.filter((p) => p.after.breakout_days != null).length;
  const pcts = withAfter.map((p) => p.after.pct).filter((v) => v != null);
  const avg = pcts.reduce((a, b) => a + b, 0) / (pcts.length || 1);
  const up = pcts.filter((v) => v > 0).length;
  return `
    <div class="after-summary">
      <div class="after-summary-title">判定日から${days}営業日後の結果</div>
      <div class="after-summary-row">
        <span>ピボット上抜け <strong>${broke}/${withAfter.length}件</strong></span>
        <span>上昇 <strong>${up}/${pcts.length}件</strong></span>
        <span>平均 <strong class="${avg > 0 ? "is-up-text" : avg < 0 ? "is-down-text" : ""}">${formatSigned(avg)}%</strong></span>
      </div>
    </div>
  `;
}

async function loadPickup() {
  showMessage("読み込み中…");
  try {
    state.pickupData = await fetchJson(`${DATA_BASE}/pickup.json`);
    renderPickup(state.pickupData);
    setUpdatedAt(state.pickupData);
  } catch (err) {
    showMessage("ピックアップのデータがまだありません。夕方以降にもう一度確認してください。");
  }
}

function renderPickup(data) {
  const c = data.criteria || {};
  const finsDate = data.fins_latest_disc_date ? formatMonthDay(data.fins_latest_disc_date) : "-";
  const note = `
    <div class="after-summary">
      <div class="after-summary-title">${formatMonthDay(data.window_start)}〜${formatMonthDay(data.date)}に注目タブに出た銘柄のうち、最新の決算(直近四半期)が前年同期比で</div>
      <div class="after-summary-row">
        <span>売上高 <strong>+${c.sales_yoy_min ?? 10}%以上</strong></span>
        <span>営業利益 <strong>+${c.op_yoy_min ?? 20}%以上</strong></span>
      </div>
      <div class="after-sub">決算はJ-Quants無料版のため約12週間遅れ(現在は${escapeHtml(finsDate)}発表分まで)</div>
    </div>`;
  const items = sortPatterns((data.stocks || []).slice(), state.sortBy);
  if (items.length === 0) {
    els.listRoot.innerHTML = note + `<p class="state-msg">条件を満たす銘柄はありませんでした。</p>`;
    return;
  }
  els.listRoot.innerHTML =
    `<p class="sort-status">${items.length}銘柄 ・ 並び順: ${escapeHtml(currentSortLabel())}</p>` +
    note +
    items.map(renderPatternCard).join("");
}

function formatMonthDay(d) {
  if (!d) return "-";
  const [, m, day] = d.split("-");
  return `${Number(m)}/${Number(day)}`;
}

function formatYoy(v) {
  if (v == null) return "-";
  const cls = v > 0 ? "is-up-text" : v < 0 ? "is-down-text" : "";
  return `<span class="${cls}">${v > 0 ? "+" : ""}${v.toFixed(1)}%</span>`;
}

// 最新の決算(直近四半期)の前年同期比。決算データがない銘柄はその旨を出す
function renderFinsRow(f) {
  if (f === undefined) return "";
  if (!f) {
    return `<div class="fins-row"><span class="fins-label">決算</span><span class="after-sub">データなし</span></div>`;
  }
  const op = f.op_turnaround ? `<span class="is-up-text">黒字転換</span>` : formatYoy(f.op_yoy);
  return `
    <div class="fins-row ${f.meets ? "is-good" : ""}">
      <div class="fins-head">
        <span class="fins-label">決算 前年同期比</span>
        ${f.meets ? `<span class="fins-badge">好業績</span>` : ""}
      </div>
      <div class="fins-values">
        <span>売上高 ${formatYoy(f.sales_yoy)}</span>
        <span>営業利益 ${op}</span>
      </div>
      <div class="after-sub">${escapeHtml(f.period)}(${escapeHtml(formatMonthDay(f.disc_date))}発表・直近3ヶ月)</div>
    </div>`;
}

function renderAppearances(list) {
  if (!list || list.length === 0) return "";
  const text = list
    .map((a) => `${formatMonthDay(a.date)} ${a.pattern_label}${a.status === "breakout" ? "(ブレイク)" : ""}`)
    .join("、");
  return `<div class="appear-row">注目タブに出た日: ${escapeHtml(text)}</div>`;
}

function renderAfterRow(a) {
  if (!a) return "";
  const pctClass = a.pct == null || Math.abs(a.pct) < 0.05 ? "is-flat" : a.pct > 0 ? "is-up" : "is-down";
  const breakout =
    a.breakout_days == null ? "ピボット未突破" : a.breakout_days === 0 ? "判定日にブレイク" : `${a.breakout_days}日後にピボット突破`;
  return `
    <div class="perf-row">
      <span class="perf-pill ${pctClass}">${formatSigned(a.pct)}%</span>
      <div class="after-text">
        <div>その後${a.days}営業日 ・ ${escapeHtml(breakout)}</div>
        <div class="after-sub">最高 ${formatSigned(a.max_pct)}% / 最安 ${formatSigned(a.min_pct)}%</div>
      </div>
      <div class="perf-right">
        <span class="perf-current">${formatYen(a.close)}円</span>
        <span class="perf-days">現在値</span>
      </div>
    </div>
  `;
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
          <span class="status-badge ${isBreakout ? "is-breakout" : ""}">${p.detected_date ? `${formatMonthDay(p.detected_date)}時点 ` : ""}${isBreakout ? "当日ブレイク" : "形成中"}</span>
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

      ${renderFinsRow(p.fins)}
      ${renderAppearances(p.appearances)}
      ${renderAfterRow(p.after)}
    </article>
  `;
}

// 終値の線に、ベースの範囲(網掛け)・ピボットの水平線・左の高値などの印を重ねて描く。
// 過去の判定日では、判定日より後の値動きを色を変えてつなげる
function renderPatternChart(p) {
  const chart = p.chart || {};
  const closes = chart.closes || [];
  if (closes.length < 2) return "";
  const afterCloses = (p.after && p.after.closes) || [];
  const total = closes.length + afterCloses.length;
  const w = 320;
  const h = 120;
  const padTop = 14;
  const padBottom = 4;
  const markers = chart.markers || [];
  const values = closes.concat(afterCloses, markers.map((m) => m[1]), [p.pivot]).filter((v) => v != null);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const x = (i) => (i / (total - 1)) * w;
  const y = (v) => padTop + (1 - (v - min) / range) * (h - padTop - padBottom);

  const line = "M" + closes.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" L");
  const lastI = closes.length - 1;
  const afterPct = p.after ? p.after.pct : null;
  const afterLine = afterCloses.length
    ? `<line x1="${x(lastI).toFixed(1)}" x2="${x(lastI).toFixed(1)}" y1="0" y2="${h}" class="chart-judge-line"></line>
       <path d="M${x(lastI).toFixed(1)},${y(closes[lastI]).toFixed(1)} L${afterCloses
         .map((v, i) => `${x(lastI + 1 + i).toFixed(1)},${y(v).toFixed(1)}`)
         .join(" L")}" class="chart-after ${afterPct > 0 ? "is-up" : afterPct < 0 ? "is-down" : ""}"></path>`
    : "";
  const baseX = x(Math.max(0, chart.base_start_i || 0));
  const baseW = x(lastI) - baseX;
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
      <rect x="${baseX.toFixed(1)}" y="0" width="${baseW.toFixed(1)}" height="${h}" class="chart-base"></rect>
      <line x1="0" x2="${w}" y1="${pivotY}" y2="${pivotY}" class="chart-pivot"></line>
      <path d="${line}" class="chart-line"></path>
      ${afterLine}
      ${marks}
    </svg>
    <div class="chart-dates"><span>${escapeHtml(chart.start_date)}</span><span>網掛け=ベース ・ 点線=ピボット${afterCloses.length ? " ・ 色付き=判定後" : ""}</span><span>${afterCloses.length ? "現在" : escapeHtml(chart.end_date)}</span></div>
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
  const label = { today: "最終更新", history: "この一覧の抽出日", watch: "判定日", pickup: "最終更新" }[state.mode];
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
