"""
全銘柄についてチャートパターン(カップウィズハンドル/フラットベース/VCP)を判定し、
Webアプリの「注目」タブ用のJSONを書き出すスクリプト。

判定ロジックは chart_patterns.py にある。GitHub Actions で毎日自動実行する。

出力先:
  docs/data/patterns.json                   最新日の判定結果
  docs/data/patterns/YYYY-MM-DD.json        直近 HISTORY_DAYS 営業日分の判定結果 + その後の値動き
  docs/data/patterns/index.json             閲覧できる日付の一覧

過去の日付の判定は、その日までのデータだけで行う(後から分かった値動きは使わない)。
一度作った日付のファイルは判定結果を使い回し、「その後の値動き」だけを毎日更新する。
まだファイルがない日付(初回や、判定条件の版が変わったとき)はさかのぼって判定する。

使い方:
  python detect_patterns.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_new_highs import DB_PATH
from build_web_data import WEB_DATA_DIR, safe_num
from chart_patterns import PATTERNS, RULE_VERSION

HISTORY_DAYS = 60              # 過去何営業日分の判定結果を残すか
LOOKBACK_CALENDAR_DAYS = 650   # 読み込む期間(最も古い判定日でも、200日線とカップの長さに足りる分)
MAX_DIST_TO_PIVOT_PCT = 5.0    # ピボットからこれ以上離れている「形成中」は載せない
# 上の距離の絞り込みをしない型。カップは取っ手の押し(5〜10%)の条件があるため、2026-10 にユーザーの判断で外した
DIST_FILTER_EXEMPT = {"cup_with_handle"}
CHART_DAYS = 250               # チャートに描く日数(ベースがもっと長ければ伸ばす)
ANOMALY_DAILY_RETURN = 0.25    # 1日でこれ以上動いた銘柄は分割未調整などの可能性があるため除外
PATTERN_HISTORY_DIR = WEB_DATA_DIR / "patterns"


class Stock:
    """1銘柄分の日足(古い順)。"""

    def __init__(self, g: pd.DataFrame):
        self.dates = g["date"].tolist()
        self.pos = {d: i for i, d in enumerate(self.dates)}
        self.h, self.l, self.c, self.v = (g[k].to_numpy(float) for k in ("high", "low", "close", "volume"))
        jumps = np.abs(np.diff(self.c) / self.c[:-1]) > ANOMALY_DAILY_RETURN
        # anomaly_upto[t] = t日目までに異常な値動きがあったか
        self.anomaly_upto = np.concatenate([[False], np.cumsum(jumps) > 0])


def load_stocks(conn: sqlite3.Connection) -> tuple[dict[str, Stock], list[str]]:
    latest = conn.execute("SELECT MAX(date) FROM stocks_daily").fetchone()[0]
    start = (pd.Timestamp(latest) - pd.Timedelta(days=LOOKBACK_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    df = pd.read_sql(
        "SELECT code, date, high, low, close, volume FROM stocks_daily WHERE date >= ? ORDER BY code, date",
        conn,
        params=(start,),
    ).dropna(subset=["high", "low", "close"])
    stocks = {code: Stock(g) for code, g in df.groupby("code", sort=False)}
    trading_dates = sorted(df["date"].unique())
    return stocks, trading_dates


def detect_on(stocks: dict[str, Stock], date: str, names: pd.DataFrame) -> list[dict]:
    """date の終値時点の判定。date より後のデータは使わない。"""
    items = []
    for code, s in stocks.items():
        t = s.pos.get(date)
        if t is None or t < 120 or s.anomaly_upto[t]:
            continue
        for key, (label, detect) in PATTERNS.items():
            res = detect(s.h, s.l, s.c, s.v, t)
            if res is None:
                continue
            if (
                res["status"] == "forming"
                and key not in DIST_FILTER_EXEMPT
                and (res["pivot"] / s.c[t] - 1) * 100 > MAX_DIST_TO_PIVOT_PCT
            ):
                continue
            items.append(build_item(code, s, t, key, label, res, names))
    items.sort(key=lambda x: (x["status"] != "breakout", x["dist_to_pivot_pct"] or 0))
    return items


def build_item(code: str, s: Stock, t: int, key: str, label: str, res: dict, names: pd.DataFrame) -> dict:
    start = max(0, min(t - CHART_DAYS + 1, res["base_start"] - 10))
    pivot = res["pivot"]
    info = names.loc[code] if code in names.index else None
    return {
        "code": code,
        "company_name": info["company_name"] if info is not None else None,
        "sector33_name": info["sector33_name"] if info is not None else None,
        "pattern": key,
        "pattern_label": label,
        "status": res["status"],
        "close": safe_num(round(float(s.c[t]), 2)),
        "pivot": safe_num(round(pivot, 2)),
        "dist_to_pivot_pct": safe_num(round((pivot / s.c[t] - 1) * 100, 2)),
        "vol_dry_ratio": safe_num(round(res["vol_dry_ratio"], 2)),
        "details": [list(d) for d in res["details"]],
        "chart": {
            "start_date": s.dates[start],
            "end_date": s.dates[t],
            "closes": [safe_num(round(float(x), 2)) for x in s.c[start:t + 1]],
            "base_start_i": res["base_start"] - start,
            "markers": [[i - start, safe_num(round(p, 2)), name] for i, p, name in res["markers"] if i >= start],
        },
    }


def followup(item: dict, s: Stock | None, date: str) -> dict | None:
    """判定日の翌日から最新日までの値動き。判定日が最新日ならNone。"""
    t = s.pos.get(date) if s else None
    if t is None or t + 1 >= len(s.c):
        return None
    entry, pivot = item["close"], item["pivot"]
    closes = s.c[t + 1:]
    # 形成中だった銘柄が、その後の終値でピボットを上抜けたか(当日ブレイクは0日目)
    if item["status"] == "breakout":
        breakout_days = 0
    else:
        above = np.nonzero(closes > pivot)[0]
        breakout_days = int(above[0]) + 1 if len(above) else None
    return {
        "days": len(closes),
        "close": safe_num(round(float(closes[-1]), 2)),
        "pct": safe_num(round((closes[-1] / entry - 1) * 100, 2)),
        "max_pct": safe_num(round((s.h[t + 1:].max() / entry - 1) * 100, 2)),
        "min_pct": safe_num(round((s.l[t + 1:].min() / entry - 1) * 100, 2)),
        "breakout_days": breakout_days,
        "closes": [safe_num(round(float(x), 2)) for x in closes],
    }


def load_existing(path: Path) -> list[dict] | None:
    """同じ判定条件の版で作られた既存ファイルがあれば、その判定結果を返す。"""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("rule_version") != RULE_VERSION:
        return None
    return data.get("patterns", [])


def write_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(description="チャートパターンを判定して注目タブ用のJSONを出力する")
    parser.add_argument("--db", default=str(DB_PATH), help="DBファイルのパス")
    parser.add_argument("--out-dir", default=str(WEB_DATA_DIR), help="出力先フォルダ(docs/data)")
    parser.add_argument("--days", type=int, default=HISTORY_DAYS, help="過去何営業日分を残すか")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    hist_dir = out_dir / PATTERN_HISTORY_DIR.name
    hist_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db)
    stocks, trading_dates = load_stocks(conn)
    names = pd.read_sql("SELECT code, company_name, sector33_name FROM stocks_master", conn).set_index("code")
    conn.close()

    target_dates = trading_dates[-args.days:]
    latest = target_dates[-1]
    generated_at = datetime.now(timezone.utc).isoformat()
    index_entries = []
    n_detected = 0
    for date in target_dates:
        path = hist_dir / f"{date}.json"
        items = None if date == latest else load_existing(path)
        if items is None:
            items = detect_on(stocks, date, names)
            n_detected += 1
        for item in items:
            item["after"] = followup(item, stocks.get(item["code"]), date)
        day = {"date": date, "generated_at": generated_at, "rule_version": RULE_VERSION, "patterns": items}
        write_json(path, day)
        index_entries.append({
            "date": date,
            "n": len(items),
            "n_breakout": sum(i["status"] == "breakout" for i in items),
        })
        if date == latest:
            write_json(out_dir / "patterns.json", day)

    # 残す期間より古い日付のファイルは削除して、データ量を一定に保つ
    keep = set(target_dates)
    for path in hist_dir.glob("????-??-??.json"):
        if path.stem not in keep:
            path.unlink()

    index_entries.sort(key=lambda x: x["date"], reverse=True)
    write_json(hist_dir / "index.json", {"dates": index_entries})

    latest_entry = index_entries[0]
    print(f"{len(target_dates)}日分を出力しました(新たに判定: {n_detected}日分) -> {hist_dir}")
    print(f"最新日 {latest}: {latest_entry['n']}件(本日ブレイク {latest_entry['n_breakout']}件)")
    return 0


if __name__ == "__main__":
    exit(main())
