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
  { value: "code_asc", label: "銘柄コード順" },
];

const SORT_OPTIONS_HISTORY = [
  { value: "score_desc", label: "総合スコアが高い順" },
  { value: "pct_change_desc", label: "値上がり率が大きい順" },
  { value: "pct_change_asc", label: "値下がり率が大きい順" },
  { value: "days_elapsed_desc", label: "経過日数が長い順" },
  { value: "win_rate_desc", label: "期待勝率が高い順" },
  { value: "code_asc", label: "銘柄コード順" },
];

const state = {
  mode: "today", // "today" | "history"
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
  historyPicker: document.getElementById("historyPicker"),
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
  els.dateSelect.addEventListener("change", (e) => loadHistoryDate(e.target.value));
  els.sortSelect.addEventListener("change", (e) => {
    state.sortBy = e.target.value;
    if (state.currentData) renderStocks(state.currentData, { showPerformance: state.mode === "history" });
  });
  els.infoBtn.addEventListener("click", () => (els.infoSheetBackdrop.hidden = false));
  els.infoSheetClose.addEventListener("click", () => (els.infoSheetBackdrop.hidden = true));
  els.infoSheetBackdrop.addEventListener("click", (e) => {
    if (e.target === els.infoSheetBackdrop) els.infoSheetBackdrop.hidden = true;
  });
}

function populateSortOptions(mode) {
  const options = mode === "history" ? SORT_OPTIONS_HISTORY : SORT_OPTIONS_TODAY;
  els.sortSelect.innerHTML = options.map((o) => `<option value="${o.value}">${o.label}</option>`).join("");
  // 今のタブで選べない並び替えを選んでいた場合は、既定(スコア順)に戻す
  if (!options.some((o) => o.value === state.sortBy)) {
    state.sortBy = "score_desc";
  }
  els.sortSelect.value = state.sortBy;
}

async function switchMode(mode) {
  state.mode = mode;
  els.tabToday.classList.toggle("is-active", mode === "today");
  els.tabToday.setAttribute("aria-selected", String(mode === "today"));
  els.tabHistory.classList.toggle("is-active", mode === "history");
  els.tabHistory.setAttribute("aria-selected", String(mode === "history"));
  els.historyPicker.hidden = mode !== "history";
  populateSortOptions(mode);

  if (mode === "today") {
    await loadToday();
  } else {
    await ensureHistoryIndex();
  }
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
  const label = state.mode === "today" ? "最終更新" : "この一覧の抽出日";
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

  els.listRoot.innerHTML = stocks.map((s) => renderCard(s, maxScore, showPerformance)).join("");
}

function renderCard(s, maxScore, showPerformance) {
  const rankOpacity = Math.max(0.25, Math.min(1, (s.composite_score ?? 0) / maxScore));
  const perfHtml = showPerformance ? renderPerformance(s) : "";

  return `
    <article class="stock-card" style="--rank-opacity: ${rankOpacity}">
      <div class="stock-card-top">
        <div class="stock-name-block">
          <div class="stock-code">${escapeHtml(s.code)} ・ ${escapeHtml(s.sector33_name || "-")}</div>
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
