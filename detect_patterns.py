"""
最新日の全銘柄について、チャートパターン(カップウィズハンドル/フラットベース/VCP)を
判定し、Webアプリの「注目」タブ用に docs/data/patterns.json を書き出すスクリプト。

判定ロジックは chart_patterns.py にある。GitHub Actions で毎日自動実行する。

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
from chart_patterns import PATTERNS

LOOKBACK_CALENDAR_DAYS = 550   # 判定に使う期間(200日線とカップの長さに足りる分)
MAX_DIST_TO_PIVOT_PCT = 5.0    # ピボットからこれ以上離れている「形成中」は載せない
CHART_DAYS = 250               # チャートに描く日数(ベースがもっと長ければ伸ばす)
ANOMALY_DAILY_RETURN = 0.25    # 1日でこれ以上動いた銘柄は分割未調整などの可能性があるため除外


def load_prices(conn: sqlite3.Connection) -> tuple[pd.DataFrame, str]:
    latest = conn.execute("SELECT MAX(date) FROM stocks_daily").fetchone()[0]
    start = (pd.Timestamp(latest) - pd.Timedelta(days=LOOKBACK_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    df = pd.read_sql(
        "SELECT code, date, high, low, close, volume FROM stocks_daily WHERE date >= ? ORDER BY code, date",
        conn,
        params=(start,),
    )
    return df.dropna(subset=["high", "low", "close"]), latest


def build_item(code: str, g: pd.DataFrame, key: str, label: str, res: dict, names: pd.DataFrame) -> dict:
    t = len(g) - 1
    close = g["close"].to_numpy(float)
    dates = g["date"].tolist()
    start = max(0, min(t - CHART_DAYS + 1, res["base_start"] - 10))
    pivot = res["pivot"]
    info = names.loc[code] if code in names.index else {}
    return {
        "code": code,
        "company_name": info.get("company_name") if len(info) else None,
        "sector33_name": info.get("sector33_name") if len(info) else None,
        "pattern": key,
        "pattern_label": label,
        "status": res["status"],
        "close": safe_num(round(float(close[t]), 2)),
        "pivot": safe_num(round(pivot, 2)),
        "dist_to_pivot_pct": safe_num(round((pivot / close[t] - 1) * 100, 2)),
        "vol_dry_ratio": safe_num(round(res["vol_dry_ratio"], 2)),
        "details": [list(d) for d in res["details"]],
        "chart": {
            "start_date": dates[start],
            "end_date": dates[t],
            "closes": [safe_num(round(float(x), 2)) for x in close[start:]],
            "base_start_i": res["base_start"] - start,
            "markers": [[i - start, safe_num(round(p, 2)), name] for i, p, name in res["markers"] if i >= start],
        },
    }


def detect_all(df: pd.DataFrame, latest: str, names: pd.DataFrame) -> list[dict]:
    items = []
    for code, g in df.groupby("code", sort=False):
        if g["date"].iloc[-1] != latest or len(g) < 120:
            continue  # 最新日のデータがない銘柄(売買停止・上場廃止など)は対象外
        g = g.reset_index(drop=True)
        h, l, c, v = (g[k].to_numpy(float) for k in ("high", "low", "close", "volume"))
        if np.any(np.abs(np.diff(c) / c[:-1]) > ANOMALY_DAILY_RETURN):
            continue
        t = len(c) - 1
        for key, (label, detect) in PATTERNS.items():
            res = detect(h, l, c, v, t)
            if res is None:
                continue
            if res["status"] == "forming" and (res["pivot"] / c[t] - 1) * 100 > MAX_DIST_TO_PIVOT_PCT:
                continue
            items.append(build_item(code, g, key, label, res, names))
    # ブレイクを先頭に、形成中はピボットに近い順
    items.sort(key=lambda x: (x["status"] != "breakout", x["dist_to_pivot_pct"] or 0))
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="チャートパターンを判定して patterns.json を出力する")
    parser.add_argument("--db", default=str(DB_PATH), help="DBファイルのパス")
    parser.add_argument("--out", default=str(WEB_DATA_DIR / "patterns.json"), help="出力先")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    df, latest = load_prices(conn)
    names = pd.read_sql("SELECT code, company_name, sector33_name FROM stocks_master", conn).set_index("code")
    conn.close()

    items = detect_all(df, latest, names)
    out = {"date": latest, "generated_at": datetime.now(timezone.utc).isoformat(), "patterns": items}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    counts = pd.Series([(i["pattern_label"], i["status"]) for i in items]).value_counts()
    print(f"{latest}: {len(items)}件のパターンを検出しました -> {args.out}")
    for (label, status), n in counts.items():
        print(f"  {label} / {status}: {n}件")
    return 0


if __name__ == "__main__":
    exit(main())
