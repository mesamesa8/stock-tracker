"""
過去60日分のスクリーニング結果(screening_YYYY-MM-DD.csv)を読み込み、
それぞれの銘柄について「ランキングに載った日から現在までの株価推移」を
計算し、スマホ表示用の軽量なJSONファイルとして書き出すスクリプト。

60日より古いスクリーニング結果は対象外にすることで、出力されるJSONの
総量が際限なく増え続けないようにしている(定常状態に達すれば、日々の
増分と減分がほぼ相殺されるため、データ量はほぼ一定に保たれる)。

出力先:
  web/data/latest.json          最新日のランキング(価格推移はまだ1点のみ)
  web/data/history/index.json   閲覧可能な日付の一覧
  web/data/history/YYYY-MM-DD.json  各日のランキング + 価格推移

使い方:
  python build_web_data.py
  python build_web_data.py --days 60   # 遡る日数を変更する場合
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from analyze_new_highs import DB_PATH

ANALYSIS_OUTPUT_DIR = Path(__file__).parent / "analysis_output"
WEB_DATA_DIR = Path(__file__).parent / "docs" / "data"
WEB_HISTORY_DIR = WEB_DATA_DIR / "history"

DEFAULT_LOOKBACK_DAYS = 60

SCREENING_FILE_PATTERN = re.compile(r"screening_(\d{4}-\d{2}-\d{2})\.csv$")


def find_screening_files(lookback_days: int) -> list[tuple[str, Path]]:
    """analysis_output内のscreening_YYYY-MM-DD.csvのうち、直近lookback_days日分だけを返す。"""
    cutoff = datetime.today() - timedelta(days=lookback_days)
    results = []
    for path_str in glob.glob(str(ANALYSIS_OUTPUT_DIR / "screening_*.csv")):
        path = Path(path_str)
        m = SCREENING_FILE_PATTERN.search(path.name)
        if not m:
            continue
        date_str = m.group(1)
        date_val = datetime.strptime(date_str, "%Y-%m-%d")
        if date_val >= cutoff:
            results.append((date_str, path))
    return sorted(results, key=lambda x: x[0])


def load_price_history(conn: sqlite3.Connection, codes: list[str], start_date: str, end_date: str) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame(columns=["code", "date", "close"])
    placeholders = ",".join("?" * len(codes))
    query = f"""
        SELECT code, date, close FROM stocks_daily
        WHERE code IN ({placeholders}) AND date >= ? AND date <= ?
        ORDER BY code, date
    """
    return pd.read_sql(query, conn, params=(*codes, start_date, end_date))


def build_day_json(entry_date: str, screening_df: pd.DataFrame, price_history: pd.DataFrame) -> dict:
    stocks = []
    for _, row in screening_df.iterrows():
        code = row["code"]
        entry_close = float(row["close"])

        hist = price_history[price_history["code"] == code].sort_values("date")
        trajectory = []
        for _, h in hist.iterrows():
            pct = round((h["close"] - entry_close) / entry_close * 100, 2) if entry_close else None
            trajectory.append([h["date"], round(float(h["close"]), 2), pct])

        # アプリ側からすぐ参照できるよう、直近の価格・騰落率をトップレベルにも複製しておく
        if trajectory:
            current_date, current_close, current_pct_change = trajectory[-1]
        else:
            current_date, current_close, current_pct_change = None, None, None

        stocks.append(
            {
                "code": code,
                "company_name": row.get("company_name"),
                "sector33_name": row.get("sector33_name"),
                "entry_close": entry_close,
                "current_date": current_date,
                "current_close": current_close,
                "current_pct_change": current_pct_change,
                "composite_score": row.get("composite_score"),
                "expected_win_rate_pct": row.get("expected_win_rate_pct"),
                "expected_return_pct": row.get("expected_return_pct"),
                "stop_loss_price": row.get("stop_loss_price"),
                "take_profit_price": row.get("take_profit_price"),
                "price_history": trajectory,
            }
        )

    return {
        "date": entry_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stocks": stocks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Webアプリ用のスクリーニング+株価推移JSONを生成する")
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="遡って対象にする日数")
    parser.add_argument("--db", default=str(DB_PATH), help="DBファイルのパス")
    args = parser.parse_args()

    files = find_screening_files(args.days)
    print(f"対象になるスクリーニング結果ファイル: {len(files)} 件(直近{args.days}日分)")
    if not files:
        print("[WARN] 対象ファイルがありませんでした。daily_screener.pyを先に実行してください。")
        return 0

    conn = sqlite3.connect(args.db)
    latest_price_row = conn.execute("SELECT MAX(date) FROM stocks_daily").fetchone()
    latest_price_date = latest_price_row[0] if latest_price_row else None

    WEB_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    index_entries = []
    for entry_date, path in files:
        screening_df = pd.read_csv(path, dtype={"code": str})
        codes = screening_df["code"].tolist()

        # 価格推移は「載った日」〜「直近の価格データがある日」まで(最大でも args.days 日分)
        price_history = load_price_history(conn, codes, entry_date, latest_price_date or entry_date)

        day_json = build_day_json(entry_date, screening_df, price_history)

        out_path = WEB_HISTORY_DIR / f"{entry_date}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(day_json, f, ensure_ascii=False, separators=(",", ":"))

        index_entries.append({"date": entry_date, "n_stocks": len(screening_df)})
        print(f"  {entry_date}: {len(screening_df)}銘柄 -> {out_path.name}")

    conn.close()

    # 一覧(index)ファイル: アプリ側がタブ・日付選択を作るのに使う
    index_entries.sort(key=lambda x: x["date"], reverse=True)
    with open(WEB_HISTORY_DIR / "index.json", "w", encoding="utf-8") as f:
        json.dump({"dates": index_entries}, f, ensure_ascii=False, separators=(",", ":"))

    # 最新日のデータは latest.json としても複製しておく(トップ画面がすぐ参照できるように)
    if index_entries:
        latest_date = index_entries[0]["date"]
        latest_src = WEB_HISTORY_DIR / f"{latest_date}.json"
        latest_dst = WEB_DATA_DIR / "latest.json"
        latest_dst.write_text(latest_src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\n最新日({latest_date})を latest.json としても保存しました。")

    # 60日より古いhistoryファイルは削除し、データ量を一定に保つ
    cutoff_str = (datetime.today() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    removed = 0
    for path in WEB_HISTORY_DIR.glob("????-??-??.json"):
        if path.stem < cutoff_str:
            path.unlink()
            removed += 1
    if removed:
        print(f"{args.days}日より古いファイルを{removed}件削除しました。")

    print(f"\nWebデータを {WEB_DATA_DIR} に出力しました。")
    return 0


if __name__ == "__main__":
    exit(main())
